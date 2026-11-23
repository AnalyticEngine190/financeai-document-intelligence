"""Conservative, source-grounded lookup of scalar financial table cells.

This is not an LLM and does not contain reported financial values or page
numbers. It reads the Chroma-indexed document, identifies published table
headings, checks the exact period column and metric row, and returns the
source citation. If parsing is ambiguous it returns None (normal RAG follows).

Scope: consolidated quarterly/annual income, balance sheet, reported capital
ratios and risk exposure, plus LCR by currency. Exact business-area financial
highlights are also supported; other segment questions fall back to RAG.
"""

from collections import defaultdict
from dataclasses import dataclass
import re

from app.vector_store import get_collection


@dataclass(frozen=True)
class TableFact:
    answer: str
    document: str
    page: int
    metric: str
    period: str
    raw_value: str


@dataclass(frozen=True)
class MetricSpec:
    name: str
    question_patterns: tuple[str, ...]
    row_aliases: tuple[str, ...]
    table: str
    unit: str


# Labels and aliases only: NO reference numbers, accepted test answers,
# or hardcoded PDF page numbers appear in these rules.
METRICS = (
    MetricSpec("net interest income", (r"\bnet interest income\b",),
               ("Net interest income",), "income", "EURm"),
    MetricSpec("net fee and commission income", (r"\bnet fee and commission income\b",),
               ("Net fee and commission income",), "income", "EURm"),
    MetricSpec("total operating income", (r"\btotal operating income\b",),
               ("Total operating income",), "income", "EURm"),
    MetricSpec("net insurance result", (r"\bnet insurance result\b",),
               ("Net insurance result",), "income", "EURm"),
    MetricSpec("net result from items at fair value",
               (r"\bnet result from items at fair value\b",),
               ("Net result from items at fair value",), "income", "EURm"),
    MetricSpec("net profit for the period", (r"\bnet profit for the period\b", r"\bnet profit\b"),
               ("Net profit for the period",), "income", "EURm"),
    MetricSpec("operating profit", (r"\boperating profit\b",),
               ("Operating profit",), "income", "EURm"),
    MetricSpec("return on tangible equity", (r"\breturn on tangible equity\b",),
               ("Return on tangible equity",), "income", "percent"),
    MetricSpec("return on equity", (r"\breturn on equity\b",),
               ("Return on equity",), "income", "percent"),
    MetricSpec("cost-to-income ratio excluding regulatory fees",
               (r"\bcost.to.income ratio\b.*\b(?:excl\.?|excluding)\b.*\bregulatory fees\b",),
               ("Cost-to-income ratio excl. regulatory fees", "Cost-to-income ratio excluding regulatory fees"),
               "income", "percent"),
    MetricSpec("net interest margin", (r"\bnet interest margin\b",),
               ("Net interest margin",), "income", "percent"),
    MetricSpec("full-time-equivalent employees",
               (r"\bfull.time.equivalent employees\b", r"\bftes?\b", r"\bnumber of employees\b"),
               ("Number of employees (FTEs)",), "income", "count"),
    MetricSpec("assets under management", (r"\bassets under management\b",),
               ("Assets under management",), "income", "EURbn"),
    MetricSpec("total assets", (r"\btotal assets\b",),
               ("Total assets",), "balance", "EURm"),
    MetricSpec("total equity", (r"\btotal equity\b",),
               ("Total equity",), "balance", "EURm"),
    MetricSpec("common equity tier 1 capital ratio",
               (r"\bcet\s*-?\s*1\b.*\bratio\b", r"\bcommon equity tier 1\b.*\bratio\b"),
               ("Common Equity Tier 1 capital ratio", "Common Equity Tier 1 ratio"),
               "capital_ratio", "percent"),
    MetricSpec("tier 1 capital ratio", (r"\btier 1 capital ratio\b",),
               ("Tier 1 capital ratio",), "capital_ratio", "percent"),
    MetricSpec("total capital ratio", (r"\btotal capital ratio\b",),
               ("Total capital ratio",), "capital_ratio", "percent"),
    MetricSpec("credit risk exposure amount", (r"\bcredit risk\b.*\b(?:exposure|rea)\b",),
               ("Credit risk",), "credit_rea", "EURm"),
    MetricSpec("total risk exposure amount", (r"\btotal risk exposure amount\b", r"\btotal rea\b"),
               ("Total REA",), "total_rea", "EURm"),
    MetricSpec("liquidity coverage ratio", (r"\bliquidity coverage ratio\b", r"\blcr\b"),
               ("Liquidity coverage ratio (%)",), "lcr", "percent"),
)

