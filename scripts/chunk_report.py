
"""Convert a financial PDF into searchable text chunks."""

import argparse
import json
from pathlib import Path

from app.ingestion import extract_pdf
from app.chunking import chunk_report


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to the financial report",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1200,
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=200,
    )

    args = parser.parse_args()

    print("Extracting PDF...")
    report = extract_pdf(args.pdf)

    print("Cleaning and chunking...")
    chunks = chunk_report(
        report,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )

    output_dir = Path("data/processed")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = (
        output_dir / f'{report["sha256"][:12]}_chunks.json'
    )

    result = {
        "document_id": report["sha256"],
        "filename": report["filename"],
        "page_count": report["page_count"],
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }

    output_file.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nDocument: {report['filename']}")
    print(f"Pages: {report['page_count']}")
    print(f"Chunks created: {len(chunks)}")
    print(f"Saved: {output_file}")

    print("\nFirst three chunks:")

    for chunk in chunks[:3]:
        print("\n" + "-" * 60)
        print(f"Page: {chunk['page_number']}")
        print(f"Chunk: {chunk['chunk_index']}")
        print(f"Characters: {len(chunk['text'])}")
        print(chunk["text"][:300])


if __name__ == "__main__":
    main()
