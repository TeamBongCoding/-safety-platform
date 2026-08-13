import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.routers.analysis import _current_frame


class AnalysisFrameTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.routers.analysis.service_for_site")
    async def test_frame_response_exposes_version(self, service_for_site):
        service = MagicMock()
        service.get_frame.return_value = (b"jpeg-data", 7)
        service_for_site.return_value = service

        response = await _current_frame(SimpleNamespace(), after=None)

        self.assertEqual(response.body, b"jpeg-data")
        self.assertEqual(response.headers["x-frame-version"], "7")

    @patch("app.routers.analysis.service_for_site")
    async def test_after_version_waits_for_new_frame(self, service_for_site):
        service = MagicMock()
        service.get_frame.side_effect = [
            (b"old-frame", 7),
            (b"new-frame", 8),
        ]
        service_for_site.return_value = service

        response = await _current_frame(SimpleNamespace(), after=7)

        self.assertEqual(response.body, b"new-frame")
        self.assertEqual(response.headers["x-frame-version"], "8")
        self.assertEqual(service.get_frame.call_count, 2)


if __name__ == "__main__":
    unittest.main()
