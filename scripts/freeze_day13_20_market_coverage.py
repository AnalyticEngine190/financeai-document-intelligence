"""Create/check a Day 13.20 freeze over the installed project snapshot.

The existing MANIFEST_DAY13_19.sha256 is retained as a lineage record. Some
projects may also have later, separately applied fixes to financial modules;
this freeze captures their current hashes rather than trying to roll them back
or incorrectly comparing them to the older Day 13.19 source hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LINEAGE_MANIFEST = ROOT / "MANIFEST_DAY13_19.sha256"
OUT = ROOT / "reports/evaluation/day13_20_market_coverage_freeze"
MANIFEST = OUT / "manifest.json"
PATCH_FILES = {
    ".env.market.example", "app/market_entitlements.py", "app/market_data.py",
    "app/market_routes.py", "app/market_chat.py", "streamlit_market_app.py",
    "tests/test_day13_20_market_coverage.py", "scripts/diagnose_market_coverage.py",
    "scripts/freeze_day13_20_market_coverage.py", "README_DAY13_20_MARKET_COVERAGE.md",
}
GUARD_FILES = {
    "app/api_day13_6.py", "app/financial_vocabulary.py", "app/document_answering.py",
    "tests/test_day13_19_clarification_slots_and_private_account_boundary.py",
    "README_DAY13_19_CLARIFICATION_CONTEXT_PRIVATE_ACCOUNT_FIX.md",
}
TRACKED = PATCH_FILES | GUARD_FILES


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def state() -> dict:
    if not LINEAGE_MANIFEST.is_file():
        raise RuntimeError("MANIFEST_DAY13_19.sha256 is missing; this patch expects the Day 13.19-or-later project baseline")
    files = {}
    for relative in sorted(TRACKED | {"MANIFEST_DAY13_19.sha256"}):
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError("Missing required project file: " + relative)
        files[relative] = digest(path)
    return {
        "version": "day13.20-basic-coverage-nordea-identity-v1",
        "lineage_manifest_sha256": digest(LINEAGE_MANIFEST),
        "files": files,
        "patch_files": sorted(PATCH_FILES),
        "guard_files": sorted(GUARD_FILES),
        "service": {
            "fastapi_entrypoint": "app.api_day13_6:app",
            "chat_route": "/chat_v1",
            "market_routes": ["/market/status", "/market/search", "/market/quote", "/market/history", "/market/compare"],
            "plan_configuration": "TWELVE_DATA_PLAN=basic by default; not auto-detected from API key",
            "otc_default": "blocked; separate licence required",
        },
    }


def freeze() -> None:
    if OUT.exists():
        raise RuntimeError("Day 13.20 freeze already exists; refusing to overwrite")
    data = state()
    OUT.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(OUT / "code_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(data["files"]):
            archive.write(ROOT / relative, relative)
    data["created_utc"] = datetime.now(timezone.utc).isoformat()
    data["snapshot_sha256"] = digest(OUT / "code_snapshot.zip")
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"DAY 13.20 FROZEN: {len(data['files'])} files; {len(PATCH_FILES)} patch files; baseline guards captured")


def check() -> None:
    if not MANIFEST.is_file():
        raise RuntimeError("Day 13.20 freeze missing; run --freeze once")
    previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current = state()
    for key, value in current.items():
        if previous.get(key) != value:
            raise RuntimeError("Day 13.20 freeze violation: " + key)
    snapshot = OUT / "code_snapshot.zip"
    if not snapshot.is_file() or digest(snapshot) != previous.get("snapshot_sha256"):
        raise RuntimeError("Day 13.20 frozen code snapshot changed")
    print(f"DAY 13.20 FREEZE CHECK PASSED: {len(current['files'])} files; lineage manifest and guard files unchanged")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--freeze", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        freeze() if args.freeze else check()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
