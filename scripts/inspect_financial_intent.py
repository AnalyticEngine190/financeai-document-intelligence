"""Inspect financial-question interpretation without requiring an API server.

Examples:
 python -m scripts.inspect_financial_intent --question "..."
 python -m scripts.inspect_financial_intent --question "..." --resolve
"""
import argparse
from dataclasses import asdict
import json

from app.question_parser import parse_question


def main():
    p = argparse.ArgumentParser(description="Inspect the source-grounded financial question route")
    p.add_argument("--question", required=True, help="Question to interpret")
    p.add_argument("--resolve", action="store_true", help="Read verified cells from the current Chroma index")
    args = p.parse_args()
    intent = parse_question(args.question)
    print("INTERPRETATION")
    print(json.dumps(asdict(intent), indent=2, ensure_ascii=False))
    if not args.resolve:
        return
    from app.api import _document_id
    from app.table_lookup import resolve_intent_cells, lookup_fact
    from app.financial_reasoning import reason_over_cells

    document_id = _document_id()
    if not intent.structured:
        fact, missing = lookup_fact(args.question, document_id)
        print("LEGACY LOOKUP:", fact)
        print("PERIOD UNAVAILABLE:", missing)
        return
    if intent.needs_clarification:
        print("CLARIFICATION REQUIRED: Financial measure is not specified")
        return
    if intent.uncertain:
        print("UNRESOLVED:", intent.uncertain)
        return
    cells = resolve_intent_cells(intent, document_id)
    if cells is None:
        print("UNRESOLVED: Missing or ambiguous source evidence")
        return
    print("VERIFIED CELLS")
    print(json.dumps([asdict(c) for c in cells], indent=2, default=str))
    result = reason_over_cells(intent, cells)
    print("ANSWER:", result.answer)
    print("CITATIONS:", list(result.sources))


if __name__ == "__main__":
    main()
