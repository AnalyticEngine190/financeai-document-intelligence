"""Verify earlier frozen market README/env metadata is not falsely reported changed."""
from pathlib import Path
import json
import pytest
from scripts import freeze_day13_6_chat as f


def test_prior_market_non_py_files_still_verified(tmp_path, monkeypatch):
    root = tmp_path
    for rel, content in {
        "app/locked.py": "LOCKED\n",
        "streamlit_market_app.py": "NEW UI\n",
        "README_DAY13_5_MARKET.md": "OLD MARKET README\n",
        ".env.market.example": "TWELVE_DATA_API_KEY=\n",
        "data/evaluation/day11_50_sealed.json": "{}",
        "reports/evaluation/day11_3_freeze/code_snapshot.zip": "OLD ZIP",
        "reports/evaluation/day11_3_freeze/pip_freeze.txt": "OLD PIP",
        "reports/evaluation/day13_5_market_freeze/code_snapshot.zip": "MARKET ZIP",
        "reports/evaluation/day13_5_market_freeze/pip_freeze.txt": "MARKET PIP",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    finance = {
        "files": {"app/locked.py": f.digest(root / "app/locked.py")},
        "gold_sha256": f.digest(root / "data/evaluation/day11_50_sealed.json"),
        "documents": {},
        "snapshot_sha256": f.digest(root / "reports/evaluation/day11_3_freeze/code_snapshot.zip"),
        "pip_freeze_sha256": f.digest(root / "reports/evaluation/day11_3_freeze/pip_freeze.txt"),
    }
    market = {
        "files": {
            "app/locked.py": f.digest(root / "app/locked.py"),
            "streamlit_market_app.py": "PRIOR_UI_HASH_NOT_EQUAL_TO_NEW_UI",
            "README_DAY13_5_MARKET.md": f.digest(root / "README_DAY13_5_MARKET.md"),
            ".env.market.example": f.digest(root / ".env.market.example"),
        },
        "snapshot_sha256": f.digest(root / "reports/evaluation/day13_5_market_freeze/code_snapshot.zip"),
        "pip_freeze_sha256": f.digest(root / "reports/evaluation/day13_5_market_freeze/pip_freeze.txt"),
    }
    for name, data in (("day11_3_freeze", finance), ("day13_5_market_freeze", market)):
        (root / f"reports/evaluation/{name}/manifest.json").write_text(json.dumps(data))
    monkeypatch.setattr(f, "ROOT", root)
    monkeypatch.setattr(f, "SEALED", root / "data/evaluation/day11_50_sealed.json")
    monkeypatch.setattr(f, "PREVIOUS_FINANCE", root / "reports/evaluation/day11_3_freeze/manifest.json")
    monkeypatch.setattr(f, "PREVIOUS_MARKET", root / "reports/evaluation/day13_5_market_freeze/manifest.json")
    monkeypatch.setattr(f, "source_files", lambda: {"app/locked.py": f.digest(root / "app/locked.py"),
                                                   "streamlit_market_app.py": f.digest(root / "streamlit_market_app.py")})
    monkeypatch.setattr(f, "doc_fingerprint", lambda url: {})
    assert len(f.verify_predecessors()) == 6
    (root / "README_DAY13_5_MARKET.md").write_text("TAMPERED")
    with pytest.raises(RuntimeError, match="Prior market source changed"):
        f.verify_predecessors()
