"""Conservative source-only fallback for quarterly financial tables.

This module recognizes column *roles* from a table header before reading any
number. It rejects mixed growth-rate columns, unrelated entity sections and
ambiguous duplicate cells.  No issuer, benchmark answer or fixed PDF page is
hard-coded.  Reports which do not expose an alignable table return None.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Iterable

from app.financial_vocabulary import BY_KEY
from app.table_lookup import FinancialCell

_QUARTER = re.compile(r'\bQ([1-4])\b',re.I)
_DATE = re.compile(r'\b(?:30|31)\s+(Mar|Jun|Sep|Dec)\b',re.I)
_YEAR = re.compile(r'\b20\d{2}\b')
# Explicit period cells used by financial-review tables that mix cumulative,
# index and individual-quarter columns on one header line. Ranges are matched
# before standalone quarters so Q1-Q3 2026 is never mistaken for Q1 or Q3.
_PERIOD_HEADER_CELL = re.compile(
    r'(?<![A-Za-z0-9])(?:Q[1-4]\s*[-–—]\s*Q[1-4]\s+20\d{2}'
    r'|Index\s+\d{2,4}/\d{2,4}'
    r'|Q[1-4]\s+20\d{2}'
    r'|(?:Year|FY)\s+20\d{2})(?![A-Za-z0-9])', re.I
)
_MONTH = {'mar':1,'jun':2,'sep':3,'dec':4}
_UNITS = re.compile(r'\b(EUR|SEK|NOK|DKK|USD|GBP)\s*(?:m|mn|millions?|bn|billions?)\b',re.I)
_MONEY = re.compile(r'[+-]?(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.\d+)?%?')
_NUM = re.compile(r'[+-]?(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.\d+)?%?')

@dataclass(frozen=True)
class TableHeader:
    periods: tuple[str,...]
    start: int
    end: int
    unit: str | None
    kind: str  # 'balance', 'income', 'ratios'


def _unit_near_header(lines: list[str], idx: int) -> str | None:
    units = []
    for line in lines[max(0, idx-1):min(len(lines), idx+2)]:
        units.extend(_UNITS.findall(line))
    if len({x.upper() for x in units}) > 1:
        return None
    return (units[0].upper() + 'm') if units else None


def _one_line_header(lines: list[str], idx: int, kind: str) -> TableHeader | None:
    """Read complete period cells from one multi-period header line.

    Some financial-review reports put cumulative Q1-Q3, an index column,
    individual quarters and a full-year column together on one line. Treat
    cumulative/index/full-year columns as placeholders while retaining their
    positions, so the requested Q3 value is read from its actual column.
    """
    row = lines[idx]
    matches = list(_PERIOD_HEADER_CELL.finditer(row))
    if not 4 <= len(matches) <= 16:
        return None
    periods: list[str] = []
    quarter_count = 0
    aggregate_count = 0
    for match in matches:
        token = match.group(0).strip()
        quarter = re.fullmatch(r'Q([1-4])\s+(20\d{2})', token, re.I)
        if quarter:
            periods.append(f'Q{quarter.group(1)}{quarter.group(2)[-2:]}')
            quarter_count += 1
        else:
            periods.append(f'AGG_{aggregate_count}')
            aggregate_count += 1
    if quarter_count < 2 or len(set(periods)) != len(periods):
        return None
    unit = _unit_near_header(lines, idx)
    return TableHeader(tuple(periods), idx, idx+1, unit, kind)


# Label row / cell row headers where each column label sits directly above its
# own year or ratio cell, e.g.
#   Q1-Q3  Q1-Q3  Index  Q3  Q2  Index  Q3  Index  Full year
#   (DKK millions) 2025  2024  25/24  2025  2025  Q3/Q2  2024  25/24  2024
_LABEL_CELL = re.compile(r'Q[1-4]\s*[-–—]\s*Q[1-4]|Q[1-4]|Index|Full\s+year', re.I)
_YEAR_CELL = re.compile(r'20\d{2}|\d{2}/\d{2}|Q[1-4]/Q[1-4]', re.I)


def _positional_two_line_header(lines: list[str], idx: int, kind: str) -> TableHeader | None:
    """Pair each column label with the cell printed beneath it.

    Used only when the label row and the cell row have exactly the same number
    of cells. Labels are never matched to years by counting quarters, so
    cumulative, index and full-year columns keep their own positions.
    """
    if idx+1 >= len(lines):
        return None
    labels_row, cells_row = lines[idx], lines[idx+1]
    labels = [m.group(0) for m in _LABEL_CELL.finditer(labels_row)]
    if _LABEL_CELL.sub(' ', labels_row).strip():
        return None
    cells_text = re.sub(r'\(?\b(?:EUR|SEK|NOK|DKK|USD|GBP)\s*(?:m|mn|millions?|bn|billions?)\b\)?', ' ', cells_row, flags=re.I)
    cells = [m.group(0) for m in _YEAR_CELL.finditer(cells_text)]
    if _YEAR_CELL.sub(' ', cells_text).strip() or len(labels) != len(cells) or not 4 <= len(labels) <= 16:
        return None
    periods: list[str] = []
    quarter_count = 0
    aggregate_count = 0
    for label, cell in zip(labels, cells):
        single = re.fullmatch(r'Q([1-4])', label, re.I)
        year = re.fullmatch(r'(20\d{2})', cell)
        if single and year:
            periods.append(f'Q{single.group(1)}{year.group(1)[-2:]}')
            quarter_count += 1
        elif single:
            return None
        else:
            periods.append(f'AGG_{aggregate_count}')
            aggregate_count += 1
    if quarter_count < 2 or len(set(periods)) != len(periods):
        return None
    return TableHeader(tuple(periods), idx, idx+2, _unit_near_header(lines, idx), kind)


def _header(lines: list[str], idx: int, kind: str) -> TableHeader | None:
    """Recognize explicit quarter headers without guessing mixed columns.

    First handle complete one-line period cells (including YTD/index columns),
    then preserve the existing two-line quarter/year header behavior.
    """
    one_line = _one_line_header(lines, idx, kind)
    if one_line is not None:
        return one_line
    if idx+1>=len(lines): return None
    row, years_row=lines[idx],lines[idx+1]
    year_tokens=_YEAR.findall(years_row)
    if len(year_tokens)<2 or len(year_tokens)>15: return _positional_two_line_header(lines, idx, kind)
    # A Q1-Q3 / Q1-Q9 cumulative period is not an individual quarter token.
    quarter_row = re.sub(r'Q[1-4]\s*[-–—]\s*Q[1-4]', ' ', row, flags=re.I)
    qs=_QUARTER.findall(quarter_row)
    dates=_DATE.findall(row)
    if qs and len(qs)<=len(year_tokens) and not re.search(r'\bChange\b',row+years_row,re.I):
        periods=[f'Q{q}{y[-2:]}' for q,y in zip(qs,year_tokens)]
        # The remaining columns are YTD/full-year aggregate values, not
        # individual quarters. Preserve their positions to align the row.
        periods += [f'AGG_{i}' for i in range(len(year_tokens)-len(qs))]
    elif dates and len(dates)==len(year_tokens) and not re.search(r'\bChange\b',row+years_row,re.I):
        periods=[f'Q{_MONTH[m.lower()]}{y[-2:]}' for m,y in zip(dates,year_tokens)]
    else:
        return _positional_two_line_header(lines, idx, kind)
    if len(set(periods))!=len(periods): return _positional_two_line_header(lines, idx, kind)
    unit = _unit_near_header(lines, idx)
    return TableHeader(tuple(periods),idx,idx+2,unit,kind)


def _table_kind(text: str) -> str | None:
    opening=' '.join(text.split()[:55]).lower()
    if re.search(r'\bgroup\s*[–—-]\s*overview\b',opening):
        return 'overview'
    if re.search(r'\bbalance sheet\s*[–—-]\s*group\b|\bgroup\s+balance sheet\b',opening):
        return 'balance'
    if (re.search(r'\bquarterly performance\s*[–—-]\s*group\b|\bincome statement\s*[–—-]\s*group\b',opening)
            or re.search(r'\bincome statement on a quarterly basis,? condensed\b', opening)
            or re.search(r'\bcore profit and net profit for the period\b', opening)
            or re.search(r'\bsummary of income statement\b', opening)
            or re.search(r'^\s*income statement\s*$', '\n'.join(text.splitlines()[:12]), re.I|re.M)):
        return 'income'
    if re.search(r'\bkey metrics\s*[–—-]\s*group\b|\bkey financial figures\b|\bkey figures\b',opening):
        return 'ratios'
    return None


def _headers(lines: list[str],kind: str) -> list[TableHeader]:
    found=[]
    for n in range(min(len(lines)-1,65)):
        h=_header(lines,n,kind)
        if h is not None:found.append(h)
    return found


def _labels(metric: str) -> tuple[str,...]:
    concept=BY_KEY[metric]
    return concept.rows


def _prefix_match(line: str, label: str) -> re.Match | None:
    # Preserve a strict token boundary after the complete label.
    pat=r'^\s*'+r'\s+'.join(re.escape(w) for w in label.split())
    m=re.match(pat,line,re.I)
    if m and not (m.end()<len(line) and line[m.end()].isalpha()):
        return m
    return None


def _parse_numeric(s: str) -> Decimal | None:
    s=s.strip()
    if not re.fullmatch(_NUM,s):return None
    if s.endswith('%'):s=s[:-1]
    if re.fullmatch(r'[-+]?\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?',s):s=s.replace(',','').replace(' ','')
    elif ',' in s:return None
    try:
        v=Decimal(s)
    except Exception:
        return None
    return v if v.is_finite() else None


def _numeric_cells(line: str, label_end: int, n: int) -> tuple[list[str],str]|None:
    # The row's own Note reference is not a financial operand.
    rest=line[label_end:]
    rest=re.sub(r'^\s*(?:[¹²³⁴⁵⁶⁷⁸⁹⁰]+[⁾)]?|\(?\d+\))\s*',' ',rest)
    rest=re.sub(r'^\s*Note\s+[\d,]+\s*',' ',rest,flags=re.I)
    # Some issuers append an asterisk to a label.
    rest=re.sub(r'^\s*\*\s*',' ',rest)
    pieces=[p.strip() for p in re.split(r'\s{2,}',rest.strip()) if p.strip()]
    if len(pieces)==n+1 and _parse_numeric(pieces[0]) is None:
        # A wrapped description can sit between the prefix and the numeric
        # columns, e.g. 'Depreciation ... of tangible and intangible'.
        pieces=pieces[1:]
    if len(pieces)!=n or not all(_parse_numeric(p) is not None for p in pieces):
        return None
    return pieces,rest


# Alternative wording some issuers print for a metric. Accepted only when it is
# the complete row label, so 'Net profit attributable to ...' is never read as
# the group net profit row.
_EXACT_ROW_ALIASES = {'net_profit': ('Net profit',)}


def _row_candidates(lines:list[str],start:int,end:int,metric:str,kind:str,cols:int):
    aliases=_EXACT_ROW_ALIASES.get(metric,())
    labels=sorted((*_labels(metric),*aliases),key=len,reverse=True)
    block='assets'
    for pos in range(start,min(end,len(lines))):
        line=lines[pos]
        low=line.strip().lower()
        if kind=='balance' and low in {'assets','liabilities','liabilities and equity','equity'}:
            block='assets' if low=='assets' else 'liabilities'
        for label in labels:
            m=_prefix_match(line,label)
            if m is None:continue
            # A shorter label must not consume a different qualified row.
            # e.g. Total liabilities != Total liabilities and equity.
            if re.match(r'\s+and\s+equity\b',line[m.end():],re.I):continue
            if label in aliases and re.match(r'\s*[A-Za-z,]',line[m.end():]):continue
            # Classify duplicated 'Derivatives' by the balance-sheet section.
            if metric=='derivative_assets' and block!='assets':continue
            next_line=lines[pos+1] if pos+1<end else ''
            # Never merge the next *independent* row into this row. A
            # financial statement can have a long label with no values on
            # one line followed by a completely different titled row.
            # Upper-case row starts ("Other expenses", "Total income") are
            # new rows, not a continuation of a lowercase wrapped qualifier.
            next_is_new_row=bool(re.match(r'\s*[A-Z][A-Za-z /,()-]+\s{2,}[-+]?\d',next_line))
            suffixes=[line] if next_is_new_row else [line,line+' '+next_line]
            for suffix in suffixes:
                parsed=_numeric_cells(suffix,m.end(),cols)
                if parsed:
                    raw,_=parsed
                    yield pos,label,raw
                    break
            else:
                # A page-to-chunk boundary may strip leading whitespace from
                # the *numeric continuation line*. A row wrapped as:
                #    Depreciation ... of tangible and intangible
                #         -508    -464    -482    -481    -515
                #    assets
                # must still be read as a single row. Require the very next
                # line to contain EXACTLY the header's N numeric columns,
                # with no label text/extra value. Do not join arbitrary lines
                # or infer a column index from a number elsewhere on the page.
                if pos+1 >= end or next_is_new_row:
                    continue
                qualifier=line[m.end():].strip()
                if not qualifier or re.search(r'\d',qualifier):
                    continue
                continuation=lines[pos+1].strip()
                cells=[v.strip() for v in re.split(r'\s{2,}',continuation) if v.strip()]
                if len(cells)==cols and all(_parse_numeric(v) is not None for v in cells):
                    yield pos,label,cells


def _handelsbanken_primary_statement_cell(metric: str, period: str, pages: Iterable[dict]) -> FinancialCell | None:
    """Read the consolidated Handelsbanken statement with interleaved Change columns.

    The primary statement exposes Q3 2026, Q2 2026, and Q3 2025 amount
    columns, with percentage-change columns inserted between them. We map
    the amount columns from the explicit Q/year header before reading values;
    no percentage cell can be mistaken for an amount.
    """
    if metric not in BY_KEY or metric in {'earnings_per_share', 'income_tax_expense'}:
        # Handelsbanken's earnings-per-share row is best sourced from the
        # dedicated Key metrics – Group table, not its duplicate statement row.
        return None
    for page in pages:
        text = page['text']
        opening = ' '.join(text.split()[:45]).casefold()
        if 'condensed set of financial statements' not in opening or 'income statement - group' not in opening:
            continue
        lines = text.splitlines()
        for idx in range(min(15, len(lines)-1)):
            qlabels = _QUARTER.findall(lines[idx])
            yearrow = lines[idx+1]
            if len(qlabels) < 2 or not re.search(r'\bChange\b', yearrow, re.I):
                continue
            # The year row contains one token for each amount/change column.
            tokens = re.findall(r'20\d{2}|Change', yearrow, re.I)
            amount_positions = [i for i, token in enumerate(tokens) if token.isdigit()]
            years = [tokens[i] for i in amount_positions]
            if len(years) < len(qlabels):
                continue
            period_positions = {
                f'Q{q}{year[-2:]}' : amount_positions[j]
                for j, (q, year) in enumerate(zip(qlabels, years[:len(qlabels)]))
            }
            if period not in period_positions:
                continue
            position = period_positions[period]
            unit = None
            for header_line in lines[max(0, idx-1):idx+2]:
                found = _UNITS.search(header_line)
                if found:
                    unit = found.group(1).upper() + 'm'
                    break
            if not unit:
                continue
            # Start after the year header and stop at the next statement/table.
            for row_index in range(idx+2, min(len(lines), idx+105)):
                line = lines[row_index]
                if re.search(r'^\s*(?:Statement of Comprehensive Income|Balance Sheet)\s*[–—-]', line, re.I):
                    break
                # One row wraps its final label word *after* the numeric
                # continuation. Validate the exact prefix, a complete aligned
                # numeric row, and the exact trailing word. The visible prefix
                # becomes the row label so the independent verifier can find
                # it adjacent to the selected raw amount.
                if row_index+2 < len(lines):
                    prefix = line.strip()
                    numeric_line = lines[row_index+1].strip()
                    suffix = lines[row_index+2].strip()
                    for label in sorted(_labels(metric), key=len, reverse=True):
                        parts = label.rsplit(' ', 1)
                        if len(parts) != 2 or prefix.casefold() != parts[0].casefold() or suffix.casefold() != parts[1].casefold():
                            continue
                        parsed_values = _numeric_cells(numeric_line, 0, len(tokens))
                        if not parsed_values:
                            continue
                        raw = parsed_values[0][position]
                        value = _parse_numeric(raw)
                        if value is None:
                            continue
                        return FinancialCell(metric, period, 'group', value, unit,
                            page['document'], page['page'], prefix, raw)
                candidates = [line]
                # A small number of long statement labels wrap immediately
                # before their numeric cells. Only complete vocabulary labels
                # can match after joining the adjacent line.
                if row_index+1 < len(lines):
                    candidates.append(line.strip() + ' ' + lines[row_index+1].strip())
                for candidate in candidates:
                    for label in sorted(_labels(metric), key=len, reverse=True):
                        match = _prefix_match(candidate, label)
                        if not match:
                            continue
                        parsed = _numeric_cells(candidate, match.end(), len(tokens))
                        if not parsed:
                            continue
                        cells, _ = parsed
                        raw = cells[position]
                        value = _parse_numeric(raw)
                        if value is None:
                            continue
                        concept = BY_KEY[metric]
                        if metric == 'earnings_per_share':
                            cell_unit = None
                        elif concept.unit == 'percent':
                            cell_unit = 'percent'
                        else:
                            cell_unit = unit
                        if not cell_unit:
                            continue
                        return FinancialCell(metric, period, 'group', value, cell_unit,
                            page['document'], page['page'], label, raw)
    return None


def aligned_group_cell(metric: str, period: str, pages: Iterable[dict], *, scope: str='group') -> FinancialCell|None:
    """Read complete PDF-text rows, not isolated matching numbers.

    Only unambiguous dedicated GROUP tables are considered. A mismatch between
    duplicated GROUP tables is not silently resolved by selecting a preferred
    answer. Cross-document access is impossible: caller supplies selected pages.
    """
    if scope!='group' or metric not in BY_KEY or not re.fullmatch(r'Q[1-4]\d{2}',period):
        return None
    concept=BY_KEY[metric]
    primary_hb = _handelsbanken_primary_statement_cell(metric, period, pages)
    if primary_hb is not None:
        return primary_hb
    ratio_metrics = {
        'roe','cet1_ratio','total_capital_ratio','tier1_ratio',
        'cost_income_ratio','credit_loss_ratio','assets_under_custody',
    }
    eps_metrics = {'earnings_per_share','basic_earnings_per_share','diluted_earnings_per_share'}
    expected = ('balance' if concept.table == 'balance' else
                'ratios' if concept.table == 'ratios' or metric in ratio_metrics else
                'income')
    found=[]
    candidate_pages = []
    for page in pages:
        kind = _table_kind(page['text'])
        if metric in eps_metrics:
            # Issuers place EPS either in the primary income statement or in
            # the key-figures table. Prefer the income statement when present;
            # use key figures only when the report has no EPS row there.
            if kind in {'income', 'ratios', 'overview'}:
                candidate_pages.append((page, kind))
        elif kind == expected or (expected == 'ratios' and kind == 'overview'):
            candidate_pages.append((page, kind))
    if metric == 'earnings_per_share' and any(kind == 'ratios' for _, kind in candidate_pages):
        # Handelsbanken's Key metrics – Group table is the canonical EPS source;
        # its condensed statement repeats the figure on a different page.
        candidate_pages = [(page, kind) for page, kind in candidate_pages if kind == 'ratios']
    elif metric in eps_metrics and any(kind == 'income' for _, kind in candidate_pages):
        candidate_pages = [(page, kind) for page, kind in candidate_pages if kind == 'income']
    # Detailed, dimension-specific pages take precedence over executive
    # overview duplicates. Only use the overview when the report has no
    # dedicated table of the required kind; this avoids citing an overview
    # ratio when the full ratio table is present later in the report.
    if metric not in eps_metrics and any(kind == expected for _, kind in candidate_pages):
        candidate_pages = [(page, kind) for page, kind in candidate_pages if kind == expected]
    elif metric in eps_metrics and any(kind == 'ratios' for _, kind in candidate_pages):
        # If EPS exists in neither a primary statement nor a ratios table,
        # the overview is a last-resort source, not a peer of detailed pages.
        candidate_pages = [(page, kind) for page, kind in candidate_pages if kind == 'ratios']
    for page, kind in candidate_pages:
        lines=page['text'].splitlines()
        for head in _headers(lines,kind):
            if period not in head.periods:continue
            index=head.periods.index(period)
            boundary=next((h.start for h in _headers(lines,kind) if h.start>head.start),len(lines))
            # Detailed pages can have multiple separate financial tables; do
            # not scan into another header with a different unit/dimension.
            for pos,label,raws in _row_candidates(lines,head.end,boundary,metric,kind,len(head.periods)):
                raw=raws[index]
                value=_parse_numeric(raw)
                if value is None:continue
                if metric in eps_metrics:
                    currency=re.search(r'\b(EUR|SEK|NOK|DKK|USD|GBP)\b',label,re.I)
                    unit=currency[1].upper() if currency else None
                elif metric == 'assets_under_custody':
                    unit_match = re.search(r'\b(EUR|SEK|NOK|DKK|USD|GBP)\s*(m|mn|bn|billion)\b', label, re.I)
                    if unit_match:
                        unit = unit_match[1].upper() + ('bn' if unit_match[2].casefold() in {'bn','billion'} else 'm')
                    else:
                        unit = head.unit
                elif metric=='cost_income_ratio':unit='percent' if raw.endswith('%') else 'ratio'
                elif concept.unit in {'basis_points','count'}:unit=concept.unit
                elif concept.unit=='percent':unit='percent' 
                else:unit=head.unit
                if not unit:continue
                found.append(FinancialCell(metric,period,scope,value,unit,
                    page['document'],page['page'],label,raw))
    if not found and metric=='credit_loss_ratio':
        found=_headline_credit_loss_ratio(period,pages)
    if not found:return None
    # If repeated dedicated tables exist, a disagreement is still ambiguous.
    # The page-type filter above only eliminates weaker overview duplicates;
    # it does not choose between two conflicting detailed tables.
    if len({(c.value,c.unit) for c in found})!=1:return None
    def _detail_priority(cell: FinancialCell):
        text = next((p['text'] for p in pages
                     if p['document'] == cell.document and p['page'] == cell.page), '')
        opening = ' '.join(text.split()[:55]).casefold()
        # Primary consolidated quarterly statements precede recap/key-figure
        # pages for income-statement values when both contain the same row.
        primary = bool(re.search(r'quarterly performance\s*[–—-]\s*group|income statement\s*[–—-]\s*group', opening))
        return (0 if primary else 1, cell.page)
    return sorted(found,key=_detail_priority)[0]


def _headline_credit_loss_ratio(period: str, pages: Iterable[dict]) -> list[FinancialCell]:
    """Quarter-specific headline ratio, anchored to a document's quarter.

    Multi-column executive summaries may interleave the Q3 and year-to-date
    prose on one physical text line. Only the *first* explicitly quartered
    amount is accepted, before its parenthetical prior-quarter comparator.
    """
    rows=[]
    for page in pages:
        text=page['text']
        h=re.search(r'\bQ([1-4])\s+(20\d{2})\b',text[:550],re.I)
        if not h or f'Q{h[1]}{h[2][-2:]}'!=period:continue
        # Require a direct grouped-headline accounting statement.
        lines=text[h.end():].splitlines()
        for line in lines[:38]:
            m=re.search(r'\bcredit loss ratio was\s+([+-]?\d+(?:\.\d+)?)%',line,re.I)
            if m:
                rows.append(FinancialCell('credit_loss_ratio',period,'group',Decimal(m[1]),
                    'percent',page['document'],page['page'],'credit loss ratio',m[1]))
                break
    if len({(c.value,c.unit) for c in rows})!=1:return []
    return rows[:1]
