"""Strict automatic/manual PDF selection. No embedding-based source guessing."""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Literal

from app.document_registry import DocumentRecord


@dataclass(frozen=True)
class DocumentSelection:
    mode: Literal["auto", "manual", "compare"] = "auto"
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    documents: tuple[DocumentRecord, ...]
    reason: str
    clarification: str | None = None


def _issuer_matches(question: str, reports: list[DocumentRecord]) -> list[str]:
    """Return explicitly named issuers in the order they appear in the question.

    Matching uses exact registry issuer names only. Ordering is important for
    directional cross-bank questions (e.g. "SEB or Handelsbanken") and must
    not depend on issuer-name length or registry iteration order.
    """
    mentions: list[tuple[int, int, str]] = []
    known = {d.issuer for d in reports if d.issuer and d.issuer != "Unknown"}
    for issuer in known:
        expr = r"(?<!\w)" + re.escape(issuer) + r"(?!\w)"
        match = re.search(expr, question, flags=re.I)
        if match:
            mentions.append((match.start(), -len(issuer), issuer))
    return [issuer for _, _, issuer in sorted(mentions)]


def _edition_mentions(question: str) -> list[tuple[int, int]]:
    result = []
    patterns = (
        (r"\bQ([1-4])\s*(20\d{2})\b", lambda m: (int(m[2]), int(m[1]))),
        (r"\b(20\d{2})\s*Q([1-4])\b", lambda m: (int(m[1]), int(m[2]))),
        (r"\bQ([1-4])(2\d)\b", lambda m: (2000 + int(m[2]), int(m[1]))),
    )
    for regex, convert in patterns:
        for match in re.finditer(regex, question, re.I):
            point = convert(match)
            if point not in result:
                result.append(point)
    # 'between Q2 and Q3 2026' gives a shared year for both quarters.
    for m in re.finditer(r"\bQ([1-4])\s+(?:and|to|through|versus|vs\.?|or)\s+Q([1-4])\s*(20\d{2})\b",question,re.I):
        pair=(int(m[3]),int(m[1]))
        if pair not in result:result.insert(0,pair)
    return result


def _explicit_edition_request(question: str) -> bool:
    """True only when wording identifies a report as the source edition.

    Avoid treating a target period followed by ordinary wording such as
    "profit for Q4 2026" as a source selection. A generic preposition like
    "in" must be immediately attached to the quarter and report noun.
    "According to the Q3 2026 report" and "from the Q3 factbook" are source
    instructions; "what was profit in Q4 2026?" is a target-period request.
    """
    return bool(re.search(
        r"\b(?:according to|from|using|selected)\s+(?:the\s+)?"
        r"(?:Q[1-4]\s*20\d{2}|20\d{2}\s*Q[1-4])\s+"
        r"(?:financial\s+)?(?:factbook|interim report|quarterly report|report|pdf|document)\b|"
        r"\bin\s+(?:the\s+)?(?:Q[1-4]\s*20\d{2}|20\d{2}\s*Q[1-4])\s+"
        r"(?:financial\s+)?(?:factbook|interim report|quarterly report|report|pdf|document)\b|"
        r"\b(?:source report|report edition|selected report edition)\b",
        question, re.I,
    ))


def _comparison_requested(question: str) -> bool:
    """Detect explicit relational comparisons, including adjective questions.

    Phrasings such as "Which had higher operating profit, A or B, and by how
    much?" are comparison requests even though they contain neither "compare"
    nor "than". Keep this predicate shared by automatic source pairing and
    resolver safety checks.
    """
    return bool(re.search(
        r"\b(?:compare|comparison|difference|between|versus|vs\.?|than|"
        r"higher|lower|larger|smaller|greater|less|more|better|worse|outperform|"
        r"change|changed|increase|increased|decrease|decreased|decline|declined|rise|rose|grew|growth|"
        r"side[- ]by[- ]side|by\s+how\s+much)\b",
        question, re.I))


def _has_unqualified_profit_reference(question: str) -> bool:
    """Return True when a profit mention lacks an explicit measure.

    The question parser intentionally defaults a *single-bank* plain 'profit'
    request to net profit. That convenience must not authorize a cross-bank
    comparison: 'profit' can mean net profit, operating profit, or another
    reported measure, and silently comparing unlike concepts is unsafe.
    """
    q = question.replace("’", "'").replace("–", "-").replace("—", "-")
    qualified = re.compile(
        r"\b(?:net|operating|gross|pre[ -]?tax)\s+profit\b|"
        r"\bprofit\s+(?:before|after|margin|for\s+the\s+period|attributable)\b",
        re.I,
    )
    covered = [False] * len(q)
    for match in qualified.finditer(q):
        for index in range(match.start(), match.end()):
            covered[index] = True

    for match in re.finditer(r"\bprofit\b", q, re.I):
        if any(covered[match.start():match.end()]):
            continue
        # Ignore an explicitly negated alternative such as 'net interest
        # income, not profit'; it is not the requested comparison metric.
        prefix = q[max(0, match.start() - 30):match.start()]
        if re.search(r"\bnot\s+(?:(?:the|its|their)\s+)?$", prefix, re.I):
            continue
        return True
    return False


