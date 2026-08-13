"""LLM 클라이언트 테스트 — 실제 LLM 연결 없음."""

import json
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

    def test_fallback_mentions_llm_disabled(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertTrue(any("LLM" in l for l in report.limitations))

    def test_fallback_includes_factors_as_evidence(self):
        report = fallback_report(_RISK, horizon="7d")
        self.assertIsInstance(report.evidence, list)


class TestCallLLMDisabled(unittest.TestCase):
    def test_disabled_returns_none(self):
        cfg = LLMConfig(enabled=False)
        result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNone(result)


class TestCallLLMSuccess(unittest.TestCase):
    def _make_response(self, body: dict) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_valid_json_returns_report(self):
        llm_response = {
            "choices": [{"message": {"content": json.dumps({
                "risk_level": "high",
                "horizon": "7d",
                "risk_type": "no_helmet",
                "summary": "안전모 미착용이 증가하고 있습니다.",
                "evidence": [{"metric": "rate_ratio", "value": 3.0, "description": "3배 증가"}],
                "recommendations": [{"priority": 1, "action": "안전모 착용 강화", "reason": "위험 증가", "source_chunk_id": 1}],
                "citations": [{"document_id": 10, "chunk_id": 1, "title": "안전 매뉴얼", "section": None}],
                "limitations": [],
            })}}],
        }
        cfg = LLMConfig(enabled=True, timeout_sec=5)
        with patch("urllib.request.urlopen", return_value=self._make_response(llm_response)):
            result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, LLMReport)
        self.assertEqual(result.risk_level, "high")  # LLM cannot change risk_level

    def test_risk_level_always_matches_engine(self):
        """LLM이 risk_level을 바꾸려 해도 Engine 값으로 덮어쓴다."""
        llm_response = {
            "choices": [{"message": {"content": json.dumps({
                "risk_level": "low",  # LLM이 다른 값 반환 시도
                "horizon": "7d",
                "risk_type": "no_helmet",
                "summary": "...",
                "evidence": [],
                "recommendations": [],
                "citations": [],
                "limitations": [],
            })}}],
        }
        cfg = LLMConfig(enabled=True, timeout_sec=5)
        with patch("urllib.request.urlopen", return_value=self._make_response(llm_response)):
            result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNotNone(result)
        self.assertEqual(result.risk_level, "high")  # 엔진 값으로 강제

    def test_hallucinated_citation_removed(self):
        """참조되지 않은 chunk_id는 제거한다."""
        llm_response = {
            "choices": [{"message": {"content": json.dumps({
                "risk_level": "high",
                "horizon": "7d",
                "risk_type": "no_helmet",
                "summary": "...",
                "evidence": [],
                "recommendations": [],
                "citations": [
                    {"document_id": 10, "chunk_id": 1, "title": "실제 문서", "section": None},
                    {"document_id": 99, "chunk_id": 9999, "title": "가짜 문서", "section": None},
                ],
                "limitations": [],
            })}}],
        }
        cfg = LLMConfig(enabled=True, timeout_sec=5)
        with patch("urllib.request.urlopen", return_value=self._make_response(llm_response)):
            result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(result.citations[0].chunk_id, 1)


class TestCallLLMFailures(unittest.TestCase):
    def test_invalid_json_returns_none(self):
        resp = MagicMock()
        resp.read.return_value = b'{"choices": [{"message": {"content": "invalid json {"}}]}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        cfg = LLMConfig(enabled=True, timeout_sec=5, max_retries=0)
        with patch("urllib.request.urlopen", return_value=resp):
            result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNone(result)

    def test_connection_error_returns_none(self):
        import urllib.error
        cfg = LLMConfig(enabled=True, timeout_sec=1, max_retries=0)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        cfg = LLMConfig(enabled=True, timeout_sec=1, max_retries=0)
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            result = call_llm(_RISK, _CHUNKS, config=cfg)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
