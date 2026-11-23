
"""Compare existing semantic search with hybrid retrieval."""

from app.vector_store import search_chunks
from app.hybrid_search import hybrid_search


def main():
    questions = [
        "What is Nordea's capital position?",
        "What is Nordea's CET1 capital ratio?",
        "What is the liquidity coverage ratio?",
        "What are the main sources of credit risk?",
    ]

    for question in questions:
        print("\n" + "=" * 65)
        print("QUESTION:", question)

        semantic = search_chunks(question, top_k=5)
        hybrid = hybrid_search(question, top_k=5)

        print("\nSEMANTIC SEARCH")
        for i, result in enumerate(semantic, 1):
            print(f"{i}. PDF page {result['page']}")

        print("\nHYBRID SEARCH")
        for i, result in enumerate(hybrid, 1):
            print(f"{i}. PDF page {result['page']}")
            print(result["text"][:180].replace("\n", " "))


if __name__ == "__main__":
    main()
