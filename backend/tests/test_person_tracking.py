import unittest

import numpy as np

from app.services.person_tracking import CameraPersonTracker, HeatExposureTracker


class CameraPersonTrackerTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)

    def test_track_id_stays_stable_for_nearby_detection(self):
        tracker = CameraPersonTracker()
        first = tracker.update(
            self.frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
            100.0,
        )[0]
        second = tracker.update(
            self.frame,
            [{"box": [33, 10, 93, 110], "conf": 0.9}],
            160,
            120,
            101.0,
        )[0]

        self.assertEqual(second.local_track_id, first.local_track_id)
        self.assertEqual(second.track_id, first.track_id)

    def test_different_people_receive_different_track_ids(self):
        tracker = CameraPersonTracker()
        tracks = tracker.update(
            self.frame,
            [
                {"box": [5, 10, 45, 110], "conf": 0.9},
                {"box": [110, 10, 150, 110], "conf": 0.9},
            ],
            160,
            120,
        )
        self.assertEqual(len({track.track_id for track in tracks}), 2)

    def test_track_expires_after_missed_frame_limit(self):
        tracker = CameraPersonTracker()
        first = tracker.update(
            self.frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
        )[0]
        for _ in range(13):
            tracker.update(self.frame, [], 160, 120)
        replacement = tracker.update(
            self.frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
        )[0]
        self.assertNotEqual(replacement.track_id, first.track_id)


class HeatExposureTrackerTests(unittest.TestCase):
    def test_exposure_is_retained_until_ten_seconds_outside(self):
        tracker = HeatExposureTracker()
        self.assertEqual(tracker.update("person-000001", True, now=10.0), 0.0)
        self.assertEqual(tracker.update("person-000001", True, now=11.0), 1.0)
        self.assertEqual(tracker.update("person-000001", False, now=12.0), 2.0)
        self.assertEqual(tracker.update("person-000001", False, now=20.0), 2.0)
        self.assertEqual(tracker.update("person-000001", True, now=21.0), 2.0)
        self.assertEqual(tracker.update("person-000001", True, now=22.0), 3.0)

    def test_exposure_resets_after_ten_continuous_seconds_outside(self):
        tracker = HeatExposureTracker()
        tracker.update("person-000001", True, now=10.0)
        self.assertEqual(tracker.update("person-000001", True, now=11.0), 1.0)
        self.assertEqual(tracker.update("person-000001", False, now=12.0), 2.0)
        self.assertEqual(tracker.update("person-000001", False, now=21.9), 2.0)
        self.assertEqual(tracker.update("person-000001", False, now=22.0), 0.0)
        self.assertEqual(tracker.update("person-000001", True, now=23.0), 0.0)


if __name__ == "__main__":
    unittest.main()
