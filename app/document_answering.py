"""Source-isolated Day 10 orchestration; legacy answering stays unchanged."""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
import re

from app.document_registry import DocumentRecord, list_documents
from app.document_resolver import DocumentSelection, resolve_documents
from app.question_parser import parse_question
from app.table_lookup import FinancialCell, resolve_intent_cells
from app.document_tables import generic_cell, segment_volume_cell
from app.financial_plan import plan_question
from app.evidence_verifier import verify_cell, verify_operands


ABSTAIN = "I cannot verify the answer from the selected document passages."


def _clarify(message: str, reason: str) -> dict:
    return {"answer": message, "supported": False, "sources": [],
            "needs_clarification": True, "selection_reason": reason,
            "selected_documents": []}


def _abstain(reason: str = "") -> dict:
    return {"answer": ABSTAIN, "supported": False, "sources": [],
            "needs_clarification": False, "selection_reason": reason,
            "selected_documents": []}


def _abstain_with_message(message: str, reason: str) -> dict:
    """Return a user-facing policy/availability explanation without a value."""
    return {"answer": message, "supported": False, "sources": [],
            "needs_clarification": False, "selection_reason": reason,
            "selected_documents": []}


def _number(num: Decimal) -> str:
    return f"{num:,.{max(0, -num.as_tuple().exponent)}f}"


def _amount(cell: FinancialCell) -> str:
    v = _number(cell.value)
    if cell.unit == "percent":
        return v + "%"
    if cell.unit == "count":
        return v
    if cell.unit == "ratio":
        return v
    if re.fullmatch(r"[A-Z]{3}",cell.unit):
        return f"{cell.unit} {v}"
    if cell.unit == "basis_points":
        return v + " basis points"
    m = re.fullmatch(r"([A-Z]{3})(m|bn)", cell.unit)
    if m:
        return f"{m[1]} {v} {'million' if m[2]=='m' else 'billion'}"
    raise ValueError(f"Unknown financial unit {cell.unit!r}")


def _annotate_sources(result: dict, doc: DocumentRecord, reason: str) -> dict:
    result = dict(result)
    result["sources"] = [{**s, "document_id": doc.document_id,
                          "report": doc.display_name}
                         for s in (result.get("sources") or [])]
    result["selected_documents"] = [doc.public_dict()]
    result["selection_reason"] = reason
    return result


def _one_cell(doc: DocumentRecord, metric: str, period: str, scope: str = "group", country=None) -> FinancialCell | None:
    from app.question_parser import FinancialIntent
    intent = FinancialIntent(metric=metric, periods=(period,), operation="lookup",
                             scopes=(scope,), country=country, structured=True)
    if metric in {"segment_total_deposits", "segment_total_lending"}:
        return segment_volume_cell(metric,period,doc.document_id,scope)
    if doc.issuer.casefold() == "nordea":
        # The Nordea factbook's newer quarter tables are more reliably read by
        # the layout-aware table adapter. Try it first, but validate the unit
        # against the selected report metadata before accepting it. Some
        # older metric aliases do not cover every literal factbook row, so
        # the structured resolver remains the fallback. This is also useful
        # for isolated tests where Chroma is deliberately unavailable.
        def unit_matches_report(cell: FinancialCell) -> bool:
            money = re.fullmatch(r"([A-Z]{3})(?:m|bn)", cell.unit or "")
            if money:
                return not doc.currency or money.group(1) == doc.currency.upper()
            if re.fullmatch(r"[A-Z]{3}", cell.unit or ""):
                return not doc.currency or cell.unit == doc.currency.upper()
            return cell.unit in {"percent", "ratio", "basis_points", "count"}

        if scope == "group" and not country:
            try:
                cell = generic_cell(metric, period, doc.document_id)
            except (RuntimeError, ImportError, ValueError):
                cell = None
            if cell is not None and unit_matches_report(cell):
                return cell
        try:
            found = resolve_intent_cells(intent, doc.document_id)
        except (RuntimeError, ImportError, ValueError):
            found = None
        if found and len(found) == 1 and unit_matches_report(found[0]):
            return found[0]
        return None
    # Segment scope is a hard constraint. Never replace a requested division
    # with a consolidated Group cell if the division table is unavailable.
    if scope != "group" or country:
        from app.document_tables import report_segment_cell
        return report_segment_cell(metric, period, doc.document_id, scope, country)
    # Other issuers use a conservative generic Group adapter. It never assumes EUR.
    return generic_cell(metric, period, doc.document_id)


def _period_order(period: str) -> tuple[int, int]:
    """Canonical chronological order (year, then quarter)."""
    return (2000 + int(period[2:]), int(period[1]))


