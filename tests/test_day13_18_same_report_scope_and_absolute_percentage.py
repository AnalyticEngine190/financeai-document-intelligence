from __future__ import annotations

from decimal import Decimal
from types import ModuleType, SimpleNamespace
import sys

# These tests exercise parser/orchestration contracts without requiring the
# persistent Chroma database or a local sentence-transformer model.
if "chromadb" not in sys.modules:
    module = ModuleType("chromadb")
    module.PersistentClient = lambda **kwargs: None
    sys.modules["chromadb"] = module
if "sentence_transformers" not in sys.modules:
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = object
    sys.modules["sentence_transformers"] = module

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.question_parser import parse_question
from app.table_lookup import FinancialCell
from app import document_answering

SEB = DocumentRecord("seb-q3", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")


def _cell(metric: str, period: str, value: str, scope: str, page: int, label: str) -> FinancialCell:
    return FinancialCell(
        metric=metric, period=period, scope=scope, value=Decimal(value),
        unit="SEKm", document=SEB.filename, page=page,
        row_label=label, raw_value=value,
    )


def test_parser_recognizes_explicit_group_and_segment_as_two_scopes():
    intent = parse_question(
        "Compare SEB Baltic's operating profit with SEB Group's operating profit in Q3 2026."
    )
    assert intent.metric == "operating_profit"
    assert intent.periods == ("Q326",)
    assert intent.scopes == ("baltic", "group")
    assert intent.operation == "scope_difference"
    assert intent.uncertain is None


def test_same_report_segment_vs_group_comparison_uses_both_scoped_cells(monkeypatch):
    cells = {
        "baltic": _cell("operating_profit", "Q326", "1941", "baltic", 21, "Operating profit"),
        "group": _cell("operating_profit", "Q326", "9412", "group", 5, "Operating profit"),
    }
    requested = []

    def one_cell(doc, metric, period, scope="group", country=None):
        requested.append((doc.document_id, metric, period, scope))
        return cells.get(scope)

    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(
        document_answering, "verify_cell",
        lambda cell, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )

    result = document_answering.answer_with_selection(
        "Compare SEB Baltic's operating profit with SEB Group's operating profit in Q3 2026.",
        DocumentSelection(), [SEB],
    )

    assert result["supported"] is True
    assert "SEB Group had higher operating profit than SEB Baltic" in result["answer"]
    assert "SEK 7,471 million" in result["answer"]
    assert "SEK 1,941 million" in result["answer"]
    assert "SEK 9,412 million" in result["answer"]
    assert requested == [
        ("seb-q3", "operating_profit", "Q326", "baltic"),
        ("seb-q3", "operating_profit", "Q326", "group"),
    ]
    assert {source["page"] for source in result["sources"]} == {5, 21}


def test_missing_segment_cell_does_not_fall_back_to_group_for_scope_comparison(monkeypatch):
    group = _cell("operating_profit", "Q326", "9412", "group", 5, "Operating profit")
    calls = []

    def one_cell(doc, metric, period, scope="group", country=None):
        calls.append(scope)
        return None if scope == "baltic" else group

    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    result = document_answering.answer_with_selection(
        "Compare SEB Baltic's operating profit with SEB Group's operating profit in Q3 2026.",
        DocumentSelection(), [SEB],
    )

    assert result["supported"] is False
    assert result["sources"] == []
    assert calls == ["baltic"]


def test_same_report_percentage_request_with_both_returns_absolute_and_percentage(monkeypatch):
    cells = {
        ("net_profit", "Q226"): _cell("net_profit", "Q226", "8664", "group", 5, "NET PROFIT"),
        ("net_profit", "Q326"): _cell("net_profit", "Q326", "7498", "group", 5, "NET PROFIT"),
    }
    monkeypatch.setattr(
        document_answering, "generic_cell",
        lambda metric, period, document_id, **kwargs: cells.get((metric, period)),
    )

    result = document_answering.answer_with_selection(
        "Compare SEB's net profit in Q2 and Q3 2026. Calculate both the absolute and percentage change.",
        DocumentSelection(), [SEB],
    )

    assert result["supported"] is True
    assert "SEK 1,166 million decrease" in result["answer"]
    assert "13.5% decline" in result["answer"]
    assert "Q226" in result["answer"] and "Q326" in result["answer"]
    assert len(result["sources"]) == 1


def test_percentage_only_question_keeps_percentage_only_result(monkeypatch):
    cells = {
        ("net_profit", "Q226"): _cell("net_profit", "Q226", "8664", "group", 5, "NET PROFIT"),
        ("net_profit", "Q326"): _cell("net_profit", "Q326", "7498", "group", 5, "NET PROFIT"),
    }
    monkeypatch.setattr(
        document_answering, "generic_cell",
        lambda metric, period, document_id, **kwargs: cells.get((metric, period)),
    )

    result = document_answering.answer_with_selection(
        "Calculate the percentage decline in SEB's net profit from Q2 to Q3 2026.",
        DocumentSelection(), [SEB],
    )

    assert result["supported"] is True
    assert "13.5% decline" in result["answer"]
    assert "SEK 1,166 million decrease" not in result["answer"]


def test_three_bank_automatic_comparison_explains_two_bank_limit():
    from app.document_resolver import resolve_documents

    nordea = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")
    handels = DocumentRecord("hbq3", "Handelsbanken Q3 2026.pdf", "Handelsbanken", 2026, 3, "interim_report", "SEK")
    result = resolve_documents(
        "Which had higher operating profit in Q3 2026, SEB or Handelsbanken or Nordea, and by how much?",
        DocumentSelection(), [SEB, handels, nordea],
    )
    assert result.documents == ()
    assert result.clarification is not None
    assert "two banks at a time" in result.clarification
    assert all(name in result.clarification for name in ("SEB", "Handelsbanken", "Nordea"))


def test_seb_baltic_vs_group_reads_real_fixture_rows_and_pages(monkeypatch):
    import json
    from pathlib import Path
    import app.document_tables as document_tables

    fixture = Path(__file__).parent / "fixtures" / "seb_scope_comparison_pages.json"
    pages = json.loads(fixture.read_text(encoding="utf-8"))["pages"]
    monkeypatch.setattr(document_tables, "_get_pages", lambda document_id: pages)

    result = document_answering.answer_with_selection(
        "Compare SEB Baltic's operating profit with SEB Group's operating profit in Q3 2026.",
        DocumentSelection(), [SEB],
    )

    assert result["supported"] is True
    assert "SEB Group had higher operating profit than SEB Baltic" in result["answer"]
    assert "SEK 7,471 million" in result["answer"]
    assert {source["page"] for source in result["sources"]} == {5, 21}


def test_chat_routes_scope_comparison_to_document_answerer(monkeypatch):
    from app import market_chat

    captured = {}

    def fake_doc(question, selection):
        captured["question"] = question
        return document_answering.answer_with_selection(question, DocumentSelection(), [SEB])

    monkeypatch.setattr(market_chat.legacy, "_doc", fake_doc)
    monkeypatch.setattr(
        document_answering, "_one_cell",
        lambda doc, metric, period, scope="group", country=None: (
            _cell(metric, period, "1941", scope, 21, "Operating profit")
            if scope == "baltic" else _cell(metric, period, "9412", scope, 5, "Operating profit")
        ),
    )
    monkeypatch.setattr(
        document_answering, "verify_cell",
        lambda cell, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )

    question = "Compare SEB Baltic's operating profit with SEB Group's operating profit in Q3 2026."
    result = market_chat.process_chat(market_chat.ChatRequest(question=question))
    assert result["kind"] == "document"
    assert result["supported"] is True
    assert "SEK 7,471 million" in result["answer"]
    assert captured["question"] == question


def test_separate_nordea_q2_q3_reports_return_absolute_and_percentage_change(monkeypatch):
    from app.document_resolver import DocumentRecord

    nq2 = DocumentRecord("nq2", "Nordea Q2 2026.pdf", "Nordea", 2026, 2, "factbook", "EUR")
    nq3 = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")

    def one_cell(doc, metric, period, scope="group", country=None):
        value, page = ("1232", 7) if period == "Q226" else ("1181", 7)
        return FinancialCell(
            metric=metric, period=period, scope=scope, value=Decimal(value),
            unit="EURm", document=doc.filename, page=page,
            row_label="Net profit for the period", raw_value=value,
        )

    monkeypatch.setattr(document_answering, "_one_cell", one_cell)
    monkeypatch.setattr(
        document_answering, "verify_operands",
        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )

    result = document_answering.answer_with_selection(
        "Calculate both the absolute and percentage change in Nordea's net profit from Q2 to Q3 2026.",
        DocumentSelection(), [nq2, nq3],
    )

    assert result["supported"] is True
    assert "EUR 51 million decrease" in result["answer"]
    assert "4.1% decline" in result["answer"]
    assert {source["document_id"] for source in result["sources"]} == {"nq2", "nq3"}
