"""Risk API / 보안 테스트 — FastAPI TestClient + SQLite in-memory."""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as db_module
from app.database import Base, get_db
from app.main import app
from app.models import EventEpisode, LoginSession, Site, User
from app.auth import hash_password, _token_hash


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 모든 세션이 동일한 in-memory DB를 공유
    )
    Base.metadata.create_all(engine)
    return engine


def _setup_user(db, email, password, site_name="테스트현장"):
    user = User(
        email=email,
        password_hash=hash_password(password),
        company_name="테스트",
        manager_name="테스터",
    )
    db.add(user)
    db.flush()
    site = Site(user_id=user.id, name=site_name)
    db.add(site)
    db.flush()
    user.current_site_id = site.id
    db.commit()
    return user, site


def _make_session_cookie(db, user, days=7):
    import secrets
    token = secrets.token_urlsafe(32)
    session = LoginSession(
        token_hash=_token_hash(token),
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=days),
    )
    db.add(session)
    db.commit()
    return token


class TestRiskAPIAuth(unittest.TestCase):
    def setUp(self):
        engine = _make_engine()
        Session = sessionmaker(bind=engine)
        db_module.engine = engine
        db_module.SessionLocal = Session
        self.engine = engine
        self.Session = Session

        def override_get_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app, raise_server_exceptions=True)

        db = Session()
        self.user, self.site = _setup_user(db, "test@example.com", "password123")
        self.token = _make_session_cookie(db, self.user)
        db.close()

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_unauthenticated_risk_overview_rejected(self):
        resp = self.client.get("/api/risk/overview")
        self.assertEqual(resp.status_code, 401)

    def test_authenticated_risk_overview_empty(self):
        resp = self.client.get(
            "/api/risk/overview?horizon=24h",
            cookies={"safety_session": self.token},
        )
        # With no episode data, may return 200 or 404
        self.assertIn(resp.status_code, (200, 404))

    def test_unauthenticated_episodes_rejected(self):
        resp = self.client.get("/api/events/episodes")
        self.assertEqual(resp.status_code, 401)

    def test_episodes_returns_only_own_site(self):
        db = self.Session()
        # 다른 사용자의 site_id로 episode 생성
        other_user, other_site = _setup_user(db, "other@example.com", "password123", "타인현장")
        ep = EventEpisode(
            site_id=other_site.id,
            event_type="no_helmet",
            started_at=datetime.now() - timedelta(hours=1),
            model_version="1.0",
            rule_version="1.0",
        )
        db.add(ep)
        db.commit()
        db.close()

        resp = self.client.get(
            "/api/events/episodes",
            cookies={"safety_session": self.token},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for item in data:
            self.assertEqual(item["site_id"], self.site.id)

    def test_resolve_episode_requires_auth(self):
        resp = self.client.patch(
            "/api/events/episodes/1/resolve",
            json={"note": "test"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_resolve_episode_of_other_site_rejected(self):
        db = self.Session()
        other_user, other_site = _setup_user(db, "other2@example.com", "password123", "타인현장2")
        ep = EventEpisode(
            site_id=other_site.id,
            event_type="fall",
            started_at=datetime.now() - timedelta(hours=1),
            model_version="1.0",
            rule_version="1.0",
        )
        db.add(ep)
        db.flush()
        ep_id = ep.id
        db.commit()
        db.close()

        resp = self.client.patch(
            f"/api/events/episodes/{ep_id}/resolve",
            json={"note": "침해 시도"},
            cookies={"safety_session": self.token},
        )
        self.assertEqual(resp.status_code, 404)

    def test_knowledge_documents_unauthenticated(self):
        resp = self.client.get("/api/knowledge/documents")
        self.assertEqual(resp.status_code, 401)

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("db", data)


if __name__ == "__main__":
    unittest.main()
