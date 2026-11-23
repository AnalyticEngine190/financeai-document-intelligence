"""Shared helpers for FinanceAI Day 14 independent evaluation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "evaluation" / "day14_benchmark_80.json"
FREEZE_DIR = ROOT / "reports" / "evaluation" / "day14_freeze"
FIRST_RUN_DIR = ROOT / "reports" / "evaluation" / "day14_first_run"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_dataset(path: Path = DATASET) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != 1 or not isinstance(obj.get("cases"), list):
        raise ValueError("Unsupported Day 14 benchmark schema")
    return obj


def normalized_text(text: str) -> str:
    text = text.replace("\u00ad", "").replace("\u2011", "-")
    return re.sub(r"\s+", " ", text).strip().casefold()


def find_pdf_files(search_roots: list[Path]) -> dict[str, Path]:
    """Return exact-source PDF matches keyed by SHA-256, ignoring common bulky dirs."""
    excluded = {".venv", "venv", "__pycache__", ".git", "node_modules", "chroma_db", ".pytest_cache"}
    found: dict[str, Path] = {}
    seen: set[Path] = set()
    for root in search_roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() and root.suffix.casefold() == ".pdf" else (
            (path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() == ".pdf")
            if root.is_dir() else []
        )
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved in seen or any(part in excluded for part in resolved.parts):
                    continue
                seen.add(resolved)
                found[sha256_file(resolved)] = resolved
            except (OSError, PermissionError):
                continue
    return found


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 2)
