import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.services.heat_service import HeatService
from app.time_utils import KST


def forecast_response(date, time):
    items = [
        {"fcstDate": date, "fcstTime": time, "category": "TMP", "fcstValue": "30"},
        {"fcstDate": date, "fcstTime": time, "category": "REH", "fcstValue": "70"},
        {"fcstDate": date, "fcstTime": time, "category": "WSD", "fcstValue": "1.5"},
    ]
    return {"response": {"body": {"items": {"item": items}}}}


class HeatServiceKoreanTimeTests(unittest.TestCase):
    def fetch_at(self, now, forecast_date, forecast_time):
        response = io.StringIO(json.dumps(forecast_response(
            forecast_date,
            forecast_time,
        )))
        with (
            patch("app.services.heat_service.kst_now", return_value=now),
            patch(
                "app.services.heat_service.urllib.request.urlopen",
                return_value=response,
            ) as mock_urlopen,
        ):
            service = HeatService(37.5, 127.0, None)
            service._api_key = "test-key"
            service._fetch()
        request = mock_urlopen.call_args.args[0]
        return service, parse_qs(urlparse(request.full_url).query)

    def test_fetch_uses_latest_kma_base_time_in_kst(self):
        service, params = self.fetch_at(
            datetime(2026, 8, 13, 22, 0, tzinfo=KST),
            "20260813",
            "2200",
        )

        self.assertEqual(params["base_date"], ["20260813"])
        self.assertEqual(params["base_time"], ["2000"])
        self.assertIsNotNone(service.get_status().apparent_temp)
        self.assertFalse(service.get_status().stale)

    def test_fetch_finds_forecast_after_kst_midnight(self):
        service, params = self.fetch_at(
            datetime(2026, 8, 13, 23, 30, tzinfo=KST),
            "20260814",
            "0000",
        )

        self.assertEqual(params["base_date"], ["20260813"])
        self.assertEqual(params["base_time"], ["2300"])
        self.assertIsNotNone(service.get_status().apparent_temp)


if __name__ == "__main__":
    unittest.main()
