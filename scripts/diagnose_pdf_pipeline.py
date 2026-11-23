"""Trace one PDF through extraction -> table reading -> overview -> index.

Usage (project root, venv active):
    python -m scripts.diagnose_pdf_pipeline "Danske.pdf" [--metric net_profit] [--period Q326] [--document-id ID]

It prints the first stage where the information disappears. Read-only: it does
not write to the index or the registry.
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--metric", default="net_profit")
    ap.add_argument("--period", help="e.g. Q326 (default: Q3 of the year found in the report)")
    ap.add_argument("--document-id", help="indexed document id, to compare the index with the PDF")
    a = ap.parse_args()

    from app import document_ingest, layout_table_reader as L, document_tables
    from app.financial_vocabulary import BY_KEY
    pages = document_ingest._extract_pages(Path(a.pdf))
    print(f"[1] EXTRACTION: {len(pages)} pages, {sum(bool(p.strip()) for p in pages)} with text")
    if not any(p.strip() for p in pages):
        print("    FAIL: no text layer (scanned PDF) -> OCR needed"); return 1
    head = " ".join(pages[:3]).replace("–", "-")
    stated = ("Q3 (first nine months / Q1-Q3)" if re.search(r"first nine months|nine months|Q1\s*-\s*Q3|9M", head, re.I)
              else "Q2 (first half)" if re.search(r"first half|H1|Q1\s*-\s*Q2", head, re.I)
              else "Q1" if re.search(r"first quarter|\bQ1\b", head, re.I) else "unclear")
    print(f"[0] PERIOD THE PDF STATES: {stated}")
    try:
        from app.document_registry import list_documents
        mine = [d for d in list_documents() if d.filename == Path(a.pdf).name or a.document_id == d.document_id]
        for d in mine:
            print(f"    REGISTRY: id={d.document_id[:12]} issuer={d.issuer!r} year={d.report_year} "
                  f"quarter={d.report_quarter} currency={d.currency} type={d.report_type} chunks={d.chunk_count}")
            if stated.startswith("Q3") and d.report_quarter != 3:
                print("    FAIL: the PDF is a nine-month (Q3) report but it was indexed with "
                      f"quarter={d.report_quarter}. 'Q3' questions cannot select it. "
                      "Delete it (Manage indexed reports) and re-upload choosing Quarter = Q3.")
        if not mine:
            print("    (no registry entry with this filename; pass --document-id if it was renamed)")
    except Exception as e:  # noqa: BLE001
        print(f"    could not read the registry: {e}")
    year = a.period[-2:] if a.period else None
    if not year:
        m = re.search(r"\b(20\d{2})\b", " ".join(pages[:3]))
        year = m.group(1)[-2:] if m else "26"
    period = a.period or f"Q3{year}"
    labels = [*BY_KEY[a.metric].rows, *document_tables._EXTRA_ROWS.get(a.metric, ())]
    print(f"    metric={a.metric} period={period} row labels={labels}")

    hits = [(i + 1, p) for i, p in enumerate(pages)
            if any(re.search(r"^\s*" + re.escape(l), p, re.I | re.M) for l in labels)]
    print(f"[2] PAGES WITH A ROW LABEL: {[n for n, _ in hits]}")
    if not hits:
        print("    FAIL: the label never starts a line. Wording differs from the vocabulary, "
              "or label and values are on separate lines. Showing 'profit' lines:")
        for n, p in enumerate(pages, 1):
            for ln in p.splitlines():
                if re.search(r"\bprofit\b", ln, re.I) and len(ln) < 160:
                    print(f"      p{n}: {ln!r}")
                    break
        return 2
    for n, p in hits[:3]:
        print(f"    --- page {n} raw lines around the row (repr) ---")
        ls = p.splitlines()
        for j, ln in enumerate(ls):
            if any(re.match(r"\s*" + re.escape(l), ln, re.I) for l in labels):
                for k in range(max(0, j - 8), min(len(ls), j + 2)):
                    print(f"      {ls[k]!r}")
                break

    pg = [{"document": Path(a.pdf).name, "page": i + 1, "text": t} for i, t in enumerate(pages)]
    print("[3] TABLE READER")
    for n, p in hits[:3]:
        t = pg[n - 1]["text"]
        kind = L._table_kind(t)
        heads = L._headers(t.splitlines(), kind or "income")
        print(f"    page {n}: table kind={kind!r}; headers found={[h.periods for h in heads]}; "
              f"units={[h.unit for h in heads]}")
    cell = L.aligned_group_cell(a.metric, period, pg)
    print(f"    aligned reader -> {cell}")
    document_tables._get_pages = lambda _id: pg
    full = document_tables.generic_cell(a.metric, period, "diag")
    print(f"    generic_cell   -> {full}")
    if full is None:
        print("    FAIL at table reading (see kind/headers above: kind None = title not recognised; "
              "no headers = column labels not alignable; headers but no cell = row label/period/values).")
    print("[4] OVERVIEW")
    from app.company_overview import PageEvidence, build_company_overview
    r = build_company_overview("Issuer", "report", "diag", None, None,
                               [PageEvidence(page=i + 1, text=t) for i, t in enumerate(pages)])
    print(f"    supported={r['supported']} segments={r.get('business_segments')}")
    if a.document_id:
        print("[5] INDEX vs PDF")
        try:
            from app.vector_store import get_collection
            rec = get_collection().get(where={"document_id": a.document_id}, include=["documents", "metadatas"])
            print(f"    chunks in index: {len(rec['documents'])}")
            idx = {}
            for c, m in zip(rec["documents"], rec["metadatas"]):
                idx.setdefault(int(m["page_number"]), []).append((int(m.get("chunk_index", 0)), c))
            for n, _ in hits[:3]:
                txt = "\n".join(c for _, c in sorted(idx.get(n, [])))
                same = re.sub(r"\s+", "", txt) == re.sub(r"\s+", "", pages[n - 1])
                print(f"    page {n}: indexed text matches PDF text: {same}; "
                      f"multi-space preserved: {'  ' in txt}")
        except Exception as e:  # noqa: BLE001
            print(f"    could not read the index: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
