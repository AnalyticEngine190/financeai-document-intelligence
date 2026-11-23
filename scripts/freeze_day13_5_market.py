"""Append-only Market Intelligence freeze; respects original Day 11.3 evidence.

Unlike freeze_day11_3 --check, this accepts ONLY new additive files, requiring
all 123 previously frozen source/config files to remain byte-for-byte identical.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

from scripts.day11_12_common import ROOT, SEALED, API_URL, digest, load, doc_fingerprint, write_once
from scripts.freeze_day11_12 import source_files, packages, runtime

OLD = ROOT / "reports/evaluation/day11_3_freeze/manifest.json"
OLD_RUNS = ROOT / "reports/evaluation/day11_3_regressions"
OUT = ROOT / "reports/evaluation/day13_5_market_freeze"
MANIFEST = OUT / "manifest.json"
REQUIRED = {
    "app/market_data.py", "app/market_routes.py", "app/api_day13_5.py",
    "streamlit_market_app.py", "tests/test_day13_5_market.py", "tests/test_day13_5_freeze.py",
    "scripts/freeze_day13_5_market.py", "scripts/market_smoke.py",
    "README_DAY13_5_MARKET.md", ".env.market.example",
}


def _previous() -> tuple[dict, dict]:
    if not OLD.is_file():
        raise RuntimeError("Missing preserved Day 11.3 freeze")
    saved = load(OLD)
    current = source_files()
    for filename, sha in saved["files"].items():
        if current.get(filename) != sha:
            raise RuntimeError("Previously frozen file changed or disappeared: " + filename)
    if len(saved["files"]) < 123:
        raise RuntimeError("Unexpected Day 11.3 freeze inventory")
    if digest(SEALED) != saved["gold_sha256"]:
        raise RuntimeError("Sealed Day 11 gold differs from the preserved freeze")
    prior_docs = doc_fingerprint(API_URL)
    if prior_docs != saved["documents"]:
        raise RuntimeError("Four-report indexed document registry changed")
    previous_paths = [OLD, OLD.parent / "code_snapshot.zip", OLD.parent / "pip_freeze.txt"]
    if digest(previous_paths[1]) != saved["snapshot_sha256"]:
        raise RuntimeError("Previous frozen code snapshot modified")
    if digest(previous_paths[2]) != saved["pip_freeze_sha256"]:
        raise RuntimeError("Previous frozen dependency snapshot modified")
    pinned = {str(p.relative_to(ROOT)): digest(p) for p in previous_paths}
    return saved, pinned


def _regression() -> dict[str, str]:
    """Pin the real 46-pass / four-review run, not a fabricated score."""
    if not OLD_RUNS.is_dir():
        raise RuntimeError("Day 11.3 development regression archive is missing")
    for folder in sorted(OLD_RUNS.iterdir(), reverse=True):
        paths = [folder / name for name in ("results.jsonl", "summary.json", "run_metadata.json")]
        if not all(p.is_file() for p in paths):
            continue
        try:
            summary, meta = load(paths[1]), load(paths[2])
            if summary.get("counts") != {"PASS": 46, "REVIEW": 4}:
                continue
            if meta.get("gold_sha256") != digest(SEALED) or meta.get("freeze_sha256") != digest(OLD):
                continue
            if len(paths[0].read_text(encoding="utf-8").splitlines()) != 50:
                continue
        except (OSError, ValueError, TypeError, KeyError):
            continue
        return {str(p.relative_to(ROOT)): digest(p) for p in paths}
    raise RuntimeError("Missing unchanged archived full Day 11.3 46 PASS / 4 REVIEW run")


def state() -> dict:
    previous, pinned = _previous()
    file_hashes = source_files()
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            raise RuntimeError("Missing market feature file: " + relative)
        if relative not in file_hashes:
            file_hashes[relative] = digest(ROOT / relative)
    return {
        "files": file_hashes,
        "documents": previous["documents"],
        "gold_sha256": digest(SEALED),
        "previous_day11_3": pinned,
        "development_regression": _regression(),
        "runtime": runtime(),
        "market": {"provider": "Twelve Data", "routes": ["/market/search", "/market/quote", "/market/history", "/market/compare", "/ask_v3"],
                   "note": "API keys and fetched quotes are intentionally not frozen; no credentials saved."},
    }


def freeze() -> None:
    if OUT.exists():
        raise RuntimeError("Day 13.5 freeze already exists; refusing to overwrite")
    data = state()
    OUT.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(OUT / "code_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(data["files"]):
            archive.write(ROOT / rel, rel)
    (OUT / "pip_freeze.txt").write_text(packages(), encoding="utf-8")
    data.update({"version": "day13.5-market-intelligence-v1",
                 "created_utc": datetime.now(timezone.utc).isoformat(),
                 "snapshot_sha256": digest(OUT / "code_snapshot.zip"),
                 "pip_freeze_sha256": digest(OUT / "pip_freeze.txt")})
    write_once(MANIFEST, data)
    print(f"DAY 13.5 MARKET FROZEN: {len(data['files'])} files; {len(data['documents'])} reports")


def check() -> None:
    if not MANIFEST.is_file():
        raise RuntimeError("Day 13.5 market freeze missing; run --freeze first")
    previous = load(MANIFEST)
    current = state()
    for k, v in current.items():
        if previous.get(k) != v:
            raise RuntimeError("Day 13.5 freeze violation: " + k)
    if digest(OUT / "code_snapshot.zip") != previous["snapshot_sha256"]:
        raise RuntimeError("Day 13.5 frozen code snapshot changed")
    if digest(OUT / "pip_freeze.txt") != previous["pip_freeze_sha256"]:
        raise RuntimeError("Day 13.5 frozen dependencies changed")
    if packages().splitlines() != (OUT / "pip_freeze.txt").read_text(encoding="utf-8").splitlines():
        raise RuntimeError("Installed Python dependencies changed")
    print(f"DAY 13.5 MARKET FREEZE CHECK PASSED: {len(current['files'])} files; {len(current['documents'])} reports")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--freeze", action="store_true")
    g.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        freeze() if args.freeze else check()
    except (RuntimeError, OSError, KeyError, ValueError) as error:
        parser.exit(2, "ERROR: " + str(error) + "\n")


if __name__ == "__main__":
    main()
