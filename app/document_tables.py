"""Document-scoped, fail-closed scalar lookup for financial quarterly tables.

Day 10.2 supports both compact/one-line factbook tables and quarterly reports
whose PyMuPDF sort=True extraction has:
- years on a second header row, sometimes with YTD and annual columns;
- values such as ``10 881`` using spaces as thousands separators;
- accounting-row footnotes such as ``Net interest income¹⁾``;
- percentages without a page-level monetary unit.

The resolver only uses text belonging to `document_id`. It never looks up a
number from another report or assumes a reporting currency from a bank name.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from app.financial_vocabulary import BY_KEY
from app.table_lookup import FinancialCell, _get_pages

_Q = re.compile(r"\bQ([1-4])\b", re.I)
_YEAR = re.compile(r"\b20\d{2}\b")
_PAIRED = re.compile(r"\bQ([1-4])\s*(20\d{2})\b", re.I)
_COMPACT = re.compile(r"\bQ([1-4])(2\d)\b", re.I)
_UNIT = re.compile(r"\b(EUR|SEK|NOK|DKK|USD|GBP)\s*(m|mn|million|millions|bn|billion|billions)\b", re.I)
_FOOTNOTE = re.compile(r"^(?:\s*(?:\d+\)|[¹²³⁴⁵⁶⁷⁸⁹⁰]+[⁾)]|\(\d+\)|⁽[¹²³⁴⁵⁶⁷⁸⁹⁰]+⁾))+")
_PERCENT_UNIT = re.compile(r"^\s*,?\s*%\s*")
_MONEY_UNIT = re.compile(r"^\s*,?\s*(?:EUR|SEK|NOK|DKK|USD|GBP)\s*(?:bn|m|mn|million|billions?)\s*", re.I)

_EXTRA_ROWS = {
    "net_profit": ("Net profit", "Profit for the period"),
    "operating_profit": ("Operating profit",),
    "operating_profit_adjusted": ("Operating profit before items affecting comparability",),
    "aum": ("Assets under management",),
    "fte": ("Number of full time equivalents", "Number of full-time equivalents",
            "Number of full-time equivalents (FTEs)"),
    "lcr": ("Liquidity Coverage Ratio (LCR)",),
    "total_rea": ("Risk exposure amount",),
    "loan_loss_ratio": ("Loan loss ratio, basis points",),
    "associates_profit": ("Equity method",),
}


def _row_labels(metric: str) -> tuple[str, ...]:
    return (*BY_KEY[metric].rows, *_EXTRA_ROWS.get(metric, ()))


def _quarter_columns(header: str) -> list[str]:
    """Read complete quarter/year pairs from compact OR stacked headers.

    This helper returns *quarter* labels only. `_aligned_columns` additionally
    includes non-quarter columns for correct row-value alignment.
    """
    compact = [(int(q), 2000 + int(y)) for q, y in _COMPACT.findall(header)]
    pairs = [(int(q), int(y)) for q, y in _PAIRED.findall(header)]
    if compact and len(compact) >= 2:
        result = [f"Q{q}{y % 100:02d}" for q, y in compact]
        return result if len(set(result)) == len(result) else []
    if len(pairs) >= 2:
        # Consecutive compact pairs are unambiguous. Stacked header strings
        # with one accidental boundary pair must not enter this branch.
        total_q = len(_Q.findall(header))
        if len(pairs) == total_q:
            result = [f"Q{q}{y % 100:02d}" for q, y in pairs]
            return result if len(set(result)) == len(result) else []
    qs = [int(q) for q in _Q.findall(header)]
    ys = [int(y) for y in _YEAR.findall(header)]
    if len(qs) >= 2 and len(ys) >= len(qs) and len(qs) <= 16:
        result = [f"Q{q}{y % 100:02d}" for q, y in zip(qs, ys)]
        return result if len(set(result)) == len(result) else []
    return []


def _aligned_columns_legacy(qrow: str, yrow: str = "") -> list[str]:
    """Quarter labels followed by YTD/full-year placeholders when present.

    Year counts determine the *total* number of data cells. This matters for
    SEB's 'Q3 Q2 Q3 Jan-Sep Full year' heading, which has six columns but
    only the first three refer to individual quarters.
    """
    if yrow and "%" in yrow:
        # Growth-rate interleaving requires extra column alignment logic. No
        # safe assumption that consecutive numbers correspond to quarters.
        return []
    merged = qrow + "\n" + yrow
    qs = _quarter_columns(merged)
    if len(qs) < 2:
        return []
    n_years = len(_YEAR.findall(yrow)) if yrow else 0
    if n_years and n_years < len(qs):
        return []
    n_total = n_years if n_years else len(qs)
    if not 2 <= n_total <= 16:
        return []
    return qs + [f"OTHER_{i}" for i in range(n_total - len(qs))]


_STACKED_TOP = re.compile(r"^(?:Q([1-4])|Q[1-4]-Q[1-4]|H[12]|Year|FY|Index|Change)$", re.I)
_STACKED_BOTTOM = re.compile(r"^(?:(20\d{2})\*?|\d{2}/\d{2}|Q[1-4]/Q[1-4])$")


def _stacked_columns(qrow: str, yrow: str) -> list[str]:
    """Two-row header with one label per column on each row, e.g.::

        Q1-Q3   Q1-Q3   Index   Q3     Q2     Q1     Q4     Q3     Year
         2026    2025   26/25   2026   2026   2026   2025   2025   2025

    Year-to-date, index and full-year columns stay as placeholders so cells
    remain aligned; only one quarter over one year becomes a quarter column.

    PDFs often print two statements side by side, so the label row is cut at
    its first wide gap (only the left table is read) and the year row keeps
    just its year/index tokens (words such as "Note (DKK millions)" are
    ignored). Each label must pair with a recognised year/index token and
    every label must be known, otherwise nothing is returned (fail closed).
    """
    left = re.split(r"\s{20,}", qrow.strip())[0]
    top = re.sub(r"\bFull\s+year\b", "FY", left, flags=re.I).split()
    bottom = [t for t in yrow.split() if _STACKED_BOTTOM.fullmatch(t)]
    if not top or len(top) > 16 or len(bottom) < len(top):
        return []
    columns: list[str] = []
    for i, (a, b) in enumerate(zip(top, bottom)):
        ma, mb = _STACKED_TOP.fullmatch(a), _STACKED_BOTTOM.fullmatch(b)
        if not ma or not mb:
            return []
        if ma.group(1) and mb.group(1):
            columns.append(f"Q{ma.group(1)}{int(mb.group(1)) % 100:02d}")
        else:
            columns.append(f"OTHER_{i}")
    quarters = [c for c in columns if not c.startswith("OTHER_")]
    if len(quarters) < 2 or len(set(quarters)) != len(quarters):
        return []
    return columns


def _aligned_columns(qrow: str, yrow: str = "") -> list[str]:
    """Existing header readers first; the stacked-label reader only as a fallback."""
    return _aligned_columns_legacy(qrow, yrow) or (_stacked_columns(qrow, yrow) if yrow else [])


def _currency_unit(text: str) -> str | None:
    choices = set()
    for m in _UNIT.finditer(text):
        scale = "bn" if m[2].casefold() in {"bn", "billion", "billions"} else "m"
        choices.add(m[1].upper() + scale)
    return next(iter(choices)) if len(choices) == 1 else None


def _label_tail(line: str, alias: str) -> tuple[str, str] | None:
    """Strict row-prefix match, allowing footnotes/units but not qualifiers."""
    s = line.strip()
    # PDF text layout may split a row label across physical lines; joining
    # those lines can introduce multiple spaces. Compare words, not spacing.
    pattern = r"^" + r"\s+".join(re.escape(w) for w in alias.split())
    match = re.match(pattern, s, re.I)
    if not match:
        return None
    rest = s[match.end():]
    if rest and rest[0].isalpha():
        return None  # e.g. 'Operating profits' vs 'Operating profit'
    # Strip a footnote immediately following a metric label.
    rest = _FOOTNOTE.sub("", rest)
    prefix = s[:match.end()]
    for unit_regex in (_PERCENT_UNIT, _MONEY_UNIT):
        m = unit_regex.match(rest)
        if m:
            prefix += rest[:m.end()]
            rest = rest[m.end():]
            break
    rest = _FOOTNOTE.sub("", rest)
    # Some PDF issuers append a bare footnote numeral directly to the label
    # e.g. 'Loan loss ratio, basis points3   8 ...'. Only drop it when the
    # next delimiter is a two-space table-column separator.
    rest = re.sub(r"^\d{1,2}(?=\s{2,}[-+]?\d)", "", rest)
    # The remaining suffix must start with a numeric data cell, not an
    # adjusted metric qualifier ('before ...', 'excluding ...').
    rest = rest.strip()
    if not rest or not (rest[0].isdigit() or rest[0] in "+-"):
        return None
    return rest, prefix


def _parse_value(raw: str, percent: bool) -> Decimal | None:
    s = raw.strip().rstrip("%")
    if re.fullmatch(r"[+-]?\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "").replace(" ", "")
    elif percent and re.fullmatch(r"[+-]?\d+,\d{1,2}", s):
        s = s.replace(",", ".")
    elif not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _extract_row(line: str, metric: str, ncolumns: int, col: int,
                 header_unit: str | None):
    concept = BY_KEY[metric]
    for alias in sorted(_row_labels(metric), key=len, reverse=True):
        found = _label_tail(line, alias)
        if found is None:
            continue
        tail, prefix = found
        unit = _currency_unit(prefix) or header_unit
        if concept.unit in {"percent", "ratio", "basis_points"}:
            unit = concept.unit
        elif concept.unit == "count":
            unit = "count"
        elif concept.unit == "currency":
            currency = re.search(r"\b(EUR|SEK|NOK|DKK|USD|GBP)\b", prefix, re.I)
            unit = currency.group(1).upper() if currency else None
        elif concept.unit == "money":
            unit = _currency_unit(prefix) or header_unit
        if not unit:
            continue
        # Table cells are separated by runs of >=2 spaces in fitz sort=True.
        # A single space *within* a cell can be a thousands separator.
        cells = re.split(r"\s{2,}", tail)
        if len(cells) != ncolumns:
            # Support simple flattened fixture/extractor lines with distinct
            # comma-grouped numbers and single-space separators.
            cells = tail.split()
        if len(cells) != ncolumns or col >= len(cells):
            continue
        if unit != "percent" and any(x.endswith("%") for x in cells):
            continue
        values = [_parse_value(x, unit == "percent") for x in cells]
        if any(x is None for x in values):
            continue
        return values[col], unit, alias, cells[col]
    return None


def _is_financial_row(line: str) -> bool:
    x = line.strip().casefold()
    if any(x.startswith(alias.casefold()) and
           (len(x) == len(alias) or not x[len(alias)].isalpha())
           for metric in BY_KEY for alias in _row_labels(metric)):
        return True
    # A first line may contain only the beginning of a long known row label
    # (e.g. "Assets in pooled schemes and unit-linked"). This does not
    # extract a value by itself; generic_cell still requires the rest of the
    # exact alias plus every numeric column on the continuation line.
    return any(alias.casefold().startswith(x + " ")
               for metric in BY_KEY for alias in _row_labels(metric)
               if len(x) >= 12)


def _table_candidates(lines: list[str]):
    """Yield (all column IDs, first row, final row, currency/header context)."""
    seen = set()
    n = len(lines)
    for i, line in enumerate(lines):
        qrow = line.strip()
        # Option A: stacked Q3 Q2 Q1 / SEK m 2026 2026 2026.
        qcount = len(_Q.findall(qrow)) + len(_COMPACT.findall(qrow))
        if qcount >= 2 and (len(qrow) < 180 or _stacked_columns(qrow, next((lines[j] for j in range(i+1, min(i+4,n)) if lines[j].strip()), ""))):
            nextline = next((lines[j] for j in range(i+1, min(i+4,n)) if lines[j].strip()), "")
            if len(_YEAR.findall(nextline)) >= qcount or _stacked_columns(qrow, nextline):
                columns = _aligned_columns(qrow, nextline)
                end_header = next((j for j in range(i+1,min(i+4,n)) if lines[j] == nextline), i+1)
            else:
                # Option B: 'SEKm Q3 2026 Q2 2026' all on one line.
                columns = _aligned_columns(qrow)
                end_header = i
        elif re.match(r"^\s*(?:EUR|SEK|NOK|DKK|USD|GBP)\s*(?:m|bn)\b", line, re.I):
            # Option C: header-unit line *before* a separate Q heading.
            nxt = next((j for j in range(i+1,min(i+5,n)) if len(_Q.findall(lines[j])) >= 2), None)
            if nxt is None:
                continue
            qline = lines[nxt].strip()
            yline = next((lines[j] for j in range(nxt+1,min(nxt+4,n)) if lines[j].strip()), "")
            columns = _aligned_columns(qline, yline if _YEAR.search(yline) else "")
            end_header = nxt + (1 if _YEAR.search(yline) else 0)
        else:
            continue
        if len(columns) < 2 or (i, tuple(columns)) in seen:
            continue
        seen.add((i, tuple(columns)))
        # A table must have a financial row shortly after its header.
        first = next((j for j in range(end_header+1, min(end_header+20,n))
                      if _is_financial_row(lines[j])), None)
        if first is None:
            continue
        context_start = i
        # Only use the immediately preceding currency/unit header, never an
        # earlier unrelated metric row (SEK bn vs SEK m).
        if i > 0 and re.match(r"^\s*(?:EUR|SEK|NOK|DKK|USD|GBP)\s*(?:m|bn)\s*$", lines[i-1], re.I):
            context_start = i-1
        context = "\n".join(lines[context_start:end_header+1])
        # A new recognized table heading terminates this one.
        limit = min(n, first+85)
        for j in range(first+1,limit):
            if re.search(r"\b(?:income statement|key figures|balance sheet)\b.*\b(?:quarters?|condensed)\b", lines[j], re.I):
                limit = j
                break
        yield columns, first, limit, context


def generic_cell(metric: str, period: str, document_id: str, *, scope: str = "group") -> FinancialCell | None:
    """Return only uniquely verifiable quarterly group metrics from *one* PDF.

    Duplicate identical values on multiple pages are acceptable. Conflicting
    values or units in validated tables force abstention. Prefer an exact
    income-statement/key-figures table over an executive-summary table when
    reporting otherwise-identical figures.
    """
    if scope != "group" or not re.fullmatch(r"Q[1-4]\d{2}", period) or metric not in BY_KEY:
        return None
    concept = BY_KEY[metric]
    if concept.table not in {"income", "balance", "capital", "ratios", "rea", "lcr"}:
        return None
    matches = []
    pages = _get_pages(document_id)
    # Header-aware reader is an additive, issuer-neutral path for explicit
    # GROUP tables; older SEB/Nordea adapters remain the fallback. It never
    # loads another document ID and rejects differing values across matches.
    from app.layout_table_reader import aligned_group_cell
    aligned = aligned_group_cell(metric, period, pages, scope=scope)
    # Keep scanning all independently parseable statement layouts even when
    # the header-aware reader found a value. A second valid table with a
    # contradictory value is an ambiguity, not something to hide by returning
    # the first parser's answer. This is especially important when a page has
    # a separate compact summary table and a full quarterly statement.
    # Exclude parent-company statements for every issuer, not just SEB.
    # Some reports repeat the same row label with a different parent-company
    # value; those rows must never make an otherwise valid Group cell ambiguous.
    parent_patterns = (
        r"^\s*skandinaviska enskilda banken.*parent company\b",
        r"^\s*condensed set of financial statements\s*[–—-]\s*parent\s*company\b",
        r"^\s*(?:income statement|balance sheet)\s*[–—-]\s*parent\s*company\b",
    )
    parent_pages = []
    for page in pages:
        opening = " ".join(line.strip() for line in page["text"].splitlines()[:5] if line.strip())
        if any(re.search(pattern, opening, re.I) for pattern in parent_patterns):
            parent_pages.append(page["page"])
    parent_start = min(parent_pages) if parent_pages else None
    for page in pages:
        if parent_start is not None and page["page"] >= parent_start:
            continue
        # Nordea factbooks repeat Group row names in each BUSINESS AREAS
        # section. Those are not consolidated figures and must never compete
        # with a Group cell or make a correct Group value appear ambiguous.
        if re.search(r"\bBUSINESS\s+AREAS\b", page["text"][:900], re.I):
            continue
        if concept.table == "balance":
            if not (re.search(r"\bbalance sheet, (?:12-quarter overview|condensed)", page["text"][:1400], re.I)):
                continue
        lines = page["text"].splitlines()
        for columns, first, last, context in (*_table_candidates(lines), *_date_table_candidates(lines)):
            if period not in columns:
                continue
            index = columns.index(period)
            header_unit = _currency_unit(context)
            for i in range(first,last):
                row = _extract_row(lines[i],metric,len(columns),index,header_unit)
                # PDF layout engines can split a long row label (not its
                # numeric values) over two lines. Join only when the first
                # line is a prefix of one of this metric's literal source
                # labels; the complete exact label and full numeric column
                # count are still required by _extract_row.
                if row is None and i+1 < last and _is_financial_row(lines[i]):
                    row = _extract_row(lines[i].strip()+" "+lines[i+1].strip(),metric,
                                       len(columns),index,header_unit)
                if row is None:
                    continue
                value, unit, label, raw = row
                matches.append(FinancialCell(metric=metric,period=period,scope=scope,
                                             value=value,unit=unit,document=page["document"],
                                             page=page["page"],row_label=label,raw_value=raw))
    if aligned is not None:
        # Cross-check other parsable layouts on the same physical page. This
        # catches a contradictory second table printed beside/below the
        # aligned statement (e.g. two operating-profit tables on one page),
        # without treating an executive recap on another page as a competing
        # Group statement. The aligned reader independently checks all pages
        # of the relevant detailed table type.
        same_page = [m for m in matches if m.page == aligned.page]
        if any((m.value, m.unit) != (aligned.value, aligned.unit) for m in same_page):
            return None
        return aligned
    if not matches:
        return None
    # A disagreement between fallback tables is not repaired by selecting a
    # convenient page.
    if len({(x.value,x.unit) for x in matches}) != 1:
        return None
    # Prefer the dedicated, more detailed table when value/unit agree.
    def _source_priority(cell: FinancialCell) -> tuple[int, int, int]:
        page_text = next((p["text"] for p in pages
                          if p["document"] == cell.document and p["page"] == cell.page), "")
        detailed = bool(re.search(r"quarterly performance\s*[–—-]\s*group|income statement\s*[–—-]\s*group", page_text[:900], re.I))
        exact_label = cell.row_label.casefold() == BY_KEY[metric].rows[0].casefold()
        return (0 if detailed else 1, 0 if exact_label else 1, cell.page)
    return sorted(matches, key=_source_priority)[0]


def _date_table_candidates(lines: list[str]):
    """Recognize *unambiguous* quarterly end-of-period balance-sheet dates.

    Only 30/31 Mar/Jun/Sep/Dec are allowed; no approximate month inference.
    Align against the following year row and require the same column count.
    """
    mon_q={"mar":1,"jun":2,"sep":3,"dec":4}
    for i, line in enumerate(lines[:-1]):
        matches=list(re.finditer(r"\b(30|31)\s+(Mar|Jun|Sep|Dec)\b",line,re.I))
        if len(matches)<2 or len(matches)>10:
            continue
        y=lines[i+1]
        years=_YEAR.findall(y)
        if len(years)!=len(matches):
            continue
        cols=[f"Q{mon_q[m[2].casefold()]}{year[-2:]}" for m,year in zip(matches,years)]
        if len(set(cols))!=len(cols):
            continue
        first=next((j for j in range(i+2,min(i+14,len(lines))) if _is_financial_row(lines[j])),None)
        if first is None:
            continue
        curr="\n".join(lines[max(0,i-1):i+2])
        limit=min(len(lines),first+85)
        for j in range(first+1, limit):
            if re.search(r"\b(?:Income statement|Cash flow statement|Notes to|Definitions)\b",lines[j],re.I):
                limit=j
                break
        yield cols,first,limit,curr


# Dedicated segment report pages use a repeated quarterly table layout. The
# page title is the scope guard; the table header maps quarter columns before
# any values are read. This supports SEB's interleaved percentage columns and
# Handelsbanken's quarter/Change columns without falling back to Group rows.
_REPORT_SEGMENT_TITLES = {
    "corporate & investment banking": ("Corporate & Investment Banking", "Corporate and Investment Banking"),
    "business & retail banking": ("Business & Retail Banking", "Business and Retail Banking"),
    "wealth & asset management": ("Wealth & Asset Management", "Wealth and Asset Management"),
    "baltic": ("Baltic",),
    "handelsbanken sweden": ("Handelsbanken Sweden",),
    "handelsbanken uk": ("Handelsbanken UK",),
    "handelsbanken norway": ("Handelsbanken Norway",),
    "handelsbanken the netherlands": ("Handelsbanken the Netherlands",),
    "handelsbanken markets": ("Handelsbanken Markets",),
}


def _extract_interleaved_row(line: str, metric: str, ncolumns: int, column: int,
                             header_unit: str | None):
    """Read one quarter from tables whose Change/% columns are interleaved.

    All cells must be numeric, but non-target Change columns may be percentages
    (or unmarked percentage deltas in SEB's table). The selected period index is
    derived from the header, so only its actual amount is returned.
    """
    for alias in sorted(_row_labels(metric), key=len, reverse=True):
        found = _label_tail(line, alias)
        if found is None:
            continue
        tail, prefix = found
        cells = [cell.strip() for cell in re.split(r"\s{2,}", tail.strip()) if cell.strip()]
        unit = _currency_unit(prefix) or header_unit
        if not unit:
            continue
        if len(cells) == ncolumns and column < len(cells):
            if cells[column].endswith("%"):
                continue
            if any(_parse_value(cell, cell.endswith("%")) is None for cell in cells):
                continue
            value = _parse_value(cells[column], False)
            if value is not None:
                return value, unit, alias, cells[column]
        # A few issuer tables omit an empty Change column from the extracted
        # text. When the requested period is the *first* amount column, its
        # position is still anchored by the row label and header. Accept only
        # that first numeric token; never shift a later quarter into place.
        if column == 0:
            first = re.match(r"^([+-]?(?:\d{1,3}(?: \d{3})+|\d+)(?:\.\d+)?)\b", tail.strip())
            if first:
                value = _parse_value(first.group(1), False)
                if value is not None:
                    return value, unit, alias, first.group(1)
    return None


def report_segment_cell(metric: str, period: str, document_id: str, scope: str,
                        country: str | None = None) -> FinancialCell | None:
    """Read one cell from the requested bank-division page, never Group scope.

    Only dedicated division pages whose first non-empty line matches the
    requested scope are eligible. The quarter/year header and interleaved
    Change/% columns are mapped before reading the exact financial row.
    Unsupported country sub-scopes, periods, or ambiguous matches abstain.
    """
    if country or scope not in _REPORT_SEGMENT_TITLES:
        return None
    if metric not in BY_KEY or not re.fullmatch(r"Q[1-4]\d{2}", period):
        return None
    aliases = _REPORT_SEGMENT_TITLES[scope]
    matches: list[FinancialCell] = []
    for page in _get_pages(document_id):
        lines = page["text"].splitlines()
        opening = next((line.strip() for line in lines if line.strip()), "")
        if not any(re.fullmatch(re.escape(alias), opening, re.I) for alias in aliases):
            continue
        income_idx = next((i for i, line in enumerate(lines[:15])
                           if re.fullmatch(r"\s*income\s+statement\s*", line, re.I)), None)
        if income_idx is None:
            continue
        q_idx = next((i for i in range(income_idx + 1, min(income_idx + 5, len(lines)))
                      if len(_Q.findall(lines[i])) >= 2), None)
        if q_idx is None or q_idx + 1 >= len(lines):
            continue
        q_labels = [int(q) for q in _Q.findall(lines[q_idx])]
        year_line = lines[q_idx + 1]
        year_matches = list(_YEAR.finditer(year_line))
        if len(q_labels) < 2 or len(year_matches) < len(q_labels):
            continue
        prefix_count = len(year_line[:year_matches[0].start()].split())
        year_positions = [len(year_line[:m.start()].split()) - prefix_count for m in year_matches]
        column_map = {
            f"Q{q}{year_matches[i].group(0)[-2:]}" : year_positions[i]
            for i, q in enumerate(q_labels)
        }
        column_count = len(year_line.split()) - prefix_count
        column = column_map.get(period)
        if column is None or column >= column_count:
            continue
        unit_context = "\n".join(lines[max(0, q_idx - 1):q_idx + 2])
        header_unit = _currency_unit(unit_context)
        if not header_unit:
            continue
        for i in range(q_idx + 2, min(len(lines), q_idx + 90)):
            if re.fullmatch(r"\s*(?:The third quarter|Business development|Q3 \d{4} compared with Q2 \d{4})\s*", lines[i], re.I):
                break
            if metric == "operating_profit" and re.search(r"\boperating\s+profit\s+before\b", lines[i], re.I):
                continue
            row = _extract_interleaved_row(lines[i], metric, column_count, column, header_unit)
            if row is None and i + 1 < len(lines) and _is_financial_row(lines[i]):
                row = _extract_interleaved_row(lines[i].strip() + "  " + lines[i + 1].strip(),
                                               metric, column_count, column, header_unit)
            if row is None:
                continue
            value, unit, label, raw = row
            matches.append(FinancialCell(
                metric=metric, period=period, scope=scope, value=value, unit=unit,
                document=page["document"], page=page["page"],
                row_label=label, raw_value=raw,
            ))
            break
    unique = {(cell.value, cell.unit, cell.document, cell.page): cell for cell in matches}
    if not unique or len({(cell.value, cell.unit) for cell in unique.values()}) != 1:
        return None
    return sorted(unique.values(), key=lambda cell: cell.page)[0]


def segment_volume_cell(metric: str, period: str, document_id: str, scope: str):
    """Nordea-style business-area EURbn 'Volumes' table; no group substitution."""
    if metric not in {'segment_total_deposits', 'segment_total_lending'} or scope not in {'personal banking','business banking'}:
        return None
    row_label = 'Total deposits' if metric == 'segment_total_deposits' else 'Total lending'
    matches=[]
    for page in _get_pages(document_id):
        text=page['text']
        if not re.search(r'BUSINESS\s+AREAS\s+'+re.escape(scope), ' '.join(text.split()),re.I):
            continue
        # The factbook repeats segment names on detailed country/product pages.
        # Only the main segment page has both the Financial highlights block
        # and the consolidated Volumes table; detail pages may repeat "Total
        # lending" for individual components and are not interchangeable.
        if not re.search(r'\bFinancial highlights\b', text, re.I):
            continue
        found=re.search(r'\bVolumes\b',text,re.I)
        if found is None:
            continue
        lines=text[found.start():].splitlines()
        # Real factbook PDFs commonly split the table unit and quarter labels
        # across two physical text lines ("EURbn" then "Q326 Q226 Q126").
        # Accept either a combined header or a unit line followed immediately
        # by a quarter-only line; never scan beyond this short header window.
        header_index = next((i for i, line in enumerate(lines[:8])
                             if re.search(r"(?<!\w)EUR\s*bn\b", line, re.I)), None)
        if header_index is None:
            continue
        header = lines[header_index]
        period_header = header
        if not _quarter_columns(period_header):
            period_header = next((line for line in lines[header_index + 1:header_index + 4]
                                  if _quarter_columns(line)), "")
        cols = _quarter_columns(period_header)
        if not cols or period not in cols:
            continue
        scan = lines[header_index + 1:header_index + 100]
        for line in scan:
            if re.match(r'^\s*(?:Financial highlights|Business development|Restatements)\b', line, re.I):
                break
            if not re.match(r'^\s*' + re.escape(row_label) + r'\b',line,re.I):
                continue
            tail=re.sub(r'^\s*' + re.escape(row_label) + r'\b','',line,flags=re.I).strip()
            cells=[cell.strip() for cell in re.split(r'\s{2,}',tail) if cell.strip()]
            # Some extracted Nordea volume rows collapse column padding to one
            # space. These EURbn values are decimals, so a strict token-count
            # fallback is safe; a mismatch still fails closed.
            if len(cells) < len(cols):
                cells=tail.split()
            # Nordea Volumes appends growth columns after the quarter amounts.
            # The seven explicit Q-codes map to the first seven values only.
            if len(cells) < len(cols):
                continue
            amount_cells = cells[:len(cols)]
            if not all(_parse_value(cell, False) is not None for cell in amount_cells):
                continue
            val=_parse_value(amount_cells[cols.index(period)],False)
            if val is not None:
                matches.append(FinancialCell(metric=metric,period=period,scope=scope,
                    value=val,unit='EURbn',document=page['document'],page=page['page'],
                    row_label=row_label,raw_value=amount_cells[cols.index(period)]))
    unique={(c.document,c.page,c.value,c.unit):c for c in matches}
    return next(iter(unique.values())) if len(unique)==1 else None