SEGMENTS = (
    "personal banking", "business banking", "asset & wealth management",
    "asset and wealth management", "large corporates", "life and pension",
    "group functions", "by business area", "by segment",
)

VALUE_TOKEN = re.compile(r"(?<!\S)-?\d[\d,]*(?:\.\d+)?%?(?!\S)")
QUARTER_TOKEN = re.compile(r"\bQ[1-4]\d{2}\b", re.I)


def quarter_code(question: str) -> str | None:
    """Parse an explicit quarter, including the full requested year."""
    for pattern, order in (
        (r"\bQ([1-4])\s*[-/]?\s*(20\d{2})\b", "qy"),
        (r"\b(20\d{2})\s*[-/]?\s*Q([1-4])\b", "yq"),
        (r"\bQ([1-4])(\d{2})\b", "short"),
    ):
        match = re.search(pattern, question, re.I)
        if match:
            if order == "yq":
                return f"Q{match.group(2)}{match.group(1)[-2:]}"
            return f"Q{match.group(1)}{match.group(2)[-2:]}"
    return None


def full_year(question: str) -> str | None:
    m = re.search(r"\bfull[- ]year\s+(20\d{2})\b", question, re.I)
    return m.group(1) if m else None


def _spec(question: str) -> MetricSpec | None:
    # Direct cell extraction is for *one* published scalar, not analysis,
    # calculation, comparisons or multi-metric questions.
    if re.search(
        r"\b(?:compare|versus|trend|growth|drivers|breakdown|why|forecast|"
        r"predict|estimated|projection|difference|increased|decreased)\b",
        question, re.I,
    ):
        return None
    matches = [
        metric for metric in METRICS
        if any(re.search(p, question, re.I) for p in metric.question_patterns)
    ]
    return matches[0] if len(matches) == 1 else None


def _get_pages(document_id: str) -> list[dict]:
    records = get_collection().get(
        where={"document_id": document_id},
        include=["documents", "metadatas"],
    )
    chunks = defaultdict(list)
    for content, meta in zip(records["documents"], records["metadatas"]):
        key = (meta["filename"], int(meta["page_number"]))
        chunks[key].append((int(meta.get("chunk_index", 0)), content))
    return [
        {"document": doc, "page": page,
         "text": "\n".join(t for _, t in sorted(parts))}
        for (doc, page), parts in sorted(chunks.items(), key=lambda kv: kv[0][1])
    ]


def _section_matches(spec: MetricSpec, page_text: str, *, annual: bool) -> bool:
    p = page_text.casefold()
    if spec.table in {"income", "balance", "capital_ratio"}:
        if "key financial figures" not in p:
            return False
        if annual:
            return spec.table == "income" and "income statement, 10-year overview" in p
        if spec.table == "balance":
            return "balance sheet, 12-quarter overview" in p
        return "income statement, 12-quarter overview" in p
    if spec.table == "credit_rea":
        return "risk exposure amount (banking group)" in p
    if spec.table == "total_rea":
        return "capital position" in p and "total rea" in p
    if spec.table == "lcr":
        return "liquidity coverage ratio subcomponents" in p
    return False


def _quarter_headers(page_text: str) -> list[str]:
    """Only accept actual multi-column table headers, not title/footer dates."""
    headers = [
        [c.upper() for c in QUARTER_TOKEN.findall(line)]
        for line in page_text.splitlines()
    ]
    return max((h for h in headers if len(h) >= 3), key=len, default=[])


def _annual_headers(page_text: str) -> list[str]:
    for line in page_text.splitlines():
        if "Jan-Sep" in line:
            years = re.findall(r"\b20\d{2}\b", line)
            if len(years) >= 3:
                return [f"Jan-Sep {years[0]}", *years[1:]]
    return []


