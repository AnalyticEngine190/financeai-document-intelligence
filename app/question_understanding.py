"""Detect financial questions that need clarification."""

import re


_FINANCIAL_SCOPE_TERMS = re.compile(
    r"\b(?:finance|financial|bank|banking|report|factbook|interim report|"
    r"profit|earnings|income|revenue|sales|turnover|expense|cost|margin|"
    r"capital|CET1|Tier 1|liquidity|LCR|loan|lending|deposit|asset|liability|"
    r"equity|credit|risk|ratio|return on equity|ROE|dividend|stock|share price|"
    r"market|ticker|business segment|quarterly results|Q[1-4]\s*20\d{2})\b", re.I)
_DEFAULT_ISSUERS = ("Nordea", "SEB", "Handelsbanken")
_OUT_OF_SCOPE = (
    "I'm focused on indexed bank reports and supported market data, so that question "
    "is outside my scope. Ask me about a bank's reported profit, income, capital, "
    "liquidity, business segments, or share-price data."
)


def is_in_financial_scope(question: str, issuer_names=()) -> bool:
    """Conservative scope gate before document/issuer disambiguation."""
    if _FINANCIAL_SCOPE_TERMS.search(question):
        return True
    names = sorted(set(_DEFAULT_ISSUERS) | {str(x) for x in issuer_names if x},
                   key=len, reverse=True)
    return any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", question, re.I)
               for name in names)


def out_of_scope_response() -> str:
    return _OUT_OF_SCOPE


def clarification_for(question: str) -> str | None:
    q = " ".join(question.lower().split())

    # First implementation: ambiguous revenue terminology.
    if not re.search(r"\b(revenue|sales|turnover)\b", q):
        return None

    # An explicitly named income measure is unambiguous.
    specific_metrics = (
        "total income",
        "operating income",
        "net interest income",
        "net fee and commission income",
    )

    if any(metric in q for metric in specific_metrics):
        return None

    # Preserve existing behavior for explicitly dated
    # questions, including unsupported future years.
    if re.search(r"\b(?:19|20)\d{2}\b", q):
        return None

    return (
        "Which revenue measure do you mean: total income, "
        "net interest income, or net fee and commission income? "
        "I can use the latest quarter in the selected report."
    )


def small_talk_response(question: str) -> str | None:
    """Handle a small set of casual messages safely."""

    cleaned = re.sub(
        r"[^\w\s]", "", question.casefold()
    )
    cleaned = " ".join(cleaned.split())

    if cleaned in {"hi", "hello", "hey", "good morning"}:
        return (
            "Hello! I'm your Financial AI Assistant. "
            "What would you like to explore?"
        )

    if cleaned in {"how are you", "hows it going"}:
        return (
            "Doing well and ready to help! "
            "Want to explore some financial results?"
        )

    if cleaned in {"thanks", "thank you"}:
        return "You're welcome!"


    # Explain financial concepts when the user asks
    # for a definition, not a reported financial value.
    if cleaned in {
        "what is cet1",
        "what does cet1 mean",
        "explain cet1",
    }:
        return (
            "CET1 stands for Common Equity Tier 1. "
            "It is a bank's highest-quality regulatory "
            "capital, primarily common equity and "
            "retained earnings after regulatory adjustments. "
            "The CET1 ratio compares that capital with "
            "risk-weighted assets. You can also ask me "
            "for Nordea's reported CET1 ratio."
        )

    # Explain the actual capabilities of this application.
    if cleaned in {
        "what are your capabilities",
        "what is your capabilities",
        "what can you do",
        "what can you help me with",
    }:
        return (
            "I can analyze the indexed Nordea Q3 2026 "
            "financial factbook, answer questions about "
            "reported financial metrics, explain results, "
            "and provide PDF page citations. I can also "
            "clarify ambiguous questions and tell you when "
            "the available evidence is insufficient. "
            "My Fun Mode adds optional visual reactions."
        )



    return None