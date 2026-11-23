"""Chat-first financial assistant: report RAG + opt-in market tool routing.

Only asks the market provider when the user explicitly requests market prices or
performance. Exchange selection is always based on verified search candidates.
No financial values are embedded in this module. Existing /ask_v2 is unchanged.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.market_data import MarketError, require_exchange, require_symbol
from app import market_routes as legacy
from app.financial_vocabulary import metric_matches

router = APIRouter()


class Listing(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    exchange: str = Field(min_length=1, max_length=50)
    # Entity key prevents an old Apple selection being applied to a later MSFT
    # question, and lets the chat remember choices for this Streamlit session.
    entity_key: str | None = Field(default=None, max_length=80)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # Prior user question and assistant clarification prompt for short slot replies
    # such as "commission income" or "Nordea".
    context_question: str | None = Field(default=None, max_length=4000)
    context_prompt: str | None = Field(default=None, max_length=2000)
    # Recent conversation history is accepted for clients that send chat turns.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    selected_listings: list[Listing] = Field(default_factory=list, max_length=2)
    remembered_listings: dict[str, Listing] = Field(default_factory=dict, max_length=20)
    document_selection: legacy.DocumentSelectionRequest = Field(default_factory=legacy.DocumentSelectionRequest)
    history_bars: int = Field(default=90, ge=2, le=250)


MARKET_TERMS = re.compile(
    r"\b(?:stocks?|stock[- ]prices?|share prices?|share quotes?|stock quotes?|"
    r"share[- ]price|share[- ]performance|stock performance|trading prices?|"
    r"share history|stock history|market quotation|market prices?|"
    r"historical share prices?|historical stock prices?|ticker|"
    r"shares? (?:trading|chart)|stock chart)\b", re.I)
HISTORY_TERMS = re.compile(
    r"\b(?:history|historical|chart|graph|performance|trend|past (?:\d+ )?(?:calendar )?(?:days?|weeks?|months?|years?)|"
    r"last (?:\d+ )?(?:calendar )?(?:days?|weeks?|months?|years?)|over (?:\d+ )?(?:calendar )?(?:days?|weeks?|months?|years?)|"
    r"six months|three months|one year)\b", re.I)
COMPARE_TERMS = re.compile(r"\b(?:compare|comparison|versus|vs\.?|against)\b", re.I)
REPORT_TERMS = re.compile(
    r"\b(?:CET1|capital adequacy|capital ratio|cost[- ]to[- ]income|"
    r"net interest income|net fee(?: and commission)? income|operating profit|"
    r"net profit|\bprofit\b|earnings per share|EPS|revenue|income statement|"
    r"balance sheet|total assets|return on equity|liquidity coverage|"
    r"financial report|interim report|factbook)\b", re.I)
TICKER_TOKEN = re.compile(r"\b[A-Z][A-Z0-9.]{0,9}\b")
EXCLUDE_TOKENS = {"I", "A", "THE", "AND", "OR", "MY", "OF", "IN", "TO", "IS", "Q1", "Q2", "Q3", "Q4", "PDF", "USD", "EUR", "SEK", "CET1", "EPS", "ROE", "LCR", "LLM", "AI", "API", "SEK", "YTD"}
TICKER_MARKET_ACTION = re.compile(
    r"\b(?:latest|current|last available|closing|historical|history|price|quote|performance|chart|change|previous close)\b",
    re.I,
)


def _explicit_ticker_market_request(question: str) -> bool:
    """Recognize ticker shorthand while deduplicating repeated issuer tokens."""
    tickers = list(dict.fromkeys(
        token.upper() for token in TICKER_TOKEN.findall(question)
        if token.upper() not in EXCLUDE_TOKENS and len(token) >= 2
    ))
    if len(tickers) >= 2 and COMPARE_TERMS.search(question):
        return True
    return bool(tickers and TICKER_MARKET_ACTION.search(question))
COMPANY_BEFORE_MARKET = re.compile(
    r"\b([A-Za-z][A-Za-z0-9 .&'-]{0,48}?)\s*(?:['’]s)?\s+(?:stock|shares?|share[- ]price)\b", re.I)
COMPANY_AFTER_MARKET = re.compile(
    r"\b(?:stock|share)\s+(?:price|quote|history|performance|chart)\s+(?:of|for)\s+"
    r"([A-Za-z][A-Za-z0-9 .&'-]{0,48}?)(?=\s+(?:over|during|for|in|from|and|vs|versus)|[?!.]|$)", re.I)
LEADING_NOISE = re.compile(
    r"^(?:(?:can you|could you|please|i want to|i'd like to|what's|what is|what was|"
    r"show(?: me)?|give(?: me)?|tell me|the|latest|current|price of|how has|how did|"
    r"check|view|see|look up)\s+)+", re.I)
# These words are often captured between the company possessive and "stock price".
# Remove the suffix, not arbitrary words in the middle of a company name.
TRAILING_NOISE = re.compile(
    r"\s+\b(?:latest|current|available|recent|historical|closing|quoted|market)\b.*$", re.I)

# Search hints only: the adapter still asks Twelve Data for exact listings and
# only displays provider-returned symbol/exchange/currency candidates. No
# exchange, price, or preferred listing is hardcoded here.
COMPANY_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "nordea": ("Nordea Bank Abp", "NDA"),
    "nordea bank": ("Nordea Bank Abp", "NDA"),
    "nordea bank abp": ("NDA",),
}


ABOUT_TERMS = re.compile(
    r"\b(?:who\s+(?:made|created|built|developed)\s+(?:you|this assistant)|"
    r"who is your (?:creator|developer)|what can you do|what are your capabilities|"
    r"what is your purpose|what are you)\b", re.I)

CREATOR_ANSWER = """I'm FinanceAI, a financial-intelligence portfolio assistant designed and developed by **Serhat Turan**.

Serhat holds a B.S. in Mathematics and is building his career in data science and AI engineering. He created this project to demonstrate practical work in Python, retrieval-augmented generation (RAG), financial-document processing, evidence verification, market-data API integration, and software testing. His portfolio also includes a finish in approximately the **top 10%** of the Kaggle RSNA 2026 Knee Abnormality Detection competition.

This is an educational portfolio project, not a commercial banking product."""

CAPABILITIES_ANSWER = """I'm a financial-document and market-data assistant. I can:

- Answer questions from the financial reports currently indexed in this project, with document and PDF-page citations when evidence is available.
- Find supported stock listings and retrieve the latest available quotes or historical price series through Twelve Data.
- Compare price-only stock performance over shared trading dates.
- Combine market quotations with financial-report figures while keeping their sources and timestamps separate.

