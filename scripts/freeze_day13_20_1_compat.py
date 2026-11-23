"""Create/check a separate Day 13.20.1 compatibility snapshot.

This freeze is deliberately additive: it does not modify the Day 13.20 freeze,
Day 11 evaluation artifacts, financial PDFs, Chroma data, or provider settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/evaluation/day13_20_1_compat_freeze"
MANIFEST = OUT / "manifest.json"
PATCH_FILES = {
    "app/market_chat.py",
    "streamlit_market_app.py",
    "tests/test_day13_20_1_compat.py",
    "scripts/freeze_day13_20_1_compat.py",
    "README_DAY13_20_1_COMPAT_FIX.md",
}
GUARD_FILES = {
    "app/market_entitlements.py",
    "app/market_data.py",
    "app/market_routes.py",
    "app/api_day13_6.py",
    "app/document_answering.py",
    "app/financial_plan.py",
}
TRACKED = PATCH_FILES | GUARD_FILES


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def state() -> dict:
    files = {}
    for relative in sorted(TRACKED):
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError("Missing required file: " + relative)
        files[relative] = digest(path)
    return {
        "version": "day13.20.1-chat-followup-compatibility-v1",
        "files": files,
        "patch_files": sorted(PATCH_FILES),
        "guard_files": sorted(GUARD_FILES),
        "api_entrypoint": "app.api_day13_6:app",
        "chat_endpoint": "/chat_v1",
    }


def freeze() -> None:
    if OUT.exists():
        raise RuntimeError("Day 13.20.1 freeze already exists; refusing to overwrite")
    data = state()
    OUT.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(OUT / "code_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(data["files"]):
            archive.write(ROOT / relative, relative)
    data["created_utc"] = datetime.now(timezone.utc).isoformat()
    data["snapshot_sha256"] = digest(OUT / "code_snapshot.zip")
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"DAY 13.20.1 FROZEN: {len(data['files'])} files; {len(PATCH_FILES)} patch files")


def check() -> None:
    if not MANIFEST.is_file():
        raise RuntimeError("Day 13.20.1 freeze missing; run --freeze once")
    previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current = state()
    for key, value in current.items():
        if previous.get(key) != value:
            raise RuntimeError("Day 13.20.1 freeze violation: " + key)
    snapshot = OUT / "code_snapshot.zip"
    if not snapshot.is_file() or digest(snapshot) != previous.get("snapshot_sha256"):
        raise RuntimeError("Day 13.20.1 frozen code snapshot changed")
    print(f"DAY 13.20.1 FREEZE CHECK PASSED: {len(current['files'])} files unchanged")


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