def _same_report_change(question: str, doc: DocumentRecord, reason: str, intent) -> dict:
    """Two verified quarterly cells from ONE selected non-Nordea PDF.

    Both source cells must exist, share a metric and unit, and be backed by
    the selected document. Never infer an absent period from another report.
    """
    if len(intent.periods) != 2 or intent.uncertain or intent.scopes != ("group",) or intent.country:
        return _abstain("Ambiguous or unsupported multi-period question")
    periods = sorted(intent.periods, key=_period_order)
    cells = [generic_cell(intent.metric, p, doc.document_id) for p in periods]
    if any(c is None for c in cells):
        return _annotate_sources(_abstain("A requested period was not verified in the selected report"), doc, reason)
    old, new = cells
    if old.metric != new.metric or old.scope != new.scope or old.unit != new.unit:
        return _annotate_sources(_abstain("Incompatible metric, scope, or financial units"), doc, reason)
    delta = new.value - old.value
    if re.search(r'\babsolute (?:amount|value|magnitude)\b',question,re.I):
        # A decline in the *magnitude* of a signed expense differs from a
        # signed accounting delta (e.g. -5,963 -> -5,910).
        delta = abs(new.value) - abs(old.value)
    if intent.operation == "percentage_change":
        if not old.value:
            return _annotate_sources(_abstain("Cannot calculate percentage change from zero"), doc, reason)
        change = (delta / abs(old.value) * Decimal(100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        asks_for_both = bool(re.search(
            r"\babsolute\b.{0,70}\bpercent(?:age)?\b|"
            r"\bpercent(?:age)?\b.{0,70}\babsolute\b|"
            r"how much.{0,70}by what percentage|by what percentage.{0,70}how much|"
            r"how much.{0,100}\band what (?:was|is) the percentage|"
            r"including (?:its |the )?percentage change|"
            r"both.{0,60}\b(?:SEK|EUR|NOK|DKK|USD|GBP)?\s*(?:million|m|bn|billion)?\b.{0,60}\bpercent(?:age)?\b",
            question, re.I,
        ))
        if asks_for_both:
            if old.unit == "percent":
                absolute_text = (
                    f"{_number(abs(delta))} percentage points "
                    f"{'increase' if delta > 0 else 'decline' if delta < 0 else 'change'}"
                )
            else:
                absolute_text = (
                    f"{_amount(replace(old, value=abs(delta)))} "
                    f"{'increase' if delta > 0 else 'decrease' if delta < 0 else 'change'}"
                )
            percentage_text = (
                f"{abs(change):.1f}% "
                f"{'increase' if change > 0 else 'decline' if change < 0 else 'change'}"
            )
            result = f"{absolute_text} ({percentage_text})"
        elif re.search(r"\bpercentage decline\b|\bpercent decline\b", question, re.I):
            if change >= 0:
                return _annotate_sources(
                    _abstain("Verified values do not show the requested percentage decline"), doc, reason
                )
            result = f"{abs(change):.1f}% decline"
        elif re.search(r"\bpercentage increase\b|\bpercent increase\b", question, re.I):
            if change <= 0:
                return _annotate_sources(
                    _abstain("Verified values do not show the requested percentage increase"), doc, reason
                )
            result = f"{change:.1f}% increase"
        else:
            result = (f"{change:.1f}% increase" if change > 0 else
                      f"{abs(change):.1f}% decline" if change < 0 else
                      "no percentage change (0.0%)")
    elif intent.operation == "percentage_points":
        if old.unit != "percent":
            return _annotate_sources(_abstain("Percentage-point comparison requires two rates"), doc, reason)
        result = f"{_number(abs(delta))} percentage points {'increase' if delta > 0 else 'decline' if delta < 0 else 'change'}"
    elif intent.operation in ("absolute_change", "decline"):
        if old.unit == "percent":
            result = f"{_number(abs(delta))} percentage points {'increase' if delta > 0 else 'decline' if delta < 0 else 'change'}"
        else:
            result = f"{_amount(replace(old, value=abs(delta)))} {'increase' if delta > 0 else 'decline' if delta < 0 else 'change'}"
    else:
        return _annotate_sources(_abstain("Requested calculation is not supported"), doc, reason)
    sources = list(dict.fromkeys((c.document, c.page) for c in cells))
    response = {
        "answer": f"{result}. {periods[0]}: {_amount(old)}; {periods[1]}: {_amount(new)}.",
        "supported": True,
        "sources": [{"document": d, "page": p} for d, p in sources],
        "needs_clarification": False,
    }
    return _annotate_sources(response, doc, reason)


def _same_report_scope_comparison(question: str, doc: DocumentRecord,
                                  reason: str, intent) -> dict:
    """Compare a division and its consolidated Group inside one report PDF.

    Both operands are read independently from the same document ID and period.
    A missing division cell never falls back to the Group value, and the Group
    cell is never substituted for a requested segment.
    """
    if (intent.metric is None or len(intent.periods) != 1
            or len(intent.scopes) != 2 or "group" not in intent.scopes
            or intent.country):
        return _annotate_sources(
            _clarify("Specify one division and the consolidated Group for the same reporting period.", reason),
            doc, reason,
        )
    period = intent.periods[0]
    cells: list[FinancialCell] = []
    for scope in intent.scopes:
        cell = _one_cell(doc, intent.metric, period, scope, None)
        if cell is None:
            return _annotate_sources(
                _abstain(f"The requested {scope} {intent.metric.replace('_', ' ')} cell is not verified"),
                doc, reason,
            )
        verdict = verify_cell(cell, document_id=doc.document_id, metric=intent.metric,
                              period=period, scope=scope)
        if not verdict.accepted:
            return _annotate_sources(
                _abstain(f"The requested {scope} cell failed evidence verification: {verdict.reason}"),
                doc, reason,
            )
        cells.append(cell)
    if cells[0].unit != cells[1].unit:
        return _annotate_sources(_abstain("The division and Group values use incompatible units"), doc, reason)

    labels = {
        "group": f"{doc.issuer} Group",
        "baltic": f"{doc.issuer} Baltic",
        "corporate & investment banking": f"{doc.issuer} Corporate & Investment Banking",
        "business & retail banking": f"{doc.issuer} Business & Retail Banking",
        "wealth & asset management": f"{doc.issuer} Wealth & Asset Management",
        "personal banking": f"{doc.issuer} Personal Banking",
        "business banking": f"{doc.issuer} Business Banking",
        "asset & wealth management": f"{doc.issuer} Asset & Wealth Management",
        "large corporates & institutions": f"{doc.issuer} Large Corporates & Institutions",
        "handelsbanken sweden": "Handelsbanken Sweden",
        "handelsbanken uk": "Handelsbanken UK",
        "handelsbanken norway": "Handelsbanken Norway",
        "handelsbanken the netherlands": "Handelsbanken the Netherlands",
        "handelsbanken markets": "Handelsbanken Markets",
    }
    names = [labels.get(scope, scope.replace("_", " ").title()) for scope in intent.scopes]
    metric_label = intent.metric.replace("_", " ")
    gap = abs(cells[0].value - cells[1].value)
    if cells[0].value == cells[1].value:
        comparison = f"Both reporting scopes showed the same {metric_label}."
    else:
        higher_idx = 0 if cells[0].value > cells[1].value else 1
        lower_idx = 1 - higher_idx
        gap_text = (f"{_number(gap)} percentage points" if cells[higher_idx].unit == "percent"
                    else _amount(replace(cells[higher_idx], value=gap)))
        comparison = (
            f"{names[higher_idx]} had higher {metric_label} than {names[lower_idx]} "
            f"by {gap_text}."
        )
    values = "; ".join(f"{name}: {_amount(cell)}" for name, cell in zip(names, cells))
    sources = [
        {"document": cell.document, "page": cell.page,
         "document_id": doc.document_id, "report": doc.display_name}
        for cell in cells
    ]
    return {
        "answer": f"{comparison} {values}.",
        "supported": True,
        "sources": sources,
        "needs_clarification": False,
        "selected_documents": [doc.public_dict()],
        "selection_reason": reason,
    }


def _document_currency(doc: DocumentRecord) -> str | None:
    """Use declared PDF table units if legacy registry currency is empty.

    Ignore narrative mentions of currencies: only explicit source-table unit
    headers (e.g. EURm) qualify. An ambiguous file returns None.
    """
    if doc.currency:
        return doc.currency
    from app.document_tables import _get_pages
    try:
        pages=_get_pages(doc.document_id)
    except (RuntimeError,ImportError,ValueError):
        return None
    tally={}
    for page in pages:
        if page['page']>12:continue
        for line in page['text'].splitlines():
            m=re.match(r'\s*(EUR|SEK|NOK|DKK|USD|GBP)\s*(?:m|mn|million|bn|billion)\b',line,re.I)
            if m:tally[m[1].upper()]=tally.get(m[1].upper(),0)+1
    return next(iter(tally)) if len(tally)==1 and next(iter(tally.values()))>=2 else None


def _compare(question: str, documents: tuple[DocumentRecord, ...], reason: str) -> dict:
    """Cross-document arithmetic only with two exact verified operands."""
    if len(documents) != 2:
        return _abstain("Exactly two reports are required")
    # Currency compatibility is determined from the verified source cells below,
    # not from registry metadata alone. Different-currency figures may still be
    # presented side by side; only arithmetic or FX conversion is prohibited.
    intent = parse_question(question)
    if (not intent.periods and documents[0].issuer != documents[1].issuer
            and documents[0].report_year and documents[0].report_quarter
            and (documents[0].report_year, documents[0].report_quarter)
                == (documents[1].report_year, documents[1].report_quarter)):
        common_year = documents[0].report_year
        common_quarter = documents[0].report_quarter
        question = f"{question.rstrip(' .?!')} in Q{common_quarter} {common_year}"
        intent = parse_question(question)
    if intent.metric is None or intent.needs_clarification or intent.country or intent.scopes != ("group",):
        return _clarify("Specify one comparable group-level financial metric to compare.", reason)
    if len(intent.periods) == 2 and documents[0].issuer == documents[1].issuer:
        # Map each requested period to its exact report edition; don't use a
        # report's historical column as a substitute for the named edition.
        matching = {}
        for period in intent.periods:
            for doc in documents:
                if doc.report_year and doc.report_quarter and period == f"Q{doc.report_quarter}{str(doc.report_year)[-2:]}":
                    matching[period] = doc
        if len(matching) != 2:
            return _clarify("Select the two report editions matching the two periods being compared.", reason)
        ordered = sorted(intent.periods, key=lambda p: (int(p[2:]), int(p[1])))
        pairs = [(matching[p], p) for p in ordered]
    elif len(intent.periods) == 1 and documents[0].issuer != documents[1].issuer:
        # Two issuers, same accounting period; source order follows question.
        pairs = [(doc, intent.periods[0]) for doc in documents]
    else:
        return _clarify("Specify two reporting periods for the same bank, or one common period for two banks.", reason)

    # A directional/difference request across two currencies is already
    # impossible to calculate from the report metadata alone. Explain the FX
    # requirement before attempting cell retrieval so the user receives a
    # useful, explicit abstention even when one metric row is not indexed.
    # This guard never supplies or estimates an exchange rate and emits no
    # financial-source citations because no arithmetic operands were used.
    cross_issuer_same_period = (
        documents[0].issuer != documents[1].issuer
        and len(pairs) == 2
        and pairs[0][1] == pairs[1][1]
    )
    asks_for_difference = bool(re.search(
        r"\b(?:difference|how much more|how many more|more than|less than|"
        r"higher|lower|larger|smaller|greater|less|more|better|worse|"
        r"exceed(?:ed|s)?|by how much|gap between|increase|decrease|growth|percentage change)\b",
        question, re.I,
    ))
    # Currency mismatch prevents arithmetic only for monetary concepts.
    # Percentages, ratios, basis-point metrics, and counts are comparable across
    # issuers without FX conversion even when their report currencies differ.
    from app.financial_vocabulary import BY_KEY
    concept = BY_KEY.get(intent.metric)
    concept_unit = getattr(concept, "unit", None)
    monetary_metric = concept_unit not in {"percent", "ratio", "basis_points", "count"}
    if cross_issuer_same_period and asks_for_difference and monetary_metric:
        currencies = [_document_currency(doc) for doc, _ in pairs]
        if all(currencies) and currencies[0] != currencies[1]:
            return {
                "answer": (
                    f"I can identify the selected report currencies "
                    f"({pairs[0][0].issuer}: {currencies[0]}; "
                    f"{pairs[1][0].issuer}: {currencies[1]}), but I cannot calculate "
                    "the requested difference without a verified exchange rate "
                    "source and conversion date."
                ),
                "supported": False, "sources": [], "needs_clarification": False,
                "selected_documents": [d.public_dict() for d in documents],
                "selection_reason": "Currency mismatch; arithmetic requires verified FX",
            }

    cells = []
    for doc, period in pairs:
        cell = _one_cell(doc, intent.metric, period)
        if cell is None:
            return _abstain(f"Exact cell not available in {doc.display_name}")
        cells.append(cell)
    verdict=verify_operands(cells,document_ids=[doc.document_id for doc,_ in pairs],
                            metric=intent.metric,periods=[period for _,period in pairs])
    if not verdict.accepted and "Incompatible" not in verdict.reason:
        return _abstain("Comparison operands lack row-level evidence: "+verdict.reason)
    if cells[0].unit != cells[1].unit:
        # A cross-issuer comparison can remain useful as a source-grounded
        # side-by-side presentation even when the reports use different
        # currencies. Do not subtract, rank by nominal numeric amount, or
        # invent an FX rate/date. The individual values remain verified.
        monetary = [re.fullmatch(r"([A-Z]{3})(m|bn)", c.unit) for c in cells]
        explicit_side_by_side = bool(re.search(r"\bside[- ]by[- ]side\b", question, re.I))
        # The Day 13.12 cross-currency convenience was deliberately introduced
        # for the verified net-profit comparison. Keep other metrics fail-closed
        # unless the user explicitly asks for a side-by-side, non-arithmetic
        # presentation. This preserves the previous safety contract for broad
        # cross-currency "compare" requests such as net interest income.
        side_by_side_is_explicit_or_approved = (
            intent.metric == "net_profit" or explicit_side_by_side
        )
        if (all(monetary) and documents[0].issuer != documents[1].issuer
                and len(pairs) == 2 and pairs[0][1] == pairs[1][1]
                and side_by_side_is_explicit_or_approved):
            # If the wording explicitly asks for a difference/directional amount,
            # keep the strict no-FX arithmetic guard. A plain net-profit compare
            # can be answered side by side without conversion.
            if asks_for_difference:
                return {
                    "answer": (
                        f"I can verify both {intent.metric.replace('_', ' ')} figures for {pairs[0][1]}, "
                        "but the reports use different currencies. I cannot calculate a difference, "
                        "say which amount is larger, or rank them without a verified exchange-rate "
                        "source and conversion date."
                    ),
                    "supported": False, "sources": [], "needs_clarification": False,
                    "selected_documents": [d.public_dict() for d in documents],
                    "selection_reason": "Currency mismatch; arithmetic requires verified FX",
                }
            period = pairs[0][1]
            period_display = (f"Q{period[1]} 20{period[2:]}"
                              if re.fullmatch(r"Q[1-4]\d{2}", period) else period)
            comparison_lines = [
                f"**{doc.issuer}:** {_amount(cell)} ({doc.display_name})"
                for (doc, _), cell in zip(pairs, cells)
            ]
            currencies = {m.group(1) for m in monetary}
            if len(currencies) > 1:
                limitation = (
                    "The reports use different currencies, so these values are shown side by side only. "
                    "No subtraction, ranking by the raw numeric amounts, or foreign-exchange conversion "
                    "has been performed. A converted comparison requires a verified exchange-rate source "
                    "and conversion date."
                )
            else:
                limitation = (
                    "The reports express the same currency using different scales, so the source values "
                    "are shown side by side without arithmetic normalization."
                )
            sources = [
                {"document": cell.document, "page": cell.page,
                 "document_id": doc.document_id, "report": doc.display_name}
                for (doc, _), cell in zip(pairs, cells)
            ]
            return {
                "answer": f"**Side-by-side {intent.metric.replace('_', ' ')} comparison — {period_display}**\n\n"
                          + "\n\n".join(f"- {line}" for line in comparison_lines)
                          + "\n\n" + limitation,
                "supported": True, "sources": sources, "needs_clarification": False,
                "selected_documents": [d.public_dict() for d in documents],
                "selection_reason": "Both source values verified; no cross-currency arithmetic performed",
            }
        return {"answer": "The verified figures use incompatible units and cannot be directly compared arithmetically.",
                "supported": False, "sources": [], "needs_clarification": False,
                "selected_documents": [d.public_dict() for d in documents],
                "selection_reason": "Incompatible source units; no conversion performed"}
    if cells[0].metric != cells[1].metric or cells[0].scope != cells[1].scope:
        return _abstain("Metric or reporting scope differs")
    plain_cross_bank_side_by_side = (
        documents[0].issuer != documents[1].issuer
        and bool(re.search(r"\b(?:vs\.?|versus|compare|comparison)\b", question, re.I))
        and not asks_for_difference
    )
    if plain_cross_bank_side_by_side:
        period = pairs[0][1]
        period_display = (f"Q{period[1]} 20{period[2:]}"
                          if re.fullmatch(r"Q[1-4]\d{2}", period) else period)
        lines = [f"**{doc.issuer}:** {_amount(cell)}"
                 for (doc, _), cell in zip(pairs, cells)]
        sources = [{"document": cell.document, "page": cell.page,
                    "document_id": doc.document_id, "report": doc.display_name}
                   for (doc, _), cell in zip(pairs, cells)]
        return {
            "answer": f"**Side-by-side {intent.metric.replace('_', ' ')} comparison — {period_display}**\n\n"
                      + "\n\n".join(f"- {line}" for line in lines)
                      + "\n\nBoth figures are independently verified from the same reporting period. No subtraction, ranking, or currency conversion has been performed.",
            "supported": True, "sources": sources, "needs_clarification": False,
            "selected_documents": [d.public_dict() for d in documents],
            "selection_reason": reason,
        }
    if not re.search(r"\b(?:difference|more|higher|increase|rise|decline|decrease|fell|fall|drop|change|growth|grew|grow|exceed|compare|versus|vs\.?)\b", question, re.I):
        return _clarify("Ask which comparison you want (difference, growth percentage, or side-by-side values).", reason)

    a, b = cells
    delta = b.value - a.value
    percent_change = bool(re.search(
        r"\b(?:growth|grew|grow|percentage change|percent change|percentage decline|percent decline|"
        r"percentage decrease|percent decrease|percentage increase|percent increase)\b|"
        r"by what percentage|what percentage|including (?:its |the )?percentage change",
        question, re.I,
    ))
    # For two banks, align directional questions to the issuer order in
    # the wording, not the arbitrary selection order in the dropdown.
    if documents[0].issuer != documents[1].issuer:
        q = question.casefold()
        positions = [q.find(doc.issuer.casefold()) for doc in documents]
        if not all(p >= 0 for p in positions):
            return _clarify("Mention both bank names explicitly in the comparison.", reason)
        if positions[0] < positions[1]:
            delta = a.value - b.value
        elif positions[1] < positions[0]:
            delta = b.value - a.value
        else:
            return _clarify("Specify which bank should be compared with which.", reason)
    # Answer explicit "which issuer had higher/lower" questions with the
    # winner's name and the absolute gap. Do not leave the user to infer the
    # answer from an unlabeled delta. The cross-currency FX guard above runs
    # before this block, so this arithmetic is reached only for compatible units.
    if documents[0].issuer != documents[1].issuer:
        asks_which_higher = bool(re.search(
            r"\bwhich\b.{0,100}\b(?:higher|greater)\b|\bhow much higher was\b", question, re.I
        ))
        asks_which_lower = bool(re.search(
            r"\bwhich\b.{0,100}\b(?:lower|less)\b|\bhow much lower was\b", question, re.I
        ))
        if asks_which_higher or asks_which_lower:
            higher = asks_which_higher
            winner_idx = (0 if cells[0].value >= cells[1].value else 1) if higher else (0 if cells[0].value <= cells[1].value else 1)
            loser_idx = 1 - winner_idx
            winner_doc, winner_cell = pairs[winner_idx][0], cells[winner_idx]
            loser_doc, loser_cell = pairs[loser_idx][0], cells[loser_idx]
            gap = abs(winner_cell.value - loser_cell.value)
            direction = "higher" if higher else "lower"
            metric_label = intent.metric.replace("_", " ")
            gap_text = (f"{_number(gap)} percentage points" if winner_cell.unit == "percent"
                        else _amount(replace(winner_cell, value=gap)))
            result = (
                f"{winner_doc.issuer} had {direction} {metric_label} than "
                f"{loser_doc.issuer} by {gap_text}. "
                f"{winner_doc.issuer}: {_amount(winner_cell)}; "
                f"{loser_doc.issuer}: {_amount(loser_cell)}"
            )
            sources = [{"document": c.document, "page": c.page, "document_id": doc.document_id,
                        "report": doc.display_name} for (doc, _), c in zip(pairs, cells)]
            return {"answer": result + ".", "supported": True, "sources": sources,
                    "needs_clarification": False,
                    "selected_documents": [d.public_dict() for d in documents],
                    "selection_reason": reason}

    if percent_change:
        if documents[0].issuer != documents[1].issuer:
            return _clarify("Specify the reference bank for the percentage change.", reason)
        if a.value == 0:
            return _abstain("Cannot compute growth from a zero base")
        value = ((b.value - a.value) / abs(a.value) * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        decline_requested = bool(re.search(r"\b(?:percentage|percent) decline\b", question, re.I))
        increase_requested = bool(re.search(r"\b(?:percentage|percent) increase\b", question, re.I))
        if decline_requested and value >= 0:
            return _abstain("Verified values do not show the requested percentage decline")
        if increase_requested and value <= 0:
            return _abstain("Verified values do not show the requested percentage increase")
        asks_for_both = bool(re.search(
            r"\babsolute\b.{0,70}\bpercent(?:age)?\b|"
            r"\bpercent(?:age)?\b.{0,70}\babsolute\b|"
            r"how much.{0,70}by what percentage|by what percentage.{0,70}how much|"
            r"how much.{0,100}\band what (?:was|is) the percentage|"
            r"including (?:its |the )?percentage change|"
            r"both.{0,60}\b(?:SEK|EUR|NOK|DKK|USD|GBP)?\s*(?:million|m|bn|billion)?\b.{0,60}\bpercent(?:age)?\b",
            question, re.I,
        ))
        if asks_for_both:
            if a.unit == "percent":
                absolute_text = (
                    f"{_number(abs(delta))} percentage points "
                    f"{'increase' if delta > 0 else 'decline' if delta < 0 else 'change'}"
                )
            else:
                absolute_text = (
                    f"{_amount(replace(a, value=abs(delta)))} "
                    f"{'increase' if delta > 0 else 'decrease' if delta < 0 else 'change'}"
                )
            percentage_text = (
                f"{abs(value):.1f}% "
                f"{'increase' if value > 0 else 'decline' if value < 0 else 'change'}"
            )
            result = f"{absolute_text} ({percentage_text})"
        elif decline_requested:
            result = f"{abs(value):.1f}% decline"
        elif increase_requested:
            result = f"{value:.1f}% increase"
        else:
            result = (f"{value:.1f}% increase" if value > 0 else
                      f"{abs(value):.1f}% decline" if value < 0 else
                      "no percentage change (0.0%)")
    elif a.unit == "percent":
        result = f"{_number(delta)} percentage points"
    else:
        # For a cross-bank phrase 'how much MORE did Nordea have than SEB',
        # respect the first named issuer (not dropdown order).
        if documents[0].issuer == documents[1].issuer:
            # Latest minus earlier for two editions, not dropdown ordering.
            delta = b.value - a.value
            decline_only = bool(re.search(
                r"\b(?:decline|decreased?|fell|fall|dropped?|reduction)\b", question, re.I
            )) and not bool(re.search(r"\bincrease\s+or\s+decrease\b", question, re.I))
            if decline_only:
                if delta >= 0:
                    return _abstain("Verified values do not show the requested decline")
                result = _amount(replace(a, value=abs(delta))) + " decline"
            elif delta > 0:
                result = _amount(replace(a, value=delta)) + " increase"
            elif delta < 0:
                result = _amount(replace(a, value=abs(delta))) + " decrease"
            else:
                result = "no change"
        else:
            result = _amount(replace(a, value=delta))
    sources = [{"document": c.document, "page": c.page, "document_id": doc.document_id,
                "report": doc.display_name} for (doc, _), c in zip(pairs, cells)]
    explanation = "; ".join(f"{doc.display_name}: {_amount(cell)} ({period})"
                            for (doc, period), cell in zip(pairs, cells))
    return {"answer": f"{result}. {explanation}.", "supported": True,
            "sources": sources, "needs_clarification": False,
            "selected_documents": [d.public_dict() for d in documents],
            "selection_reason": reason}


def _is_unqualified_profit_request(question: str) -> bool:
    """True only for a broad "profit" question, not a named profit measure."""
    if not re.search(r"\bprofit\b", question, re.I):
        return False
    return not bool(re.search(
        r"\b(?:net|operating|gross|pre[ -]?tax)\s+profit\b|"
        r"\bprofit\s+(?:before|after|margin)\b|\bprofitability\b",
        question, re.I,
    ))


def _default_profit_answer(question: str, doc: DocumentRecord, reason: str) -> dict | None:
    """Default an undated, unqualified profit request to the report's quarter.

    This applies only to non-Nordea reports where the deterministic table-cell
    path is used. The response labels net profit as the default and reports
    operating profit separately when that row is also verifiable.
    """
    if not _is_unqualified_profit_request(question):
        return None
    if not doc.report_year or not doc.report_quarter:
        return _annotate_sources(
            _clarify("Which reporting quarter should I use for net profit?", reason), doc, reason
        )
    period = f"Q{doc.report_quarter}{doc.report_year % 100:02d}"
    net = generic_cell("net_profit", period, doc.document_id)
    if net is None:
        return None
    answer = (
        f"Interpreting the unqualified word ‘profit’ as **net profit**, the latest quarter "
        f"in the selected report ({period[:2]} {doc.report_year}) reports **{_amount(net)}**."
    )
    sources = [{"document": net.document, "page": net.page}]
    operating = generic_cell("operating_profit", period, doc.document_id)
    if operating is not None:
        answer += f" For comparison, **operating profit** was {_amount(operating)}."
        sources.append({"document": operating.document, "page": operating.page})
    answer += " Ask for net profit, operating profit, or another measure explicitly to change the definition."
    # Deduplicate the same physical PDF page when both rows share one table.
    unique_sources = {(s["document"], s["page"]): s for s in sources}
    return _annotate_sources({
        "answer": answer, "supported": True,
        "sources": list(unique_sources.values()), "needs_clarification": False,
    }, doc, reason)


_PERIOD_ONLY_REPLY = re.compile(
    r"^\s*(?:(?:please\s+)?(?:use|choose|select|go with|take)\s+)?"
    r"(?:the\s+)?Q([1-4])(?:\s*(20\d{2}|\d{2}))?(?:\s+report)?[.!?]*\s*$", re.I
)
_REPORT_RELEVANCE = re.compile(
    r"\b(?:bank|banks|banking|financial|finance|profit|profits|earnings|income|revenue|"
    r"assets|liabilities|equity|capital|CET1|loan|loans|lending|deposit|deposits|"
    r"interest|dividend|credit|ratio|segment|division|business area|business areas|"
    r"balance sheet|cash flow|report|factbook|quarter|Q[1-4])\b", re.I
)
_PERSONAL_ACCOUNT_REQUEST = re.compile(
    r"\b(?:my|mine|personal|own)\b.{0,100}\b(?:current|checking|savings|bank)\s+account\b"
    r"|\b(?:my|mine|personal|own)\b.{0,100}\b(?:account\s+balance|bank\s+balance|"
    r"account\s+transactions?|bank\s+statement|account\s+details)\b"
    r"|\b(?:my|personal|own)\s+(?:bank\s+)?(?:account|balance|transactions?|statement)\b", re.I
)
_CURRENCY_CONVERSION_REQUEST = re.compile(
    r"\b(?:convert|conversion|translate|express)\b.{0,140}"
    r"\b(?:into|to|in)\s+(?:euros?|eur|swedish kron(?:a|or)|sek|norwegian kroner|nok|"
    r"danish kroner|dkk|us dollars?|usd|pounds?|gbp)\b|"
    r"\b(?:using|at)\s+(?:the\s+)?(?:quarter'?s?\s+)?(?:average\s+)?exchange rate\b",
    re.I,
)



_QUARTER_PAIR_NO_YEAR = re.compile(
    r"\bQ([1-4])\s+(?:and|to|through|versus|vs\.?|than|or)\s+Q([1-4])\b",
    re.I,
)


def _infer_quarter_pair_year(question: str, reports: list[DocumentRecord]) -> tuple[str | None, str | None]:
    """Resolve a yearless Qx/Qy comparison only when both exact report editions are unique.

    The inference is based on indexed report metadata, never on the current
    date or model guess. Missing/duplicate editions remain a clarification.
    """
    if _periods_have_year(question):
        return question, None
    match = _QUARTER_PAIR_NO_YEAR.search(question)
    if not match:
        return question, None
    issuers = _issuer_names_in_question(question, reports)
    if len(issuers) != 1:
        return None, "Please name one bank and the two quarters to compare."
    q1, q2 = int(match.group(1)), int(match.group(2))
    issuer = issuers[0]
    candidate_years = {d.report_year for d in reports
                       if d.issuer.casefold() == issuer.casefold() and d.report_year
                       and d.report_quarter in {q1, q2}}
    common = set()
    for candidate_year in candidate_years:
        left = [d for d in reports if d.issuer.casefold() == issuer.casefold()
                and d.report_year == candidate_year and d.report_quarter == q1]
        right = [d for d in reports if d.issuer.casefold() == issuer.casefold()
                 and d.report_year == candidate_year and d.report_quarter == q2]
        if len(left) == 1 and len(right) == 1 and left[0].document_id != right[0].document_id:
            common.add(candidate_year)
    if len(common) != 1:
        return None, (
            f"I found no unique year with both Q{q1} and Q{q2} reports for {issuer}. "
            "Please specify the year, for example Q2 and Q3 2026."
        )
    year = next(iter(common))
    # The connected quarter tokens are the pair represented by this pattern.
    normalized_pair = f"Q{q1} {year} and Q{q2} {year}"
    normalized = question[:match.start()] + normalized_pair + question[match.end():]
    return normalized, None


def _periods_have_year(question: str) -> bool:
    return len(parse_question(question).periods) >= 2


def _issuer_names_in_question(question: str, reports: list[DocumentRecord]) -> list[str]:
    names = sorted({d.issuer for d in reports if d.issuer and d.issuer.casefold() != "unknown"},
                   key=len, reverse=True)
    return [name for name in names
            if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", question, re.I)]


_BARE_QUARTER = re.compile(
    r"(?<!\d{4}\s)\bQ([1-4])\b(?!\s*(?:of\s+)?[-/]?\s*(?:20\d{2}|\d{2}))", re.I
)


def _infer_yearless_single_quarter(
    question: str, reports: list[DocumentRecord], selection: DocumentSelection | None = None
) -> tuple[str | None, str | None]:
    """Resolve a bare Qx only when the indexed report editions identify one year.

    A bare quarter is an explicit period request, not a periodless question.
    If the year cannot be established from a unique exact report edition, ask
    instead of allowing the periodless latest-report default to answer another
    quarter.
    """
    matches = list(_BARE_QUARTER.finditer(question))
    if not matches:
        return question, None

    # The parser can inherit one explicit year across paired quarter expressions
    # such as "Q2 to Q3 2026". Those requests are already fully period-scoped.
    parsed_periods = plan_question(question).target_periods
    parsed_quarters = {
        int(period[1]) for period in parsed_periods
        if re.fullmatch(r"Q[1-4]\d{2}", period)
    }
    if parsed_periods and all(int(match.group(1)) in parsed_quarters for match in matches):
        return question, None

    if len(matches) != 1:
        return None, (
            "Please include the year for each requested quarter "
            "(for example Q2 2026 and Q3 2026)."
        )

    quarter = int(matches[0].group(1))
    issuers = _issuer_names_in_question(question, reports)
    relevant: list[DocumentRecord] = []
    if issuers:
        relevant = [d for d in reports if d.issuer in issuers
                    and d.report_quarter == quarter and d.report_year]
        if len(issuers) > 1:
            by_issuer = {issuer: [d for d in relevant if d.issuer == issuer]
                         for issuer in issuers}
            if any(len(rows) != 1 for rows in by_issuer.values()):
                relevant = []
            elif len({(rows[0].report_year, rows[0].report_quarter)
                      for rows in by_issuer.values()}) != 1:
                relevant = []
    elif selection and selection.mode == "manual" and len(selection.document_ids) == 1:
        relevant = [d for d in reports if d.document_id == selection.document_ids[0]
                    and d.report_quarter == quarter and d.report_year]

    editions = {(d.report_year, d.report_quarter) for d in relevant}
    if len(editions) != 1:
        issuer_label = ", ".join(issuers) if issuers else "the selected bank"
        return None, (
            f"You specified Q{quarter} but not the year. I could not identify one unique indexed "
            f"Q{quarter} report edition for {issuer_label}. Please specify the year "
            f"(for example Q{quarter} 2025 or Q{quarter} 2026). "
            "I won't substitute the latest available quarter."
        )

    year, _ = next(iter(editions))
    match = matches[0]
    return question[:match.end()] + f" {year}" + question[match.end():], None


def _unindexed_issuer_reference(question: str, known_issuers: set[str]) -> str | None:
    """Recognize a simple named institution after 'of/for/from' when not indexed."""
    excluded = {
        "bank", "banks", "group", "company", "business", "quarter", "period",
        "income", "profit", "revenue", "assets", "liabilities", "equity",
        "q1", "q2", "q3", "q4", "eps", "cet1", "roe", "lcr",
    }
    known = {name.casefold() for name in known_issuers}
    for match in re.finditer(r"\b(?:of|for|from)\s+([A-Z][A-Za-z0-9&.-]{2,40})\b", question):
        candidate = match.group(1).strip(" .,-")
        if candidate.casefold() not in known and candidate.casefold() not in excluded:
            return candidate
    return None


def _verified_group_cell(doc: DocumentRecord, metric: str, period: str) -> FinancialCell | None:
    """Retrieve and verify one exact Group cell from one exact report PDF."""
    try:
        cell = _one_cell(doc, metric, period, "group", None)
    except (RuntimeError, AttributeError):
        # Some isolated legacy tests intentionally replace the answerer without
        # providing a vector database. Treat that as unavailable evidence.
        return None
    if cell is None:
        return None
    verdict = verify_cell(cell, document_id=doc.document_id, metric=metric,
                          period=period, scope="group")
    return cell if verdict.accepted else None


def _nordea_periodless_net_profit_with_context(
    question: str, latest_doc: DocumentRecord, reports: list[DocumentRecord], reason: str
) -> dict | None:
    """Answer a periodless Nordea net-profit question using Q3 and its prior PDF.

    The latest quarter is the primary result. If the immediately preceding
    quarterly report is uniquely indexed, its own exact net-profit cell is also
    retrieved and independently verified. No historical column from the later
    factbook is substituted for the earlier PDF.
    """
    if (latest_doc.issuer.casefold() != "nordea" or not latest_doc.report_year
            or not latest_doc.report_quarter):
        return None
    plan = plan_question(question)
    if plan.metric != "net_profit" or plan.target_periods or plan.kind != "lookup":
        return None

    quarter = latest_doc.report_quarter
    year = latest_doc.report_year
    latest_period = f"Q{quarter}{year % 100:02d}"
    current = _verified_group_cell(latest_doc, "net_profit", latest_period)
    if current is None:
        return None

    previous_year, previous_quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    previous_candidates = [d for d in reports
        if d.issuer.casefold() == latest_doc.issuer.casefold()
        and (d.report_year, d.report_quarter) == (previous_year, previous_quarter)
        and d.document_id != latest_doc.document_id]
    comparison = None
    if len(previous_candidates) == 1:
        previous_doc = previous_candidates[0]
        previous_period = f"Q{previous_quarter}{previous_year % 100:02d}"
        previous_cell = _verified_group_cell(previous_doc, "net_profit", previous_period)
        if previous_cell is not None and previous_cell.unit == current.unit:
            operand_check = verify_operands(
                [previous_cell, current],
                document_ids=[previous_doc.document_id, latest_doc.document_id],
                metric="net_profit", periods=[previous_period, latest_period],
            )
            if operand_check.accepted:
                comparison = (previous_cell, previous_doc, previous_period)

    is_unqualified = _is_unqualified_profit_request(question)
    heading = "Interpreting ‘profit’ as net profit" if is_unqualified else "Net profit"
    answer = f"{heading}: **{_amount(current)}** in Q{quarter} {year}."
    sources = [{"document": current.document, "page": current.page,
                "document_id": latest_doc.document_id, "report": latest_doc.display_name}]
    selected_docs = [latest_doc.public_dict()]

    if comparison:
        previous_cell, previous_doc, previous_period = comparison
        delta = current.value - previous_cell.value
        delta_text = "no change" if delta == 0 else (
            f"{_amount(replace(current, value=abs(delta)))} "
            f"{'increase' if delta > 0 else 'decrease'}"
        )
        pct = (delta / abs(previous_cell.value) * Decimal("100")).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        ) if previous_cell.value else None
        answer += (f" Previous quarter: **{_amount(previous_cell)}** in "
                   f"Q{previous_doc.report_quarter} {previous_doc.report_year}. "
                   f"Quarter-on-quarter change: **{delta_text}**")
        if pct is not None:
            answer += f" ({abs(pct):.1f}% {'increase' if pct > 0 else 'decline' if pct < 0 else 'change'})."
        else:
            answer += "."
        sources.insert(0, {"document": previous_cell.document, "page": previous_cell.page,
                           "document_id": previous_doc.document_id,
                           "report": previous_doc.display_name})
        selected_docs.insert(0, previous_doc.public_dict())

    return {
        "answer": answer,
        "supported": True,
        "sources": sources,
        "needs_clarification": False,
        "selected_documents": selected_docs,
        "selection_reason": reason,
        "defaulted_period": latest_period,
    }

def answer_with_selection(question: str, selection: DocumentSelection | None = None,
                          documents: list[DocumentRecord] | None = None) -> dict:
    """Resolve permissible sources before ANY retrieval or financial lookup."""
    if not question or not question.strip():
        raise ValueError("Question cannot be empty")
    # A quarter-only reply is contextual, not a fresh company question. The
    # direct answer API has no chat history, so request the complete question.
    if _PERIOD_ONLY_REPLY.fullmatch(question.strip()):
        return _clarify(
            "I don't have the previous question attached to this reply. Please repeat the complete financial question with the bank and quarter, for example: 'What was Nordea's net profit in Q3 2026?'",
            "Period-only follow-up without conversation context",
        )
    # Greetings/capability help are not document-dependent and should continue
    # working even when multiple banks/reports are indexed.
    from app.question_understanding import small_talk_response
    conversational = small_talk_response(question)
    if conversational is not None:
        return {"answer": conversational, "supported": False, "sources": [],
                "needs_clarification": False, "is_small_talk": True,
                "selection_reason": "Conversational question; no report searched",
                "selected_documents": []}
    if _PERSONAL_ACCOUNT_REQUEST.search(question):
        return {
            "answer": (
                "I can't access personal bank-account balances, private transactions, or individual banking records. "
                "I can answer questions about published financial reports from the indexed banks and supported stock-market data."
            ),
            "supported": False, "sources": [], "needs_clarification": False,
            "out_of_scope": True,
            "selection_reason": "Personal account information is outside the indexed-report scope",
            "selected_documents": [],
        }
    if _CURRENCY_CONVERSION_REQUEST.search(question):
        return {
            "answer": (
                "I cannot convert a reported amount into another currency without a verified "
                "exchange rate and conversion date (or a documented average-rate period). "
                "The report's original-currency figure is not an FX conversion."
            ),
            "supported": False, "sources": [], "needs_clarification": False,
            "selection_reason": "Currency conversion requires a verified exchange-rate source and date",
            "selected_documents": [],
        }
    reports = list_documents() if documents is None else documents
    known_issuers = {doc.issuer for doc in reports if doc.issuer and doc.issuer.casefold() != "unknown"}
    explicit_issuer = any(
        re.search(r"(?<!\w)" + re.escape(issuer) + r"(?!\w)", question, re.I)
        for issuer in known_issuers
    )
    if not explicit_issuer:
        unindexed = _unindexed_issuer_reference(question, known_issuers)
        if unindexed:
            available = sorted(known_issuers, key=str.casefold)
            available_text = ", ".join(available) if available else "none"
            return _clarify(
                f"I don't have a financial report indexed for {unindexed}. "
                f"Currently indexed institutions: {available_text}. Add that bank's report or choose an indexed institution.",
                "Named institution is not present in the document registry",
            )
    if not explicit_issuer and not _REPORT_RELEVANCE.search(question):
        return {
            "answer": "That question is outside my scope. I'm focused on financial reports from the indexed banks and supported stock-market data. I can't assess personal or unrelated questions, but you can ask about a bank's profit, capital ratio, business segments, or share price.",
            "supported": False, "sources": [], "needs_clarification": False,
            "out_of_scope": True, "selection_reason": "Question is outside the financial-report domain",
            "selected_documents": [],
        }
    # Resolve two-quarter comparisons first so the first Qx is not mistaken
    # for a missing-year single-quarter request.
    inferred_question, inference_error = _infer_quarter_pair_year(question, reports)
    if inference_error:
        return _clarify(inference_error, "Yearless quarter comparison is missing a unique report pair")
    if inferred_question:
        question = inferred_question
    question, bare_quarter_error = _infer_yearless_single_quarter(question, reports, selection)
    if bare_quarter_error:
        return _clarify(bare_quarter_error, "Yearless quarter request was not uniquely resolvable")
    decision = resolve_documents(question, selection or DocumentSelection(), reports)
    if decision.clarification:
        return _clarify(decision.clarification, decision.reason)
    if len(decision.documents) == 2:
        return _compare(question, decision.documents, decision.reason)
    if len(decision.documents) != 1:
        return _clarify("Select a financial report to continue.", decision.reason)
    doc = decision.documents[0]
    original_question = question
    plan = plan_question(question)
    parsed_scope_intent = parse_question(question)
    if (parsed_scope_intent.operation == "scope_difference"
            and len(parsed_scope_intent.scopes) == 2
            and len(parsed_scope_intent.periods) == 1):
        return _same_report_scope_comparison(
            question, doc, decision.reason, parsed_scope_intent
        )

    # A periodless Nordea net-profit query considers the latest quarter and,
    # when available, the preceding quarter's separate indexed PDF. The latest
    # quarter remains the headline figure; the prior report is contextual.
    contextual_profit = _nordea_periodless_net_profit_with_context(
        original_question, doc, reports, decision.reason
    )
    if contextual_profit is not None:
        return contextual_profit

    # Keep the established concise explanation for an unqualified plain
    # "profit" question on non-Nordea reports. It states that net profit is
    # the default and, when available, distinguishes operating profit.
    if (doc.issuer.casefold() != "nordea" and plan.metric == "net_profit"
            and not plan.target_periods and plan.kind == "lookup"):
        defaulted = _default_profit_answer(question, doc, decision.reason)
        if defaulted is not None:
            return defaulted

    # For a single-bank scalar lookup with no period, use the latest quarter
    # in the selected report. This is applied only after issuer/source selection
    # and never to comparisons, forecasts, segment calculations, or multi-period
    # questions. The selected report's quarter is made explicit to the existing
    # deterministic table reader so it can retrieve the exact column.
    default_period_applied = False
    default_period = None
    if (plan.kind == "lookup" and plan.metric and not plan.target_periods
            and plan.scopes == ("group",) and not plan.country
            and doc.report_year and doc.report_quarter):
        default_period = f"Q{doc.report_quarter}{doc.report_year % 100:02d}"
        question = f"{question.rstrip(' ?.!')} in Q{doc.report_quarter} {doc.report_year}"
        plan = plan_question(question)
        default_period_applied = True

    def _label_latest_report(result: dict) -> dict:
        if not default_period_applied or not result.get("supported"):
            return result
        labelled = dict(result)
        if _is_unqualified_profit_request(original_question):
            prefix = (f"Interpreting ‘profit’ as net profit, using the latest indexed report "
                      f"({default_period[0:2]} {doc.report_year}): ")
        else:
            prefix = (f"Using the latest indexed report ({default_period[0:2]} "
                      f"{doc.report_year}): ")
        labelled["answer"] = prefix + str(labelled.get("answer", ""))
        labelled["defaulted_period"] = default_period
        return labelled

    # original_question retains the user's wording; question now carries the
    # exact defaulted reporting period for retrieval.
    if plan.kind=="unsupported" or re.search(r"\b(?:forecast|projected|predict)\b", question, re.I):
        metric_name = (plan.metric or "the requested metric").replace("_", " ")
        future_year = re.search(r"\b20\d{2}\b", question)
        year_text = f" for {future_year.group(0)}" if future_year else ""
        response = _abstain_with_message(
            f"I cannot verify a forecast of {doc.issuer}'s {metric_name}{year_text} from a historical report; "
            "that forecast is not available in the selected report and is not a reported financial result.",
            "Forecasting is outside the report's historical evidence scope",
        )
        return _annotate_sources(response,doc,decision.reason)
    if plan.kind=="lookup" and doc.report_year and doc.report_quarter:
        latest=(doc.report_year,doc.report_quarter)
        future_periods = [t for t in plan.target_periods
                          if re.fullmatch(r"Q[1-4]\d{2}",t) and _period_order(t) > latest]
        if future_periods:
            requested = future_periods[0]
            requested_label = f"Q{requested[1]} {2000 + int(requested[2:])}"
            available_label = f"Q{doc.report_quarter} {doc.report_year}"
            response = _abstain_with_message(
                f"I cannot verify {requested_label} profit from the selected report; "
                f"those results are not available in the selected {available_label} "
                f"{doc.issuer} report.",
                "Requested reporting period is later than the selected report",
            )
            return _annotate_sources(response,doc,decision.reason)
    if plan.kind=="explain":
        from app.narrative_answering import narrative_answer
        edition = (f"Q{doc.report_quarter} {doc.report_year}"
                   if doc.report_quarter and doc.report_year else None)
        return _annotate_sources(narrative_answer(question,doc.document_id,report_edition=edition),doc,decision.reason)
    if (plan.kind=="lookup" and plan.metric and len(plan.target_periods)==1
            and not plan.uncertain and not plan.country):
        scope=plan.scopes[0] if len(plan.scopes)==1 else None
        if scope:
            period=plan.target_periods[0]
            try:
                cell=_one_cell(doc,plan.metric,period,scope,plan.country)
            except (RuntimeError, AttributeError):
                # Legacy routes can still run in isolated unit tests that
                # deliberately mock the answerer instead of providing Chroma.
                # AttributeError also covers a deliberately minimal Chroma stub
                # whose PersistentClient is unavailable in lightweight tests.
                if doc.issuer.casefold()!='nordea': raise
                cell=None
            if cell is not None:
                verdict=verify_cell(cell,document_id=doc.document_id,metric=plan.metric,
                                    period=period,scope=scope if not plan.country else scope+"/"+plan.country)
                if verdict.accepted and (not plan.output_unit or
                        plan.output_unit in {cell.unit,"percent" if cell.unit=="percent" else cell.unit} or
                        (plan.output_unit=="basis_points" and cell.unit=="basis_points")):
                    return _label_latest_report(_annotate_sources({"answer":_amount(cell),"supported":True,
                        "sources":[{"document":cell.document,"page":cell.page}],
                        "needs_clarification":False},doc,decision.reason))
                if not verdict.accepted:
                    return _annotate_sources(_abstain("Aligned evidence failed verification: "+verdict.reason),doc,decision.reason)
                if plan.output_unit and plan.output_unit != cell.unit:
                    return _annotate_sources(_abstain("Requested unit differs from the source cell; no conversion performed"),doc,decision.reason)
            # A recognized new financial concept must not fall through to an
            # unrelated number from generative retrieval.
            from app.financial_vocabulary import BY_KEY
            legacy_concepts={"net_interest_income","net_fee_income","net_insurance_result",
                "fair_value_result","total_operating_income","segment_total_income",
                "net_profit","operating_profit","roe","roe_tangible","cet1_ratio",
                "tier1_ratio","total_capital_ratio","aum","total_assets","total_equity",
                "lcr","fte","net_interest_margin","credit_rea","total_rea","segment_rea"}
            if plan.metric in BY_KEY and plan.metric not in legacy_concepts:
                return _annotate_sources(_abstain("No verified row/period for the financial concept"),doc,decision.reason)
    # A question containing both a report edition and a different target
    # period is not an ordinary single-value lookup. Do not answer it with
    # a value from the source edition just because that quarter was named.
    pre_intent = parse_question(question)
    if (not plan.source_edition and pre_intent.metric and pre_intent.operation == "lookup" and len(pre_intent.periods) > 1):
        return _annotate_sources(_abstain("Multiple periods named without a verified comparison"), doc, decision.reason)
    if re.search(r"\b(?:retail )?customers?\b", question, re.I) and not plan.metric:
        response = _abstain_with_message(
            f"I cannot verify a retail-customer count for the requested period; it is not available in the selected {doc.issuer} report.",
            "Requested retail-customer field is not provided by the selected report",
        )
        return _annotate_sources(response, doc, decision.reason)

    if doc.issuer.casefold() == "nordea":
        # Known Nordea pipeline, with its exact Chroma document_id restriction.
        # The appended quarter is the latest quarter of this selected report.
        from app.answering import answer_question
        legacy_question = original_question if default_period_applied else question
        result = _annotate_sources(answer_question(legacy_question, doc.document_id), doc, decision.reason)
        return _label_latest_report(result)

    # New issuers: no Nordea-specific table/parser inference. A value is
    # supported only by a verified row/header in the selected PDF.
    intent = parse_question(question)
    if intent.needs_clarification:
        if re.search(r"\b(?:revenue|turnover|sales)\b", question, re.I):
            prompt = (
                "Which financial measure do you mean: total operating income, net interest income, "
                "net fee and commission income, net profit, or another reported measure?"
            )
        else:
            prompt = "Which financial metric do you mean? Please name the specific reported line, such as net profit, operating profit, or total operating income."
        return _clarify(prompt, decision.reason)
    if (intent.metric and intent.operation in {"absolute_change", "decline", "percentage_change", "percentage_points"}
            and len(intent.periods) == 2 and intent.scopes == ("group",)
            and not intent.country):
        return _same_report_change(question, doc, decision.reason, intent)
    if (intent.metric and intent.operation == "lookup" and len(intent.periods) == 1
            and intent.scopes == ("group",) and not intent.country):
        cell = generic_cell(intent.metric, intent.periods[0], doc.document_id)
        if cell:
            response = {"answer": _amount(cell), "supported": True,
                        "sources": [{"document": cell.document, "page": cell.page}]}
            return _label_latest_report(_annotate_sources(response, doc, decision.reason))
    # Unknown report layouts are NOT silently interpreted as Nordea EUR data.
    result = _abstain("The selected report's exact table cell was not verified")
    return _annotate_sources(result, doc, decision.reason)
