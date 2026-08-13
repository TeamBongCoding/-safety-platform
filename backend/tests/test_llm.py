"""LLM 클라이언트 테스트 — 실제 LLM 연결 없음."""

import unittest
from unittest.mock import MagicMock, patch

from app.services.llm_client import (
    LLMConfig,
    LLMReport,
    call_llm,
    fallback_report,
)

_RISK = {
    "event_type": "no_helmet",
    "window_label": "5분",
    "horizon": "7d",
    "risk_level": "high",
    "risk_score": 65.0,
    "baseline_rate": 1.0,
    "recent_rate": 3.0,
    "change_percent": 200.0,
    "confidence_level": "medium",
    "factors": [{"metric": "rate_ratio", "value": 3.0, "description": "증가"}],
    "limitations": [],
}

_CHUNKS = [
    {"chunk_id": 1, "document_id": 10, "title": "안전 매뉴얼", "section": None, "content": "안전모 착용 필수"}
]


class TestFallbackReport(unittest.TestCase):
    def test_fallback_has_correct_risk_level(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertEqual(report.risk_level, "high")

    def test_fallback_has_summary(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertTrue(len(report.summary) > 10)
        self.assertIn("최근 5분", report.summary)

    def test_fallback_mentions_llm_disabled(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertTrue(any("LLM" in l for l in report.limitations))

    def test_fallback_includes_factors_as_evidence(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertIsInstance(report.evidence, list)

    def test_fallback_always_has_five_recommendations(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertEqual(len(report.recommendations), 5)


class TestCallLLMDisabled(unittest.TestCase):
    def test_disabled_returns_none(self):
        cfg = LLMConfig(enabled=False)
        result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNone(result)

    def test_enabled_without_api_key_returns_none(self):
        cfg = LLMConfig(enabled=True, api_key="")
        result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNone(result)


class TestCallLLMSuccess(unittest.TestCase):
    def _make_report(
        self,
        risk_level: str = "high",
        citations: list[dict] | None = None,
    ) -> LLMReport:
        return LLMReport.model_validate({
            "risk_level": risk_level,
            "horizon": "7d",
            "risk_type": "no_helmet",
            "summary": "안전모 미착용이 증가하고 있습니다.",
            "evidence": [
                {"metric": "rate_ratio", "value": 3.0, "description": "3배 증가"}
            ],
            "recommendations": [
                {
                    "priority": priority,
                    "action": f"안전모 착용 강화 {priority}",
                    "reason": "위험 증가",
                    "source_chunk_id": 1,
                }
                for priority in range(1, 6)
            ],
            "citations": citations or [],
            "limitations": [],
        })

    def _make_client(self, report: LLMReport | None) -> MagicMock:
        client = MagicMock()
        client.responses.parse.return_value.output_parsed = report
        return client

    def test_structured_output_returns_report(self):
        client = self._make_client(self._make_report())
        cfg = LLMConfig(enabled=True, api_key="test-key", timeout_sec=5)
        with patch("app.services.llm_client.OpenAI", return_value=client) as openai_cls:
            result = call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, LLMReport)
        self.assertEqual(result.risk_level, "high")  # LLM cannot change risk_level
        openai_cls.assert_called_once_with(
            api_key="test-key",
            timeout=5,
            max_retries=1,
            base_url="https://api.openai.com/v1",
        )
        parse_kwargs = client.responses.parse.call_args.kwargs
        self.assertIs(parse_kwargs["text_format"], LLMReport)
        self.assertEqual(parse_kwargs["model"], "gpt-4o-mini")

    def test_risk_level_always_matches_engine(self):
        """LLM이 risk_level을 바꾸려 해도 Engine 값으로 덮어쓴다."""
        client = self._make_client(self._make_report(risk_level="low"))
        cfg = LLMConfig(enabled=True, api_key="test-key", timeout_sec=5)
        with patch("app.services.llm_client.OpenAI", return_value=client):
            result = call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertIsNotNone(result)
        self.assertEqual(result.risk_level, "high")  # 엔진 값으로 강제

    def test_hallucinated_citation_removed(self):
        """참조되지 않은 chunk_id는 제거한다."""
        report = self._make_report(citations=[
            {
                "document_id": 10,
                "chunk_id": 1,
                "title": "실제 문서",
                "section": None,
            },
            {
                "document_id": 99,
                "chunk_id": 9999,
                "title": "가짜 문서",
                "section": None,
            },
        ])
        client = self._make_client(report)
        cfg = LLMConfig(enabled=True, api_key="test-key", timeout_sec=5)
        with patch("app.services.llm_client.OpenAI", return_value=client):
            result = call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertIsNotNone(result)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(result.citations[0].chunk_id, 1)

    def test_missing_citation_is_restored_from_recommendation_source(self):
        report = self._make_report(citations=[])
        client = self._make_client(report)
        cfg = LLMConfig(enabled=True, api_key="test-key", timeout_sec=5)
        with patch("app.services.llm_client.OpenAI", return_value=client):
            result = call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertIsNotNone(result)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(result.citations[0].chunk_id, 1)
        self.assertEqual(result.citations[0].title, "안전 매뉴얼")

    def test_custom_base_url_is_forwarded_to_sdk(self):
        client = self._make_client(self._make_report())
        cfg = LLMConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://gateway.example/v1/",
        )
        with patch("app.services.llm_client.OpenAI", return_value=client) as openai_cls:
            call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertEqual(
            openai_cls.call_args.kwargs["base_url"],
            "https://gateway.example/v1",
        )


class TestCallLLMFailures(unittest.TestCase):
    def test_missing_structured_output_returns_none(self):
        client = MagicMock()
        client.responses.parse.return_value.output_parsed = None
        cfg = LLMConfig(enabled=True, api_key="test-key", max_retries=0)
        with patch("app.services.llm_client.OpenAI", return_value=client):
            result = call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertIsNone(result)

    def test_sdk_error_returns_none(self):
        client = MagicMock()
        client.responses.parse.side_effect = RuntimeError("connection failed")
        cfg = LLMConfig(enabled=True, api_key="test-key", max_retries=0)
        with patch("app.services.llm_client.OpenAI", return_value=client):
            result = call_llm(_RISK, _CHUNKS, config=cfg)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
