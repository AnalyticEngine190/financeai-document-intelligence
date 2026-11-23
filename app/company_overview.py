"""Evidence-first company overview builder for indexed financial reports.

The overview is composed from page text already stored in the selected Chroma
collection. It deliberately does not call the metric-oriented question planner,
LLM, market provider, or any other issuer's document. All displayed claims have
physical-PDF page citations.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class PageEvidence:
    page: int
    text: str


# The labels are generic banking-segment vocabulary, not issuer-specific facts.
# Longer names are evaluated first to prevent partial duplicate matches.
SEGMENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Large Corporates & Institutions", re.compile(r"\bLarge\s+Corporates\s*(?:&|and)\s*Institutions\b", re.I)),
    ("Corporate & Investment Banking", re.compile(r"\bCorporate\s*(?:&|and)\s*Investment\s+Banking\b", re.I)),
    ("Asset & Wealth Management", re.compile(r"\bAsset\s*(?:&|and)\s*Wealth\s+Management\b", re.I)),
    ("Personal Banking", re.compile(r"\bPersonal\s+Banking\b", re.I)),
    ("Retail Banking", re.compile(r"\bRetail\s+Banking\b", re.I)),
    ("Consumer Banking", re.compile(r"\bConsumer\s+Banking\b", re.I)),
    ("Business Banking", re.compile(r"\bBusiness\s+Banking\b", re.I)),
    ("Commercial Banking", re.compile(r"\bCommercial\s+Banking\b", re.I)),
    ("Institutional Banking", re.compile(r"\bInstitutional\s+Banking\b", re.I)),
    ("Investment Banking", re.compile(r"\bInvestment\s+Banking\b", re.I)),
    ("Wealth Management", re.compile(r"\bWealth\s+Management\b", re.I)),
    ("Private Banking", re.compile(r"\bPrivate\s+Banking\b", re.I)),
    ("Markets", re.compile(r"\b(?:Global\s+)?Markets\b", re.I)),
    ("Insurance", re.compile(r"\bInsurance\b", re.I)),
    ("Group Functions", re.compile(r"\bGroup\s+Functions\b", re.I)),
)

BUSINESS_SECTION = re.compile(
    r"\b(?:business\s+areas?|business\s+segments?|operating\s+segments?|"
    r"segment\s+reporting|reporting\s+by\s+segment|business\s+units?)\b", re.I
)
VOLUME_CONTEXT = re.compile(r"\b(?:volumes?|EURbn|SEKbn|DKKbn|NOKbn|total\s+lending|total\s+deposits)\b", re.I)

# Used only to recognize country names in a page that is already identified as
# a business-area/segment page. A country is reported only when present in the
# selected PDF text; this list does not assert the bank operates there.
COUNTRIES = (
    "Denmark", "Finland", "Norway", "Sweden", "Iceland", "United Kingdom",
    "Ireland", "Germany", "France", "Netherlands", "Belgium", "Luxembourg",
    "Switzerland", "Austria", "Spain", "Portugal", "Italy", "Poland",
    "Estonia", "Latvia", "Lithuania", "United States", "Canada", "Brazil",
    "Mexico", "China", "Hong Kong", "Singapore", "Japan", "Australia",
)

METRIC_LABELS = {
    "Personal Banking": ("Total lending", "Total deposits"),
    "Business Banking": ("Total lending", "Total deposits"),
    "Commercial Banking": ("Total lending", "Total deposits"),
    "Large Corporates & Institutions": ("Total lending", "Total deposits"),
    "Asset & Wealth Management": ("Investment product AuM",),
    "Wealth Management": ("Investment product AuM", "Total assets under management"),
    "Asset Management": ("Investment product AuM", "Total assets under management"),
}

_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def load_indexed_pages(document_id: str, collection=None) -> list[PageEvidence]:
    """Load and join chunks from exactly one indexed document, grouped by PDF page."""
    if not document_id or not isinstance(document_id, str):
        raise ValueError("A document ID is required")
    if collection is None:
        from app.vector_store import get_collection
        collection = get_collection()
    result = collection.get(
        where={"document_id": document_id},
        include=["documents", "metadatas"],
    )
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    if len(documents) != len(metadatas):
        raise RuntimeError("The selected document index returned mismatched chunk metadata")

    grouped: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for text, metadata in zip(documents, metadatas):
        if not isinstance(metadata, dict) or str(metadata.get("document_id", "")) != document_id:
            # Re-check the filter result to prevent cross-document source leakage.
            continue
        try:
            page = int(metadata.get("page_number"))
            chunk_index = int(metadata.get("chunk_index", 0))
        except (TypeError, ValueError):
            continue
        if page < 1 or not isinstance(text, str) or not text.strip():
            continue
        grouped[page].append((chunk_index, text.strip()))

    return [
        PageEvidence(page=page, text="\n".join(text for _, text in sorted(chunks)))
        for page, chunks in sorted(grouped.items())
    ]


def _segment_pages(pages: Iterable[PageEvidence]) -> tuple[list[str], dict[str, list[int]], list[PageEvidence]]:
    segments: list[str] = []
    citations: dict[str, list[int]] = defaultdict(list)
    business_pages: list[PageEvidence] = []
    for page in pages:
        text = page.text
        # Only the page's opening heading block can establish the segment. A
        # word such as "insurance", "treasury" or "wealth management" appearing
        # deep in another segment's table is not itself a business-area heading.
        top_lines = [line.strip() for line in text.splitlines() if line.strip()][:12]
        has_business_context = any(BUSINESS_SECTION.fullmatch(line) for line in top_lines)
        if not has_business_context:
            continue
        business_pages.append(page)
        for line in top_lines:
            for label, pattern in SEGMENTS:
                # Exact heading-line matches avoid treating table rows such as
                # "Net insurance result" as a separate Insurance business segment.
                if pattern.fullmatch(line):
                    if label not in segments:
                        segments.append(label)
                    if page.page not in citations[label]:
                        citations[label].append(page.page)
                    break

    # Suppress sub-area labels when the selected report explicitly groups them
    # under a broader operating segment. For example, Private Banking and Wealth
    # Management can be activities within Asset & Wealth Management, not peers.
    parent_child = {
        "Wealth Management": "Asset & Wealth Management",
        "Private Banking": "Asset & Wealth Management",
    }
    segments = [s for s in segments if not (parent_child.get(s) in segments)]
    return segments, citations, business_pages


def _line_value(text: str, label: str) -> str | None:
    """Read the first value after an exact row label on a page with EURbn context."""
    if not re.search(r"\b(?:EURbn|SEKbn|DKKbn|NOKbn)\b", text, re.I):
        return None
    lines = [line.strip() for line in text.splitlines()]
    label_re = re.compile(r"^\s*" + re.escape(label) + r"\s*(.*?)\s*$", re.I)
    for i, line in enumerate(lines):
        row = label_re.match(line)
        if not row:
            continue
        # PyMuPDF may keep a table row label and all numeric columns on one
        # physical text line, or place the numeric cells on the next line.
        inline = _NUMBER.search(row.group(1) or "")
        if inline:
            return inline.group(0).replace(",", "")
        for candidate in lines[i + 1:i + 3]:
            match = _NUMBER.search(candidate)
            if match:
                return match.group(0).replace(",", "")
    return None


def _period_header_present(text: str, report_year: int | None, report_quarter: int | None) -> bool:
    """Require an explicit selected-edition header before using a table value."""
    if not report_year or not report_quarter:
        return False
    yy = str(report_year)[-2:]
    patterns = (
        rf"\bQ{report_quarter}{yy}\b",              # Q326
        rf"\bQ{report_quarter}\s+{report_year}\b", # Q3 2026
        rf"\b{report_year}\s+Q{report_quarter}\b", # 2026 Q3
        rf"\b{report_quarter}Q{yy}\b",              # 3Q26
    )
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _metric_highlights(segment: str, pages: list[PageEvidence],
                       report_year: int | None, report_quarter: int | None) -> tuple[list[str], list[int]]:
    labels = METRIC_LABELS.get(segment, ())
    facts: list[str] = []
    used_pages: list[int] = []
    for label in labels:
        for page in pages:
            # Require the segment heading, unit, and selected reporting-period
            # header on the same page before extracting a volume.
            if not re.search(re.escape(segment), page.text, re.I):
                continue
            if not _period_header_present(page.text, report_year, report_quarter):
                continue
            value = _line_value(page.text, label)
            if value is None:
                continue
            currency_unit = re.search(r"\b(EURbn|SEKbn|DKKbn|NOKbn)\b", page.text, re.I)
            unit = currency_unit.group(1) if currency_unit else ""
            pretty_label = "assets under management" if "aum" in label.casefold() else label.casefold()
            facts.append(f"{pretty_label}: {value} {unit}")
            used_pages.append(page.page)
            break
    return facts, used_pages




# Report-layout adapters for the two indexed quarterly-report formats that do
# not expose Nordea-style BUSINESS AREAS headings. These are section/table
# labels copied from the selected report structure; values and descriptions
# are still read from that report's own indexed page text.
_SEB_SEGMENT_LABELS = (
    "Corporate & Investment Banking",
    "Business & Retail Banking",
    "Wealth & Asset Management",
    "Baltic",
)
_HANDELSBANKEN_SEGMENT_LABELS = (
    "Handelsbanken Sweden",
    "Handelsbanken UK",
    "Handelsbanken Norway",
    "Handelsbanken the Netherlands",
    "Handelsbanken Markets",
)


def _flat(text: str) -> str:
    # Rejoin words split by line-end hyphens in PDF extraction (e.g. "advisory-\n-driven").
    text = re.sub(r"(?<=\w)-\s+(?=[a-z])", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def _page_has_heading(page: PageEvidence, heading: str) -> bool:
    """Check an actual page heading, not a stray mention deep in a table."""
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    target = _flat(heading).casefold()
    # A segment name may also appear in another division's prose (for example
    # Wealth & Asset Management mentions the Baltic division as a distributor).
    # Accept only a standalone heading line in the page's opening block.
    return any(_flat(line).casefold() == target for line in lines[:8])


def _first_description(page: PageEvidence, heading: str) -> str | None:
    """Read the first prose paragraph after a division heading, if present."""
    lines = page.text.splitlines()
    start = None
    for i, line in enumerate(lines[:12]):
        if _flat(line).casefold() == _flat(heading).casefold():
            start = i + 1
            break
    if start is None:
        return None
    paragraph: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if re.fullmatch(r"(?:Income statement|Business volumes|Business development)", stripped, re.I):
            break
        # A new section title ends the description; table labels/numbers are not
        # turned into natural-language claims.
        if paragraph and re.match(r"^(?:Q[1-4]|Jan[- ]Sep|Full year|SEK m)\b", stripped, re.I):
            break
        paragraph.append(stripped)
    text = _flat(" ".join(paragraph))
    if not text or not re.search(r"[A-Za-z]{3,}", text):
        return None
    # Keep the first two complete sentences, and never infer details from a
    # table row. Some report pages contain only a heading and tables.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return " ".join(sentences[:2]).strip() or None


def _make_overview_result(issuer: str, report_name: str, document_id: str,
                          report_year: int | None, report_quarter: int | None,
                          segments: list[str], segment_pages: dict[str, list[int]],
                          descriptions: dict[str, str], group_functions: bool,
                          intro: str, extra_lines: list[str] | None = None) -> dict:
    edition = (f"Q{report_quarter} {report_year}" if report_quarter and report_year
               else str(report_year) if report_year else "selected indexed edition")
    answer = [f"**Overview based on {report_name}**", intro]
    sources_by_page: dict[int, dict] = {}
    for label, page_numbers in segment_pages.items():
        for page_no in page_numbers:
            sources_by_page[page_no] = {"document": report_name, "report": report_name,
                                         "document_id": document_id, "page": page_no}
    for segment in segments:
        detail = descriptions.get(segment)
        if detail:
            answer.append(f"- **{segment}:** {detail}")
        else:
            answer.append(f"- **{segment}:** The selected report presents this as a separate business segment.")
    if group_functions:
        answer.append("**Group Functions:** The report lists Group Functions separately from its customer/business divisions.")
    for line in extra_lines or []:
        answer.append(line)
    answer.append(
        "This is a report-based view of how the selected edition presents the business; "
        "it is not an exhaustive product catalogue or an independently verified current corporate profile."
    )
    return {
        "kind": "overview", "answer": "\n\n".join(answer), "supported": bool(segments),
        "needs_clarification": False,
        "sources": [sources_by_page[p] for p in sorted(sources_by_page)],
        "selected_document_id": document_id, "overview_period": edition,
        "selected_report": report_name, "evidence_pages": sorted(sources_by_page),
        "business_segments": list(segments), "group_functions_reported": group_functions,
        "geographies_in_segment_tables": [],
        # Keep the supporting page map available for precise division-level
        # follow-up questions routed through the same evidence-first builder.
        "segment_page_map": {label: list(page_numbers)
                             for label, page_numbers in segment_pages.items()},
    }


def _seb_report_overview(issuer: str, report_name: str, document_id: str,
                         report_year: int | None, report_quarter: int | None,
                         pages: list[PageEvidence]) -> dict | None:
    """Read SEB's four divisions from its actual report table/detail-page layout.

    Important layout detail: the table of contents also contains the phrases
    "Business segments" and "Income statement by segment". It must not be
    mistaken for the segment table. The real table page begins with the
    standalone heading "Business segments"; dedicated division pages provide
    the clearest authoritative labels and descriptions.
    """
    def opening_lines(page: PageEvidence, count: int = 12) -> list[str]:
        return [line.strip() for line in page.text.splitlines() if line.strip()][:count]

    table_page = next((p for p in pages
                       if any(re.fullmatch(r"Business segments", line, re.I)
                              for line in opening_lines(p, 4))
                       and any(re.fullmatch(r"Income statement by segment", line, re.I)
                               for line in opening_lines(p, 8))), None)

    # Extract the four divisions from their dedicated section pages. This is
    # intentionally not dependent on the interleaved multi-column table text:
    # PDF text extraction places the words in column order, so a column such
    # as "Corporate & Investment Banking" may be split across separate lines.
    detail_pages: dict[str, PageEvidence] = {}
    for name in _SEB_SEGMENT_LABELS:
        candidate = next((p for p in pages if _page_has_heading(p, name)), None)
        if candidate is not None:
            detail_pages[name] = candidate

    found = [name for name in _SEB_SEGMENT_LABELS if name in detail_pages]
    if len(found) != len(_SEB_SEGMENT_LABELS):
        return None

    citations: dict[str, list[int]] = {}
    descriptions: dict[str, str] = {}
    for name in found:
        detail_page = detail_pages[name]
        citations[name] = [detail_page.page]
        if table_page is not None:
            citations[name].append(table_page.page)
        desc = _first_description(detail_page, name)
        if desc:
            descriptions[name] = desc

    groups = False
    eliminations = False
    if table_page is not None:
        header_lines = opening_lines(table_page, 12)
        header_text = " ".join(header_lines)
        # In the actual multi-column PDF text, "Group" and "Functions" can
        # be separated by the other column labels. They are nevertheless
        # both present in the header block of this identified segment table.
        groups = bool(re.search(r"\bGroup\b", header_text, re.I)
                      and re.search(r"\bFunctions\b", header_text, re.I))
        eliminations = bool(re.search(r"\bEliminations\b", header_text, re.I))
        if groups:
            citations["Group Functions"] = [table_page.page]

    intro = (
        "The report's business-segment structure identifies four divisions — "
        + ", ".join(found) + "."
    )
    if groups:
        intro += " Group Functions are reported separately from the four customer/business divisions."
    if eliminations:
        intro += " Eliminations are shown as a reporting column, not as a business division."
    return _make_overview_result(issuer, report_name, document_id, report_year, report_quarter,
                                 found, citations, descriptions, groups, intro)


def _handelsbanken_report_overview(issuer: str, report_name: str, document_id: str,
                                   report_year: int | None, report_quarter: int | None,
                                   pages: list[PageEvidence]) -> dict | None:
    table_page = next((p for p in pages if
                       re.search(r"Handelsbanken Group\s*[–—-]\s*Business segments", p.text, re.I)), None)
    if table_page is None:
        return None
    flat = _flat(table_page.text)
    # The page's declared segment sentence is available in ordinary reading
    # order, but can be omitted by fitz sort=True where the table columns are
    # positioned above it. Validate the same five home-market/Markets columns
    # directly from the business-segment table as a source-faithful fallback.
    names_present = {
        "Handelsbanken Sweden": bool(re.search(r"\bSweden\b", flat, re.I)),
        "Handelsbanken UK": bool(re.search(r"\bUK\b", flat, re.I)),
        "Handelsbanken Norway": bool(re.search(r"\bNorway\b", flat, re.I)),
        "Handelsbanken the Netherlands": bool(re.search(r"\bNether\s*-?\s*lands\b", flat, re.I)),
        "Handelsbanken Markets": bool(re.search(r"\bMarkets\b", flat, re.I)),
    }
    found = [name for name in _HANDELSBANKEN_SEGMENT_LABELS if names_present[name]]
    if len(found) != len(_HANDELSBANKEN_SEGMENT_LABELS):
        return None
    citations: dict[str, list[int]] = {name: [table_page.page] for name in found}
    descriptions: dict[str, str] = {}
    for name in found:
        detail_page = next((p for p in pages if p.page != table_page.page and _page_has_heading(p, name)), None)
        if detail_page is not None:
            citations[name].append(detail_page.page)
        if name == "Handelsbanken Markets":
            descriptions[name] = "A separate Markets business segment, reported alongside the geographic home-market segments."
        else:
            descriptions[name] = "A home-market banking segment with its own income statement and business-volume reporting in the selected report."
    markets_page = next((p for p in pages if _page_has_heading(p, "Handelsbanken Markets")), None)
    extra = []
    if markets_page is not None:
        market_note = re.search(
            r"A large proportion of the fee and commission income and net gains/losses on financial transactions related to Markets’ products is recognised in the profit/loss of the respective home market segment\.",
            _flat(markets_page.text), re.I,
        )
        if market_note:
            extra.append("**Markets reporting note:** " + market_note.group(0))
    intro = (
        "The report explicitly identifies five business segments: " + ", ".join(found) +
        ". The geographic home-market segments are reported alongside Handelsbanken Markets. "
        "The report presents Other and adjustments/eliminations separately from this five-segment list."
    )
    return _make_overview_result(issuer, report_name, document_id, report_year, report_quarter,
                                 found, citations, descriptions, False, intro, extra)


def _heading_text(line: str) -> str:
    """Normalize PDF/Markdown heading decoration while retaining exact wording."""
    return re.sub(r"^[#*\s]+|[#*\s]+$", "", line.strip()).strip()


def _complementary_business_areas_overview(
    issuer: str, report_name: str, document_id: str,
    report_year: int | None, report_quarter: int | None,
    pages: list[PageEvidence],
) -> dict | None:
    """Read reports that describe business areas as named activity sections.

    This layout is different from banks that list customer-facing divisions in
    a business-area table. It is accepted only when one selected-document page
    has a top-level 'Business areas' heading, an explicit scope sentence, and
    all three standalone activity headings. Descriptions are copied from the
    text between those headings; no activities are inferred from the bank name.
    """
    area_labels = ("Banking activities", "Mortgage activities", "Leasing activities")
    selected_page = None
    selected_lines: list[str] = []
    heading_positions: dict[str, int] = {}
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines()]
        nonempty = [(i, _heading_text(line)) for i, line in enumerate(lines) if line.strip()]
        top = [text.casefold() for _, text in nonempty[:10]]
        if "business areas" not in top:
            continue
        flat = _flat(page.text)
        if not re.search(
            r"business areas reflect all activities in banking, mortgage financing and leasing",
            flat, re.I,
        ):
            continue
        positions: dict[str, int] = {}
        for index, line in enumerate(lines):
            normalized = _heading_text(line).casefold()
            for label in area_labels:
                if normalized == label.casefold():
                    positions[label] = index
        if len(positions) == len(area_labels):
            selected_page = page
            selected_lines = lines
            heading_positions = positions
            break
    if selected_page is None:
        return None

    descriptions: dict[str, str] = {}
    ordered_positions = sorted((index, label) for label, index in heading_positions.items())
    report_footers = re.compile(
        r"^(?:interim financial report\b|navigation\s*:|page\s+\d+\s*$|"
        r"financial review\s*$)", re.I,
    )
    for ordinal, (start, label) in enumerate(ordered_positions):
        stop = ordered_positions[ordinal + 1][0] if ordinal + 1 < len(ordered_positions) else len(selected_lines)
        paragraph_lines: list[str] = []
        for line in selected_lines[start + 1:stop]:
            cleaned = _heading_text(line)
            if not cleaned:
                continue
            if report_footers.match(cleaned):
                break
            if cleaned.casefold() == "complementary business areas":
                continue
            paragraph_lines.append(cleaned)
        description = _flat(" ".join(paragraph_lines))
        # Keep only source prose, not a heading or visual-layout placeholder.
        if not description or not re.search(r"[A-Za-z]{4,}", description):
            return None
        descriptions[label] = description

    edition = (f"Q{report_quarter} {report_year}" if report_quarter and report_year
               else str(report_year) if report_year else "selected indexed edition")
    source = {
        "document": report_name, "report": report_name,
        "document_id": document_id, "page": selected_page.page,
    }
    intro = _flat(
        "The report states that the business areas reflect all activities in banking, "
        "mortgage financing and leasing."
    )
    answer_parts = [f"**Overview based on {report_name}**", intro]
    for label in area_labels:
        answer_parts.append(f"- **{label}:** {descriptions[label]}")
    answer_parts.append(
        "This is a report-based description of the selected edition; it is not an "
        "exhaustive product catalogue or an independently verified current corporate profile."
    )
    return {
        "kind": "overview", "answer": "\n\n".join(answer_parts),
        "supported": True, "needs_clarification": False, "sources": [source],
        "selected_document_id": document_id, "overview_period": edition,
        "selected_report": report_name, "evidence_pages": [selected_page.page],
        "business_segments": list(area_labels),
        "business_areas": list(area_labels),
        "group_functions_reported": False,
        "geographies_in_segment_tables": [],
        "segment_page_map": {label: [selected_page.page] for label in area_labels},
    }


_ACTIVITY_AREAS = ("Banking activities", "Mortgage activities", "Leasing activities")


def _area_pre_tax_profit(text: str) -> tuple[str, str, str] | None:
    """(amount, currency unit, period label) read from one division page's own table.

    Accepted only when the same page shows the unit line, the stacked
    "Q1-Q3 | Q1-Q3 | Index" header with its year row, and a Pre-tax profit row.
    The first data column is the current period.
    """
    header = re.search(
        r"^[^\n]*?\b(Q[1-4]-Q[1-4]|H[12]|Q[1-4])\s+(?:Q[1-4]-Q[1-4]|H[12]|Q[1-4])\s+Index\b[^\n]*\n"
        r"[^\n]*?\b(20\d{2})\s+20\d{2}\s+\d{2}/\d{2}\b", text, re.M)
    unit = re.search(r"Summary of income statement[^\n]*\n[^\n]*?\b([A-Z]{3})m\b", text)
    row = re.search(r"Pre.tax profit\s+(-?\d{1,3}(?:,\d{3})*)\s+-?\d{1,3}(?:,\d{3})*\s+(?:\d+|-)\s", text)
    if not (header and unit and row):
        return None
    return row.group(1), unit.group(1), f"{header.group(1)} {header.group(2)}"


def _activity_areas_from_division_pages(
    issuer: str, report_name: str, document_id: str,
    report_year: int | None, report_quarter: int | None,
    pages: list[PageEvidence],
) -> dict | None:
    """Fallback for Banking / Mortgage / Leasing reports whose 'Business areas'
    page prints its three columns side by side (prose cannot be separated).

    Needs a 'Business areas' page naming all three activities AND one division
    page per activity; figures come from each division page's own income table.
    Anything missing returns None.
    """
    overview = next((p for p in pages if _page_has_heading(p, "Business areas")
                     and all(re.search(re.escape(a), p.text) for a in _ACTIVITY_AREAS)), None)
    if overview is None:
        return None
    citations: dict[str, list[int]] = {}
    descriptions: dict[str, str] = {}
    for area in _ACTIVITY_AREAS:
        page = next((p for p in pages if p.page != overview.page and _page_has_heading(p, area)), None)
        if page is None:
            return None
        citations[area] = [overview.page, page.page]
        profit = _area_pre_tax_profit(page.text)
        descriptions[area] = ("A separate business area in the report, with its own income statement "
                              "and balance-sheet summary."
                              + (f" Pre-tax profit: {profit[1]} {profit[0]}m ({profit[2]})." if profit else ""))
    intro = ("The report presents the group's activities through three business areas: "
             + ", ".join(_ACTIVITY_AREAS) + ".")
    if re.search(r"banking,\s*mort-?\s*gage financing and leasing", _flat(overview.text), re.I):
        intro += (" The report states that the business areas reflect all activities in banking, "
                  "mortgage financing and leasing.")
    result = _make_overview_result(issuer, report_name, document_id, report_year, report_quarter,
                                   list(_ACTIVITY_AREAS), citations, descriptions, False, intro)
    result["business_areas"] = list(_ACTIVITY_AREAS)
    return result


def _bulleted_business_units_overview(
    issuer: str, report_name: str, document_id: str,
    report_year: int | None, report_quarter: int | None,
    pages: list[PageEvidence],
) -> dict | None:
    """Reports that state their structure as a bulleted list under 'Business segments'.

    Accepted only for a page headed "Business segments" that contains a sentence
    "organised in <n> reporting ... units/segments" followed by at least two
    bullets. Each unit's description is the report's own clause after its name
    (", which serves ..."). Anything else returns None.
    """
    for page in pages:
        lines = [l.strip() for l in page.text.splitlines() if l.strip()]
        if not any(re.match(r"^(?:[A-Z]\d+\.\s*)?Business segments\b", l, re.I) for l in lines[:8]):
            continue
        intro_at = next((i for i, l in enumerate(lines)
                         if re.search(r"organised in \w+ reporting", l, re.I)), None)
        if intro_at is None:
            continue
        intro_match = re.search(r"organised in (\w+) reporting ([a-z ]+?)\s*:", lines[intro_at], re.I)
        units: list[list[str]] = []
        for line in lines[intro_at + 1:]:
            bullet = re.match(r"^[•·▪]\s*(.+)$", line)
            if bullet:
                units.append([bullet.group(1)])
            elif units and not units[-1][-1].endswith("."):
                units[-1].append(line)       # a wrapped bullet continues on the next line
            else:
                break
        parsed: list[tuple[str, str | None]] = []
        for parts in units:
            text = _flat(" ".join(parts))
            name, sep, rest = text.partition(", which ")
            name = name.strip()
            if not name or len(name.split()) > 6:
                parsed = []
                break
            detail = (rest[:1].upper() + rest[1:]).strip() if sep else None
            parsed.append((name, detail))
        if len(parsed) < 2:
            continue
        names = [n for n, _ in parsed]
        descriptions = {n: d for n, d in parsed if d}
        group_functions = bool(re.search(r"include\s+Group\s+Functions", _flat(page.text), re.I))
        kind = f"{intro_match.group(1)} reporting {intro_match.group(2)}" if intro_match else "reporting units"
        intro = f"The report presents the group's activities through {kind}: " + ", ".join(names) + "."
        result = _make_overview_result(issuer, report_name, document_id, report_year, report_quarter,
                                       names, {n: [page.page] for n in names}, descriptions,
                                       group_functions, intro)
        result["business_areas"] = names
        return result
    return None


def _titled_business_unit_overview(
    issuer: str, report_name: str, document_id: str,
    report_year: int | None, report_quarter: int | None,
    pages: list[PageEvidence],
) -> dict | None:
    """Read a 'Business units/areas' page where each unit is a heading + prose.

    Fallback for reports whose unit names are not in the fixed SEGMENTS
    vocabulary. A unit is a short, unpunctuated heading line immediately
    followed by prose; at least two are required. Descriptions are copied from
    that prose, and the cited page is the page they were read from.
    """
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines()]
        nonempty = [(i, _heading_text(line)) for i, line in enumerate(lines) if line.strip()]
        section = next((i for i, text in nonempty[:12] if BUSINESS_SECTION.fullmatch(text)), None)
        if section is None:
            continue
        units: list[tuple[str, list[str]]] = []
        index = section + 1
        while index < len(lines):
            heading = _heading_text(lines[index])
            # A heading wrapped over two lines ends its first line with '&'.
            if heading.endswith("&") and index + 1 < len(lines):
                heading = f"{heading} {_heading_text(lines[index + 1])}"
                index += 1
            is_heading = (
                heading and len(heading) <= 40 and len(heading.split()) <= 6
                and heading[0].isupper() and not re.search(r"[.,;:\d]", heading)
            )
            following = next((l for l in lines[index + 1:index + 3] if l.strip()), "")
            if is_heading and len(following) >= 30:
                units.append((heading, []))
            elif units and heading:
                units[-1][1].append(lines[index])
            index += 1
        described: dict[str, str] = {}
        for name, prose in units:
            text = _flat(" ".join(prose))
            if re.search(r"[A-Za-z]{4,}", text) and name not in described:
                described[name] = " ".join(re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)[:2]).strip()
        if len(described) < 2:
            continue
        names = list(described)
        return _make_overview_result(
            issuer, report_name, document_id, report_year, report_quarter,
            names, {name: [page.page] for name in names}, described, False,
            f"The selected report describes {issuer}'s business through these units: " + ", ".join(names) + ".",
        )
    return None


def build_company_overview(issuer: str, report_name: str, document_id: str,
                           report_year: int | None, report_quarter: int | None,
                           pages: list[PageEvidence]) -> dict:
    """Compose a concise overview strictly from one selected report's page text."""
    # These quarterly report layouts use different, but explicit, segment
    # structures. Parse their own section/table headings before the Nordea-style
    # BUSINESS AREAS fallback. Each adapter still receives only this PDF's pages.
    issuer_key = re.sub(r"\s+", " ", issuer or "").strip().casefold()
    if issuer_key == "seb":
        adapted = _seb_report_overview(issuer, report_name, document_id, report_year, report_quarter, pages)
        if adapted is not None:
            return adapted
    if issuer_key == "handelsbanken":
        adapted = _handelsbanken_report_overview(issuer, report_name, document_id, report_year, report_quarter, pages)
        if adapted is not None:
            return adapted
    # Some report families define areas as Banking/Mortgage/Leasing activities
    # rather than Nordea-style business divisions. Use the explicit source page
    # only when its heading and all three explanatory sections are present.
    adapted = _complementary_business_areas_overview(
        issuer, report_name, document_id, report_year, report_quarter, pages
    )
    if adapted is not None:
        return adapted
    adapted = _activity_areas_from_division_pages(
        issuer, report_name, document_id, report_year, report_quarter, pages
    )
    if adapted is not None:
        return adapted
    adapted = _bulleted_business_units_overview(
        issuer, report_name, document_id, report_year, report_quarter, pages
    )
    if adapted is not None:
        return adapted
    edition = (f"Q{report_quarter} {report_year}" if report_quarter and report_year
               else str(report_year) if report_year else "selected indexed edition")
    segments, segment_citations, business_pages = _segment_pages(pages)
    # Group Functions is a central reporting category, not a customer-facing
    # business segment. List it separately if the report identifies it.
    group_functions = "Group Functions" in segments
    customer_segments = [s for s in segments if s != "Group Functions"]
    if len(customer_segments) < 2:
        # One vocabulary hit is not a reliable overview; a page of headed
        # business units can describe the units the fixed vocabulary lacks.
        adapted = _titled_business_unit_overview(
            issuer, report_name, document_id, report_year, report_quarter, pages
        )
        if adapted is not None:
            return adapted
    if not customer_segments:
        return {
            "kind": "overview", "answer": (
                f"I found {report_name}, but its indexed pages do not contain enough "
                "clearly identified business-segment information to produce a reliable overview. "
                "Try a specific section or financial metric."
            ), "supported": False, "needs_clarification": False, "sources": [],
            "selected_document_id": document_id, "overview_period": edition,
            "retrieval_note": "No segment-labelled business-area passages were found in the selected document.",
        }

    all_sources: dict[int, dict] = {}
    def add_source(page_no: int):
        all_sources[page_no] = {"document": report_name, "report": report_name,
                                "document_id": document_id, "page": page_no}

    for label in customer_segments:
        for page_no in segment_citations.get(label, []):
            add_source(page_no)
    if group_functions:
        for page_no in segment_citations.get("Group Functions", []):
            add_source(page_no)
    answer_parts = [
        f"**Overview based on {report_name}**",
        f"The selected report presents {issuer}'s activities through these business areas: "
        + ", ".join(customer_segments) + "."
        + (" It reports Group Functions separately from these customer-facing areas." if group_functions else ""),
    ]

    # Use exact segment-specific table rows only when the label, row and currency
    # unit occur together in the same selected-document page text.
    detail_lines = []
    for segment in customer_segments:
        segment_pages = [p for p in business_pages if re.search(re.escape(segment), p.text, re.I)]
        facts, metric_pages = _metric_highlights(
            segment, segment_pages, report_year, report_quarter
        )
        if segment == "Personal Banking":
            activity = "The report tracks mortgage and other lending, customer deposits, and investment-product assets."
        elif segment == "Asset & Wealth Management":
            activity = "The report separately presents investment-product assets and Private Banking activity."
        elif segment in ("Business Banking", "Commercial Banking"):
            activity = "The report presents business lending, deposits, and segment income and profitability."
        elif segment in ("Large Corporates & Institutions", "Corporate & Investment Banking", "Institutional Banking"):
            activity = "The report presents corporate/institutional lending, deposits, and segment income and profitability."
        elif segment in ("Retail Banking", "Consumer Banking", "Personal Banking"):
            activity = "The report presents customer lending, deposits, and related banking income."
        else:
            activity = f"The report identifies {segment} as a separate business area."
        if facts:
            activity += " Reported volume highlights: " + "; ".join(facts) + "."
        answer_parts.append(f"- **{segment}:** {activity}")
        for page_no in metric_pages:
            add_source(page_no)
        # Include the best detail page for this segment even if the only direct
        # support is the segment's own business-area heading and tables.
        for page in segment_pages:
            if VOLUME_CONTEXT.search(page.text) and page.page not in all_sources:
                add_source(page.page)
                break

    # Reported geography is described cautiously as the countries appearing in
    # country-level business-area tables, not as an exhaustive footprint claim.
    geography_pages = []
    geographic_terms: set[str] = set()
    for page in business_pages:
        text = page.text
        found = [country for country in COUNTRIES
                 if re.search(r"(?<!\w)" + re.escape(country) + r"(?!\w)", text, re.I)]
        if found:
            geographic_terms.update(found)
            geography_pages.append(page.page)
    if geographic_terms:
        ordered_countries = [c for c in COUNTRIES if c in geographic_terms]
        answer_parts.append(
            "**Geographic reporting:** The selected business-area tables include country-level "
            "breakdowns for " + ", ".join(ordered_countries) + "."
        )
        for page_no in geography_pages[:4]:
            add_source(page_no)

    answer_parts.append(
        "This is a report-based view of how the selected edition presents the business; "
        "it is not an exhaustive product catalogue or an independently verified current corporate profile."
    )
    sources = [all_sources[p] for p in sorted(all_sources)]
    return {
        "kind": "overview", "answer": "\n\n".join(answer_parts),
        "supported": True, "needs_clarification": False, "sources": sources,
        "selected_document_id": document_id,
        "overview_period": edition,
        "selected_report": report_name,
        "evidence_pages": sorted(all_sources),
        "business_segments": customer_segments,
        "group_functions_reported": group_functions,
        "geographies_in_segment_tables": [c for c in COUNTRIES if c in geographic_terms],
        "segment_page_map": {label: list(segment_citations.get(label, []))
                             for label in customer_segments},
    }


def build_indexed_company_overview(document) -> dict:
    """Load selected-document Chroma chunks and build a deterministic overview."""
    pages = load_indexed_pages(document.document_id)
    return build_company_overview(
        issuer=document.issuer,
        report_name=document.display_name,
        document_id=document.document_id,
        report_year=document.report_year,
        report_quarter=document.report_quarter,
        pages=pages,
    )
