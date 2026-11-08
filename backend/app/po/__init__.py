"""Synthetic purchasing workflow added on top of the upstream pipeline.

Seeded purchase orders, a decimal-exact invoice-vs-PO reconciliation, and a
reviewer decision record. Everything here is fictional demo data.

Modified from madebyagents/doc-approval-demo (MIT): new module.
"""

from .reconcile import POReconciliation, reconcile_invoice
from .seed import PURCHASE_ORDERS, get_po

__all__ = ["PURCHASE_ORDERS", "POReconciliation", "get_po", "reconcile_invoice"]
