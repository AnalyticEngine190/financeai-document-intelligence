"""Evidence resolver tests on fictional numbers and document names."""
from unittest.mock import patch

from app.question_parser import parse_question
from app.table_lookup import resolve_intent_cells


PAGES = {
    7: """KEY FINANCIAL FIGURES
Income statement, 12-quarter overview
EURm Q326 Q226 Q126 Q425 Q325
Net interest income 120 105 101 100 95
Net fee and commission income 80 76 74 73 69
Total operating income 300 280 290 270 240
Net profit for the period 120 110 100 90 80
Net profit excl. items affecting comparability1 120 110 108 90 80
Return on equity, % 15.2 14.0 13.0 12.0 11.0
Return on equity with amortised regulatory fees, % 15.0 14.2 13.1 12.1 11.1
Tier 1 capital ratio2,4, % 17.1 17.5 17.6 18.5 18.3
""",
    8: """KEY FINANCIAL FIGURES
Balance sheet, 12-quarter overview
EURm Q326 Q226 Q126 Q425 Q325
Cash and balances with central banks 42,600 43,000 40,000 39,000 38,000
Total assets 700,000 690,000 680,000 650,000 640,000
""",
    13: """BUSINESS AREAS Personal Banking Financial highlights
EURm Q326 Q226 Q126 Q425 Q325 Q3/Q3 Q3/Q2
Net interest income 78 77 76 75 74 -1% 2%
Total income 115 110 105 103 102 1% 2%
Volumes
""",
    14: """BUSINESS AREAS Personal Banking
EURm Q326 Q226 Q126 Q425 Q325
Net interest income, EURm
PeB Finland 21 20 19 18 17 1% 1%
PeB Sweden 30 29 28 27 26 1% 1%
Net fee and commission income, EURm
PeB Finland 11 10 9 8 7 1% 1%
""",
    20: """BUSINESS AREAS Business Banking Financial highlights
EURm Q326 Q226 Q126 Q425 Q325 Q3/Q3 Q3/Q2
Total income 90 88 86 84 82 1% 2%
Risk exposure amount (REA) 460 450 440 430 420 1% 2%
Volumes
""",
    21: """BUSINESS AREAS Business Banking
EURm Q326 Q226 Q126 Q425 Q325
Net interest income, EURm
BB Sweden 15 14 13 12 11 1% 1%
Net fee and commission income, EURm
BB Sweden 7 6 5 4 3 1% 1%
""",
    41: """RISK, LIQUIDITY AND CAPITAL MANAGEMENT
Risk exposure amount (banking group)
EURm Q326 Q226 Q126 Q425 Q325
Credit risk 300 290 280 270 260
Operational risk 30 29 28 27 26
""",
    53: """RISK, LIQUIDITY AND CAPITAL MANAGEMENT
Liquidity coverage ratio subcomponents (LCR delegated act)
Q3 2026 Combined USD EUR
Liquidity coverage ratio (%) 150% 200% 170%
""",
    5: """KEY FINANCIAL FIGURES
Income statement, 10-year overview
EURm Jan-Sep 2026 2025 2024 2023
Operating profit 50 60 70 80
""",
}


class Collection:
    def get(self, where, include):
        assert where == {"document_id": "d"}
        return {
            "documents": list(PAGES.values()),
            "metadatas": [
                {"filename": "fiction.pdf", "page_number": p, "chunk_index": 0}
                for p in PAGES
            ],
        }


def resolve(q):
    with patch("app.table_lookup.get_collection", return_value=Collection()):
        return resolve_intent_cells(parse_question(q), "d")


def test_resolve_two_periods_without_crossing_rows():
    cells = resolve("How much did net interest income rise from Q2 2026 to Q3 2026?")
    assert [str(c.value) for c in cells] == ["105", "120"]
    assert [c.page for c in cells] == [7, 7]


def test_qualified_metric_is_not_unqualified_parent():
    cells = resolve("In Q1 2026 what was net profit excluding items affecting comparability rather than reported net profit?")
    assert cells and cells[0].value == 108 and cells[0].page == 7


def test_scope_difference_returns_both_pages():
    cells = resolve("How many EUR million more total income did Personal Banking have than Business Banking in Q3 2026?")
    assert [c.value for c in cells] == [115, 90]
    assert [c.page for c in cells] == [13, 20]


def test_subsegment_country_never_uses_parent_total():
    c = resolve("Within Personal Banking, how much Q3 2026 net interest income was attributed to Finland?")
    assert c and c[0].value == 21 and c[0].page == 14
    c = resolve("For Q3 2026 what net fee and commission income did Business Banking Sweden contribute?")
    assert c and c[0].value == 7 and c[0].page == 21


def test_currency_only_lcr_not_combined():
    cells = resolve("For Q3 2026, what was the euro-only LCR, not the combined LCR and not the dollar-only LCR?")
    assert cells and cells[0].value == 170 and cells[0].scope == "EUR"


def test_unavailable_period_and_missing_country_fail_closed():
    assert resolve("How much did net interest income rise from Q2 2035 to Q3 2035?") is None
    assert resolve("Within Personal Banking, what was Q3 2026 net interest income in Denmark?") is None


def test_bank_risk_and_balance_sheet():
    a = resolve("What was operational-risk REA rather than credit-risk REA in Q3 2026?")
    b = resolve("Find Cash and balances with central banks in Q2 2026")
    assert a and a[0].value == 30 and a[0].page == 41
    assert b and b[0].value == 43000 and b[0].page == 8


def test_annual_income_not_ytd():
    c = resolve("Using the 10-year income overview, what was full-calendar-year 2024 operating profit?")
    assert c and c[0].value == 70 and c[0].page == 5
