
"""Index a processed report and search it interactively."""

import argparse
import json
from pathlib import Path

from app.vector_store import index_chunks, search_chunks


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "json_file",
        type=Path,
        help="Processed chunks JSON file",
    )

    args = parser.parse_args()

    data = json.loads(
        args.json_file.read_text(encoding="utf-8")
    )

    print(f"Indexing {len(data['chunks'])} chunks...")

    count = index_chunks(data["chunks"])

    print(f"Indexed {count} chunks.")
    print("Semantic search ready.")
    print("Type 'exit' to stop.\n")

    while True:
        question = input("Question: ").strip()

        if question.lower() in {"exit", "quit"}:
            break

        if not question:
            continue

        results = search_chunks(
            question,
            top_k=5,
            document_id=data["document_id"],
        )

        if not results:
            print("No results found.")
            continue

        for rank, match in enumerate(results, 1):
            print(f"\n--- Result {rank} ---")
            print(f"Document: {match['document']}")
            print(f"PDF page: {match['page']}")
            print(f"Distance: {match['distance']:.4f}")
            print(match["text"][:600])
            print()


if __name__ == "__main__":
    main()
