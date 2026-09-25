# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Per-camera dwell-time tracking for VMS-triggered loitering detection.

Tracks per-object dwell time on the ``nx/dls_vision/{device_id}`` MQTT path and reports every
currently-tracked object with a ``status`` field ("loitering" once continuously tracked for at
least ``stop_duration_seconds``, else "normal") so consumers can query/filter on dwell time
themselves rather than only ever seeing already-loitering objects.

Identity across frames is the persistent per-object ``id`` assigned by DL Streamer's
``gvatrack`` element — the exact same field (and the exact same exact-match-by-id logic) the
standalone (no-VMS) Node-RED loitering flow uses, so behavior matches it: every object present
in ``tracked_objects`` is reported every update (no merging/re-identification heuristics), and
an object missing from a frame is dropped, so its dwell time resets if it later reappears.

The loitering ZONE is not hardcoded: it's read directly from the payload itself. DL Streamer's
``gvaattachroi`` element attaches a crop region to every frame (configured in the pipeline's
``config.json``, e.g. ``gvaattachroi roi=0,200,300,400``); this shows up as an object entry
with a ``bounding_box`` but no ``detection.confidence`` (see ``extract_zone_bbox``). Only
objects that sufficiently overlap that region count towards dwell time — mirroring Node-RED's
region-intersection (IOR) logic, just sourced from the pipeline's own attached ROI instead of a
separately drawn/hardcoded zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _Track:
    entry_time_s: float
    roi_type: str
    alerted: bool = False


def extract_zone_bbox(metadata: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Return the pipeline's attached inference ROI (gvaattachroi) as (x_min,y_min,x_max,y_max).

    That object carries a ``bounding_box`` but no ``detection.confidence``/``roi_type`` — it's
    the static crop region attached to every frame, not a real detection. Returns ``None`` if no
    such entry is present (e.g. a pipeline without ``gvaattachroi``), in which case callers treat
    every detection as "in zone".
    """
    for obj in metadata.get("objects", []):
        detection = obj.get("detection") or {}
        if detection.get("confidence") is not None:
            continue
        bbox = detection.get("bounding_box") or {}
        x_min, x_max = bbox.get("x_min"), bbox.get("x_max")
        y_min, y_max = bbox.get("y_min"), bbox.get("y_max")
        if None in (x_min, x_max, y_min, y_max):
            continue
        return (x_min, y_min, x_max, y_max)
    return None


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    x_min, y_min, x_max, y_max = rect
    return max(0.0, x_max - x_min) * max(0.0, y_max - y_min)


def _rect_intersection_area(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ix_min, iy_min = max(a[0], b[0]), max(a[1], b[1])
    ix_max, iy_max = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, ix_max - ix_min) * max(0.0, iy_max - iy_min)


def extract_tracked_objects(
    metadata: dict[str, Any],
    min_confidence: float = 0.1,
    zone_ior_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Pull ``{"track_id", "roi_type"}`` dicts for every qualifying, in-zone detection.

    Mirrors the standalone Node-RED extraction filter: skips objects missing a persistent
    tracker ``id`` (dwell time can't be computed without stable identity) or without a
    ``detection.confidence`` greater than ``min_confidence`` — this also excludes DL
    Streamer's ``gvaattachroi`` region, which is itself surfaced as an object entry (a
    static, always-present id with no confidence/roi_type) and would otherwise be
    misdetected as permanently loitering.

    When the payload carries a zone (see ``extract_zone_bbox``), an object only counts if its
    own bounding box overlaps the zone by at least ``zone_ior_threshold`` of its own area
    (intersection-over-object-area, matching Node-RED's ``intersection_type="object"`` default
    of 0.5) — an object with no bounding box is kept as-is (can't be zone-checked). Without a
    zone in the payload, every qualifying object counts (no filtering).
    """
    zone = extract_zone_bbox(metadata)
    tracked: list[dict[str, Any]] = []
    for obj in metadata.get("objects", []):
        track_id = obj.get("id")
        if track_id is None:
            continue
        detection = obj.get("detection") or {}
        confidence = detection.get("confidence")
        if confidence is None or confidence <= min_confidence:
            continue

        if zone is not None:
            bbox = detection.get("bounding_box") or {}
            x_min, x_max = bbox.get("x_min"), bbox.get("x_max")
            y_min, y_max = bbox.get("y_min"), bbox.get("y_max")
            if None not in (x_min, x_max, y_min, y_max):
                obj_rect = (x_min, y_min, x_max, y_max)
                obj_area = _rect_area(obj_rect)
                if obj_area > 0:
                    ior = _rect_intersection_area(obj_rect, zone) / obj_area
                    if ior < zone_ior_threshold:
                        continue

        label = obj.get("roi_type") or detection.get("label") or "object"
        tracked.append({"track_id": str(track_id), "roi_type": str(label)})
    return tracked


class LoiteringTracker:
    """Tracks per-device, per-object dwell time and reports a status snapshot per object."""

    def __init__(self, stop_duration_seconds: float) -> None:
        self._stop_duration_s = stop_duration_seconds
        self._devices: dict[str, dict[str, _Track]] = {}

    def update(
        self,
        device_id: str,
        tracked_objects: list[dict[str, Any]],
        timestamp_ms: int,
    ) -> list[dict[str, Any]]:
        """Update dwell state for one frame and return a snapshot of every tracked object.

        Every object in ``tracked_objects`` is returned (not just ones over threshold) so
        consumers can query/filter on ``dwell_seconds`` themselves; each entry carries a
        ``status`` field ("loitering" once ``dwell_seconds >= stop_duration_seconds``, else
        "normal") and a ``just_started_loitering`` flag that is ``True`` exactly once per
        loitering episode (the update where it first crosses the threshold) so callers can
        fire a one-time action (e.g. an Nx bookmark) instead of repeating it every update.
        Objects absent from ``tracked_objects`` are dropped, so dwell time (and the alerted
        flag) resets if an object leaves and later re-enters the frame (matches Node-RED's
        per-frame rebuild + exact-id-match behavior).
        """
        now_s = timestamp_ms / 1000.0
        previous = self._devices.get(device_id, {})
        current: dict[str, _Track] = {}

        for det in tracked_objects:
            track_id = det["track_id"]
            existing = previous.get(track_id)
            entry_time_s = existing.entry_time_s if existing else now_s
            alerted = existing.alerted if existing else False
            current[track_id] = _Track(
                entry_time_s=entry_time_s, roi_type=det["roi_type"], alerted=alerted
            )

        self._devices[device_id] = current

        snapshot = []
        for track_id, track in current.items():
            dwell_seconds = round(now_s - track.entry_time_s, 1)
            is_loitering = dwell_seconds >= self._stop_duration_s
            just_started = is_loitering and not track.alerted
            if just_started:
                track.alerted = True
            snapshot.append(
                {
                    "track_id": track_id,
                    "roi_type": track.roi_type,
                    "entry_time_ms": int(track.entry_time_s * 1000),
                    "dwell_seconds": dwell_seconds,
                    "status": "loitering" if is_loitering else "normal",
                    "just_started_loitering": just_started,
                }
            )
        return snapshot

    def reset(self, device_id: str | None = None) -> None:
        """Clear tracked state for one device, or all devices if none given."""
        if device_id is None:
            self._devices.clear()
        else:
            self._devices.pop(device_id, None)

