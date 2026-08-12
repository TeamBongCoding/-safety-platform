import unittest

import numpy as np
from shapely.geometry import Polygon

from app.services.person_tracking import (
    CameraPersonTracker,
    GlobalIdentityManager,
    _maximum_weight_assignment,
)


def roi(points):
    return {"poly": Polygon(points)}


class GlobalIdentityManagerTests(unittest.TestCase):
    def test_assignment_is_globally_optimal_and_one_to_one(self):
        assignments = _maximum_weight_assignment(
            2,
            2,
            [
                (0.90, 0, 0),
                (0.80, 0, 1),
                (0.85, 1, 0),
                (0.10, 1, 1),
            ],
        )

        self.assertEqual(set(assignments), {(0, 1), (1, 0)})

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

    def test_overlap_merge_requires_repeated_confirmation(self):
        manager = GlobalIdentityManager()
        embedding = np.ones(144, dtype=np.float32)
        embedding /= np.linalg.norm(embedding)
        first_id, _ = manager.assign(1, embedding, 0.9, (0.5, 0.5), (0, 0), [], 100.0)
        second_id, _ = manager.assign(2, embedding, 0.9, (0.5, 0.5), (0, 0), [], 100.0)

        results = []
        for frame_index in range(5):
            timestamp = 101.0 + frame_index * 0.1
            manager.register_active_track(1, 1, first_id, embedding, 0.9, timestamp, True)
            manager.register_active_track(2, 1, second_id, embedding, 0.9, timestamp, True)
            results.append(
                manager.try_overlap_match(1, 1, first_id, embedding, 0.9, timestamp)
            )

        self.assertEqual(results[:4], [None] * 4)
        self.assertEqual(results[4], first_id)
        self.assertEqual(manager.resolve_id(second_id), first_id)

    def test_people_coexisting_in_one_camera_are_never_merged(self):
        manager = GlobalIdentityManager()
        embedding = np.ones(144, dtype=np.float32)
        embedding /= np.linalg.norm(embedding)
        first_id, _ = manager.assign(1, embedding, 0.9, (0.4, 0.5), (0, 0), [], 100.0)
        second_id, _ = manager.assign(1, embedding, 0.9, (0.6, 0.5), (0, 0), [], 100.0)

        for frame_index in range(6):
            timestamp = 101.0 + frame_index * 0.1
            manager.register_active_track(1, 1, first_id, embedding, 0.9, timestamp, True)
            manager.register_active_track(1, 2, second_id, embedding, 0.9, timestamp, True)
            manager.register_active_track(2, 1, second_id, embedding, 0.9, timestamp, True)
            merged = manager.try_overlap_match(
                1, 1, first_id, embedding, 0.9, timestamp
            )
            self.assertIsNone(merged)

        self.assertNotEqual(manager.resolve_id(first_id), manager.resolve_id(second_id))


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

    def test_fixed_contact_point_survives_large_box_size_change(self):
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
            [{"box": [52, 78, 68, 110], "conf": 0.9}],
            160,
            120,
            [],
            [],
            101.0,
        )[0]

        self.assertEqual(second.local_track_id, first.local_track_id)
        self.assertEqual(second.global_person_id, first.global_person_id)

    def test_same_appearance_keeps_id_after_large_position_change(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        first_frame = np.zeros((120, 240, 3), dtype=np.uint8)
        first_frame[10:110, 10:70] = (20, 40, 220)
        first = tracker.update(
            first_frame,
            [{"box": [10, 10, 70, 110], "conf": 0.9}],
            240,
            120,
            [],
            [],
            100.0,
        )[0]

        second_frame = np.zeros((120, 240, 3), dtype=np.uint8)
        second_frame[10:110, 170:230] = (20, 40, 220)
        second = tracker.update(
            second_frame,
            [{"box": [170, 10, 230, 110], "conf": 0.9}],
            240,
            120,
            [],
            [],
            101.0,
        )[0]

        self.assertEqual(second.local_track_id, first.local_track_id)
        self.assertEqual(second.global_person_id, first.global_person_id)

    def test_crossing_people_follow_appearance_instead_of_position(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        left_box = [10, 10, 70, 110]
        right_box = [170, 10, 230, 110]
        first_frame = np.zeros((120, 240, 3), dtype=np.uint8)
        first_frame[10:110, 10:70] = (20, 40, 220)
        first_frame[10:110, 170:230] = (220, 40, 20)
        first = tracker.update(
            first_frame,
            [
                {"box": left_box, "conf": 0.9},
                {"box": right_box, "conf": 0.9},
            ],
            240,
            120,
            [],
            [],
            100.0,
        )
        red_id = next(track.local_track_id for track in first if track.box == left_box)
        blue_id = next(track.local_track_id for track in first if track.box == right_box)

        crossed_frame = np.zeros((120, 240, 3), dtype=np.uint8)
        crossed_frame[10:110, 10:70] = (220, 40, 20)
        crossed_frame[10:110, 170:230] = (20, 40, 220)
        crossed = tracker.update(
            crossed_frame,
            [
                {"box": left_box, "conf": 0.9},
                {"box": right_box, "conf": 0.9},
            ],
            240,
            120,
            [],
            [],
            101.0,
        )

        self.assertEqual(next(t.local_track_id for t in crossed if t.box == right_box), red_id)
        self.assertEqual(next(t.local_track_id for t in crossed if t.box == left_box), blue_id)

    def test_two_overlapping_people_keep_unique_ids(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        first = tracker.update(
            frame,
            [
                {"box": [20, 10, 70, 110], "conf": 0.9},
                {"box": [90, 10, 140, 110], "conf": 0.9},
            ],
            160,
            120,
            [],
            [],
            100.0,
        )
        second = tracker.update(
            frame,
            [
                {"box": [48, 10, 98, 110], "conf": 0.9},
                {"box": [62, 10, 112, 110], "conf": 0.9},
            ],
            160,
            120,
            [],
            [],
            101.0,
        )

        self.assertEqual(len(second), 2)
        self.assertEqual(len({track.local_track_id for track in second}), 2)
        self.assertEqual(len({track.global_person_id for track in second}), 2)

    def test_stationary_track_recovers_after_temporary_detection_loss(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        detection = [{"box": [30, 10, 90, 110], "conf": 0.9}]
        first = None
        for frame_index in range(3):
            current = tracker.update(
                frame, detection, 160, 120, [], [], 100.0 + frame_index
            )[0]
            first = first or current

        for frame_index in range(20):
            tracker.update(frame, [], 160, 120, [], [], 103.0 + frame_index)

        recovered = tracker.update(
            frame, detection, 160, 120, [], [], 123.0
        )[0]
        self.assertEqual(recovered.local_track_id, first.local_track_id)
        self.assertEqual(recovered.global_person_id, first.global_person_id)

    def test_pose_fallback_track_remains_visible_during_short_dropout(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        detection = [{
            "box": [40, 70, 125, 110],
            "conf": 0.75,
            "source": "pose_fallback",
        }]
        first = tracker.update(
            frame, detection, 160, 120, [], [], 100.0
        )[0]
        first.behavior_hold = True

        held = []
        for frame_index in range(5):
            held = tracker.update(
                frame, [], 160, 120, [], [], 100.1 + frame_index * 0.1
            )

        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].local_track_id, first.local_track_id)
        self.assertEqual(held[0].global_person_id, first.global_person_id)

        expired = tracker.update(frame, [], 160, 120, [], [], 103.1)
        self.assertEqual(expired, [])

    def test_low_profile_pose_gets_enhanced_hold_before_state_confirmation(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        first = tracker.update(
            frame,
            [{
                "box": [40, 70, 125, 110],
                "conf": 0.75,
                "source": "pose_fallback",
            }],
            160,
            120,
            [],
            [],
            100.0,
        )[0]

        held = tracker.update(frame, [], 160, 120, [], [], 102.0)
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].local_track_id, first.local_track_id)

        expired = tracker.update(frame, [], 160, 120, [], [], 103.1)
        self.assertEqual(expired, [])

    def test_pose_fallback_without_lying_state_does_not_leave_ghost_box(self):
        manager = GlobalIdentityManager()
        tracker = CameraPersonTracker(1, manager)
        frame = np.full((120, 160, 3), (30, 120, 220), dtype=np.uint8)
        tracker.update(
            frame,
            [{
                "box": [55, 10, 95, 110],
                "conf": 0.75,
                "source": "pose_fallback",
            }],
            160,
            120,
            [],
            [],
            100.0,
        )

        visible = tracker.update(frame, [], 160, 120, [], [], 101.0)
        self.assertEqual(visible, [])

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
