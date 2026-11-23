"""Query-focused, source-scoped narrative evidence selection (Day 10.5.1).

Ranked hits are *suggestions* from hybrid search. The authoritative text is
reconstructed from the selected document's indexed pages. No unselected page,
file, or raw model-provided passage may enter the evidence list.

No report name, page number, question, answer, or gold benchmark value is
hard-coded into this module.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re

# Exclude grammatical words, not financial vocabulary. 'report' and 'quarter'
# are low-information in this type of question; keep numbers and dates.
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "did", "do", "does",
    "for", "from", "how", "i", "in", "into", "is", "it", "its", "of", "on", "or", "our",
    "report", "reports", "according", "should", "the", "their", "there", "these", "they",
    "this", "those", "to", "was", "were", "what", "when", "where", "which", "who",
    "why", "with", "would", "you", "your", "quarter", "third", "second", "first",
})

# Each selected excerpt is short enough for local 4B-class models. It remains
# an unchanged substring of the indexed page, for exact quote verification.
EXCERPT_CHARS = 1750
MAX_EVIDENCE_PAGES = 5
MAX_SEARCH_HITS = 24


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def _key_terms(question: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(t for t in _tokens(question) if len(t) >= 3 and t not in STOPWORDS))


def _coverage_score(question_terms: tuple[str, ...], text: str, document_frequency: Counter,
                    total_documents: int) -> float:
    """Weighted token coverage in a compact candidate excerpt, not page length."""
    words = set(_tokens(text))
    score = 0.0
    for t in question_terms:
        if t in words:
            score += 0.75 + math.log1p(total_documents / (1 + document_frequency[t]))
    # Local co-occurrence matters more than scattered terms across a long page.
    # Higher-order phrases are generated from the question rather than fixed
    # domain/benchmark-specific strings.
    full = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    query_words = _tokens(" ".join(question_terms))
    for size, multiplier in ((2, 0.6), (3, 1.0)):
        for i in range(len(query_words) - size + 1):
            phrase = " ".join(query_words[i:i + size])
            if phrase in full:
                score += multiplier
    return score


def _intent_bonus(question: str, excerpt: str) -> float:
    """Prefer evidence that answers the *kind* of explanation being asked.

    This is generic discourse matching, not an answer list. A match is only a
    ranking signal and cannot bypass the model's quote/citation checks.
    """
    q = question.casefold()
    t = excerpt.casefold()
    score = 0.0
    if re.search(r"\b(?:why|factor|factors|reason|reasons|drivers?|caused|contributed)\b", q):
        if re.search(r"\b(?:explained|because|driven|mainly|due to|resulted|contributed|reflecting|attributed)\b", t):
            score += 2.0
        if re.search(r"\b(?:higher|lower|increase|increased|decrease|decreased)\b", t):
            score += 0.8
    if re.search(r"\b(?:rise|rose|increase|increased|growth|grew)\b", q):
        if re.search(r"\b(?:rose|increased|growth|grew)\b", t):
            score += 1.5
        # Strong preference for a sentence mentioning the *requested metric*
        # together with the reported direction, not just an unrelated rise.
        qterms = _key_terms(question)
        normalized_excerpt = " ".join(_tokens(excerpt))
        for width in (3, 2):
            for j in range(len(qterms) - width + 1):
                phrase = " ".join(qterms[j:j + width])
                if re.search(rf"\b{re.escape(phrase)}\s+(?:increased|rose|grew)\b", normalized_excerpt):
                    return score + 4.0
    if re.search(r"\b(?:change|changed|presentation|reclassif|introduced)\b", q):
        if re.search(r"\b(?:previously|instead of|revised|changed|reported|presented)\b", t):
            score += 1.5
    return score


def _excerpt(question: str, text: str, terms: tuple[str, ...], df: Counter, n: int,
             size: int = EXCERPT_CHARS) -> tuple[str, float]:
    """Choose one contiguous, exact substring that concentrates question terms."""
    if not text:
        return "", 0.0
    if len(text) <= size:
        return text, _coverage_score(terms, text, df, n) + _intent_bonus(question, text)

    # Candidate windows begin at local matches, at newline boundaries and at
    # regularly spaced offsets so a question doesn't depend on PDF reading
    # order or relevant content being near the first 6,500 characters.
    low = text.casefold()
    starts = {0, max(0, len(text) - size)}
    for term in terms:
        for match in list(re.finditer(rf"\b{re.escape(term)}\b", low))[:24]:
            starts.add(max(0, match.start() - size // 3))
            starts.add(max(0, match.start() - (size * 2) // 3))
    for pos in range(0, len(text), max(500, size // 2)):
        starts.add(pos)

    best_text, best_score = "", float("-inf")
    for start in sorted(starts):
        start = min(start, len(text) - size)
        candidate = text[start:start + size]
        score = _coverage_score(terms, candidate, df, n) + _intent_bonus(question, candidate)
        # Equal-strength passages prefer earlier document order, not larger
        # page numbers or any question-specific fixed source.
        if score > best_score:
            best_text, best_score = candidate, score
    return best_text, best_score


@dataclass(frozen=True)
class NarrativeEvidence:
    """One selected indexed page plus the exact excerpt shown to the model."""
    page: dict
    excerpt: str
    score: float
    original_rank: int


def select_narrative_evidence(question: str, indexed_pages: list[dict],
                              ranked_hits: list[dict],
                              max_pages: int = MAX_EVIDENCE_PAGES) -> list[NarrativeEvidence]:
    """Rerank source-valid candidate pages and return focused excerpts.

    Deliberately does not scan nonretrieved pages: the initial hybrid stage
    must surface the source candidate within the search horizon. Never
    trust the text in a search hit; use the authoritative page-index record.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    index = {(p["document"], int(p["page"])): p for p in indexed_pages}
    unique: list[tuple[int, dict]] = []
    seen: set[tuple[str, int]] = set()
    for rank, hit in enumerate(ranked_hits, 1):
        try:
            key = (hit.get("document"), int(hit.get("page", -1)))
        except (TypeError, ValueError):
            continue
        if key not in index or key in seen:
            continue
        seen.add(key)
        unique.append((rank, index[key]))

    if not unique:
        return []
    terms = _key_terms(question)
    texts = [str(p.get("text", "")) for _, p in unique]
    df = Counter(t for t in terms for s in texts if t in set(_tokens(s)))
    candidates = []
    for rank, page in unique:
        excerpt, score = _excerpt(question, str(page.get("text", "")), terms, df, len(unique))
        if excerpt.strip():
            candidates.append(NarrativeEvidence(page=page, excerpt=excerpt,
                                                score=score, original_rank=rank))

    # Stronger local coverage can promote a rank-6 result over a weak rank-2
    # hit; when coverage ties, preserve hybrid search order. No scores are
    # interpreted as probabilities or as proof that a statement is true.
    # Hybrid rank is a valuable prior. Without it, generic "net interest
    # income" mentions in divisional tables can outrank the group-level
    # commentary which the hybrid search placed first. Give early hits a
    # *bounded* preference while still allowing unusually strong later
    # evidence to move up. This is reranking, not a hard first-k cutoff.
    def rank_adjusted(ev: NarrativeEvidence) -> float:
        return ev.score + 4.5 / math.log2(ev.original_rank + 1)

    candidates.sort(key=lambda ev: (-rank_adjusted(ev), ev.original_rank))
    return candidates[:max_pages]


def render_narrative_evidence(items: list[NarrativeEvidence]) -> str:
    return "\n\n".join(
        f"[{i}] Source: {item.page['document']} | PDF page {item.page['page']}\n{item.excerpt}"
        for i, item in enumerate(items, 1)
    )
