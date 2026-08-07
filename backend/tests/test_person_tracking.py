import unittest

import numpy as np
from shapely.geometry import Polygon

from app.services.person_tracking import CameraPersonTracker, GlobalIdentityManager


def roi(points):
    return {"poly": Polygon(points)}


class GlobalIdentityManagerTests(unittest.TestCase):
    def test_global_id_is_reused_between_exit_and_entry(self):
        manager = GlobalIdentityManager()
        embedding = np.ones(144, dtype=np.float32)
        embedding /= np.linalg.norm(embedding)
        exit_zone = roi([(0.75, 0), (1, 0), (1, 1), (0.75, 1)])
        entry_zone = roi([(0, 0), (0.25, 0), (0.25, 1), (0, 1)])

        first_id, _ = manager.assign(1, embedding, 0.9, (0.5, 0.8), (0, 0), [], 100.0)
        self.assertTrue(
            manager.register_departure(
                first_id,
                1,
                embedding,
                0.9,
                (0.9, 0.8),
                (1, 0),
                [exit_zone],
                101.0,
            )
        )
        second_id, details = manager.assign(
            2,
            embedding,
            0.9,
            (0.1, 0.8),
            (1, 0),
            [entry_zone],
            103.0,
        )

        self.assertEqual(second_id, first_id)
        self.assertEqual(details["matched_from_camera_id"], 1)
        self.assertEqual(details["transition_seconds"], 2.0)

    def test_same_camera_does_not_consume_transition_candidate(self):
        manager = GlobalIdentityManager()
        embedding = np.ones(144, dtype=np.float32)
        embedding /= np.linalg.norm(embedding)
        zone = roi([(0, 0), (1, 0), (1, 1), (0, 1)])
        first_id, _ = manager.assign(1, embedding, 0.9, (0.5, 0.5), (0, 0), [], 100.0)
        manager.register_departure(
            first_id, 1, embedding, 0.9, (0.5, 0.5), (1, 0), [zone], 101.0
        )

        second_id, details = manager.assign(
            1, embedding, 0.9, (0.5, 0.5), (1, 0), [zone], 102.0
        )

        self.assertNotEqual(second_id, first_id)
        self.assertIsNone(details)


class CameraPersonTrackerTests(unittest.TestCase):
    def test_local_track_id_stays_stable(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        first = tracker.update(
            frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
            [],
            [],
            100.0,
        )[0]
        second = tracker.update(
            frame,
            [{"box": [33, 10, 93, 110], "conf": 0.9}],
            160,
            120,
            [],
            [],
            101.0,
        )[0]

        self.assertEqual(second.local_track_id, first.local_track_id)
        self.assertEqual(second.global_person_id, first.global_person_id)

    def test_entry_track_retries_after_exit_candidate_arrives_late(self):
        manager = GlobalIdentityManager()
        source_tracker = CameraPersonTracker(1, manager)
        destination_tracker = CameraPersonTracker(2, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        full_roi = roi([(0, 0), (1, 0), (1, 1), (0, 1)])
        detection = [{"box": [30, 10, 90, 110], "conf": 0.9}]

        source = source_tracker.update(
            frame, detection, 160, 120, [], [], 99.0
        )[0]
        destination_before = destination_tracker.update(
            frame, detection, 160, 120, [full_roi], [], 100.0
        )[0]
        self.assertNotEqual(destination_before.global_person_id, source.global_person_id)

        source_tracker.update(
            frame, detection, 160, 120, [], [full_roi], 101.0
        )
        destination_after = destination_tracker.update(
            frame, detection, 160, 120, [full_roi], [], 102.0
        )[0]

        self.assertEqual(destination_after.global_person_id, source.global_person_id)
        self.assertEqual(destination_after.match_details["matched_from_camera_id"], 1)

    def test_exit_roi_registers_candidate_before_track_disappears(self):
        manager = GlobalIdentityManager()
        source_tracker = CameraPersonTracker(1, manager)
        destination_tracker = CameraPersonTracker(2, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        full_roi = roi([(0, 0), (1, 0), (1, 1), (0, 1)])
        detection = [{"box": [30, 10, 90, 110], "conf": 0.9}]

        source = source_tracker.update(
            frame, detection, 160, 120, [], [full_roi], 100.0
        )[0]
        destination = destination_tracker.update(
            frame, detection, 160, 120, [full_roi], [], 101.0
        )[0]

        self.assertEqual(destination.global_person_id, source.global_person_id)
        self.assertEqual(destination.match_details["matched_from_camera_id"], 1)


if __name__ == "__main__":
    unittest.main()
