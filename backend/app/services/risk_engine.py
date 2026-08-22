"""결정론적 Risk Engine — 관측된 위험행동 증가 추세를 수치화한다.

⚠ 이 엔진은 사고 확률을 예측하지 않는다.
   실제 사고 라벨이 없으므로 "관측 위험행동의 빈도 변화 추세"만 계산한다.
   risk_level은 이 엔진이 결정하며, LLM이 변경할 수 없다.

점수 공식 (0~100)
-----------------
base_score = rate_ratio_score (0~50) — 최근/기준 발생률 비율
           + recency_score (0~20)    — EWMA 가중 최근성
           + unresolved_score (0~15) — 미해결 사건 경과시간 가중치
           + severity_score (0~10)   — 심각도 분포 가중치
           + zone_score (0~5)        — 반복 발생 구역 패턴

risk_level 임계값
-----------------
 0~24 → low
25~49 → medium
50~74 → high
75~  → critical

confidence_level 결정
---------------------
- critical → very_low: 표본 부족 (< 3 episodes)
- low      : worker_hours 데이터 없음
- medium   : 7일 이상 데이터, 3+ episodes
- high     : 28일 이상 데이터, 10+ episodes

추후 LightGBM/XGBoost 교체를 위해 RiskModel 인터페이스를 분리한다.
"""

from __future__ import annotations

import json
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from ..time_utils import utc_now
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

RISK_THRESHOLDS = {
    "low":      (0, 24),
    "medium":   (25, 49),
    "high":     (50, 74),
    "critical": (75, 100),
}

SEVERITY_WEIGHTS = {"high": 2.0, "medium": 1.0, "low": 0.5}

EWMA_ALPHA = 0.3   # weight for most recent window vs older windows


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class RiskFactor:
    metric: str
    value: float
    description: str
    weight: float = 1.0


@dataclass
class RiskResult:
    event_type: str
    horizon: str          # "24h" | "7d"
    window_label: str     # display label, e.g. "1분" | "24시간"
    risk_score: float     # 0–100
    risk_level: str       # low | medium | high | critical
    baseline_rate: float  # episodes per 100 worker-hours (or per day)
    recent_rate: float
    change_percent: float
    confidence_level: str
    factors: list[RiskFactor]
    limitations: list[str]
    generated_at: datetime
    site_id: int | None = None
    zone_id: int | None = None
    model_version: str = "rule/1.0"


# ── Interface ─────────────────────────────────────────────────────────────────

class RiskModel(ABC):
    """Plug-in interface: implement to swap in an ML model."""

    @abstractmethod
    def predict(
        self,
        site_id: int | None,
        db: Session,
        horizon: str = "24h",
        event_types: list[str] | None = None,
        zone_id: int | None = None,
    ) -> list[RiskResult]:
        ...


# ── Deterministic rule-based implementation ───────────────────────────────────

