"""EpisodeAggregator 단위 테스트."""

import threading
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch


def _always_persist(event: dict) -> bool:
    """stagger/heat_stagger 제외 정책."""
    if event.get("type") in {"stagger", "heat_stagger"}:
        return False
    if event.get("type") == "no_helmet" and not event.get("in_risk_zone", False):
        return False
    return True


def _make_aggregator(close_gap=30.0, min_duration=2.0, update_interval=10.0):
    from app.services.episode_aggregator import EpisodeAggregator

    saved_rows = []
    clock = [0.0]

    @contextmanager
    def fake_session():
        session = MagicMock()

        def fake_get(cls, pk):
            for row in saved_rows:
                if getattr(row, "id", None) == pk:
                    return row
            return None

        def fake_add(row):
            if not hasattr(row, "id") or row.id is None:
                row.id = len(saved_rows) + 1
            saved_rows.append(row)

        def fake_flush():
            pass

        def fake_commit():
            pass

        def fake_delete(row):
            if row in saved_rows:
                saved_rows.remove(row)

        session.get = fake_get
        session.add = fake_add
        session.flush = fake_flush
        session.commit = fake_commit
        session.delete = fake_delete
        yield session

    agg = EpisodeAggregator(
        session_factory=fake_session,
        should_persist=_always_persist,
        close_gap_sec=close_gap,
        min_duration_sec=min_duration,
        update_interval_sec=update_interval,
        now_fn=lambda: clock[0],
    )
    return agg, saved_rows, clock


class TestEpisodeStart(unittest.TestCase):
    def test_new_event_opens_episode(self):
        agg, rows, clock = _make_aggregator()
        clock[0] = 0.0
        agg.process_events([{"type": "no_helmet", "in_risk_zone": True, "confidence": 0.9, "site_id": 1}])
        self.assertEqual(agg.open_episode_count(), 1)
        self.assertEqual(len(rows), 1)

    def test_repeated_event_extends_same_episode(self):
        agg, rows, clock = _make_aggregator()
        for t in [0.0, 1.0, 2.0]:
            clock[0] = t
            agg.process_events([{"type": "no_helmet", "in_risk_zone": True, "confidence": 0.9, "site_id": 1}])
        self.assertEqual(agg.open_episode_count(), 1)
        # DB row count should be 1 (only inserts, updates do not add rows in our mock)
        self.assertEqual(sum(1 for r in rows if r.event_type == "no_helmet"), 1)

    def test_gap_closes_episode(self):
        agg, rows, clock = _make_aggregator(close_gap=5.0, min_duration=0.0)
        clock[0] = 0.0
        agg.process_events([{"type": "fall", "confidence": 0.8, "site_id": 1}])
        self.assertEqual(agg.open_episode_count(), 1)
        clock[0] = 10.0
        agg.process_events([])  # no new events triggers stale check
        self.assertEqual(agg.open_episode_count(), 0)

    def test_different_track_ids_are_separate_episodes(self):
        agg, rows, clock = _make_aggregator()
        clock[0] = 0.0
        agg.process_events([
            {"type": "no_helmet", "in_risk_zone": True, "confidence": 0.9, "site_id": 1, "track_id": "person-1"},
            {"type": "no_helmet", "in_risk_zone": True, "confidence": 0.8, "site_id": 1, "track_id": "person-2"},
        ])
        self.assertEqual(agg.open_episode_count(), 2)

    def test_flush_closes_all_open_episodes(self):
        agg, rows, clock = _make_aggregator(min_duration=0.0)
        clock[0] = 0.0
        agg.process_events([
            {"type": "fall", "confidence": 0.9, "site_id": 1},
            {"type": "no_helmet", "in_risk_zone": True, "confidence": 0.8, "site_id": 1},
        ])
        self.assertEqual(agg.open_episode_count(), 2)
        clock[0] = 5.0
        agg.flush()
        self.assertEqual(agg.open_episode_count(), 0)

    def test_short_episode_discarded(self):
        agg, rows, clock = _make_aggregator(close_gap=5.0, min_duration=3.0)
        clock[0] = 0.0
        agg.process_events([{"type": "fall", "confidence": 0.9, "site_id": 1}])
        clock[0] = 1.0  # only 1 sec — below min_duration=3
        agg.process_events([])  # not enough to close (gap < 5)
        # close gap
        clock[0] = 10.0
        agg.process_events([])
        self.assertEqual(agg.open_episode_count(), 0)
        # The row should have been deleted
        self.assertEqual(len(rows), 0)

    def test_stagger_not_persisted(self):
        agg, rows, clock = _make_aggregator()
        clock[0] = 0.0
        agg.process_events([{"type": "stagger", "confidence": 0.7, "site_id": 1}])
        self.assertEqual(agg.open_episode_count(), 0)
        self.assertEqual(len(rows), 0)

    def test_no_helmet_outside_risk_zone_not_persisted(self):
        agg, rows, clock = _make_aggregator()
        clock[0] = 0.0
        agg.process_events([{"type": "no_helmet", "in_risk_zone": False, "confidence": 0.9, "site_id": 1}])
        self.assertEqual(agg.open_episode_count(), 0)
        self.assertEqual(len(rows), 0)

    def test_no_duplicate_rows_on_repeated_frames(self):
        agg, rows, clock = _make_aggregator(update_interval=100.0)  # high interval = no extra updates
        for t in range(20):
            clock[0] = float(t)
            agg.process_events([{"type": "fall", "confidence": 0.9, "site_id": 1}])
        # One INSERT per episode (no extra rows during the episode)
        self.assertEqual(len(rows), 1)

    def test_brief_gap_does_not_split_episode(self):
        """짧은 검출 누락은 같은 episode로 유지한다."""
        agg, rows, clock = _make_aggregator(close_gap=10.0, min_duration=0.0)
        clock[0] = 0.0
        agg.process_events([{"type": "no_helmet", "in_risk_zone": True, "confidence": 0.9, "site_id": 1}])
        # 5초 누락 (< close_gap=10)
        clock[0] = 5.0
        agg.process_events([{"type": "no_helmet", "in_risk_zone": True, "confidence": 0.8, "site_id": 1}])
        self.assertEqual(agg.open_episode_count(), 1)
        self.assertEqual(len(rows), 1)  # still same episode, no new INSERT

    def test_long_episode_uses_constant_size_running_confidence_stats(self):
        agg, _rows, clock = _make_aggregator(update_interval=100_000.0)
        values = [0.2, 0.8] * 5_000

        for confidence in values:
            agg.process_events([{
                "type": "fall",
                "confidence": confidence,
                "site_id": 1,
            }])

        episode = next(iter(agg._open.values()))
        self.assertFalse(hasattr(episode, "confidence_values"))
        self.assertEqual(episode.observation_count, 10_000)
        self.assertAlmostEqual(episode.confidence_min, 0.2)
        self.assertAlmostEqual(episode.confidence_max, 0.8)
        self.assertAlmostEqual(
            episode.confidence_sum / episode.observation_count,
            0.5,
        )


if __name__ == "__main__":
    unittest.main()
