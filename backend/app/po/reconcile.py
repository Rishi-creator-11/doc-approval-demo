"""Invoice vs purchase-order reconciliation using exact decimal arithmetic.

The extracted invoice fields (value/confidence/grounding nodes) are compared with
the seeded PO. Every comparison records the expected value, the observed value and
the evidence snippet/page, so a reviewer can see why a mismatch routed to review.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

from .seed import PurchaseOrder, get_po

Status = Literal["match", "mismatch", "missing"]
Overall = Literal["matched", "mismatch", "po_not_found", "no_po_number"]

TOTAL_TOLERANCE = Decimal("0.01")
_SUFFIXES = {"co", "inc", "llc", "ltd", "corp", "company", "corporation", "the"}


class Evidence(BaseModel):
    page: int | None = None
    snippet: str | None = None


class Comparison(BaseModel):
    key: str
    label: str
    expected: str | None
    observed: str | None
    status: Status
    evidence: Evidence | None = None


class POReconciliation(BaseModel):
    po_number: str | None
    supplier_on_po: str | None = None
    status: Overall
    comparisons: list[Comparison] = []
    reasons: list[str] = []


def _dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money(d: Decimal | None) -> str | None:
    return None if d is None else f"{d.quantize(Decimal('0.01')):,.2f}"


def _qty(d: Decimal | None) -> str | None:
    return None if d is None else f"{d.normalize():f}"


def norm_name(name: str) -> str:
    tokens = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " and ")).split()
    return " ".join(t for t in tokens if t not in _SUFFIXES)


def _tokens(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _node(fields: dict, key: str) -> dict:
    n = fields.get(key)
    return n if isinstance(n, dict) else {}


def _evidence(node: dict) -> Evidence | None:
    g = node.get("grounding") or {}
    if g.get("page") is None and not g.get("snippet"):
        return None
    return Evidence(page=g.get("page"), snippet=g.get("snippet"))


def _match_line(desc: str, po: PurchaseOrder, used: set[int]) -> int | None:
    """Index of the PO line whose description best matches (exact, then token overlap)."""
    target = _tokens(desc)
    best, best_score = None, 0.0
    for i, ln in enumerate(po.lines):
        if i in used:
            continue
        cand = _tokens(ln.desc)
        if not cand or not target:
            continue
        score = len(cand & target) / len(cand | target)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.5 else None


def reconcile_invoice(fields: dict) -> POReconciliation:
    """Compare structured invoice ``fields`` against the matching seeded PO."""
    po_node = _node(fields, "po_number")
    po_raw = po_node.get("value")
    if not po_raw:
        return POReconciliation(
            po_number=None,
            status="no_po_number",
            reasons=["Invoice carries no purchase-order number, so it cannot be matched."],
        )
    po = get_po(str(po_raw))
    if po is None:
        return POReconciliation(
            po_number=str(po_raw),
            status="po_not_found",
            comparisons=[
                Comparison(
                    key="po_number", label="PO number", expected="a seeded PO",
                    observed=str(po_raw), status="mismatch", evidence=_evidence(po_node),
                )
            ],
            reasons=[f"PO {po_raw} does not exist in the purchasing system."],
        )

    cmps: list[Comparison] = []
    reasons: list[str] = []

    def add(key, label, expected, observed, ok, node=None, missing=False):
        status: Status = "missing" if missing else ("match" if ok else "mismatch")
        cmps.append(Comparison(key=key, label=label, expected=expected, observed=observed,
                               status=status, evidence=_evidence(node or {})))
        return status

    cmps.append(Comparison(key="po_number", label="PO number", expected=po.po_number,
                           observed=str(po_raw), status="match", evidence=_evidence(po_node)))

    vendor_node = _node(fields, "vendor")
    vendor = vendor_node.get("value")
    if vendor is None:
        add("supplier", "Supplier", po.supplier, None, False, vendor_node, missing=True)
        reasons.append("Supplier name could not be read from the invoice.")
    else:
        ok = norm_name(str(vendor)) == norm_name(po.supplier)
        add("supplier", "Supplier", po.supplier, str(vendor), ok, vendor_node)
        if not ok:
            reasons.append(f"Supplier '{vendor}' does not match PO supplier '{po.supplier}'.")

    used: set[int] = set()
    for idx, item in enumerate(fields.get("line_items") or [], start=1):
        desc_node = _node(item, "desc")
        desc = str(desc_node.get("value") or "")
        qty = _dec(_node(item, "qty").get("value"))
        price = _dec(_node(item, "unit_price").get("value"))
        amount = _dec(_node(item, "amount").get("value"))
        i = _match_line(desc, po, used)
        if i is None:
            add(f"line{idx}.desc", f"Line {idx}: item", None, desc or None, False, desc_node)
            reasons.append(f"Line {idx} '{desc}' is not on {po.po_number}.")
            continue
        used.add(i)
        pl = po.lines[i]
        label = f"Line {idx}: {pl.desc}"
        if qty is None:
            add(f"line{idx}.qty", f"{label} qty", _qty(pl.qty), None, False, item, missing=True)
        elif add(f"line{idx}.qty", f"{label} qty", _qty(pl.qty), _qty(qty), qty == pl.qty,
                 _node(item, "qty")) == "mismatch":
            reasons.append(f"Line {idx} quantity {_qty(qty)} differs from PO quantity {_qty(pl.qty)} ({pl.desc}).")
        if price is None:
            add(f"line{idx}.price", f"{label} unit price", _money(pl.unit_price), None, False, item, missing=True)
        elif add(f"line{idx}.price", f"{label} unit price", _money(pl.unit_price), _money(price),
                 price == pl.unit_price, _node(item, "unit_price")) == "mismatch":
            reasons.append(f"Line {idx} unit price {_money(price)} differs from PO price {_money(pl.unit_price)} ({pl.desc}).")
        if amount is not None:
            expected_amt = (qty * price).quantize(Decimal("0.01")) if qty is not None and price is not None else pl.amount
            ok = abs(amount - expected_amt) <= TOTAL_TOLERANCE
            add(f"line{idx}.amount", f"{label} amount", _money(expected_amt), _money(amount), ok, _node(item, "amount"))
            if not ok:
                reasons.append(f"Line {idx} amount {_money(amount)} is not qty x price ({_money(expected_amt)}).")

    for i, pl in enumerate(po.lines):
        if i not in used:
            add(f"po_line{i + 1}", f"PO line not invoiced: {pl.desc}", f"{_qty(pl.qty)} @ {_money(pl.unit_price)}",
                None, False, None, missing=True)
            reasons.append(f"PO line '{pl.desc}' ({_qty(pl.qty)}) is not on the invoice.")

    total_node = _node(fields, "total")
    total = _dec(total_node.get("value"))
    if total is None:
        add("total", "Invoice total", _money(po.total), None, False, total_node, missing=True)
        reasons.append("Invoice total could not be read.")
    else:
        ok = abs(total - po.total) <= TOTAL_TOLERANCE
        add("total", "Invoice total", _money(po.total), _money(total), ok, total_node)
        if not ok:
            reasons.append(f"Invoice total {_money(total)} differs from PO total {_money(po.total)} (difference {_money(total - po.total)}).")

    bad = any(c.status != "match" for c in cmps)
    return POReconciliation(
        po_number=po.po_number, supplier_on_po=po.supplier,
        status="mismatch" if bad else "matched", comparisons=cmps, reasons=reasons,
    )
