# Day 14 evaluator wrapper — non-mutating freeze check

## Why this wrapper is needed

The original frozen `scripts/evaluate_day14.py` calls
`scripts.day14_freeze --check`. That legacy check recomputes the Chroma
inventory by opening the ChromaDB client. Your Day 14.0.3 recovery workflow
restored the exact frozen index and verified it using raw filesystem hashes,
without opening Chroma.

This additive wrapper leaves every frozen source file unchanged. It imports
the original evaluator and substitutes its in-memory `_check_freeze` callback
with `tools.day14.verify_freeze.verify()`. The latter performs the raw-hash
check and does not instantiate ChromaDB.

## Required state

- `reports/evaluation/day14_freeze/` exists and is preserved.
- `tools/day14/verify_freeze.py` from Day 14.0.3 is installed.
- The non-mutating check currently passes:
  `python -m tools.day14.verify_freeze --check`
- FastAPI uses the same frozen API entrypoint as Day 13.19.

## Run

Start FastAPI in terminal 1 and leave it running:

```powershell
python -m uvicorn app.api_day13_5:app --host 127.0.0.1 --port 8000
```

In terminal 2, from the same project root and active `.venv`:

```powershell
python -m tools.day14.run_evaluation --first-run
```

For an interrupted run only:

```powershell
python -m tools.day14.run_evaluation --resume
```

Optional live market smoke (may consume a small number of Twelve Data API
requests; it is not needed for the RAG benchmark):

```powershell
python -m tools.day14.run_evaluation --first-run --market-live
```

Do not use `python -m scripts.evaluate_day14 --first-run` for this restored
freeze: that command still calls the legacy Chroma-opening check. Do not run
`pytest`, `scripts.day14_freeze --check`, or any other Chroma-opening command
between the non-mutating freeze verification and starting the evaluation.

The wrapper does not edit `scripts/evaluate_day14.py`, the freeze manifest,
the Chroma snapshot, the benchmark, or prior evaluation records. The original
evaluator remains the source of the results and report files.
