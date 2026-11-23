"""Typed and auditable question *interpretation*, not an answer generator.

An edition is where evidence is sought; a target period is a column/date in it.
This layer contains no reported financial numbers, citations, or gold answers.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Literal
from app.question_parser import parse_question, _periods

Kind = Literal["lookup", "compare", "explain", "clarify", "unsupported"]

@dataclass(frozen=True)
class FinancialQuestionPlan:
    metric: str | None
    source_edition: str | None
    target_periods: tuple[str, ...]
    operation: str
    output_unit: str | None
    scopes: tuple[str, ...]
    kind: Kind
    country: str | None = None
    uncertain: str | None = None

_EDITION = re.compile(
    r"(?:(?:according to|from|in|using|under)\s+)?"
    r"(?:(?:the|selected)\s+)?"
    r"(?:(?:Q[1-4]\s*20\d{2}|20\d{2}\s*Q[1-4])\s+)"
    r"(?:financial\s+)?(?:factbook|quarterly report|interim report|report|pdf|document)\b",
    re.I,
)
_EDITION_POST = re.compile(
    r"\b(?:according to|from|in|using|selected)\s+(?:the\s+)?"
    r"(?:factbook|quarterly report|interim report|report|pdf)\s+"
    r"(?:for\s+)?(Q[1-4]\s*20\d{2}|20\d{2}\s*Q[1-4])\b", re.I,
)
_EXPLAIN = re.compile(
    r"\b(?:why|explain|factors?|drivers?|reasons?|accounting presentation change|"
    r"what (?:accounting|presentation) change|what changed|describe|what led to|attributed to|"
    r"which (?:home markets?|countries|factors?)|what (?:two )?factors|what happened to|"
    r"what (?:does|did) .{0,70} say about|what (?:has|was) implemented|"
    r"how is .{0,90} (?:organised|organized|structured)|"
    r"what does .{0,90} (?:division|business area) provide)\b", re.I,
)
_COMPARE = re.compile(r"\b(?:between|versus|vs\.?|than|difference|how much (?:more|less|higher|lower)|"
                      r"compare|compared|changed|change|increase|increased|decline|declined|"
                      r"decrease|decreased|fell|fall|grow|grew|growth)\b", re.I)
_UNAVAILABLE = re.compile(r"\b(?:forecast|projection|projected|will be|will earn|predict)\b",re.I)


def plan_question(question: str) -> FinancialQuestionPlan:
    original = question.strip()
    source_edition = None
    target_text = original
    for pat in (_EDITION, _EDITION_POST):
        match = pat.search(original)
        if match:
            src_periods = _periods(match.group(0))
            if len(src_periods) == 1:
                source_edition = src_periods[0]
                target_text = original[:match.start()] + " " + original[match.end():]
                break
    intent = parse_question(target_text)
    targets = intent.periods
    if not targets and source_edition and not _UNAVAILABLE.search(original):
        # A source-edition-only scalar question normally refers to that quarter.
        targets = (source_edition,)
    operation = intent.operation
    if _EXPLAIN.search(original) and not re.search(r"\bhow (?:much|many)\b", original, re.I):
        kind: Kind = "explain"
        operation = "explain"
    elif _UNAVAILABLE.search(original) and not targets:
        kind = "unsupported"
    elif intent.needs_clarification:
        kind = "clarify"
    elif _COMPARE.search(target_text) and len(targets) >= 2:
        kind = "compare"
        if operation == "lookup":
            operation = "absolute_change"
    elif len(targets) >= 2:
        kind = "clarify"
    else:
        kind = "lookup"
    if re.search(r"\b(?:percentage points?|pp)\b",original,re.I):
        operation = "percentage_points" if kind == "compare" else "lookup"
    elif re.search(
        r"\b(?:percentage|percent) (?:growth|change|decline|decrease|increase)\b|"
        r"by what percentage|what percentage|including (?:its |the )?percentage change|"
        r"both.{0,60}\bpercent(?:age)?\b",
        original,re.I,
    ):
        operation = "percentage_change"
    elif re.search(r"\b(?:fall|fell|decline|declined|decrease|decreased|drop|dropped)\b",original,re.I) and kind == "compare":
        operation = "decline"
    elif kind == "lookup":
        operation = "lookup"  # 'as a percentage' is an OUTPUT UNIT, not an operation.
    unit = None  # Currency unit is determined by evidence, not issuer-agnostic vocabulary.
    if re.search(r"\b(?:SEK|EUR|NOK|DKK|USD|GBP)\s*(?:m|mn|million|millions)\b",original,re.I):
        match = re.search(r"\b(SEK|EUR|NOK|DKK|USD|GBP)\s*(?:m|mn|million|millions)\b",original,re.I)
        unit = match[1].upper()+"m"
    elif re.search(r"\b(?:SEK|EUR|NOK|DKK|USD|GBP)\s*(?:bn|billion|billions)\b",original,re.I):
        match = re.search(r"\b(SEK|EUR|NOK|DKK|USD|GBP)\s*(?:bn|billion|billions)\b",original,re.I)
        unit = match[1].upper()+"bn"
    elif re.search(r"\b(?:as a decimal|not a percentage)\b",original,re.I):
        unit = "ratio"
    elif re.search(r"\b(?:percentage|percent)\b",original,re.I) and kind=="lookup":
        unit = "percent"
    elif re.search(r"\b(?:basis points?|bps?)\b",original,re.I):
        unit = "basis_points"
    return FinancialQuestionPlan(
        metric=intent.metric, source_edition=source_edition,
        target_periods=targets, operation=operation, output_unit=unit,
        scopes=intent.scopes, kind=kind, country=intent.country,
        uncertain=intent.uncertain if kind not in ("explain","unsupported") else None,
    )
