import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
from shapely.geometry import Polygon

from app.services.person_tracking import CameraPersonTracker
from app.services.pipeline import process_frame
from app.services.pose_behavior_detector import BehaviorState, behavior_event_type
from app.services.safety_rules import evaluate
from app.services.analysis_service import should_persist_event
from app.schemas import EventOut


class SafetyRuleTests(unittest.TestCase):
    def test_no_helmet_is_allowed_outside_zone(self):
        level, violations = evaluate("outside", None, helmet_on=False)
        self.assertEqual(level, "ok")
        self.assertEqual(violations, [])

    def test_unknown_helmet_state_does_not_create_violation(self):
        level, violations = evaluate("outside", None, helmet_on=None)
        self.assertEqual(level, "ok")
        self.assertEqual(violations, [])

    def test_helmet_and_zone_violations_are_separate(self):
        zone = {"zone_type": "fall_risk", "risk_level": "high"}
        level, violations = evaluate("inside", zone, helmet_on=False)
        self.assertEqual(level, "alert")
        self.assertEqual(
            [item["type"] for item in violations],
            ["no_helmet", "fall_risk_entry"],
        )

    def test_low_risk_inside_zone_is_warning(self):
        zone = {"zone_type": "heavy_equip", "risk_level": "low"}
        level, violations = evaluate("inside", zone, helmet_on=True)
        self.assertEqual(level, "warn")
        self.assertEqual(violations[0]["type"], "heavy_equipment_entry")

    def test_critical_zone_approach_is_alert(self):
        zone = {"zone_type": "no_entry", "risk_level": "critical"}
        level, violations = evaluate("near", zone, helmet_on=True)
        self.assertEqual(level, "alert")
        self.assertEqual(violations[0]["type"], "zone_approach")

    def test_work_area_only_warns_for_missing_helmet_inside(self):
        zone = {"zone_type": "work_area", "risk_level": "high"}
        level, violations = evaluate("inside", zone, helmet_on=False)
        self.assertEqual(level, "alert")
        self.assertEqual([item["type"] for item in violations], ["no_helmet"])
        self.assertTrue(violations[0]["zone_related"])

    def test_entering_work_area_with_helmet_is_not_an_event(self):
        zone = {"zone_type": "work_area", "risk_level": "high"}
        level, violations = evaluate("inside", zone, helmet_on=True)
        self.assertEqual(level, "ok")
        self.assertEqual(violations, [])


class EventLogPolicyTests(unittest.TestCase):
    def test_event_timestamp_is_serialized_as_korean_time(self):
        event = EventOut(
            id=1,
            timestamp=datetime(2026, 8, 12, 16, 30, 0),
            event_type="no_helmet",
            zone_id=1,
            snapshot_path=None,
            confidence=0.9,
            resolved=False,
        )
        self.assertEqual(
            event.model_dump(mode="json")["timestamp"],
            "2026-08-13T01:30:00+09:00",
        )

    def test_utc_event_timestamp_is_converted_to_korean_time(self):
        event = EventOut(
            id=1,
            timestamp=datetime(2026, 8, 12, 7, 30, 0, tzinfo=timezone.utc),
            event_type="no_helmet",
            zone_id=1,
            snapshot_path=None,
            confidence=0.9,
            resolved=False,
        )
        self.assertEqual(
            event.model_dump(mode="json")["timestamp"],
            "2026-08-12T16:30:00+09:00",
        )

    def test_general_area_no_helmet_is_not_persisted(self):
        self.assertFalse(should_persist_event({
            "type": "no_helmet",
            "in_risk_zone": False,
        }))

    def test_risk_zone_no_helmet_is_persisted(self):
        self.assertTrue(should_persist_event({
            "type": "no_helmet",
            "in_risk_zone": True,
        }))

    def test_stagger_is_never_persisted(self):
        self.assertFalse(should_persist_event({"type": "stagger"}))
        self.assertFalse(should_persist_event({"type": "heat_stagger"}))

    def test_other_behavior_events_are_persisted(self):
        self.assertTrue(should_persist_event({"type": "fall"}))
        self.assertTrue(should_persist_event({"type": "heat_sudden_sit"}))


class BehaviorEventMeaningTests(unittest.TestCase):
    def test_non_heat_zone_behaviors_use_plain_event_types(self):
        expected = {
            BehaviorState.STAGGER: "stagger",
            BehaviorState.SUDDEN_SIT: "sudden_sit",
            BehaviorState.FALL: "fall",
            BehaviorState.FALL_STILL: "fall_still",
        }
        self.assertEqual(
            {state: behavior_event_type(state, False) for state in expected},
            expected,
        )

    def test_heat_zone_behaviors_use_heat_event_types(self):
        expected = {
            BehaviorState.STAGGER: "heat_stagger",
            BehaviorState.SUDDEN_SIT: "heat_sudden_sit",
            BehaviorState.FALL: "heat_fall",
            BehaviorState.FALL_STILL: "heat_fall_still",
        }
        self.assertEqual(
            {state: behavior_event_type(state, True) for state in expected},
            expected,
        )


class SafetyPipelineTests(unittest.TestCase):
    def test_events_have_stable_types_and_track_id(self):
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        detections = [
            {"cls": "person", "box": [30, 10, 90, 110], "conf": 0.9},
            {"cls": "no-helmet", "box": [45, 12, 70, 35], "conf": 0.85},
        ]
        zones = [{
            "id": 7,
            "zone_type": "fall_risk",
            "risk_level": "high",
            "poly": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        }]

        with patch("app.services.pipeline.draw_status", side_effect=lambda image, *args, **kwargs: image):
            _, events, status = process_frame(
                frame,
                detections,
                zones,
                160,
                120,
                CameraPersonTracker(),
                include_status=True,
            )

        self.assertEqual(status["no_helmet_count"], 1)
        self.assertEqual(
            {event["type"] for event in events},
            {"no_helmet", "fall_risk_entry"},
        )
        self.assertEqual({event["track_id"] for event in events}, {"person-000001"})
        by_type = {event["type"]: event for event in events}
        self.assertEqual(by_type["no_helmet"]["zone_id"], 7)
        self.assertEqual(by_type["fall_risk_entry"]["zone_id"], 7)

    def test_general_area_no_helmet_is_not_an_event_or_count(self):
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        detections = [
            {"cls": "person", "box": [30, 10, 90, 110], "conf": 0.9},
            {"cls": "no-helmet", "box": [45, 12, 70, 35], "conf": 0.85},
        ]

        with patch("app.services.pipeline.draw_status", side_effect=lambda image, *args, **kwargs: image):
            _, events, status = process_frame(
                frame,
                detections,
                [],
                160,
                120,
                CameraPersonTracker(),
                include_status=True,
            )

        self.assertEqual(events, [])
        self.assertEqual(status["no_helmet_count"], 0)
        self.assertEqual(status["workers"][0]["level"], "ok")


if __name__ == "__main__":
    unittest.main()
