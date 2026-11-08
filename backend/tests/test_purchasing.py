"""Tests for the purchasing workflow added on top of upstream (all offline)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.po import reconcile_invoice
from app.po.seed import PURCHASE_ORDERS


def node(v):
    return {"value": v, "confidence": 1.0, "grounding": {"page": 1, "snippet": str(v)}}


def invoice_fields(**over):
    f = {
        "vendor": node("Lumen Ink & Toner Supply"),
        "po_number": node("PO-24018"),
        "total": node(3097.01),
        "line_items": [
            {"desc": node("Cyan pigment ink cartridge"), "qty": node(12), "unit_price": node(91.4), "amount": node(1096.8)},
            {"desc": node("Magenta pigment ink cartridge"), "qty": node(12), "unit_price": node(91.4), "amount": node(1096.8)},
            {"desc": node("Black pigment ink cartridge"), "qty": node(8), "unit_price": node(84.25), "amount": node(674.0)},
        ],
    }
    f.update(over)
    return f


def test_seed_totals_are_decimal_exact():
    po = PURCHASE_ORDERS["PO-24018"]
    assert po.subtotal == Decimal("2867.60")
    assert po.tax == Decimal("229.41")
    assert po.total == Decimal("3097.01")


def test_exact_match():
    rec = reconcile_invoice(invoice_fields())
    assert rec.status == "matched", rec.reasons
    assert all(c.status == "match" for c in rec.comparisons)


def test_supplier_name_normalisation_tolerates_punctuation_and_suffix():
    rec = reconcile_invoice(invoice_fields(vendor=node("LUMEN INK AND TONER SUPPLY, Inc.")))
    assert rec.status == "matched", rec.reasons


def test_quantity_and_price_mismatch_reports_expected_and_observed():
    f = invoice_fields(total=node(3320.14))
    f["line_items"][0] = {"desc": node("Cyan pigment ink cartridge"), "qty": node(14), "unit_price": node(93.1), "amount": node(1303.4)}
    rec = reconcile_invoice(f)
    assert rec.status == "mismatch"
    by = {c.key: c for c in rec.comparisons}
    assert (by["line1.qty"].expected, by["line1.qty"].observed) == ("12", "14")
    assert (by["line1.price"].expected, by["line1.price"].observed) == ("91.40", "93.10")
    assert (by["total"].expected, by["total"].observed) == ("3,097.01", "3,320.14")
    assert any("quantity 14 differs from PO quantity 12" in r for r in rec.reasons)
    assert by["line1.qty"].evidence.page == 1


def test_one_cent_total_difference_is_tolerated_two_is_not():
    assert reconcile_invoice(invoice_fields(total=node(3097.02))).status == "matched"
    assert reconcile_invoice(invoice_fields(total=node(3097.03))).status == "mismatch"


def test_float_artifacts_do_not_cause_false_mismatch():
    # 0.1 + 0.2 style float noise must not matter: values are compared as Decimals.
    f = invoice_fields()
    f["line_items"][0]["unit_price"] = node(91.4)
    f["line_items"][0]["amount"] = node(12 * 91.4)  # 1096.8000000000002 as float
    assert reconcile_invoice(f).status == "matched"


def test_unknown_po_missing_po_and_wrong_supplier():
    assert reconcile_invoice(invoice_fields(po_number=node("PO-99999"))).status == "po_not_found"
    assert reconcile_invoice(invoice_fields(po_number=node(None))).status == "no_po_number"
    rec = reconcile_invoice(invoice_fields(vendor=node("Harbor & Pine Paper Co.")))
    assert rec.status == "mismatch" and any("does not match PO supplier" in r for r in rec.reasons)


def test_uninvoiced_po_line_and_unknown_line_are_flagged():
    f = invoice_fields()
    f["line_items"] = f["line_items"][:2] + [
        {"desc": node("Premium gold foil roll"), "qty": node(1), "unit_price": node(10), "amount": node(10)}
    ]
    rec = reconcile_invoice(f)
    assert rec.status == "mismatch"
    assert any("is not on PO-24018" in r for r in rec.reasons)
    assert any("not on the invoice" in r for r in rec.reasons)


# --- end-to-end through the API (recorded mode, no network) -----------------------


def run(client, sample, session="t1"):
    r = client.post(f"/samples/{sample}/run", params={"mode": "recorded"}, headers={"X-Session-Id": session})
    assert r.status_code == 200, r.text
    return r.json()["document_id"]


def test_valid_sample_reaches_reviewable_match():
    with TestClient(app) as c:
        doc = run(c, "hp-invoice-3301-valid", "valid-1")
        wf = c.get(f"/documents/{doc}/workflow").json()
        assert wf["mode"] == "recorded"
        assert wf["po_reconciliation"]["status"] == "matched"
        assert wf["decision"]["decision"] == "approve"
        assert wf["decision"]["model"] == "rules-only"


def test_mismatch_sample_routes_to_review_with_concrete_reason():
    with TestClient(app) as c:
        doc = run(c, "lumen-invoice-8842-mismatch", "mismatch-1")
        wf = c.get(f"/documents/{doc}/workflow").json()
        assert wf["decision"]["decision"] == "needs_review"
        reasons = " ".join(wf["decision"]["reasons"])
        assert "quantity 14 differs from PO quantity 12" in reasons
        keys = {x["key"]: x for x in wf["po_reconciliation"]["comparisons"]}
        assert keys["total"]["expected"] == "3,097.01" and keys["total"]["observed"] == "3,320.14"


def test_reviewer_decision_validation_timestamp_and_write_once():
    with TestClient(app) as c:
        doc = run(c, "lumen-invoice-8842-mismatch", "review-1")
        bad = c.post(f"/documents/{doc}/review", headers={"X-Session-Id": "review-1"}, json={"reviewer": "A", "outcome": "approve", "reason": "ok"})
        assert bad.status_code == 422
        bad2 = c.post(f"/documents/{doc}/review", headers={"X-Session-Id": "review-1"}, json={"reviewer": "Dana Reyes", "outcome": "maybe", "reason": "long enough reason"})
        assert bad2.status_code == 422
        forbidden = c.post(f"/documents/{doc}/review", headers={"X-Session-Id": "someone-else"}, json={"reviewer": "Dana Reyes", "outcome": "reject", "reason": "Quantity exceeds PO; ask supplier for credit note."})
        assert forbidden.status_code == 403
        ok = c.post(f"/documents/{doc}/review", headers={"X-Session-Id": "review-1"}, json={"reviewer": "Dana Reyes", "outcome": "reject", "reason": "Quantity exceeds PO; ask supplier for credit note."})
        assert ok.status_code == 201
        rec = ok.json()
        assert rec["system_decision"] == "needs_review" and rec["decided_at"].endswith("+00:00")
        again = c.post(f"/documents/{doc}/review", headers={"X-Session-Id": "review-1"}, json={"reviewer": "Dana Reyes", "outcome": "approve", "reason": "changed my mind entirely"})
        assert again.status_code == 409
        assert c.get(f"/documents/{doc}/workflow").json()["review"]["outcome"] == "reject"


def test_review_before_decision_is_rejected():
    with TestClient(app) as c:
        pdf = (settings.data_path.parent / "samples" / "hp-invoice-3301-valid.pdf") if False else None
        from tests.conftest import SAMPLES
        up = c.post("/documents", files={"file": ("x.pdf", (SAMPLES / "hp-invoice-3301-valid.pdf").read_bytes(), "application/pdf")})
        doc = up.json()["id"]
        assert c.post(f"/documents/{doc}/review", json={"reviewer": "Dana Reyes", "outcome": "approve", "reason": "looks fine to me"}).status_code in (404, 409)


def test_duplicate_invoice_is_scoped_to_the_visitor_session():
    with TestClient(app) as c:
        run(c, "hp-invoice-3301-valid", "dup-A")
        other = run(c, "hp-invoice-3301-valid", "dup-B")
        assert c.get(f"/documents/{other}/workflow").json()["decision"]["decision"] == "approve"
        same = run(c, "hp-invoice-3301-valid", "dup-A")
        assert c.get(f"/documents/{same}/workflow").json()["decision"]["decision"] == "flag"


def test_live_mode_without_key_is_a_clear_503(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with TestClient(app) as c:
        r = c.post("/samples/hp-invoice-3301-valid/run", params={"mode": "live"})
        assert r.status_code == 503 and "not configured" in r.json()["detail"]


# --- input validation + safe file access -----------------------------------------


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr(settings, "public_mode", True)
    monkeypatch.setattr(settings, "sandbox_token", "s3cret-test")


def test_public_mode_blocks_arbitrary_upload_and_destructive_calls(public):
    from tests.conftest import SAMPLES
    pdf = (SAMPLES / "hp-invoice-3301-valid.pdf").read_bytes()
    with TestClient(app) as c:
        assert c.post("/documents", files={"file": ("a.pdf", pdf, "application/pdf")}).status_code == 403
        assert c.delete("/documents").status_code == 403
        assert c.get("/documents").json() == []
        ok = c.post("/documents", files={"file": ("a.pdf", pdf, "application/pdf")}, headers={"X-Sandbox-Token": "s3cret-test"})
        assert ok.status_code == 201
        assert c.post("/documents", files={"file": ("a.pdf", pdf, "application/pdf")}, headers={"X-Sandbox-Token": "wrong"}).status_code == 403


def test_sandbox_rejects_oversize_wrong_type_and_spoofed_content(public):
    h = {"X-Sandbox-Token": "s3cret-test"}
    with TestClient(app) as c:
        big = b"%PDF-" + b"0" * (settings.sandbox_max_upload_mb * 1024 * 1024)
        assert c.post("/documents", files={"file": ("big.pdf", big, "application/pdf")}, headers=h).status_code == 413
        assert c.post("/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=h).status_code == 415
        assert c.post("/documents", files={"file": ("fake.pdf", b"not a pdf at all", "application/pdf")}, headers=h).status_code == 415
        assert c.post("/documents", files={"file": ("empty.pdf", b"", "application/pdf")}, headers=h).status_code == 400
        assert c.post("/documents", files={"file": ("bad.pdf", b"%PDF-1.4 garbage", "application/pdf")}, headers=h).status_code == 422


def test_database_and_traversal_are_not_served():
    with TestClient(app) as c:
        assert c.get("/files/app.db").status_code == 404
        assert c.get("/files/../state/app.db").status_code in (404, 400)
        assert c.get("/files/%2e%2e/state/app.db").status_code in (404, 400)


def test_expired_documents_are_purged_in_public_mode(public, monkeypatch):
    monkeypatch.setattr(settings, "doc_ttl_minutes", 0)
    with TestClient(app) as c:
        a = run(c, "hp-invoice-3301-valid", "ttl-1")
        run(c, "hp-invoice-3301-valid", "ttl-2")  # ingest purges documents older than the TTL
        assert c.get(f"/documents/{a}").status_code == 404


def test_public_mode_gates_paid_stage_routes_but_not_free_ones(public):
    from tests.conftest import SAMPLES
    pdf = (SAMPLES / "hp-invoice-3301-valid.pdf").read_bytes()
    h = {"X-Sandbox-Token": "s3cret-test"}
    with TestClient(app) as c:
        doc = c.post("/documents", files={"file": ("a.pdf", pdf, "application/pdf")}, headers=h).json()["id"]
        # paid / heavy engines and default providers need the token (even with the token
        # only ... the free ones below are exercised both without and with it)
        assert c.post(f"/documents/{doc}/ocr", params={"engine": "qwen-vl"}).status_code == 403
        assert c.post(f"/documents/{doc}/ocr", params={"engine": "docling"}).status_code == 403
        assert c.post(f"/documents/{doc}/ocr").status_code == 403  # default engine is docling
        assert c.post(f"/documents/{doc}/structure", params={"doc_type": "invoice"}).status_code == 403
        assert c.post(f"/documents/{doc}/decide").status_code == 403  # default provider is llm
        # free deterministic ones work, but a sandbox upload has no owning session, so
        # every stage route still needs the sandbox token
        assert c.post(f"/documents/{doc}/ocr", params={"engine": "pdf-text"}).status_code == 403
        assert c.post(f"/documents/{doc}/ocr", params={"engine": "pdf-text"}, headers=h).status_code == 200
        assert c.post(f"/documents/{doc}/structure", params={"doc_type": "invoice", "provider": "mock", "ocr_engine": "pdf-text"}, headers=h).status_code == 200
        assert c.post(f"/documents/{doc}/decide", params={"provider": "mock"}, headers=h).status_code == 200
        # a sandbox upload has no owning session, so reviewing it needs the token
        body = {"reviewer": "Dana Reyes", "outcome": "approve", "reason": "checked against the PO"}
        assert c.post(f"/documents/{doc}/review", json=body).status_code == 403
        assert c.post(f"/documents/{doc}/review", json=body, headers=h).status_code == 201


def test_session_secret_is_never_exposed_and_stage_routes_check_ownership(public):
    with TestClient(app) as c:
        doc = run(c, "hp-invoice-3301-valid", "owner-sess")
        wf = c.get(f"/documents/{doc}/workflow").json()
        assert "session" not in wf["meta"] and "owner-sess" not in str(wf)
        import hashlib
        assert hashlib.sha256(b"owner-sess").hexdigest() not in str(wf)
        # a different visitor cannot re-run the owner's stages; the owner can
        assert c.post(f"/documents/{doc}/decide", params={"provider": "rules"}, headers={"X-Session-Id": "intruder"}).status_code == 403
        assert c.post(f"/documents/{doc}/decide", params={"provider": "rules"}).status_code == 403
        assert c.post(f"/documents/{doc}/decide", params={"provider": "rules"}, headers={"X-Session-Id": "owner-sess"}).status_code == 200
        assert c.post(f"/documents/{doc}/prescan", headers={"X-Session-Id": "intruder"}).status_code == 403
