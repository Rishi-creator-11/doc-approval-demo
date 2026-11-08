"""Fictional purchase orders (original synthetic data, no real companies)."""

from __future__ import annotations

from decimal import Decimal as D

from pydantic import BaseModel


class POLine(BaseModel):
    desc: str
    qty: D
    unit_price: D

    @property
    def amount(self) -> D:
        return (self.qty * self.unit_price).quantize(D("0.01"))


class PurchaseOrder(BaseModel):
    po_number: str
    supplier: str
    currency: str = "USD"
    tax_rate: D  # fraction, e.g. 0.08
    lines: list[POLine]

    @property
    def subtotal(self) -> D:
        return sum((ln.amount for ln in self.lines), D("0.00"))

    @property
    def tax(self) -> D:
        return (self.subtotal * self.tax_rate).quantize(D("0.01"))

    @property
    def total(self) -> D:
        return self.subtotal + self.tax


PURCHASE_ORDERS: dict[str, PurchaseOrder] = {
    po.po_number: po
    for po in [
        PurchaseOrder(
            po_number="PO-24017",
            supplier="Harbor & Pine Paper Co.",
            tax_rate=D("0.08"),
            lines=[
                POLine(desc="Matte coated paper 100lb 12x18 (ream)", qty=D(40), unit_price=D("62.50")),
                POLine(desc="Archival cardstock 80lb (ream)", qty=D(25), unit_price=D("48.00")),
            ],
        ),
        PurchaseOrder(
            po_number="PO-24018",
            supplier="Lumen Ink & Toner Supply",
            tax_rate=D("0.08"),
            lines=[
                POLine(desc="Cyan pigment ink cartridge", qty=D(12), unit_price=D("91.40")),
                POLine(desc="Magenta pigment ink cartridge", qty=D(12), unit_price=D("91.40")),
                POLine(desc="Black pigment ink cartridge", qty=D(8), unit_price=D("84.25")),
            ],
        ),
        PurchaseOrder(
            po_number="PO-24019",
            supplier="Northgate Packaging Works",
            tax_rate=D("0.08"),
            lines=[
                POLine(desc="Corrugated mailer box 12x9x3", qty=D(600), unit_price=D("0.84")),
                POLine(desc="Tissue wrap sheet", qty=D(1000), unit_price=D("0.12")),
            ],
        ),
    ]
}


def get_po(po_number: str) -> PurchaseOrder | None:
    key = "".join(po_number.upper().split())
    return PURCHASE_ORDERS.get(key)
