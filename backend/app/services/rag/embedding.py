"""EmbeddingProvider 인터페이스와 구현체.

- SentenceTransformerProvider : BAAI/bge-m3 (lazy loading, HF_HOME 경로 사용)
- OpenAIEmbeddingProvider     : OpenAI Embeddings API (로컬 모델 로드 없음)
- MockEmbeddingProvider       : 테스트용, 모델 다운로드 불필요
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from openai import OpenAI

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

    def __init__(self, model_name: str = "BAAI/bge-m3", dimension: int = 1024):
        self._model_name = model_name
        self._dimension = dimension
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
            self._model = SentenceTransformer(self._model_name, cache_folder=cache_dir)
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


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI Embeddings API를 사용하여 로컬 모델 메모리를 사용하지 않는다."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model_name: str = "text-embedding-3-small",
        dimension: int = 1024,
        timeout_sec: float = 60.0,
        max_retries: int = 1,
        batch_size: int = 64,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._dimension = dimension
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._batch_size = max(1, batch_size)
        self._client: Any | None = None

    @property
    def model_name(self) -> str:
        # 동일 모델이라도 차원이 다른 벡터는 검색에서 섞으면 안 된다.
        return f"openai:{self._model_name}:{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_client(self) -> OpenAI:
        if not self._api_key:
            raise RuntimeError(
                "OpenAI 임베딩을 사용하려면 OPENAI_API_KEY가 필요합니다."
            )
        if self._client is None:
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout_sec,
                max_retries=self._max_retries,
            )
        return self._client

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = client.embeddings.create(
                model=self._model_name,
                input=batch,
                dimensions=self._dimension,
                encoding_format="float",
            )
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise RuntimeError(
                    "OpenAI 임베딩 응답 개수가 입력 개수와 다릅니다."
                )
            for item in items:
                vector = list(item.embedding)
                if len(vector) != self._dimension:
                    raise RuntimeError(
                        "OpenAI 임베딩 차원이 설정과 다릅니다: "
                        f"expected={self._dimension}, actual={len(vector)}"
                    )
                vectors.append(vector)
        return vectors


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
        from ...config import (
            EMBEDDING_DIM,
            EMBEDDING_MODEL_NAME,
            EMBEDDING_PROVIDER,
            OPENAI_API_KEY,
            OPENAI_BASE_URL,
            OPENAI_EMBEDDING_BATCH_SIZE,
            OPENAI_EMBEDDING_MODEL,
            OPENAI_ENABLED,
            OPENAI_MAX_RETRIES,
            OPENAI_TIMEOUT_SEC,
        )

        use_openai = EMBEDDING_PROVIDER == "openai" or (
            EMBEDDING_PROVIDER == "auto" and OPENAI_ENABLED
        )
        if use_openai:
            _provider = OpenAIEmbeddingProvider(
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL,
                model_name=OPENAI_EMBEDDING_MODEL,
                dimension=EMBEDDING_DIM,
                timeout_sec=OPENAI_TIMEOUT_SEC,
                max_retries=OPENAI_MAX_RETRIES,
                batch_size=OPENAI_EMBEDDING_BATCH_SIZE,
            )
            logger.info("Using external embedding provider: %s", _provider.model_name)
        else:
            _provider = SentenceTransformerProvider(EMBEDDING_MODEL_NAME, EMBEDDING_DIM)
            logger.info("Using local embedding provider: %s", _provider.model_name)
    return _provider


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """테스트 또는 커스텀 구현 교체용."""
    global _provider
    _provider = provider
