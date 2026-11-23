"""POST /documents/ingest: upload one PDF and index it with the existing pipeline.

This module deliberately contains NO extraction, chunking or embedding code.
Everything after validation is delegated to app.document_ingest.ingest_pdf(),
the same function used by scripts/ingest_reports_day10.py, so uploaded reports
get exactly the same SHA-256 duplicate detection, chunking, embeddings,
registry entry and rollback-on-failure as the reports you already have.

Limits (environment variables, optional):
  FINANCE_AI_MAX_UPLOAD_MB     default 50
  FINANCE_AI_MAX_UPLOAD_PAGES  default 400
"""
from __future__ import annotations

import importlib.util
import io
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.document_ingest import ingest_pdf, delete_indexed_document

log = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_REPORT_TYPES = {"financial_report", "interim_report", "factbook"}
_INGEST_LOCK = threading.Lock()  # one indexing job at a time: embeddings are heavy
_MULTIPART_OK = bool(importlib.util.find_spec("python_multipart")
                     or importlib.util.find_spec("multipart"))


def _max_bytes() -> int:
    try:
        return max(1, int(float(os.getenv("FINANCE_AI_MAX_UPLOAD_MB", "50")) * 1024 * 1024))
    except ValueError:
        return 50 * 1024 * 1024


def _max_pages() -> int:
    try:
        return max(1, int(os.getenv("FINANCE_AI_MAX_UPLOAD_PAGES", "400")))
    except ValueError:
        return 400


def _safe_filename(name: str | None) -> str:
    stem = Path(name or "report.pdf").name  # drop any directory components
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "report"
    if not stem.casefold().endswith(".pdf"):
        stem += ".pdf"
    return stem[:150]


def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """Read the upload in chunks and stop as soon as it exceeds the limit."""
    chunks, total = [], 0
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, detail=f"The file is larger than the {limit // (1024 * 1024)} MB upload limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _page_count(data: bytes) -> int:
    """Validate PDF bytes without opening a temporary file (important on Windows)."""
    try:
        try:
            import fitz  # PyMuPDF, same parser the ingestion pipeline prefers
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if pdf.is_encrypted:
                    raise ValueError("Encrypted PDFs are not supported.")
                return pdf.page_count
        except ImportError:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported.")
            return len(reader.pages)
    except ValueError:
        raise
    except Exception as exc:  # corrupt or truncated file
        raise ValueError("The file could not be opened as a valid PDF.") from exc


def _ingest_upload(upload: UploadFile, *, issuer: str, year: int, quarter: int,
                   currency: str, report_type: str) -> dict:
    if report_type not in ALLOWED_REPORT_TYPES:
        raise HTTPException(422, detail="Report type must be one of: " + ", ".join(sorted(ALLOWED_REPORT_TYPES)))
    data = _read_limited(upload, _max_bytes())
    if not data:
        raise HTTPException(422, detail="The uploaded file is empty.")
    # Validate real content, not the extension: a PDF starts with %PDF- near byte 0.
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(422, detail="The file is not a PDF (its content does not look like a PDF document).")

    try:
        # Validate in memory first. PyMuPDF can leave a failed path-based open
        # locked on Windows when it rejects a corrupt PDF.
        pages = _page_count(data)
        if pages < 1:
            raise ValueError("The PDF has no pages.")
        if pages > _max_pages():
            raise ValueError(f"The PDF has {pages} pages; the limit is {_max_pages()}.")
        with tempfile.TemporaryDirectory(prefix="financeai_upload_") as tmp:
            path = Path(tmp) / _safe_filename(upload.filename)
            path.write_bytes(data)
            with _INGEST_LOCK:
                result = ingest_pdf(path, issuer=issuer, year=year, quarter=quarter,
                                    currency=currency, report_type=report_type)
    except ValueError as exc:
        message = str(exc)
        code = 409 if "already indexed with different" in message else 422
        raise HTTPException(code, detail=message) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Document indexing failed")
        raise HTTPException(
            500, detail="Indexing failed, so nothing was added to the report list. "
                        "Check that the embedding model and vector database are available.") from exc

    status = result.get("status", "indexed")
    document = {k: v for k, v in result.items() if k != "status"}
    return {"status": status, "document": document}


if _MULTIPART_OK:
    @router.post("/documents/ingest")
    def ingest_document(
        file: Annotated[UploadFile, File(description="One PDF report")],
        issuer: Annotated[str, Form(min_length=1, max_length=120)],
        year: Annotated[int, Form(ge=2000, le=2100)],
        quarter: Annotated[int, Form(ge=1, le=4)],
        currency: Annotated[str, Form(min_length=3, max_length=3)],
        report_type: Annotated[str, Form()] = "financial_report",
    ) -> dict:
        return _ingest_upload(file, issuer=issuer.strip(), year=year, quarter=quarter,
                              currency=currency.strip().upper(), report_type=report_type.strip())
else:  # keep the rest of the API working even if the dependency is missing
    @router.post("/documents/ingest")
    def ingest_document_unavailable() -> dict:
        raise HTTPException(503, detail="PDF upload needs the 'python-multipart' package. "
                                        "Run: pip install python-multipart, then restart the API.")

@router.delete("/documents/{document_id}")
def delete_document(document_id: str) -> dict:
    """Remove one exact report from the local Chroma index."""
    try:
        with _INGEST_LOCK:
            result = delete_indexed_document(document_id)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Document deletion failed")
        raise HTTPException(500, detail="The selected report could not be deleted from the local index.") from exc
    if result.get("status") == "not_found":
        raise HTTPException(404, detail="The selected report is not present in the index.")
    return result
