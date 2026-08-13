import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Event, EventEpisode, RiskPrediction, Site, User, Zone
from app.routers.zones import delete_zone


class ZoneDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_delete_zone_preserves_history_and_clears_references(self):
        db = self.Session()
        user = User(
            email="manager@example.com",
            password_hash="test",
            company_name="테스트 건설",
            manager_name="관리자",
        )
        db.add(user)
        db.flush()
        site = Site(user_id=user.id, name="테스트 현장")
        db.add(site)
        db.flush()
        zone = Zone(
            site_id=site.id,
            name="출입 금지 구역",
            zone_type="no_entry",
            polygon="[[0, 0], [1, 0], [0, 1]]",
        )
        db.add(zone)
        db.flush()

        event = Event(site_id=site.id, event_type="zone_intrusion", zone_id=zone.id)
        episode = EventEpisode(
            site_id=site.id,
            event_type="zone_intrusion",
            zone_id=zone.id,
            started_at=datetime.now(),
        )
        prediction = RiskPrediction(
            site_id=site.id,
            zone_id=zone.id,
            event_type="zone_intrusion",
            horizon="24h",
            risk_level="high",
        )
        db.add_all([event, episode, prediction])
        db.commit()
        zone_id = zone.id
        event_id = event.id
        episode_id = episode.id
        prediction_id = prediction.id

        delete_zone(zone_id, site, db)

        self.assertIsNone(db.get(Zone, zone_id))
        self.assertIsNone(db.get(Event, event_id).zone_id)
        self.assertIsNone(db.get(EventEpisode, episode_id).zone_id)
        self.assertIsNone(db.get(RiskPrediction, prediction_id).zone_id)
        db.close()


if __name__ == "__main__":
    unittest.main()
