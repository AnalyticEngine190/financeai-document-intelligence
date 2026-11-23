"""Create/check an additive Day 14 freeze of the exact working FinanceAI installation.

The freeze is local and append-only. It archives source code, prior evaluation
artifacts, the document registry, Chroma index files, exact source PDFs,
Python package state, and a freshly captured full pytest result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass
from collections.abc import Mapping
from typing import Iterable

from scripts.day14_common import ROOT, FREEZE_DIR, FIRST_RUN_DIR, DATASET, load_dataset, sha256_file, find_pdf_files
from scripts.audit_day14_benchmark import audit as audit_benchmark

IGNORED_DIRS = {"__pycache__", ".venv", "venv", ".git", ".pytest_cache", "node_modules"}
SAFE_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".txt", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".csv", ".css", ".html", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
ROOT_FILES = {"streamlit_app.py", "streamlit_market_app.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", ".python-version"}


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_project_files() -> list[Path]:
    selected: set[Path] = set()
    for folder in (ROOT / "app", ROOT / "scripts", ROOT / "tests", ROOT / "data" / "evaluation",
                   ROOT / "data" / "processed", ROOT / "config", ROOT / "assets", ROOT / "static", ROOT / "docs"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or any(part in IGNORED_DIRS for part in path.parts):
                continue
            if path.suffix.casefold() in SAFE_SUFFIXES and not path.name.casefold().startswith(".env"):
                selected.add(path)
    for name in ROOT_FILES:
        candidate = ROOT / name
        if candidate.is_file():
            selected.add(candidate)
    for pattern in ("requirements*.txt", "constraints*.txt", "*.py"):
        for candidate in ROOT.glob(pattern):
            if candidate.is_file() and not candidate.name.casefold().startswith(".env"):
                selected.add(candidate)
    streamlit_config = ROOT / ".streamlit" / "config.toml"
    if streamlit_config.is_file():
        selected.add(streamlit_config)
    for path in ROOT.glob("README*"):
        if path.is_file():
            selected.add(path)
    # Include safe env templates, never actual credentials.
    for path in ROOT.glob(".env*"):
        if path.is_file() and (path.name.endswith(".example") or path.name.endswith(".template")):
            selected.add(path)
    return sorted(selected, key=lambda p: p.relative_to(ROOT).as_posix().casefold())


def _source_hashes() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): sha256_file(p) for p in _safe_project_files()}


def _prior_evaluation_hashes() -> dict[str, str]:
    base = ROOT / "reports" / "evaluation"
    result: dict[str, str] = {}
    if not base.exists():
        return result
    excluded_roots = {"day14_freeze", "day14_first_run", "day14_preflight_pytest_failed.txt"}
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if rel.parts and rel.parts[0] in excluded_roots:
            continue
        result[rel.as_posix()] = sha256_file(path)
    return result


def _previous_freeze_hashes() -> dict[str, str]:
    base = ROOT / "reports" / "evaluation"
    result: dict[str, str] = {}
    if not base.exists():
        return result
    for folder in sorted(base.iterdir()):
        if folder.is_dir() and "freeze" in folder.name.casefold() and folder.name != "day14_freeze":
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    result[path.relative_to(ROOT).as_posix()] = sha256_file(path)
    return result


def _runtime() -> dict:
    import platform
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "implementation": platform.python_implementation(),
    }


def _packages() -> str:
    proc = subprocess.run([sys.executable, "-m", "pip", "freeze"], cwd=ROOT,
                          text=True, capture_output=True, check=True)
    return proc.stdout


def _public_document_dict(document: object) -> dict:
    """Normalize the registry's public document record across supported shapes.

    The live registry normally returns DocumentRecord instances, whose
    ``public_dict()`` method produces dictionaries. Some compatible registry
    implementations already return dictionaries. The freeze inventory is
    always serialized to plain dictionaries so the manifest is JSON-safe.
    """
    if isinstance(document, Mapping):
        payload = dict(document)
    else:
        public_dict = getattr(document, "public_dict", None)
        if callable(public_dict):
            payload = public_dict()
        elif is_dataclass(document):
            payload = asdict(document)
        else:
            raise RuntimeError(
                "Unsupported document-registry record type: "
                f"{type(document).__name__}. Expected a mapping or DocumentRecord-like object."
            )
    if not isinstance(payload, Mapping):
        raise RuntimeError(
            "Document-registry public representation must be a mapping; "
            f"received {type(payload).__name__}."
        )
    result = dict(payload)
    if not result.get("document_id"):
        raise RuntimeError("A document-registry record is missing its document_id.")
    return result


def _indexed_document_ids(documents: Iterable[object]) -> set[str]:
    """Read document IDs from the normalized inventory (plain mappings)."""
    ids: set[str] = set()
    for document in documents:
        if isinstance(document, Mapping):
            document_id = document.get("document_id")
        else:
            document_id = getattr(document, "document_id", None)
        if document_id:
            ids.add(str(document_id))
    return ids


def _registry_and_index() -> tuple[Path, dict, Path]:
    try:
        from app.document_registry import registry_path, list_documents
        from app.vector_store import DB_PATH, COLLECTION_NAME, get_collection
        from app.embeddings import MODEL_NAME
    except Exception as exc:
        raise RuntimeError(f"Cannot import the document registry or vector store: {exc}") from exc

    registry = registry_path()
    if not registry.is_file():
        raise RuntimeError(f"Document registry not found: {registry}")
    documents = list_documents()
    collection = get_collection()
    metadata = collection.get(include=["metadatas"])
    grouped: dict[str, dict] = {}
    for meta in metadata.get("metadatas") or []:
        if not isinstance(meta, dict) or not meta.get("document_id"):
            continue
        doc_id = str(meta["document_id"])
        row = grouped.setdefault(doc_id, {"chunk_count": 0, "pages": set(), "filenames": set()})
        row["chunk_count"] += 1
        if meta.get("page_number") is not None:
            row["pages"].add(int(meta["page_number"]))
        if meta.get("filename"):
            row["filenames"].add(str(meta["filename"]))
    for row in grouped.values():
        row["pages"] = sorted(row["pages"])
        row["filenames"] = sorted(row["filenames"])
    inventory = {
        "collection_name": COLLECTION_NAME,
        "embedding_model": MODEL_NAME,
        "chroma_db_path": str(DB_PATH),
        "collection_count": int(collection.count()),
        "documents": [_public_document_dict(doc) for doc in documents],
        "indexed_chunks_by_document": grouped,
    }
    return registry, inventory, Path(DB_PATH)


def _pdf_sources() -> dict[str, Path]:
    data = load_dataset()
    found = find_pdf_files([ROOT / "data" / "raw", ROOT, Path.home() / "FinanceAI_Local"])
    result: dict[str, Path] = {}
    missing = []
    for key, info in data["source_documents"].items():
        path = found.get(info["sha256"])
        if path is None:
            missing.append(f"{info['issuer']} Q{info['quarter']} {info['year']} ({info['sha256']})")
        else:
            result[key] = path
    if missing:
        raise RuntimeError(
            "Exact PDF source files are required for the Day 14 evidence audit and source backup. "
            "Place the original PDFs in data/raw and retry. Missing: " + "; ".join(missing)
        )
    return result


def _zip_files(output: Path, files: Iterable[tuple[Path, str]], base: Path | None = None) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, arcname in files:
            archive.write(path, arcname)
    return sha256_file(output)


def _index_file_hashes(db_path: Path) -> dict[str, dict]:
    if not db_path.exists():
        raise RuntimeError(f"Chroma index directory does not exist: {db_path}")
    result = {}
    for path in sorted(db_path.rglob("*")):
        if path.is_file():
            result[path.relative_to(db_path).as_posix()] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    if not result:
        raise RuntimeError("Chroma index directory is empty; refusing to freeze an empty index")
    return result


def _write_zip_snapshot(output: Path, file_items: list[tuple[Path, str]]) -> str:
    return _zip_files(output, file_items)


def _state() -> tuple[dict, dict]:
    registry, inventory, db_path = _registry_and_index()
    pdf_paths = _pdf_sources()
    # Exact file hashes in the four source documents must line up with the index IDs.
    indexed_ids = _indexed_document_ids(inventory["documents"])
    expected_ids = {info["sha256"] for info in load_dataset()["source_documents"].values()}
    if not expected_ids.issubset(indexed_ids):
        missing = sorted(expected_ids - indexed_ids)
        raise RuntimeError("The four benchmark source PDFs are not all in the live Chroma index: " + ", ".join(missing))
    if len(inventory["documents"]) != 4:
        raise RuntimeError(f"Expected exactly four indexed source reports, found {len(inventory['documents'])}")
    state = {
        "source_files": _source_hashes(),
        "prior_evaluation_files": _prior_evaluation_hashes(),
        "prior_freeze_files": _previous_freeze_hashes(),
        "registry_path": str(registry),
        "registry_sha256": sha256_file(registry),
        "index_inventory": inventory,
        "index_files": _index_file_hashes(db_path),
        "source_pdfs": {key: {"path": str(path), "sha256": sha256_file(path)} for key, path in pdf_paths.items()},
        "runtime": _runtime(),
        "pip_freeze_sha256": _sha_bytes(_packages().encode("utf-8")),
        "benchmark_sha256": sha256_file(DATASET),
        "runtime_configuration": {
            # Non-secret deployment settings needed to interpret the freeze.
            # Never persist API keys, market tokens, or .env contents.
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
    return state, {"registry": registry, "db_path": db_path, "pdf_paths": pdf_paths}


def _run_full_pytest() -> str:
    print("Running the complete pytest suite before freezing Day 13.19 ...", flush=True)
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT,
                          text=True, capture_output=True)
    output = (proc.stdout or "") + ("\n--- STDERR ---\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        failure_path = ROOT / "reports" / "evaluation" / "day14_preflight_pytest_failed.txt"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(output, encoding="utf-8")
        raise RuntimeError(f"Full pytest suite failed (exit {proc.returncode}). Log: {failure_path}")
    return output


def freeze() -> None:
    if FREEZE_DIR.exists():
        raise RuntimeError(f"{FREEZE_DIR} already exists; Day 14 freezes are write-once")
    if FIRST_RUN_DIR.exists():
        raise RuntimeError(f"{FIRST_RUN_DIR} already exists. A benchmark run appears to have started before the Day 14 freeze; preserve it and investigate rather than overwriting it.")
    audit_result = audit_benchmark([ROOT / "data" / "raw", ROOT, Path.home() / "FinanceAI_Local"])
    if not audit_result["passed"]:
        raise RuntimeError("Day 14 benchmark PDF audit failed; run scripts.audit_day14_benchmark first")
    pytest_output = _run_full_pytest()
    state, external = _state()

    FREEZE_DIR.mkdir(parents=True, exist_ok=False)
    # Full source-code/test/config snapshot; excludes secrets, environments, and databases.
    code_files = []
    for rel in state["source_files"]:
        code_files.append((ROOT / rel, rel))
    code_sha = _write_zip_snapshot(FREEZE_DIR / "code_snapshot.zip", code_files)

    # Exact indexed database backup. API/Streamlit must be stopped before this command.
    db_files = [(p, f"chroma_db/{p.relative_to(external['db_path']).as_posix()}")
                for p in sorted(external["db_path"].rglob("*")) if p.is_file()]
    index_sha = _write_zip_snapshot(FREEZE_DIR / "chroma_index_snapshot.zip", db_files)

    pdf_files = []
    for key, path in external["pdf_paths"].items():
        info = load_dataset()["source_documents"][key]
        safe_name = f"{info['issuer'].replace(' ', '_')}_Q{info['quarter']}_{info['year']}_{path.name}"
        pdf_files.append((path, f"source_pdfs/{safe_name}"))
    pdf_sha = _write_zip_snapshot(FREEZE_DIR / "source_pdfs_snapshot.zip", pdf_files)

    # Archive all historical evaluation outputs/freezes as they existed pre-Day14.
    eval_root = ROOT / "reports" / "evaluation"
    previous_files = []
    if eval_root.exists():
        for p in sorted(eval_root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(eval_root)
            if rel.parts and rel.parts[0] in {"day14_freeze", "day14_first_run", "day14_preflight_pytest_failed.txt"}:
                continue
            previous_files.append((p, f"reports/evaluation/{rel.as_posix()}"))
    prior_sha = _write_zip_snapshot(FREEZE_DIR / "prior_evaluation_artifacts.zip", previous_files) if previous_files else None

    registry_bytes = external["registry"].read_bytes()
    (FREEZE_DIR / "registry_snapshot.json").write_bytes(registry_bytes)
    (FREEZE_DIR / "index_inventory.json").write_text(json.dumps(state["index_inventory"], indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    (FREEZE_DIR / "runtime.json").write_text(json.dumps(state["runtime"], indent=2)+"\n", encoding="utf-8")
    (FREEZE_DIR / "pip_freeze.txt").write_text(_packages(), encoding="utf-8")
    (FREEZE_DIR / "pytest_full.txt").write_text(pytest_output, encoding="utf-8")
    (FREEZE_DIR / "benchmark_pdf_audit.json").write_text(json.dumps(audit_result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    manifest = {
        "version": "day14_freeze_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "day13_baseline": "Day 13.19",
        "state": state,
        "artifacts": {
            "code_snapshot_sha256": code_sha,
            "chroma_index_snapshot_sha256": index_sha,
            "source_pdfs_snapshot_sha256": pdf_sha,
            "prior_evaluation_artifacts_sha256": prior_sha,
            "registry_snapshot_sha256": sha256_file(FREEZE_DIR / "registry_snapshot.json"),
            "index_inventory_sha256": sha256_file(FREEZE_DIR / "index_inventory.json"),
            "runtime_sha256": sha256_file(FREEZE_DIR / "runtime.json"),
            "pip_freeze_sha256": sha256_file(FREEZE_DIR / "pip_freeze.txt"),
            "pytest_full_sha256": sha256_file(FREEZE_DIR / "pytest_full.txt"),
            "benchmark_pdf_audit_sha256": sha256_file(FREEZE_DIR / "benchmark_pdf_audit.json"),
        },
        "notes": [
            "ChromaDB was backed up with the API and Streamlit stopped.",
            "Only exact source PDFs whose SHA-256 matches the indexed document IDs were included.",
            "Previous evaluation and freeze artifacts were archived without modification.",
            "The Day 14 benchmark is held out from the runtime and is only passed to /chat_v1 by the evaluator.",
            "Provider credentials and .env secrets are deliberately excluded.",
        ],
    }
    (FREEZE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(f"DAY 14 FREEZE CREATED: {len(state['source_files'])} source/config/test files; "
          f"{len(state['index_inventory']['documents'])} indexed reports; "
          f"{state['index_inventory']['collection_count']} Chroma chunks.")
    print("Full pytest passed and its output is preserved in reports/evaluation/day14_freeze/pytest_full.txt")


def check() -> None:
    manifest_path = FREEZE_DIR / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("No Day 14 freeze exists. Run python -m scripts.day14_freeze --freeze once.")
    frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
    current, _ = _state()
    if current != frozen.get("state"):
        keys = [k for k in current if current.get(k) != frozen.get("state", {}).get(k)]
        raise RuntimeError("Day 14 freeze check failed; changed state component(s): " + ", ".join(keys))
    artifacts = frozen.get("artifacts", {})
    files = {
        "code_snapshot_sha256": FREEZE_DIR / "code_snapshot.zip",
        "chroma_index_snapshot_sha256": FREEZE_DIR / "chroma_index_snapshot.zip",
        "source_pdfs_snapshot_sha256": FREEZE_DIR / "source_pdfs_snapshot.zip",
        "prior_evaluation_artifacts_sha256": FREEZE_DIR / "prior_evaluation_artifacts.zip",
        "registry_snapshot_sha256": FREEZE_DIR / "registry_snapshot.json",
        "index_inventory_sha256": FREEZE_DIR / "index_inventory.json",
        "runtime_sha256": FREEZE_DIR / "runtime.json",
        "pip_freeze_sha256": FREEZE_DIR / "pip_freeze.txt",
        "pytest_full_sha256": FREEZE_DIR / "pytest_full.txt",
        "benchmark_pdf_audit_sha256": FREEZE_DIR / "benchmark_pdf_audit.json",
    }
    for key, path in files.items():
        if artifacts.get(key) is None:
            if key == "prior_evaluation_artifacts_sha256" and path.exists() is False:
                continue
            raise RuntimeError(f"Freeze manifest is missing {key}")
        if not path.is_file() or sha256_file(path) != artifacts[key]:
            raise RuntimeError(f"Frozen artifact changed or missing: {path.name}")
    print(f"DAY 14 FREEZE CHECK PASSED: {len(current['source_files'])} files; "
          f"{len(current['index_inventory']['documents'])} indexed reports; "
          f"{current['index_inventory']['collection_count']} chunks. No Day 13 source/index changes detected.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--freeze", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        freeze() if args.freeze else check()
    except Exception as exc:
        parser.exit(2, "ERROR: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
