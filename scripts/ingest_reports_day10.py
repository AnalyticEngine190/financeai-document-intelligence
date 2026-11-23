"""Add or inspect PDF reports without altering your existing ingest script."""
import argparse
import json
from pathlib import Path

from app.document_ingest import ingest_pdf
from app.document_registry import list_documents


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 10 multi-PDF ingestion")
    parser.add_argument("--list", action="store_true", help="List currently indexed PDFs")
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--issuer", help="Nordea, SEB, etc.")
    parser.add_argument("--year", type=int)
    parser.add_argument("--quarter", type=int)
    parser.add_argument("--currency", help="Explicit currency e.g. EUR or SEK")
    parser.add_argument("--report-type", default="financial_report")
    parser.add_argument("--title", default="")
    args = parser.parse_args()

    if args.list:
        print(json.dumps([d.public_dict() for d in list_documents()], indent=2))
        return
    required = ("pdf", "issuer", "year", "quarter", "currency")
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error("Missing required options: " + ", ".join("--" + s for s in missing))
    result = ingest_pdf(args.pdf, issuer=args.issuer, year=args.year,
                        quarter=args.quarter, currency=args.currency,
                        report_type=args.report_type, title=args.title)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
