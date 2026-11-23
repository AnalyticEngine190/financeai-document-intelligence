from pathlib import Path

import pytest

from scripts.day10_4_freeze import _check_documents, tracked_files
from scripts.evaluate_day10_4 import validate_dataset

DATASET = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "day10_4_40.json"


def test_minimum_bundle_sources_tracked():
    tracked = tracked_files()
    assert "scripts/day10_4_freeze.py" in tracked
    assert "scripts/evaluate_day10_4.py" in tracked

    # Keep checking the benchmark's freeze inclusion on the private/local
    # project, but do not require the private benchmark in public source.
    if DATASET.is_file():
        assert "data/evaluation/day10_4_40.json" in tracked


@pytest.mark.skipif(
    not DATASET.is_file(),
    reason="Private Day 10.4 benchmark is not included in the public repository",
)
def test_freeze_requires_correct_three_reports():
    ds = validate_dataset()
    info = {
        d["document_id"]: dict(
            issuer=d["issuer"],
            report_year=int(d["edition"][-4:]),
            report_quarter=int(d["edition"][1]),
            chunk_count=100,
        )
        for d in ds["documents"].values()
    }
    _check_documents(info, ds)
    with pytest.raises(RuntimeError):
        _check_documents({**info, "extra": {}}, ds)
    with pytest.raises(RuntimeError):
        _check_documents(dict(list(info.items())[:2]), ds)