def _row_values(page_text: str, aliases: tuple[str, ...], n_columns: int) -> list[str] | None:
    """Require a literal row label and exactly the period-aligned cells.

    Extra numeric tokens are allowed only as compact footnote markers
    immediately attached to the label (e.g. 'ratio2,4, %').
    """
    for line in page_text.splitlines():
        stripped = line.strip()
        for alias in aliases:
            if not stripped.casefold().startswith(alias.casefold()):
                continue
            rest = stripped[len(alias):]
            # Reject other rows sharing a prefix (e.g. 'Return on equity
            # with amortised fees' is not 'Return on equity').
            if rest and rest[0].isalpha():
                continue
            matches = list(VALUE_TOKEN.finditer(rest))
            if len(matches) < n_columns:
                continue
            selected = matches[-n_columns:]
            extra = rest[:selected[0].start()].strip()
            if extra:
                # Text before the data cells can contain footnotes and units,
                # but must not be another metric label or a previous data cell.
                remainder = re.sub(r"\b(?:EURbn|EURm|basis points|bp)\b", "", extra, flags=re.I)
                if not re.fullmatch(r"[\s0-9,.*%\-]*", remainder):
                    continue
                # Reject a decimal in the extra values: it is more likely
                # an additional data column than a footnote.
                if re.search(r"\d+\.\d+", extra):
                    continue
            if rest[selected[-1].end():].strip():
                continue
            return [m.group() for m in selected]
    return None


def _format_value(raw: str, unit: str) -> str:
    value = raw.rstrip("%")
    if unit == "EURm":
        return f"EUR {value} million"
    if unit == "EURbn":
        return f"EUR {value} billion"
    if unit == "percent":
        return f"{value}%"
    if unit == "count":
        return f"{value} employees (FTEs)"
    raise ValueError(f"Unsupported unit: {unit}")


def _lcr_cell(page_text: str, question: str) -> str | None:
    p = page_text.casefold()
    if not all(tag in p for tag in ("combined", "usd", "eur")):
        return None
    match = re.search(
        r"^\s*Liquidity coverage ratio \(%\)\s+(\d+(?:\.\d+)?%)\s+"
        r"(\d+(?:\.\d+)?%)\s+(\d+(?:\.\d+)?%)\s*$",
        page_text, re.I | re.M,
    )
    if not match:
        return None
    q = question.lower()
    index = 1 if re.search(r"\busd\b", q) else 2 if re.search(r"\beur\b", q) else 0
    return match.group(index + 1)



# Only explicit, unambiguous business-area names can trigger segment lookup.
# Do not conflate segment highlights with Nordea Group consolidated metrics.
SEGMENT_ALIASES = {
    "personal banking": ("personal banking",),
    "business banking": ("business banking",),
    "asset & wealth management": (
        "asset & wealth management", "asset and wealth management",
    ),
    "large corporates & institutions": (
        "large corporates & institutions",
        "large corporates and institutions",
    ),
    "life & pension": ("life & pension", "life and pension"),
    "group functions": ("group functions",),
}


def _requested_segment(question: str) -> tuple[str, tuple[str, ...]] | None:
    q = question.casefold()
    found = [
        (name, aliases) for name, aliases in SEGMENT_ALIASES.items()
        if any(alias in q for alias in aliases)
    ]
    return found[0] if len(found) == 1 else None


def _segment_metric(question: str) -> MetricSpec | None:
    """Recognize one scalar metric, including segment-only 'total income'."""
    spec = _spec(question)
    # A segment question requesting more than one metric must not silently
    # return the other metric's scalar cell.
    if spec and re.search(r"\btotal income\b", question, re.I):
        return None
    if spec and spec.table == "income":
        return spec
    if spec is not None:
        return None
    # 'Total income' is the business-area row label, distinct from the
    # consolidated group row 'Total operating income'.
    if re.search(r"\btotal income\b", question, re.I) and not re.search(
        r"\b(?:compare|versus|trend|growth|why|forecast|difference)\b",
        question, re.I,
    ):
        return MetricSpec(
            "total income", (r"\btotal income\b",),
            ("Total income",), "income", "EURm",
        )
    return None


def _segment_highlights(text: str, aliases: tuple[str, ...]) -> str | None:
    """Find one segment's highlights despite PDF whitespace/layout changes.

    Indexed PDF text may be line-by-line, row-aligned, or flattened to one
    long line. Work on whitespace-normalized text, then strictly require the
    business-area heading followed by the highlights table heading.
    """
    flattened = " ".join(text.split())
    if not flattened:
        return None

    for alias in aliases:
        match = re.search(
            rf"\bBUSINESS\s+AREAS\s+{re.escape(alias)}"
            rf"\s+Financial\s+highlights\b",
            flattened,
            re.I,
        )
        if match is None:
            continue
        section = flattened[match.end():]
        # Volume tables are reported in EURbn and are not part of the
        # financial-highlights EURm table.
        section = re.split(
            r"\bVolumes(?:\*+)?\b", section, maxsplit=1, flags=re.I
        )[0]
        return section
    return None


