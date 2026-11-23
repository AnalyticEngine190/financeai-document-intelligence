"""Small offline self-test for the Day 14.0.5 evaluator wrapper.

Run from the FinanceAI project root:
    python -m tools.day14.selftest_0_5
This test does not start ChromaDB, contact the API, or submit benchmark cases.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.day14 import verify_freeze
from tools.day14.run_evaluation import _document_inventory_check


def main() -> None:
    # Source whitelist must be exactly the post-freeze Day 14.0.4 README.
    frozen = {"app/api.py": "a" * 64}
    with patch.object(verify_freeze, "_source_hashes", return_value={
        "app/api.py": "a" * 64,
        "README_DAY14_EVALUATOR_WRAPPER.md": "support-only",
    }):
        normalized = verify_freeze._source_hashes_for_frozen_baseline(frozen)
    assert normalized == frozen

    # The whitelist must never hide a changed application source or new app file.
    with patch.object(verify_freeze, "_source_hashes", return_value={
        "app/api.py": "b" * 64,
        "app/new_route.py": "c" * 64,
        "README_DAY14_EVALUATOR_WRAPPER.md": "support-only",
    }):
        normalized = verify_freeze._source_hashes_for_frozen_baseline(frozen)
    diff = verify_freeze._source_differences(frozen, normalized)
    assert diff == {"missing": [], "added": ["app/new_route.py"], "changed": ["app/api.py"]}

    # API inventory validates all report fields and chunk counts before sending cases.
    docs = [
        {"document_id": "id1", "filename": "nq2.pdf", "issuer": "Nordea",
         "report_year": 2026, "report_quarter": 2, "report_type": "factbook",
         "currency": "EUR", "sha256": "id1", "chunk_count": 178},
        {"document_id": "id2", "filename": "seb.pdf", "issuer": "SEB",
         "report_year": 2026, "report_quarter": 3, "report_type": "quarterly_report",
         "currency": "SEK", "sha256": "id2", "chunk_count": 171},
    ]
    good = {"passed": True, "errors": [], "actual_documents": docs}
    assert _document_inventory_check(good, docs, 349)["passed"] is True
    bad = [dict(docs[0]), dict(docs[1], chunk_count=170)]
    checked = _document_inventory_check(good | {"actual_documents": bad}, docs, 349)
    assert checked["passed"] is False
    assert checked["index_inventory_check"]["actual_registry_chunks"] == 348

    # Exercise the strict offline check -> pre-start attestation -> live evaluator check.
    with tempfile.TemporaryDirectory(prefix="day14_05_selftest_") as tmp:
        root = Path(tmp)
        freeze_dir = root / "reports" / "evaluation" / "day14_freeze"
        freeze_dir.mkdir(parents=True)
        frozen_state = {
            "source_files": {"app/api.py": "a" * 64},
            "prior_evaluation_files": {}, "prior_freeze_files": {},
            "registry_path": str(root / "documents.json"), "registry_sha256": "r",
            "source_pdfs": {}, "runtime": {}, "pip_freeze_sha256": "p",
            "benchmark_sha256": "b", "runtime_configuration": {}, "runtime_environment_flags": {},
            "index_files": {"chroma.sqlite3": {"sha256": "b" * 64, "size_bytes": 1}},
            "index_inventory": {"collection_count": 1, "documents": [{"document_id": "id1"}]},
        }
        manifest = {"state": frozen_state, "artifacts": {}}
        manifest_path = freeze_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        attestation_path = root / "reports" / "observability" / "day14_preflight_attestation.json"
        current_state = {k: v for k, v in frozen_state.items() if k not in {"index_files", "index_inventory"}}
        with patch.object(verify_freeze, "FREEZE_DIR", freeze_dir), \
             patch.object(verify_freeze, "_ATTESTATION_PATH", attestation_path), \
             patch.object(verify_freeze, "_verify_snapshot_integrity", return_value=({"chroma.sqlite3": manifest["state"]["index_files"]["chroma.sqlite3"]}, [])), \
             patch.object(verify_freeze, "_current_non_chroma_state", return_value=(current_state, root / "live_chroma")), \
             patch.object(verify_freeze, "_verify_live_index", return_value=(manifest["state"]["index_files"], {"added": [], "removed": [], "changed": []})):
            assert verify_freeze.verify() == 0
            # After API startup the live Chroma files may differ. Evaluation verification
            # must rely on the pre-start raw index attestation and must not hash them again.
            with patch.object(verify_freeze, "_verify_live_index", side_effect=AssertionError("live index must not be raw-hashed after API start")):
                assert verify_freeze.verify_for_evaluation() == 0

    # This helper must not initialize Chroma/vector-store modules.
    assert "chromadb" not in sys.modules
    assert "app.vector_store" not in sys.modules
    print("DAY 14.0.5 SELF-TEST PASSED: strict pre-start attestation, post-start evaluator verification, source whitelist, live inventory and no Chroma initialization.")


if __name__ == "__main__":
    main()
