import unittest

import numpy as np

from app.services.multi_camera_identity import MultiCameraIdentityManager
from app.services.person_tracking import LocalTrack


def make_track(local_id, embedding, quality=0.9):
    return LocalTrack(
        local_track_id=local_id,
        track_id=f"local-{local_id}",
        box=[10.0, 10.0, 50.0, 110.0],
        confidence=0.9,
        embedding=np.asarray(embedding, dtype=np.float32),
        quality=quality,
        point=(0.2, 0.9),
        last_seen_at=0.0,
    )


class MultiCameraIdentityTests(unittest.TestCase):
    def test_simultaneous_matching_tracks_share_global_id(self):
        manager = MultiCameraIdentityManager()
        camera_a = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)
        camera_b = manager.resolve("camera-2", [make_track(1, [1, 0])], now=10.5)

        self.assertEqual(camera_a[0].track_id, camera_b[0].track_id)

    def test_different_appearance_creates_different_global_ids(self):
        manager = MultiCameraIdentityManager()
        camera_a = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)
        camera_b = manager.resolve("camera-2", [make_track(1, [0, 1])], now=10.5)

        self.assertNotEqual(camera_a[0].track_id, camera_b[0].track_id)

    def test_nonoverlap_handoff_keeps_id_and_learns_separate_layout(self):
        manager = MultiCameraIdentityManager()
        camera_a = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)
        camera_b = manager.resolve("camera-2", [make_track(1, [1, 0])], now=16.0)

        self.assertEqual(camera_a[0].track_id, camera_b[0].track_id)
        self.assertEqual(manager.layout_status()["mode"], "separate")

    def test_repeated_simultaneous_observations_learn_overlap_layout(self):
        manager = MultiCameraIdentityManager()
        manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)
        for timestamp in (10.2, 10.4, 10.6):
            manager.resolve("camera-2", [make_track(1, [1, 0])], now=timestamp)

        self.assertEqual(manager.layout_status()["mode"], "overlap")

    def test_ble_binding_is_reflected_in_resolved_track(self):
        manager = MultiCameraIdentityManager()
        first = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)[0]
        self.assertTrue(manager.bind_tag("TAG:1:1", first.track_id))

        resolved = manager.resolve("camera-1", [make_track(1, [1, 0])], now=11.0)[0]
        self.assertEqual(resolved.ble_tag_key, "TAG:1:1")
        self.assertTrue(manager.is_visible(first.track_id, now=11.5))

    def test_ble_proximity_recovers_borderline_cross_camera_appearance(self):
        manager = MultiCameraIdentityManager()
        first = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)[0]
        self.assertTrue(manager.bind_tag("TAG:1:1", first.track_id))
        manager.update_receiver_strengths({
            "TAG:1:1": {"receiver-1": -80, "receiver-2": -42},
        })

        handoff = manager.resolve(
            "camera-2",
            [make_track(1, [0.55, 0.835])],
            now=12.5,
        )[0]

        self.assertEqual(first.track_id, handoff.track_id)

    def test_forget_camera_prevents_reused_local_id_from_forcing_old_identity(self):
        manager = MultiCameraIdentityManager()
        first = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)[0]
        manager.forget_camera("camera-1")
        replacement = manager.resolve(
            "camera-1",
            [make_track(1, [0, 1])],
            now=11.0,
        )[0]

        self.assertNotEqual(first.track_id, replacement.track_id)

    def test_layout_reset_preserves_global_identity(self):
        manager = MultiCameraIdentityManager()
        first = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)[0]
        manager.resolve("camera-2", [make_track(1, [1, 0])], now=16.0)
        manager.reset_layout()

        self.assertEqual(manager.layout_status()["mode"], "calibrating")
        again = manager.resolve("camera-1", [make_track(1, [1, 0])], now=17.0)[0]
        self.assertEqual(first.track_id, again.track_id)

    def test_merge_rewrites_existing_local_camera_mapping(self):
        manager = MultiCameraIdentityManager()
        primary = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)[0]
        duplicate = manager.resolve("camera-2", [make_track(1, [0, 1])], now=10.5)[0]

        merged = manager.merge_track_ids(primary.track_id, [duplicate.track_id])
        resolved = manager.resolve("camera-2", [make_track(1, [0, 1])], now=11.0)[0]

        self.assertEqual(merged, [duplicate.track_id])
        self.assertEqual(resolved.track_id, primary.track_id)

    def test_merge_refuses_identities_with_different_ble_tags(self):
        manager = MultiCameraIdentityManager()
        primary = manager.resolve("camera-1", [make_track(1, [1, 0])], now=10.0)[0]
        duplicate = manager.resolve("camera-2", [make_track(1, [0, 1])], now=10.5)[0]
        manager.bind_tag("TAG:1:1", primary.track_id)
        manager.bind_tag("TAG:1:2", duplicate.track_id)

        with self.assertRaisesRegex(ValueError, "서로 다른 BLE"):
            manager.merge_track_ids(primary.track_id, [duplicate.track_id])


if __name__ == "__main__":
    unittest.main()
