
"""Regression tests for financial hybrid retrieval."""

from unittest.mock import MagicMock, patch

from app.hybrid_search import hybrid_search


def test_cet1_expansion_prioritizes_reported_ratio():
    collection = MagicMock()

    collection.get.return_value = {
        "ids": ["buffer", "capital", "navigation"],
        "documents": [
            (
                "Nordea Q3 2026 capital buffers. "
                "Common Equity Tier 1 available to meet "
                "capital buffers: 10.4 percentage points."
            ),
            (
                "Nordea Q3 2026 capital position. "
                "Own funds including profit. "
                "Common Equity Tier 1 ratio 15.8%. "
                "Tier 1 capital ratio 17.7%."
            ),
            (
                "Nordea Q3 2026 Table of contents. "
                "Capital position, liquidity and risk."
            ),
        ],
        "metadatas": [
            {"filename": "report.pdf", "page_number": 44},
            {"filename": "report.pdf", "page_number": 40},
            {"filename": "report.pdf", "page_number": 2},
        ],
    }

    # Simulate poor semantic ordering.
    collection.query.return_value = {
        "ids": [["buffer", "navigation", "capital"]]
    }

    with (
        patch(
            "app.hybrid_search.get_collection",
            return_value=collection,
        ),
        patch(
            "app.hybrid_search.embed_texts",
            return_value=[[0.1, 0.2]],
        ),
    ):
        results = hybrid_search(
            "What was Nordea's CET1 capital ratio in Q3 2026?",
            document_id="test-document",
        )

    assert results[0]["page"] == 40
    assert "15.8%" in results[0]["text"]
