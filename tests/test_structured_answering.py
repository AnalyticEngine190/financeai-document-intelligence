"""New answering route never needs Qwen for a verified calculation."""
from decimal import Decimal
from unittest.mock import patch

from app.answering import answer_question
from app.table_lookup import FinancialCell


def _c(value, period):
    return FinancialCell(
        metric="net_interest_income", period=period, scope="group",
        value=Decimal(value), unit="EURm", document="fiction.pdf", page=7,
        row_label="Net interest income", raw_value=value,
    )


def test_calculation_uses_two_grounded_cells_without_ollama():
    with patch("app.answering.resolve_intent_cells", return_value=[_c("100", "Q226"), _c("120", "Q326")]):
        with patch("app.answering._call_ollama") as llm:
            r = answer_question("How much did net interest income rise from Q2 2026 to Q3 2026?", "doc")
    assert r["supported"] is True
    assert r["answer"].startswith("EUR 20 million")
    assert r["sources"] == [{"document": "fiction.pdf", "page": 7}]
    llm.assert_not_called()


def test_missing_comparison_operand_must_abstain():
    with patch("app.answering.resolve_intent_cells", return_value=None):
        with patch("app.answering._call_ollama") as llm:
            r = answer_question("How much did net interest income rise from Q2 2026 to Q3 2026?", "doc")
    assert r["supported"] is False
    llm.assert_not_called()


def test_ambiguous_income_triggers_clarification():
    r = answer_question("How much income did Nordea make in Q3 2026?", "doc")
    assert r["needs_clarification"] is True and r["supported"] is False


def test_legacy_scalar_flow_still_reaches_mocked_ollama():
    with patch("app.answering.hybrid_search", return_value=[
        {"document": "fiction.pdf", "page": 53, "chunk_index": 0,
         "text": "Liquidity coverage ratio Q3 2026 Combined USD EUR 150%"},
    ]):
        with patch("app.answering.lookup_fact", return_value=(None, False)):
            with patch("app.answering._call_ollama", return_value={"answer": "150%", "citation_ids": [1], "supported": True}) as model:
                r = answer_question("What was the combined LCR in Q3 2026?", "doc")
    assert r["supported"] is True
    model.assert_called_once()
