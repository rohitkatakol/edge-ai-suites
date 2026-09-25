# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the VMS-side loitering dwell-time tracker."""

from __future__ import annotations

from analytics_app_shim.object_detection.loitering_tracker import (
    LoiteringTracker,
    extract_tracked_objects,
    extract_zone_bbox,
)


def _obj(track_id, roi_type="person", confidence=0.9, bbox=None, **extra):
    detection = {"confidence": confidence, "label": roi_type}
    if bbox is not None:
        x_min, y_min, x_max, y_max = bbox
        detection["bounding_box"] = {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}
    return {"id": track_id, "roi_type": roi_type, "detection": detection, **extra}


def _zone_obj(x_min, y_min, x_max, y_max, track_id=1):
    # The gvaattachroi crop region: has a bounding_box but no confidence/roi_type.
    return {
        "id": track_id,
        "region_id": 0,
        "detection": {"bounding_box": {"x_min": x_min, "y_min": y_min, "x_max": x_max, "y_max": y_max}},
    }


def _det(track_id, roi_type="person"):
    return {"track_id": str(track_id), "roi_type": roi_type}


# ── extract_tracked_objects ────────────────────────────────────────────────────

def test_extract_tracked_objects_requires_id():
    metadata = {"objects": [_obj(7), {"detection": {"confidence": 0.9}, "roi_type": "car"}]}  # no id -> skipped
    tracked = extract_tracked_objects(metadata)
    assert tracked == [{"track_id": "7", "roi_type": "person"}]


def test_extract_tracked_objects_falls_back_to_detection_label():
    metadata = {"objects": [_obj(3)]}
    metadata["objects"][0].pop("roi_type")
    tracked = extract_tracked_objects(metadata)
    assert tracked == [{"track_id": "3", "roi_type": "person"}]  # falls back to detection.label


def test_extract_tracked_objects_skips_entries_without_confidence():
    # e.g. the gvaattachroi region itself, surfaced as an object with a static id
    # but no detection.confidence -- must not be mistaken for a real detection.
    metadata = {
        "objects": [
            {"detection": {"bounding_box": {}}, "id": 1, "region_id": 0},
            _obj(30, roi_type="pedestrian"),
        ]
    }
    assert extract_tracked_objects(metadata) == [{"track_id": "30", "roi_type": "pedestrian"}]


def test_extract_tracked_objects_returns_all_qualifying_objects_in_frame():
    """The bug this guards against: two distinct people in the same frame must both
    be returned, not merged/collapsed into one."""
    metadata = {"objects": [_obj(1, roi_type="pedestrian"), _obj(2, roi_type="pedestrian")]}
    tracked = extract_tracked_objects(metadata)
    assert {t["track_id"] for t in tracked} == {"1", "2"}


def test_extract_tracked_objects_filters_by_min_confidence():
    metadata = {"objects": [_obj(1, confidence=0.05)]}
    assert extract_tracked_objects(metadata, min_confidence=0.1) == []
    assert extract_tracked_objects(metadata, min_confidence=0.01) == [{"track_id": "1", "roi_type": "person"}]


def test_extract_tracked_objects_empty_when_no_objects():
    assert extract_tracked_objects({}) == []


# ── extract_zone_bbox ───────────────────────────────────────────────────────────

def test_extract_zone_bbox_finds_the_attached_roi():
    metadata = {"objects": [_zone_obj(0.0, 0.2, 0.3, 0.6), _obj(30, bbox=(0.1, 0.3, 0.2, 0.4))]}
    assert extract_zone_bbox(metadata) == (0.0, 0.2, 0.3, 0.6)


def test_extract_zone_bbox_none_when_no_such_entry():
    metadata = {"objects": [_obj(30, bbox=(0.1, 0.3, 0.2, 0.4))]}
    assert extract_zone_bbox(metadata) is None


# ── extract_tracked_objects: zone (gvaattachroi) IOR filtering ──────────────────

def test_extract_tracked_objects_keeps_object_fully_inside_zone():
    metadata = {
        "objects": [
            _zone_obj(0.0, 0.2, 0.3, 0.6),
            _obj(30, bbox=(0.05, 0.25, 0.15, 0.35)),  # entirely inside the zone
        ]
    }
    assert extract_tracked_objects(metadata) == [{"track_id": "30", "roi_type": "person"}]


def test_extract_tracked_objects_drops_object_outside_zone():
    metadata = {
        "objects": [
            _zone_obj(0.0, 0.2, 0.3, 0.6),
            _obj(30, bbox=(0.5, 0.5, 0.6, 0.6)),  # nowhere near the zone
        ]
    }
    assert extract_tracked_objects(metadata) == []


def test_extract_tracked_objects_zone_ior_threshold_boundary():
    # object half-inside/half-outside the zone: x in [0.2, 0.4], zone x_max=0.3 -> ~50% overlap
    metadata = {"objects": [_zone_obj(0.0, 0.0, 0.3, 1.0), _obj(30, bbox=(0.2, 0.0, 0.4, 1.0))]}
    assert extract_tracked_objects(metadata, zone_ior_threshold=0.49) == [
        {"track_id": "30", "roi_type": "person"}
    ]
    assert extract_tracked_objects(metadata, zone_ior_threshold=0.51) == []


