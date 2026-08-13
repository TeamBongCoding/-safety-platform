"""RAG 컴포넌트 테스트 — MockEmbeddingProvider 사용, 모델 다운로드 없음."""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import DocumentChunk, KnowledgeDocument
from app.services.rag.embedding import MockEmbeddingProvider
from app.services.rag.indexer import DocumentIndexer, chunk_text
from app.services.rag.retriever import KnowledgeRetriever


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session


class TestChunking(unittest.TestCase):
    def test_short_text_is_single_chunk(self):
        chunks = chunk_text("안전 수칙을 지키세요.", chunk_size=500, overlap=50)
        self.assertEqual(len(chunks), 1)

    def test_long_text_splits(self):
        text = "안전 수칙.\n\n" * 50
        chunks = chunk_text(text, chunk_size=100, overlap=10)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 200)  # 너그러운 허용

    def test_empty_text_returns_empty(self):
        chunks = chunk_text("", chunk_size=500)
        self.assertEqual(chunks, [])


class TestDocumentIndexer(unittest.TestCase):
    def setUp(self):
        Session = _make_db()
        self.Session = Session

        @contextmanager
        def session_factory():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        self.session_factory = session_factory
        self.provider = MockEmbeddingProvider(dimension=4)  # small for tests
        self.indexer = DocumentIndexer(
            session_factory=self.session_factory,
            embedding_provider=self.provider,
            chunk_size=100,
            chunk_overlap=10,
        )

    def test_index_and_retrieve(self):
        doc_id = self.indexer.index_document(
            site_id=1,
            title="안전 수칙",
            source="테스트",
            version="1.0",
            content_bytes="안전모를 착용하세요. 위험구역에 진입하지 마세요.".encode("utf-8"),
            filename="safety.txt",
        )
        self.assertIsNotNone(doc_id)
        with self.session_factory() as db:
            doc = db.get(KnowledgeDocument, doc_id)
            self.assertEqual(doc.title, "안전 수칙")
            chunks = db.query(DocumentChunk).filter_by(document_id=doc_id).all()
            self.assertGreater(len(chunks), 0)

    def test_duplicate_version_replaced(self):
        content = "안전 지침 v1".encode("utf-8")
        doc_id1 = self.indexer.index_document(1, "가이드", "test", "1.0", content, "guide.txt")
        content2 = "안전 지침 v2 업데이트".encode("utf-8")
        doc_id2 = self.indexer.index_document(1, "가이드", "test", "1.0", content2, "guide.txt")
        self.assertEqual(doc_id1, doc_id2)

    def test_delete_document(self):
        doc_id = self.indexer.index_document(1, "삭제용", "test", "1.0", b"content", "del.txt")
        result = self.indexer.delete_document(site_id=1, document_id=doc_id)
        self.assertTrue(result)
        with self.session_factory() as db:
            self.assertIsNone(db.get(KnowledgeDocument, doc_id))

    def test_site_filter_in_retriever(self):
        # 현장 1 문서
        self.indexer.index_document(1, "현장1 안전", "test", "1.0", b"fall prevention site 1", "s1.txt")
        # 현장 2 문서
        self.indexer.index_document(2, "현장2 안전", "test", "1.0", b"fall prevention site 2", "s2.txt")

        retriever = KnowledgeRetriever(
            session_factory=self.session_factory,
            embedding_provider=self.provider,
        )
        results = retriever.search("fall prevention", site_id=1)
        for r in results:
            with self.session_factory() as db:
                chunk = db.get(DocumentChunk, r.chunk_id)
                self.assertEqual(chunk.site_id, 1)


class TestKnowledgeRetrieverFallback(unittest.TestCase):
    """SQLite에서 Python-level cosine similarity fallback이 동작하는지 확인."""

    def setUp(self):
        Session = _make_db()

        @contextmanager
        def session_factory():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        self.session_factory = session_factory
        self.provider = MockEmbeddingProvider(dimension=4)
        self.indexer = DocumentIndexer(
            session_factory=self.session_factory,
            embedding_provider=self.provider,
            chunk_size=200,
        )

    def test_sqlite_search_returns_results(self):
        self.indexer.index_document(
            1, "안전 매뉴얼", "test", "1.0",
            "안전모 착용은 필수입니다.".encode("utf-8"),
            "manual.txt",
        )
        retriever = KnowledgeRetriever(
            session_factory=self.session_factory,
            embedding_provider=self.provider,
            threshold=-1.0,  # 랜덤 임베딩이므로 similarity < 0 허용
        )
        results = retriever.search("안전모 착용", site_id=1)
        self.assertGreater(len(results), 0)
        self.assertIn("chunk_id", dir(results[0]))

    def test_citation_contains_correct_fields(self):
        self.indexer.index_document(
            1, "안전 지침서", "test", "1.0",
            "추락 방지 장비를 항상 착용하세요.".encode("utf-8"),
            "guide.txt",
        )
        retriever = KnowledgeRetriever(
            session_factory=self.session_factory,
            embedding_provider=self.provider,
        )
        results = retriever.search("추락 방지", site_id=1)
        for r in results:
            self.assertTrue(hasattr(r, "chunk_id"))
            self.assertTrue(hasattr(r, "document_id"))
            self.assertTrue(hasattr(r, "title"))
            self.assertTrue(hasattr(r, "content"))
            self.assertIsInstance(r.similarity, float)


if __name__ == "__main__":
    unittest.main()
