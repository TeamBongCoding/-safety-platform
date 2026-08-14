"""Risk API / 보안 테스트 — FastAPI TestClient + SQLite in-memory."""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as db_module
from app.config import (
    RISK_LONG_WINDOW_MINUTES,
    RISK_REFRESH_SECONDS,
    RISK_SHORT_WINDOW_MINUTES,
    RISK_WINDOW_MODE,
)
from app.database import Base, get_db
from app.main import app
from app.models import EventEpisode, LoginSession, Site, User
from app.auth import hash_password, _token_hash
from app.services.rag.indexer import EmbeddingGenerationError
from app.routers.risk import rag_query_for_event


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

    def test_risk_config_matches_environment(self):
        resp = self.client.get(
            "/api/risk/config",
            cookies={"safety_session": self.token},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["mode"], RISK_WINDOW_MODE)
        self.assertEqual(data["refresh_seconds"], RISK_REFRESH_SECONDS)
        self.assertEqual([item["value"] for item in data["options"]], ["24h", "7d"])
        expected_labels = (
            [f"{RISK_SHORT_WINDOW_MINUTES}분", f"{RISK_LONG_WINDOW_MINUTES}분"]
            if RISK_WINDOW_MODE == "demo" else ["24시간", "7일"]
        )

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

    def test_knowledge_upload_embedding_failure_cleans_storage(self):
        storage = Mock()
        storage.delete.return_value = True
        self.client.cookies.set("safety_session", self.token)

        with (
            patch("app.services.storage.get_storage", return_value=storage),
            patch(
                "app.routers.knowledge.DocumentIndexer.index_document",
                side_effect=EmbeddingGenerationError("임베딩 실패"),
            ),
        ):
            resp = self.client.post(
                "/api/knowledge/documents",
                files={"file": ("safety.txt", b"safety content", "text/plain")},
                data={"title": "안전 문서"},
            )

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "임베딩 실패")
        storage.upload.assert_called_once()
        storage.delete.assert_called_once()

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("db", data)

    def test_external_http_redirects_to_https(self):
        resp = self.client.get(
            "/health",
            headers={
                "host": "example.trycloudflare.com",
                "x-forwarded-proto": "http",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers["location"], "https://example.trycloudflare.com/health")

    def test_external_https_has_security_headers(self):
        resp = self.client.get(
            "/health",
            headers={
                "host": "example.trycloudflare.com",
                "x-forwarded-proto": "https",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(resp.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", resp.headers["content-security-policy"])
        self.assertIn("max-age=31536000", resp.headers["strict-transport-security"])


class TestRagQuery(unittest.TestCase):
    def test_known_event_uses_korean_safety_terms(self):
        query = rag_query_for_event("no_helmet")
        self.assertIn("안전모 미착용", query)
        self.assertNotIn("no_helmet", query)

    def test_unknown_event_removes_internal_underscores(self):
        query = rag_query_for_event("custom_hazard")
        self.assertIn("custom hazard", query)
        self.assertNotIn("custom_hazard", query)


if __name__ == "__main__":
    unittest.main()
