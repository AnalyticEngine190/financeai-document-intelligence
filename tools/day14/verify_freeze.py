"""Non-mutating verification and safe restoration for a Day 14 Chroma freeze.

IMPORTANT: this module intentionally does not instantiate ChromaDB. A normal
PersistentClient open can update SQLite/HNSW durable files, which would make a
"verification" command alter the state it is supposed to inspect.

Commands:
  python -m tools.day14.verify_freeze --check
  python -m tools.day14.verify_freeze --restore-index

Restore is explicit, verifies the frozen archive against the immutable manifest,
backs up the current index outside the project, then restores the exact snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from typing import Any

from scripts.day14_common import ROOT, FREEZE_DIR, sha256_file, load_dataset
from scripts.day14_freeze import (
    _source_hashes,
    _prior_evaluation_hashes,
    _previous_freeze_hashes,
    _pdf_sources,
    _runtime,
    _packages,
    _sha_bytes,
    _safe_project_files,
    _index_file_hashes,
)


_VOLATILE_SUFFIXES = ("-wal", "-shm", "-journal")
_VOLATILE_NAMES = {"lock", "chroma.lock", ".lock"}
_PREFIX = "chroma_db/"
# This documentation file was added by the Day 14.0.4 evaluator-wrapper
# support patch AFTER the original freeze. It is not application code and was
# never part of the immutable Day 13.19 snapshot.
_POST_FREEZE_SUPPORT_FILES = {"README_DAY14_EVALUATOR_WRAPPER.md"}
_ATTESTATION_PATH = ROOT / "reports" / "observability" / "day14_preflight_attestation.json"
_ATTESTATION_MAX_AGE_HOURS = 36


def is_volatile_index_path(path: str) -> bool:
    name = Path(path).name.casefold()
    return name in _VOLATILE_NAMES or name.endswith(_VOLATILE_SUFFIXES) or name.endswith(".lock")


def _hash_zip_members(path: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(_PREFIX):
                continue
            rel = info.filename[len(_PREFIX):]
            if not rel:
                continue
            payload = archive.read(info.filename)
            found[rel] = {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
    return found


def _verify_artifacts(manifest: dict, freeze_dir: Path) -> list[str]:
    artifacts = manifest.get("artifacts", {})
    paths = {
        "code_snapshot_sha256": freeze_dir / "code_snapshot.zip",
        "chroma_index_snapshot_sha256": freeze_dir / "chroma_index_snapshot.zip",
        "source_pdfs_snapshot_sha256": freeze_dir / "source_pdfs_snapshot.zip",
        "prior_evaluation_artifacts_sha256": freeze_dir / "prior_evaluation_artifacts.zip",
        "registry_snapshot_sha256": freeze_dir / "registry_snapshot.json",
        "index_inventory_sha256": freeze_dir / "index_inventory.json",
        "runtime_sha256": freeze_dir / "runtime.json",
        "pip_freeze_sha256": freeze_dir / "pip_freeze.txt",
        "pytest_full_sha256": freeze_dir / "pytest_full.txt",
        "benchmark_pdf_audit_sha256": freeze_dir / "benchmark_pdf_audit.json",
    }
    errors: list[str] = []
    for key, path in paths.items():
        expected = artifacts.get(key)
        if key == "prior_evaluation_artifacts_sha256" and expected is None and not path.exists():
            continue
        if not path.is_file():
            errors.append(f"missing frozen artifact: {path.name}")
        elif expected is None or sha256_file(path) != expected:
            errors.append(f"frozen artifact hash mismatch: {path.name}")
    return errors


def _frozen_index_files(manifest: dict) -> dict[str, dict]:
    state = manifest.get("state") or {}
    files = state.get("index_files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("Freeze manifest has no index_files inventory")
    return files


def _verify_snapshot_integrity(manifest: dict, freeze_dir: Path) -> tuple[dict[str, dict], list[str]]:
    errors = _verify_artifacts(manifest, freeze_dir)
    snapshot_path = freeze_dir / "chroma_index_snapshot.zip"
    if not snapshot_path.is_file():
        return {}, errors + ["missing frozen Chroma snapshot"]
    expected_zip_hash = (manifest.get("artifacts") or {}).get("chroma_index_snapshot_sha256")
    if not expected_zip_hash or sha256_file(snapshot_path) != expected_zip_hash:
        errors.append("Chroma snapshot ZIP hash does not match the freeze manifest")
        return {}, errors
    try:
        archived = _hash_zip_members(snapshot_path)
    except Exception as exc:
        return {}, errors + [f"cannot read Chroma snapshot ZIP: {exc}"]
    frozen = _frozen_index_files(manifest)
    if archived != frozen:
        errors.append("Chroma snapshot members do not match the frozen index_files inventory")
    return archived, errors


def _source_hashes_for_frozen_baseline(frozen_hashes: dict[str, str]) -> dict[str, str]:
    """Return current frozen-source hashes, ignoring one documented post-freeze helper README.

    New/changed application files are never ignored. The helper is removed only
    when it is extra (not present in the original frozen manifest).
    """
    current = _source_hashes()
    for rel in _POST_FREEZE_SUPPORT_FILES:
        if rel not in frozen_hashes:
            current.pop(rel, None)
    return current


def _source_differences(frozen_hashes: dict[str, str], current_hashes: dict[str, str]) -> dict[str, list[str]]:
    expected_names, actual_names = set(frozen_hashes), set(current_hashes)
    return {
        "missing": sorted(expected_names - actual_names),
        "added": sorted(actual_names - expected_names),
        "changed": sorted(
            name for name in expected_names & actual_names
            if frozen_hashes[name] != current_hashes[name]
        ),
    }


def _canonical_fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_preflight_attestation(manifest: dict, manifest_path: Path) -> None:
    state = manifest["state"]
    attestation = {
        "schema_version": 1,
        "verified_before_api_start": True,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_manifest_sha256": sha256_file(manifest_path),
        "frozen_index_files_fingerprint": _canonical_fingerprint(state["index_files"]),
        "frozen_source_files_fingerprint": _canonical_fingerprint(state["source_files"]),
        "registry_sha256": state["registry_sha256"],
        "benchmark_sha256": state["benchmark_sha256"],
        "pip_freeze_sha256": state["pip_freeze_sha256"],
        "index_file_count": len(state["index_files"]),
        "indexed_chunk_count": int((state.get("index_inventory") or {}).get("collection_count", -1)),
        "indexed_document_ids": sorted(
            str(d.get("document_id")) for d in (state.get("index_inventory") or {}).get("documents", [])
        ),
    }
    _ATTESTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _ATTESTATION_PATH.with_suffix(_ATTESTATION_PATH.suffix + ".tmp")
    temp_path.write_text(json.dumps(attestation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp_path, _ATTESTATION_PATH)


def _invalidate_attestation() -> None:
    try:
        _ATTESTATION_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _current_non_chroma_state(manifest: dict) -> tuple[dict, Path]:
    """Recompute frozen state except Chroma inventory, without opening Chroma."""
    frozen = manifest["state"]
    registry_path = Path(frozen["registry_path"])
    if not registry_path.is_file():
        raise RuntimeError(f"Document registry not found: {registry_path}")

    pdf_paths = _pdf_sources()  # SHA lookup only; no vector DB client.
    current = {
        "source_files": _source_hashes_for_frozen_baseline(frozen.get("source_files", {})),
        "prior_evaluation_files": _prior_evaluation_hashes(),
        "prior_freeze_files": _previous_freeze_hashes(),
        "registry_path": str(registry_path),
        "registry_sha256": sha256_file(registry_path),
        "source_pdfs": {
            key: {"path": str(path), "sha256": sha256_file(path)}
            for key, path in pdf_paths.items()
        },
        "runtime": _runtime(),
        "pip_freeze_sha256": _sha_bytes(_packages().encode("utf-8")),
        "benchmark_sha256": sha256_file(load_dataset_path()),
        "runtime_configuration": {
            "ollama_model": os.getenv("OLLAMA_MODEL", "qwen3:4b"),
            "ollama_host_class": (
                "local" if any(host in os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").casefold()
                                for host in ("localhost", "127.0.0.1", "::1")) else "nonlocal"
            ),
            "finance_ai_debug": os.getenv("FINANCE_AI_DEBUG", "unset"),
        },
        "runtime_environment_flags": {
            name: bool(os.getenv(name)) for name in (
                "FINANCE_AI_DOCUMENT_REGISTRY", "FINANCE_AI_CHUNKS_FILE",
                "TWELVE_DATA_API_KEY", "FINANCE_AI_MARKET_ACCESS_TOKEN",
            )
        },
    }
    db_path = Path((frozen.get("index_inventory") or {}).get("chroma_db_path", ""))
    if not str(db_path):
        raise RuntimeError("Freeze manifest does not record chroma_db_path")
    return current, db_path


def load_dataset_path() -> Path:
    # Keep the benchmark path canonical and avoid opening/validating Chroma.
    from scripts.day14_common import DATASET
    return DATASET


def _verify_live_index(db_path: Path, expected: dict[str, dict]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    # Pure filesystem hashing: never import app.vector_store or chromadb.
    actual = _index_file_hashes(db_path)
    exp_names, act_names = set(expected), set(actual)
    added = sorted(act_names - exp_names)
    removed = sorted(exp_names - act_names)
    changed = sorted(
        name for name in exp_names & act_names
        if expected[name].get("sha256") != actual[name].get("sha256")
        or expected[name].get("size_bytes") != actual[name].get("size_bytes")
    )
    return actual, {"added": added, "removed": removed, "changed": changed}


def verify() -> int:
    """Strict offline check. Run only while API/Streamlit are stopped.

    On success it writes a short-lived attestation that records the exact raw
    Chroma snapshot match BEFORE the API opens the DB. The evaluator later uses
    that attestation plus API document/chunk inventory checks; it must not hash
    Chroma files again after Chroma has been opened by the live API.
    """
    manifest_path = FREEZE_DIR / "manifest.json"
    if not manifest_path.is_file():
        _invalidate_attestation()
        print(f"ERROR: Day 14 freeze manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("state"), dict):
        _invalidate_attestation()
        print("ERROR: Freeze manifest has no valid state object.", file=sys.stderr)
        return 2

    archived, integrity_errors = _verify_snapshot_integrity(manifest, FREEZE_DIR)
    current_non_chroma, db_path = _current_non_chroma_state(manifest)
    frozen_state = manifest["state"]
    component_errors = [
        key for key, value in current_non_chroma.items()
        if frozen_state.get(key) != value
    ]
    source_diff = _source_differences(
        frozen_state.get("source_files", {}), current_non_chroma.get("source_files", {})
    )
    actual_index, diff = _verify_live_index(db_path, frozen_state.get("index_files", {}))

    print("DAY 14 STRICT OFFLINE FREEZE VERIFICATION")
    print("ChromaDB client instantiated: NO")
    print(f"Frozen Chroma files: {len(archived)}")
    print(f"Current Chroma files: {len(actual_index)}")
    if any(diff.values()):
        print("Persistent Chroma differences:")
        for kind in ("added", "removed", "changed"):
            for name in diff[kind]:
                print(f"  {kind}: {name}")
    if any(source_diff.values()):
        print("Source-file differences:")
        for kind in ("missing", "added", "changed"):
            for name in source_diff[kind]:
                print(f"  {kind}: {name}")
    if component_errors:
        non_source = sorted(set(component_errors) - {"source_files"})
        if non_source:
            print("Changed non-Chroma state component(s): " + ", ".join(non_source))
    for error in integrity_errors:
        print("ERROR: " + error)

    if component_errors or any(diff.values()) or integrity_errors:
        _invalidate_attestation()
        print("DAY 14 STRICT CHECK FAILED. Freeze preserved; do not rerun --freeze.")
        return 2

    _write_preflight_attestation(manifest, manifest_path)
    inventory = frozen_state.get("index_inventory", {})
    print(
        "DAY 14 STRICT CHECK PASSED: "
        f"{len(frozen_state.get('source_files', {}))} frozen source/config/test files; "
        f"{len(inventory.get('documents', []))} reports; "
        f"{inventory.get('collection_count')} Chroma chunks."
    )
    print(f"Pre-start index attestation written: {_ATTESTATION_PATH}")
    print("Now start the API. Do not run this raw-file check again while Chroma is open; use the evaluator wrapper.")
    return 0


def verify_for_evaluation() -> int:
    """Verify a pre-start attestation and all static state without hashing live Chroma.

    Chroma's SQLite/HNSW files can be rewritten during normal client startup.
    The exact raw index was already verified offline and attested before API
    startup. While the API is live, the evaluator separately verifies the four
    report IDs and per-report chunk counts through GET /documents.
    """
    manifest_path = FREEZE_DIR / "manifest.json"
    if not manifest_path.is_file():
        print("ERROR: Day 14 freeze manifest is missing.", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("state"), dict):
        print("ERROR: Day 14 freeze manifest has no valid state object.", file=sys.stderr)
        return 2

    archived, integrity_errors = _verify_snapshot_integrity(manifest, FREEZE_DIR)
    current_non_chroma, _db_path = _current_non_chroma_state(manifest)
    frozen_state = manifest["state"]
    source_diff = _source_differences(
        frozen_state.get("source_files", {}), current_non_chroma.get("source_files", {})
    )
    component_errors = [
        key for key, value in current_non_chroma.items()
        if key != "source_files" and frozen_state.get(key) != value
    ]
    if any(source_diff.values()):
        component_errors.append("source_files")

    attestation_errors: list[str] = []
    if not _ATTESTATION_PATH.is_file():
        attestation_errors.append("missing pre-start attestation; stop the API and rerun verify_freeze --check")
    else:
        try:
            attestation = json.loads(_ATTESTATION_PATH.read_text(encoding="utf-8"))
            if attestation.get("schema_version") != 1 or attestation.get("verified_before_api_start") is not True:
                attestation_errors.append("attestation schema or pre-start marker is invalid")
            if attestation.get("freeze_manifest_sha256") != sha256_file(manifest_path):
                attestation_errors.append("attestation belongs to a different freeze manifest")
            if attestation.get("frozen_index_files_fingerprint") != _canonical_fingerprint(frozen_state.get("index_files", {})):
                attestation_errors.append("attested Chroma fingerprint does not match frozen index inventory")
            if attestation.get("frozen_source_files_fingerprint") != _canonical_fingerprint(frozen_state.get("source_files", {})):
                attestation_errors.append("attested source fingerprint does not match frozen code snapshot")
            if attestation.get("registry_sha256") != frozen_state.get("registry_sha256"):
                attestation_errors.append("attested registry fingerprint does not match freeze")
            if attestation.get("benchmark_sha256") != frozen_state.get("benchmark_sha256"):
                attestation_errors.append("attested benchmark fingerprint does not match freeze")
            if attestation.get("pip_freeze_sha256") != frozen_state.get("pip_freeze_sha256"):
                attestation_errors.append("attested Python dependency fingerprint does not match freeze")
            created = datetime.fromisoformat(str(attestation.get("created_utc", "")).replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 3600
            if age_hours < -0.05 or age_hours > _ATTESTATION_MAX_AGE_HOURS:
                attestation_errors.append(
                    f"pre-start attestation is {age_hours:.1f} hours old; stop services, restore/check again"
                )
        except Exception as exc:
            attestation_errors.append(f"cannot validate pre-start attestation: {type(exc).__name__}: {exc}")

    print("DAY 14 PRE-START ATTESTED EVALUATION CHECK")
    print("ChromaDB client instantiated: NO")
    print(f"Frozen Chroma snapshot files verified: {len(archived)}")
    print("Live Chroma raw bytes re-hashed: NO (API has already opened the database)")
    if component_errors:
        print("Changed non-Chroma state component(s): " + ", ".join(sorted(set(component_errors))))
        if any(source_diff.values()):
            for kind in ("missing", "added", "changed"):
                for name in source_diff[kind]:
                    print(f"  source {kind}: {name}")
    for error in integrity_errors + attestation_errors:
        print("ERROR: " + error)

    if component_errors or integrity_errors or attestation_errors:
        print("DAY 14 EVALUATION CHECK FAILED. No benchmark questions should be sent.")
        return 2

    print("DAY 14 EVALUATION CHECK PASSED: attested pre-start Chroma baseline and unchanged static state.")
    print("The wrapper will now validate the live API's report IDs and per-report chunk counts before sending questions.")
    return 0

def _safe_extract_snapshot(snapshot_path: Path, staging_dir: Path, expected: dict[str, dict]) -> None:
    staging_dir.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(snapshot_path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(_PREFIX):
                continue
            rel_text = info.filename[len(_PREFIX):]
            rel = PurePosixPath(rel_text)
            if (not rel_text or rel.is_absolute() or ".." in rel.parts
                    or any(":" in part or "\\" in part for part in rel.parts)):
                raise RuntimeError(f"Unsafe path in Chroma archive: {info.filename}")
            destination = staging_dir.joinpath(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info.filename))
    extracted: dict[str, dict] = {}
    for path in sorted(staging_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(staging_dir).as_posix()
            extracted[rel] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    if extracted != expected:
        raise RuntimeError("Restored staging index does not match the immutable freeze inventory")


def restore_index() -> int:
    """Restore the frozen Chroma snapshot, preserving a full backup of live DB."""
    manifest_path = FREEZE_DIR / "manifest.json"
    if not manifest_path.is_file():
        print("ERROR: Day 14 freeze manifest not found.", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        archived, integrity_errors = _verify_snapshot_integrity(manifest, FREEZE_DIR)
        if integrity_errors:
            raise RuntimeError("Frozen artifacts are not internally valid: " + "; ".join(integrity_errors))
        expected = _frozen_index_files(manifest)
        if archived != expected:
            raise RuntimeError("Frozen Chroma snapshot does not exactly match its manifest inventory")

        state = manifest["state"]
        db_path = Path((state.get("index_inventory") or {}).get("chroma_db_path", ""))
        if not str(db_path):
            raise RuntimeError("Freeze manifest has no Chroma path")
        parent = db_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = parent / f"{db_path.name}.pre_day14_restore_{stamp}"
        staging_path = parent / f"{db_path.name}.day14_restore_staging_{stamp}"
        displaced_path = parent / f"{db_path.name}.day14_restore_old_{stamp}"
        if backup_path.exists() or staging_path.exists() or displaced_path.exists():
            raise RuntimeError("Restore destination already exists; refusing to overwrite")

        if db_path.exists():
            sidecars = [p.relative_to(db_path).as_posix() for p in db_path.rglob("*")
                        if p.is_file() and is_volatile_index_path(p.name)]
            if sidecars:
                raise RuntimeError(
                    "SQLite/runtime sidecars exist, so the Chroma DB may still be open. "
                    "Stop FastAPI, Streamlit and Python processes using Chroma, then retry. "
                    f"Sidecars: {sidecars[:10]}"
                )

        _safe_extract_snapshot(FREEZE_DIR / "chroma_index_snapshot.zip", staging_path, expected)
        moved_current = False
        try:
            if db_path.exists():
                # Same-volume directory rename preserves the entire current DB as
                # the backup without making a second, potentially inconsistent copy.
                os.replace(db_path, backup_path)
                moved_current = True
                print(f"Current Chroma database preserved at: {backup_path}")
            else:
                print("Current Chroma directory is absent; no pre-restore backup was needed.")
            os.replace(staging_path, db_path)
        except Exception:
            if db_path.exists():
                shutil.rmtree(db_path, ignore_errors=True)
            if moved_current and backup_path.exists() and not db_path.exists():
                os.replace(backup_path, db_path)
            raise

        restored, diff = _verify_live_index(db_path, expected)
        if any(diff.values()):
            raise RuntimeError(f"Post-restore raw-file check failed: {diff}")
        _invalidate_attestation()
        print(f"FROZEN CHROMA INDEX RESTORED: {len(restored)} files; {state['index_inventory']['collection_count']} chunks recorded in freeze.")
        print("The original freeze, manifest and snapshot were not modified.")
        print("Next: with all services stopped, run python -m tools.day14.verify_freeze --check to create a fresh pre-start attestation.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="non-mutating verification (default)")
    group.add_argument("--restore-index", action="store_true", help="back up live DB and restore exact frozen Chroma snapshot")
    args = parser.parse_args()
    raise SystemExit(restore_index() if args.restore_index else verify())


if __name__ == "__main__":
    main()
