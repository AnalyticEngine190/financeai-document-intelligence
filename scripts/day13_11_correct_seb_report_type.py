"""Safely correct the existing SEB Q3 2026 registry label.

The supplied source PDF is titled 'SEB quarterly report – Third quarter 2026'.
This script changes only the registry record matching that PDF's SHA-256 ID and
only when its current type is the known incorrect value 'factbook'. It does not
re-index documents or modify Chroma content.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

from app.document_registry import _read_manifest, registry_path, save_document

SEB_Q3_2026_SHA256 = "38a50672dc55958566a46c7bb6a64250b04b7514d6b6a3186ff8f3471ae2aeac"


def main() -> int:
    path = registry_path()
    records = _read_manifest()
    record = records.get(SEB_Q3_2026_SHA256)
    if record is None:
        print("No matching SEB Q3 2026 record was found in the document registry; no changes made.")
        return 2

    if (record.issuer.casefold() != "seb" or record.report_year != 2026
            or record.report_quarter != 3 or "seb" not in record.filename.casefold()):
        print("The matching document ID has unexpected metadata; no changes made.")
        return 3

    if record.report_type == "quarterly_report":
        print("SEB Q3 2026 is already labelled quarterly_report; no changes needed.")
        return 0
    if record.report_type != "factbook":
        print(f"Expected the known incorrect type 'factbook', found {record.report_type!r}; no changes made.")
        return 4

    backup = Path(str(path) + ".day13_11.bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
        print(f"Registry backup created: {backup}")

    save_document(replace(record, report_type="quarterly_report"))
    updated = _read_manifest().get(SEB_Q3_2026_SHA256)
    if updated is None or updated.report_type != "quarterly_report":
        raise RuntimeError("Registry update did not verify after writing")
    print("Updated only SEB Q3 2026: factbook -> quarterly_report.")
    print("No PDF content was changed and no re-indexing was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
