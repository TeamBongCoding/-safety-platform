"""Risk API / 보안 테스트 — FastAPI TestClient + SQLite in-memory."""

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

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
from app.routers.risk import _build_rag_query, _diversify_chunks, _select_rag_chunks
from app.auth import _token_hash


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
        password_hash="unused",
        company_name="테스트",
        manager_name="테스터",
        is_ephemeral=True,
        last_seen_at=datetime.now(),
        expires_at=datetime.now() + timedelta(hours=2),
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


class TestRagReportHelpers(unittest.TestCase):
    def test_korean_query_expands_event_and_response_terms(self):
        query = _build_rag_query(
            "no_helmet",
            {"factors": [{"description": "최근 안전모 미착용 증가"}]},
        )
        self.assertIn("안전모 미착용", query)
        self.assertIn("관리감독자 교육", query)
        self.assertIn("작업중지", query)

    def test_chunk_selection_prefers_different_documents(self):
        chunks = [
            SimpleNamespace(document_id=1, name="1-a"),
            SimpleNamespace(document_id=1, name="1-b"),
            SimpleNamespace(document_id=1, name="1-c"),
            SimpleNamespace(document_id=2, name="2-a"),
            SimpleNamespace(document_id=2, name="2-b"),
            SimpleNamespace(document_id=3, name="3-a"),
        ]
        selected = _diversify_chunks(chunks, limit=4)
        self.assertEqual([chunk.document_id for chunk in selected], [1, 2, 3, 1])

    def test_rag_selection_uses_best_match_when_normal_threshold_is_empty(self):
        chunks = [
            SimpleNamespace(document_id=1, similarity=0.52),
            SimpleNamespace(document_id=2, similarity=0.48),
        ]
        selected = _select_rag_chunks(
            chunks,
            limit=5,
            threshold=0.55,
            fallback_threshold=0.35,
        )
        self.assertEqual(selected, [chunks[0]])

    def test_rag_selection_rejects_weak_fallback(self):
        chunks = [SimpleNamespace(document_id=1, similarity=0.2)]
        selected = _select_rag_chunks(
            chunks,
            limit=5,
            threshold=0.55,
            fallback_threshold=0.35,
        )
        self.assertEqual(selected, [])


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
        self.site_id = self.site.id
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

    def test_knowledge_chunk_requires_auth_and_site_access(self):
        db = self.Session()
        own_doc = KnowledgeDocument(
            site_id=self.site_id,
            title="내 현장 안전 문서",
            source="test",
            version="1.0",
        )
        db.add(own_doc)
        db.flush()
        own_chunk = DocumentChunk(
            document_id=own_doc.id,
            site_id=self.site_id,
            chunk_index=0,
            content="안전모를 올바르게 착용하세요.",
            character_count=18,
        )
        db.add(own_chunk)

        _, other_site = _setup_user(
            db, "knowledge-other@example.com", "password123", "다른 현장"
        )
        other_doc = KnowledgeDocument(
            site_id=other_site.id,
            title="다른 현장 문서",
            source="test",
            version="1.0",
        )
        db.add(other_doc)
        db.flush()
        other_chunk = DocumentChunk(
            document_id=other_doc.id,
            site_id=other_site.id,
            chunk_index=0,
            content="다른 현장 전용 내용",
            character_count=11,
        )
        db.add(other_chunk)
        db.commit()
        own_chunk_id = own_chunk.id
        other_chunk_id = other_chunk.id
        db.close()

        unauth = self.client.get(f"/api/knowledge/chunks/{own_chunk_id}")
        self.assertEqual(unauth.status_code, 401)

        self.client.cookies.set("safety_session", self.token)
        own = self.client.get(f"/api/knowledge/chunks/{own_chunk_id}")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.json()["title"], "내 현장 안전 문서")
        self.assertIn("안전모", own.json()["content"])

        forbidden = self.client.get(f"/api/knowledge/chunks/{other_chunk_id}")
        self.assertEqual(forbidden.status_code, 404)

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
        self.assertIn("media-src 'self' blob:", resp.headers["content-security-policy"])
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


class TestRiskTimestampSerialization(unittest.TestCase):
    def test_utc_stored_timestamp_is_converted_to_kst(self):
        value = utc_stored_isoformat(datetime(2026, 8, 14, 1, 7, 50))
        self.assertEqual(value, "2026-08-14T10:07:50+09:00")


if __name__ == "__main__":
    unittest.main()
