from __future__ import annotations

from decimal import Decimal
from types import ModuleType, SimpleNamespace
import sys

# These tests exercise orchestration and resolver logic with synthetic document
# records and verified-cell stand-ins. They must not load a local Chroma index,
# embedding model, Ollama server, or market-data API.
if "chromadb" not in sys.modules:
    chromadb = ModuleType("chromadb")
    chromadb.PersistentClient = lambda **kwargs: None
    sys.modules["chromadb"] = chromadb
if "sentence_transformers" not in sys.modules:
    sentence_transformers = ModuleType("sentence_transformers")
    sentence_transformers.SentenceTransformer = object
    sys.modules["sentence_transformers"] = sentence_transformers

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection, resolve_documents
from app import document_answering, market_chat
from app.financial_plan import plan_question
from app.table_lookup import FinancialCell

NQ2 = DocumentRecord("nq2", "Nordea Q2 2026.pdf", "Nordea", 2026, 2, "factbook", "EUR")
NQ3 = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")
SEB = DocumentRecord("seb3", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
HB = DocumentRecord("hb3", "Handelsbanken Q3 2026.pdf", "Handelsbanken", 2026, 3, "interim_report", "SEK")
DOCS = [NQ2, NQ3, SEB, HB]


def _cell(metric: str, period: str, value: str, doc: DocumentRecord) -> FinancialCell:
    unit = "EURm" if doc.issuer == "Nordea" else "SEKm"
    return FinancialCell(
        metric=metric, period=period, scope="group", value=Decimal(value), unit=unit,
        document=doc.filename, page=7 if doc.issuer == "Nordea" else 5,
        row_label="Net profit for the period" if metric == "net_profit" else "Common Equity Tier 1 capital ratio",
        raw_value=value,
    )


def test_yearless_q4_net_profit_never_falls_through_to_latest_quarter(monkeypatch):
    calls = []
    monkeypatch.setattr(document_answering, "_one_cell", lambda *a, **k: calls.append((a, k)))
    result = document_answering.answer_with_selection(
        "Nordea Q4 net profit", DocumentSelection(), DOCS
    )
    assert result["supported"] is False
    assert result["needs_clarification"] is True
    assert "Q4" in result["answer"]
    assert "year" in result["answer"].casefold()
    assert "won't substitute the latest available quarter" in result["answer"].casefold()
    assert calls == []
    assert result["sources"] == []


def test_unique_yearless_nordea_q3_cet1_shorthand_resolves_to_q3_2026(monkeypatch):
    plan = plan_question("Tell me about Nordea Q3 2026 CET1")
    assert plan.metric == "cet1_ratio"
    assert plan.target_periods == ("Q326",)

    captured = {}
    def one_cell(doc, metric, period, scope="group", country=None):
        captured.update(document=doc.document_id, metric=metric, period=period)
        return FinancialCell(metric, period, scope, Decimal("15.8"), "percent",
                             doc.filename, 7, "Common equity tier 1 capital ratio", "15.8")
    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(document_answering, "verify_cell",
                        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))

    result = document_answering.answer_with_selection(
        "Tell me about Nordea Q3 CET1", DocumentSelection(), DOCS
    )
    assert result["supported"] is True
    assert "15.8%" in result["answer"]
    assert captured == {"document": "nq3", "metric": "cet1_ratio", "period": "Q326"}
    assert result["sources"][0]["document_id"] == "nq3"


def test_ticker_shorthand_routes_to_quote_not_out_of_scope(monkeypatch):
    assert market_chat._intent("AAPL latest price") == "quote"
    assert market_chat._entities("AAPL latest price", "quote") == ["AAPL"]
    monkeypatch.setattr(
        market_chat, "_selected_or_search",
        lambda *args, **kwargs: (market_chat.Listing(symbol="AAPL", exchange="NASDAQ", entity_key="aapl"), []),
    )
    monkeypatch.setattr(market_chat.legacy.market_client, "quote", lambda *a, **k: {
        "symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD",
        "close": "342.56", "provider_datetime": "2026-11-20",
    })
    result = market_chat.process_chat(market_chat.ChatRequest(question="AAPL latest price"))
    assert result["kind"] == "market_quote"
    assert result["supported"] is True
    assert "342.56" in result["answer"]


def test_cross_bank_without_metric_asks_for_metric_not_manual_report_mode():
    result = resolve_documents("SEB vs Handelsbanken", DocumentSelection(), DOCS)
    assert result.documents == ()
    assert result.clarification is not None
    assert "metric" in result.clarification.casefold()
    assert "net profit" in result.clarification.casefold()


def test_periodless_cross_bank_net_profit_uses_unique_latest_common_reports(monkeypatch):
    result = resolve_documents("Net profit of SEB vs Handelsbanken", DocumentSelection(), DOCS)
    assert result.clarification is None
    assert [d.document_id for d in result.documents] == ["seb3", "hb3"]

    values = {"seb3": "7498", "hb3": "7353"}
    def one_cell(doc, metric, period, scope="group", country=None):
        assert metric == "net_profit"
        assert period == "Q326"
        return _cell(metric, period, values[doc.document_id], doc)
    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(document_answering, "verify_operands",
                        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))

    answer = document_answering.answer_with_selection(
        "Net profit of SEB vs Handelsbanken", DocumentSelection(), DOCS
    )
    assert answer["supported"] is True
    assert "Q3 2026" in answer["answer"]
    assert "SEB" in answer["answer"] and "SEK 7,498 million" in answer["answer"]
    assert "Handelsbanken" in answer["answer"] and "SEK 7,353 million" in answer["answer"]
    assert {s["document_id"] for s in answer["sources"]} == {"seb3", "hb3"}
    assert "difference" not in answer["answer"].casefold()


