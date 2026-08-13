"""EmbeddingProvider 인터페이스와 구현체.

- SentenceTransformerProvider : BAAI/bge-m3 (lazy loading, HF_HOME 경로 사용)
- MockEmbeddingProvider       : 테스트용, 모델 다운로드 불필요
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """임베딩 벡터를 반환하는 추상 인터페이스."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        ...

    @abstractmethod
    def encode(self, texts: list[str]) -> list[list[float]]:
        """texts를 임베딩 벡터 목록으로 변환한다."""
        ...

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


class SentenceTransformerProvider(EmbeddingProvider):
    """sentence-transformers 기반 실제 임베딩 프로바이더 (lazy loading)."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        dimension: int = 1024,
        revision: str | None = None,
    ):
        self._model_name = model_name
        self._dimension = dimension
        self._revision = revision
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _load(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            hf_home = os.getenv("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
            cache_dir = os.path.join(hf_home, "sentence_transformers")
            logger.info("Loading embedding model %s from %s", self._model_name, cache_dir)
            self._model = SentenceTransformer(
                self._model_name,
                cache_folder=cache_dir,
                revision=self._revision,
            )
            logger.info("Embedding model loaded: dim=%d", self._dimension)
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers가 설치되지 않았습니다. "
                "pip install sentence-transformers"
            ) from exc

    def encode(self, texts: list[str]) -> list[list[float]]:
        self._load()
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]


class MockEmbeddingProvider(EmbeddingProvider):
    """단위 테스트용 결정론적 Mock — 모델을 다운로드하지 않는다."""

    def __init__(self, dimension: int = 1024):
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: list[str]) -> list[list[float]]:
        results = []
        for text in texts:
            # 텍스트 해시 기반의 결정론적 벡터
            rng = np.random.default_rng(abs(hash(text)) % (2**31))
            vec = rng.standard_normal(self._dimension).astype(np.float32)
            vec /= np.linalg.norm(vec) + 1e-10
            results.append(vec.tolist())
        return results


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """싱글톤 EmbeddingProvider를 반환한다. 테스트에서는 set_provider()로 교체 가능."""
    global _provider
    if _provider is None:
        from ...config import EMBEDDING_DIM, EMBEDDING_MODEL_NAME, EMBEDDING_MODEL_REVISION
        _provider = SentenceTransformerProvider(
            EMBEDDING_MODEL_NAME,
            EMBEDDING_DIM,
            revision=EMBEDDING_MODEL_REVISION or None,
        )
    return _provider


def set_embedding_provider(provider: EmbeddingProvider) -> None:
    """테스트 또는 커스텀 구현 교체용."""
    global _provider
    _provider = provider
