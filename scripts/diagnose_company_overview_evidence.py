"""Read-only inspection of the selected report's indexed business-area evidence.

Does not call the LLM, market provider, or modify the vector database.
"""
from __future__ import annotations
import argparse

from app import market_chat
from app.company_overview import load_indexed_pages, build_company_overview


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="Tell me about Nordea")
    args = parser.parse_args()
    selection = market_chat.legacy.DocumentSelectionRequest(mode="auto", document_ids=[])
    document, error = market_chat._overview_document(args.question, selection)
    if error:
        print("SELECTION ERROR:", error)
        return 2
    print("Selected report:", document.display_name)
    print("Document ID:", document.document_id)
    pages = load_indexed_pages(document.document_id)
    print("Indexed physical pages loaded:", len(pages))
    result = build_company_overview(
        document.issuer, document.display_name, document.document_id,
        document.report_year, document.report_quarter, pages,
    )
    print("Supported:", result.get("supported"))
    print("Business areas:", ", ".join(result.get("business_segments") or []) or "none detected")
    print("Evidence pages:", result.get("evidence_pages") or [])
    print("\nPROPOSED OVERVIEW\n")
    print(result.get("answer", ""))
    print("\nNo LLM, market API or external network request was made.")
    return 0 if result.get("supported") else 1


if __name__ == "__main__":
    raise SystemExit(main())