def test_cross_bank_without_unique_common_latest_period_clarifies():
    hb_q2 = DocumentRecord("hb2", "Handelsbanken Q2 2026.pdf", "Handelsbanken", 2026, 2, "interim_report", "SEK")
    reports = [SEB, hb_q2]
    result = resolve_documents("Net profit of SEB vs Handelsbanken", DocumentSelection(), reports)
    assert not result.documents
    assert result.clarification is not None
    assert "different periods" in result.clarification.casefold()


def test_named_unindexed_bank_is_not_misreported_as_a_generic_bank_ambiguity():
    result = document_answering.answer_with_selection(
        "Net profit of Finansbank", DocumentSelection(), DOCS
    )
    assert result["needs_clarification"] is True
    assert "Finansbank" in result["answer"]
    assert "Nordea" in result["answer"] and "SEB" in result["answer"]
    assert "report indexed for finansbank" in result["answer"].casefold()


def test_periodless_nordea_net_profit_still_uses_latest_and_previous_quarter(monkeypatch):
    def one_cell(doc, metric, period, scope="group", country=None):
        value = "1181" if period == "Q326" else "1232"
        return _cell(metric, period, value, doc)
    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(document_answering, "verify_cell",
                        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    monkeypatch.setattr(document_answering, "verify_operands",
                        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    result = document_answering.answer_with_selection(
        "What is Nordea's net profit?", DocumentSelection(), DOCS
    )
    assert result["supported"] is True
    assert "EUR 1,181 million" in result["answer"]
    assert "EUR 1,232 million" in result["answer"]


def test_periodless_question_without_quarter_is_not_blocked_by_new_quarter_guard():
    # The guard only acts on an explicit bare Qx. It must not turn a normal
    # periodless query into an unnecessary clarification.
    normalized, error = document_answering._infer_yearless_single_quarter(
        "What is Nordea's net profit?", DOCS
    )
    assert normalized == "What is Nordea's net profit?"
    assert error is None


def test_year_reply_is_applied_to_the_previous_target_quarter_not_report_selection(monkeypatch):
    captured = {}
    def fake_doc(question, selection):
        captured["question"] = question
        return {"answer": "The requested target period is being verified.",
                "supported": False, "sources": [], "needs_clarification": False}
    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="Q4 2026",
        context_question="Nordea Q4 net profit",
        context_prompt="You specified Q4 but not the year. Please specify the year.",
    ))
    assert result["kind"] == "document"
    assert captured["question"] == "Nordea Q4 2026 net profit"


def test_cross_bank_metric_clarification_can_be_answered_in_the_next_turn(monkeypatch):
    captured = {}
    def fake_doc(question, selection):
        captured["question"] = question
        return {"answer": "Side-by-side result", "supported": True,
                "sources": [], "needs_clarification": False}
    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    result = market_chat.process_chat(market_chat.ChatRequest(
        question="net profit",
        context_question="SEB vs Handelsbanken",
        context_prompt="Which metric would you like to compare at Group level?",
    ))
    assert result["kind"] == "document"
    assert captured["question"] == "SEB vs Handelsbanken for net profit"


def test_two_ticker_comparison_can_be_recognized_without_the_word_stock():
    assert market_chat._intent("Compare AAPL vs MSFT") == "compare"
    assert market_chat._entities("Compare AAPL vs MSFT", "compare") == ["AAPL", "MSFT"]


def test_explicit_quarter_year_formats_are_not_mistaken_for_yearless_quarters():
    for question in (
        "Tell me Nordea's CET1 ratio in Q3 2026",
        "Tell me Nordea's CET1 ratio in Q3/2026",
        "Tell me Nordea's CET1 ratio in Q3-2026",
        "Tell me Nordea's CET1 ratio in Q3 of 2026",
        "Tell me Nordea's CET1 ratio in 2026 Q3",
    ):
        normalized, error = document_answering._infer_yearless_single_quarter(question, DOCS)
        assert normalized == question
        assert error is None


def test_chat_routes_periodless_cross_bank_net_profit_to_verified_side_by_side_answer(monkeypatch):
    values = {"seb3": "7498", "hb3": "7353"}
    monkeypatch.setattr(
        document_answering, "_one_cell",
        lambda doc, metric, period, scope="group", country=None:
            _cell(metric, period, values[doc.document_id], doc),
    )
    monkeypatch.setattr(document_answering, "verify_operands",
                        lambda *a, **k: SimpleNamespace(accepted=True, reason="verified"))
    monkeypatch.setattr(
        market_chat.legacy, "_doc",
        lambda question, selection: document_answering.answer_with_selection(
            question, DocumentSelection(), DOCS
        ),
    )
    result = market_chat.process_chat(
        market_chat.ChatRequest(question="Net profit of SEB vs Handelsbanken")
    )
    assert result["kind"] == "document"
    assert result["supported"] is True
    assert "Q3 2026" in result["answer"]
    assert {s["document_id"] for s in result["sources"]} == {"seb3", "hb3"}


def test_greeting_uses_a_natural_chat_response_without_report_routing():
    result = market_chat.process_chat(market_chat.ChatRequest(question="Hello"))
    assert result["kind"] == "smalltalk"
    assert result["supported"] is True
    assert "Hello!" in result["answer"]
    assert "financial reports or stock market" in result["answer"].casefold()


def test_how_are_you_uses_a_natural_chat_response_without_report_routing():
    result = market_chat.process_chat(market_chat.ChatRequest(question="How are you"))
    assert result["kind"] == "smalltalk"
    assert result["supported"] is True
    assert "doing well" in result["answer"].casefold()
