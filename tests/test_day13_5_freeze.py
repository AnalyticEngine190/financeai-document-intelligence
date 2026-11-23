"""Day 13.5 archive tests use a disposable fake project, not a real freeze."""
from __future__ import annotations
import json
from pathlib import Path
import zipfile
import pytest

from scripts import freeze_day13_5_market as f


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    root = tmp_path / "finance"
    root.mkdir()
    old_dir = root / "reports/evaluation/day11_3_freeze"
    old_dir.mkdir(parents=True)
    old = old_dir / "manifest.json"
    gold = root / "data/evaluation/day11_50_sealed.json"
    gold.parent.mkdir(parents=True)
    gold.write_text('{"example":"preserved"}', encoding="utf8")
    snapshot = old_dir / "code_snapshot.zip"
    with zipfile.ZipFile(snapshot, "w") as z:
        z.writestr("example", "old code")
    deps = old_dir / "pip_freeze.txt"
    deps.write_text("pytest==TEST\n", encoding="utf8")
    originals = {}
    for i in range(123):
        name = f"app/legacy_{i:03d}.py"
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# frozen file {i}\n", encoding="utf8")
        originals[name] = f.digest(path)
    docs = {"REPORT_ID": {"issuer": "Example", "chunk_count": 3}}
    old.write_text(json.dumps({"files": originals, "documents": docs,
        "gold_sha256": f.digest(gold), "snapshot_sha256": f.digest(snapshot),
        "pip_freeze_sha256": f.digest(deps)}), encoding="utf8")
    for rel in f.REQUIRED:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"new patch file {rel}\n", encoding="utf8")
    monkeypatch.setattr(f, "ROOT", root)
    monkeypatch.setattr(f, "OLD", old)
    monkeypatch.setattr(f, "SEALED", gold)
    monkeypatch.setattr(f, "OLD_RUNS", root / "unused")
    monkeypatch.setattr(f, "OUT", root / "reports/evaluation/day13_5_market_freeze")
    monkeypatch.setattr(f, "MANIFEST", f.OUT / "manifest.json")
    monkeypatch.setattr(f, "doc_fingerprint", lambda u: docs)
    monkeypatch.setattr(f, "runtime", lambda: {"model": "test"})
    monkeypatch.setattr(f, "packages", lambda: "pytest==TEST\n")
    monkeypatch.setattr(f, "_regression", lambda: {"archived_result": "PINNED"})
    def source_files():
        return {p.relative_to(root).as_posix(): f.digest(p) for p in root.rglob("*.py")
                if "reports" not in p.parts}
    monkeypatch.setattr(f, "source_files", source_files)
    return root


def test_frozen_previous_code_cannot_change(frozen):
    assert len(f._previous()[0]["files"]) == 123
    (frozen / "app/legacy_008.py").write_text("# changed\n", encoding="utf8")
    with pytest.raises(RuntimeError, match="Previously frozen file changed"):
        f._previous()


def test_new_snapshot_is_append_only(frozen):
    f.freeze()
    f.check()
    assert f.MANIFEST.exists()
    with pytest.raises(RuntimeError, match="already exists"):
        f.freeze()


def test_modified_market_file_invalidates_snapshot(frozen):
    f.freeze()
    (frozen / "app/market_data.py").write_text("# modified\n", encoding="utf8")
    with pytest.raises(RuntimeError, match="Day 13.5 freeze violation"):
        f.check()


def test_missing_market_file_prevents_freeze(frozen):
    (frozen / "app/market_routes.py").unlink()
    with pytest.raises(RuntimeError, match="Missing market feature file"):
        f.freeze()
