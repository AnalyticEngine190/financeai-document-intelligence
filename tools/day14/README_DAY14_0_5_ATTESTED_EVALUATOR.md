# Day 14.0.5 — Pre-start attestation and live API inventory check

## Why this patch is needed

The Day 14 freeze captures exact raw Chroma SQLite/HNSW file hashes while FastAPI and Streamlit are stopped. Chroma may rewrite persistent files when the API opens the database. A strict raw-file check performed *after* startup can therefore report different hashes even when the report registry and chunk inventory are unchanged.

A second issue: the Day 14.0.4 support package added `README_DAY14_EVALUATOR_WRAPPER.md` at the project root. The original freeze's source scanner includes root `README*` files. This patch explicitly treats that one post-freeze helper document as evaluation tooling, not application source. No application `.py`, configuration, test, registry or benchmark file is ignored.

## Safe sequence — do not skip ordering

1. Stop FastAPI and Streamlit. Ensure no Python process has the Chroma database open.
2. Restore the exact frozen index only if the strict check reports persistent index differences:

   ```powershell
   python -m tools.day14.verify_freeze --restore-index
   ```

   This preserves the current database in a timestamped backup and does not alter the freeze.
3. While all services remain stopped, run the strict check:

   ```powershell
   python -m tools.day14.verify_freeze --check
   ```

   On success, this writes `reports/observability/day14_preflight_attestation.json`. This attestation records the exact index fingerprint and freeze-manifest hash verified before API startup. It is short-lived (36 hours).
4. Start FastAPI in terminal 1 and leave it running:

   ```powershell
   python -m uvicorn app.api_day13_5:app --host 127.0.0.1 --port 8000
   ```

   Do not Ctrl+C this terminal until the benchmark finishes.
5. In terminal 2, run the wrapper, not the original evaluator:

   ```powershell
   python -m tools.day14.run_evaluation --first-run
   ```

   For an interrupted run only:

   ```powershell
   python -m tools.day14.run_evaluation --resume
   ```

## What is verified after API startup

The evaluator checks:

- the original freeze manifest and every frozen artifact hash;
- exact source-code/config/test hashes, apart from the one named Day 14.0.4 support README;
- registry, source PDF, benchmark, runtime and Python dependency fingerprints;
- the pre-start attestation is present, recent, and belongs to this exact freeze;
- the API is healthy, exposes `/chat_v1`, and lists precisely the four frozen source document IDs;
- each API document record matches the frozen filename, issuer, year, quarter, report type, currency, SHA-256 and chunk count;
- the sum of registry chunk counts equals the frozen collection count (737 for this freeze).

It deliberately does **not** compare live raw Chroma file bytes after the API has opened Chroma. Their pre-start raw hashes are attested, and the live logical report/chunk inventory is checked through the API. Benchmark retrieval and answer performance are then measured by the 80 held-out cases.

## Important

- Do not run `scripts.day14_freeze --freeze` again.
- Do not run the original `scripts.evaluate_day14 --first-run`; it uses the legacy freeze check. Use `tools.day14.run_evaluation`.
- Do not run `tools.day14.verify_freeze --check` after starting FastAPI. It is specifically the offline, pre-start strict check.
- If the 36-hour attestation expires, stop services and repeat steps 2–3. Do not overwrite the freeze.
- Do not run pytest between the strict pre-start check and the first-run evaluation. The full 534-test suite is already preserved in the freeze.
- If `/documents` metadata differs, the wrapper aborts before submitting benchmark questions.
