import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.database as db_module
from app.database import get_db
from app.main import app

from app.database import Base
from app.models import (
    DocumentChunk,
    Event,
    EventEpisode,
    ExposureHourly,
    KnowledgeDocument,
    LoginSession,
    RiskPrediction,
    Site,
    User,
    Zone,
)
from app.services.demo_accounts import purge_demo_user, purge_expired_demo_users
from app.services.storage import StorageBackend, set_storage


class FakeStorage(StorageBackend):
    def __init__(self, *, fail_delete=False):
        self.fail_delete = fail_delete
        self.deleted = []

    def upload(self, bucket, key, data, content_type="application/octet-stream"):
        return key

    def signed_url(self, bucket, key, expires_in=300):
        return f"/{bucket}/{key}"

    def delete(self, bucket, key):
        self.deleted.append((bucket, key))
        return not self.fail_delete


class DemoAccountCleanupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.storage = FakeStorage()
        set_storage(self.storage)

    def tearDown(self):
        self.engine.dispose()

    def _create_demo(self, *, expired=False):
        now = datetime.now()
        with self.Session() as db:
            user = User(
                email=f"demo-{now.timestamp()}@internal.invalid",
                password_hash="unused",
                company_name="데모",
                manager_name="임시",
                is_ephemeral=True,
                status="active",
                last_seen_at=now - timedelta(hours=1) if expired else now,
                expires_at=now - timedelta(minutes=1) if expired else now + timedelta(hours=2),
            )
            db.add(user)
            db.flush()
            site = Site(user_id=user.id, name="데모 현장")
            db.add(site)
            db.flush()
            user.current_site_id = site.id
            zone = Zone(
                site_id=site.id,
                name="위험 구역",
                zone_type="no_entry",
                polygon="[[0,0],[1,0],[1,1]]",
            )
            db.add(zone)
            db.flush()
            db.add(Event(
                site_id=site.id,
                zone_id=zone.id,
                event_type="zone_intrusion",
                snapshot_path="demo/snapshot.jpg",
            ))
            db.add(EventEpisode(
                site_id=site.id,
                zone_id=zone.id,
                event_type="zone_intrusion",
                started_at=now,
                snapshot_path="demo/episode.jpg",
                clip_object_key="demo/clip.mp4",
            ))
            db.add(ExposureHourly(site_id=site.id, bucket_start=now))
            db.add(RiskPrediction(
                site_id=site.id,
                zone_id=zone.id,
                event_type="zone_intrusion",
                horizon="24h",
                risk_level="high",
            ))
            document = KnowledgeDocument(
                site_id=site.id,
                title="안전 문서",
                storage_object_key="demo/document.pdf",
            )
            db.add(document)
            db.flush()
            db.add(DocumentChunk(
                document_id=document.id,
                site_id=site.id,
                content="문서 내용",
            ))
            db.add(LoginSession(
                token_hash=f"token-{user.id}",
                user_id=user.id,
                expires_at=now + timedelta(hours=2),
            ))
            db.commit()
            return user.id, site.id

    @patch("app.services.demo_accounts._stop_site_services")
    def test_purge_removes_all_rows_and_storage_objects(self, _stop):
        user_id, _site_id = self._create_demo()

        self.assertTrue(purge_demo_user(user_id, session_factory=self.Session))

        with self.Session() as db:
            for model in (
                LoginSession,
                DocumentChunk,
                KnowledgeDocument,
                RiskPrediction,
                ExposureHourly,
                EventEpisode,
                Event,
                Zone,
                Site,
                User,
            ):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)
        self.assertEqual(
            set(self.storage.deleted),
            {
                ("safety-documents", "demo/document.pdf"),
                ("event-snapshots", "demo/snapshot.jpg"),
                ("event-snapshots", "demo/episode.jpg"),
                ("event-clips", "demo/clip.mp4"),
            },
        )

    @patch("app.services.demo_accounts._stop_site_services")
    def test_storage_failure_blocks_account_and_preserves_retry_data(self, _stop):
        user_id, site_id = self._create_demo()
        self.storage.fail_delete = True

        with self.assertRaises(RuntimeError):
            purge_demo_user(user_id, session_factory=self.Session)

        with self.Session() as db:
            user = db.get(User, user_id)
            self.assertEqual(user.status, "deleting")
            self.assertIsNotNone(db.get(Site, site_id))
            self.assertGreater(db.scalar(select(func.count()).select_from(Event)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(LoginSession)), 0)

        self.storage.fail_delete = False
        self.assertEqual(
            purge_expired_demo_users(session_factory=self.Session),
            1,
        )
        with self.Session() as db:
            self.assertIsNone(db.get(User, user_id))

    @patch("app.services.demo_accounts._stop_site_services")
    def test_expired_demo_is_purged_but_permanent_user_is_preserved(self, _stop):
        expired_user_id, _site_id = self._create_demo(expired=True)
        with self.Session() as db:
            permanent = User(
                email="permanent@example.com",
                password_hash="unused",
                company_name="기존",
                manager_name="사용자",
                is_ephemeral=False,
            )
            db.add(permanent)
            db.commit()
            permanent_id = permanent.id

        self.assertEqual(purge_expired_demo_users(session_factory=self.Session), 1)
        with self.Session() as db:
            self.assertIsNone(db.get(User, expired_user_id))
            self.assertIsNotNone(db.get(User, permanent_id))


class DemoAuthAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        db_module.SessionLocal = self.Session
        set_storage(FakeStorage())

        def override_get_db():
            with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    @patch("app.services.demo_accounts._stop_site_services")
    def test_two_clients_are_isolated_and_end_removes_only_own_data(self, _stop):
        with TestClient(app, base_url="https://testserver") as client_a, TestClient(app, base_url="https://testserver") as client_b:
            response_a = client_a.post("/api/auth/demo")
            response_b = client_b.post("/api/auth/demo")
            self.assertEqual(response_a.status_code, 201)
            self.assertEqual(response_b.status_code, 201)
            data_a = response_a.json()
            data_b = response_b.json()
            self.assertNotEqual(data_a["user"]["id"], data_b["user"]["id"])
            self.assertNotEqual(data_a["current_site"]["id"], data_b["current_site"]["id"])

            self.assertEqual(client_a.delete("/api/auth/demo").status_code, 200)
            self.assertEqual(client_a.get("/api/auth/me").status_code, 401)
            self.assertEqual(client_b.get("/api/auth/me").status_code, 200)
            with self.Session() as db:
                remaining_ids = set(db.scalars(select(User.id)))
            self.assertEqual(remaining_ids, {data_b["user"]["id"]})


if __name__ == "__main__":
    unittest.main()
