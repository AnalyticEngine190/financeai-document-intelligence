"""Hybrid financial passage retrieval.

Combines semantic and TF-IDF retrieval using Reciprocal Rank Fusion,
then gives modest priority to evidence matching the requested financial
metric, reporting period, and company/segment scope.

Scores are ranking signals, NOT probabilities or confidence estimates.
"""

import re

from sklearn.feature_extraction.text import TfidfVectorizer

from app.embeddings import embed_texts
from app.vector_store import get_collection


RRF_K = 60
SEMANTIC_CANDIDATES = 30


# These phrases indicate an explicitly requested business segment. In that
# case, we must not favor group-wide income over the specified segment.
SEGMENT_NAMES = (
    "personal banking",
    "business banking",
    "asset wealth management",
    "asset and wealth management",
    "large corporates institutions",
    "large corporates and institutions",
    "life pension",
    "life and pension",
    "group functions",
)


def normalize(text: str) -> str:
    """Lowercase and normalize punctuation and whitespace."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def expand_financial_terms(question: str) -> str:
    """Add known equivalent financial expressions to keyword retrieval."""
    additions = []

    if re.search(r"\bcet[\s-]*1\b", question, re.I):
        additions.extend(("common equity tier 1", "common equity tier 1 ratio"))

    if re.search(r"\blcr\b", question, re.I):
        additions.append("liquidity coverage ratio")

    if re.search(r"\brea\b", question, re.I):
        additions.append("risk exposure amount")

    if re.search(r"\brwa\b", question, re.I):
        additions.append("risk weighted assets")

    if re.search(r"\btotal(?:\s+operating)?\s+income\b", question, re.I):
        additions.append("total operating income")

    if re.search(r"\brevenue\b", question, re.I):
        additions.append("total operating income")

    return " ".join((question, *additions))


def metric_phrases(question: str) -> list[str]:
    """Return metric labels important to answering this particular question."""
    q = normalize(question)
    phrases = []

    if ("cet1" in q or "cet 1" in q or "common equity tier 1" in q) and "ratio" in q:
        phrases.extend((
            "common equity tier 1 ratio",
            "common equity tier 1 capital ratio",
            "cet1 ratio",
            "cet 1 capital ratio",
        ))

    if "liquidity coverage ratio" in q or "lcr" in q.split():
        phrases.append("liquidity coverage ratio")

    if "capital position" in q:
        phrases.append("capital position")

    if "net interest income" in q:
        phrases.append("net interest income")

    if "net fee and commission income" in q:
        phrases.append("net fee and commission income")

    if "total operating income" in q or "total income" in q or "revenue" in q.split():
        phrases.extend(("total operating income", "total income"))

    return list(dict.fromkeys(phrases))


def _requested_quarter_code(question: str) -> str | None:
    """Convert 'Q3 2026', '2026 Q3' or 'Q326' to the PDF-style 'q326'."""
    q = question.lower()

    match = re.search(r"\bq([1-4])\s*[-/]?\s*(20\d{2})\b", q)
    if match:
        return f"q{match.group(1)}{match.group(2)[-2:]}"

    match = re.search(r"\b(20\d{2})\s*[-/]?\s*q([1-4])\b", q)
    if match:
        return f"q{match.group(2)}{match.group(1)[-2:]}"

    match = re.search(r"\bq([1-4])(\d{2})\b", q)
    if match:
        return f"q{match.group(1)}{match.group(2)}"

    return None


def _asks_about_income(question: str) -> bool:
    q = normalize(question)
    return any(label in q for label in (
        "net interest income",
        "net fee and commission income",
        "total operating income",
        "total income",
        "revenue",
    ))


def _asks_about_segment(question: str) -> bool:
    q = normalize(question)
    return any(segment in q for segment in SEGMENT_NAMES) or any(
        phrase in q for phrase in ("by business area", "by segment")
    )


def hybrid_search(
    question: str,
    top_k: int = 5,
    document_id: str | None = None,
) -> list[dict]:
    """Rank PDF chunks using semantic, keyword, and table-context signals.

    Returns [{'id', 'text', 'document', 'page', 'chunk_index', 'score'}].
    """
    if not question.strip():
        raise ValueError("Question cannot be empty")
    if top_k < 1:
        raise ValueError("top_k must be positive")

    collection = get_collection()
    where = {"document_id": document_id} if document_id else None

    records = collection.get(
        where=where,
        include=["documents", "metadatas"],
    )
    ids = records["ids"]
    texts = records["documents"]
    metadatas = records["metadatas"]

    if not ids:
        return []

    # Search embedding uses the original question (aliases affect keywords).
    semantic = collection.query(
        query_embeddings=embed_texts([question]),
        n_results=min(SEMANTIC_CANDIDATES, len(ids)),
        where=where,
        include=["distances"],
    )
    semantic_ids = semantic["ids"][0]

    vectorizer = TfidfVectorizer(
        stop_words="english", ngram_range=(1, 2), sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(texts)
    query_vector = vectorizer.transform([expand_financial_terms(question)])
    keyword_scores = (matrix @ query_vector.T).toarray().ravel()

    keyword_indices = sorted(
        range(len(ids)), key=lambda i: keyword_scores[i], reverse=True,
    )
    keyword_ids = [ids[i] for i in keyword_indices if keyword_scores[i] > 0]

    # Reciprocal Rank Fusion combines the two ordinal rankings.
    fused: dict[str, float] = {}
    for ranking in (semantic_ids, keyword_ids):
        for rank, chunk_id in enumerate(ranking, 1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)

    lookup = {
        chunk_id: (text, metadata)
        for chunk_id, text, metadata in zip(ids, texts, metadatas)
    }

    # Assemble page-level context for identifying table headings even when
    # the header and numeric row were separated into overlapping chunks.
    page_chunks: dict[tuple[str, int], list[tuple[int, str]]] = {}
    for text, metadata in zip(texts, metadatas):
        key = (metadata["filename"], metadata["page_number"])
        page_chunks.setdefault(key, []).append(
            (metadata.get("chunk_index", 0), text)
        )
    pages = {
        key: normalize(" ".join(
            text for _, text in sorted(chunks, key=lambda pair: pair[0])
        ))
        for key, chunks in page_chunks.items()
    }

    phrases = metric_phrases(question)
    quarter_code = _requested_quarter_code(question)
    quarterly_group_income = (
        quarter_code is not None
        and _asks_about_income(question)
        and not _asks_about_segment(question)
    )

    def is_group_quarterly_income(page_text: str) -> bool:
        return (
            "key financial figures" in page_text
            and "income statement" in page_text
            and "12 quarter overview" in page_text
            and quarter_code in page_text
        )

    # Seed truly relevant group-quarter candidates even if the generic
    # semantic/TF-IDF top lists didn't surface them. No page numbers or
    # financial figures are hardcoded.
    if quarterly_group_income:
        for chunk_id, (text, metadata) in lookup.items():
            key = (metadata["filename"], metadata["page_number"])
            if is_group_quarterly_income(pages[key]) and any(
                normalize(phrase) in normalize(text) for phrase in phrases
            ):
                fused.setdefault(chunk_id, 0.0)

    for chunk_id in fused:
        text, metadata = lookup[chunk_id]
        normalized_text = normalize(text)
        page_key = (metadata["filename"], metadata["page_number"])
        page_text = pages[page_key]

        # Avoid section dividers and navigation taking priority over data.
        if "table of contents" in page_text:
            fused[chunk_id] -= 0.03
        if len(text.split()) < 20:
            fused[chunk_id] -= 0.02

        # Previous metric matching: keep improvements for CET1 and LCR.
        if any(normalize(phrase) in normalized_text for phrase in phrases):
            fused[chunk_id] += 0.035
            if "ratio" in question.lower() and "%" in text:
                fused[chunk_id] += 0.007

        # Quarter-specific consolidated income reports take precedence over
        # annual/YTD or business-segment figures for group-wide questions.
        if quarterly_group_income:
            if is_group_quarterly_income(page_text):
                fused[chunk_id] += 0.10
                # Give the actual metric row more priority than unrelated
                # chunks from the same table/page.
                if any(normalize(phrase) in normalized_text for phrase in phrases):
                    fused[chunk_id] += 0.02
            elif "10 year overview" in page_text:
                fused[chunk_id] -= 0.02
            elif "business areas" in page_text:
                fused[chunk_id] -= 0.015

    ranked_ids = sorted(fused, key=fused.get, reverse=True)[:top_k]

    return [
        {
            "id": chunk_id,
            "text": lookup[chunk_id][0],
            "document": lookup[chunk_id][1]["filename"],
            "page": lookup[chunk_id][1]["page_number"],
            "chunk_index": lookup[chunk_id][1].get("chunk_index", 0),
            "score": fused[chunk_id],
        }
        for chunk_id in ranked_ids
    ]
