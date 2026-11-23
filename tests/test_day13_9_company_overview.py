"""Updated Day 13.9 routing tests plus the Day 13.10 evidence-based overview."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import ast

import pytest

from app import market_chat
from app import document_registry


@dataclass(frozen=True)
class FakeDocument:
    document_id: str
    issuer: str
    report_year: int
    report_quarter: int
    report_type: str = "factbook"
    filename: str = "report.pdf"
    indexed_at: str = "2026-11-17T10:00:00+00:00"

    @property
    def display_name(self):
        return f"{self.issuer} — Q{self.report_quarter} {self.report_year} ({self.report_type})"

    def public_dict(self):
        return {**asdict(self), "display_name": self.display_name}


@pytest.fixture
def reports(monkeypatch):
    docs = [
        FakeDocument("n-q2", "Nordea", 2026, 2, filename="Nordea Q2 2026.pdf"),
        FakeDocument("n-q3", "Nordea", 2026, 3, filename="Nordea Q3 2026.pdf"),
        FakeDocument("s-q3", "SEB", 2026, 3, filename="SEB Q3 2026.pdf"),
        FakeDocument("h-q3", "Handelsbanken", 2026, 3, filename="Handelsbanken Q3 2026.pdf"),
    ]
    monkeypatch.setattr(document_registry, "list_documents", lambda: docs)
    return docs


def test_general_bank_overview_is_distinct_intent():
    assert market_chat._intent("Tell me about Nordea") == "overview"
    assert market_chat._intent("Tell me about Nordea Q3") == "overview"
    assert market_chat._intent("Tell me about Nordea company overview") == "overview"
    assert market_chat._intent("Give me an overview of SEB") == "overview"
    assert market_chat._intent("What does Handelsbanken do?") == "overview"


def test_specific_metric_stays_on_document_path():
    assert market_chat._intent("Tell me about Nordea's CET1 ratio in Q3 2026") == "document"


def test_stock_question_stays_on_market_path():
    assert market_chat._intent("Tell me about Nordea's stock price") == "quote"


def test_quarter_only_selects_latest_year_for_that_quarter(reports):
    doc, error = market_chat._overview_document("Tell me about Nordea Q3", market_chat.legacy.DocumentSelectionRequest())
    assert error is None and doc.document_id == "n-q3"


def test_q2_only_selects_q2_not_latest_q3(reports):
    doc, error = market_chat._overview_document("Tell me about Nordea Q2", market_chat.legacy.DocumentSelectionRequest())
    assert error is None and doc.document_id == "n-q2"


def test_no_period_selects_latest_indexed_edition(reports):
    doc, error = market_chat._overview_document("Tell me about Nordea", market_chat.legacy.DocumentSelectionRequest())
    assert error is None and doc.document_id == "n-q3"


def test_explicit_year_and_quarter_select_exact_edition(reports):
    doc, error = market_chat._overview_document("Tell me about Nordea Q2 2026", market_chat.legacy.DocumentSelectionRequest())
    assert error is None and doc.document_id == "n-q2"


def test_unindexed_period_does_not_fall_back_to_another_period(reports):
    doc, error = market_chat._overview_document("Tell me about Nordea Q4 2026", market_chat.legacy.DocumentSelectionRequest())
    assert doc is None and "couldn't find an indexed Q4 2026 report" in error


def test_unknown_issuer_asks_for_an_indexed_bank(reports):
    doc, error = market_chat._overview_document("Tell me about Danske Bank", market_chat.legacy.DocumentSelectionRequest())
    assert doc is None and "Which indexed bank" in error


def test_two_issuers_are_not_silently_merged(reports):
    doc, error = market_chat._overview_document("Tell me about Nordea and SEB", market_chat.legacy.DocumentSelectionRequest())
    assert doc is None and "one bank at a time" in error


def test_manual_selection_is_respected(reports):
    selection = market_chat.legacy.DocumentSelectionRequest(mode="manual", document_ids=["n-q2"])
    doc, error = market_chat._overview_document("Tell me about Nordea", selection)
    assert error is None and doc.document_id == "n-q2"


def test_manual_selection_missing_id_fails_closed(reports):
    selection = market_chat.legacy.DocumentSelectionRequest(mode="manual", document_ids=["missing"])
    doc, error = market_chat._overview_document("Tell me about Nordea", selection)
    assert doc is None and "manually selected report is unavailable" in error


def test_manual_selection_period_conflict_fails_closed(reports):
    selection = market_chat.legacy.DocumentSelectionRequest(mode="manual", document_ids=["n-q2"])
    doc, error = market_chat._overview_document("Tell me about Nordea Q3", selection)
    assert doc is None and "different report period" in error


def test_overview_uses_indexed_evidence_builder_not_metric_planner(reports, monkeypatch):
    captured = {}
    def fake_builder(document):
        captured["document"] = document
        return {"answer": "**Overview based on Nordea Q3 2026**\n\nFour business areas are evidenced.",
                "supported": True, "sources": [{"document": "Nordea Q3 2026.pdf", "page": 13}],
                "needs_clarification": False, "kind": "overview"}
    monkeypatch.setattr("app.company_overview.build_indexed_company_overview", fake_builder)
    out = market_chat._company_overview("Tell me about Nordea Q3", market_chat.legacy.DocumentSelectionRequest())
    assert out["kind"] == "overview" and out["supported"]
    assert captured["document"].document_id == "n-q3"
    assert "Overview based on Nordea — Q3 2026" in out["answer"]
    assert out["sources"][0]["page"] == 13


def test_overview_does_not_call_old_metric_answerer(reports, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("metric planner should not be called for an overview")
    monkeypatch.setattr(market_chat, "_report", fail)
    monkeypatch.setattr("app.company_overview.build_indexed_company_overview", lambda doc: {
        "kind": "overview", "answer": "Business areas are listed.", "supported": True,
        "sources": [{"document": doc.filename, "page": 2}], "needs_clarification": False,
    })
    out = market_chat._company_overview("Tell me about Nordea", market_chat.legacy.DocumentSelectionRequest())
    assert out["supported"]


def test_overview_without_indexed_business_detail_abstains(reports, monkeypatch):
    monkeypatch.setattr("app.company_overview.build_indexed_company_overview", lambda doc: {
        "kind": "overview", "answer": "Not enough segment evidence.", "supported": False,
        "sources": [], "needs_clarification": False,
    })
    out = market_chat._company_overview("Tell me about Nordea", market_chat.legacy.DocumentSelectionRequest())
    assert out["supported"] is False and out["sources"] == []


def test_yes_is_smalltalk_not_a_document_query():
    assert market_chat._intent("Yes") == "smalltalk"
    out = market_chat.process_chat(market_chat.ChatRequest(question="Yes"))
    assert out["kind"] == "smalltalk" and out["supported"]
    assert "Tell me about Nordea" in out["answer"]


def test_how_are_you_is_smalltalk():
    assert market_chat._intent("How are you") == "smalltalk"


def test_ui_adds_a_company_overview_example():
    root = Path(__file__).resolve().parents[1]
    source = (root / "streamlit_market_app.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert '"Tell me about Nordea"' in source


def test_legacy_marketing_filter_still_recognizes_footer_boilerplate():
    marketing = "For more than 200 years, we have proudly served as a trusted financial partner for a greater good. Our vision is to be the best-performing financial services group in the Nordics."
    assert market_chat._looks_like_marketing_boilerplate(marketing)