**Limitations:** Market coverage depends on the Twelve Data plan configured in this installation. Basic uses the default US listed-equity feed here; primary Nordic exchange feeds require Pro; OTC securities require a separate data licence even on Pro. Market data may be delayed and is not guaranteed real-time. Report answers are limited to the documents indexed in this installation. I do not execute trades, provide personalized investment recommendations, or guarantee forecasts. This is an educational demonstration, not investment advice."""


OVERVIEW_TERMS = re.compile(
    r"\b(?:tell me about|give me (?:a|an) overview of|overview of|"
    r"describe|summari[sz]e|what does .{1,70}? do|what kind of (?:bank|company|business) is)\b|"
    r"\b(?:what are|list|name)\b.{0,90}\b(?:five|5)?\s*(?:business\s+)?segments?\b|"
    r"\bwhich\s+(?:business\s+)?segments?\b.{0,80}\b(?:has|have|does|do)\b", re.I)
SMALLTALK_TERMS = re.compile(
    r"^(?:\s*(?:yes|yeah|yep|sure|okay|ok|thanks|thank you|hello|hi|hey|"
    r"how are you|how's it going|good morning|good afternoon|good evening)\s*[.!?]*)$", re.I)

FINANCIAL_DOMAIN_TERMS = re.compile(
    r"\b(?:bank|banks|banking|financial|finance|interest|loan|loans|lending|deposit|"
    r"deposits|assets|liabilities|equity|dividend|capital|credit|funding|income|"
    r"earnings|profit|profits|segment|business area|balance sheet|cash flow|"
    r"regulatory|portfolio|mortgage|savings|insurance|liquidity|risk|revenue)\b", re.I)
PERIOD_ONLY_FOLLOWUP = re.compile(
    r"^\s*(?:(?:please\s+)?(?:use|choose|select|go with|take)\s+)?"
    r"(?:(?:and|what about|how about)\s+(?:in\s+)?)?"
    r"(?:the\s+)?Q([1-4])(?:\s*(20\d{2}|\d{2}))?(?:\s+report)?[.!?]*\s*$", re.I)
COMPARISON_PERIOD_FOLLOWUP = re.compile(
    r"^\s*(?:how\s+(?:does|did)\s+(?:that|it|this)\s+)?"
    r"compare\s+(?:with|to)\s+(?:the\s+)?Q([1-4])(?:\s*(20\d{2}|\d{2}))?"
    r"[.!?]*\s*$", re.I,
)

PERSONAL_ACCOUNT_REQUEST = re.compile(
    r"\b(?:my|mine|personal|own)\b.{0,100}\b(?:current|checking|savings|bank)\s+account\b"
    r"|\b(?:my|mine|personal|own)\b.{0,100}\b(?:account\s+balance|bank\s+balance|"
    r"account\s+transactions?|bank\s+statement|account\s+details)\b"
    r"|\b(?:my|personal|own)\s+(?:bank\s+)?(?:account|balance|transactions?|statement)\b", re.I)

METRIC_CLARIFICATION_OPTIONS = {
    "commission income": "net fee and commission income",
    "net commission income": "net fee and commission income",
    "net fee and commission income": "net fee and commission income",
    "net interest income": "net interest income",
    "total operating income": "total operating income",
    "operating income": "total operating income",
    "net profit": "net profit",
    "operating profit": "operating profit",
    "profit before credit losses": "profit before credit losses",
}

METRIC_FOLLOWUP_RE = re.compile(
    r"^\s*(?:what about|how about|and)\s+(?P<metric>.+?)\s*[.!?]*\s*$", re.I
)
MARKET_PREVIOUS_CLOSE_FOLLOWUP = re.compile(
    r"\b(?:previous|prior)\s+close\b|"
    r"\bhow much\s+(?:has|did)\s+(?:it|the stock|the share)\s+change(?:d)?\b|"
    r"\bprice change and percentage change\b", re.I
)
FOLLOWUP_METRIC_LABELS = {
    "net_profit": "net profit",
    "operating_profit": "operating profit",
    "net_interest_income": "net interest income",
    "net_fee_income": "net fee and commission income",
    "total_operating_income": "total operating income",
    "cet1_ratio": "CET1 capital ratio",
    "return_on_equity": "return on equity",
    "profit_before_credit_losses_fees": "profit before credit losses",
}
SHORT_SLOT_REPLY = re.compile(
    r"^(?:commission income|net commission income|net fee and commission income|"
    r"net interest income|total operating income|operating income|"
    r"net profit|operating profit|profit before credit losses|"
    r"Nordea|SEB|Handelsbanken|Q[1-4](?:\s*20\d{2})?)\s*[.!?]*$",
    re.I,
)

def _merge_clarification_reply(followup: str, context_question: str, context_prompt: str) -> str | None:
    """Resolve one clarification slot without discarding the rest of the question."""
    follow = (followup or "").strip().strip(".!?")
    context = (context_question or "").strip().rstrip(".!?")
    prompt = (context_prompt or "").casefold()
    if not follow or not context or not prompt:
        return None
    if "which financial measure" in prompt or "which metric" in prompt:
        metric = METRIC_CLARIFICATION_OPTIONS.get(follow.casefold())
        if not metric:
            return None
        replaced, count = re.subn(
            r"\b(?:revenue|turnover|sales|income|earnings|profit)\b", metric, context, count=1, flags=re.I
        )
        return replaced if count else f"{context} for {metric}"
    if "which bank" in prompt or "which issuer" in prompt or "which company" in prompt:
        issuers = _known_issuer_mentions(follow)
        if len(issuers) == 1 and not _known_issuer_mentions(context):
            return f"{context} for {issuers[0]}"
    return None


def _known_issuer_mentions(question: str) -> list[str]:
    """Return explicit indexed issuer names only; never infer a bank by similarity."""
    try:
        from app.document_registry import list_documents
        issuers = sorted(
            {d.issuer for d in list_documents() if d.issuer and d.issuer.casefold() != "unknown"},
            key=len, reverse=True,
        )
    except Exception:
        issuers = ["Handelsbanken", "Nordea", "SEB"]
    return [name for name in issuers
            if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", question, re.I)]


def _is_financial_or_indexed_bank_question(question: str) -> bool:
    """Keep the report resolver away from unrelated conversation and gibberish."""
    return bool(
        REPORT_TERMS.search(question)
        or FINANCIAL_DOMAIN_TERMS.search(question)
        or _known_issuer_mentions(question)
        or MARKET_TERMS.search(question)
        or _explicit_ticker_market_request(question)
    )


def _merge_target_period_year_reply(
    followup: str, context_question: str, context_prompt: str
) -> str | None:
    """Attach a year-only quarter selection to the prior target-period question."""
    reply = PERIOD_ONLY_FOLLOWUP.fullmatch((followup or "").strip())
    context = (context_question or "").strip()
    prompt = (context_prompt or "").casefold()
    if not reply or not context or not reply.group(2):
        return None
    if not ("not the year" in prompt or "specify the year" in prompt or "include the year" in prompt):
        return None
    target = re.search(
        r"(?<!\d{4}\s)\bQ([1-4])\b(?!\s*(?:of\s+)?[-/]?\s*(?:20\d{2}|\d{2}))",
        context, re.I,
    )
    if not target or int(target.group(1)) != int(reply.group(1)):
        return None
    year = reply.group(2)
    if len(year) == 2:
        year = "20" + year
    return context[:target.end()] + f" {year}" + context[target.end():]


def _merge_comparison_period_followup(
    followup: str, context_question: str
) -> tuple[str | None, str | None]:
    """Merge a contextual 'compare with Qx' reply only with a clear prior query."""
    match = COMPARISON_PERIOD_FOLLOWUP.fullmatch(followup or "")
    if not match:
        return None, None
    context = (context_question or "").strip()
    if not context:
        return None, (
            "I don't have the previous financial question attached to that comparison. "
            "Please repeat the complete question with the bank, metric and both quarters, "
            "for example: 'Compare Nordea's operating profit in Q2 and Q3 2026.'"
        )
    issuers = _known_issuer_mentions(context)
    if len(issuers) != 1:
        return None, (
            "I couldn't identify one specific bank from the previous question. "
            "Please repeat the comparison with the bank name, financial metric and both quarters."
        )
    try:
        from app.question_parser import parse_question
        prior_intent = parse_question(context)
    except Exception:
        prior_intent = None
    if prior_intent is None or not prior_intent.metric:
        return None, (
            f"I can compare financial figures for {issuers[0]}, but the previous question "
            "didn't identify a specific financial measure. Which metric should I compare "
            "between the quarters, such as operating profit or net profit?"
        )
    prior_periods = tuple(prior_intent.periods or ())
    if len(prior_periods) != 1 or not re.fullmatch(r"Q[1-4]\d{2}", prior_periods[0]):
        return None, (
            f"I couldn't identify exactly one previous quarter for {issuers[0]}. "
            "Please repeat the comparison with both quarters and the year."
        )
    prior_period = prior_periods[0]
    target_quarter = int(match.group(1))
    target_year_raw = match.group(2)
    if target_year_raw:
        target_year = int(target_year_raw)
        if target_year < 100:
            target_year += 2000
    else:
        target_year = 2000 + int(prior_period[2:])
    target_period = f"Q{target_quarter}{str(target_year)[-2:]}"
    if target_period == prior_period:
        return None, "Both parts of that comparison refer to the same quarter. Please specify a different reporting quarter."
    original_question = context.strip().rstrip(" .?!")
    if not original_question:
        return None, "Please repeat the complete financial comparison question."
    return f"Compare {original_question} with Q{target_quarter} {target_year}.", None


def _merge_period_followup(followup: str, context_question: str) -> tuple[str | None, str | None]:
    """Attach a compact Qx reply to its prior financial question, fail-closed."""
    match = PERIOD_ONLY_FOLLOWUP.fullmatch(followup or "")
    if not match:
        return context_question, None
    context = (context_question or "").strip()
    if not context:
        return None, (
            "I don't have the original financial question attached to that reply. "
            "Please repeat the complete question, for example: "
            "'What was Nordea's net profit in Q3 2026?'"
        )

    try:
        from app.document_registry import list_documents
        reports = list_documents()
    except Exception:
        reports = []
    issuer_mentions = _known_issuer_mentions(context)
    if len(issuer_mentions) != 1:
        return None, (
            "I couldn't connect that quarter selection to one specific bank. "
            "Please repeat the complete financial question with the bank name and quarter."
        )
    issuer = issuer_mentions[0]
    quarter = int(match.group(1))
    requested_year = match.group(2)
    if requested_year:
        year = int(requested_year)
        if year < 100:
            year += 2000
    else:
        existing = re.search(r"\b(?:Q[1-4]\s*)?(20\d{2})\b", context, re.I)
        year = int(existing.group(1)) if existing else None
        if year is None:
            possible_years = [d.report_year for d in reports
                              if d.issuer.casefold() == issuer.casefold()
                              and d.report_quarter == quarter and d.report_year]
            year = max(possible_years) if possible_years else None
    if year is None:
        return None, (
            f"I couldn't find an indexed Q{quarter} report year for {issuer}. "
            "Please include the year, for example 'Q3 2026'."
        )
    if reports and not any(d.issuer.casefold() == issuer.casefold()
                           and d.report_year == year and d.report_quarter == quarter
                           for d in reports):
        return None, f"I couldn't find an indexed Q{quarter} {year} report for {issuer}."

    # Replace any earlier quarter token instead of accumulating conflicting
    # periods (Q2 then Q3 then Q4) in the normalized question.
    normalized = re.sub(
        r"\s+(?:(?:in|for|during)\s+)?Q[1-4]\s*20\d{2}\b|"
        r"\s+20\d{2}\s*Q[1-4]\b",
        " ", context, flags=re.I,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip(" .?!")
    return f"{normalized} in Q{quarter} {year}", None


def _merge_metric_followup(followup: str, context_question: str) -> str | None:
    """Change only the metric in a short follow-up, preserving issuer and period.

    This intentionally accepts a small set of complete metric names, not free
    text. A longer follow-up containing a business-area qualifier is handled by
    the issuer/scope path instead of silently dropping that qualifier.
    """
    match = METRIC_FOLLOWUP_RE.fullmatch(followup or "")
    context = (context_question or "").strip()
    if not match or not context:
        return None
    requested = match.group("metric").strip().casefold()
    new_key = next((key for key, label in FOLLOWUP_METRIC_LABELS.items()
                    if requested == label.casefold()), None)
    if new_key is None:
        return None
    try:
        from app.question_parser import parse_question
        old_key = parse_question(context).metric
    except Exception:
        old_key = None
    if not old_key or old_key == new_key:
        return None
    old_hits = [hit for hit in metric_matches(context) if hit[0].key == old_key]
    if not old_hits:
        return None
    _concept, _matched_text, start, end = old_hits[0]
    return context[:start] + FOLLOWUP_METRIC_LABELS[new_key] + context[end:]


def _merge_market_previous_close_followup(followup: str, context_question: str) -> str | None:
    """Resolve a previous-close follow-up only when one prior listing is clear."""
    if not MARKET_PREVIOUS_CLOSE_FOLLOWUP.search(followup or ""):
        return None
    context = (context_question or "").strip()
    if not context:
        return None
    prior_intent = _intent(context)
    if prior_intent not in {"quote", "history", "compare", "combined"}:
        return None
    entities = _entities(context, prior_intent)
    if len(entities) != 1:
        return None
    return f"{entities[0]} price change from previous close"


INLINE_QUARTER_FOLLOWUP = re.compile(
    r"^(?P<base>.+?)\?\s*(?:and|what about)\s+in\s+Q(?P<quarter>[1-4])"
    r"(?:\s*(?P<year>20\d{2}|\d{2}))?\s*\??\s*$", re.I
)


def _expand_inline_quarter_followup(question: str) -> str | None:
    """Turn '... in Q3 2026? And in Q2?' into an explicit two-period request."""
    match = INLINE_QUARTER_FOLLOWUP.fullmatch((question or "").strip())
    if not match:
        return None
    base = match.group("base").strip()
    old_period = re.search(r"\bQ([1-4])\s*(?:of\s+)?(20\d{2}|\d{2})\b", base, re.I)
    if not old_period:
        return None
    old_q = int(old_period.group(1))
    new_q = int(match.group("quarter"))
    year_text = match.group("year") or old_period.group(2)
    year = int(year_text)
    if year < 100:
        year += 2000
    if old_q == new_q:
        return None
    without_old_period = re.sub(
        r"\s+(?:(?:in|for|during)\s+)?Q[1-4]\s*(?:of\s+)?(?:20\d{2}|\d{2})\b", "", base,
        count=1, flags=re.I,
    ).strip(" .?!")
    without_old_period = re.sub(r"^(?:what was|what is|tell me|show me)\s+", "", without_old_period, flags=re.I)
    return (f"Compare {without_old_period} in Q{new_q} {year} and Q{old_q} {year}, "
            "showing both reported amounts side by side.")


def _intent(question: str) -> Literal["about", "overview", "smalltalk", "document", "quote", "history", "compare", "combined"]:
    if ABOUT_TERMS.search(question):
        return "about"
    market = bool(MARKET_TERMS.search(question) or _explicit_ticker_market_request(question))
    if not market and OVERVIEW_TERMS.search(question) and not REPORT_TERMS.search(question):
        return "overview"
    if not market and SMALLTALK_TERMS.fullmatch(question.strip()):
        return "smalltalk"
    if not market:
        return "document"
    if COMPARE_TERMS.search(question) and not REPORT_TERMS.search(question):
        return "compare"
    if REPORT_TERMS.search(question):
        return "combined"
    if HISTORY_TERMS.search(question):
        return "history"
    return "quote"


def _clean_entity(raw: str) -> str:
    """Extract only the issuer/ticker, excluding conversational and quote modifiers."""
    cleaned = LEADING_NOISE.sub("", raw.strip())
    cleaned = TRAILING_NOISE.sub("", cleaned)
    # Also strip a small set of trailing request qualifiers when they are captured
    # before the market phrase (e.g. "Nordea's latest available stock price").
    tokens = cleaned.split()
    suffixes = {"latest", "current", "available", "recent", "historical", "closing", "quoted", "market"}
    while tokens and tokens[-1].strip(" ?.,:;!").casefold() in suffixes:
        tokens.pop()
    cleaned = " ".join(tokens).strip()
    cleaned = re.sub(r"(?:['’]s)$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^(?:of|for|the)\s+", "", cleaned, flags=re.I)
    return cleaned.strip(" ?.,:-")[:75]


def _search_terms_for(term: str) -> list[str]:
    """Return a bounded sequence of company-name aliases; all matches stay provider-verified."""
    canonical = _clean_entity(term).casefold()
    terms = [_clean_entity(term)]
    for alias in COMPANY_SEARCH_ALIASES.get(canonical, ()):
        if alias.casefold() not in {x.casefold() for x in terms}:
            terms.append(alias)
    return [item for item in terms if item]


def _memory_key(raw: str) -> str:
    """Canonical, bounded key for a user-confirmed listing in chat session memory."""
    value = _clean_entity(raw).casefold()
    aliases = {
        "nordea bank abp": "nordea", "nordea bank": "nordea",
        "nda": "nordea", "nbnkf": "nordea", "nrdby": "nordea",
    }
    if value in aliases:
        return aliases[value]
    tokens = re.findall(r"[a-z0-9]+", value)
    legal_suffixes = {"inc", "incorporated", "corp", "corporation", "plc", "abp", "ltd", "limited", "sa", "nv", "se"}
    while tokens and tokens[-1] in legal_suffixes:
        tokens.pop()
    return " ".join(tokens)[:80]


def _prior_market_entities(body: ChatRequest) -> list[str]:
    """Find entities in the latest prior market question for explicit pronoun follow-ups."""
    for turn in reversed(body.history):
        if turn.role != "user":
            continue
        prior_intent = _intent(turn.content)
        if prior_intent in {"quote", "history", "compare", "combined"}:
            entities = _entities(turn.content, prior_intent)
            if entities:
                return entities
    return []


def _entities(question: str, intent: str) -> list[str]:
    # Uppercase tickers are unambiguous *search terms*, but not exchange choices.
    tickers = [x for x in TICKER_TOKEN.findall(question) if x.upper() not in EXCLUDE_TOKENS]
    tickers = list(dict.fromkeys(tickers))
    if intent == "compare":
        if len(tickers) >= 2:
            return tickers[:2]
        # Named companies: "Compare Apple and Microsoft stock performance".
        match = re.search(r"\bcompare\s+(.+?)\s+(?:stocks?|shares?|share[- ]prices?|stock[- ]prices?|performances?|over\b)", question, re.I)
        if match:
            names = re.split(r"\s+(?:and|vs\.?|versus|against)\s+", match.group(1), maxsplit=1, flags=re.I)
            if len(names) == 2:
                return [_clean_entity(name) for name in names]
        return tickers
    # Prefer explicit ticker if written, e.g. AAPL's stock price.
    if tickers:
        return tickers[:1]
    for pattern in (COMPANY_AFTER_MARKET, COMPANY_BEFORE_MARKET):
        matches = pattern.findall(question)
        if matches:
            ent = _clean_entity(matches[-1])
            if ent and len(ent) <= 75:
                return [ent]
    return []


def _bars(question: str, fallback: int) -> int:
    match = re.search(
        r"\b(?:past|last|over)\s+(\d{1,3})\s+(?:calendar\s+)?(days?|weeks?|months?)\b",
        question, re.I,
    )
    if match:
        n = int(match.group(1))
        unit = match.group(2).lower()
        return min(250, max(2, round(n * (1 if unit.startswith("day") else 5 if unit.startswith("week") else 21))))
    if re.search(r"\b(?:six|6) months\b", question, re.I):
        return 126
    if re.search(r"\b(?:three|3) months\b", question, re.I):
        return 63
    if re.search(r"\b(?:one|1) year\b", question, re.I):
        return 250
    return fallback


def _calendar_days(question: str) -> int | None:
    """Read an explicitly requested rolling calendar window, not trading bars."""
    match = re.search(
        r"\b(?:last|past)\s+(\d{1,3})\s+(?:calendar\s+)?(days?|weeks?|months?|years?)\b",
        question, re.I,
    )
    if match:
        amount = int(match.group(1))
        unit = match.group(2).casefold()
        days = amount if unit.startswith("day") else amount * 7 if unit.startswith("week") else (
            amount * 30 if unit.startswith("month") else amount * 365
        )
        return min(3650, max(1, days))
    if re.search(r"\b(?:last|past)\s+(?:one\s+)?(?:calendar\s+)?month\b", question, re.I):
        return 30
    if re.search(r"\b(?:last|past)\s+(?:three|3)\s+(?:calendar\s+)?months\b", question, re.I):
        return 90
    if re.search(r"\b(?:last|past)\s+(?:six|6)\s+(?:calendar\s+)?months\b", question, re.I):
        return 180
    if re.search(r"\b(?:last|past)\s+(?:one|1)\s+(?:calendar\s+)?year\b", question, re.I):
        return 365
    return None


def _history_request_size(question: str, fallback: int) -> int:
    days = _calendar_days(question)
    if days is None:
        return _bars(question, fallback)
    # Fetch a buffer for weekends, exchange holidays and provider gaps; the
    # returned observations are then filtered by actual calendar dates.
    return min(250, max(60, int(days * 1.7) + 10))


def _filter_history_calendar_window(history: dict, calendar_days: int) -> dict:
    points = list(history.get("points") or [])
    if not points:
        raise MarketError("insufficient_shared_dates", 422)
    end = date.fromisoformat(points[-1]["date"][:10])
    start = end - timedelta(days=calendar_days)
    filtered = [point for point in points
                if start <= date.fromisoformat(point["date"][:10]) <= end]
    if len(filtered) < 2:
        raise MarketError("insufficient_shared_dates", 422)
    # If the provider returned too little history (for example, a newly listed
    # company), do not relabel a shorter interval as the full requested window.
    if date.fromisoformat(filtered[0]["date"][:10]) > start + timedelta(days=5):
        raise MarketError("insufficient_shared_dates", 422)
    return {
        **history,
        "points": filtered,
        "requested_calendar_days": calendar_days,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


def _compare_history_data(
    first: dict, second: dict, calendar_days: int | None = None,
) -> tuple[dict, dict, dict]:
    """Calculate price-only returns from exactly the requested shared dates."""
    left = {point["date"]: Decimal(point["close"]) for point in first.get("points") or []}
    right = {point["date"]: Decimal(point["close"]) for point in second.get("points") or []}
    shared = sorted(left.keys() & right.keys())
    if len(shared) < 2:
        raise MarketError("insufficient_shared_dates", 422)
    end_date = shared[-1]
    start_bound = (
        date.fromisoformat(end_date[:10]) - timedelta(days=calendar_days)
        if calendar_days is not None else None
    )
    if start_bound is not None:
        shared = [day for day in shared if date.fromisoformat(day[:10]) >= start_bound]
    if len(shared) < 2:
        raise MarketError("insufficient_shared_dates", 422)
    if (start_bound is not None
            and date.fromisoformat(shared[0][:10]) > start_bound + timedelta(days=5)):
        raise MarketError("insufficient_shared_dates", 422)
    start_date, end_date = shared[0], shared[-1]

    # Keep the graph and its displayed observation count within the same
    # filtered date window used for the reported returns.
    first_filtered = {
        **first,
        "points": [p for p in first["points"]
                   if (start_bound is None or date.fromisoformat(p["date"][:10]) >= start_bound)
                   and p["date"] <= end_date],
    }
    second_filtered = {
        **second,
        "points": [p for p in second["points"]
                   if (start_bound is None or date.fromisoformat(p["date"][:10]) >= start_bound)
                   and p["date"] <= end_date],
    }

    def result_row(series: dict, values: dict[str, Decimal], symbol: str) -> dict:
        first_close, last_close = values[start_date], values[end_date]
        return {
            "symbol": symbol,
            "exchange": series["exchange"],
            "currency": series["currency"],
            "first_close": str(first_close),
            "last_close": str(last_close),
            "return_percent": str((last_close / first_close - Decimal(1)) * Decimal(100)),
        }

    comparison = {
        "provider": "Twelve Data",
        "start_date": start_date,
        "end_date": end_date,
        "shared_trading_dates": len(shared),
        "requested_calendar_days": calendar_days,
        "results": [
            result_row(first, left, first["symbol"]),
            result_row(second, right, second["symbol"]),
        ],
        "comparison_type": "unadjusted_price_return_percent_excludes_dividends",
        "warning": "Comparing percentage price returns, not currency-denominated prices or total returns; markets may have different trading calendars.",
        "sources": ["Twelve Data /time_series"],
    }
    return comparison, first_filtered, second_filtered


def _base(kind: str, answer: str, **extra) -> dict:
    return {"kind": kind, "answer": answer, "supported": False,
            "needs_clarification": False, "sources": [], **extra}


def _friendly_error(exc: MarketError) -> dict:
    from app.market_entitlements import configured_plan

    plan = configured_plan()
    if exc.code == "market_plan_restricted":
        if plan == "basic":
            message = (
                "This FinanceAI installation is configured for the Twelve Data Basic subscription "
                "(TWELVE_DATA_PLAN=basic; this is application configuration, not auto-detected from the API key). "
                "The default US listed-equity feed used here covers supported US equities, so the requested Nordic primary "
                "exchange is not available. Twelve Data marks Nasdaq Helsinki, Stockholm and Copenhagen as Pro. "
                "US-listed stocks such as AAPL and MSFT remain available. Financial-report questions still work."
            )
        else:
            message = (
                f"This FinanceAI installation is configured for Twelve Data {plan.title()}. "
                "The provider did not confirm access to this exchange for the current API key. "
                "Please verify the actual exchange entitlement in your Twelve Data account. "
                "Financial-report questions still work."
            )
    elif exc.code == "market_otc_license_required":
        message = (
            f"This FinanceAI installation is configured for Twelve Data {plan.title()}. OTC securities are not "
            "included in the default subscription feeds and require a separate OTC data licence, even on Pro. "
            "I won't display an unlicensed OTC quotation. US-listed equities available under the configured plan "
            "and financial-report questions remain available."
        )
    else:
        messages = {
            "market_provider_not_configured": "Market prices aren't configured on the API server yet. Financial-report questions still work.",
            "market_provider_rate_limited": "Twelve Data has reached the request limit for this API key. Wait before trying again; I won't invent a price.",
            "market_provider_authentication_failed": "Twelve Data rejected the API key. Check that TWELVE_DATA_API_KEY is valid in the FastAPI server terminal. Financial-report questions still work.",
            "market_provider_access_denied": (
                f"Twelve Data denied access to this instrument or exchange. This installation is configured for {plan.title()}. "
                "The Basic default equity feed used here is US-listed equities; other exchanges or data products may require Pro "
                "or a separate licence. Check the actual entitlements in your Twelve Data account. Financial-report questions still work."
            ),
            "market_provider_unavailable": "Twelve Data returned a temporary service error after a bounded retry. Please try again later; no unverified price was shown.",
            "market_symbol_unavailable": "The provider couldn't find market data for this listing. Please choose a different listing.",
            "provider_exchange_mismatch": "The provider returned a different exchange from the one selected, so I rejected the quotation.",
            "invalid_provider_data": "The provider returned incomplete or invalid market data. No price was displayed.",
            "invalid_exchange": "The selected exchange is invalid. Please choose an exact listing from the search results.",
            "insufficient_shared_dates": (
                "The provider did not return enough matching price observations to cover the full requested calendar window. "
                "I did not substitute a shorter period."
            ),
        }
        message = messages.get(exc.code, "Market data could not be verified for this request.")

    message = getattr(exc, "coverage_message", None) or message
    return _base("market_unavailable", message, error_code=exc.code)


def _lookup_term(term: str) -> list[dict]:
    """Search the extracted name, then known spelling/name/ticker aliases if empty.

    Candidate symbol, exchange, currency and company label always come from the
    active market provider. Alias entries are search strings only.
    """
    search_terms = _search_terms_for(term)
    if not search_terms:
        return []
    aggregated: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for search_term in search_terms:
        try:
            results = legacy.market_client.search(search_term).get("candidates", [])
        except MarketError as exc:
            # An unrecognised alias is not fatal; quota/auth/provider failures are.
            if exc.code == "market_symbol_unavailable" and search_term != search_terms[-1]:
                continue
            raise
        if re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", search_term):
            exact = [r for r in results if r.get("symbol", "").upper() == search_term]
            if exact:
                results = exact
        for row in results:
            pair = (row.get("symbol", "").upper(), row.get("exchange", "").casefold())
            if pair in seen or not all(pair):
                continue
            seen.add(pair)
            aggregated.append(row)
            if len(aggregated) >= 12:
                break
        # If this alias produced an entitled listing, no further search is
        # needed. If every hit is restricted (e.g. OTC first), continue to the
        # official ticker alias to discover primary exchange candidates too.
        has_usable_or_unclassified = any(
            not isinstance(row.get("entitlement"), dict)
            or bool(row["entitlement"].get("allowed"))
            for row in aggregated
        )
        if has_usable_or_unclassified or len(aggregated) >= 12:
            break
    return aggregated[:12]


def _selected_or_search(term: str, slot: int, selected: list[Listing],
                        remembered: dict[str, Listing] | None = None) -> tuple[Listing | None, list[dict]]:
    key = _memory_key(term)
    # A listing chosen in the current clarification takes precedence, but only
    # when its entity key matches this slot. Legacy clients without entity_key
    # retain the old slot-ordered behavior.
    for chosen in selected:
        if chosen.entity_key and _memory_key(chosen.entity_key) == key:
            verified_exchange = require_exchange(chosen.exchange)
            if not verified_exchange:
                raise MarketError("invalid_exchange", 422)
            return Listing(symbol=require_symbol(chosen.symbol), exchange=verified_exchange,
                           entity_key=key), []
    if len(selected) > slot and selected[slot].entity_key is None:
        chosen = selected[slot]
        verified_exchange = require_exchange(chosen.exchange)
        if not verified_exchange:
            raise MarketError("invalid_exchange", 422)
        return Listing(symbol=require_symbol(chosen.symbol), exchange=verified_exchange,
                       entity_key=key), []

    saved = (remembered or {}).get(key)
    if saved:
        verified_exchange = require_exchange(saved.exchange)
        if not verified_exchange:
            raise MarketError("invalid_exchange", 422)
        # Session memory can outlive a plan change. Revalidate the remembered
        # instrument before reusing it; a now-ineligible listing is discarded
        # and the provider is searched again for an eligible alternative.
        try:
            from app.market_entitlements import require_listing_entitlement
            require_listing_entitlement(require_symbol(saved.symbol), verified_exchange)
        except MarketError:
            saved = None
        if saved:
            return Listing(symbol=require_symbol(saved.symbol), exchange=verified_exchange,
                           entity_key=key), []

    choices = _lookup_term(term)
    if choices:
        entitlement = [row.get("entitlement") for row in choices]
        known = [x for x in entitlement if isinstance(x, dict)]
        if known and all(not x.get("allowed", False) for x in known):
            from app.market_entitlements import configured_plan, nordea_basic_coverage_message
            if key == "nordea" and configured_plan() == "basic":
                error = MarketError("market_plan_restricted", 403)
                error.coverage_message = nordea_basic_coverage_message()
                raise error
            first = known[0]
            error = MarketError(str(first.get("reason_code") or "market_plan_restricted"), 403)
            error.coverage_message = str(first.get("message") or "This listing is outside the configured market-data entitlement.")
            raise error
    elif key == "nordea":
        # The Basic symbol-search endpoint may hide Pro Nordic listings. If it
        # returns no candidate at all, explain the known entitlement boundary
        # rather than presenting an unhelpful generic 'not found' message.
        from app.market_entitlements import configured_plan, nordea_basic_coverage_message
        if configured_plan() == "basic":
            error = MarketError("market_plan_restricted", 403)
            error.coverage_message = nordea_basic_coverage_message()
            raise error
    if len(choices) == 1:
        item = choices[0]
        ent = item.get("entitlement") or {}
        if not ent or ent.get("allowed", False):
            return Listing(symbol=item["symbol"], exchange=item["exchange"], entity_key=key), []
    return None, choices


def _select_message(question: str, slot: int, choices: list[dict], selected: list[Listing],
                    document_selection: legacy.DocumentSelectionRequest | None = None,
                    *, include_report: bool = False, entity_key: str | None = None) -> dict:
    if not choices:
        result = _base("market_clarification", "I couldn't find a verified exchange listing. Try a different company name or ticker.",
                       needs_clarification=True, slot=slot, candidates=[], selected_listings=[x.model_dump() for x in selected], question=question,
                       entity_key=entity_key)
    else:
        restricted = [x for x in choices if isinstance(x.get("entitlement"), dict)
                      and not x["entitlement"].get("allowed", False)]
        if restricted and len(restricted) == len(choices):
            prompt = str(restricted[0]["entitlement"].get("message") or "These listings are outside the configured market-data entitlement.")
        else:
            prompt = "Which exchange listing did you mean?" if slot == 0 else "Select the second stock listing."
        result = _base("market_clarification", prompt,
                       needs_clarification=True, slot=slot, candidates=choices,
                       selected_listings=[x.model_dump() for x in selected], question=question,
                       entity_key=entity_key)
    # For combined questions, don't hold the independent PDF answer hostage to
    # exchange disambiguation. Return verified report evidence alongside choices.
    if include_report and document_selection is not None:
        report = _report(question, document_selection)
        result["document"] = report
        result["sources"] = report.get("sources") or []
        result["document_supported"] = bool(report.get("supported"))
        result["partial"] = True
    return result


def _market_source(item: dict, endpoint: str) -> dict:
    return {"provider": "Twelve Data", "endpoint": endpoint, "symbol": item.get("symbol"),
            "exchange": item.get("exchange"), "provider_datetime": item.get("provider_datetime"),
            "fetched_at_utc": item.get("fetched_at_utc"),
            "security_type": item.get("security_type"),
            "security_label": item.get("security_label")}


def _report(question: str, selection: legacy.DocumentSelectionRequest) -> dict:
    try:
        result = legacy._doc(question, selection)
    except ValueError:
        return _base("document", "The selected reports don't match this request. Choose an appropriate source in the sidebar.", needs_clarification=True)
    except RuntimeError:
        return _base("document", "The financial-report engine is temporarily unavailable. Try again after its dependencies are ready.", error_code="document_backend_unavailable")
    result = {**result, "kind": "document"}
    if result.get("supported") and not result.get("needs_clarification"):
        answer_text = str(result.get("answer") or "").strip()
        # A bare numeric cell is correct data but poor conversational context,
        # especially after a metric/issuer clarification. Add issuer, metric,
        # and quarter only when all can be read from the merged request.
        if answer_text and len(answer_text) <= 80 and not re.search(r"[.!?]\s+", answer_text):
            try:
                from app.question_parser import parse_question
                intent = parse_question(question)
                selected_docs = result.get("selected_documents") or []
                issuer = selected_docs[0].get("issuer") if selected_docs else None
                period = intent.periods[0] if len(intent.periods) == 1 else None
                if issuer and intent.metric and period and re.fullmatch(r"Q[1-4]\d{2}", period):
                    metric_label = intent.metric.replace("_", " ")
                    if intent.metric == "cet1_ratio":
                        metric_label = "CET1 ratio"
                    elif intent.metric in {"basic_earnings_per_share", "diluted_earnings_per_share", "earnings_per_share"}:
                        metric_label = intent.metric.replace("_", " ")
                    period_label = f"Q{period[1]} 20{period[2:]}"
                    result["answer"] = f"{issuer} {metric_label} in {period_label}: {answer_text}."
            except Exception:
                # Context labels are presentation-only. Never let them affect
                # whether the underlying answer is accepted or cited.
                pass
    if result.get("needs_clarification"):
        prompt = str(result.get("answer") or "").casefold()
        if "which financial measure" in prompt or "which metric" in prompt:
            result["clarification_type"] = "metric"
        elif "which bank" in prompt or "which issuer" in prompt:
            result["clarification_type"] = "issuer"
        elif "which pdf" in prompt or "which report" in prompt or "report edition" in prompt:
            result["clarification_type"] = "edition"
    return result


def _overview_edition(question: str) -> tuple[int | None, int | None]:
    """Extract an explicitly mentioned report period; quarter-only is valid."""
    patterns = (
        (r"\bQ([1-4])\s*(20\d{2})\b", lambda m: (int(m.group(2)), int(m.group(1)))),
        (r"\b(20\d{2})\s*Q([1-4])\b", lambda m: (int(m.group(1)), int(m.group(2)))),
        (r"\bQ([1-4])\b", lambda m: (None, int(m.group(1)))),
        (r"\b(first|second|third|fourth) quarter(?: of)?\s*(20\d{2})?\b", lambda m: (int(m.group(2)) if m.group(2) else None, {"first": 1, "second": 2, "third": 3, "fourth": 4}[m.group(1).casefold()])),
    )
    for pattern, convert in patterns:
        match = re.search(pattern, question, re.I)
        if match:
            return convert(match)
    year = re.search(r"\b(20\d{2})\b", question)
    return (int(year.group(1)), None) if year else (None, None)


def _overview_document(question: str, selection: legacy.DocumentSelectionRequest):
    """Choose one indexed report deterministically for a broad company overview.

    Explicit manual selection is respected. In automatic mode, issuer identity is
    matched only against the document registry; an explicit quarter narrows that
    issuer's reports, otherwise the latest indexed report is used. No semantic
    nearest-neighbour or bank-name guessing is used for source selection.
    """
    from app.document_registry import list_documents

    reports = list_documents()
    by_id = {doc.document_id: doc for doc in reports}
    if selection.mode == "manual":
        selected = [by_id[item] for item in selection.document_ids if item in by_id]
        if len(selected) != 1 or len(selection.document_ids) != 1:
            return None, "The manually selected report is unavailable. Choose one indexed report."
        doc = selected[0]
        named_issuers = [name for name in {d.issuer for d in reports if d.issuer and d.issuer.casefold() != "unknown"}
                         if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", question, re.I)]
        if named_issuers and doc.issuer.casefold() not in {name.casefold() for name in named_issuers}:
            return None, f"You selected {doc.display_name}, but the question asks about {', '.join(named_issuers)}. Select the matching report or Automatic."
        requested_year, requested_quarter = _overview_edition(question)
        if ((requested_year is not None and doc.report_year != requested_year) or
                (requested_quarter is not None and doc.report_quarter != requested_quarter)):
            return None, f"You selected {doc.display_name}, but the question requests a different report period. Select the matching report or Automatic."
        return doc, None
    if selection.mode == "compare":
        return None, "A company overview uses one report. Choose Automatic or select one report in the sidebar."

    # Find an explicitly named issuer from the actual indexed registry.
    issuer_names = sorted({doc.issuer for doc in reports if doc.issuer and doc.issuer.casefold() != "unknown"},
                          key=len, reverse=True)
    matches = []
    for issuer in issuer_names:
        if re.search(r"(?<!\w)" + re.escape(issuer) + r"(?!\w)", question, re.I):
            matches.append(issuer)
    matches = list(dict.fromkeys(matches))
    if not matches:
        return None, "Which indexed bank should I summarize? Mention its name, for example, 'Tell me about Nordea'."
    if len(matches) > 1:
        return None, "Please ask for one bank at a time, or use the report-comparison option."

    issuer = matches[0]
    candidates = [doc for doc in reports if doc.issuer.casefold() == issuer.casefold()]
    year, quarter = _overview_edition(question)
    if quarter is not None:
        candidates = [doc for doc in candidates if doc.report_quarter == quarter]
    if year is not None:
        candidates = [doc for doc in candidates if doc.report_year == year]
    if not candidates:
        period = (f"Q{quarter} {year}" if quarter and year else f"Q{quarter}" if quarter else str(year))
        return None, f"I couldn't find an indexed {period} report for {issuer}. Check the available reports in the sidebar."

    # If the question omits a year, prefer the latest indexed edition in the
    # requested quarter. If no edition is named, use the issuer's latest report.
    candidates.sort(key=lambda d: (d.report_year or 0, d.report_quarter or 0, d.indexed_at or "", d.document_id), reverse=True)
    newest = candidates[0]
    same_edition = [d for d in candidates if (d.report_year, d.report_quarter) == (newest.report_year, newest.report_quarter)]
    if len(same_edition) > 1:
        options = "; ".join(d.display_name for d in same_edition)
        return None, f"Several reports match this overview request: {options}. Please select one in the sidebar."
    return newest, None


def _looks_like_marketing_boilerplate(answer: str) -> bool:
    """Reject a profile answer that is only corporate mission/brand copy."""
    text = re.sub(r"[^a-z0-9 ]+", " ", (answer or "").casefold())
    promotional = (
        "for a greater good", "dreams and aspirations", "our vision is to be",
        "trusted financial partner", "proudly served", "accelerating through our scale",
        "best performing financial services group",
    )
    substantive = (
        "personal banking", "corporate banking", "business banking", "investment banking",
        "asset management", "wealth management", "mortgage", "lending", "deposits",
        "savings business", "financial markets", "payment services", "credit portfolio",
        "loan portfolio", "customer segments", "business segments", "insurance",
        "financing", "capital markets", "fund management", "transaction banking",
    )
    has_promo = any(phrase in text for phrase in promotional)
    has_business_detail = any(phrase in text for phrase in substantive)
    return has_promo and not has_business_detail


def _requested_division(question: str, segments: list[str] | None) -> str | None:
    """Identify an explicitly named division question without guessing aliases."""
    if not re.search(r"\b(?:what does|describe|explain)\b", question, re.I):
        return None
    q = question.casefold()
    matches = []
    for segment in segments or []:
        pattern = re.escape(segment).replace(r"\&", r"(?:&|and)")
        if re.search(r"(?<!\w)" + pattern + r"(?!\w)", q, re.I):
            matches.append(segment)
    return max(matches, key=len) if matches else None


def _company_overview(question: str, selection: legacy.DocumentSelectionRequest) -> dict:
    """Build a report-based overview from all chunks of exactly one selected PDF.

    The previous implementation fed a long overview prompt into the financial
    metric-answering path. That path is optimized for specific metrics and often
    retrieved an irrelevant or promotional passage. Day 13.10 instead assembles
    a cautious, deterministic overview from business-area headings and table
    rows already indexed for the selected document, with physical page citations.
    """
    try:
        document, clarification = _overview_document(question, selection)
    except RuntimeError:
        return _base("overview", "The indexed report registry is temporarily unavailable. Try again shortly.", error_code="document_registry_unavailable")
    if clarification:
        return _base("overview", clarification, needs_clarification=True)
    try:
        from app.company_overview import build_indexed_company_overview
        result = build_indexed_company_overview(document)
    except RuntimeError:
        return _base("overview", "The selected report's indexed passages could not be loaded. Check the document index and try again.", error_code="overview_index_unavailable")
    except Exception:
        # Do not expose internal paths, database contents, or provider details.
        return _base("overview", "The company overview could not be assembled from the selected report. Try a specific business area or financial metric.", error_code="overview_build_error")
    result["selected_document"] = document.public_dict()
    if result.get("supported"):
        result["answer"] = f"**Overview based on {document.display_name}**\n\n" + result["answer"].split("\n\n", 1)[-1]
        # A named-division follow-up should not repeat the entire company
        # overview. Keep only that division's source-backed paragraph/pages.
        division = _requested_division(question, result.get("business_segments"))
        if division:
            marker = re.compile(r"\*\*" + re.escape(division) + r":\*\*", re.I)
            paragraph = next((part for part in result["answer"].split("\n\n")
                              if marker.search(part)), None)
            if paragraph:
                result["answer"] = f"**{division} — {document.display_name}**\n\n" + paragraph.lstrip("- ")
                page_map = result.get("segment_page_map") or {}
                pages = set(page_map.get(division, []))
                if pages:
                    result["sources"] = [source for source in result.get("sources", [])
                                         if source.get("page") in pages]
                    result["evidence_pages"] = sorted(pages)
                result["requested_business_segment"] = division
    return result


def _chart_series(first: dict, second: dict) -> list[dict]:
    left = {point["date"]: Decimal(point["close"]) for point in first["points"]}
    right = {point["date"]: Decimal(point["close"]) for point in second["points"]}
    dates = sorted(left.keys() & right.keys())
    if len(dates) < 2:
        return []
    p0, q0 = left[dates[0]], right[dates[0]]
    return [{"date": day, "first_pct": str((left[day] / p0 - 1) * 100),
             "second_pct": str((right[day] / q0 - 1) * 100)} for day in dates]


def _previous_close_answer(quote: dict) -> str:
    """Report the provider's previous-close movement only when both closes exist."""
    close = Decimal(str(quote["close"]))
    previous_raw = quote.get("previous_close")
    if previous_raw in (None, ""):
        return (
            f"Twelve Data did not supply a verified previous-close value for "
            f"{quote['symbol']} on {quote['exchange']}, so I cannot calculate the "
            "absolute price change from the previous close."
        )
    previous = Decimal(str(previous_raw))
    if previous <= 0:
        return (
            f"Twelve Data supplied an invalid previous-close value for {quote['symbol']}; "
            "no change calculation was displayed."
        )
    change = close - previous
    # Calculate both movements from the two verified closes. This keeps the
    # displayed absolute and percentage changes internally consistent instead
    # of mixing one derived number with a separate provider summary field.
    percent = (change / previous) * Decimal(100)
    direction = "increase" if change > 0 else "decrease" if change < 0 else "no change"
    return (
        f"{quote['symbol']} on {quote['exchange']}: latest available close "
        f"{quote['currency']} {close:,.2f}; previous close "
        f"{quote['currency']} {previous:,.2f}. That is a {direction} of "
        f"{quote['currency']} {abs(change):,.2f} ({percent:+.2f}%) from the previous close. "
        f"Provider time: {quote.get('provider_datetime')}. This is provider-reported data, "
        "not a guaranteed real-time quotation."
    )


