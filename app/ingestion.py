
"""Extract PDF text and preserve document metadata."""

from pathlib import Path
import hashlib

import pymupdf


def hash_file(path: Path) -> str:
    """Calculate the SHA-256 fingerprint of a file."""
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for block in iter(
            lambda: source.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def extract_pdf(pdf_path: str | Path) -> dict:
    """Extract PDF text while preserving page numbers."""

    path = Path(pdf_path)

    if not path.is_file():
        raise FileNotFoundError(f"Report not found: {path}")

    if path.suffix.lower() != ".pdf":
        raise ValueError("Expected a PDF file")

    pages = []

    with pymupdf.open(path) as document:
        if not document.is_pdf:
            raise ValueError("Input is not a valid PDF")

        if document.needs_pass:
            raise ValueError(
                "Password-protected PDFs are not supported"
            )

        for index, page in enumerate(document):
            text = page.get_text(
                "text",
                sort=True,
            ).strip()

            pages.append({
                "page_number": index + 1,
                "text": text,
            })

    return {
        "filename": path.name,
        "sha256": hash_file(path),
        "page_count": len(pages),
        "text_page_count": sum(
            bool(page["text"]) for page in pages
        ),
        "pages": pages,
    }
