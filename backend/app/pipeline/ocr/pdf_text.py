"""Embedded-text-layer "OCR" for born-digital PDFs (adapter addition).

NOT optical recognition: it reads the PDF's own text layer with PyMuPDF, so it is
fast, free and deterministic. It exists so the public sample mode can run the same
downstream stages (structuring, PO reconciliation, decision) without loading Docling
or paying for a VLM. Scanned PDFs/images have no text layer and are rejected - use
the docling or qwen-vl engines for those.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from app import storage
from app.config import settings
from app.schemas import OCRBlock, OCRPage

from .base import OCREngine


class PdfTextEngine(OCREngine):
    name = "pdf-text"
    version = "1.0"

    def _ocr_pages(self, doc_id: str, pages: list[Path]) -> tuple[list[OCRPage], list[str]]:
        original = storage._doc_dir(doc_id) / "original.pdf"
        if not original.exists():
            raise ValueError("pdf-text engine needs a PDF with an embedded text layer.")
        scale = settings.render_dpi / 72.0
        out: list[OCRPage] = []
        with fitz.open(original) as pdf:
            for page_no, page in enumerate(pdf, start=1):
                blocks: list[OCRBlock] = []
                for b in page.get_text("dict")["blocks"]:
                    for line in b.get("lines", []):
                        text = "".join(s["text"] for s in line["spans"]).strip()
                        if text:
                            x0, y0, x1, y1 = (v * scale for v in line["bbox"])
                            blocks.append(OCRBlock(page=page_no, text=text, bbox=(x0, y0, x1, y1),
                                                   confidence=1.0, label="text"))
                if not blocks:
                    raise ValueError("No embedded text layer found; use the docling or qwen-vl engine.")
                out.append(OCRPage(page=page_no, text="\n".join(b.text for b in blocks), blocks=blocks, tables=[]))
        return out, []