def _prior_user_question(body: ChatRequest) -> str:
    if body.context_question and body.context_question.strip():
        return body.context_question.strip()
    # Reconstruct a compact, explicit context from the user's recent turns.
    # The UI sends no model-generated summaries: only the original questions.
    context = ""
    for turn in body.history:
        if turn.role != "user":
            continue
        candidate = turn.content.strip()
        if not candidate:
            continue
        if PERIOD_ONLY_FOLLOWUP.fullmatch(candidate):
            merged, _message = _merge_period_followup(candidate, context)
            if merged:
                context = merged
            continue
        metric_merged = _merge_metric_followup(candidate, context)
        if metric_merged:
            context = metric_merged
            continue
        market_merged = _merge_market_previous_close_followup(candidate, context)
        if market_merged:
            context = market_merged
            continue
        # Greetings and acknowledgements do not erase a still-useful subject.
        if SMALLTALK_TERMS.fullmatch(candidate):
            continue
        if _is_financial_or_indexed_bank_question(candidate) or _intent(candidate) in {
            "quote", "history", "compare", "combined", "overview"
        }:
            context = candidate
        else:
            context = ""
    return context

def _prior_metric_clarification_prompt(body: ChatRequest) -> str | None:
    """Recover the most recent assistant metric clarification from chat history."""
    for turn in reversed(body.history):
        if turn.role != "assistant":
            continue

        prompt = turn.content.strip()
        lowered = prompt.casefold()

        if (
            "which financial measure" in lowered
            or "which metric" in lowered
        ):
            return prompt

        # Do not accidentally reuse an older clarification.
        return None

    return None


