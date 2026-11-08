"""Document ingestion + retrieval endpoints (Phase 1)."""

import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlmodel import Session, delete, select

from app import storage
from app.config import settings
from app.db import get_session
from app.models import DocType, Document, PipelineRun
from app.schemas import DocumentDetail, DocumentSummary, PageInfo

router = APIRouter(prefix="/documents", tags=["documents"])

# Modified: magic-byte signatures so a renamed file can't masquerade as another type.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


def require_sandbox(token: str | None) -> None:
    """In PUBLIC_MODE, arbitrary uploads and destructive calls need the sandbox token."""
    if not settings.public_mode:
        return
    if not settings.sandbox_token or not token or not secrets.compare_digest(token, settings.sandbox_token):
        raise HTTPException(
            status_code=403,
            detail="Arbitrary upload is disabled on the public demo. Use a predefined sample, or the authorized sandbox.",
        )


def ingest_document(session: Session, filename: str, content: bytes, doc_type: DocType | None) -> Document:
    """Validate, persist and rasterize a document (shared by upload and sample runs)."""
    try:
        ext, mime = storage.detect_type(filename or "")
    except storage.UnsupportedFileType:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Accepted: {', '.join(sorted(storage.ALLOWED_TYPES))}",
        ) from None
    limit_mb = settings.sandbox_max_upload_mb if settings.public_mode else settings.max_upload_mb
    if len(content) > limit_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit_mb} MB upload limit.")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if not content.startswith(_MAGIC[mime]):
        raise HTTPException(status_code=415, detail="File contents do not match its extension.")

    storage.purge_expired(session)
    safe_name = "".join(ch for ch in (filename or f"upload{ext}") if ch.isprintable() and ch not in '/\\')[:120]
    doc = Document(filename=safe_name or f"upload{ext}", doc_type=doc_type, mime=mime)
    original = storage.save_original(doc.id, ext, content)
    try:
        doc.page_count = storage.normalize_to_pages(doc.id, original, mime)
    except Exception as exc:  # corrupt/unreadable file
        storage.delete_document_dir(doc.id)
        raise HTTPException(
            status_code=422, detail="Could not process file; it may be corrupt or unsupported."
        ) from exc
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def _to_detail(doc: Document) -> DocumentDetail:
    pages = [PageInfo(**p) for p in storage.page_urls(doc.id, doc.page_count)]
    return DocumentDetail(**doc.model_dump(), pages=pages)


@router.post("", response_model=DocumentDetail, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    doc_type: DocType | None = Form(default=None),
    x_sandbox_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> DocumentDetail:
    """Upload a PDF/PNG/JPG/TIFF, persist it, and rasterize pages to PNGs."""
    require_sandbox(x_sandbox_token)
    # Read at most limit+1 bytes so an oversized body is rejected without buffering it all.
    limit_mb = settings.sandbox_max_upload_mb if settings.public_mode else settings.max_upload_mb
    content = await file.read(limit_mb * 1024 * 1024 + 1)
    doc = ingest_document(session, file.filename or "", content, doc_type)
    return _to_detail(doc)


@router.get("", response_model=list[DocumentSummary])
def list_documents(
    x_sandbox_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> list[Document]:
    """List documents, newest first (public mode: only the authorized sandbox sees the list)."""
    if settings.public_mode:
        try:
            require_sandbox(x_sandbox_token)
        except HTTPException:
            return []
    return session.exec(select(Document).order_by(Document.created_at.desc())).all()


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str, session: Session = Depends(get_session)) -> DocumentDetail:
    """Document detail with per-page image + thumbnail URLs."""
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _to_detail(doc)


@router.delete("", status_code=204)
def delete_all_documents(
    x_sandbox_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> None:
    """Permanently remove every document: all pipeline runs, DB rows, and files.

    Ids are collected before the rows are deleted so the on-disk ``data/<id>/``
    trees can be removed after the DB commit.
    """
    require_sandbox(x_sandbox_token)
    doc_ids = list(session.exec(select(Document.id)).all())
    session.exec(delete(PipelineRun))
    session.exec(delete(Document))
    session.commit()

    for doc_id in doc_ids:
        storage.delete_document_dir(doc_id)


@router.delete("/{doc_id}", status_code=204)
def delete_document(
    doc_id: str,
    x_sandbox_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> None:
    """Permanently remove a document: its pipeline runs, DB row, and on-disk files.

    The PipelineRun -> Document foreign key has no DB cascade configured, so the
    runs are deleted explicitly before the document.
    """
    require_sandbox(x_sandbox_token)
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    session.exec(delete(PipelineRun).where(PipelineRun.document_id == doc_id))
    session.delete(doc)
    session.commit()

    storage.delete_document_dir(doc_id)
