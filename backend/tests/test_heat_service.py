import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.services.heat_service import HeatService, calc_apparent_temp
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


class HeatServiceFetchTests(unittest.TestCase):
    @patch("app.services.heat_service.kst_now")
    @patch("app.services.heat_service.urllib.request.urlopen")
    def test_fetch_uses_kst_and_handles_midnight_forecast_slot(
        self,
        urlopen,
        mocked_kst_now,
    ):
        mocked_kst_now.return_value = datetime(2026, 8, 14, 23, 30, tzinfo=KST)
        payload = {
            "response": {
                "body": {
                    "items": {
                        "item": [
                            {"fcstDate": "20260815", "fcstTime": "0000", "category": "TMP", "fcstValue": "30"},
                            {"fcstDate": "20260815", "fcstTime": "0000", "category": "REH", "fcstValue": "70"},
                            {"fcstDate": "20260815", "fcstTime": "0000", "category": "WSD", "fcstValue": "1.5"},
                        ]
                    }
                }
            }
        }
        urlopen.return_value = io.BytesIO(json.dumps(payload).encode())
        service = HeatService(None, None, None)
        service._lat = 37.210118
        service._lon = 126.979479
        service._api_key = "encoded%2Bservice%3Dkey"

        service._fetch()

        self.assertEqual(
            service.get_status().apparent_temp,
            round(calc_apparent_temp(30.0, 70.0, 1.5), 1),
        )
        request = urlopen.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(query["base_date"], ["20260814"])
        self.assertEqual(query["base_time"], ["2300"])


if __name__ == "__main__":
    unittest.main()
