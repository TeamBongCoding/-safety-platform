"""DocumentIndexer — 안전 문서를 청크 단위로 분할하고 DB에 임베딩과 함께 저장한다."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from .embedding import EmbeddingProvider, get_embedding_provider

logger = logging.getLogger(__name__)

# 허용 확장자
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
CHUNK_SIZE = 500        # 글자 수
CHUNK_OVERLAP = 50


class EmbeddingGenerationError(RuntimeError):
    """문서 임베딩을 만들 수 없어 인덱싱을 완료하지 못한 경우."""


def allowed_extension(filename: str) -> bool:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return suffix in ALLOWED_EXTENSIONS


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """텍스트를 일정 크기 청크로 분할한다. 단락 경계를 우선 사용한다."""
    # 단락 단위로 먼저 분리
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size:
            current = (current + "\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) <= chunk_size:
                current = para
            else:
                # 단락이 너무 길면 글자 단위로 분할
                for i in range(0, len(para), chunk_size - overlap):
                    chunks.append(para[i : i + chunk_size])
                current = ""
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def extract_text(content_bytes: bytes, filename: str) -> str:
    """파일 내용을 plain text로 추출한다."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == ".pdf":
        try:
            import io
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            parts = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(p.strip() for p in parts if p.strip())
        except ImportError:
            raise ValueError("PDF 지원을 위해 pypdf를 설치하세요: pip install pypdf")
        except Exception as exc:
            raise ValueError(f"PDF 텍스트 추출 실패: {exc}") from exc
    return content_bytes.decode("utf-8", errors="replace")


class DocumentIndexer:
    """문서를 청크로 분할하고 임베딩을 생성하여 DB에 저장한다."""

    def __init__(
        self,
        session_factory: Callable,
        embedding_provider: EmbeddingProvider | None = None,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ):
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def _get_provider(self) -> EmbeddingProvider:
        return self._embedding_provider or get_embedding_provider()

    def index_document(
        self,
        site_id: int | None,
        title: str,
        source: str,
        version: str,
        content_bytes: bytes,
        filename: str,
        storage_object_key: str | None = None,
    ) -> int:
        """문서를 처리하고 DB에 저장한다. 중복 버전은 덮어쓴다.

        Returns
        -------
        document_id : int
        """
        from ...models import DocumentChunk, KnowledgeDocument

        text = extract_text(content_bytes, filename)
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        chunks = chunk_text(text, self._chunk_size, self._chunk_overlap)
        if not chunks:
            raise ValueError("문서에서 추출된 텍스트가 없습니다.")

        provider = self._get_provider()

        # 임베딩 생성이 실패한 문서를 정상 처리된 것처럼 저장하면 이후 검색에서
        # 영구적으로 누락된다. DB를 변경하기 전에 실패시켜 원자성을 보장한다.
        try:
            embeddings = provider.encode(chunks)
        except Exception as exc:
            logger.exception("임베딩 생성 실패")
            raise EmbeddingGenerationError(
                "문서 임베딩 생성에 실패했습니다. 서버의 AI 모델 설정을 확인해 주세요."
            ) from exc

        with self._session_factory() as db:
            # 같은 현장+제목+버전이 있으면 청크만 교체
            from sqlalchemy import select
            existing = db.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.site_id == site_id,
                    KnowledgeDocument.title == title,
                    KnowledgeDocument.version == version,
                )
            )
            if existing:
                # 기존 청크 삭제
                db.execute(
                    __import__("sqlalchemy").delete(DocumentChunk).where(
                        DocumentChunk.document_id == existing.id
                    )
                )
                doc = existing
                doc.content_hash = content_hash
                doc.storage_object_key = storage_object_key
            else:
                doc = KnowledgeDocument(
                    site_id=site_id,
                    title=title,
                    source=source,
                    version=version,
                    storage_object_key=storage_object_key,
                    content_hash=content_hash,
                )
                db.add(doc)
                db.flush()

            # 임베딩 생성
            try:
                embeddings = provider.encode(chunks)
            except Exception as exc:
                logger.error("임베딩 생성 실패: %s", exc)
                raise ValueError(
                    "문서 임베딩 생성에 실패했습니다. 모델 설정과 서버 로그를 확인하세요."
                ) from exc

            for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                db_chunk = DocumentChunk(
                    document_id=doc.id,
                    site_id=site_id,
                    chunk_index=idx,
                    content=chunk,
                    character_count=len(chunk),
                    embedding_model=provider.model_name,
                    embedding=emb,
                )
                db.add(db_chunk)

            db.commit()
            logger.info("Indexed doc id=%d, %d chunks", doc.id, len(chunks))
            return doc.id

    def delete_document(self, site_id: int | None, document_id: int) -> bool:
        from ...models import DocumentChunk, KnowledgeDocument
        from sqlalchemy import delete, select

        with self._session_factory() as db:
            doc = db.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id,
                    KnowledgeDocument.site_id == site_id,
                )
            )
            if not doc:
                return False
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
            db.delete(doc)
            db.commit()
        return True
