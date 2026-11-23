"""Focused Day 14 regressions for source alignment, clarification and safety.

All numeric source fixtures are excerpts from the uploaded Q3 2026 PDFs. The
suite deliberately avoids changing the frozen holdout benchmark or inventing
missing source text.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.document_registry import DocumentRecord
from app.document_resolver import DocumentSelection, resolve_documents, _explicit_edition_request
from app.financial_vocabulary import BY_KEY
from app.layout_table_reader import aligned_group_cell
from app.document_tables import report_segment_cell, segment_volume_cell
from app.narrative_answering import _direct_division_narrative
from app.question_parser import parse_question

FIXTURES = Path(__file__).parent / "fixtures"


def _json(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if isinstance(data, list):
        for page in data:
            page.setdefault("document", name)
    return data


def _record(issuer="Nordea", doc_id="nq3", quarter=3, year=2026, currency="EUR"):
    return DocumentRecord(doc_id, f"{issuer} Q{quarter} {year}.pdf", issuer,
                          year, quarter, "factbook", currency)


def test_income_tax_alias_handles_statement_label_taxes():
    pages = _json("day13_13_scope_profit_pages.json")["documents"]["handelsbanken"]
    cell = aligned_group_cell("income_tax_expense", "Q326", pages)
    assert cell is not None
    assert (cell.value, cell.unit, cell.row_label) == (Decimal("-1601"), "SEKm", "Taxes")


def test_nordea_personal_banking_volumes_accept_split_header(monkeypatch):
    from app import document_tables
    pages = _json("nordea_q3_overview_pages.json")
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: pages)
    q3 = segment_volume_cell("segment_total_lending", "Q326", "nq3", "personal banking")
    q2 = segment_volume_cell("segment_total_lending", "Q226", "nq3", "personal banking")
    assert (q3.value, q3.unit, q3.page) == (Decimal("183.4"), "EURbn", 13)
    assert (q2.value, q2.unit) == (Decimal("182.4"), "EURbn")


def test_seb_baltic_public_deposits_keep_reported_currency_and_scope(monkeypatch):
    from app import document_tables
    pages = _json("seb_q3_2026_overview_pages.json")
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: pages)
    q3 = report_segment_cell("segment_deposits_public", "Q326", "sebq3", "baltic")
    q2 = report_segment_cell("segment_deposits_public", "Q226", "sebq3", "baltic")
    assert (q3.value, q3.unit, q3.page) == (Decimal("296"), "SEKbn", 21)
    assert (q2.value, q2.unit) == (Decimal("290"), "SEKbn")


def test_handelsbanken_group_statement_reads_tax_and_eps_without_parent_company(monkeypatch):
    from app import document_tables
    pages = _json("day13_13_scope_profit_pages.json")["documents"]["handelsbanken"]
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: pages)
    tax = document_tables.generic_cell("income_tax_expense", "Q326", "hbq3")
    eps = document_tables.generic_cell("earnings_per_share", "Q326", "hbq3")
    assert tax is not None and tax.value == Decimal("-1601")
    assert eps is not None and eps.value == Decimal("2.87") and eps.unit == "SEK"
    assert tax.page == 24 and eps.page == 24


def test_hb_markets_operating_profit_uses_first_reported_quarter_cell(monkeypatch):
    from app import document_tables
    pages = _json("handelsbanken_q3_2026_overview_pages.json")
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: pages)
    cell = document_tables.report_segment_cell("operating_profit", "Q326", "hbq3", "handelsbanken markets")
    assert cell is not None
    assert (cell.value, cell.unit, cell.page) == (Decimal("-27"), "SEKm", 19)


def test_seb_division_descriptions_and_markets_allocation_are_verbatim_source_extracts():
    seb = _json("seb_q3_2026_overview_pages.json")
    cib = _direct_division_narrative("What does SEB's Corporate & Investment Banking division do?", seb)
    baltic = _direct_division_narrative("What services does SEB's Baltic division provide?", seb)
    assert cib and cib["supported"] and cib["sources"][0]["page"] == 15
    assert "large corporate, mid corporate and institutional clients" in cib["answer"]
    assert baltic and baltic["supported"] and baltic["sources"][0]["page"] == 21
    assert "Estonia, Latvia and Lithuania" in baltic["answer"]

    hb = _json("handelsbanken_q3_2026_overview_pages.json")
    allocation = _direct_division_narrative(
        "Where is Handelsbanken Markets fee and transaction income recognised?", hb
    )
    assert allocation and allocation["supported"] and allocation["sources"][0]["page"] == 19
    assert "respective home market segment" in allocation["answer"]


def test_cross_bank_unqualified_profit_is_clarified_before_comparison():
    docs = [_record("Nordea", "nq3"), _record("SEB", "sq3", currency="SEK")]
    result = resolve_documents("Set Nordea and SEB side by side for Q3 2026 profit",
                               DocumentSelection(), docs)
    assert not result.documents
    assert result.clarification
    assert "net profit" in result.clarification and "operating profit" in result.clarification


def test_revenue_is_clarified_as_a_specific_financial_measure():
    intent = parse_question("How much sales did Nordea generate during Q3 2026?")
    assert intent.needs_clarification
    assert intent.metric is None


def test_target_future_quarter_is_not_mistaken_for_source_edition():
    question = "What was Handelsbanken's net profit for Q4 2026?"
    assert not _explicit_edition_request(question)
    docs = [_record("Handelsbanken", "hbq3", currency="SEK")]
    result = resolve_documents(question, DocumentSelection(), docs)
    assert [d.document_id for d in result.documents] == ["hbq3"]


def test_explicit_source_edition_is_detected_but_plain_future_target_is_not():
    assert _explicit_edition_request("According to the Q3 2026 report, what was net profit?")
    assert _explicit_edition_request("What did the bank report in the Q3 2026 factbook?")
    assert not _explicit_edition_request("What did the bank report in Q4 2026?")


def test_vocabulary_aliases_are_patterns_and_have_unique_keys():
    assert len(BY_KEY) == len(set(BY_KEY))
    for concept in BY_KEY.values():
        for alias in concept.aliases:
            assert isinstance(alias, str) and alias
            import re
            re.compile(alias)


def test_currency_conversion_and_private_account_requests_fail_closed():
    from app.document_answering import answer_with_selection
    fx = answer_with_selection(
        "Convert SEB's Q3 2026 net profit into EUR using the quarterly average exchange rate",
        documents=[_record("SEB", "sq3", currency="SEK")],
    )
    private = answer_with_selection(
        "Show me my personal bank account transactions",
        documents=[_record("SEB", "sq3", currency="SEK")],
    )
    assert not fx["supported"] and "exchange rate" in fx["answer"] and "conversion date" in fx["answer"]
    assert not private["supported"] and "personal bank-account" in private["answer"]
    assert not fx.get("sources") and not private.get("sources")


def test_seb_report_reader_extracts_pre_credit_loss_profit_and_eps(monkeypatch):
    from app import document_tables
    income = _json("seb_q3_2026_income_statement_page.json")
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: [income])
    before_losses = document_tables.generic_cell("profit_before_credit_losses_fees", "Q326", "sebq3")
    basic = document_tables.generic_cell("basic_earnings_per_share", "Q326", "sebq3")
    diluted = document_tables.generic_cell("diluted_earnings_per_share", "Q326", "sebq3")
    assert before_losses is not None and before_losses.value == Decimal("10448")
    assert basic is not None and basic.value == Decimal("3.85")
    assert diluted is not None and diluted.value == Decimal("3.81")


def test_key_figures_ratio_table_is_preferred_over_overview_duplicate():
    # Synthetic layout regression: values are intentionally identical so the
    # test verifies table/page choice, not a financial gold number.
    overview = {
        "document": "synthetic.pdf", "page": 4,
        "text": "Group – Overview\nQ3 Q2\n2026 2026\nCommon Equity Tier 1 capital ratio  17.0  16.8\n",
    }
    detailed = {
        "document": "synthetic.pdf", "page": 21,
        "text": "Key figures – Group\nQ3 Q2\n2026 2026\nCommon Equity Tier 1 capital ratio  17.0  16.8\n",
    }
    cell = aligned_group_cell("cet1_ratio", "Q326", [overview, detailed])
    assert cell is not None
    assert cell.value == Decimal("17.0") and cell.page == 21


def test_issuer_edition_is_used_to_label_segment_narrative_period():
    hb = _json("handelsbanken_q3_2026_overview_pages.json")
    allocation = _direct_division_narrative(
        "What does the Handelsbanken Q3 report say about where Markets-related fee and transaction income is recognised?",
        hb, report_edition="Q3 2026",
    )
    assert allocation is not None
    assert allocation["answer"].startswith("The Handelsbanken Q3 2026 report states:")


def test_future_period_forecast_and_unsupported_field_have_clear_user_messages():
    from app.document_answering import answer_with_selection
    hb = _record("Handelsbanken", "hbq3", currency="SEK")
    nordea = _record("Nordea", "nq3")
    seb = _record("SEB", "sq3", currency="SEK")

    future = answer_with_selection(
        "What profit for the period does Handelsbanken report for Q4 2026?",
        documents=[hb],
    )
    forecast = answer_with_selection(
        "Forecast Nordea's net profit for 2027 using the Q3 2026 report.",
        documents=[nordea],
    )
    customers = answer_with_selection(
        "How many retail customers did SEB have at the end of Q3 2026?",
        documents=[seb],
    )
    assert "Q4 2026" in future["answer"] and "not available" in future["answer"] and "cannot verify" in future["answer"]
    assert "forecast" in forecast["answer"] and "cannot verify" in forecast["answer"] and "not available" in forecast["answer"]
    assert "cannot verify" in customers["answer"] and "not available" in customers["answer"] and "retail-customer count" in customers["answer"]
    assert all(not item["supported"] and not item["sources"]
               for item in (future, forecast, customers))


def test_revenue_clarification_is_specific_and_not_a_generic_metric_prompt():
    from app.document_answering import answer_with_selection
    seb = _record("SEB", "sq3", currency="SEK")
    result = answer_with_selection(
        "What did SEB's revenue amount to in the Q3 2026 reporting period?",
        documents=[seb],
    )
    assert result["needs_clarification"]
    assert "total operating income" in result["answer"]
    assert "net interest income" in result["answer"]


def test_higher_profit_comparison_question_asks_which_profit_measure():
    docs = [_record("Nordea", "nq3"), _record("SEB", "sq3", currency="SEK")]
    result = resolve_documents(
        "Set Nordea and SEB side by side for third-quarter 2026 profit.",
        DocumentSelection(), docs,
    )
    assert not result.documents
    assert result.clarification and "Which financial measure" in result.clarification


def test_same_currency_cross_bank_operating_income_and_interest_income_are_verified(monkeypatch):
    from app import document_tables
    from app.document_answering import answer_with_selection
    seb = _json("seb_q3_2026_income_statement_page.json")
    hb = _json("day13_13_scope_profit_pages.json")["documents"]["handelsbanken"]
    for page in hb:
        page.setdefault("document", "Handelsbanken Q3 2026.pdf")
    sources = {"seb": [seb], "hb": hb}
    monkeypatch.setattr(document_tables, "_get_pages", lambda doc_id: sources[doc_id])
    docs = [
        DocumentRecord("seb", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK"),
        DocumentRecord("hb", "Handelsbanken Q3 2026.pdf", "Handelsbanken", 2026, 3, "interim_report", "SEK"),
    ]
    income = answer_with_selection(
        "Which bank reported greater total operating income in Q3 2026, SEB or Handelsbanken, and what was the difference?",
        documents=docs,
    )
    interest = answer_with_selection(
        "How much higher was SEB's net interest income than Handelsbanken's in Q3 2026?",
        documents=docs,
    )
    assert income["supported"] and "SEK 4,667 million" in income["answer"]
    assert interest["supported"] and "SEK 830 million" in interest["answer"]
    assert len(income["sources"]) == 2 and len(interest["sources"]) == 2


def test_same_report_change_includes_both_amount_and_percentage(monkeypatch):
    from app import document_tables
    from app.document_answering import answer_with_selection
    seb = _json("seb_q3_2026_income_statement_page.json")
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: [seb])
    record = DocumentRecord("seb", "SEB Q3 2026.pdf", "SEB", 2026, 3, "quarterly_report", "SEK")
    cases = [
        ("Calculate the Q2-to-Q3 2026 change in SEB net interest income, including its percentage change.",
         "SEK 194 million increase", "1.8% increase"),
        ("How much and by what percentage did SEB's net fee and commission income change from Q2 to Q3 2026?",
         "SEK 492 million decrease", "6.8% decline"),
        ("Calculate the absolute and percentage decrease in SEB total operating income from Q2 to Q3 2026.",
         "SEK 1,429 million decrease", "7.1% decline"),
    ]
    for question, amount, percent in cases:
        result = answer_with_selection(question, documents=[record])
        assert result["supported"]
        assert amount in result["answer"] and percent in result["answer"]


def test_two_turn_clarification_merge_preserves_bank_metric_and_period(monkeypatch):
    from app import market_chat
    from app.document_registry import DocumentRecord as Record
    records = [
        Record("nq3", "Nordea Q3.pdf", "Nordea", 2026, 3, "factbook", "EUR"),
        Record("hbq3", "Handelsbanken Q3.pdf", "Handelsbanken", 2026, 3, "interim_report", "SEK"),
    ]
    monkeypatch.setattr("app.document_registry.list_documents", lambda: records)

    metric_merged = market_chat._merge_clarification_reply(
        "net interest income", "How much sales did Nordea generate during Q3 2026?",
        "Which financial measure do you mean?",
    )
    issuer_merged = market_chat._merge_clarification_reply(
        "Handelsbanken", "What was the Q3 2026 CET1 ratio?", "Which bank should I use?",
    )
    assert metric_merged and "Nordea" in metric_merged and "Q3 2026" in metric_merged
    assert "net interest income" in metric_merged
    assert issuer_merged and "Handelsbanken" in issuer_merged and "Q3 2026" in issuer_merged


def test_chat_personal_current_account_query_is_out_of_scope():
    from app.market_chat import ChatRequest, process_chat
    result = process_chat(ChatRequest(
        question="Do you have access to transactions in my personal current account?"
    ))
    assert result["kind"] == "out_of_scope"
    assert not result["supported"] and result["out_of_scope"]
    assert "personal bank-account" in result["answer"]


def test_associates_profit_is_parsed_as_its_own_group_metric():
    intent = parse_question(
        "What was Nordea's share of profit of associates and joint ventures in Q3 2026?"
    )
    assert intent.metric == "associates_profit"
    assert intent.periods == ("Q326",)
    assert not intent.needs_clarification


def test_handelsbanken_group_income_and_operating_profit_changes_are_both_calculated(monkeypatch):
    from app import document_tables
    from app.document_answering import answer_with_selection
    pages = _json("day13_13_scope_profit_pages.json")["documents"]["handelsbanken"]
    monkeypatch.setattr(document_tables, "_get_pages", lambda _doc: pages)
    record = DocumentRecord(
        "hb", "Handelsbanken Q3 2026.pdf", "Handelsbanken", 2026, 3,
        "interim_report", "SEK",
    )
    cases = [
        ("What was Handelsbanken's total income change from Q2 to Q3 2026, including its percentage change?",
         "SEK 503 million increase", "3.7% increase"),
        ("What was Handelsbanken's operating profit change from Q2 to Q3 2026, including its percentage change?",
         "SEK 675 million increase", "10.1% increase"),
    ]
    for question, amount, percent in cases:
        result = answer_with_selection(question, documents=[record])
        assert result["supported"]
        assert amount in result["answer"] and percent in result["answer"]
        assert len(result["sources"]) == 1