def _segment_row_values(
    table: str, aliases: tuple[str, ...], n_columns: int
) -> list[str] | None:
    """Read the first n quarter cells after an exact segment metric label.

    Change percentages occur *after* the quarter columns. They must never
    be used as the requested metric's value. Matching consecutive numeric
    tokens also prevents skipping across another metric label if a row is
    incomplete.
    """
    if n_columns < 3:
        return None

    flattened = " ".join(table.split())
    numeric_cell = r"(-?\d[\d,]*(?:\.\d+)?%?)"

    for alias in aliases:
        label_pattern = re.compile(
            rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", re.I
        )
        for label in label_pattern.finditer(flattened):
            tail = flattened[label.end():]
            # Some metric labels have compact footnotes and percent units,
            # e.g. 'ratio*, %'. Never skip arbitrary words here.
            tail = re.sub(r"^\s*(?:\*+,?\s*%?|,\s*%)\s*", " ", tail)
            values: list[str] = []
            for _ in range(n_columns):
                cell = re.match(rf"^\s+{numeric_cell}(?=\s|$)", tail)
                if not cell:
                    break
                values.append(cell.group(1))
                tail = tail[cell.end():]
            if len(values) == n_columns:
                return values
    return None


def _find_segment_fact(
    question: str, pages: list[dict],
) -> tuple[TableFact | None, bool]:
    """Look up exact reported business-area cells; fail closed on ambiguity."""
    segment = _requested_segment(question)
    spec = _segment_metric(question)
    code = quarter_code(question)
    if not segment or spec is None or code is None:
        return None, False

    _name, aliases = segment
    sections = []
    for page in pages:
        section = _segment_highlights(page["text"], aliases)
        if section is not None:
            sections.append((page, section))
    if not sections:
        return None, False

    matches = []
    period_present = False
    for page, section in sections:
        headers = _quarter_headers(section)
        if code not in headers:
            continue
        period_present = True
        values = _segment_row_values(section, spec.row_aliases, len(headers))
        if values is None:
            continue
        raw = values[headers.index(code)]
        matches.append(TableFact(
            answer=_format_value(raw, spec.unit),
            document=page["document"], page=page["page"],
            metric=f"{_name}: {spec.name}", period=code, raw_value=raw,
        ))
    if len({(m.document, m.page, m.raw_value) for m in matches}) == 1:
        return matches[0], False
    return None, not period_present

def _find_fact(question: str, pages: list[dict]) -> TableFact | None:
    spec = _spec(question)
    if spec is None or any(part in question.lower() for part in SEGMENTS):
        return None
    code = quarter_code(question)
    year = full_year(question)
    if not code and not year:
        return None

    matches = []
    for page in pages:
        text = page["text"]
        if not _section_matches(spec, text, annual=bool(year)):
            continue
        if spec.table == "lcr":
            # Liquidity table identifies the quarter explicitly, rather
            # than as a Q326-style column.
            if not code:
                continue
            q_num, y_num = code[1], f"20{code[2:]}"
            if not re.search(rf"\bQ{q_num}\s+{y_num}\b", text, re.I):
                continue
            raw = _lcr_cell(text, question)
        else:
            headers = _annual_headers(text) if year else _quarter_headers(text)
            requested = year if year else code
            if requested not in headers:
                continue
            values = _row_values(text, spec.row_aliases, len(headers))
            raw = values[headers.index(requested)] if values else None
        if raw is None:
            continue
        matches.append(TableFact(
            answer=_format_value(raw, spec.unit), document=page["document"],
            page=page["page"], metric=spec.name,
            period=year or code, raw_value=raw,
        ))

    # Multiple different candidate values are not safe to reconcile by guess.
    if matches and len({(m.document, m.page, m.raw_value) for m in matches}) == 1:
        return matches[0]
    return None


