"""위험 추세 예측 API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..config import RISK_REFRESH_SECONDS, RISK_WINDOW_MODE
from ..database import get_db
from ..models import RiskPrediction, Site
from ..schemas import RiskOverviewOut, RiskReportOut, RiskResultOut
from ..services.risk_engine import get_risk_engine, result_to_dict
from ..time_utils import utc_now

router = APIRouter(prefix="/api/risk", tags=["risk"])
logger = logging.getLogger(__name__)

_RAG_EVENT_TERMS = {
    "no_helmet": "안전모 미착용 개인보호구 착용 점검",
    "zone_intrusion": "위험구역 침입 출입 통제 접근 방지",
    "zone_approach": "위험구역 접근 경고 출입 통제",
    "fall_risk_entry": "추락 위험구역 진입 추락 방지",
    "fall": "낙상 쓰러짐 사고 예방 응급 대응",
    "fall_still": "낙상 후 움직임 없음 구조 응급 대응",
    "sudden_sit": "급작스러운 주저앉음 건강 이상 대응",
    "stagger": "휘청거림 전도 위험 건강 이상 대응",
    "heat_fall": "폭염 온열질환 쓰러짐 응급 대응",
    "heat_fall_still": "폭염 쓰러짐 움직임 없음 응급 구조",
    "heat_sudden_sit": "폭염 주저앉음 온열질환 대응",
    "heat_stagger": "폭염 휘청거림 온열질환 예방",
    "heavy_equipment_entry": "중장비 작업반경 접근 충돌 예방",
}


def _build_rag_query(event_type: str, risk_result: dict) -> str:
    event_terms = _RAG_EVENT_TERMS.get(event_type, event_type.replace("_", " "))
    factor_text = " ".join(
        str(factor.get("description", ""))
        for factor in risk_result.get("factors", [])
    )
    return (
        f"{event_terms} 위험 대응 안전 수칙 "
        "예방 점검 작업 절차 관리감독자 교육 보호구 작업중지 비상대응 "
        f"{factor_text[:500]}"
    ).strip()


def _diversify_chunks(chunks: list, limit: int, per_document: int = 2) -> list:
    """Prefer multiple documents while preserving similarity order."""
    if limit <= 0:
        return []

    selected = []
    selected_indexes: set[int] = set()
    per_document_count: dict[int, int] = {}

    # 첫 순회는 문서당 1개, 다음 순회는 문서당 2개를 허용한다.
    for allowed_per_document in range(1, per_document + 1):
        for index, chunk in enumerate(chunks):
            if index in selected_indexes:
                continue
            document_id = chunk.document_id
            if per_document_count.get(document_id, 0) >= allowed_per_document:
                continue
            selected.append(chunk)
            selected_indexes.add(index)
            per_document_count[document_id] = per_document_count.get(document_id, 0) + 1
            if len(selected) == limit:
                return selected

    # 문서가 적으면 남은 유사도 상위 청크로 limit을 채운다.
    for index, chunk in enumerate(chunks):
        if index not in selected_indexes:
            selected.append(chunk)
            if len(selected) == limit:
                break
    return selected


def _select_rag_chunks(
    chunks: list,
    limit: int,
    threshold: float,
    fallback_threshold: float,
) -> list:
    """Apply the normal threshold, with one conservative best-match fallback."""
    qualified = [
        chunk for chunk in chunks
        if float(chunk.similarity) >= threshold
    ]
    if qualified:
        return _diversify_chunks(qualified, limit)

    if chunks and float(chunks[0].similarity) >= fallback_threshold:
        return [chunks[0]]
    return []


@router.get("/config")
def get_risk_config(site: Site = Depends(require_current_site)):
    """Return display labels without changing legacy horizon API values."""
    engine = get_risk_engine()
    return {
        "mode": engine.window_mode,
        "refresh_seconds": RISK_REFRESH_SECONDS,
        "options": engine.window_options(),
    }


@router.get("/overview", response_model=RiskOverviewOut)
def get_risk_overview(
    horizon: str = "24h",
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    if horizon not in ("24h", "7d"):
        raise HTTPException(status_code=422, detail="horizon은 24h 또는 7d만 허용됩니다.")

    # 가장 최근 예측 결과 조회 (valid_until 이내)
    now = utc_now()
    rows = db.scalars(
        select(RiskPrediction).where(
            RiskPrediction.site_id == site.id,
            RiskPrediction.horizon == horizon,
            RiskPrediction.valid_until >= now,
        ).order_by(RiskPrediction.generated_at.desc())
    ).all()

    if RISK_WINDOW_MODE == "demo":
        cache_cutoff = now - timedelta(seconds=RISK_REFRESH_SECONDS)
        rows = [row for row in rows if row.generated_at >= cache_cutoff]
    latest_rows = {}
    for row in rows:
        latest_rows.setdefault(row.event_type, row)
    rows = list(latest_rows.values())

    if not rows:
        # 없으면 즉시 계산
        engine = get_risk_engine()
        results = engine.predict(site.id, db, horizon=horizon)
        if not results:
            raise HTTPException(status_code=404, detail="위험 데이터가 없습니다. 먼저 영상 분석을 실행하세요.")
        _persist_predictions(db, site.id, horizon, results)
        result_dicts = [result_to_dict(r) for r in results]
    else:
        result_dicts = []
        for row in rows:
            factors = json.loads(row.factors_json) if row.factors_json else []
            limitations = json.loads(row.limitations_json) if row.limitations_json else []
            result_dicts.append({
                "event_type": row.event_type,
                "horizon": row.horizon,
                "risk_score": row.risk_score,
                "risk_level": row.risk_level,
                "baseline_rate": row.baseline_rate,
                "recent_rate": row.recent_rate,
                "change_percent": row.change_percent,
                "confidence_level": row.confidence_level,
                "factors": factors,
                "limitations": limitations,
                "generated_at": row.generated_at.isoformat(),
                "site_id": row.site_id,
                "zone_id": row.zone_id,
                "model_version": row.model_version,
            })

    overall_score = max((r["risk_score"] for r in result_dicts), default=0.0)
    from ..services.risk_engine import score_to_level
    overall_level = score_to_level(overall_score)

    return RiskOverviewOut(
        horizon=horizon,
        results=[RiskResultOut(**r) for r in result_dicts],
        overall_risk_level=overall_level,
        overall_risk_score=overall_score,
        generated_at=utc_now(),
    )


@router.post("/predictions/generate")
def generate_predictions(
    horizon: str = "24h",
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    if horizon not in ("24h", "7d"):
        raise HTTPException(status_code=422, detail="horizon은 24h 또는 7d만 허용됩니다.")
    engine = get_risk_engine()
    results = engine.predict(site.id, db, horizon=horizon)
    _persist_predictions(db, site.id, horizon, results)
    return {"generated": len(results), "horizon": horizon}


@router.get("/predictions", response_model=list[RiskResultOut])
def list_predictions(
    horizon: str = "24h",
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    now = utc_now()
    rows = db.scalars(
        select(RiskPrediction).where(
            RiskPrediction.site_id == site.id,
            RiskPrediction.horizon == horizon,
        ).order_by(RiskPrediction.generated_at.desc()).limit(20)
    ).all()

    results = []
    for row in rows:
        factors = json.loads(row.factors_json) if row.factors_json else []
        limitations = json.loads(row.limitations_json) if row.limitations_json else []
        results.append(RiskResultOut(
            event_type=row.event_type,
            horizon=row.horizon,
            risk_score=row.risk_score,
            risk_level=row.risk_level,
            baseline_rate=row.baseline_rate,
            recent_rate=row.recent_rate,
            change_percent=row.change_percent,
            confidence_level=row.confidence_level,
            factors=factors,
            limitations=limitations,
            generated_at=row.generated_at,
            site_id=row.site_id,
            zone_id=row.zone_id,
            model_version=row.model_version,
        ))
    return results


@router.post("/reports/generate", response_model=RiskReportOut)
def generate_report(
    horizon: str = "7d",
    event_type: str = "no_helmet",
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    """Risk Engine + RAG + LLM으로 위험 보고서를 생성한다."""
    if horizon not in ("24h", "7d"):
        raise HTTPException(status_code=422, detail="horizon은 24h 또는 7d만 허용됩니다.")

    # 1. Risk Engine 계산
    engine = get_risk_engine()
    results = engine.predict(site.id, db, horizon=horizon, event_types=[event_type])
    if not results:
        raise HTTPException(status_code=404, detail="위험 데이터가 없습니다.")
    risk_result = result_to_dict(results[0])

    # 2. RAG 검색 (실패해도 계속)
    retrieved = []
    try:
        from ..services.rag.retriever import KnowledgeRetriever
        from ..config import RAG_FALLBACK_THRESHOLD, RAG_TOP_K, RAG_THRESHOLD
        from ..database import SessionLocal

        retriever = KnowledgeRetriever(
            session_factory=SessionLocal,
            top_k=RAG_TOP_K,
        )
        candidates = retriever.search(
            query=_build_rag_query(event_type, risk_result),
            site_id=site.id,
            top_k=max(RAG_TOP_K * 3, RAG_TOP_K),
            threshold=-1.0,
        )
        chunks = _select_rag_chunks(
            candidates,
            limit=RAG_TOP_K,
            threshold=RAG_THRESHOLD,
            fallback_threshold=RAG_FALLBACK_THRESHOLD,
        )
        logger.info(
            "RAG retrieval: site_id=%s event_type=%s candidates=%d selected=%d top_similarity=%s",
            site.id,
            event_type,
            len(candidates),
            len(chunks),
            f"{candidates[0].similarity:.4f}" if candidates else "none",
        )
        retrieved = [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "title": c.title,
                "section": c.section,
                "content": c.content,
                "similarity": c.similarity,
            }
            for c in chunks
        ]
    except Exception:
        logger.exception(
            "RAG search failed: site_id=%s event_type=%s",
            site.id,
            event_type,
        )

    # 3. LLM 호출 (실패 시 fallback)
    from ..services.llm_client import call_llm, fallback_report
    llm_report = call_llm(risk_result, retrieved)
    if llm_report is None:
        llm_report = fallback_report(risk_result, horizon)

    llm_dict = llm_report.model_dump()

    # 4. DB 저장
    _persist_predictions(db, site.id, horizon, results)
    pred = db.scalar(
        select(RiskPrediction).where(
            RiskPrediction.site_id == site.id,
            RiskPrediction.horizon == horizon,
            RiskPrediction.event_type == event_type,
        ).order_by(RiskPrediction.generated_at.desc())
    )
    if pred:
        pred.llm_report_json = json.dumps(llm_dict, ensure_ascii=False)
        db.commit()
        db.refresh(pred)
        return RiskReportOut(
            id=pred.id,
            event_type=pred.event_type,
            horizon=pred.horizon,
            risk_level=pred.risk_level,
            risk_score=pred.risk_score,
            change_percent=pred.change_percent,
            confidence_level=pred.confidence_level,
            llm_report=llm_dict,
            generated_at=pred.generated_at,
        )

    raise HTTPException(status_code=500, detail="보고서 저장에 실패했습니다.")


@router.get("/reports/latest", response_model=RiskReportOut)
def get_latest_report(
    horizon: str = "7d",
    event_type: str = "no_helmet",
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    pred = db.scalar(
        select(RiskPrediction).where(
            RiskPrediction.site_id == site.id,
            RiskPrediction.horizon == horizon,
            RiskPrediction.event_type == event_type,
            RiskPrediction.llm_report_json.isnot(None),
        ).order_by(RiskPrediction.generated_at.desc())
    )
    if not pred:
        raise HTTPException(status_code=404, detail="저장된 보고서가 없습니다. 먼저 보고서를 생성하세요.")

    llm_dict = json.loads(pred.llm_report_json) if pred.llm_report_json else None
    return RiskReportOut(
        id=pred.id,
        event_type=pred.event_type,
        horizon=pred.horizon,
        risk_level=pred.risk_level,
        risk_score=pred.risk_score,
        change_percent=pred.change_percent,
        confidence_level=pred.confidence_level,
        llm_report=llm_dict,
        generated_at=pred.generated_at,
    )


# ── helper ────────────────────────────────────────────────────────────────────

def _persist_predictions(db, site_id: int, horizon: str, results) -> None:
    from ..services.risk_engine import result_to_dict
    if RISK_WINDOW_MODE == "demo":
        valid_until = utc_now() + timedelta(seconds=RISK_REFRESH_SECONDS)
    else:
        valid_until = utc_now() + timedelta(hours=1 if horizon == "24h" else 6)
    for r in results:
        d = result_to_dict(r)
        pred = RiskPrediction(
            site_id=site_id,
            zone_id=r.zone_id,
            event_type=r.event_type,
            horizon=horizon,
            risk_level=r.risk_level,
            risk_score=r.risk_score,
            baseline_rate=r.baseline_rate,
            recent_rate=r.recent_rate,
            change_percent=r.change_percent,
            confidence_level=r.confidence_level,
            factors_json=json.dumps([f for f in d["factors"]], ensure_ascii=False),
            limitations_json=json.dumps(r.limitations, ensure_ascii=False),
            model_version=r.model_version,
            generated_at=utc_now(),
            valid_until=valid_until,
        )
        db.add(pred)
    db.commit()
