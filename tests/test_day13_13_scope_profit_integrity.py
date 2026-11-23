from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection
from app.question_parser import parse_question
import app.document_tables as document_tables
import app.document_answering as document_answering
from app.company_overview import PageEvidence, build_company_overview

FIXTURE = Path(__file__).parent / "fixtures" / "day13_13_scope_profit_pages.json"


@pytest.fixture(scope="module")
def report_pages():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["documents"]


def _record(issuer: str, document_id: str, report_type: str, currency: str = "SEK"):
    return DocumentRecord(
        document_id=document_id,
        filename=f"{issuer}_Q3_2026.pdf",
        issuer=issuer,
        report_year=2026,
        report_quarter=3,
        report_type=report_type,
        currency=currency,
    )


def _patch_pages(monkeypatch, report_pages):
    by_id = {"handels": "handelsbanken", "seb": "seb"}
    monkeypatch.setattr(
        document_tables,
        "_get_pages",
        lambda document_id: report_pages[by_id[document_id]],
    )


def test_seb_baltic_scope_is_parsed_as_a_segment_not_group():
    intent = parse_question("What was SEB Baltic division's operating profit in Q3 2026?")
    assert intent.metric == "operating_profit"
    assert intent.periods == ("Q326",)
    assert intent.scopes == ("baltic",)
    assert intent.country is None


def test_seb_baltic_operating_profit_uses_its_own_division_page(monkeypatch, report_pages):
    _patch_pages(monkeypatch, report_pages)
    seb = _record("SEB", "seb", "quarterly_report")

    result = document_answering.answer_with_selection(
        "What was SEB Baltic division's operating profit in Q3 2026?",
        DocumentSelection(), [seb],
    )

    assert result["supported"] is True
    assert result["answer"] == "SEK 1,941 million"
    assert [source["page"] for source in result["sources"]] == [21]


def test_handelsbanken_group_profit_for_period_ignores_parent_company_statement(
    monkeypatch, report_pages
):
    _patch_pages(monkeypatch, report_pages)
    hb = _record("Handelsbanken", "handels", "interim_report")

    result = document_answering.answer_with_selection(
        "What was Handelsbanken's profit for the period in Q3 2026?",
        DocumentSelection(), [hb],
    )

    assert result["supported"] is True
    assert result["answer"] == "SEK 5,753 million"
    assert [source["page"] for source in result["sources"]] == [24]
    assert 57 not in [source["page"] for source in result["sources"]]


def test_requested_segment_never_falls_back_to_group_cell(monkeypatch):
    hb = _record("Handelsbanken", "handels", "interim_report")
    called = {"group": False}

    monkeypatch.setattr(document_tables, "report_segment_cell", lambda *a, **k: None)

    def fake_group_cell(*args, **kwargs):
        called["group"] = True
        return object()

    monkeypatch.setattr(document_answering, "generic_cell", fake_group_cell)
    result = document_answering._one_cell(
        hb, "operating_profit", "Q326", "baltic"
    )

    assert result is None
    assert called["group"] is False


def test_handelsbanken_five_segment_question_routes_to_overview(monkeypatch, report_pages):
    import app.market_chat as market_chat

    question = "What are Handelsbanken's five business segments?"
    assert market_chat._intent(question) == "overview"

    pages = [PageEvidence(page=p["page"], text=p["text"])
             for p in report_pages["handelsbanken"] if p["page"] in {10, 11, 13, 15, 17, 19}]
    result = build_company_overview(
        "Handelsbanken", "Handelsbanken — Q3 2026 (interim_report)",
        "handels", 2026, 3, pages,
    )

    assert result["supported"] is True
    assert result["business_segments"] == [
        "Handelsbanken Sweden", "Handelsbanken UK", "Handelsbanken Norway",
        "Handelsbanken the Netherlands", "Handelsbanken Markets",
    ]
    assert "five business segments" in result["answer"].casefold()


def test_handelsbanken_segment_list_chat_route_builds_overview(monkeypatch, report_pages):
    import app.market_chat as market_chat
    import app.company_overview as company_overview
    import app.market_routes as market_routes

    hb = _record("Handelsbanken", "handels", "interim_report")
    pages = [PageEvidence(page=p["page"], text=p["text"])
             for p in report_pages["handelsbanken"] if p["page"] in {10, 11, 13, 15, 17, 19}]
    built = build_company_overview("Handelsbanken", hb.display_name, "handels", 2026, 3, pages)
    monkeypatch.setattr(market_chat, "_overview_document", lambda *a, **k: (hb, None))
    monkeypatch.setattr(company_overview, "build_indexed_company_overview", lambda _doc: built)

    question = "What are Handelsbanken's five business segments?"
    result = market_chat._company_overview(
        question, market_routes.DocumentSelectionRequest()
    )

    assert result["supported"] is True
    assert "Handelsbanken Sweden" in result["answer"]
    assert "Handelsbanken Markets" in result["answer"]
    assert len(result["business_segments"]) == 5


def test_named_seb_division_followup_returns_only_that_division(monkeypatch, report_pages):
    import app.market_chat as market_chat
    import app.company_overview as company_overview
    import app.market_routes as market_routes

    seb = _record("SEB", "seb", "quarterly_report")
    pages = [PageEvidence(page=p["page"], text=p["text"])
             for p in report_pages["seb"]]
    built = build_company_overview(
        "SEB", seb.display_name, "seb", 2026, 3, pages,
    )
    monkeypatch.setattr(market_chat, "_overview_document", lambda *a, **k: (seb, None))
    monkeypatch.setattr(company_overview, "build_indexed_company_overview", lambda _doc: built)

    question = "What does SEB's Corporate & Investment Banking division do?"
    assert market_chat._intent(question) == "overview"
    result = market_chat._company_overview(
        question, market_routes.DocumentSelectionRequest()
    )

    assert result["supported"] is True
    assert result["requested_business_segment"] == "Corporate & Investment Banking"
    assert "large corporate, mid corporate and institutional clients" in result["answer"].casefold()
    assert "Business & Retail Banking:" not in result["answer"]
    assert [source["page"] for source in result["sources"]] == [14, 15]
