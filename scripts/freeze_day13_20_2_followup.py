"""Create/check an additive Day 13.20.2 follow-up compatibility snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/evaluation/day13_20_2_followup_freeze"
MANIFEST = OUT / "manifest.json"
PATCH_FILES = {
    "app/market_chat.py",
    "tests/test_day13_20_2_followup_contract.py",
    "scripts/freeze_day13_20_2_followup.py",
    "README_DAY13_20_2_FOLLOWUP_FIX.md",
}
GUARD_FILES = {
    # Market/data policy and the established document-answering pipeline.
    "app/market_entitlements.py",
    "app/market_data.py",
    "app/market_routes.py",
    "app/api_day13_6.py",
    "app/document_answering.py",
    "app/financial_plan.py",
    # Keep the prior UI and follow-up contracts intact.
    "streamlit_market_app.py",
    "tests/test_conversational_comparison_followups.py",
    "tests/test_day13_19_clarification_slots_and_private_account_boundary.py",
    "tests/test_day13_20_1_compat.py",
    "tests/test_day13_20_market_coverage.py",
}
TRACKED = PATCH_FILES | GUARD_FILES


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def state() -> dict:
    files: dict[str, str] = {}
    missing: list[str] = []
    for relative in sorted(TRACKED):
        path = ROOT / relative
        if not path.is_file():
            missing.append(relative)
        else:
            files[relative] = digest(path)
    if missing:
        raise RuntimeError("Missing required files: " + ", ".join(missing))
    return {
        "version": "day13.20.2-followup-compatibility-v1",
        "files": files,
        "patch_files": sorted(PATCH_FILES),
        "guard_files": sorted(GUARD_FILES),
        "api_entrypoint": "app.api_day13_6:app",
        "chat_endpoint": "/chat_v1",
        "financial_answering_path": "existing /ask_v2 document engine via legacy._doc",
    }


def freeze() -> None:
    if OUT.exists():
        raise RuntimeError("Day 13.20.2 freeze already exists; refusing to overwrite")
    data = state()
    OUT.mkdir(parents=True, exist_ok=False)
    snapshot = OUT / "code_snapshot.zip"
    with zipfile.ZipFile(snapshot, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(data["files"]):
            archive.write(ROOT / relative, relative)
    data["created_utc"] = datetime.now(timezone.utc).isoformat()
    data["snapshot_sha256"] = digest(snapshot)
    MANIFEST.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"DAY 13.20.2 FROZEN: {len(data['files'])} files "
        f"({len(PATCH_FILES)} patch, {len(GUARD_FILES)} guard)"
    )


def check() -> None:
    if not MANIFEST.is_file():
        raise RuntimeError("Day 13.20.2 freeze missing; run --freeze after tests")
    previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
    current = state()
    for key, value in current.items():
        if previous.get(key) != value:
            raise RuntimeError("Day 13.20.2 freeze violation: " + key)
    snapshot = OUT / "code_snapshot.zip"
    if not snapshot.is_file() or digest(snapshot) != previous.get("snapshot_sha256"):
        raise RuntimeError("Day 13.20.2 frozen code snapshot changed")
    print(f"DAY 13.20.2 FREEZE CHECK PASSED: {len(current['files'])} files unchanged")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--freeze", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        freeze() if args.freeze else check()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(2, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
