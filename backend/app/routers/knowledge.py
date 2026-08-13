"""안전 문서 RAG API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..database import SessionLocal, get_db
from ..models import DocumentChunk, KnowledgeDocument, Site
from ..schemas import DocumentOut
from ..services.rag.indexer import DocumentIndexer, allowed_extension

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(..., max_length=255),
    source: str = Form(default="", max_length=255),
    version: str = Form(default="1.0", max_length=50),
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    if not allowed_extension(file.filename or ""):
        raise HTTPException(
            status_code=415,
            detail="허용되는 파일 형식: txt, md, pdf",
        )

    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="파일 크기가 10 MB를 초과합니다.")
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일입니다.")

    # Supabase Storage 업로드 (실패해도 계속)
    storage_key: str | None = None
    try:
        from ..services.storage import get_storage
        from ..config import SUPABASE_DOCUMENT_BUCKET
        import uuid
        key = f"{site.id}/{uuid.uuid4()}/{file.filename}"
        get_storage().upload(SUPABASE_DOCUMENT_BUCKET, key, content, file.content_type or "text/plain")
        storage_key = key
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Storage 업로드 실패: %s", exc)

    indexer = DocumentIndexer(session_factory=SessionLocal)
    try:
        doc_id = indexer.index_document(
            site_id=site.id,
            title=title.strip(),
            source=source.strip(),
            version=version.strip(),
            content_bytes=content,
            filename=file.filename or "upload.txt",
            storage_object_key=storage_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    doc = db.get(KnowledgeDocument, doc_id)
    if not doc:
        raise HTTPException(status_code=500, detail="문서 저장에 실패했습니다.")
    return doc


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    docs = db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.site_id == site.id)
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(100)
    ).all()

    # chunk counts (single aggregation query)
    counts = dict(db.execute(
        select(DocumentChunk.document_id, func.count().label("n"))
        .where(DocumentChunk.document_id.in_([d.id for d in docs]))
        .group_by(DocumentChunk.document_id)
    ).all()) if docs else {}

    return [
        DocumentOut(**{c: getattr(doc, c) for c in DocumentOut.model_fields if c != 'chunk_count'}, chunk_count=counts.get(doc.id, 0))
        for doc in docs
    ]


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: int,
    site: Site = Depends(require_current_site),
):
    indexer = DocumentIndexer(session_factory=SessionLocal)
    if not indexer.delete_document(site.id, document_id):
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
