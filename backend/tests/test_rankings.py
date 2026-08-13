import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Event, Site, User, Zone
from app.routers.rankings import today_rankings


class TodayRankingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.user_a = User(
            email="a@example.com",
            password_hash="test",
            company_name="A사",
            manager_name="A",
            role="user",
            status="active",
        )
        user_b = User(
            email="b@example.com",
            password_hash="test",
            company_name="B사",
            manager_name="B",
            role="user",
            status="active",
        )
        self.db.add_all([self.user_a, user_b])
        self.db.flush()

        site_a = Site(user_id=self.user_a.id, name="A현장")
        site_b = Site(user_id=user_b.id, name="B현장")
        self.db.add_all([site_a, site_b])
        self.db.flush()
        zone_a = Zone(
            site_id=site_a.id,
            name="A작업구역",
            zone_type="work_area",
            risk_level="high",
            polygon="[[0,0],[1,0],[1,1]]",
        )
        zone_b = Zone(
            site_id=site_b.id,
            name="B작업구역",
            zone_type="work_area",
            risk_level="high",
            polygon="[[0,0],[1,0],[1,1]]",
        )
        self.db.add_all([zone_a, zone_b])
        self.db.flush()

        now = datetime.now()
        self.db.add_all([
            Event(site_id=site_a.id, zone_id=zone_a.id, event_type="no_helmet", timestamp=now),
            Event(site_id=site_a.id, zone_id=zone_a.id, event_type="fall_risk_entry", timestamp=now),
            Event(site_id=site_a.id, zone_id=None, event_type="no_helmet", timestamp=now),
            Event(site_id=site_b.id, zone_id=zone_b.id, event_type="no_helmet", timestamp=now),
            Event(site_id=site_b.id, zone_id=zone_b.id, event_type="no_helmet", timestamp=now),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_only_in_zone_helmet_warnings_determine_rank(self):
        result = today_rankings(self.user_a, self.db)

        self.assertEqual(
            [(row["company_name"], row["warning_count"], row["rank"]) for row in result["companies"]],
            [("A사", 1, 1), ("B사", 2, 2)],
        )
        self.assertEqual(
            [(row["site_name"], row["warning_count"]) for row in result["sites"]],
            [("A현장", 1), ("B현장", 2)],
        )


if __name__ == "__main__":
    unittest.main()
