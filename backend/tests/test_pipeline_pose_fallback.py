import unittest

import numpy as np

from app.services.pipeline import _add_pose_fallback_persons
from app.services.overlay import draw_status
from app.services.brightness import estimate_sun_shade, frame_avg_brightness
from app.services.pose_behavior_detector import BehaviorState, PoseBehaviorDetector


class PoseFallbackTests(unittest.TestCase):
    def test_pose_detector_can_reset_between_video_sources(self):
        detector = PoseBehaviorDetector()
        features = {
            "hip_cx": 110.0,
            "hip_cy": 110.0,
            "shoulder_cx": 75.0,
            "shoulder_cy": 155.0,
        }
        detector.update(1, 2, features, [50.0, 80.0, 140.0, 180.0], 100.0)

        detector.reset()

        self.assertIsNone(detector.current(1, 2, 100.1))

    def test_missing_horizontal_person_is_added_from_pose(self):
        keypoints = np.zeros((17, 3), dtype=np.float32)
        keypoints[:, 2] = 0.8

        persons = _add_pose_fallback_persons(
            [{"cls": "person", "box": [10, 10, 40, 100], "conf": 0.9}],
            [([60, 70, 140, 105], keypoints)],
        )

        self.assertEqual(len(persons), 2)
        self.assertEqual(persons[1]["source"], "pose_fallback")

    def test_pose_box_does_not_duplicate_existing_person(self):
        keypoints = np.zeros((17, 3), dtype=np.float32)
        keypoints[:, 2] = 0.8

        persons = _add_pose_fallback_persons(
            [{"cls": "person", "box": [10, 10, 50, 100], "conf": 0.9}],
            [([12, 12, 52, 102], keypoints)],
        )

        self.assertEqual(len(persons), 1)

    def test_horizontal_pose_becomes_fall_after_confirmation_time(self):
        detector = PoseBehaviorDetector()
        features = {
            "hip_cx": 100.0,
            "hip_cy": 90.0,
            "shoulder_cx": 60.0,
            "shoulder_cy": 120.0,
        }
        result = None
        for timestamp in (100.0, 100.3, 100.6, 100.9, 101.2):
            result = detector.update(
                1, 1, features, [40.0, 60.0, 150.0, 130.0], timestamp
            )

        self.assertIn(result.state, (BehaviorState.FALL, BehaviorState.FALL_STILL))

    def test_pushup_geometry_is_lying_not_sudden_sit(self):
        detector = PoseBehaviorDetector()
        features = {
            "hip_cx": 110.0,
            "hip_cy": 110.0,
            "shoulder_cx": 75.0,
            "shoulder_cy": 155.0,
        }
        result = detector.update(
            1, 2, features, [50.0, 80.0, 140.0, 180.0], 100.0
        )

        self.assertEqual(result.state, BehaviorState.LYING)
        self.assertEqual(result.label, "누워있음")

    def test_lying_state_survives_brief_pose_dropout(self):
        detector = PoseBehaviorDetector()
        features = {
            "hip_cx": 110.0,
            "hip_cy": 110.0,
            "shoulder_cx": 75.0,
            "shoulder_cy": 155.0,
        }
        detector.update(1, 2, features, [50.0, 80.0, 140.0, 180.0], 100.0)

        cached = detector.current(1, 2, 100.5)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.state, BehaviorState.LYING)

    def test_bright_ground_below_person_is_sun(self):
        frame = np.full((200, 200, 3), 80, dtype=np.uint8)
        frame[150:190, 40:120] = 220

        status = estimate_sun_shade(
            frame, [60, 40, 100, 145], frame_avg_brightness(frame)
        )
        self.assertEqual(status, "sun")

    def test_status_panel_does_not_cover_person_box(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        rendered = draw_status(
            frame,
            [80, 120, 180, 220],
            True,
            None,
            "ok",
            "person-000001",
            1,
            behavior_state=BehaviorState.FALL,
        )

        # Ignore the two-pixel rectangle border; the person area stays clear.
        self.assertEqual(int(rendered[123:217, 83:177].sum()), 0)


if __name__ == "__main__":
    unittest.main()
