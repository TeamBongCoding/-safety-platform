import unittest
from unittest.mock import patch

from app.services.analysis_service import AnalysisService


class MultiCameraAnalysisStatusTests(unittest.TestCase):
    def test_same_global_worker_in_two_cameras_is_counted_once(self):
        service = AnalysisService(site_id=321, external=True)
        first = service._camera_states["camera-1"]
        second = service._camera_states["camera-2"]
        worker = {
            "id": "person-000001",
            "track_id": "person-000001",
            "helmet_violation": False,
            "reasons": [],
            "confidence": 0.9,
        }
        first.update({
            "connected": True,
            "latest_jpeg": b"a",
            "frame_index": 4,
            "processing_fps": 5.0,
            "workers": [worker | {"camera_id": "camera-1", "camera_ids": ["camera-1"]}],
        })
        second.update({
            "connected": True,
            "latest_jpeg": b"b",
            "frame_index": 3,
            "processing_fps": 4.0,
            "workers": [worker | {"camera_id": "camera-2", "camera_ids": ["camera-2"]}],
        })

        status = service._refresh_external_status()

        self.assertEqual(status["worker_count"], 1)
        self.assertEqual(status["workers"][0]["camera_ids"], ["camera-1", "camera-2"])
        self.assertEqual(status["processing_fps"], 9.0)
        self.assertEqual(len(status["cameras"]), 2)

    def test_event_cooldown_keys_are_periodically_pruned(self):
        service = AnalysisService(site_id=321, external=True)
        old_key = ("fall", None, "person-old")
        recent_key = ("fall", None, "person-current")
        service._event_last_seen = {
            old_key: 900.0,
            recent_key: 980.0,
        }
        service._last_event_cleanup = 0.0

        with patch("app.services.analysis_service.time.monotonic", return_value=1000.0):
            service._save_events([])

        self.assertNotIn(old_key, service._event_last_seen)
        self.assertIn(recent_key, service._event_last_seen)


if __name__ == "__main__":
    unittest.main()
