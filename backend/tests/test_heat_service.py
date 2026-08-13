import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.services.heat_service import HeatService, calc_apparent_temp
from app.time_utils import KST


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
