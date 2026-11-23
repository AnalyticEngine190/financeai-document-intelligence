"""Offline report-selection diagnostic for broad company-overview questions.

Does not call Ollama, Chroma retrieval, Twelve Data or any network endpoint.
"""
from __future__ import annotations
import argparse

from app import market_chat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="Tell me about Nordea")
    args = parser.parse_args()
    print(f"Question: {args.question}")
    print(f"Intent: {market_chat._intent(args.question)}")
    if market_chat._intent(args.question) != "overview":
        print("This wording is not routed to company overview.")
        return 2
    try:
        doc, error = market_chat._overview_document(
            args.question, market_chat.legacy.DocumentSelectionRequest(mode="auto", document_ids=[])
        )
    except RuntimeError as exc:
        print(f"REGISTRY ERROR: {type(exc).__name__}")
        return 3
    if error:
        print(f"SELECTION NEEDS CLARIFICATION: {error}")
        return 1
    print("SELECTED REPORT:")
    print(f"  issuer: {doc.issuer}")
    print(f"  edition: Q{doc.report_quarter} {doc.report_year}")
    print(f"  display_name: {doc.display_name}")
    print(f"  document_id: {doc.document_id}")
    print("No LLM, PDF retrieval or market API call was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
