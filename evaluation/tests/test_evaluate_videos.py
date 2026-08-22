import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import evaluate_videos as evaluator


class EvaluationHelpersTests(unittest.TestCase):
    def test_resolves_clip_id_prefixed_recording(self):
        row = {"clip_id": "C001", "file_name": "sample.mp4"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "C001sample.mp4"
            path.touch()
            self.assertEqual(
                evaluator.resolve_video_path(row, Path(directory)),
                path,
            )

    def test_loads_completed_clips_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text(
                json.dumps({"clips": [{"clip_id": "C002", "excluded": False}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                evaluator.load_resume_results([path]),
                {"C002": {"clip_id": "C002", "excluded": False}},
            )

    def test_zone_only_clips_are_excluded(self):
        zone_row = {
            "scenario_action": "enter_zone",
            "expected_events": "zone_intrusion",
        }
        helmet_row = {
            "scenario_action": "stand",
            "expected_events": "no_helmet",
        }
        self.assertTrue(evaluator.is_zone_only_clip(zone_row))
        self.assertFalse(evaluator.is_zone_only_clip(helmet_row))

    def test_heat_fixture_uses_video_time_segments(self):
        fixture = {
            "segments": [
                {"start_sec": 0, "end_sec": 3, "in_heat": True},
                {"start_sec": 3, "end_sec": 14, "in_heat": False},
                {"start_sec": 14, "end_sec": None, "in_heat": True},
            ]
        }
        self.assertTrue(evaluator.fixture_in_heat(fixture, 2.9))
        self.assertFalse(evaluator.fixture_in_heat(fixture, 3.0))
        self.assertTrue(evaluator.fixture_in_heat(fixture, 14.0))

    def test_behavior_priority_selects_most_severe_state(self):
        self.assertEqual(
            evaluator.predicted_behavior({"NORMAL", "FALL", "FALL_STILL"}),
            "FALL_STILL",
        )

    def test_unknown_helmet_is_not_counted_as_correct_clip(self):
        result = {
            "clip_id": "C001",
            "test_group": "helmet_zone",
            "excluded": False,
            "expected_helmet_on": True,
            "predicted_helmet": "unknown",
            "expected_behavior_state": "NORMAL",
            "predicted_behavior_state": "NORMAL",
            "expected_events": [],
            "predicted_event_counts": {},
            "expected_rest_needed": False,
            "observed_rest_needed": False,
            "expected_timer_reset": False,
            "observed_timer_reset": False,
            "expected_person_count": 1,
            "predicted_person_count": 1,
            "expected_id_switch_count": None,
            "predicted_id_switch_proxy": 0,
        }
        metrics = evaluator.aggregate_metrics([result])
        self.assertEqual(metrics["helmet"]["known_coverage"], 0.0)
        self.assertEqual(
            metrics["helmet"]["clip_accuracy_including_unknown"],
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
