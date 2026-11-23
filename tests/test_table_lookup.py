"""Offline regression tests for the structured table-cell path.

No external data or local Ollama required. Checks parsing, period/scope
safety, and source page propagation with synthetic financial tables.
"""

from unittest.mock import patch

from app.table_lookup import lookup_fact, quarter_code


class FakeCollection:
    def get(self, where, include):
        assert where == {"document_id": "doc"}
        return {
            "documents": [
                (
                    "KEY FINANCIAL FIGURES\n"
                    "Income statement, 12-quarter overview\n"
                    "EURm Q326 Q226 Q126 Q425\n"
                    "Net interest income 100 101 102 103\n"
                    "Total operating income 200 201 202 203\n"
                    "Return on equity, % 13.5 14.3 12.1 12.2\n"
                    "Tier 1 capital ratio2,4, % 18.6 18.5 18.4 18.3\n"
                ),
                (
                    "RISK, LIQUIDITY AND CAPITAL MANAGEMENT\n"
                    "Liquidity coverage ratio subcomponents\n"
                    "Q3 2026 Combined USD EUR\n"
                    "Liquidity coverage ratio (%) 152% 205% 169%\n"
                ),
                (
                    "KEY FINANCIAL FIGURES\n"
                    "Income statement, 10-year overview\n"
                    "EURm Jan-Sep 2026 2025 2024 2023\n"
                    "Total operating income 800 900 950 950\n"
                ),
            ],
            "metadatas": [
                {"filename": "report.pdf", "page_number": page, "chunk_index": 0}
                for page in (9, 10, 5)
            ],
        }


def get_fact(question):
    with patch("app.table_lookup.get_collection", return_value=FakeCollection()):
        return lookup_fact(question, "doc")


def test_full_year_and_quarter_are_not_confused():
    fact, missing = get_fact("What was total operating income in Q3 2026?")
    assert (fact.answer, fact.page, missing) == ("EUR 200 million", 9, False)
    fact, missing = get_fact("What was full-year 2025 total operating income?")
    assert (fact.answer, fact.page, missing) == ("EUR 900 million", 5, False)


def test_ratio_footnotes_do_not_become_values():
    fact, missing = get_fact("What was the Tier 1 capital ratio in Q3 2026?")
    assert fact.answer == "18.6%"
    assert missing is False


def test_ratio_does_not_match_wrong_variant():
    fact, missing = get_fact("What was return on equity in Q3 2026?")
    assert fact.answer == "13.5%"
    assert missing is False


def test_lcr_currency_specific_values():
    assert get_fact("USD liquidity coverage ratio in Q3 2026?")[0].answer == "205%"
    assert get_fact("EUR liquidity coverage ratio in Q3 2026?")[0].answer == "169%"
    assert get_fact("Combined LCR in Q3 2026?")[0].answer == "152%"


def test_future_quarter_fails_closed():
    fact, missing = get_fact("What was total operating income in Q3 2035?")
    assert fact is None and missing is True


def test_full_year_2026_does_not_use_jan_sep():
    fact, missing = get_fact("What was full-year 2026 total operating income?")
    assert fact is None and missing is True


def test_explicit_segment_does_not_use_group_total():
    fact, missing = get_fact("What was Personal Banking's net interest income in Q3 2026?")
    assert fact is None and missing is False


def test_quarter_formats():
    assert quarter_code("2026 Q3") == "Q326"
    assert quarter_code("Q3 2026") == "Q326"
    assert quarter_code("Q326") == "Q326"
    assert quarter_code("Q4 2030") == "Q430"


def test_comparisons_do_not_collapse_to_one_metric():
    fact, missing = get_fact(
        "Compare net interest income versus total operating income in Q3 2026"
    )
    assert fact is None and missing is False


def test_two_lcr_currencies_need_multi_value_answer():
    fact, missing = get_fact("Compare USD and EUR LCR in Q3 2026")
    assert fact is None and missing is False
