"""Financial concept vocabulary, NOT a database of reported figures.

Concepts are matched independently from the reporting period and company scope.
Exact financial values are always read from indexed evidence.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Concept:
    key: str
    aliases: tuple[str, ...]       # Regex alternatives in user language
    rows: tuple[str, ...]          # Literal PDF row labels
    table: str                     # income, balance, capital, rea, lcr
    unit: str                      # EURm, EURbn, percent, count


CONCEPTS = (
    # Concepts describe labels and dimensions ONLY; no reported figures.
    Concept("other_operating_income", (r"\bother operating income\b",),
            ("Other operating income",), "income", "EURm"),
    Concept("net_financial_income", (r"\bnet financial income\b",),
            ("Net financial income",), "income", "EURm"),
    Concept("net_other_income", (r"\bnet other income\b",),
            ("Net other income",), "income", "EURm"),
    Concept("net_expected_credit_losses", (
        r"\bnet expected credit losses\b", r"\bexpected credit losses\b"),
            ("Net expected credit losses",), "income", "EURm"),
    Concept("operating_expenses_ex_reg_fees", (
        r"\btotal operating expenses?\s+(?:excl\.?|excluding)\s+(?:reg\.?|regulatory)\s+fees\b",
        r"\btotal operating expenses?\s+excluding\s+regulatory\s+fees\b",
    ), ("Total operating expenses excl. reg. fees", "Total operating expenses excl. regulatory fees"),
            "income", "EURm"),
    Concept("cost_income_ratio", (
        r"\bcost\s*[/\-]\s*income ratio\b", r"\bcost.to.income ratio\b", r"\bC\s*[/\-]\s*I\s+ratio\b",
    ), ("Cost/income ratio", "C/I ratio"), "income", "ratio"),
    Concept("nsfr", (r"\bnet stable funding ratio\b", r"\bNSFR\b"),
            ("Net Stable Funding Ratio (NSFR)",), "capital", "percent"),
    Concept("deposits_public", (
        r"\bdeposits and borrowings? from the public\b",),
            ("Deposits and borrowings from the public", "Deposits and borrowing from the public"), "balance", "EURm"),
    Concept("segment_total_deposits", (
        r"\btotal deposits\b", r"\btotal customer deposits\b",),
            ("Total deposits",), "segment", "EURbn"),
    Concept("segment_total_lending", (
        r"\btotal lending\b", r"\btotal loans\b"),
            ("Total lending",), "segment", "EURbn"),
    Concept("segment_deposits_public", (
        r"\bdeposits from the public\b", r"\bdeposits from public\b",
        # Natural-language shorthand for the same reported segment row.
        # It remains a segment-only concept; it must not be treated as Group
        # deposits when the user omits the reporting division.
        r"\bpublic deposits\b"),
            ("Deposits from the public",), "segment", "money"),
    Concept("loan_loss_ratio", (
        r"\b(?:ordinary )?loan.loss ratio\b",),
            ("Loan loss ratio, basis points",), "income", "basis_points"),
    Concept("operating_profit_adjusted", (
        r"\boperating profit\s*,?\s*(?:before|excluding|excl\.?)\s+items affecting comparability\b",
        r"\boperating profit\s*,?\s*(?:before|excluding|excl\.?)\s+(?:iac)\b",
    ), ("Operating profit before items affecting comparability",), "income", "EURm"),
    Concept("net_profit_adjusted", (
        r"net profit\s+(?:excl\.?|excluding)\s+items affecting comparability",
        r"net profit\s+excluding\s+items affecting comparability",
        r"profit\s+excluding\s+items affecting comparability",
    ), ("Net profit excl. items affecting comparability",), "income", "EURm"),
    Concept("roe_amortised", (
        r"return on equity\s+with\s+amortis(?:ed|ized)\s+regulatory fees",
        r"roe\s+with\s+amortis(?:ed|ized)\s+regulatory fees",
    ), ("Return on equity with amortised regulatory fees",), "income", "percent"),
    Concept("cost_to_income_ex_fees", (
        r"cost.to.income ratio\s+(?:excl\.?|excluding)\s+regulatory fees",
        r"ratio of costs? to income\s+with\s+regulatory fees excluded",
        r"ratio of costs? to income\s+(?:excl\.?|excluding)\s+regulatory fees",
    ), ("Cost-to-income ratio excl. regulatory fees",), "income", "percent"),
    Concept("cet1_ratio", (
        r"common equity tier\s*1\s+(?:capital\s+)?ratio",
        r"\bcet\s*-?\s*1\s+(?:capital\s+)?ratio",
        r"\bcet1\b",
    ), ("Common Equity Tier 1 capital ratio", "Common equity tier 1 ratio, CRR"), "capital", "percent"),
    Concept("tier1_ratio", (
        r"(?<!equity )\btier\s*1\s+capital\s+ratio",
    ), ("Tier 1 capital ratio",), "capital", "percent"),
    Concept("total_capital_ratio", (
        r"\btotal\s+capital\s+ratio",
    ), ("Total capital ratio",), "capital", "percent"),
    Concept("net_interest_income", (
        r"\bnet\s+interest\s+income\b",
        r"\binterest\s+income\b.{0,60}\b(?:deducting|minus|less|after)\b.{0,35}\binterest\s+expenses?\b",
        r"\binterest\s+(?:received|earned)\b.{0,35}\b(?:minus|less)\b.{0,35}\binterest\s+(?:paid|expense)\b",
    ), ("Net interest income",), "income", "EURm"),
    Concept("net_fee_income", (
        r"net\s+fee\s*(?:and|&)\s*commission\s+income",
        r"net\s+fee-and-commission\s+income",
        r"net\s+(?:amount|earnings|income)\s+from\s+fees?\s+and\s+commissions?",
        # FinanceAI offers this as a clarification option. Accept the concise
        # user reply as shorthand for the report's exact "Net fee and
        # commission income" row, not as a separate invented metric.
        r"\bcommission\s+income\b",
    ), ("Net fee and commission income",), "income", "EURm"),
    Concept("net_insurance_result", (r"\bnet insurance result\b",),
            ("Net insurance result",), "income", "EURm"),
    Concept("fair_value_result", (r"net result from items at fair value",),
            ("Net result from items at fair value",), "income", "EURm"),
    Concept("total_operating_income", (
        r"\btotal operating income\b",
        r"\boperating income\b",
        r"\bconsolidated total income\b",
        r"\btotal income\b",
    ), ("Total operating income", "Total income"), "income", "EURm"),
    Concept("segment_total_income", (r"\btotal income\b",),
            ("Total income",), "segment", "EURm"),
    Concept("net_profit", (
        r"\bnet profit(?: for the period)?\b",
        r"\bprofit for the period\b",
        r"\bbottom.line profit\b",
        r"\bbottom.line (?:earnings|result)\b",
    ), ("Net profit for the period",), "income", "EURm"),
    Concept("operating_profit", (r"\boperating profit\b",),
            ("Operating profit",), "income", "EURm"),
    Concept("roe_tangible", (r"\breturn on tangible equity\b", r"\brote\b"),
            ("Return on tangible equity",), "income", "percent"),
    Concept("roe", (r"\b(?:ordinary )?return on equity\b", r"\broe\b"),
            ("Return on equity",), "income", "percent"),
    Concept("net_interest_margin", (r"\bnet interest margin\b",),
            ("Net interest margin",), "income", "percent"),
    Concept("fte", (r"\bfull.time.equivalent employees\b", r"\bftes?\b", r"\bnumber of employees\b"),
            ("Number of employees (FTEs)",), "income", "count"),
    Concept("aum", (
        r"\bassets under management\b",
        r"\b(?:how much|amount|assets)\b.{0,45}\bmanage(?:d)? on behalf of investors\b",
        r"\bassets managed for (?:investors|clients)\b",
    ), ("Assets under management",), "income", "EURbn"),
    Concept("assets_in_pooled_schemes", (
        r"\bassets in pooled schemes and unit-linked investment contracts\b",
        r"\bpooled schemes and unit-linked investment contracts\b",
    ), ("Assets in pooled schemes and unit-linked investment contracts",), "income", "EURm"),
    Concept("total_assets", (
        r"\btotal assets?\b",
        r"\b(?:consolidated )?balance.sheet\b.{0,70}\b(?:large|size|total)\b",
        r"\bhow large\b.{0,65}\bconsolidated balance.sheet\b",
    ), ("Total assets",), "balance", "EURm"),
    Concept("total_equity", (r"\btotal equity\b",),
            ("Total equity",), "balance", "EURm"),
    Concept("cash_central_banks", (r"\bcash and balances with central banks\b",),
            ("Cash and balances with central banks",), "balance", "EURm"),
    Concept("operational_rea", (
        r"\boperational.risk\s+(?:rea|risk exposure amount)\b",
        r"\boperational.risk\b.{0,20}\brea\b",
    ), ("Operational risk",), "rea", "EURm"),
    Concept("credit_rea", (
        r"\bcredit.risk\s+(?:rea|risk exposure amount|exposure amount)\b",
        r"\bcredit.risk\b.{0,20}\brea\b",
    ), ("Credit risk",), "rea", "EURm"),
    Concept("total_rea", (
        r"\btotal risk exposure amount\b",
        r"\bbanking group total\s+(?:rea|risk exposure amount)\b",
        r"\btotal rea\b",
    ), ("Total REA",), "capital", "EURm"),
    Concept("segment_rea", (
        r"\brisk exposure amount\s*\(?(?:rea)?\)?\b",
        r"\brisk exposure amount\b",
    ), ("Risk exposure amount (REA)",), "segment", "EURm"),
    # Cross-issuer concepts: labels and synonyms only, never reported values.
    Concept("group_total_income", (r"\btotal income\b",),
            ("Total income",), "income", "EURm"),
    Concept("other_income_group", (r"\bother income(?: line)?\b",),
            ("Other income",), "income", "EURm"),
    Concept("net_gains_financial", (r"\bnet gains?\s*(?:/|or)\s*losses on financial transactions\b",),
            ("Net gains/losses on financial transactions",), "income", "EURm"),
    Concept("other_dividend_income", (r"\bother dividend income\b",),
            ("Other dividend income",), "income", "EURm"),
    Concept("associates_profit", (
        r"\bshare of profit of associates and joint ventures\b",
        r"\bequity[ -]method income\b",
        r"\bequity accounted income\b",
    ), ("Share of profit of associates and joint ventures",), "income", "EURm"),
    Concept("staff_costs", (r"\bstaff[- ]costs?\b",), ("Staff costs",), "income", "EURm"),
    Concept("other_expenses", (r"\bother expenses\b",), ("Other expenses",), "income", "EURm"),
    Concept("depreciation_impairment", (r"\bdepreciation,? amortisation and impairment\b",),
            ("Depreciation, amortisation and impairment",), "income", "EURm"),
    Concept("total_expenses", (r"\btotal expenses\b",), ("Total expenses",), "income", "EURm"),
    Concept("profit_before_credit_losses_fees", (
        # SEB labels the line "Profit before credit losses and imposed levies";
        # users commonly omit the bank-specific suffix when naming the metric.
        r"\bprofit before credit losses\b",
        r"\bprofit before credit losses and regulatory fees\b",
        r"\bprofit before credit losses and imposed levies\b",
    ), ("Profit before credit losses and regulatory fees",
        "Profit before credit losses and imposed levies"), "income", "EURm"),
    Concept("profit_before_loan_losses", (
        r"\bprofit before loan losses\b",
        r"\boperating profit before loan losses\b",
    ), ("Operating profit before loan losses", "Profit before loan losses"), "income", "EURm"),
    Concept("income_tax_expense", (
        r"\bincome tax expense\b", r"\btax expense\b", r"\btaxes\b"),
        ("Income tax expense", "Tax expense", "Taxes", "Tax"), "income", "EURm"),
    Concept("assets_under_custody", (
        r"\bassets under custody\b",),
        ("Assets under custody, SEK bn", "Assets under custody",), "ratios", "money"),
    Concept("loans_public", (r"\bloans to the public\b",),
            ("Loans to the public",), "balance", "EURm"),
    Concept("issued_securities", (r"\bissued securities(?:,? etc\.?)?\b",),
            ("Issued securities, etc.", "Issued securities"), "balance", "EURm"),
    Concept("total_liabilities", (r"\btotal liabilities\b(?!\s+and equity)",),
            ("Total liabilities",), "balance", "EURm"),
    Concept("derivative_assets", (r"\bderivative assets\b",),
            ("Derivatives",), "balance", "EURm"),
    Concept("basic_earnings_per_share", (
        r"\bbasic (?:earnings per share|eps)\b",),
        ("Basic earnings per share, SEK", "Basic earnings per share, EUR"), "income", "currency"),
    Concept("diluted_earnings_per_share", (
        r"\bdiluted (?:earnings per share|eps)\b",),
        ("Diluted earnings per share, SEK", "Diluted earnings per share, EUR"), "income", "currency"),
    Concept("earnings_per_share", (r"\bearnings per share\b", r"\beps\b"),
            ("Earnings per share, SEK", "Earnings per share, EUR"), "income", "currency"),
    Concept("credit_loss_ratio", (r"\bcredit loss ratio\b",),
            ("Credit loss ratio",), "capital", "percent"),
    Concept("lcr", (r"\bliquidity coverage ratio\b", r"\blcr\b"),
            ("Liquidity coverage ratio (%)",), "lcr", "percent"),
)

BY_KEY = {c.key: c for c in CONCEPTS}

SEGMENTS = {
    # Nordea business areas.
    "personal banking": ("personal banking", "peb"),
    "business banking": ("business banking", "bb"),
    "asset & wealth management": ("asset & wealth management", "asset and wealth management"),
    "large corporates & institutions": ("large corporates & institutions", "large corporates and institutions"),
    "group functions": ("group functions",),
    "life & pension": ("life & pension", "life and pension"),
    # SEB divisions. Keep the two similarly named wealth divisions distinct.
    "corporate & investment banking": ("corporate & investment banking", "corporate and investment banking"),
    "business & retail banking": ("business & retail banking", "business and retail banking"),
    "wealth & asset management": ("wealth & asset management", "wealth and asset management"),
    "baltic": ("baltic", "baltic division"),
    # Handelsbanken business segments; the full labels avoid confusing
    # standalone geography/Markets references with a named segment.
    "handelsbanken sweden": ("handelsbanken sweden",),
    "handelsbanken uk": ("handelsbanken uk",),
    "handelsbanken norway": ("handelsbanken norway",),
    "handelsbanken the netherlands": ("handelsbanken the netherlands",),
    "handelsbanken markets": ("handelsbanken markets",),
}


def metric_matches(text: str) -> list[tuple[Concept, str, int, int]]:
    """Return matched concepts with their actual spans, longest/specific first."""
    hits = []
    for concept in CONCEPTS:
        for pattern in concept.aliases:
            for m in re.finditer(pattern, text, re.I):
                hits.append((concept, m.group(0), m.start(), m.end()))
    return sorted(hits, key=lambda h: (-(h[3] - h[2]), h[2]))
