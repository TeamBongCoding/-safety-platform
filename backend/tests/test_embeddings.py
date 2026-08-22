"""Embedding provider 테스트 — 실제 OpenAI 호출 없음."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.rag.embedding import (
    OpenAIEmbeddingProvider,
    SentenceTransformerProvider,
    get_embedding_provider,
)


class TestOpenAIEmbeddingProvider(unittest.TestCase):
    def test_batches_requests_and_preserves_response_order(self):
        client = MagicMock()
        client.embeddings.create.side_effect = [
            SimpleNamespace(data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ]),
            SimpleNamespace(data=[
                SimpleNamespace(index=0, embedding=[0.5, 0.5]),
            ]),
        ]
        provider = OpenAIEmbeddingProvider(
            api_key="test-key",
            model_name="text-embedding-3-small",
            dimension=2,
            batch_size=2,
        )

        with patch("app.services.rag.embedding.OpenAI", return_value=client) as openai_cls:
            vectors = provider.encode(["first", "second", "third"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])
        self.assertEqual(client.embeddings.create.call_count, 2)
        self.assertEqual(
            client.embeddings.create.call_args_list[0].kwargs,
            {
                "model": "text-embedding-3-small",
                "input": ["first", "second"],
                "dimensions": 2,
                "encoding_format": "float",
            },
        )
        openai_cls.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            timeout=60.0,
            max_retries=1,
        )

    def test_missing_api_key_fails_without_constructing_client(self):
        provider = OpenAIEmbeddingProvider(api_key="", dimension=2)
        with patch("app.services.rag.embedding.OpenAI") as openai_cls:
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                provider.encode(["text"])
        openai_cls.assert_not_called()

    def test_unexpected_dimension_is_rejected(self):
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[1.0, 0.0, 0.0]),
        ])
        provider = OpenAIEmbeddingProvider(api_key="test-key", dimension=2)
        with patch("app.services.rag.embedding.OpenAI", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "expected=2, actual=3"):
                provider.encode(["text"])

    def test_empty_input_does_not_construct_client(self):
        provider = OpenAIEmbeddingProvider(api_key="", dimension=2)
        with patch("app.services.rag.embedding.OpenAI") as openai_cls:
            self.assertEqual(provider.encode([]), [])
        openai_cls.assert_not_called()


class TestEmbeddingProviderSelection(unittest.TestCase):
    def _config(self, *, enabled: bool) -> dict:
        return {
            "EMBEDDING_PROVIDER": "auto",
            "OPENAI_ENABLED": enabled,
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://gateway.example/v1",
            "OPENAI_EMBEDDING_MODEL": "text-embedding-3-small",
            "OPENAI_EMBEDDING_BATCH_SIZE": 8,
            "OPENAI_TIMEOUT_SEC": 5.0,
            "OPENAI_MAX_RETRIES": 0,
            "EMBEDDING_MODEL_NAME": "BAAI/bge-m3",
            "EMBEDDING_DIM": 1024,
        }

    def test_auto_uses_openai_when_openai_is_enabled(self):
        with (
            patch("app.services.rag.embedding._provider", None),
            patch.multiple("app.config", **self._config(enabled=True)),
        ):
            provider = get_embedding_provider()

        self.assertIsInstance(provider, OpenAIEmbeddingProvider)
        self.assertEqual(provider.model_name, "openai:text-embedding-3-small:1024")

    def test_auto_keeps_local_provider_when_openai_is_disabled(self):
        with (
            patch("app.services.rag.embedding._provider", None),
            patch.multiple("app.config", **self._config(enabled=False)),
        ):
            provider = get_embedding_provider()

        self.assertIsInstance(provider, SentenceTransformerProvider)


if __name__ == "__main__":
    unittest.main()
