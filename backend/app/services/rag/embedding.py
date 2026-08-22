"""RAG embedding providers.

- OpenAIEmbeddingProvider: production provider using the OpenAI Embeddings API.
- MockEmbeddingProvider: deterministic provider for unit tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class EmbeddingProvider(ABC):
    """Interface for converting text into embedding vectors."""

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
        ...

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI Embeddings API provider with bounded batching and validation."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        dimension: int = 1024,
        *,
        api_key: str = "",
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        batch_size: int = 128,
        client: Any | None = None,
    ):
        self._model_name = model_name
        self._dimension = dimension
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._batch_size = max(
            1,
            min(batch_size, 2048),
        )
        self._client = client

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 없어 RAG 임베딩을 생성할 수 없습니다."
            )

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI SDK가 설치되지 않았습니다: pip install openai"
            ) from exc

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        return self._client

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if any(
            not isinstance(value, str) or not value.strip()
            for value in texts
        ):
            raise ValueError(
                "임베딩 입력은 비어 있지 않은 문자열이어야 합니다."
            )

        client = self._get_client()
        vectors: list[list[float]] = []

        for offset in range(0, len(texts), self._batch_size):
            batch = texts[offset : offset + self._batch_size]

            try:
                response = client.embeddings.create(
                    model=self._model_name,
                    input=batch,
                    dimensions=self._dimension,
                    encoding_format="float",
                )
            except Exception as exc:
                raise RuntimeError(
                    f"OpenAI 임베딩 API 호출 실패: {exc}"
                ) from exc

            ordered = sorted(
                response.data,
                key=lambda item: item.index,
            )
            batch_vectors = [
                list(item.embedding)
                for item in ordered
            ]

            if len(batch_vectors) != len(batch):
                raise RuntimeError(
                    "OpenAI 임베딩 API 응답 개수가 "
                    "요청한 텍스트 개수와 다릅니다."
                )

            if any(
                len(vector) != self._dimension
                for vector in batch_vectors
            ):
                raise RuntimeError(
                    f"OpenAI 임베딩 차원이 설정값 "
                    f"{self._dimension}과 다릅니다."
                )

            vectors.extend(batch_vectors)

        return vectors


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic test provider that performs no external API calls."""

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
            rng = np.random.default_rng(
                abs(hash(text)) % (2**31)
            )
            vector = rng.standard_normal(
                self._dimension
            ).astype(np.float32)
            vector /= np.linalg.norm(vector) + 1e-10
            results.append(vector.tolist())

        return results


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    """Return the singleton production provider; tests may replace it."""

    global _provider

    if _provider is None:
        from ...config import (
            EMBEDDING_BATCH_SIZE,
            EMBEDDING_DIM,
            EMBEDDING_MODEL_NAME,
            OPENAI_API_KEY,
            OPENAI_BASE_URL,
            OPENAI_MAX_RETRIES,
            OPENAI_TIMEOUT_SEC,
        )

        _provider = OpenAIEmbeddingProvider(
            model_name=EMBEDDING_MODEL_NAME,
            dimension=EMBEDDING_DIM,
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
            timeout=OPENAI_TIMEOUT_SEC,
            max_retries=OPENAI_MAX_RETRIES,
            batch_size=EMBEDDING_BATCH_SIZE,
        )

    return _provider


def set_embedding_provider(
    provider: EmbeddingProvider | None,
) -> None:
    """Replace or reset the singleton provider for tests."""

    global _provider
    _provider = provider