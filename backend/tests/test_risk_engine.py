"""Risk Engine 단위 테스트 — SQLAlchemy in-memory SQLite 사용."""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import EventEpisode, ExposureHourly, RiskPrediction
from app.services.risk_engine import RuleBasedRiskEngine, score_to_level


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _add_episodes(db, site_id, event_type, n, days_ago=3, severity="medium"):
    for i in range(n):
        ep = EventEpisode(
            site_id=site_id,
            event_type=event_type,
            started_at=datetime.now() - timedelta(days=days_ago, hours=i),
            duration_sec=30.0,
            severity=severity,
            confidence_avg=0.8,
            confidence_min=0.7,
            confidence_max=0.9,
            observation_count=5,
            model_version="1.0",
            rule_version="1.0",
        )
        db.add(ep)
    db.commit()


def _add_exposure(db, site_id, worker_seconds, days=28):
    for d in range(days):
        row = ExposureHourly(
            site_id=site_id,
            bucket_start=datetime.now() - timedelta(days=d),
            observed_seconds=3600.0,
            worker_seconds=worker_seconds / days,
            max_concurrent_workers=3,
            average_workers=2.0,
            frame_count=900,
        )
        db.add(row)
    db.commit()


class TestScoreToLevel(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(score_to_level(0), "low")
        self.assertEqual(score_to_level(24), "low")
        self.assertEqual(score_to_level(25), "medium")
        self.assertEqual(score_to_level(49), "medium")
        self.assertEqual(score_to_level(50), "high")
        self.assertEqual(score_to_level(74), "high")
        self.assertEqual(score_to_level(75), "critical")
        self.assertEqual(score_to_level(100), "critical")


class TestRiskEngineNoData(unittest.TestCase):
    def setUp(self):
        Session = _make_db()
        self.db = Session()
        self.engine = RuleBasedRiskEngine()

    def tearDown(self):
        self.db.close()

    def test_no_data_returns_low(self):
        results = self.engine.predict(site_id=1, db=self.db, event_types=["no_helmet"])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.risk_level, "low")
        self.assertEqual(r.risk_score, 0.0)

    def test_no_data_confidence_is_very_low(self):
        results = self.engine.predict(site_id=1, db=self.db, event_types=["fall"])
        self.assertEqual(results[0].confidence_level, "very_low")

    def test_baseline_zero_handled(self):
        # baseline=0 but recent>0 → change_pct=100%, no division error
        _add_episodes(self.db, site_id=1, event_type="no_helmet", n=3, days_ago=1)
        results = self.engine.predict(site_id=1, db=self.db, event_types=["no_helmet"])
        r = results[0]
        self.assertGreater(r.risk_score, 0)
        self.assertIn("기준 기간 데이터가 없어", " ".join(r.limitations))


class TestRiskEngineWithData(unittest.TestCase):
    def setUp(self):
        Session = _make_db()
        self.db = Session()
        self.engine = RuleBasedRiskEngine()

    def tearDown(self):
        self.db.close()

    def test_increasing_rate_raises_score(self):
        # 직전 7일: 1건, 최근 7일: 5건 → 증가 → 점수 상승
        _add_episodes(self.db, site_id=1, event_type="no_helmet", n=1, days_ago=10)
        _add_episodes(self.db, site_id=1, event_type="no_helmet", n=5, days_ago=2)
        results = self.engine.predict(site_id=1, db=self.db, event_types=["no_helmet"])
        r = results[0]
        self.assertGreater(r.risk_score, 0)
        self.assertGreater(r.recent_rate, 0)

    def test_worker_hours_normalization(self):
        _add_exposure(self.db, site_id=2, worker_seconds=360000)  # 100 worker-hours
        _add_episodes(self.db, site_id=2, event_type="fall", n=5, days_ago=3)
        results = self.engine.predict(site_id=2, db=self.db, event_types=["fall"])
        r = results[0]
        self.assertIsNotNone(r.baseline_rate)
        # rate should be in events-per-100-worker-hours scale
        self.assertNotIn("worker-hours 데이터가 부족", " ".join(r.limitations))

    def test_small_sample_confidence(self):
        _add_episodes(self.db, site_id=3, event_type="fall", n=2, days_ago=5)
        results = self.engine.predict(site_id=3, db=self.db, event_types=["fall"])
        r = results[0]
        self.assertIn(r.confidence_level, ("very_low", "low"))

    def test_large_sample_medium_or_high_confidence(self):
        _add_exposure(self.db, site_id=4, worker_seconds=360000)
        _add_episodes(self.db, site_id=4, event_type="no_helmet", n=15, days_ago=5)
        results = self.engine.predict(site_id=4, db=self.db, event_types=["no_helmet"])
        r = results[0]
        self.assertIn(r.confidence_level, ("medium", "high"))

    def test_factors_are_present(self):
        _add_episodes(self.db, site_id=5, event_type="zone_intrusion", n=3, days_ago=2)
        results = self.engine.predict(site_id=5, db=self.db, event_types=["zone_intrusion"])
        r = results[0]
        self.assertGreater(len(r.factors), 0)
        metric_names = [f.metric for f in r.factors]
        self.assertIn("sample_count", metric_names)


if __name__ == "__main__":
    unittest.main()
