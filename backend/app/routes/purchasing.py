"""Purchasing workflow endpoints (adapter addition): sample runs, PO list, reviewer decision.

Public visitors run predefined samples only. "recorded" mode replays a stored
extraction (labelled as such) through the real grounding, PO reconciliation and rule
engine; "live" mode runs the upstream OCR + LangExtract + LLM decision with the
configured OpenRouter key, behind a per-IP and global hourly cap.
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from app.config import BACKEND_ROOT, settings
from app.db import get_session
from app.models import DocType, Document, _utcnow
from app.pipeline.agent import run_decision
from app.pipeline.ocr import run_ocr
from app.pipeline.prescan import run_prescan
from app.pipeline.structuring import run_structuring
from app.models import DocumentStatus
from app.po import PURCHASE_ORDERS, reconcile_invoice
from app.rules import DecisionContext
from app.schemas import OCRResult, StructuredResult

from .documents import ingest_document, require_sandbox
from .pipeline import (
    _latest_run, _prior_invoice_numbers, _run_stage, _save_stage, get_or_create_run,
)

router = APIRouter(tags=["purchasing"])

SAMPLES_DIR = BACKEND_ROOT / "samples"
SAMPLES = {
    "hp-invoice-3301-valid": {
        "title": "Valid invoice - Harbor & Pine Paper Co.",
        "file": "hp-invoice-3301-valid.pdf",
        "expect": "Matches PO-24017 line by line; reaches a reviewable match.",
    },
    "lumen-invoice-8842-mismatch": {
        "title": "Mismatched invoice - Lumen Ink & Toner Supply",
        "file": "lumen-invoice-8842-mismatch.pdf",
        "expect": "Cyan cartridge billed at 14 x 93.10 against PO-24018's 12 x 91.40; flagged for review.",
    },
}

# --- live-mode caps (in-memory: they reset when the free instance restarts) -----
_LIVE_HITS: deque[tuple[float, str]] = deque()
_LIVE_LOCK = Lock()


def _check_live_quota(ip: str) -> None:
    now = time.time()
    with _LIVE_LOCK:
        while _LIVE_HITS and now - _LIVE_HITS[0][0] > 3600:
            _LIVE_HITS.popleft()
        if len(_LIVE_HITS) >= settings.live_global_per_hour or sum(1 for _, i in _LIVE_HITS if i == ip) >= settings.live_ip_per_hour:
            raise HTTPException(status_code=429, detail="Live-run limit reached for this hour. Use recorded mode or try later.")
        _LIVE_HITS.append((now, ip))


@router.get("/config")
def public_config() -> dict:
    """What the UI needs to label modes and gate controls. Never returns secrets."""
    return {
        "public_mode": settings.public_mode,
        "live_available": bool(settings.openrouter_api_key),
        "upload_enabled": not settings.public_mode,
        "sandbox_max_upload_mb": settings.sandbox_max_upload_mb,
        "ttl_minutes": settings.doc_ttl_minutes,
        "live_models": {"ocr": settings.ocr_vlm_model, "structuring": settings.structuring_model, "decision": settings.decision_model},
    }


@router.get("/samples")
def list_samples() -> list[dict]:
    return [{"id": k, **{x: v[x] for x in ("title", "expect")}} for k, v in SAMPLES.items()]


@router.get("/purchase-orders")
def list_purchase_orders() -> list[dict]:
    return [
        {
            "po_number": po.po_number, "supplier": po.supplier, "currency": po.currency,
            "subtotal": f"{po.subtotal:.2f}", "tax": f"{po.tax:.2f}", "total": f"{po.total:.2f}",
            "lines": [{"desc": l.desc, "qty": f"{l.qty.normalize():f}", "unit_price": f"{l.unit_price:.2f}", "amount": f"{l.amount:.2f}"} for l in po.lines],
        }
        for po in PURCHASE_ORDERS.values()
    ]


@router.post("/samples/{sample_id}/run")
async def run_sample(
    sample_id: str,
    request: Request,
    mode: str = Query(default="recorded", pattern="^(recorded|live)$"),
    x_session_id: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Ingest a predefined sample and run the whole pipeline, returning the document id."""
    sample = SAMPLES.get(sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail="Unknown sample.")
    if mode == "live":
        if not settings.openrouter_api_key:
            raise HTTPException(status_code=503, detail="Live mode is not configured on this deployment (no model key).")
        _check_live_quota(request.client.host if request.client else "unknown")

    content = (SAMPLES_DIR / sample["file"]).read_bytes()
    doc = ingest_document(session, sample["file"], content, DocType.invoice)
    run = get_or_create_run(session, doc.id)
    scope = (x_session_id or uuid4().hex)[:64]
    ocr_engine = "pdf-text" if mode == "recorded" else "qwen-vl"
    s_provider = "recorded" if mode == "recorded" else "langextract"
    d_provider = "rules" if mode == "recorded" else "llm"

    _save_stage(session, run, doc, "meta", {"mode": mode, "sample": sample_id, "session": scope, "ocr_engine": ocr_engine}, "pending", doc.status)

    report = await _run_stage("Prescan", settings.prescan_timeout_s, run_prescan, doc, deskew=True, clean=False)
    _save_stage(session, run, doc, "prescan", report.model_dump(mode="json"), "prescanned", DocumentStatus.prescanned)

    ocr = await _run_stage("OCR", settings.ocr_timeout_s, run_ocr, doc, ocr_engine)
    _save_stage(session, run, doc, "ocr", {ocr_engine: ocr.model_dump(mode="json")}, "ocr_done", DocumentStatus.ocr_done)

    structured = await _run_stage("Structuring", settings.llm_timeout_s, run_structuring, doc, OCRResult(**ocr.model_dump()), DocType.invoice, s_provider)
    _save_stage(session, run, doc, "structure", structured.model_dump(mode="json"), "structured", DocumentStatus.structured)

    ctx = DecisionContext(
        po_reconciliation=reconcile_invoice(structured.fields),
        extraction_confidence=structured.extraction_confidence,
        prescan_verdict=report.verdict,
        prescan_reasons=list(report.reasons or []),
        prior_invoice_numbers=_prior_invoice_numbers(session, doc.id, scope),
    )
    result = await _run_stage("Decision", settings.llm_timeout_s, run_decision, doc, structured, ctx, d_provider)
    _save_stage(session, run, doc, "decide", result.model_dump(mode="json"), result.status.value, result.status)
    return {"document_id": doc.id, "mode": mode, "decision": result.decision}


