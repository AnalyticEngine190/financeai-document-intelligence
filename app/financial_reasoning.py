"""Pure Decimal arithmetic over verified PDF table cells. No language model.

The caller must provide every required operand, with each source cell retaining
its PDF page and dimension. Unsupported or incompatible comparisons fail closed.
"""
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass

from app.question_parser import FinancialIntent
from app.table_lookup import FinancialCell


@dataclass(frozen=True)
class ReasonedAnswer:
    answer: str
    sources: tuple[dict, ...]
    result: Decimal
    unit: str


def _num(value: Decimal) -> str:
    return f"{value:,.{max(0, -value.as_tuple().exponent)}f}"


def _amount(value: Decimal, unit: str) -> str:
    if unit == "EURm":
        return f"EUR {_num(value)} million"
    if unit == "EURbn":
        return f"EUR {_num(value)} billion"
    if unit == "percent":
        return f"{_num(value)}%"
    if unit == "count":
        return f"{_num(value)} employees (FTEs)"
    raise ValueError("Unsupported unit")


def _unique_sources(cells: list[FinancialCell]) -> tuple[dict, ...]:
    seen = set()
    out = []
    for cell in cells:
        key = (cell.document, cell.page)
        if key not in seen:
            out.append({"document": cell.document, "page": cell.page})
            seen.add(key)
    return tuple(out)


def _quarter_sort_key(code: str) -> tuple[int, int]:
    if code.startswith("Q") and len(code) == 4:
        return int("20" + code[2:]), int(code[1])
    if len(code) == 4 and code.isdigit():
        return int(code), 0
    raise ValueError("Unknown period")


def reason_over_cells(intent: FinancialIntent, cells: list[FinancialCell]) -> ReasonedAnswer:
    """Compute *only* over complete, dimensionally aligned source cells."""
    if not cells or any(c.metric != intent.metric for c in cells):
        raise ValueError("Missing or mismatched metric")
    if len({(c.metric, c.period, c.scope) for c in cells}) != len(cells):
        raise ValueError("Duplicate or ambiguous operands")
    units = {c.unit for c in cells}
    if len(units) != 1:
        raise ValueError("Incompatible units")
    unit = cells[0].unit
    if any(c.document != cells[0].document for c in cells):
        raise ValueError("Cross-document calculation requires explicit document alignment")

    if intent.operation == "lookup":
        if len(cells) != 1:
            raise ValueError("Lookup requires exactly one cell")
        result = cells[0].value
        formatted = _amount(result, unit)
        return ReasonedAnswer(formatted, _unique_sources(cells), result, unit)

    if len(cells) != 2:
        raise ValueError("Comparison requires exactly two verified operands")
    if intent.operation in ("scope_difference", "currency_difference"):
        if cells[0].period != cells[1].period or cells[0].scope == cells[1].scope:
            raise ValueError("Comparison scopes must be distinct and periods equal")
        delta = cells[0].value - cells[1].value
        if intent.operation == "currency_difference" and unit != "percent":
            raise ValueError("Currency LCR difference must use percent")
    else:
        if cells[0].scope != cells[1].scope:
            raise ValueError("Time comparison needs the same scope")
        a, b = sorted(cells, key=lambda c: _quarter_sort_key(c.period))
        if a.period == b.period:
            raise ValueError("Time comparison requires different periods")
        delta = b.value - a.value

    if intent.operation in ("percentage_points", "currency_difference"):
        if unit != "percent":
            raise ValueError("Percentage-point arithmetic needs two percentages")
        result = delta
        formatted = f"{_num(result)} percentage points"
        result_unit = "percentage_points"
    elif intent.operation == "percentage_change":
        if unit == "count" and cells[0].value == 0:
            raise ValueError("Undefined growth from zero")
        a, b = sorted(cells, key=lambda c: _quarter_sort_key(c.period))
        if a.value == 0:
            raise ValueError("Undefined growth from zero")
        result = ((b.value - a.value) / abs(a.value) * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP)
        formatted = f"{result:.1f}%"
        result_unit = "percent"
    else:
        if intent.operation == "decline":
            if delta > 0:
                raise ValueError("Question asks about a decline but the value increased")
            result = -delta
        else:
            result = delta
        formatted = _amount(result, unit)
        result_unit = unit

    # A clear arithmetic trace can be audited against the cited source cells.
    if intent.operation in ("percentage_change",):
        a, b = sorted(cells, key=lambda c: _quarter_sort_key(c.period))
        explanation = f"From {_amount(a.value, unit)} ({a.period}) to {_amount(b.value, unit)} ({b.period})."
    elif intent.operation in ("scope_difference", "currency_difference"):
        explanation = (f"{cells[0].scope}: {_amount(cells[0].value, unit)}; "
                       f"{cells[1].scope}: {_amount(cells[1].value, unit)}.")
    else:
        a, b = sorted(cells, key=lambda c: _quarter_sort_key(c.period))
        explanation = f"{a.period}: {_amount(a.value, unit)}; {b.period}: {_amount(b.value, unit)}."

    return ReasonedAnswer(
        answer=f"{formatted}. {explanation}", sources=_unique_sources(cells),
        result=result, unit=result_unit,
    )
