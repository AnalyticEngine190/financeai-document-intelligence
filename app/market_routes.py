"""Additive market-data routes and conservative /ask_v3 routing.

Never changes /ask_v2, the document index, or existing financial verification.
"""
from __future__ import annotations

from decimal import Decimal
import hmac
import os
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.market_data import MarketClient, MarketError, require_exchange, require_search, require_symbol
from app.market_entitlements import status_payload

router = APIRouter()
market_client = MarketClient()


class MarketInstrument(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    exchange: str | None = Field(default=None, max_length=50)


class HistoryRequest(MarketInstrument):
    interval: Literal["1day", "1week", "1month"] = "1day"
    outputsize: int = Field(default=90, ge=2, le=250)


class CompareRequest(BaseModel):
    first: MarketInstrument
    second: MarketInstrument
    outputsize: int = Field(default=90, ge=2, le=250)


class DocumentSelectionRequest(BaseModel):
    mode: Literal["auto", "manual", "compare"] = "auto"
    document_ids: list[str] = Field(default_factory=list)


class AskV3Request(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    market_symbol: str | None = Field(default=None, max_length=20)
    market_exchange: str | None = Field(default=None, max_length=50)
    document_question: str | None = Field(default=None, max_length=4000)
    document_selection: DocumentSelectionRequest = Field(default_factory=DocumentSelectionRequest)
    history_outputsize: int = Field(default=90, ge=2, le=250)


def _gate(request: Request) -> None:
    """Avoid accidentally publishing paid API credits on an unauthenticated host."""
    token = os.getenv("FINANCE_AI_MARKET_ACCESS_TOKEN", "")
    if token:
        incoming = request.headers.get("X-Market-Token", "")
        if not hmac.compare_digest(incoming, token):
            raise HTTPException(status_code=403, detail="market_access_denied")
    elif not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(status_code=403, detail="market_local_only")


def _call(work):
    try:
        return work()
    except MarketError as error:
        raise HTTPException(status_code=error.status, detail=error.code) from None


def _doc(question: str, selection: DocumentSelectionRequest) -> dict:
    from app.document_answering import answer_with_selection
    from app.document_resolver import DocumentSelection

    return answer_with_selection(question, DocumentSelection(
        mode=selection.mode, document_ids=tuple(selection.document_ids)))


@router.get("/market/status")
def market_status(request: Request) -> dict:
    _gate(request)
    return {"provider": "Twelve Data", "configured": market_client.is_configured(),
            "quotes_real_time_guaranteed": False, "provider_access": "server_side_only",
            "subscription": status_payload(),
            "warning": "The configured plan is application configuration, not auto-detected from the API key. Exchange entitlements and quote delays must be verified with the data provider."}


@router.get("/market/search")
def market_search(request: Request, query: str) -> dict:
    _gate(request)
    return _call(lambda: market_client.search(query))


@router.get("/market/quote")
def market_quote(request: Request, symbol: str, exchange: str | None = None) -> dict:
    _gate(request)
    return _call(lambda: market_client.quote(symbol, exchange))


@router.get("/market/history")
def market_history(request: Request, symbol: str, exchange: str | None = None,
                   interval: Literal["1day", "1week", "1month"] = "1day",
                   outputsize: int = 90) -> dict:
    _gate(request)
    return _call(lambda: market_client.history(symbol, exchange,
                                               interval=interval, outputsize=outputsize))


@router.post("/market/compare")
def market_compare(request: Request, body: CompareRequest) -> dict:
    _gate(request)
    return _call(lambda: market_client.compare(
        (require_symbol(body.first.symbol), require_exchange(body.first.exchange)),
        (require_symbol(body.second.symbol), require_exchange(body.second.exchange)),
        outputsize=body.outputsize))


_MARKET_RE = re.compile(
    r"\b(?:stock price|share price|share quotation|stock quote|market quote|price history|"
    r"stock performance|share performance|stock market|share market|equity price|"
    r"share-price|ticker|trading price|historical prices|stock chart|stock's price)\b",
    re.I,
)
_HISTORY_RE = re.compile(r"\b(?:historical|history|chart|trend|performance|past (?:week|month|year)|last (?:week|month|year)|6 months|six months)\b", re.I)
_TICKER_PATTERNS = (
    re.compile(r"\b(?:ticker|symbol)\s*(?:is|:)?\s*([A-Z][A-Z0-9._-]{0,19})\b"),
    re.compile(r"\b([A-Z][A-Z0-9._-]{0,19})\s+(?:stock|shares?|share-price)\b"),
    re.compile(r"\b(?:stock|shares?)\s+(?:for|of)\s+([A-Z]{2,9})\b"),
)
_COMPANY_PATTERNS = (
    re.compile(r"\b([A-Z][a-zA-Z]{2,})(?:['’]s)?\s+(?:stock|share)\s+(?:price|quote|performance|history)\b"),
    re.compile(r"\b(?:stock|share)\s+(?:price|quote|performance|history)\s+(?:of|for)\s+([A-Z][a-zA-Z]{2,})\b"),
)


def _symbol_from_text(question: str) -> str | None:
    for pattern in _TICKER_PATTERNS:
        match = pattern.search(question)
        if match:
            ticker = match.group(1)
            if ticker not in {"IS", "THE", "Q3", "Q2", "CET1", "PDF"}:
                return ticker
    return None


def _company_from_text(question: str) -> str | None:
    for pattern in _COMPANY_PATTERNS:
        match = pattern.search(question)
        if match:
            return match.group(1)
    return None


def _market_source(item: dict, endpoint: str) -> dict:
    return {"provider": "Twelve Data", "endpoint": endpoint,
            "symbol": item["symbol"], "exchange": item["exchange"],
            "provider_datetime": item.get("provider_datetime"),
            "fetched_at_utc": item.get("fetched_at_utc")}


def _quote_answer(item: dict) -> str:
    return (f"Latest available provider-reported closing price for {item['symbol']} "
            f"on {item['exchange']}: {item['currency']} {item['close']}. "
            f"Provider timestamp (exchange local): {item['provider_datetime']}. "
            "Quote delay and exchange entitlements are not independently verified; this is not guaranteed live.")


def _history_answer(item: dict) -> str:
    points = item["points"]
    if not points:
        return "No history was supplied by the provider."
    return (f"Historical closing-price series for {item['symbol']} on {item['exchange']}: "
            f"{len(points)} observations, {points[0]['date']} to {points[-1]['date']}. "
            f"Latest close: {item['currency']} {points[-1]['close']}. "
            "Historical price-only series, not a total return; dividends are excluded.")


@router.post("/ask_v3")
def ask_v3(request: Request, body: AskV3Request) -> dict:
    _gate(request)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="empty_question")
    explicit = bool(body.market_symbol)
    if not explicit and not _MARKET_RE.search(question):
        # Backwards compatible; existing source isolation and verification apply.
        try:
            original = _doc(question, body.document_selection)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_document_selection") from None
        except RuntimeError:
            raise HTTPException(status_code=503, detail="document_backend_unavailable") from None
        return {**original, "kind": "document"}
    if body.document_question is not None and not body.document_question.strip():
        raise HTTPException(status_code=422, detail="empty_document_question")
    symbol = body.market_symbol or _symbol_from_text(question)
    exchange = body.market_exchange
    if not symbol:
        company = _company_from_text(question)
        if not company:
            return {"answer": "Specify the exact stock ticker and exchange or search for the company first.",
                    "supported": False, "needs_clarification": True,
                    "kind": "market_clarification", "sources": [], "candidates": []}
        matches = _call(lambda: market_client.search(company))
        return {"answer": "Select the intended stock listing and exchange before retrieving a quote.",
                "supported": False, "needs_clarification": True, "kind": "market_clarification",
                "sources": [], "candidates": matches["candidates"]}
    # If the user did not specify an exchange, never guess among multiple listings.
    if not exchange:
        hits = _call(lambda: market_client.search(symbol))
        exact = [row for row in hits["candidates"] if row["symbol"] == symbol.upper()]
        if len(exact) != 1:
            return {"answer": "Select an exact stock listing and exchange; symbols can be reused across markets.",
                    "supported": False, "needs_clarification": True,
                    "kind": "market_clarification", "sources": [],
                    "candidates": exact or hits["candidates"]}
        exchange = exact[0]["exchange"]
    is_history = bool(_HISTORY_RE.search(question)) and not body.document_question
    if is_history:
        item = _call(lambda: market_client.history(symbol, exchange, outputsize=body.history_outputsize))
        result = {"answer": _history_answer(item), "supported": True,
                  "needs_clarification": False, "kind": "market_history",
                  "market": item, "sources": [_market_source(item, "/time_series")]}
    else:
        item = _call(lambda: market_client.quote(symbol, exchange))
        result = {"answer": _quote_answer(item), "supported": True,
                  "needs_clarification": False, "kind": "market_quote",
                  "market": item, "sources": [_market_source(item, "/quote")]}
    if body.document_question:
        try:
            doc = _doc(body.document_question, body.document_selection)
        except (RuntimeError, ValueError):
            doc = {"answer": "Document evidence could not be verified.", "supported": False,
                   "sources": [], "needs_clarification": False}
        result["kind"] = "combined"
        result["document"] = doc
        result["supported"] = bool(doc.get("supported")) and result["supported"]
        result["sources"] = result["sources"] + (doc.get("sources") or [])
        result["answer"] += ("\n\nReport evidence: " + (doc.get("answer") or "No verified report answer."))
        if not doc.get("supported"):
            result["answer"] += "\nThe report portion is not verified; do not infer a combined conclusion."
        else:
            result["answer"] += "\nMarket and report figures refer to different timestamps and should not be conflated."
    return result
