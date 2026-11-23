"""Regressions for a synthetic nine-month report layout.

The fixture contains invented institution names and values. It preserves the
column ordering, unit placement, row labels and narrative sections needed to
test generic extraction behaviour without redistributing a real report.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from decimal import Decimal
from pathlib import Path

if importlib.util.find_spec("chromadb") is None or importlib.util.find_spec("sentence_transformers") is None:
    _stub = types.ModuleType("app.vector_store")
    _stub.get_collection = lambda: None
    sys.modules.setdefault("app.vector_store", _stub)

from app import document_tables
from app.company_overview import PageEvidence, build_company_overview

FIXTURE = Path(__file__).parent / "fixtures" / "danske_nine_months_layout_pages.json"


def _pages() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["pages"]


def _cell(monkeypatch, period):
    pages = _pages()
    monkeypatch.setattr(document_tables, "_get_pages", lambda _id: pages[:1])
    return document_tables.generic_cell("net_profit", period, "meridian-9m")


def test_quarter_column_is_read_from_its_own_position_not_the_ytd_column(monkeypatch):
    cell = _cell(monkeypatch, "Q326")
    assert cell is not None
    assert (cell.value, cell.unit, cell.page, cell.row_label) == (Decimal("5865"), "DKKm", 3, "Net profit")
    assert cell.raw_value == "5,865"


def test_other_quarter_columns_align_and_index_columns_are_skipped(monkeypatch):
    assert _cell(monkeypatch, "Q226").value == Decimal("5708")
    assert _cell(monkeypatch, "Q325").value == Decimal("5505")


def test_period_not_in_the_table_abstains(monkeypatch):
    assert _cell(monkeypatch, "Q427") is None


def test_net_profit_alias_never_reads_a_longer_net_profit_row():
    from app import layout_table_reader as reader
    lines = ["Net profit attributable to minority holders    1    2    3    4"]
    assert list(reader._row_candidates(lines, 0, 1, "net_profit", "income", 4)) == []


def test_plural_millions_is_recognised_as_a_unit():
    from app import layout_table_reader as reader
    assert reader._UNITS.search("(DKK millions)").group(1) == "DKK"


def test_business_units_overview_uses_unit_headings_and_page_citation():
    data = _pages()[1]
    result = build_company_overview(
        issuer="Meridian Bank", report_name="Meridian Bank - 9M 2026", document_id="meridian-9m",
        report_year=2026, report_quarter=3, pages=[PageEvidence(page=data["page"], text=data["text"])],
    )
    assert result["supported"] is True
    assert result["business_segments"] == [
        "Retail and Private Banking", "Business Banking", "Corporate and Institutional Banking",
        "Retirement and Protection", "Regional Services",
    ]
    assert "pension and insurance" in result["answer"]
    assert [s["page"] for s in result["sources"]] == [11]


def test_page_without_unit_prose_still_abstains():
    page = PageEvidence(page=2, text="Business units\nRetail Banking\n12\nBusiness Banking\n14\n")
    result = build_company_overview(
        issuer="Example Institution", report_name="r", document_id="d",
        report_year=2026, report_quarter=3, pages=[page],
    )
    assert result["supported"] is False
