import unittest
from datetime import datetime, timezone

from app.time_utils import kst_isoformat, utc_naive_to_kst_isoformat


class KoreanTimeSerializationTests(unittest.TestCase):
    def test_naive_utc_is_converted_to_kst(self):
        value = datetime(2026, 8, 13, 17, 53, 4)
        self.assertEqual(kst_isoformat(value), "2026-08-14T02:53:04+09:00")

    def test_risk_naive_utc_is_converted_to_kst(self):
        value = datetime(2026, 8, 13, 8, 53, 4)
        self.assertEqual(
            utc_naive_to_kst_isoformat(value),
            "2026-08-13T17:53:04+09:00",
        )

    def test_aware_utc_is_converted_to_kst(self):
        value = datetime(2026, 8, 13, 8, 53, 4, tzinfo=timezone.utc)
        self.assertEqual(
            utc_naive_to_kst_isoformat(value),
            "2026-08-13T17:53:04+09:00",
        )


if __name__ == "__main__":
    unittest.main()
