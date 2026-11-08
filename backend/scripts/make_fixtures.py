"""Generate the two original, synthetic demo invoices + their recorded extractions.

Run: uv run --no-sync python scripts/make_fixtures.py
Both vendors and all numbers are fictional. The mismatched invoice differs from
PO-24018 in the cyan cartridge quantity (14 vs 12) and unit price (93.10 vs 91.40).
Added by the adapter (not part of upstream doc-approval-demo).
"""

import json
from decimal import Decimal as D
from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parent.parent / "samples"

INVOICES = [
    {
        "file": "hp-invoice-3301-valid",
        "vendor": "Harbor & Pine Paper Co.",
        "address": "410 Quay Street, Portland, ME 04101",
        "invoice_no": "INV-HP-3301",
        "po": "PO-24017",
        "date": "2026-10-12",
        "due": "2026-11-11",
        "lines": [("Matte coated paper 100lb 12x18 (ream)", 40, "62.50"), ("Archival cardstock 80lb (ream)", 25, "48.00")],
    },
    {
        "file": "lumen-invoice-8842-mismatch",
        "vendor": "Lumen Ink & Toner Supply",
        "address": "88 Foundry Row, Albany, NY 12207",
        "invoice_no": "INV-LI-8842",
        "po": "PO-24018",
        "date": "2026-10-14",
        "due": "2026-11-13",
        "lines": [("Cyan pigment ink cartridge", 14, "93.10"), ("Magenta pigment ink cartridge", 12, "91.40"), ("Black pigment ink cartridge", 8, "84.25")],
    },
]


def fmt(d: D) -> str:
    return f"{d.quantize(D('0.01')):,.2f}"


def build(inv: dict) -> None:
    rows, sub = [], D("0")
    for desc, qty, price in inv["lines"]:
        amt = (D(qty) * D(price)).quantize(D("0.01"))
        sub += amt
        rows.append((f"{qty} x {desc} @ {price} = {fmt(amt)}", desc, qty, price, fmt(amt)))
    tax = (sub * D("0.08")).quantize(D("0.01"))
    total = sub + tax
    lines = [
        (inv["vendor"], 16),
        (inv["address"], 10),
        ("", 10),
        (f"INVOICE {inv['invoice_no']}", 14),
        (f"PO: {inv['po']}", 11),
        (f"Date: {inv['date']}   Due: {inv['due']}", 11),
        ("", 10),
        *[(r[0], 11) for r in rows],
        ("", 10),
        (f"Subtotal: {fmt(sub)}", 11),
        (f"Tax (8%): {fmt(tax)}", 11),
        (f"Total: ${fmt(total)} USD", 12),
        ("Payment terms: Net 30", 11),
        ("SYNTHETIC DEMO DOCUMENT - fictional vendor, not a real invoice", 8),
    ]
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Letterhead band + rule lines give the page realistic ink coverage so the upstream
    # pre-scan contrast gate does not warn on an otherwise sparse born-digital page.
    page.draw_rect(fitz.Rect(0, 0, 612, 64), color=None, fill=(0.12, 0.2, 0.3))
    page.draw_rect(fitz.Rect(40, 330, 572, 334), color=None, fill=(0.1, 0.1, 0.1))
    y = 100
    for n, (text, size) in enumerate(lines):
        if text:
            if n == 0:
                page.insert_text((60, 40), text, fontsize=22, fontname="hebo", color=(1, 1, 1))
                y = 98
                continue
            page.insert_text((60, y), text, fontsize=size + 3, fontname="hebo" if size >= 12 else "helv")
        y += size + 16
    doc.save(OUT / f"{inv['file']}.pdf")

    ex = [
        {"cls": "vendor", "text": inv["vendor"]},
        {"cls": "invoice_no", "text": inv["invoice_no"]},
        {"cls": "po_number", "text": inv["po"]},
        {"cls": "invoice_date", "text": inv["date"]},
        {"cls": "due_date", "text": inv["due"]},
        *[
            {"cls": "line_item", "text": r[0], "attributes": {"desc": r[1], "qty": str(r[2]), "unit_price": r[3], "amount": r[4]}}
            for r in rows
        ],
        {"cls": "subtotal", "text": fmt(sub)},
        {"cls": "tax", "text": fmt(tax)},
        {"cls": "total", "text": f"${fmt(total)}"},
        {"cls": "currency", "text": "USD"},
        {"cls": "payment_terms", "text": "Net 30"},
    ]
    rec = OUT / "recorded"
    rec.mkdir(exist_ok=True)
    (rec / f"{inv['file']}.json").write_text(json.dumps(ex, indent=2) + "\n")
    print(inv["file"], "subtotal", sub, "tax", tax, "total", total)


if __name__ == "__main__":
    for i in INVOICES:
        build(i)
