"""Day 11.3 archival pinning remains fail-closed and read-only."""
import json
from pathlib import Path
import pytest

from scripts import freeze_day11_3 as f


def _fixture(tmp_path, monkeypatch):
    old_dir=tmp_path/'reports'/'evaluation'/'day11_2_freeze'
    old_dir.mkdir(parents=True)
    previous=old_dir/'manifest.json'
    snapshot=old_dir/'code_snapshot.zip';snapshot.write_bytes(b'sealed previous code')
    dependencies=old_dir/'pip_freeze.txt';dependencies.write_text('a==1\n')
    sealed=tmp_path/'data'/'evaluation'/'sealed.json';sealed.parent.mkdir(parents=True)
    sealed.write_text('{"frozen":true}\n')
    previous.write_text(json.dumps({'snapshot_sha256':f.digest(snapshot),
        'pip_freeze_sha256':f.digest(dependencies),'gold_sha256':f.digest(sealed)}))
    regressions=tmp_path/'reports'/'evaluation'/'day11_2_regressions'
    regressions.mkdir(parents=True)
    run=regressions/'original';run.mkdir()
    (run/'run_metadata.json').write_text(json.dumps({'question_ids':[
        'D11_12','D11_45','D11_46','D11_47'],
        'gold_sha256':f.digest(sealed),'freeze_sha256':f.digest(previous)}))
    (run/'summary.json').write_text(json.dumps({'counts':{'PASS':1,'FAIL':1,'REVIEW':2}}))
    (run/'results.jsonl').write_text('{"case":"D11_46","status":"FAIL"}\n')
    monkeypatch.setattr(f,'ROOT',tmp_path)
    monkeypatch.setattr(f,'PREVIOUS',previous)
    monkeypatch.setattr(f,'PREVIOUS_RUNS',regressions)
    monkeypatch.setattr(f,'SEALED',sealed)
    return previous, snapshot, dependencies, sealed, run


def test_pins_previous_frozen_code_and_run(tmp_path, monkeypatch):
    previous,snapshot,dependencies,sealed,run=_fixture(tmp_path,monkeypatch)
    old=f._preserved_snapshot()
    saved=f._preserved_regression()
    assert len(old)==3 and len(saved)==3
    assert str(run.joinpath('results.jsonl').relative_to(tmp_path)) in saved
    assert f.digest(previous) in old.values()


def test_rejects_mutated_prior_snapshot(tmp_path, monkeypatch):
    previous,snapshot,dependencies,sealed,run=_fixture(tmp_path,monkeypatch)
    snapshot.write_bytes(snapshot.read_bytes()+b'altered')
    with pytest.raises(RuntimeError,match='changed'):
        f._preserved_snapshot()


def test_rejects_mutated_gold(tmp_path, monkeypatch):
    previous,snapshot,dependencies,sealed,run=_fixture(tmp_path,monkeypatch)
    sealed.write_text('{"frozen":false}\n')
    with pytest.raises(RuntimeError,match='gold'):
        f._preserved_snapshot()
    with pytest.raises(RuntimeError,match='targeted run'):
        f._preserved_regression()


def test_rejects_wrong_previous_run_count(tmp_path, monkeypatch):
    previous,snapshot,dependencies,sealed,run=_fixture(tmp_path,monkeypatch)
    (run/'summary.json').write_text(json.dumps({'counts':{'PASS':4}}))
    with pytest.raises(RuntimeError,match='targeted run'):
        f._preserved_regression()
