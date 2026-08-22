import unittest
from unittest.mock import patch

import numpy as np

from app.config import TRACK_MAX_MISSED_FRAMES
from app.services.person_tracking import CameraPersonTracker, HeatExposureTracker
from app.services.pipeline import process_frame


class SequencedBotTracker:
    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = 0

    def update(self, *_args, **_kwargs):
        index = min(self.calls, len(self.frames) - 1)
        self.calls += 1
        return np.asarray(self.frames[index], dtype=np.float32)

    def reset(self):
        return None


def bot_row(box, bot_id, detection_index):
    return [*box, bot_id, 0.9, 0, detection_index]


def _center_x(box):
    return (box[0] + box[2]) / 2.0


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

    def test_track_id_is_restored_after_short_disappearance(self):
        tracker = CameraPersonTracker()
        first = tracker.update(
            self.frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
        )[0]
        for _ in range(TRACK_MAX_MISSED_FRAMES + 1):
            tracker.update(self.frame, [], 160, 120)
        replacement = tracker.update(
            self.frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
        )[0]
        self.assertEqual(replacement.track_id, first.track_id)

    def test_reid_corrects_existing_bot_ids_swapped_after_crossing(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([
            [
                bot_row([10, 10, 40, 110], 101, 0),
                bot_row([120, 10, 150, 110], 202, 1),
            ],
            [
                bot_row([115, 10, 145, 110], 202, 0),
                bot_row([15, 10, 45, 110], 101, 1),
            ],
        ])
        person_a = np.asarray([1.0, 0.0], dtype=np.float32)
        person_b = np.asarray([0.0, 1.0], dtype=np.float32)

        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            side_effect=[
                np.stack([person_a, person_b]),
                np.stack([person_a, person_b]),
            ],
        ):
            first = tracker.update(
                self.frame,
                [
                    {"box": [10, 10, 40, 110], "conf": 0.9},
                    {"box": [120, 10, 150, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=10.0,
            )
            crossed = tracker.update(
                self.frame,
                [
                    {"box": [115, 10, 145, 110], "conf": 0.9},
                    {"box": [15, 10, 45, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=11.0,
            )

        first_ids = [track.track_id for track in first]
        by_id = {track.track_id: track for track in crossed}
        self.assertEqual(_center_x(by_id[first_ids[0]].box), 130.0)
        self.assertEqual(_center_x(by_id[first_ids[1]].box), 30.0)

    def test_ambiguous_appearance_does_not_swap_ids_from_motion_alone(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([
            [
                bot_row([10, 10, 40, 110], 101, 0),
                bot_row([120, 10, 150, 110], 202, 1),
            ],
            [
                bot_row([40, 10, 70, 110], 101, 0),
                bot_row([90, 10, 120, 110], 202, 1),
            ],
            [],
            [
                bot_row([95, 10, 125, 110], 202, 0),
                bot_row([35, 10, 65, 110], 101, 1),
            ],
        ])
        same_clothes = np.asarray([1.0, 0.0], dtype=np.float32)
        both = np.stack([same_clothes, same_clothes])

        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            side_effect=[both, both, np.empty((0, 2), dtype=np.float32), both],
        ):
            first = tracker.update(
                self.frame,
                [
                    {"box": [10, 10, 40, 110], "conf": 0.9},
                    {"box": [120, 10, 150, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=20.0,
            )
            tracker.update(
                self.frame,
                [
                    {"box": [40, 10, 70, 110], "conf": 0.9},
                    {"box": [90, 10, 120, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=21.0,
            )
            tracker.update(self.frame, [], 160, 120, timestamp=22.0)
            separated = tracker.update(
                self.frame,
                [
                    {"box": [95, 10, 125, 110], "conf": 0.9},
                    {"box": [35, 10, 65, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=23.0,
            )

        first_ids = [track.track_id for track in first]
        by_id = {track.track_id: track for track in separated}
        self.assertEqual(_center_x(by_id[first_ids[0]].box), 50.0)
        self.assertEqual(_center_x(by_id[first_ids[1]].box), 110.0)

    def test_overlap_does_not_contaminate_clean_reid_gallery(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([
            [
                bot_row([10, 10, 50, 110], 101, 0),
                bot_row([110, 10, 150, 110], 202, 1),
            ],
            [
                bot_row([40, 10, 100, 110], 101, 0),
                bot_row([50, 10, 110, 110], 202, 1),
            ],
        ])
        clean = np.eye(2, dtype=np.float32)
        contaminated = np.asarray(
            [[0.707, 0.707], [0.707, 0.707]],
            dtype=np.float32,
        )

        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            side_effect=[clean, contaminated],
        ):
            tracker.update(
                self.frame,
                [
                    {"box": [10, 10, 50, 110], "conf": 0.9},
                    {"box": [110, 10, 150, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=30.0,
            )
            tracker.update(
                self.frame,
                [
                    {"box": [40, 10, 100, 110], "conf": 0.9},
                    {"box": [50, 10, 110, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=31.0,
            )

        self.assertEqual(set(tracker._identities), {1, 2})
        self.assertEqual(
            [len(track.embedding_gallery) for track in tracker._identities.values()],
            [1, 1],
        )

    def test_moderate_reid_switch_requires_three_clean_frames(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([[
            bot_row([10, 10, 40, 110], 101, 0),
            bot_row([120, 10, 150, 110], 202, 1),
        ]])
        person_a = np.asarray([1.0, 0.0], dtype=np.float32)
        person_b = np.asarray([0.0, 1.0], dtype=np.float32)
        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            return_value=np.stack([person_a, person_b]),
        ):
            tracker.update(
                self.frame,
                [
                    {"box": [10, 10, 40, 110], "conf": 0.9},
                    {"box": [120, 10, 150, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=10.0,
            )

        observations = [
            {
                "box": [120, 10, 150, 110],
                "bot_id": 202,
                "embedding": np.asarray([0.8, 0.6], dtype=np.float32),
                "quality": 0.9,
            },
            {
                "box": [10, 10, 40, 110],
                "bot_id": 101,
                "embedding": np.asarray([0.6, 0.8], dtype=np.float32),
                "quality": 0.9,
            },
        ]
        tracker._appearance_focus_until = 20.0

        first, first_uncertain = tracker._assign_identities(observations, 10.1, 160, 120)
        second, second_uncertain = tracker._assign_identities(observations, 10.2, 160, 120)
        third, third_uncertain = tracker._assign_identities(observations, 10.3, 160, 120)

        self.assertEqual(first, {0: 2, 1: 1})
        self.assertEqual(second, {0: 2, 1: 1})
        self.assertEqual(third, {0: 1, 1: 2})
        self.assertEqual(first_uncertain, {0, 1})
        self.assertEqual(second_uncertain, {0, 1})
        self.assertEqual(third_uncertain, set())

    def test_active_ble_identity_survives_gallery_timeout(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([[bot_row([10, 10, 40, 110], 101, 0)]])
        embedding = np.asarray([1.0, 0.0], dtype=np.float32)
        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            return_value=np.asarray([embedding]),
        ):
            original = tracker.update(
                self.frame,
                [{"box": [10, 10, 40, 110], "conf": 0.9}],
                160,
                120,
                timestamp=1.0,
            )[0]

        self.assertTrue(tracker.bind_tag("UUID:1:1", original.track_id))
        tracker.set_active_ble_tags({"UUID:1:1"})
        assigned, _ = tracker._assign_identities(
            [{
                "box": [120, 10, 150, 110],
                "bot_id": 999,
                "embedding": embedding,
                "quality": 0.9,
            }],
            200.0,
            160,
            120,
        )
        self.assertEqual(assigned, {0: original.local_track_id})

    def test_nearby_people_request_dense_inference(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([[
            bot_row([10, 10, 50, 110], 101, 0),
            bot_row([55, 10, 95, 110], 202, 1),
        ]])
        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            return_value=np.eye(2, dtype=np.float32),
        ):
            tracker.update(
                self.frame,
                [
                    {"box": [10, 10, 50, 110], "conf": 0.9},
                    {"box": [55, 10, 95, 110], "conf": 0.9},
                ],
                160,
                120,
                timestamp=10.0,
            )

        self.assertTrue(tracker.needs_dense_inference(timestamp=10.1))

    def test_skipped_frame_predicts_display_box_without_moving_clean_anchor(self):
        tracker = CameraPersonTracker()
        tracker._tracker = SequencedBotTracker([
            [bot_row([10, 10, 40, 110], 101, 0)],
            [bot_row([20, 10, 50, 110], 101, 0)],
        ])
        embedding = np.asarray([[1.0, 0.0]], dtype=np.float32)
        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            side_effect=[embedding, embedding],
        ):
            tracker.update(
                self.frame,
                [{"box": [10, 10, 40, 110], "conf": 0.9}],
                160,
                120,
                timestamp=10.0,
            )
            track = tracker.update(
                self.frame,
                [{"box": [20, 10, 50, 110], "conf": 0.9}],
                160,
                120,
                timestamp=11.0,
            )[0]

        clean_anchor = list(track.motion_box)
        predicted = tracker.predict(timestamp=11.1, width=160, height=120)[0]

        self.assertGreater(_center_x(predicted.box), 35.0)
        self.assertEqual(predicted.motion_box, clean_anchor)

    def test_stale_detection_frame_does_not_advance_botsort(self):
        tracker = CameraPersonTracker()
        fake_bot = SequencedBotTracker([
            [bot_row([30, 10, 90, 110], 101, 0)],
        ])
        tracker._tracker = fake_bot
        detections = [{"cls": "person", "box": [30, 10, 90, 110], "conf": 0.9}]

        with patch(
            "app.services.person_tracking.reid_encoder.extract",
            return_value=np.asarray([[1.0, 0.0]], dtype=np.float32),
        ):
            process_frame(
                self.frame,
                detections,
                [],
                160,
                120,
                tracker,
                timestamp=40.0,
                render_overlay=False,
                detections_fresh=True,
            )
            _, _, status = process_frame(
                self.frame,
                detections,
                [],
                160,
                120,
                tracker,
                timestamp=40.1,
                render_overlay=False,
                detections_fresh=False,
                include_status=True,
            )

        self.assertEqual(fake_bot.calls, 1)
        self.assertEqual(status["worker_count"], 1)

    def test_ble_tag_can_be_bound_to_visible_identity(self):
        tracker = CameraPersonTracker()
        track = tracker.update(
            self.frame,
            [{"box": [30, 10, 90, 110], "conf": 0.9}],
            160,
            120,
        )[0]

        self.assertTrue(tracker.bind_tag("UUID:1:1", track.track_id))
        self.assertEqual(track.ble_tag_key, "UUID:1:1")
        tracker.unbind_tag("UUID:1:1")
        self.assertIsNone(track.ble_tag_key)


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

    def test_update_automatically_purges_stale_track_ids(self):
        tracker = HeatExposureTracker()
        tracker.update("person-old", True, now=1.0)

        tracker.update("person-current", True, now=100.0)

        self.assertNotIn("person-old", tracker._state)
        self.assertIn("person-current", tracker._state)


if __name__ == "__main__":
    unittest.main()