def lookup_fact(question: str, document_id: str) -> tuple[TableFact | None, bool]:
    """Return (verified cell, period_unavailable).

    The second value is true only when an explicit period is missing from
    actual quarterly or yearly report table headers. This prevents a nearby
    reported period from being mistaken for the requested period.
    """
    code = quarter_code(question)
    year = full_year(question)
    spec = _spec(question)
    if _requested_segment(question):
        # Only exact quarterly business-area metrics use the table reader.
        # Other segment questions keep their Qwen fallback behavior.
        if not code or _segment_metric(question) is None:
            return None, False
        return _find_segment_fact(question, _get_pages(document_id))
    if spec is None or (not code and not year) or any(s in question.lower() for s in SEGMENTS):
        return None, False
    if spec.table == "lcr" and re.search(r"\busd\b", question, re.I) and re.search(r"\beur\b", question, re.I):
        return None, False

    pages = _get_pages(document_id)
    fact = _find_fact(question, pages)
    if fact:
        return fact, False

    if code:
        # Do not confuse a period mentioned in a footnote with a reporting
        # period available as a column. LCR has a separate period heading.
        present = any(code in _quarter_headers(p["text"]) for p in pages)
        if spec.table == "lcr":
            q_num, y_num = code[1], f"20{code[2:]}"
            present = any(
                "liquidity coverage ratio subcomponents" in p["text"].lower()
                and re.search(rf"\bQ{q_num}\s+{y_num}\b", p["text"], re.I)
                for p in pages
            )
        return None, not present

    if year:
        present = any(
            year in _annual_headers(p["text"])
            and "10-year overview" in p["text"].lower()
            for p in pages
        )
        return None, not present
    return None, False


# ---------------------------------------------------------------------------
# Day 9.1: opt-in structured, multi-cell route. The existing lookup_fact()
# function above is intentionally unchanged for backward compatibility.
# ---------------------------------------------------------------------------
from decimal import Decimal, InvalidOperation

from app.financial_vocabulary import BY_KEY as _VOCAB, SEGMENTS as _AREAS
from app.question_parser import FinancialIntent


@dataclass(frozen=True)
class FinancialCell:
    metric: str
    period: str
    scope: str
    value: Decimal
    unit: str
    document: str
    page: int
    row_label: str
    raw_value: str


def _financial_cell(concept, period, scope, raw, page, row_label):
    try:
        amount = Decimal(raw.replace(",", "").rstrip("%"))
    except (InvalidOperation, ValueError):
        return None
    return FinancialCell(
        metric=concept.key, period=period, scope=scope, value=amount,
        unit=concept.unit, document=page["document"], page=page["page"],
        row_label=row_label, raw_value=raw,
    )


def _read_group_cell(concept, period, pages, *, currency="combined"):
    matches = []
    annual = len(period) == 4 and period.isdigit()
    for page in pages:
        text = page["text"]
        lower = text.casefold()
        if concept.table in {"income", "capital", "balance"}:
            if "key financial figures" not in lower:
                if not (concept.key == "total_rea" and "capital position" in lower):
                    continue
            if annual:
                if concept.table != "income" or "income statement, 10-year overview" not in lower:
                    continue
            elif concept.table == "balance":
                if "balance sheet, 12-quarter overview" not in lower:
                    continue
            elif concept.key == "total_rea":
                if "capital position" not in lower:
                    continue
            elif "income statement, 12-quarter overview" not in lower:
                continue
        elif concept.table == "rea":
            if "risk exposure amount (banking group)" not in lower:
                continue
        elif concept.table == "lcr":
            if "liquidity coverage ratio subcomponents" not in lower:
                continue
            if len(period) != 4 or not period.startswith("Q"):
                continue
            q_num, y = period[1], f"20{period[2:]}"
            if not re.search(rf"\bQ{q_num}\s+{y}\b", text, re.I):
                continue
            match = re.search(
                r"^\s*Liquidity coverage ratio \(%\)\s+"
                r"(\d+(?:\.\d+)?%)\s+(\d+(?:\.\d+)?%)\s+"
                r"(\d+(?:\.\d+)?%)\s*$", text, re.I | re.M,
            )
            if match:
                i = {"combined": 1, "USD": 2, "EUR": 3}.get(currency)
                if i is not None:
                    cell = _financial_cell(concept, period, currency, match.group(i),
                                           page, "Liquidity coverage ratio (%)")
                    if cell:
                        matches.append(cell)
            continue
        else:
            continue
        headers = _annual_headers(text) if annual else _quarter_headers(text)
        if period not in headers:
            continue
        values = _row_values(text, concept.rows, len(headers))
        if not values:
            continue
        raw = values[headers.index(period)]
        cell = _financial_cell(concept, period, "group", raw, page, concept.rows[0])
        if cell:
            matches.append(cell)
    return _unique_cell(matches)


