"""Versioned chat UI freeze; preserves Day 11.3 and optional Day 13.5 evidence.

This is a new version. An earlier Day 13.5 freeze, if present, must remain
unchanged, but its previous Streamlit UI is intentionally replaced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import zipfile

from scripts.day11_12_common import ROOT, SEALED, API_URL, digest, load, doc_fingerprint, write_once
from scripts.freeze_day11_12 import source_files, packages, runtime

PREVIOUS_FINANCE = ROOT / "reports/evaluation/day11_3_freeze/manifest.json"
PREVIOUS_MARKET = ROOT / "reports/evaluation/day13_5_market_freeze/manifest.json"
OUT = ROOT / "reports/evaluation/day13_6_chat_freeze"
MANIFEST = OUT / "manifest.json"
ALLOWED_REPLACEMENT = {"streamlit_market_app.py"}
REQUIRED = {
    "app/market_chat.py", "app/api_day13_6.py", "streamlit_market_app.py",
    "tests/test_day13_6_chat.py", "scripts/chat_smoke.py",
    "scripts/freeze_day13_6_chat.py", "README_DAY13_6_CHAT.md",
}


def verify_predecessors() -> dict:
    previous = {}
    present = source_files()
    if not PREVIOUS_FINANCE.exists():
        raise RuntimeError("Day 11.3 freeze manifest missing; do not recreate it")
    fin = load(PREVIOUS_FINANCE)
    for name, expected in fin.get("files", {}).items():
        if present.get(name) != expected:
            raise RuntimeError("Day 11.3 frozen source changed: " + name)
    if digest(SEALED) != fin["gold_sha256"]:
        raise RuntimeError("Original sealed Day 11 benchmark changed")
    if doc_fingerprint(API_URL) != fin["documents"]:
        raise RuntimeError("Indexed report registry differs from frozen Day 11.3")
    for artifact in (PREVIOUS_FINANCE, PREVIOUS_FINANCE.parent / "code_snapshot.zip", PREVIOUS_FINANCE.parent / "pip_freeze.txt"):
        if not artifact.is_file():
            raise RuntimeError("Missing preserved Day 11.3 artifact: " + str(artifact))
        previous[str(artifact.relative_to(ROOT))] = digest(artifact)
    if digest(PREVIOUS_FINANCE.parent / "code_snapshot.zip") != fin.get("snapshot_sha256"):
        raise RuntimeError("Day 11.3 code snapshot changed")
    if digest(PREVIOUS_FINANCE.parent / "pip_freeze.txt") != fin.get("pip_freeze_sha256"):
        raise RuntimeError("Day 11.3 dependency snapshot changed")
    if PREVIOUS_MARKET.exists():
        market = load(PREVIOUS_MARKET)
        for name, expected in market.get("files", {}).items():
            if name in ALLOWED_REPLACEMENT:
                continue
            previous_file = ROOT / name
            # Day 13.5 also pinned .env.market.example and README explicitly;
            # source_files() only inventories .py, requirements and benchmark JSON.
            actual = present.get(name)
            if actual is None and previous_file.is_file():
                actual = digest(previous_file)
            if actual != expected:
                raise RuntimeError("Prior market source changed unexpectedly: " + name)
        for artifact in (PREVIOUS_MARKET, PREVIOUS_MARKET.parent / "code_snapshot.zip", PREVIOUS_MARKET.parent / "pip_freeze.txt"):
            if not artifact.is_file():
                raise RuntimeError("Missing prior market artifact: " + str(artifact))
            previous[str(artifact.relative_to(ROOT))] = digest(artifact)
        if digest(PREVIOUS_MARKET.parent / "code_snapshot.zip") != market.get("snapshot_sha256"):
            raise RuntimeError("Day 13.5 snapshot changed")
        if digest(PREVIOUS_MARKET.parent / "pip_freeze.txt") != market.get("pip_freeze_sha256"):
            raise RuntimeError("Day 13.5 dependency snapshot changed")
    return previous


def state() -> dict:
    pinned = verify_predecessors()
    hashes = source_files()
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            raise RuntimeError("Missing new chat feature file: " + rel)
        hashes[rel] = digest(ROOT / rel)
    return {
        "files": hashes,
        "documents": doc_fingerprint(API_URL),
        "gold_sha256": digest(SEALED),
        "archived_freezes": pinned,
        "runtime": runtime(),
        "service": {"entrypoint": "app.api_day13_6:app", "chat_route": "/chat_v1", "ui": "streamlit_market_app.py", "provider_credentials_frozen": False},
    }


def freeze() -> None:
    if OUT.exists():
        raise RuntimeError("Day 13.6 freeze already exists; refusing to overwrite")
    data = state()
    OUT.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(OUT / "code_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(data["files"]):
            archive.write(ROOT / filename, filename)
    (OUT / "pip_freeze.txt").write_text(packages(), encoding="utf-8")
    data.update({"version": "day13.6-chat-first-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
                 "snapshot_sha256": digest(OUT / "code_snapshot.zip"),
                 "pip_freeze_sha256": digest(OUT / "pip_freeze.txt")})
    write_once(MANIFEST, data)
    print(f"DAY 13.6 CHAT FROZEN: {len(data['files'])} files, {len(data['documents'])} reports")


def check() -> None:
    if not MANIFEST.is_file():
        raise RuntimeError("No Day 13.6 freeze; run --freeze once")
    previous = load(MANIFEST)
    current = state()
    for key, val in current.items():
        if previous.get(key) != val:
            raise RuntimeError("Day 13.6 freeze violation: " + key)
    if digest(OUT / "code_snapshot.zip") != previous.get("snapshot_sha256"):
        raise RuntimeError("Day 13.6 code snapshot changed")
    if digest(OUT / "pip_freeze.txt") != previous.get("pip_freeze_sha256"):
        raise RuntimeError("Day 13.6 dependency snapshot changed")
    if packages().splitlines() != (OUT / "pip_freeze.txt").read_text(encoding="utf-8").splitlines():
        raise RuntimeError("Installed Python packages changed")
    print(f"DAY 13.6 CHAT FREEZE CHECK PASSED: {len(current['files'])} files, {len(current['documents'])} reports")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        freeze() if args.freeze else check()
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        parser.exit(2, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
