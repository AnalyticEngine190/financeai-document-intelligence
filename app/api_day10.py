"""Add Day 10 routes without modifying the existing app.api implementation.

Run: python -m uvicorn app.api_day10:app --host 127.0.0.1 --port 8000
The original /ask and /health endpoints remain available for 98 regressions.
"""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api import app  # reuse existing FastAPI object and legacy endpoints
from app.document_registry import list_documents
from app.document_resolver import DocumentSelection
from app.document_answering import answer_with_selection

router = APIRouter()


class DocumentSelectionRequest(BaseModel):
    mode: Literal["auto", "manual", "compare"] = "auto"
    document_ids: list[str] = Field(default_factory=list)


class AskV2Request(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_selection: DocumentSelectionRequest = Field(default_factory=DocumentSelectionRequest)


class AskV2Response(BaseModel):
    answer: str
    supported: bool
    sources: list[dict] = Field(default_factory=list)
    needs_clarification: bool = False
    is_small_talk: bool = False
    selected_documents: list[dict] = Field(default_factory=list)
    selection_reason: str = ""


@router.get("/documents")
def documents() -> dict:
    try:
        return {"documents": [d.public_dict() for d in list_documents()]}
    except Exception as exc:
        raise HTTPException(503, detail=f"Document index unavailable: {exc}") from exc


@router.post("/ask_v2", response_model=AskV2Response)
def ask_v2(body: AskV2Request) -> dict:
    if not body.question.strip():
        raise HTTPException(422, detail="Question cannot be empty")
    try:
        selection = DocumentSelection(mode=body.document_selection.mode,
                                      document_ids=tuple(body.document_selection.document_ids))
        return answer_with_selection(body.question, selection)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


app.include_router(router)

from app.document_upload import router as upload_router  # POST /documents/ingest
app.include_router(upload_router)
