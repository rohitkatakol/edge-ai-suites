# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""MQTT subscriber for object detection inference metadata.

Subscribes to the MQTT broker and routes incoming DL Streamer inference
metadata to the appropriate VMS shim for analytics push.

Topic convention: ``/{vms_name}/{analytics_app_id}/{camera_id}``
Example:         ``/nx-main/dls_vision/abc123-device-uuid``

On each message:
1. Parse vms_name, analytics_app_id, camera_id from topic.
2. Look up the VMS shim by vms_name.
3. Translate DLS metadata to Nx object-push format.
4. Call ``vms_shim.push_analytics_objects(device_id, objects, timestamp_ms)``.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from .loitering_tracker import LoiteringTracker, extract_tracked_objects
from .translator import translate_dls_metadata

if TYPE_CHECKING:
    from plugin.core.factory import VmsShimSet

logger = structlog.get_logger(__name__)


class MqttSubscriber:
    """Async MQTT subscriber that routes DLS inference metadata to VMS shims.

    Usage::

        subscriber = MqttSubscriber()
        task = asyncio.create_task(
            subscriber.run(mqtt_host, mqtt_port, vms_shim_sets)
        )
        # on shutdown:
        task.cancel()
    """

    async def run(
        self,
        mqtt_host: str,
        mqtt_port: int,
        vms_shim_sets: list[VmsShimSet],
        analytics_app_id: str = "dls_vision",
        label_type_map: dict[str, str] | None = None,
        timestamp_offset_ms: int = 0,
        tls_context: ssl.SSLContext | None = None,
        loitering_stop_duration_seconds: float = 0.0,
        loitering_publish_interval_seconds: float = 5.0,
        loitering_min_confidence: float = 0.1,
        loitering_zone_ior_threshold: float = 0.5,
    ) -> None:
        """Subscribe to MQTT and dispatch messages until cancelled.

        Topic wildcard: ``+/{analytics_app_id}/+`` (matches ``/{vms_name}/{analytics_app_id}/{camera_id}``)
        Leading slash is optional — both ``/nx-main/dls_vision/device`` and ``nx-main/dls_vision/device`` are
        handled by stripping the leading slash before splitting.

        When ``loitering_stop_duration_seconds`` > 0, per-camera dwell time is tracked from the
        same messages and a per-object snapshot (every tracked object, each with a ``status``
        field) is published (rate-limited, default every 5s) to
        ``{vms_name}/{analytics_app_id}/loiter_status/{device_id}``.
        """
        try:
            import aiomqtt  # type: ignore[import]
        except ImportError:
            logger.error(
                "mqtt_subscriber_aiomqtt_missing",
                detail="Install aiomqtt: pip install aiomqtt",
            )
            return

        # Build a name → shim lookup for fast dispatch
        shim_map: dict[str, Any] = {ss.name: ss.vms_shim for ss in vms_shim_sets}
        _label_map: dict[str, str] = {k.lower(): v for k, v in (label_type_map or {}).items()}
        tracker = (
            LoiteringTracker(loitering_stop_duration_seconds)
            if loitering_stop_duration_seconds > 0
            else None
        )
        last_loiter_publish: dict[str, float] = {}

        # Wildcard: single-level + matches any vms_name; trailing + matches any camera_id
        topic_filter = f"+/{analytics_app_id}/+"

        logger.info(
            "mqtt_subscriber_starting",
            host=mqtt_host,
            port=mqtt_port,
            topic_filter=topic_filter,
        )

        while True:
            try:
                async with aiomqtt.Client(mqtt_host, port=mqtt_port, tls_context=tls_context) as client:
                    await client.subscribe(topic_filter)
                    logger.info("mqtt_subscriber_subscribed", topic_filter=topic_filter)
                    async for message in client.messages:
                        await self._handle_message(
                            str(message.topic),
                            message.payload,
                            shim_map,
                            analytics_app_id,
                            _label_map,
                            timestamp_offset_ms,
                            client,
                            tracker,
                            loitering_publish_interval_seconds,
                            last_loiter_publish,
                            loitering_min_confidence,
                            loitering_zone_ior_threshold,
                            loitering_stop_duration_seconds,
                        )
            except asyncio.CancelledError:
                logger.info("mqtt_subscriber_stopped")
                return
            except Exception as exc:  # noqa: BLE001 — reconnect on any broker error
                logger.warning(
                    "mqtt_subscriber_disconnected",
                    error=str(exc),
                    retrying_in_seconds=5,
                )
                await asyncio.sleep(5)

    async def _handle_message(
        self,
        topic: str,
        payload: bytes,
        shim_map: dict[str, Any],
        analytics_app_id: str,
        label_type_map: dict[str, str] | None = None,
        timestamp_offset_ms: int = 0,
        mqtt_client: Any = None,
        tracker: LoiteringTracker | None = None,
        loitering_publish_interval_s: float = 1.0,
        last_loiter_publish: dict[str, float] | None = None,
        loitering_min_confidence: float = 0.1,
        loitering_zone_ior_threshold: float = 0.5,
        loitering_stop_duration_s: float = 0.0,
    ) -> None:
        """Parse topic, translate payload, and dispatch to VMS shim."""
        # Normalise: strip optional leading slash, split into parts
        parts = topic.lstrip("/").split("/")
        if len(parts) != 3:  # noqa: PLR2004
            logger.warning("mqtt_unexpected_topic_format", topic=topic)
            return

        vms_name, _, camera_id = parts

        # Exact match first (e.g. "nx-main"), then prefix match (e.g. "nx" → "nx-main")
        shim = shim_map.get(vms_name) or next(
            (v for k, v in shim_map.items() if k.startswith(vms_name)), None
        )
        if shim is None:
            logger.warning(
                "mqtt_unknown_vms",
                vms_name=vms_name,
                known=list(shim_map.keys()),
            )
            return

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("mqtt_payload_parse_failed", topic=topic, error=str(exc))
            return
        # accommodate DLS envelope {"metadata": {...}, "blob": ""} as well as just {},
        # as seen in DLS pipelines with appsink based destination vs gvametapublish based destination
        metadata = data.get("metadata", data)
        objects, timestamp_ms = translate_dls_metadata(metadata, label_type_map, timestamp_offset_ms)

        # device_id = camera_id without vendor prefix (e.g. "nx:abc" → "abc")
        device_id = camera_id.split(":", 1)[-1] if ":" in camera_id else camera_id

        if tracker is not None:
            tracked = extract_tracked_objects(metadata, loitering_min_confidence, loitering_zone_ior_threshold)
            tracked_snapshot = tracker.update(device_id, tracked, timestamp_ms)
            await self._push_loitering_bookmarks(
                shim, vms_name, device_id, tracked_snapshot, loitering_stop_duration_s, timestamp_ms,
            )
            await self._maybe_publish_loitering(
                mqtt_client,
                vms_name,
                analytics_app_id,
                device_id,
                tracked_snapshot,
                timestamp_ms,
                loitering_publish_interval_s,
                last_loiter_publish if last_loiter_publish is not None else {},
            )

        if not objects:
            logger.debug("mqtt_no_objects_in_frame", topic=topic)
            return

        ok = await shim.push_analytics_objects(device_id, objects, timestamp_ms)
        if not ok:
            logger.warning(
                "mqtt_push_failed",
                vms_name=vms_name,
                device_id=device_id,
                objects_count=len(objects),
            )
        else:
            logger.debug(
                "mqtt_pushed_objects",
                vms_name=vms_name,
                device_id=device_id,
                objects_count=len(objects),
                timestamp_ms=timestamp_ms,
            )

    async def _maybe_publish_loitering(
        self,
        mqtt_client: Any,
        vms_name: str,
        analytics_app_id: str,
        device_id: str,
        tracked_snapshot: list[dict[str, Any]],
        timestamp_ms: int,
        publish_interval_s: float,
        last_publish: dict[str, float],
    ) -> None:
        """Rate-limited publish of every tracked object's dwell status for one camera.

        Each object in ``objects`` carries a ``status`` ("loitering"/"normal") field so
        consumers can filter on dwell time without recomputing it.
        """
        if mqtt_client is None:
            return
        now = time.monotonic()
        if now - last_publish.get(device_id, 0.0) < publish_interval_s:
            return
        last_publish[device_id] = now

        topic = f"{vms_name}/{analytics_app_id}/loiter_status/{device_id}"
        body = json.dumps({
            "device_id": device_id,
            "timestamp_ms": timestamp_ms,
            "objects": tracked_snapshot,
        })
        try:
            await mqtt_client.publish(topic, body)
            logger.debug("mqtt_loiter_status_published", device_id=device_id, count=len(tracked_snapshot))
        except Exception as exc:  # noqa: BLE001 — publish failures shouldn't crash the subscriber loop
            logger.warning("mqtt_loiter_status_publish_failed", device_id=device_id, error=str(exc))

    async def _push_loitering_bookmarks(
        self,
        shim: Any,
        vms_name: str,
        device_id: str,
        tracked_snapshot: list[dict[str, Any]],
        stop_duration_s: float,
        timestamp_ms: int,
    ) -> None:
        """Create a one-time Nx bookmark for each object that just crossed the loitering threshold.

        Fires once per loitering episode (``just_started_loitering``), not on every update, so
        the camera timeline gets a single marker per person rather than continuous spam.
        """
        if shim is None or not hasattr(shim, "set_bookmark"):
            return
        alerts = [e for e in tracked_snapshot if e.get("just_started_loitering")]
        if not alerts:
            return

        camera_id_ref = f"{vms_name}:{device_id}"
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        for entry in alerts:
            label = f"Person {entry['track_id']} found loitering more than {int(stop_duration_s)}s in the zone"
            try:
                result = await shim.set_bookmark(camera_id_ref, timestamp, label)
                logger.info(
                    "loitering_bookmark_pushed",
                    device_id=device_id,
                    track_id=entry["track_id"],
                    status=getattr(result, "status", None),
                )
            except Exception as exc:  # noqa: BLE001 — bookmark failures shouldn't crash the subscriber loop
                logger.warning(
                    "loitering_bookmark_failed",
                    device_id=device_id,
                    track_id=entry["track_id"],
                    error=str(exc),
                )