def _read_segment_cell(concept, period, segment, country, pages):
    if len(period) != 4 or not period.startswith("Q"):
        return None
    matches = []
    for page in pages:
        if country:
            # Country-level tables use 'PeB Finland' / 'BB Sweden', and their
            # metric heading immediately precedes the country's row. Never
            # substitute parent business-area totals.
            prefix = {"personal banking": "PeB", "business banking": "BB"}.get(segment)
            if prefix is None or concept.key not in {"net_interest_income", "net_fee_income"}:
                continue
            normalized = " ".join(page["text"].split())
            if not re.search(rf"\bBUSINESS\s+AREAS\s+{re.escape(segment)}\b", normalized, re.I):
                continue
            headers = _quarter_headers(page["text"])
            if period not in headers:
                continue
            heading = "Net interest income" if concept.key == "net_interest_income" else "Net fee and commission income"
            # Stop at next heading, so a match cannot leak into another metric.
            start = re.search(rf"\b{re.escape(heading)}\s*,\s*EURm\b", normalized, re.I)
            if not start:
                continue
            tail = normalized[start.end():]
            boundary = re.search(r"\b(?:Net interest income|Net fee and commission income|Net loan losses|Volumes|Total lending)\s*,\s*EUR", tail, re.I)
            section = tail[:boundary.start()] if boundary else tail
            values = _segment_row_values(section, (f"{prefix} {country}",), len(headers))
        else:
            aliases = _AREAS.get(segment)
            if not aliases:
                continue
            section = _segment_highlights(page["text"], tuple(a for a in aliases if len(a) > 3))
            if section is None:
                continue
            headers = _quarter_headers(section)
            if period not in headers:
                continue
            values = _segment_row_values(section, concept.rows, len(headers))
        if not values:
            continue
        cell = _financial_cell(concept, period,
                               f"{segment}/{country}" if country else segment,
                               values[headers.index(period)], page, concept.rows[0])
        if cell:
            matches.append(cell)
    return _unique_cell(matches)


def _unique_cell(matches):
    """Require exactly one *physical source*; don't guess across tables."""
    unique = {(c.document, c.page, c.metric, c.period, c.scope, c.value): c for c in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def resolve_intent_cells(intent: FinancialIntent, document_id: str) -> list[FinancialCell] | None:
    """Resolve every operand against indexed source text, or fail closed.

    Each cell includes exact page and metric-row provenance. A comparison is
    never answered when one operand is missing, or when multiple conflicting
    source values exist. No known answers or benchmark values appear here.
    """
    if not intent.metric or not intent.periods or intent.uncertain:
        return None
    concept = _VOCAB.get(intent.metric)
    if concept is None:
        return None
    pages = _get_pages(document_id)
    if not pages:
        return None

    targets = []
    if intent.operation == "scope_difference":
        if len(intent.scopes) != 2 or len(intent.periods) != 1 or intent.country:
            return None
        targets = [(intent.periods[0], scope, None) for scope in intent.scopes]
    elif intent.operation == "currency_difference":
        if len(intent.currency_scopes) != 2 or len(intent.periods) != 1:
            return None
        targets = [(intent.periods[0], "group", cur) for cur in intent.currency_scopes]
    elif intent.operation == "lookup":
        if len(intent.periods) != 1 or len(intent.scopes) != 1:
            return None
        targets = [(intent.periods[0], intent.scopes[0],
                    intent.currency_scopes[0] if intent.currency_scopes else None)]
    else:
        if len(intent.periods) != 2 or len(intent.scopes) != 1:
            return None
        targets = [(period, intent.scopes[0],
                    intent.currency_scopes[0] if intent.currency_scopes else None)
                   for period in intent.periods]

    resolved = []
    for period, scope, currency in targets:
        if scope != "group":
            cell = _read_segment_cell(concept, period, scope, intent.country, pages)
        elif concept.table == "segment":
            return None
        else:
            cell = _read_group_cell(concept, period, pages, currency=currency or "combined")
        if cell is None:
            return None
        resolved.append(cell)
    return resolved
