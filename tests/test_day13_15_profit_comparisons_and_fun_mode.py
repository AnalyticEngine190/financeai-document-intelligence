from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

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
from app import document_answering

NQ2 = DocumentRecord("nq2", "Nordea Q2 2026.pdf", "Nordea", 2026, 2, "factbook", "EUR")
NQ3 = DocumentRecord("nq3", "Nordea Q3 2026.pdf", "Nordea", 2026, 3, "factbook", "EUR")
DOCS = [NQ2, NQ3]


def _cell(doc, metric, period, scope="group", country=None):
    values = {
        ("net_profit", "Q226"): Decimal("1232"),
        ("net_profit", "Q326"): Decimal("1181"),
        ("net_interest_income", "Q226"): Decimal("1779"),
        ("net_interest_income", "Q326"): Decimal("1799"),
        ("total_operating_income", "Q226"): Decimal("3032"),
        ("total_operating_income", "Q326"): Decimal("2968"),
        ("operating_profit", "Q226"): Decimal("1608"),
        ("operating_profit", "Q326"): Decimal("1541"),
        ("roe", "Q226"): Decimal("16.0"),
        ("roe", "Q326"): Decimal("15.3"),
    }
    unit = "percent" if metric == "roe" else "EURm"
    return document_answering.FinancialCell(
        metric=metric, period=period, scope=scope, value=values[(metric, period)],
        unit=unit, document=doc.filename, page=7,
        row_label=metric, raw_value=str(values[(metric, period)]),
    )


def _patch_cells(monkeypatch):
    monkeypatch.setattr(document_answering, "_one_cell", _cell)
    monkeypatch.setattr(
        document_answering, "verify_cell",
        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )
    monkeypatch.setattr(
        document_answering, "verify_operands",
        lambda *args, **kwargs: SimpleNamespace(accepted=True, reason="verified"),
    )


def test_plain_profit_and_net_profit_retrieve_both_quarter_pdfs(monkeypatch):
    _patch_cells(monkeypatch)
    for question in ("What is Nordea's profit?", "Tell me Nordea's net profit."):
        result = document_answering.answer_with_selection(question, DocumentSelection(), DOCS)
        assert result["supported"] is True
        assert "EUR 1,181 million" in result["answer"]
        assert "EUR 1,232 million" in result["answer"]
        assert "4.1% decline" in result["answer"]
        assert {s["document_id"] for s in result["sources"]} == {"nq2", "nq3"}
        assert {d["document_id"] for d in result["selected_documents"]} == {"nq2", "nq3"}


def test_q3_values_are_not_confused_with_q2_values(monkeypatch):
    _patch_cells(monkeypatch)
    for question, expected in (
        ("What was Nordea's total operating income in Q3 2026?", "EUR 2,968 million"),
        ("What was Nordea's operating profit in Q3 2026?", "EUR 1,541 million"),
        ("What was Nordea's return on equity in Q3 2026?", "15.3%"),
    ):
        result = document_answering.answer_with_selection(question, DocumentSelection(), DOCS)
        assert result["supported"] is True
        assert expected in result["answer"]
        assert result["sources"][0]["document_id"] == "nq3"


def test_net_profit_absolute_change_without_year_infers_unique_q2_q3_pair(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "How much did Nordea's net profit change between Q2 and Q3?",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "EUR 51 million decrease" in result["answer"]
    assert "EUR 1,232 million" in result["answer"]
    assert "EUR 1,181 million" in result["answer"]
    assert len(result["sources"]) == 2


def test_net_profit_percentage_decline_without_year(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "Calculate the percentage decline in Nordea's net profit from Q2 to Q3.",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "4.1% decline" in result["answer"]
    assert len(result["sources"]) == 2


def test_net_interest_income_comparison_without_year(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "Compare Nordea's net interest income in Q2 and Q3.",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "EUR 20 million increase" in result["answer"]
    assert len(result["sources"]) == 2


def test_operating_income_alias_resolves_to_total_operating_income(monkeypatch):
    _patch_cells(monkeypatch)
    result = document_answering.answer_with_selection(
        "Did Nordea's operating income increase or decrease between Q2 and Q3?",
        DocumentSelection(), DOCS,
    )
    assert result["supported"] is True
    assert "EUR 64 million decrease" in result["answer"]
    assert len(result["sources"]) == 2


def test_ambiguous_or_duplicate_quarter_pair_requests_year_instead_of_guessing():
    duplicate_q3 = DocumentRecord("nq3-copy", "Nordea Q3 duplicate.pdf", "Nordea", 2026, 3, "factbook", "EUR")
    for reports in ([NQ3], [NQ2, NQ3, duplicate_q3]):
        result = document_answering.answer_with_selection(
            "Compare Nordea's net profit between Q2 and Q3", DocumentSelection(), list(reports)
        )
        assert result["needs_clarification"] is True
        assert "unique" in result["answer"].lower()
        assert result["sources"] == []


def test_fun_mode_has_an_obvious_ui_control_and_works_from_both_entrypoints():
    root = Path(__file__).resolve().parents[1]
    source = (root / "streamlit_market_app.py").read_text(encoding="utf-8")
    wrapper = (root / "streamlit_app.py").read_text(encoding="utf-8")
    assert 'st.checkbox(' in source
    assert '"🎭 Fun mode"' in source
    assert 'key="fun_mode_enabled"' in source
    assert "render_fun_reaction" in source
    assert "choose_reaction" in source
    assert "artwork.is_file()" in source  # optional-art fallback is safe
    assert "runpy.run_path" in wrapper  # legacy streamlit_app.py launch works too
