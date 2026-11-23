"""Pure presentation-only meme selection for optional Fun mode.

No calls to Ollama, retrieval, or financial calculations.  Numeric answers
alone are always neutral: magnitude is not equivalent to good or bad news.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


@dataclass(frozen=True)
class Reaction:
    category: str
    headline: str
    caption: str
    image_file: str


# 12 original, locally bundled illustrations; no remote meme dependencies.
REACTIONS = {
    "positive": (
        Reaction("positive", "📈 STONKS ENERGY", "The reported change looks favorable.", "positive_1.webp"),
        Reaction("positive", "🚀 UPWARD MOMENTUM", "A favorable move, according to the reported figures.", "positive_2.webp"),
    ),
    "negative": (
        Reaction("negative", "📉 NOT STONKS", "The reported change is unfavorable.", "negative_1.webp"),
        Reaction("negative", "🥀 THE CHART NEEDS A HUG", "This financial trend is going the wrong way.", "negative_2.webp"),
    ),
    "risk": (
        Reaction("risk", "⚡ RISK RADAR ACTIVATED", "A reported risk deserves a closer look.", "risk_1.webp"),
        Reaction("risk", "🛡️ CAUTION FLAG RAISED", "A potentially serious financial warning appears in the evidence.", "risk_2.webp"),
    ),
    "neutral": (
        Reaction("neutral", "🧮 NUMBER CRUNCHER APPROVED", "A useful figure, without a built-in good-or-bad verdict.", "neutral_1.webp"),
        Reaction("neutral", "🔎 RECEIPTS ACQUIRED", "Facts first. Financial vibes second.", "neutral_2.webp"),
    ),
    "mixed": (
        Reaction("mixed", "⚖️ MIXED MARKET SIGNALS", "Some changes look favorable; others do not.", "mixed_1.webp"),
        Reaction("mixed", "🎢 FINANCIAL ROLLERCOASTER", "One answer, two directions.", "mixed_2.webp"),
    ),
    "unsupported": (
        Reaction("unsupported", "🕵️ NO RECEIPTS, NO CLAIMS", "The evidence isn't sufficient for a verified answer.", "unsupported_1.webp"),
        Reaction("unsupported", "🤖 DATA NOT FOUND", "No hallucinations on this trading floor.", "unsupported_2.webp"),
    ),
}

# Conservative metric-specific wording. A high ratio or an increase alone
# does NOT imply positive sentiment (loan losses can increase, too).
GOOD_PATTERNS = (
    r"\b(?:profit|profits|revenue|revenues|earnings|net income)\s+(?:has\s+|have\s+)?(?:increased|improved|rose|grew)\b",
    r"\b(?:higher|growing|increased|improved)\s+(?:net\s+)?(?:profit|profits|revenue|revenues|earnings)\b",
    r"\b(?:costs|expenses|loan losses|default rates|defaults)\s+(?:have\s+|has\s+)?(?:decreased|declined|fell|dropped)\b",
    r"\b(?:cet1(?: capital)? ratio|liquidity coverage ratio)\s+(?:has\s+)?(?:increased|improved|rose)\b",
)
BAD_PATTERNS = (
    r"\b(?:profit|profits|revenue|revenues|earnings|net income)\s+(?:has\s+|have\s+)?(?:decreased|declined|fell|dropped)\b",
    r"\b(?:lower|falling|decreased|declining)\s+(?:net\s+)?(?:profit|profits|revenue|revenues|earnings)\b",
    r"\b(?:costs|expenses|loan losses|default rates|defaults)\s+(?:have\s+|has\s+)?(?:increased|rose|grew)\b",
    r"\b(?:cet1(?: capital)? ratio|liquidity coverage ratio)\s+(?:has\s+)?(?:decreased|declined|fell|dropped)\b",
)
RISK_PATTERNS = (
    r"\b(?:liquidity shortfall|capital shortfall|insufficient capital|material uncertainty)\b",
    r"\b(?:breached|violated)\s+(?:the\s+)?(?:regulatory\s+)?(?:capital\s+)?(?:minimum|requirement|limit)\b",
    r"\bbelow\s+(?:the\s+)?(?:regulatory|required)\s+minimum\b",
)


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_reaction(answer: str, supported: bool) -> str:
    """Choose a cautious category using the supported *answer only*."""
    if not supported:
        return "unsupported"

    # Don't try to interpret unsupported or absent facts as sentiment.
    if not answer or not answer.strip():
        return "neutral"

    if _matches(RISK_PATTERNS, answer):
        return "risk"

    good = _matches(GOOD_PATTERNS, answer)
    bad = _matches(BAD_PATTERNS, answer)

    if good and bad:
        return "mixed"
    if good:
        return "positive"
    if bad:
        return "negative"
    return "neutral"


def choose_reaction(question: str, answer: str, supported: bool) -> Reaction:
    """Stable meme choice: toggling or rerunning won't change the reaction."""
    category = classify_reaction(answer, supported)
    choices = REACTIONS[category]
    digest = sha256((question + "\0" + answer + "\0" + category).encode("utf-8")).digest()
    return choices[int.from_bytes(digest[:4], "big") % len(choices)]


def image_path(reaction: Reaction) -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "reactions" / reaction.image_file