def resolve_documents(question: str, selection: DocumentSelection,
                      reports: list[DocumentRecord]) -> Resolution:
    """Fail closed on conflicting manual choices and ambiguous automatic picks."""
    if selection.mode not in ("auto", "manual", "compare"):
        raise ValueError("Invalid document selection mode")
    by_id = {doc.document_id: doc for doc in reports}
    if not reports:
        return Resolution((), "No indexed reports", "No PDF reports have been indexed yet.")

    issuers = _issuer_matches(question, reports)
    editions = _edition_mentions(question)
    from app.financial_plan import plan_question
    question_plan = plan_question(question)
    from app.question_parser import parse_question
    parsed_intent_early = parse_question(question)
    if (selection.mode == "auto" and len(issuers) == 2
            and parsed_intent_early.metric == "net_profit"
            and _has_unqualified_profit_reference(question)
            and (_comparison_requested(question) or re.search(r"\bside[- ]by[- ]side\b", question, re.I))):
        return Resolution(
            (), "Ambiguous cross-bank profit measure",
            "Which financial measure should I put side by side: net profit, operating profit, or profit before credit losses?",
        )
    if selection.mode == "auto" and _comparison_requested(question) and len(issuers) > 2:
        return Resolution(
            (), "Comparison names more than two banks",
            "Automatic comparisons currently support two banks at a time. Please choose two of: "
            + ", ".join(issuers) + ".",
        )
    # A source edition and an older target quarter are different concepts.
    # Only the source edition can create a *manual selection* conflict.
    source_period = question_plan.source_edition
    selected = tuple(by_id[i] for i in selection.document_ids if i in by_id)
    if len(selected) != len(selection.document_ids) or len({d.document_id for d in selected}) != len(selected):
        raise ValueError("One or more selected document IDs do not exist in the index")

    if selection.mode == "manual":
        if len(selected) != 1:
            raise ValueError("Manual mode requires exactly one document ID")
        if len(issuers)>1:
            return Resolution((), "Multiple issuers requested", "This question requires two banks; choose Compare reports.")
        if _comparison_requested(question) and len(editions)>1 and not source_period:
            # One indexed issuer report may contain both historical columns.
            # When multiple editions exist, manual must never silently add or
            # substitute another indexed report.
            requested=selected[0]
            same_issuer=[d for d in reports if d.issuer==requested.issuer]
            if len(same_issuer)>1:
                return Resolution((), "Multiple sources requested", "This question needs more than one report. Choose Compare reports and select the two PDFs.")
        doc = selected[0]
        if issuers and doc.issuer not in issuers:
            return Resolution((), "Manual issuer conflict", f"You selected {doc.display_name}, but your question mentions {', '.join(issuers)}. Select the matching report or Automatic.")
        if source_period and doc.report_year and doc.report_quarter and \
                source_period != f"Q{doc.report_quarter}{doc.report_year%100:02d}":
            return Resolution((), "Manual edition conflict", f"You selected {doc.display_name}, but requested a different report edition. Change the dropdown to the requested report.")
        return Resolution((doc,), "Manual selection strictly restricts the evidence")

    if selection.mode == "compare":
        if len(selected) not in (2,):
            raise ValueError("Compare mode requires exactly two selected document IDs")
        if not _comparison_requested(question):
            return Resolution((), "Comparison needs explicit operation", "Ask a comparison question, or select one report for a single-value lookup.")
        if issuers and not all(d.issuer in issuers for d in selected):
            return Resolution((), "Selected issuers conflict", "Your comparison mentions a different bank than one of the selected reports.")
        return Resolution(selected, "Two explicitly selected source reports")

    # Explicit source edition wins over a historical TARGET period, except
    # when the question actually asks for a two-edition comparison.
    if question_plan.source_edition and question_plan.kind != 'compare' and len(issuers) <= 1:
        y=2000+int(question_plan.source_edition[2:]); q=int(question_plan.source_edition[1])
        exact=[d for d in reports if (d.report_year,d.report_quarter)==(y,q)
               and (not issuers or d.issuer==issuers[0])]
        if len(exact)==1:
            return Resolution((exact[0],),"Explicit source report edition; target period remains independent")
        return Resolution((),"Ambiguous source edition","Choose the source report edition explicitly.")

    # An exact balance-sheet reporting date (e.g. 'at 30 September 2026')
    # designates a quarter even when no explicit Q3 token appears. Do not
    # infer a quarter from 'September' without a year.
    from app.question_parser import _periods
    comparison_periods = _periods(question)
    if not editions and len(comparison_periods) == 1:
        target=comparison_periods[0]
        if re.fullmatch(r'Q[1-4]\d{2}',target):
            editions=[(2000+int(target[2:]),int(target[1]))]
    # Automatic mode: a comparison can select TWO reports only when every
    # named issuer/edition has exactly one matching document. Never rely on
    # retrieval similarity or silently choose among duplicate editions.
    from app.question_parser import parse_question
    parsed_intent = parse_question(question)
    explicit_metric = parsed_intent.metric is not None
    # The parser's single-bank default (plain 'profit' -> net profit) is not
    # sufficiently explicit for cross-bank comparison. Keep the convenience
    # for a single selected issuer, but ask the user to qualify the measure
    # before pairing reports from different banks.
    if parsed_intent.metric == "net_profit" and _has_unqualified_profit_reference(question):
        explicit_metric = False
    if _comparison_requested(question) and explicit_metric and len(issuers) == 2 and len(editions) == 1:
        y, q = editions[0]
        paired = []
        for issuer in issuers:
            matches = [d for d in reports if d.issuer == issuer and
                       (d.report_year, d.report_quarter) == (y, q)]
            if len(matches) != 1:
                return Resolution((), "Ambiguous cross-bank comparison",
                                  "Select the two matching report editions explicitly using Compare reports.")
            paired.append(matches[0])
        return Resolution(tuple(paired), "Automatically matched both named banks and their report editions")
    if (selection.mode == "auto" and _comparison_requested(question)
            and len(issuers) == 2 and explicit_metric and not editions):
        # A periodless two-bank comparison may use the latest common edition
        # only when each issuer has one unique latest report and both editions
        # cover exactly the same quarter/year. Otherwise require an explicit
        # reporting period; never pair reports from different periods.
        latest_docs = []
        latest_periods = []
        for issuer in issuers:
            issuer_docs = [d for d in reports if d.issuer == issuer
                           and d.report_year and d.report_quarter]
            if not issuer_docs:
                latest_docs = []
                break
            latest_period = max((d.report_year, d.report_quarter) for d in issuer_docs)
            latest_matches = [d for d in issuer_docs
                              if (d.report_year, d.report_quarter) == latest_period]
            if len(latest_matches) != 1:
                return Resolution((), "Duplicate latest report edition in cross-bank comparison",
                                  f"I found duplicate latest reports for {issuer}. Select the exact report editions explicitly.")
            latest_docs.append(latest_matches[0])
            latest_periods.append(latest_period)
        if len(latest_docs) == 2:
            if latest_periods[0] == latest_periods[1]:
                y, q = latest_periods[0]
                return Resolution(
                    tuple(latest_docs),
                    f"Automatically matched both banks to their unique latest common edition (Q{q} {y})",
                )
            return Resolution(
                (), "Latest cross-bank report periods differ",
                "The banks' latest indexed reports cover different periods. Specify one common quarter/year or select the two report editions explicitly.",
            )
    if (selection.mode == "auto" and _comparison_requested(question)
            and len(issuers) == 2 and not explicit_metric):
        return Resolution(
            (), "Cross-bank comparison metric missing",
            "Which metric would you like to compare at Group level? For example: net profit, operating profit, or CET1 ratio.",
        )
    if _comparison_requested(question) and len(issuers) == 1 and len(editions) == 2:
        paired = []
        for y, q in editions:
            matches = [d for d in reports if d.issuer == issuers[0] and
                       (d.report_year, d.report_quarter) == (y, q)]
            if len(matches) > 1:
                return Resolution((), "Ambiguous comparison edition",
                                  "More than one report matches a requested edition. Select both reports explicitly.")
            if len(matches) == 1:
                paired.append(matches[0])
        if len(paired) == 2 and paired[0].document_id != paired[1].document_id:
            return Resolution(tuple(paired), "Automatically matched both specified reporting editions")
        if len(reports) and len([d for d in reports if d.issuer == issuers[0]]) == 1:
            # One report can contain both historical quarterly columns, but
            # the answerer must still verify each cell in that report.
            return Resolution(tuple(d for d in reports if d.issuer == issuers[0]),
                              "Only one report for issuer; verify both quarter cells")
        return Resolution((), "Missing or ambiguous comparison edition",
                          "Select Compare reports and choose two valid reporting editions.")

    # Automatic mode: known issuer name dominates retrieval similarity.
    candidates = reports
    if issuers:
        candidates = [d for d in candidates if d.issuer in issuers]
        if not candidates:
            return Resolution((), "Issuer missing", "No indexed report matches the bank named in your question.")
        if len(issuers) > 1:
            return Resolution((), "Multi-issuer question", "Select Compare reports and choose the two reports explicitly.")
    elif len({d.issuer for d in candidates}) > 1:
        return Resolution((), "Bank ambiguous", "Which bank should I use? Choose a report from the dropdown or mention its bank name.")

    if len(editions) >= 2:
        # Distinguish the edition providing the evidence from the period asked
        # about: 'According to the Q3 2026 report, what about Q3 2030?'
        # still selects the Q3 2026 report, but the answer must abstain if
        # the requested future period is not there.
        if not _comparison_requested(question) and _explicit_edition_request(question):
            y, q = editions[0]
            matches = [d for d in candidates if (d.report_year, d.report_quarter) == (y, q)]
            if len(matches) == 1:
                return Resolution((matches[0],), "Explicit source report edition identified")
        return Resolution((), "Multiple report editions requested",
                          "Specify a comparison between the periods or choose the intended edition.")

    if editions:
        year, quarter = editions[0]
        exact = [d for d in candidates if (d.report_year, d.report_quarter) == (year, quarter)]
        if exact:
            if len(exact) == 1:
                return Resolution((exact[0],), "Exact issuer and reporting edition match")
            return Resolution((), "Ambiguous edition", "Several reports match that bank and quarter. Please select one report.")
        if _explicit_edition_request(question):
            return Resolution((), "Edition not indexed", "The requested report edition is not indexed. Choose an available report.")
        # A target quarter newer than every indexed edition is not a source
        # request. Select the unique latest report solely to establish that
        # the requested future result is absent; answer_with_selection then
        # emits an explicit unavailability response. Do not apply this to
        # historical periods or to ambiguous duplicate latest editions.
        newest = max(candidates, key=lambda d: (d.report_year or 0, d.report_quarter or 0), default=None)
        requested_order = (year, quarter)
        newest_order = ((newest.report_year or 0), (newest.report_quarter or 0)) if newest else (0, 0)
        if newest and requested_order > newest_order:
            same_latest = [d for d in candidates
                           if (d.report_year, d.report_quarter) == (newest.report_year, newest.report_quarter)]
            if len(same_latest) == 1:
                return Resolution((same_latest[0],), "Latest indexed report selected to verify requested future period is unavailable")
            return Resolution((), "Duplicate latest report edition",
                              "Several PDFs match the latest indexed edition. Select one exact report in the sidebar.")
        # Numeric period could be historical within the latest factbook; but
        # if multiple reports are present, do not guess which edition to cite.
        if len(candidates) == 1:
            return Resolution((candidates[0],), "Only indexed report for issuer; value still requires evidence")
        return Resolution((), "Historical period appears in multiple reports", "Several reports might contain that reporting period. Please select the report edition to use.")

    if len(candidates) == 1:
        return Resolution((candidates[0],), "Only matching indexed report")

    # A single explicitly named issuer with no requested quarter defaults to
    # its latest indexed report. This is safer and more useful than asking the
    # user to choose between Q2/Q3 for an ordinary scalar question. The caller
    # must still verify the value inside that selected report. Duplicate files
    # for the same exact edition remain ambiguous and are never guessed.
    if len(issuers) == 1 and candidates:
        ordered = sorted(
            candidates,
            key=lambda d: (d.report_year or 0, d.report_quarter or 0,
                           d.indexed_at or "", d.document_id),
            reverse=True,
        )
        newest = ordered[0]
        same_edition = [
            d for d in ordered
            if (d.report_year, d.report_quarter) == (newest.report_year, newest.report_quarter)
        ]
        if len(same_edition) == 1:
            edition = (f"Q{newest.report_quarter} {newest.report_year}"
                       if newest.report_quarter and newest.report_year else newest.display_name)
            return Resolution(
                (newest,),
                f"Latest indexed edition selected automatically ({edition}); older editions were not mixed",
            )
        options = "; ".join(d.display_name for d in same_edition)
        return Resolution(
            (), "Duplicate latest report edition",
            f"Several indexed PDFs match the latest {newest.issuer} edition: {options}. Select one exact report in the sidebar.",
        )

    return Resolution((), "Multiple editions", "Which report edition should I use? Choose a PDF from the dropdown or name its reporting quarter.")
