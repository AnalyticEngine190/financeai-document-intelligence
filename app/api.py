
"""HTTP API for the Financial Document Intelligence Assistant."""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.answering import answer_question


app = FastAPI(
    title="Financial Document Intelligence API",
    version="0.1.0",
    description="Grounded financial questions with PDF citations.",
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class Source(BaseModel):
    document: str
    page: int


class AskResponse(BaseModel):
    answer: str
    supported: bool
    sources: list[Source]
    needs_clarification: bool = False
    is_small_talk: bool = False
    
def _document_id() -> str:
    """Read the current document's ID from its processed JSON."""

    path = Path(
        os.getenv(
            "FINANCE_AI_CHUNKS_FILE",
            "data/processed/11cb15780bed_chunks.json",
        )
    )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        document_id = data["document_id"]

        if not isinstance(document_id, str) or not document_id:
            raise ValueError("Invalid document ID")

        return document_id

    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(
            f"Cannot load document metadata from {path}"
        ) from exc


@app.get("/health")
def health():
    """Confirm that the API process is running."""
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest):
    """Answer a financial question using the indexed PDF."""

    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=422,
            detail="Question cannot be empty",
        )

    try:
        return answer_question(
            question,
            document_id=_document_id(),
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Document or local model is unavailable",
        ) from exc
