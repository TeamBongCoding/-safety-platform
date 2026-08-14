"""KnowledgeRetriever — pgvector cosine 검색 또는 SQLite fallback."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from .embedding import EmbeddingProvider, get_embedding_provider

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    title: str
    section: str | None
    content: str
    similarity: float
    embedding_model: str | None


class KnowledgeRetriever:
    """안전 문서 청크를 cosine similarity로 검색한다.

    PostgreSQL + pgvector: <=> 연산자 사용 (빠른 ANN 검색).
    SQLite (개발/테스트): Python 수준의 cosine similarity fallback.
    """

    def __init__(
        self,
        session_factory: Callable,
        embedding_provider: EmbeddingProvider | None = None,
        top_k: int = 5,
        threshold: float = 0.0,
    ):
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider
        self._top_k = top_k
        self._threshold = threshold

    def _provider(self) -> EmbeddingProvider:
        return self._embedding_provider or get_embedding_provider()

    def search(
        self,
        query: str,
        site_id: int | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
        embedding_model_filter: str | None = None,
    ) -> list[RetrievedChunk]:
        k = top_k or self._top_k
        thr = threshold if threshold is not None else self._threshold

        provider = self._provider()
        query_vec = provider.encode_one(query)
        # 다른 모델의 벡터는 차원이 같아도 서로 비교할 수 없다.
        # 명시적 필터가 없으면 현재 provider로 인덱싱한 청크만 검색한다.
        model_filter = embedding_model_filter or provider.model_name

        # 설정 문자열이 아니라 이 retriever가 실제로 사용하는 세션의
        # dialect를 확인한다. 테스트/도구에서 별도 SQLite 세션을 주입할 수 있다.
        with self._session_factory() as db:
            dialect_name = db.get_bind().dialect.name
        if dialect_name == "sqlite":
            return self._search_sqlite(query_vec, site_id, k, thr, model_filter)
        return self._search_pgvector(query_vec, site_id, k, thr, model_filter)

    # ── PostgreSQL / pgvector ─────────────────────────────────────────────────

    def _search_pgvector(
        self, query_vec, site_id, top_k, threshold, model_filter
    ) -> list[RetrievedChunk]:
        from sqlalchemy import text
        from ...models import DocumentChunk, KnowledgeDocument

        vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]"

        filters = ["dc.embedding IS NOT NULL"]
        params = {"vec": vec_str, "k": top_k}
        if site_id is not None:
            filters.append("dc.site_id = :site_id")
            params["site_id"] = site_id
        if model_filter:
            filters.append("dc.embedding_model = :model")
            params["model"] = model_filter
        where_sql = "\n              AND ".join(filters)

        sql = f"""
            SELECT
                dc.id,
                dc.document_id,
                kd.title,
                dc.section,
                dc.content,
                1 - (dc.embedding <=> CAST(:vec AS vector)) AS similarity,
                dc.embedding_model
            FROM document_chunks dc
            JOIN knowledge_documents kd ON kd.id = dc.document_id
            WHERE {where_sql}
            ORDER BY dc.embedding <=> CAST(:vec AS vector)
            LIMIT :k
        """

        try:
            with self._session_factory() as db:
                rows = db.execute(text(sql), params).fetchall()
        except Exception as exc:
            logger.error("pgvector search failed: %s", exc)
            return []

        return [
            RetrievedChunk(
                chunk_id=row[0],
                document_id=row[1],
                title=row[2],
                section=row[3],
                content=row[4],
                similarity=float(row[5]),
                embedding_model=row[6],
            )
            for row in rows
            if float(row[5]) >= threshold
        ]

    # ── SQLite fallback ───────────────────────────────────────────────────────

    def _search_sqlite(
        self, query_vec, site_id, top_k, threshold, model_filter
    ) -> list[RetrievedChunk]:
        """Python-level cosine similarity for SQLite (dev/test only)."""
        import math
        from sqlalchemy import select
        from ...models import DocumentChunk, KnowledgeDocument

        try:
            with self._session_factory() as db:
                q = select(
                    DocumentChunk.id,
                    DocumentChunk.document_id,
                    KnowledgeDocument.title,
                    DocumentChunk.section,
                    DocumentChunk.content,
                    DocumentChunk.embedding,
                    DocumentChunk.embedding_model,
                ).join(
                    KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id
                ).where(
                    DocumentChunk.embedding.isnot(None)
                )
                if site_id is not None:
                    q = q.where(DocumentChunk.site_id == site_id)
                if model_filter:
                    q = q.where(DocumentChunk.embedding_model == model_filter)
                rows = db.execute(q).fetchall()
        except Exception as exc:
            logger.error("SQLite retriever query failed: %s", exc)
            return []

        results = []
        qn = math.sqrt(sum(v * v for v in query_vec))

        for row in rows:
            emb = row[5]
            if emb is None:
                continue
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    continue
            if not isinstance(emb, list):
                continue
            if len(emb) != len(query_vec):
                continue
            dot = sum(a * b for a, b in zip(query_vec, emb))
            en = math.sqrt(sum(v * v for v in emb))
            sim = dot / (qn * en + 1e-10)
            if sim >= threshold:
                results.append(RetrievedChunk(
                    chunk_id=row[0],
                    document_id=row[1],
                    title=row[2],
                    section=row[3],
                    content=row[4],
                    similarity=sim,
                    embedding_model=row[6],
                ))

        results.sort(key=lambda x: x.similarity, reverse=True)
        return results[:top_k]