class RuleBasedRiskEngine(RiskModel):
    """Deterministic risk scoring based on episode frequency trends.

    API horizon values stay compatible while demo mode maps them to minutes.
    """

    def __init__(
        self,
        model_version: str = "rule/1.1",
        window_mode: str = "production",
        short_window_minutes: int = 1,
        long_window_minutes: int = 5,
    ):
        self._model_version = model_version
        self._window_mode = window_mode if window_mode in {"production", "demo"} else "production"
        self._short_window_minutes = max(1, short_window_minutes)
        self._long_window_minutes = max(self._short_window_minutes, long_window_minutes)

    def window_options(self) -> list[dict[str, str]]:
        if self._window_mode == "demo":
            return [
                {"value": "24h", "label": f"{self._short_window_minutes}분"},
                {"value": "7d", "label": f"{self._long_window_minutes}분"},
            ]
        return [
            {"value": "24h", "label": "24시간"},
            {"value": "7d", "label": "7일"},
        ]

    @property
    def window_mode(self) -> str:
        return self._window_mode

    def _window(self, horizon: str) -> tuple[timedelta, str]:
        if horizon not in {"24h", "7d"}:
            raise ValueError("horizon must be 24h or 7d")
        if self._window_mode == "demo":
            minutes = self._short_window_minutes if horizon == "24h" else self._long_window_minutes
            return timedelta(minutes=minutes), f"{minutes}분"
        if horizon == "24h":
            return timedelta(days=1), "24시간"
        return timedelta(days=7), "7일"

    def predict(
        self,
        site_id: int | None,
        db: Session,
        horizon: str = "24h",
        event_types: list[str] | None = None,
        zone_id: int | None = None,
    ) -> list[RiskResult]:
        from ..models import EventEpisode

        now = utc_now()
        results: list[RiskResult] = []

        # Discover event types if not specified
        if event_types is None:
            rows = db.execute(
                select(EventEpisode.event_type)
                .where(EventEpisode.site_id == site_id)
                .distinct()
            ).scalars().all()
            event_types = list(rows) or ["no_helmet"]

        # Production uses a 28-day worker-hour average for normalization.
        # Minute-scale demo windows use counts/minute because hourly exposure
        # buckets cannot accurately represent a one-minute interval.
        worker_hours = self._get_worker_hours(db, site_id, days=28)

        for et in event_types:
            result = self._score_event_type(
                db, site_id, et, zone_id, now, horizon, worker_hours
            )
            results.append(result)

        return results

    # ── private helpers ───────────────────────────────────────────────────────

    def _score_event_type(
        self,
        db: Session,
        site_id: int | None,
        event_type: str,
        zone_id: int | None,
        now: datetime,
        horizon: str,
        worker_hours_28d: float,
    ) -> RiskResult:
        from ..models import EventEpisode

        limitations: list[str] = []
        factors: list[RiskFactor] = []

        # ── Selected window vs the immediately preceding equal window ─────────
        window, window_label = self._window(horizon)
        recent_start = now - window
        baseline_start = recent_start - window
        history_start = now - window * 4

        def count_episodes(start: datetime, end: datetime) -> int:
            q = select(func.count(EventEpisode.id)).where(
                EventEpisode.site_id == site_id,
                EventEpisode.event_type == event_type,
                EventEpisode.started_at >= start,
                EventEpisode.started_at < end,
            )
            if zone_id is not None:
                q = q.where(EventEpisode.zone_id == zone_id)
            return db.scalar(q) or 0

        recent_count = count_episodes(recent_start, now)
        baseline_count = count_episodes(baseline_start, recent_start)
        history_count = count_episodes(history_start, now)
        total_episodes = history_count

        # ── Rate calculation ──────────────────────────────────────────────────
        window_days = window.total_seconds() / 86400.0
        if self._window_mode == "production" and worker_hours_28d >= 1.0:
            estimated_window_hours = max(worker_hours_28d * window_days / 28.0, 1e-3)
            recent_rate = recent_count * 100.0 / estimated_window_hours
            baseline_rate = baseline_count * 100.0 / estimated_window_hours
            rate_unit = "per 100 worker-hours"
        else:
            if self._window_mode == "demo":
                window_minutes = window.total_seconds() / 60.0
                recent_rate = recent_count / window_minutes
                baseline_rate = baseline_count / window_minutes
                rate_unit = "per minute"
                limitations.append("데모 모드에서는 시간당 노출량 대신 분당 사건 수를 사용합니다.")
            else:
                recent_rate = recent_count / window_days
                baseline_rate = baseline_count / window_days
                rate_unit = "per day"
                limitations.append(
                    "worker-hours 데이터가 부족하여 일 단위 발생 건수로 대체했습니다. "
                    "신뢰도가 낮을 수 있습니다."
                )

        # ── Change percent ────────────────────────────────────────────────────
        if baseline_rate > 0:
            change_pct = (recent_rate - baseline_rate) / baseline_rate * 100
        elif recent_rate > 0:
            change_pct = 100.0
            limitations.append("기준 기간 데이터가 없어 증가율을 정확히 계산할 수 없습니다.")
        else:
            change_pct = 0.0

        # ── Sub-scores ────────────────────────────────────────────────────────
        # 1) Rate ratio score (0~50)
        if baseline_rate > 0:
            ratio = recent_rate / baseline_rate
        elif recent_rate > 0:
            ratio = 3.0
        else:
            ratio = 1.0

        rate_score = min(50.0, (ratio - 1.0) * 25.0) if ratio >= 1.0 else 0.0
        rate_score = max(0.0, rate_score)

        if change_pct != 0:
            factors.append(RiskFactor(
                metric="rate_ratio",
                value=round(ratio, 2),
                description=f"최근 {window_label} 발생률이 직전 {window_label} 대비 {abs(change_pct):.0f}% {'증가' if change_pct > 0 else '감소'}했습니다.",
                weight=50.0,
            ))

        # 2) EWMA recency score (0~20)
        if history_count > 0:
            recent_window_rate = float(recent_count)
            history_window_rate = history_count / 4.0
            ewma_ratio = EWMA_ALPHA * recent_window_rate + (1 - EWMA_ALPHA) * history_window_rate
            ewma_baseline = history_window_rate
            ewma_score = min(20.0, (ewma_ratio / max(ewma_baseline, 1e-6)) * 10.0) if ewma_baseline > 0 else 0.0
        else:
            ewma_score = 0.0

        factors.append(RiskFactor(
            metric="ewma_recency",
            value=round(ewma_score, 1),
            description=f"최근 4개 구간({window_label} × 4) 내 총 {history_count}건, 현재 구간 {recent_count}건 발생.",
        ))

        # 3) Unresolved episodes score (0~15)
        unresolved = self._get_unresolved(db, site_id, event_type, zone_id)
        unresolved_score = min(15.0, unresolved * 3.0)
        if unresolved > 0:
            factors.append(RiskFactor(
                metric="unresolved_count",
                value=float(unresolved),
                description=f"미해결 사건 {unresolved}건이 남아 있습니다.",
            ))

        # 4) Severity score (0~10)
        severity_score = self._severity_score(db, site_id, event_type, zone_id, recent_start)
        if severity_score > 0:
            factors.append(RiskFactor(
                metric="severity_weight",
                value=round(severity_score, 1),
                description="고위험 등급 사건이 포함되어 있습니다.",
            ))

        # 5) Zone concentration score (0~5)
        zone_score = self._zone_concentration_score(db, site_id, event_type, recent_start) if zone_id is None else 0.0
        if zone_score > 0:
            factors.append(RiskFactor(
                metric="zone_concentration",
                value=round(zone_score, 1),
                description="특정 구역에 위험이 집중되고 있습니다.",
            ))

        risk_score = round(min(100.0, rate_score + ewma_score + unresolved_score + severity_score + zone_score), 1)
        risk_level = score_to_level(risk_score)

        # ── Confidence level ──────────────────────────────────────────────────
        confidence_level = self._confidence(total_episodes, worker_hours_28d)

        if total_episodes == 0:
            limitations.append(f"분석 기간({window_label} × 4) 내 사건이 없어 기준 위험도를 계산할 수 없습니다.")
        elif total_episodes < 3:
            limitations.append(f"표본이 {total_episodes}건으로 적어 통계적 신뢰도가 낮습니다.")

        factors.append(RiskFactor(
            metric="sample_count",
            value=float(total_episodes),
            description=f"최근 4개 구간 누적 사건 수 {total_episodes}건 ({rate_unit}).",
        ))

        return RiskResult(
            window_label=window_label,
            event_type=event_type,
            horizon=horizon,
            risk_score=risk_score,
            risk_level=risk_level,
            baseline_rate=round(baseline_rate, 4),
            recent_rate=round(recent_rate, 4),
            change_percent=round(change_pct, 1),
            confidence_level=confidence_level,
            factors=factors,
            limitations=limitations,
            generated_at=utc_now(),
            site_id=site_id,
            zone_id=zone_id,
            model_version=self._model_version,
        )

    def _get_worker_hours(self, db: Session, site_id: int | None, days: int) -> float:
        from ..models import ExposureHourly
        cutoff = utc_now() - timedelta(days=days)
        total_sec = db.scalar(
            select(func.sum(ExposureHourly.worker_seconds)).where(
                ExposureHourly.site_id == site_id,
                ExposureHourly.bucket_start >= cutoff,
            )
        ) or 0.0
        return total_sec / 3600.0

    def _get_unresolved(self, db: Session, site_id, event_type, zone_id) -> int:
        from ..models import EventEpisode
        q = select(func.count(EventEpisode.id)).where(
            EventEpisode.site_id == site_id,
            EventEpisode.event_type == event_type,
            EventEpisode.resolved == False,  # noqa: E712
        )
        if zone_id is not None:
            q = q.where(EventEpisode.zone_id == zone_id)
        return db.scalar(q) or 0

    def _severity_score(self, db: Session, site_id, event_type, zone_id, cutoff: datetime) -> float:
        from ..models import EventEpisode
        q = select(EventEpisode.severity).where(
            EventEpisode.site_id == site_id,
            EventEpisode.event_type == event_type,
            EventEpisode.started_at >= cutoff,
        )
        if zone_id is not None:
            q = q.where(EventEpisode.zone_id == zone_id)
        severities = db.scalars(q).all()
        if not severities:
            return 0.0
        total_weight = sum(SEVERITY_WEIGHTS.get(s, 1.0) for s in severities)
        avg_weight = total_weight / len(severities)
        return min(10.0, (avg_weight - 1.0) * 10.0)

    def _zone_concentration_score(self, db: Session, site_id, event_type, cutoff: datetime) -> float:
        from ..models import EventEpisode
        rows = db.execute(
            select(EventEpisode.zone_id, func.count(EventEpisode.id).label("cnt")).where(
                EventEpisode.site_id == site_id,
                EventEpisode.event_type == event_type,
                EventEpisode.started_at >= cutoff,
                EventEpisode.zone_id.isnot(None),
            ).group_by(EventEpisode.zone_id)
        ).all()
        if not rows:
            return 0.0
        total = sum(r.cnt for r in rows)
        max_zone = max(r.cnt for r in rows)
        concentration = max_zone / max(total, 1)
        return min(5.0, concentration * 5.0)

    def _confidence(self, n_episodes: int, worker_hours: float) -> str:
        if n_episodes < 3:
            return "very_low"
        if worker_hours < 1.0:
            return "low"
        if n_episodes >= 10 and worker_hours >= 100.0:
            return "high"
        return "medium"


