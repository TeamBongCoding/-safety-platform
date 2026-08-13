import unittest
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Event, EventEpisode, RiskPrediction, Site, User, Zone
from app.routers.zones import delete_zone


class ZoneDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        user = User(
            email="manager@example.com",
            password_hash="test",
            company_name="테스트 건설",
            manager_name="관리자",
        )
        self.db.add(user)
        self.db.flush()
        self.site = Site(user_id=user.id, name="테스트 현장")
        self.db.add(self.site)
        self.db.flush()
        self.zone = Zone(
            site_id=self.site.id,
            name="출입 금지",
            zone_type="no_entry",
            risk_level="high",
            polygon="[[0,0],[1,0],[1,1]]",
        )
        self.db.add(self.zone)
        self.db.flush()

        self.event = Event(
            site_id=self.site.id,
            zone_id=self.zone.id,
            event_type="zone_intrusion",
        )
        self.episode = EventEpisode(
            site_id=self.site.id,
            zone_id=self.zone.id,
            event_type="zone_intrusion",
            started_at=datetime.now(),
        )
        self.prediction = RiskPrediction(
            site_id=self.site.id,
            zone_id=self.zone.id,
            event_type="zone_intrusion",
            horizon="24h",
            risk_level="high",
        )
        self.db.add_all([self.event, self.episode, self.prediction])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_delete_zone_preserves_history_and_clears_references(self):
        zone_id = self.zone.id
        event_id = self.event.id
        episode_id = self.episode.id
        prediction_id = self.prediction.id

        delete_zone(zone_id, self.site, self.db)

        self.assertIsNone(self.db.get(Zone, zone_id))
        self.assertIsNone(self.db.get(Event, event_id).zone_id)
        self.assertIsNone(self.db.get(EventEpisode, episode_id).zone_id)
        self.assertIsNone(self.db.get(RiskPrediction, prediction_id).zone_id)


if __name__ == "__main__":
    unittest.main()
