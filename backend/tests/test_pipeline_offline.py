import unittest

import numpy as np

from app.services.pipeline import process_frame


class RecordingTracker:
    def __init__(self):
        self.timestamp = None

    def update(self, frame, detections, width, height, timestamp=None):
        self.timestamp = timestamp
        return []


class OfflinePipelineTests(unittest.TestCase):
    def test_explicit_video_timestamp_reaches_tracker(self):
        tracker = RecordingTracker()
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        _, events, status = process_frame(
            frame,
            [],
            [],
            32,
            32,
            tracker,
            include_status=True,
            timestamp=42.5,
            render_overlay=False,
        )

        self.assertEqual(tracker.timestamp, 42.5)
        self.assertEqual(events, [])
        self.assertEqual(status["worker_count"], 0)


if __name__ == "__main__":
    unittest.main()