def score_to_level(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


# Singleton configured for the running application. Tests that instantiate the
# engine directly keep production defaults.
from ..config import (  # noqa: E402
    RISK_LONG_WINDOW_MINUTES,
    RISK_SHORT_WINDOW_MINUTES,
    RISK_WINDOW_MODE,
)

_engine = RuleBasedRiskEngine(
    window_mode=RISK_WINDOW_MODE,
    short_window_minutes=RISK_SHORT_WINDOW_MINUTES,
    long_window_minutes=RISK_LONG_WINDOW_MINUTES,
)


def get_risk_engine() -> RuleBasedRiskEngine:
    return _engine


def result_to_dict(r: RiskResult) -> dict:
    return {
        "event_type": r.event_type,
        "horizon": r.horizon,
        "risk_score": r.risk_score,
        "risk_level": r.risk_level,
        "baseline_rate": r.baseline_rate,
        "window_label": r.window_label,
        "recent_rate": r.recent_rate,
        "change_percent": r.change_percent,
        "confidence_level": r.confidence_level,
        "factors": [asdict(f) for f in r.factors],
        "limitations": r.limitations,
        "generated_at": r.generated_at.isoformat(),
        "site_id": r.site_id,
        "zone_id": r.zone_id,
        "model_version": r.model_version,
    }
