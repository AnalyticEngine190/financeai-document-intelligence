
"""Manually evaluate semantic and hybrid retrieval."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.vector_store import search_chunks
from app.hybrid_search import hybrid_search


QUESTIONS = [
    "What is Nordea's capital position?",
    "What is Nordea's CET1 capital ratio?",
    "What is the liquidity coverage ratio?",
    "What are the main sources of credit risk?",
]


def get_rank():
    """Ask the evaluator for the first supporting result."""
    while True:
        value = input(
            "First supporting result (1-5), "
            "or 0 if none: "
        ).strip()

        if value in {"0", "1", "2", "3", "4", "5"}:
            return int(value)

        print("Please enter a number from 0 to 5.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("chunks_json", type=Path)
    args = parser.parse_args()

    data = json.loads(
        args.chunks_json.read_text(encoding="utf-8")
    )
    document_id = data["document_id"]

    records = []

    for question in QUESTIONS:
        print("\n" + "=" * 70)
        print("QUESTION:", question)

        methods = {
            "semantic": search_chunks(
                question,
                top_k=5,
                document_id=document_id,
            ),
            "hybrid": hybrid_search(
                question,
                top_k=5,
                document_id=document_id,
            ),
        }

        for method, results in methods.items():
            print(f"\n{method.upper()} SEARCH")

            for rank, result in enumerate(results, 1):
                print(f"\n[{rank}] PDF page {result['page']}")
                print(result["text"])
                print("-" * 40)

            print(
                "\nCheck the original PDF before judging. "
                "Related keywords alone are not sufficient."
            )

            first_rank = get_rank()

            records.append({
                "question": question,
                "method": method,
                "first_supporting_rank": first_rank,
                "hit_at_5": int(first_rank > 0),
                "reciprocal_rank": (
                    1 / first_rank if first_rank else 0
                ),
                "pages": [
                    item["page"] for item in results
                ],
            })

    print("\n" + "=" * 70)
    print("EVALUATION SUMMARY")

    for method in ("semantic", "hybrid"):
        rows = [
            row for row in records
            if row["method"] == method
        ]

        hit_rate = sum(
            row["hit_at_5"] for row in rows
        ) / len(rows)

        mrr = sum(
            row["reciprocal_rank"] for row in rows
        ) / len(rows)

        print(
            f"{method}: "
            f"Hit@5={hit_rate:.1%}, "
            f"MRR@5={mrr:.3f}"
        )

    output = Path("evaluation/retrieval_baseline.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat(),
                "document_id": document_id,
                "evaluation_type": "manual exploratory",
                "records": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nResults saved to {output}")


if __name__ == "__main__":
    main()
