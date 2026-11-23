
"""Interactive financial report question answering."""

import argparse
import json
from pathlib import Path

from app.answering import answer_question


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "chunks_json",
        type=Path,
        help="Processed document chunks JSON",
    )

    args = parser.parse_args()

    data = json.loads(
        args.chunks_json.read_text(encoding="utf-8")
    )

    document_id = data["document_id"]

    print("=" * 60)
    print("FINANCIAL DOCUMENT INTELLIGENCE ASSISTANT")
    print("=" * 60)
    print("Document:", data["filename"])
    print("Model: qwen3:4b")
    print("Retrieval: Hybrid search")
    print("Type 'exit' to quit.")

    while True:
        try:
            question = input("\nQuestion: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if question.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        if not question:
            continue

        try:
            result = answer_question(
                question,
                document_id=document_id,
            )
        except RuntimeError as exc:
            print("\nError:", exc)
            continue

        print("\nANSWER")
        print(result["answer"])

        if result["sources"]:
            print("\nSOURCES")
            for source in result["sources"]:
                print(
                    f"- {source['document']}, "
                    f"PDF page {source['page']}"
                )


if __name__ == "__main__":
    main()