# --- workflow view + reviewer decision -----------------------------------------

class ReviewIn(BaseModel):
    reviewer: str = Field(min_length=2, max_length=60)
    outcome: str = Field(pattern="^(approve|reject)$")
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reviewer", "reason")
    @classmethod
    def clean(cls, v: str) -> str:
        v = " ".join(v.split())
        if any(ord(c) < 32 for c in v):
            raise ValueError("control characters are not allowed")
        return v


def _doc_run(session: Session, doc_id: str):
    if session.get(Document, doc_id) is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    run = _latest_run(session, doc_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No pipeline run for this document.")
    return run


@router.get("/documents/{doc_id}/workflow")
def get_workflow(doc_id: str, session: Session = Depends(get_session)) -> dict:
    run = _doc_run(session, doc_id)
    sr = run.stage_results
    decide = sr.get("decide")
    return {
        "mode": (sr.get("meta") or {}).get("mode", "manual"),
        "meta": sr.get("meta"),
        "decision": decide,
        "po_reconciliation": (decide or {}).get("po_reconciliation"),
        "review": sr.get("review"),
    }


@router.post("/documents/{doc_id}/review", status_code=201)
def submit_review(
    doc_id: str,
    body: ReviewIn,
    x_session_id: str | None = Header(default=None),
    x_sandbox_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Record one reviewer decision (with timestamp and reason). Write-once.

    Authorization: a sample run can only be reviewed by the browser session that created
    it (X-Session-Id must match). Documents without a session (sandbox uploads) need the
    sandbox token in public mode. The reviewer name is a free-text label, not an identity.
    """
    run = _doc_run(session, doc_id)
    owner = (run.stage_results.get("meta") or {}).get("session")
    if owner:
        if not x_session_id or not secrets.compare_digest(x_session_id[:64], owner):
            raise HTTPException(status_code=403, detail="Only the session that ran this sample can record its review.")
    else:
        require_sandbox(x_sandbox_token)
    doc = session.get(Document, doc_id)
    decide = run.stage_results.get("decide")
    if not decide:
        raise HTTPException(status_code=409, detail="Run the decision stage before reviewing.")
    if run.stage_results.get("review"):
        raise HTTPException(status_code=409, detail="This document already has a reviewer decision.")
    record = {
        "reviewer": body.reviewer,
        "outcome": body.outcome,
        "reason": body.reason,
        "decided_at": _utcnow().isoformat(),
        "system_decision": decide["decision"],
    }
    _save_stage(session, run, doc, "review", record, run.status, doc.status)
    return record