def test_extract_tracked_objects_no_zone_in_payload_does_not_filter():
    metadata = {"objects": [_obj(30, bbox=(0.5, 0.5, 0.6, 0.6))]}
    assert extract_tracked_objects(metadata) == [{"track_id": "30", "roi_type": "person"}]


def test_extract_tracked_objects_object_without_bbox_unaffected_by_zone():
    metadata = {"objects": [_zone_obj(0.0, 0.2, 0.3, 0.6), _obj(30)]}  # no bbox on the real detection
    assert extract_tracked_objects(metadata) == [{"track_id": "30", "roi_type": "person"}]


# ── LoiteringTracker ────────────────────────────────────────────────────────────

def test_object_below_threshold_reported_with_normal_status():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    result = tracker.update("cam1", [_det(1)], timestamp_ms=0)
    assert len(result) == 1
    assert result[0]["track_id"] == "1"
    assert result[0]["status"] == "normal"
    assert result[0]["dwell_seconds"] == 0.0


def test_object_reaches_threshold_status_becomes_loitering():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    tracker.update("cam1", [_det(1)], timestamp_ms=0)
    result = tracker.update("cam1", [_det(1)], timestamp_ms=300_000)
    assert len(result) == 1
    assert result[0]["track_id"] == "1"
    assert result[0]["roi_type"] == "person"
    assert result[0]["status"] == "loitering"
    assert result[0]["dwell_seconds"] == 300.0
    assert result[0]["entry_time_ms"] == 0


def test_just_started_loitering_fires_once_per_episode():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    r0 = tracker.update("cam1", [_det(1)], timestamp_ms=0)
    assert r0[0]["just_started_loitering"] is False  # not loitering yet

    r1 = tracker.update("cam1", [_det(1)], timestamp_ms=300_000)
    assert r1[0]["status"] == "loitering"
    assert r1[0]["just_started_loitering"] is True  # just crossed the threshold

    r2 = tracker.update("cam1", [_det(1)], timestamp_ms=310_000)
    assert r2[0]["status"] == "loitering"
    assert r2[0]["just_started_loitering"] is False  # already alerted, no repeat


def test_just_started_loitering_refires_after_leaving_and_returning():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    tracker.update("cam1", [_det(1)], timestamp_ms=0)
    tracker.update("cam1", [_det(1)], timestamp_ms=300_000)  # alerted once
    tracker.update("cam1", [], timestamp_ms=301_000)  # leaves frame
    tracker.update("cam1", [_det(1)], timestamp_ms=302_000)  # new episode
    result = tracker.update("cam1", [_det(1)], timestamp_ms=602_000)
    assert result[0]["status"] == "loitering"
    assert result[0]["just_started_loitering"] is True  # fires again for the new episode


def test_two_distinct_objects_in_same_frame_both_reported():
    """The bug this guards against: two distinct people in the same frame must both
    be returned, one entry per track_id, never merged into a single entry."""
    tracker = LoiteringTracker(stop_duration_seconds=300)
    result = tracker.update("cam1", [_det(1), _det(2)], timestamp_ms=0)
    assert {o["track_id"] for o in result} == {"1", "2"}
    assert all(o["status"] == "normal" for o in result)


def test_object_leaving_frame_resets_dwell_time():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    tracker.update("cam1", [_det(1)], timestamp_ms=0)
    tracker.update("cam1", [], timestamp_ms=1000)  # object left the frame
    result = tracker.update("cam1", [_det(1)], timestamp_ms=301_000)
    # entry_time reset when object reappeared, so dwell (301000-301000=0) is below threshold
    assert len(result) == 1
    assert result[0]["status"] == "normal"
    assert result[0]["dwell_seconds"] == 0.0


def test_devices_tracked_independently():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    tracker.update("cam1", [_det(1)], timestamp_ms=0)
    tracker.update("cam2", [_det(1)], timestamp_ms=300_000)
    result_cam1 = tracker.update("cam1", [_det(1)], timestamp_ms=300_000)
    assert result_cam1[0]["status"] == "loitering"


def test_reset_clears_single_device():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    tracker.update("cam1", [_det(1)], timestamp_ms=0)
    tracker.update("cam2", [_det(1)], timestamp_ms=0)
    tracker.reset("cam1")
    result_cam1 = tracker.update("cam1", [_det(1)], timestamp_ms=300_000)
    result_cam2 = tracker.update("cam2", [_det(1)], timestamp_ms=300_000)
    assert result_cam1[0]["status"] == "normal"
    assert result_cam2[0]["status"] == "loitering"


def test_reset_all_devices():
    tracker = LoiteringTracker(stop_duration_seconds=300)
    tracker.update("cam1", [_det(1)], timestamp_ms=0)
    tracker.reset()
    result = tracker.update("cam1", [_det(1)], timestamp_ms=300_000)
    assert result[0]["status"] == "normal"
