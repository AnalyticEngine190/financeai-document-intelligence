"""Freeze the Day 13.9 company-overview routing version without replacing older freezes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import zipfile

from scripts.day11_12_common import ROOT, SEALED, API_URL, digest, load, doc_fingerprint, write_once
from scripts.freeze_day11_12 import source_files, packages, runtime
from scripts.freeze_day13_8_portfolio import verify_predecessors as verify_day13_8_predecessors

OUT = ROOT / "reports/evaluation/day13_9_company_overview_freeze"
MANIFEST = OUT / "manifest.json"
PREVIOUS = ROOT / "reports/evaluation/day13_8_portfolio_freeze/manifest.json"
ALLOWED_REPLACEMENT = {"app/market_chat.py", "streamlit_market_app.py"}
REQUIRED = {
    "app/market_chat.py", "app/api_day13_6.py", "streamlit_market_app.py",
    "tests/test_day13_6_chat.py", "tests/test_day13_8_about_and_disclosure.py",
    "tests/test_day13_9_company_overview.py", "tests/test_day13_6_ui_contract.py",
    "scripts/chat_smoke.py", "scripts/diagnose_company_overview.py",
    "scripts/freeze_day13_9_overview.py", "README_DAY13_9_COMPANY_OVERVIEW.md",
}


def verify_previous() -> dict:
    archived = verify_day13_8_predecessors()
    if PREVIOUS.exists():
        old = load(PREVIOUS)
        present = source_files()
        for name, expected in old.get("files", {}).items():
            if name in ALLOWED_REPLACEMENT:
                continue
            actual = present.get(name)
            file_path = ROOT / name
            if actual is None and file_path.is_file():
                actual = digest(file_path)
            if actual != expected:
                raise RuntimeError("Day 13.8 portfolio file changed unexpectedly: " + name)
        for artifact in (PREVIOUS, PREVIOUS.parent / "code_snapshot.zip", PREVIOUS.parent / "pip_freeze.txt"):
            if not artifact.is_file():
                raise RuntimeError("Missing preserved Day 13.8 artifact: " + str(artifact))
            archived[str(artifact.relative_to(ROOT))] = digest(artifact)
        if digest(PREVIOUS.parent / "code_snapshot.zip") != old.get("snapshot_sha256"):
            raise RuntimeError("Day 13.8 source snapshot changed")
        if digest(PREVIOUS.parent / "pip_freeze.txt") != old.get("pip_freeze_sha256"):
            raise RuntimeError("Day 13.8 dependency snapshot changed")
    return archived


def state() -> dict:
    archived = verify_previous()
    files = source_files()
    for relative in REQUIRED:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError("Missing Day 13.9 file: " + relative)
        files[relative] = digest(path)
    return {
        "files": files,
        "documents": doc_fingerprint(API_URL),
        "gold_sha256": digest(SEALED),
        "archived_freezes": archived,
        "runtime": runtime(),
        "service": {
            "entrypoint": "app.api_day13_6:app",
            "chat_route": "/chat_v1",
            "overview_intent": "registry_resolved_single_report",
            "ui": "streamlit_market_app.py",
            "provider_credentials_frozen": False,
        },
    }


def freeze() -> None:
    if OUT.exists():
        raise RuntimeError("Day 13.9 freeze already exists; refusing to overwrite")
    data = state()
    OUT.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(OUT / "code_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(data["files"]):
            archive.write(ROOT / filename, filename)
    (OUT / "pip_freeze.txt").write_text(packages(), encoding="utf-8")
    data.update({
        "version": "day13.9-company-overview-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": digest(OUT / "code_snapshot.zip"),
        "pip_freeze_sha256": digest(OUT / "pip_freeze.txt"),
    })
    write_once(MANIFEST, data)
    print(f"DAY 13.9 OVERVIEW FROZEN: {len(data['files'])} files, {len(data['documents'])} reports")


def check() -> None:
    if not MANIFEST.is_file():
        raise RuntimeError("No Day 13.9 freeze; run --freeze once after all tests pass")
    previous = load(MANIFEST)
    current = state()
    for key, value in current.items():
        if previous.get(key) != value:
            raise RuntimeError("Day 13.9 freeze violation: " + key)
    if digest(OUT / "code_snapshot.zip") != previous.get("snapshot_sha256"):
        raise RuntimeError("Day 13.9 code snapshot changed")
    if digest(OUT / "pip_freeze.txt") != previous.get("pip_freeze_sha256"):
        raise RuntimeError("Day 13.9 dependency snapshot changed")
    if packages().splitlines() != (OUT / "pip_freeze.txt").read_text(encoding="utf-8").splitlines():
        raise RuntimeError("Installed Python packages changed")
    print(f"DAY 13.9 OVERVIEW FREEZE CHECK PASSED: {len(current['files'])} files, {len(current['documents'])} reports")


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
