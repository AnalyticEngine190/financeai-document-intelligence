#!/usr/bin/env python3
"""Read-only diagnosis for Day 10.5's two SEB narrative questions.

Run from the Finance AI Building project root. This tool changes neither source
files nor ChromaDB data and does not update any benchmark results.

Examples:
    python "C:\\Users\\serha\\Desktop\\narrative_probe_day10_5.py"
    python "C:\\Users\\serha\\Desktop\\narrative_probe_day10_5.py" --case 12 --model
    python "C:\\Users\\serha\\Desktop\\narrative_probe_day10_5.py" --case 12 --model --force-page7
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path.cwd()))

QUESTIONS = {
    11: "According to SEB's third-quarter report, what factors did it cite for the quarter-on-quarter rise in net interest income?",
    12: "What accounting presentation change relating to interest accruals on derivatives did SEB introduce in January 2026?",
}
SEB_Q3_DOCUMENT_ID = "38a50672dc55958566a46c7bb6a64250b04b7514d6b6a3186ff8f3471ae2aeac"


def _short(text: object, limit: int = 145) -> str:
    return " ".join(str(text).split())[:limit]


def inspect(case: int, document_id: str, use_model: bool, force_page7: bool) -> None:
    from app.hybrid_search import hybrid_search
    from app.table_lookup import _get_pages
    import app.narrative_answering as narrative

    question = QUESTIONS[case]
    pages = _get_pages(document_id)
    page_by_key = {(p["document"], int(p["page"])): p for p in pages}
    matching_page7 = [p for p in pages if int(p["page"]) == 7]

    print(f"\n=== NEW_BANK_{case} ===")
    print("Question:", question)
    print(f"Index contains {len(pages)} text pages for document_id={document_id}")
    if matching_page7:
        p7 = matching_page7[0]
        print("Physical PDF page 7 indexed: YES")
        print("Page 7 includes 'derivatives':", "derivatives" in p7["text"].lower())
        print("Page 7 includes 'net interest income':", "net interest income" in p7["text"].lower())
    else:
        print("Physical PDF page 7 indexed: NO — verify indexing and document ID")

    ranked = hybrid_search(question, top_k=10, document_id=document_id)
    accepted = []
    seen = set()
    print("\nHybrid-search results (selected-index pages only):")
    for i, item in enumerate(ranked, 1):
        try:
            key = (item.get("document"), int(item.get("page", -1)))
        except (TypeError, ValueError):
            print(f"  {i:2d}: malformed page information")
            continue
        valid = key in page_by_key
        print(f"  {i:2d}: page={key[1]:2d}, indexed={valid}, snippet={_short(item.get('text', item.get('chunk', '')))}")
        if valid and key not in seen:
            seen.add(key)
            accepted.append(page_by_key[key])
    chosen = accepted[:4]
    print("\nPages actually supplied to Qwen:", [int(p["page"]) for p in chosen])
    print("Page 7 supplied:", any(int(p["page"]) == 7 for p in chosen))
    if force_page7 and matching_page7 and not any(int(p["page"]) == 7 for p in chosen):
        chosen = [matching_page7[0]] + chosen[:3]
        print("DIAGNOSTIC ONLY: forced page 7 into the evidence; new pages:", [int(p["page"]) for p in chosen])
    if not use_model:
        print("\nAdd --model to inspect Qwen's proposed answer and each evidence-validation gate.")
        print("Add --force-page7 with --model only to distinguish retrieval from generation failures.")
        return
    if not chosen:
        print("No eligible pages; Qwen was not called.")
        return

    evidence = "\n\n".join(
        f"[{i}] Source: {p['document']} | PDF page {p['page']}\n{p['text'][:6500]}"
        for i, p in enumerate(chosen, 1)
    )
    result = narrative._call_narrative_model(question, evidence)
    print("\nQwen output (parsed):")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:6500])

    reasons = []
    if result.get("supported") is not True:
        reasons.append("supported != true (could be model abstention or request/JSON failure; exceptions are swallowed by current wrapper)")
    answer = result.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        reasons.append("missing/non-string/empty answer")
    ids = result.get("citation_ids")
    quotes = result.get("evidence_quotes")
    if not isinstance(ids, list) or not ids:
        reasons.append("missing or empty citation_ids")
    elif any(type(i) is not int or not 1 <= i <= len(chosen) for i in ids):
        reasons.append("invalid citation_ids; IDs must be 1-based and index supplied passages")
    if not isinstance(quotes, list) or not quotes:
        reasons.append("missing or empty evidence_quotes")

    if isinstance(ids, list) and ids and all(type(i) is int and 1 <= i <= len(chosen) for i in ids):
        cited = [chosen[i - 1] for i in dict.fromkeys(ids)]
        if isinstance(quotes, list) and quotes:
            for quote in quotes:
                if not isinstance(quote, str) or len(quote.strip()) < 12:
                    reasons.append("invalid evidence quote: must be string of at least 12 characters")
                elif not any(narrative._normalize(quote) in narrative._normalize(p["text"]) for p in cited):
                    reasons.append("quote not found verbatim (whitespace/case normalized) within the CITED page(s): " + _short(quote, 90))
        if isinstance(answer, str):
            answer_nums = set(re.findall(r"(?<!\w)\d[\d,.]*%?", answer))
            cited_nums = set(re.findall(r"(?<!\w)\d[\d,.]*%?", " ".join(p["text"] for p in cited)))
            if not answer_nums.issubset(cited_nums):
                reasons.append(f"numerical tokens absent from cited source: {sorted(answer_nums-cited_nums)}")
    print("\nVerification diagnosis:")
    if reasons:
        for reason in reasons:
            print("  BLOCKED:", reason)
    else:
        print("  PASSED structural checks. Human review is still required to confirm the explanation.")
    print("NOTE: This is a diagnostic replay, not the original preserved response or a new benchmark score.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=int, choices=[11, 12], help="Run one case; default: inspect both")
    parser.add_argument("--model", action="store_true", help="Call local Ollama and inspect the model-verification gates")
    parser.add_argument("--force-page7", action="store_true", help="For diagnosis only, include source page 7 among evidence pages")
    parser.add_argument("--document-id", default=SEB_Q3_DOCUMENT_ID, help="SHA-256 identifier of indexed SEB report")
    args = parser.parse_args()
    for case in [args.case] if args.case else [11, 12]:
        inspect(case, args.document_id, args.model, args.force_page7)


if __name__ == "__main__":
    main()
