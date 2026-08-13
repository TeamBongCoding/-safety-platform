"""스토리지 추상화 — Supabase Storage 또는 로컬 파일 fallback.

보안 원칙
---------
- service-role key는 backend 전용. 절대 프론트엔드에 노출하지 않는다.
- private bucket 전제. 사용자에게 보여줄 때만 짧은 만료 시간의 signed URL 생성.
- DB에는 object key만 저장, public URL은 저장하지 않는다.
- 스냅샷 업로드 실패가 분석 파이프라인 전체를 중단시키지 않는다.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageBackend(ABC):

    @abstractmethod
    def upload(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload data and return the object key."""
        ...

    @abstractmethod
    def signed_url(self, bucket: str, key: str, expires_in: int = 300) -> str:
        """Return a temporary signed URL (expires_in seconds)."""
        ...

    @abstractmethod
    def delete(self, bucket: str, key: str) -> bool:
        ...


class LocalStorageBackend(StorageBackend):
    """Fallback: store files under LOCAL_STORAGE_ROOT."""

    def __init__(self, root: str | None = None):
        self._root = Path(root or os.getenv("LOCAL_STORAGE_ROOT", "storage"))

    def upload(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        dest = self._root / bucket / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        logger.debug("LocalStorage: wrote %s/%s (%d bytes)", bucket, key, len(data))
        return key

    def signed_url(self, bucket: str, key: str, expires_in: int = 300) -> str:
        # Local backend: return a relative path; API layer resolves it
        return f"/storage/{bucket}/{key}"

    def delete(self, bucket: str, key: str) -> bool:
        dest = self._root / bucket / key
        if dest.exists():
            dest.unlink()
            return True
        return False


class SupabaseStorageBackend(StorageBackend):
    """Supabase Storage via supabase-py (backend-only)."""

    def __init__(self, url: str, service_role_key: str):
        try:
            from supabase import create_client
            self._client = create_client(url, service_role_key)
        except ImportError:
            raise RuntimeError("supabase-py가 설치되지 않았습니다: pip install supabase")

    def upload(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        try:
            self._client.storage.from_(bucket).upload(
                key, data, {"content-type": content_type, "upsert": "true"}
            )
            return key
        except Exception as exc:
            raise RuntimeError(f"Supabase 업로드 실패: {exc}") from exc

    def signed_url(self, bucket: str, key: str, expires_in: int = 300) -> str:
        try:
            resp = self._client.storage.from_(bucket).create_signed_url(key, expires_in)
            return resp["signedURL"]
        except Exception as exc:
            raise RuntimeError(f"signed URL 생성 실패: {exc}") from exc

    def delete(self, bucket: str, key: str) -> bool:
        try:
            self._client.storage.from_(bucket).remove([key])
            return True
        except Exception:
            return False


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        from ..config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
        if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
            try:
                _backend = SupabaseStorageBackend(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
                logger.info("Storage: using Supabase")
            except Exception as exc:
                logger.warning("Supabase Storage 초기화 실패, 로컬 fallback 사용: %s", exc)
                _backend = LocalStorageBackend()
        else:
            _backend = LocalStorageBackend()
            logger.info("Storage: using local filesystem")
    return _backend


def set_storage(backend: StorageBackend) -> None:
    global _backend
    _backend = backend
