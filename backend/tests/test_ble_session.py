import unittest

import numpy as np

from app.routers.ble import BleObservation
from app.services.analysis_service import AnalysisService
from app.services.person_tracking import LocalTrack


class _FakeTracker:
    def __init__(self):
        self.bound = None

    def bind_tag(self, tag_key, track_id):
        self.bound = (tag_key, track_id)
        return True

    def unbind_tag(self, tag_key):
        if self.bound and self.bound[0] == tag_key:
            self.bound = None


class BleSessionTests(unittest.TestCase):
    def setUp(self):
        self.service = AnalysisService(site_id=123)
        self.service.person_tracker = _FakeTracker()

    def test_observation_is_memory_only_and_serializable(self):
        tag = self.service.submit_ble_observation(
            uuid="74278BDA-B644-4520-8F0C-720EAF059935",
            major=1,
            minor=2,
            rssi=-61,
            measured_power=-59,
        )
        self.assertEqual(tag["uuid"], "74278BDAB64445208F0C720EAF059935")
        self.assertEqual(tag["minor"], 2)
        self.assertTrue(tag["active"])
        self.assertNotIn("last_seen_monotonic", tag)

    def test_receiver_specific_rssi_is_kept_for_both_arduinos(self):
        kwargs = {
            "uuid": "74278BDAB64445208F0C720EAF059935",
            "major": 1,
            "minor": 2,
        }
        self.service.submit_ble_observation(
            **kwargs,
            rssi=-48,
            receiver_id="receiver-1",
        )
        tag = self.service.submit_ble_observation(
            **kwargs,
            rssi=-76,
            receiver_id="receiver-2",
        )

        receivers = {
            row["receiver_id"]: row["rssi"]
            for row in tag["receivers"]
        }
        self.assertEqual(receivers, {"receiver-1": -48, "receiver-2": -76})
        self.assertEqual(tag["rssi"], -48)

    def test_signed_byte_minimum_measured_power_is_valid(self):
        observation = BleObservation(
            uuid="74278BDA-B644-4520-8F0C-720EAF059935",
            major=1,
            minor=2,
            rssi=-61,
            measured_power=-128,
        )
        self.assertEqual(observation.measured_power, -128)

    def test_active_tag_can_be_bound_and_unbound(self):
        tag = self.service.submit_ble_observation(
            uuid="74278BDAB64445208F0C720EAF059935",
            major=1,
            minor=1,
            rssi=-55,
        )
        self.service._status["workers"] = [{"track_id": "person-000001"}]
        bound = self.service.bind_ble_tag(tag["tag_key"], "person-000001")
        self.assertEqual(bound["bound_track_id"], "person-000001")
        self.assertEqual(self.service.person_tracker.bound, (tag["tag_key"], "person-000001"))

        self.service.unbind_ble_tag(tag["tag_key"])
        self.assertIsNone(self.service.person_tracker.bound)
        self.assertIsNone(self.service.get_ble_tags()[0]["bound_track_id"])

    def test_binding_auto_merges_single_worker_seen_by_each_camera(self):
        service = AnalysisService(site_id=123, external=True)
        first = service._identity_manager.resolve(
            "camera-1",
            [self._make_track(1, [1, 0])],
            now=10.0,
        )[0]
        duplicate = service._identity_manager.resolve(
            "camera-2",
            [self._make_track(1, [0, 1])],
            now=10.5,
        )[0]
        first_worker = {"id": first.track_id, "track_id": first.track_id}
        duplicate_worker = {"id": duplicate.track_id, "track_id": duplicate.track_id}
        service._camera_states["camera-1"].update(
            connected=True,
            workers=[first_worker],
        )
        service._camera_states["camera-2"].update(
            connected=True,
            workers=[duplicate_worker],
        )
        service._status["workers"] = [first_worker, duplicate_worker]
        tag = service.submit_ble_observation(
            uuid="74278BDAB64445208F0C720EAF059935",
            major=1,
            minor=1,
            rssi=-55,
        )

        result = service.bind_ble_tag(tag["tag_key"], first.track_id)
        again = service._identity_manager.resolve(
            "camera-2",
            [self._make_track(1, [0, 1])],
            now=11.0,
        )[0]

        self.assertEqual(result["merged_track_ids"], [duplicate.track_id])
        self.assertEqual(again.track_id, first.track_id)
        self.assertEqual(again.ble_tag_key, tag["tag_key"])

    @staticmethod
    def _make_track(local_id, embedding):
        return LocalTrack(
            local_track_id=local_id,
            track_id=f"local-{local_id}",
            box=[10.0, 10.0, 50.0, 110.0],
            confidence=0.9,
            embedding=np.asarray(embedding, dtype=np.float32),
            quality=0.9,
            point=(0.2, 0.9),
            last_seen_at=0.0,
        )


if __name__ == "__main__":
    unittest.main()