def _inherit_issuer_for_segment_question(question: str, context_question: str) -> str:
    """Inherit one unambiguous issuer for a segment-specific follow-up only."""
    if _known_issuer_mentions(question):
        return question
    try:
        from app.question_parser import parse_question
        scope = parse_question(question).scopes
    except Exception:
        scope = ("group",)
    if scope == ("group",):
        return question
    issuers = _known_issuer_mentions(context_question)
    if len(issuers) != 1:
        return question
    return f"{issuers[0]} {question}"


def process_chat(body: ChatRequest) -> dict:
    question = body.question.strip()

    prior_question = _prior_user_question(body)

    # Handle two questions in one message as well as genuine follow-up turns.
    inline_period_comparison = _expand_inline_quarter_followup(question)
    if inline_period_comparison:
        question = inline_period_comparison

    # Short market follow-ups must retain the last unambiguous ticker/listing;
    # they are not standalone out-of-scope financial questions.
    previous_close_question = _merge_market_previous_close_followup(question, prior_question)
    if previous_close_question:
        question = previous_close_question

    # Preserve issuer and period when the user changes only the financial metric.
    metric_followup = _merge_metric_followup(question, prior_question)
    if metric_followup:
        question = metric_followup

    # Reuse a user-confirmed listing for an unambiguous follow-up such as
    # "show its performance". Explicit new company/ticker names always win.
    if re.search(r"\b(?:it|its|that stock|that share|the same stock|their performance)\b", question, re.I):
        prior_entities = _prior_market_entities(body)
        if prior_entities and not TICKER_TOKEN.findall(question):
            question = question.rstrip(" .?!") + " for " + " and ".join(prior_entities) + " stock performance"

    # A short quarter-only reply must reuse the previous user question when
    # available; without context it requests the complete question, never a bank.
    if PERIOD_ONLY_FOLLOWUP.fullmatch(question):
        target_period_question = _merge_target_period_year_reply(
            question, prior_question, body.context_prompt or ""
        )
        if target_period_question:
            question = target_period_question
        else:
            merged, message = _merge_period_followup(question, prior_question)
            if message:
                return _base(
                    "document_clarification",
                    message,
                    needs_clarification=True,
                    context_required=True,
                    clarification_type="period",
                )
            question = merged or question

    elif COMPARISON_PERIOD_FOLLOWUP.fullmatch(question):
        merged, message = _merge_comparison_period_followup(
            question, prior_question
        )
        if message:
            return _base(
                "document_clarification",
                message,
                needs_clarification=True,
                context_required=True,
                clarification_type="period_comparison",
            )
        question = merged or question

    elif SHORT_SLOT_REPLY.fullmatch(question):
        clarification_context = (
            body.context_question.strip()
            if body.context_question
            else prior_question
        )

        clarification_prompt = (
                body.context_prompt
                or _prior_metric_clarification_prompt(body)
        )

        if clarification_context and clarification_prompt:
            merged = _merge_clarification_reply(
                question,
                clarification_context,
                clarification_prompt,
            )

            if merged:
                question = merged

    # A named business segment is not enough to identify its parent institution.
    # Reuse the prior issuer only if exactly one was established in the context.
    question = _inherit_issuer_for_segment_question(question, prior_question)

    intent = _intent(question)
    if intent == "about":
        is_creator_question = bool(re.search(
            r"\bwho\s+(?:made|created|built|developed)\s+(?:you|this assistant)|"
            r"who is your (?:creator|developer)\b", question, re.I))
        if is_creator_question:
            answer = CREATOR_ANSWER
        else:
            from app.market_entitlements import configured_plan
            plan = configured_plan().title()
            coverage = (
                "The Basic default feed used here is US listed equities; primary Nordic exchanges require Pro; OTC data needs a separate licence."
                if plan == "Basic" else
                "Primary Nordic exchange availability depends on your Pro exchange entitlements; OTC data still needs a separate licence."
            )
            answer = CAPABILITIES_ANSWER.replace(
                "Market coverage depends on the Twelve Data plan configured in this installation. Basic uses the default US listed-equity feed here; primary Nordic exchange feeds require Pro; OTC securities require a separate data licence even on Pro.",
                f"This installation is configured for Twelve Data {plan}. {coverage}"
            )
        return _base("about", answer, supported=True, needs_clarification=False)
    if intent == "smalltalk":
        if re.fullmatch(r"\s*(?:how are you|how's it going)\s*[.!?]*", question, re.I):
            answer = "I'm doing well, thanks for asking! What financial report or market question can I help you with?"
        elif re.fullmatch(r"\s*(?:hello|hi|hey|good morning|good afternoon|good evening)\s*[.!?]*", question, re.I):
            answer = "Hello! What would you like to explore in the financial reports or stock market?"
        else:
            answer = ("Great! You can ask me about an indexed bank, a financial metric, a company overview, "
                      "or a supported stock. For example: 'Tell me about Nordea' or "
                      "'What was Nordea's CET1 ratio in Q3 2026?'")
        return _base("smalltalk", answer, supported=True)
    if PERSONAL_ACCOUNT_REQUEST.search(question):
        return _base(
            "out_of_scope",
            "I can't access personal bank-account balances, private transactions, or individual banking records. "
            "I can answer questions about published financial reports from the indexed banks and supported stock-market data.",
            supported=False, needs_clarification=False, out_of_scope=True,
        )
    if not _is_financial_or_indexed_bank_question(question):
        return _base(
            "out_of_scope",
            "That question is outside my scope. I'm focused on financial reports from the indexed banks and supported stock-market data. "
            "I can't assess personal or unrelated questions, but you can ask me about a bank's profit, "
            "capital ratio, business segments, or share price.",
            supported=False, needs_clarification=False,
        )
    if intent == "overview":
        return _company_overview(question, body.document_selection)
    if intent == "document":
        result = _report(question, body.document_selection)
        if result.get("needs_clarification"):
            # Carry the fully merged request forward so a second clarification
            # doesn't replace the original bank/metric/period with "Use Q3".
            result["clarification_context_question"] = question
        return result
    entities = _entities(question, intent)
    selected = list(body.selected_listings)
    remembered = {_memory_key(key): value for key, value in body.remembered_listings.items() if _memory_key(key)}
    if intent == "compare" and len(entities) < 2:
        return _base("market_clarification", "Tell me which two stocks you want to compare, such as 'Compare AAPL and MSFT stock performance'.", needs_clarification=True, candidates=[], question=question)
    if not entities and not selected:
        return _base("market_clarification", "Which company or stock ticker should I look up? For example, 'Apple stock price'.", needs_clarification=True, candidates=[], question=question)
    if not entities:
        entities = [s.entity_key or s.symbol for s in selected]
    try:
        resolved: list[Listing] = []
        for slot in range(2 if intent == "compare" else 1):
            item, choices = _selected_or_search(entities[slot], slot, selected, remembered)
            if item is None:
                return _select_message(question, slot, choices, resolved,
                                       body.document_selection, include_report=(intent == "combined"),
                                       entity_key=_memory_key(entities[slot]))
            resolved.append(item)
        if intent == "compare":
            if (resolved[0].symbol, resolved[0].exchange.casefold()) == (resolved[1].symbol, resolved[1].exchange.casefold()):
                return _base("market_clarification", "Choose two different listings for a stock comparison.", needs_clarification=True)
            requested_days = _calendar_days(question)
            periods = _history_request_size(question, body.history_bars)
            # Fetch each time series once. Reusing these observations avoids
            # four provider calls and guarantees the return and chart share
            # exactly the same date-filtered evidence.
            first = legacy.market_client.history(
                resolved[0].symbol, resolved[0].exchange, outputsize=periods
            )
            second = legacy.market_client.history(
                resolved[1].symbol, resolved[1].exchange, outputsize=periods
            )
            result, first, second = _compare_history_data(first, second, requested_days)
            result = {**result, "chart_points": _chart_series(first, second)}
            names = [f"{r['symbol']} ({r['exchange']}) {Decimal(r['return_percent']):+.2f}%" for r in result["results"]]
            window_text = (
                f"For the trailing {requested_days}-calendar-day window ending "
                f"{result['end_date']} ({result['start_date']} to {result['end_date']}; "
                f"{result['shared_trading_dates']} shared trading observations), "
                if requested_days is not None else
                f"On shared trading dates ({result['start_date']} to {result['end_date']}; "
                f"{result['shared_trading_dates']} shared observations), "
            )
            return {**_base("market_compare", window_text + "price-only returns: " + " vs. ".join(names) + ". These are not dividend-adjusted total returns.",
                            market=result, sources=[_market_source(first, "/time_series"), _market_source(second, "/time_series")]),
                    "supported": True}
        listing = resolved[0]
        if intent == "history":
            requested_days = _calendar_days(question)
            history = legacy.market_client.history(
                listing.symbol, listing.exchange,
                outputsize=_history_request_size(question, body.history_bars),
            )
            if requested_days is not None:
                history = _filter_history_calendar_window(history, requested_days)
                answer = (
                    f"Closing-price history for {listing.symbol} on {listing.exchange} over the "
                    f"trailing {requested_days} calendar days ending {history['window_end']}: "
                    f"{len(history['points'])} trading observations from "
                    f"{history['points'][0]['date']} to {history['points'][-1]['date']}. "
                    "Prices exclude dividends and are not guaranteed live."
                )
            else:
                answer = (f"Here is the available closing-price history for {listing.symbol} on {listing.exchange}. "
                          f"The chart contains {len(history['points'])} trading observations, not necessarily calendar days. "
                          "Prices exclude dividends and are not guaranteed live.")
            return {**_base("market_history", answer, market=history, sources=[_market_source(history, "/time_series")]), "supported": True}
        quote = legacy.market_client.quote(listing.symbol, listing.exchange)
        if MARKET_PREVIOUS_CLOSE_FOLLOWUP.search(question):
            quote_answer = _previous_close_answer(quote)
            return {**_base("market_quote", quote_answer, market=quote,
                            sources=[_market_source(quote, "/quote")],
                            show_quote_answer=True),
                    "supported": quote.get("previous_close") not in (None, "")}
        quote_answer = (f"The latest available provider-reported close for {listing.symbol} on {listing.exchange} is "
                        f"{quote['currency']} {quote['close']} (provider time: {quote['provider_datetime']}). "
                        "This quotation is not guaranteed real-time.")
        if intent == "quote":
            return {**_base("market_quote", quote_answer, market=quote, sources=[_market_source(quote, "/quote")]), "supported": True}
        report = _report(question, body.document_selection)
        supported = bool(report.get("supported"))
        return {**_base("combined", quote_answer + "\n\nReport: " + str(report.get("answer") or "No verified report answer."),
                        market=quote, document=report,
                        sources=[_market_source(quote, "/quote")] + (report.get("sources") or []),
                        partial=not supported), "supported": supported}
    except MarketError as exc:
        market_error = _friendly_error(exc)
        if intent == "combined":
            report = _report(question, body.document_selection)
            report_answer = str(report.get("answer") or "No verified report answer.")
            # Preserve the legacy combined answer text for API clients while
            # exposing separate market/report provenance for the chat renderer.
            # Overall supported remains False because the requested market leg
            # was not retrieved; report_supported records the independent PDF leg.
            return {**market_error, "kind": "combined", "partial": True,
                    "market_supported": False,
                    "answer": market_error["answer"] + "\n\nReport: " + report_answer,
                    "market_message": market_error["answer"],
                    "document": report, "sources": report.get("sources", []),
                    "supported": False,
                    "report_supported": bool(report.get("supported"))}
        return market_error


@router.post("/chat_v1")
def chat_v1(request: Request, body: ChatRequest) -> dict:
    legacy._gate(request)
    if not body.question.strip():
        raise HTTPException(status_code=422, detail="empty_question")
    return process_chat(body)
