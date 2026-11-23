"""Focused regressions for synthetic mixed-period tables and activity sections.

The fixture uses fictional names and figures while retaining the source-layout
features required by these generic parser tests.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from decimal import Decimal
from pathlib import Path

if importlib.util.find_spec("chromadb") is None or importlib.util.find_spec("sentence_transformers") is None:
    vector_store_stub = types.ModuleType("app.vector_store")
    vector_store_stub.get_collection = lambda: None
    sys.modules.setdefault("app.vector_store", vector_store_stub)

import pytest

from app.document_registry import DocumentRecord
from app.company_overview import PageEvidence, build_company_overview
from app import document_tables
from app.document_answering import answer_with_selection

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "jyske_q3_2026_layout_pages.json"


def _fixture_pages() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["pages"]


def test_mixed_ytd_index_quarter_header_reads_q3_cell_not_ytd(monkeypatch):
    pages = _fixture_pages()
    monkeypatch.setattr(document_tables, "_get_pages", lambda _document_id: pages[:1])

    cell = document_tables.generic_cell("net_profit", "Q326", "meridian-q3")

    assert cell is not None
    assert (cell.value, cell.unit, cell.page) == (Decimal("1364"), "DKKm", 7)
    assert cell.row_label == "Net profit for the period"
    assert cell.raw_value == "1,364"


def test_reported_currency_overrides_conflicting_registry_currency(monkeypatch):
    pages = _fixture_pages()
    monkeypatch.setattr(document_tables, "_get_pages", lambda _document_id: pages[:1])
    document = DocumentRecord(
        document_id="meridian-q3", filename="Meridian Bank Interim Financial Report Q1-Q3 2026.pdf",
        issuer="Meridian Bank", report_year=2026, report_quarter=3,
        report_type="interim_report", currency="EUR",
    )

    explicit = answer_with_selection(
        "What was Meridian Bank's net profit for the period in Q3 2026, in DKK million?",
        documents=[document],
    )
    periodless = answer_with_selection("Tell me net profit of Meridian Bank", documents=[document])
    mixed_phrase = answer_with_selection("Tell me about Meridian Bank net profit", documents=[document])

    for result in (explicit, periodless, mixed_phrase):
        assert result["supported"] is True
        assert "DKK 1,364 million" in result["answer"]
        assert result["sources"][0]["page"] == 7
        assert result["sources"][0]["document_id"] == "meridian-q3"


def test_business_areas_overview_uses_source_sections_and_page_citation():
    page_data = _fixture_pages()[1]
    page = PageEvidence(page=page_data["page"], text=page_data["text"])

    result = build_company_overview(
        issuer="Meridian Bank", report_name="Meridian Bank - Q3 2026 (interim_report)",
        document_id="meridian-q3", report_year=2026, report_quarter=3, pages=[page],
    )

    assert result["supported"] is True
    assert result["business_areas"] == [
        "Banking activities", "Property finance activities", "Leasing activities"
    ]
    assert "advisory services" in result["answer"]
    assert "Meridian Mortgage Services" in result["answer"]
    assert "operating equipment" in result["answer"]
    assert result["sources"] == [{
        "document": "Meridian Bank - Q3 2026 (interim_report)",
        "report": "Meridian Bank - Q3 2026 (interim_report)",
        "document_id": "meridian-q3", "page": 25,
    }]
