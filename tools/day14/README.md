# Day 14.0.3 — Non-mutating Chroma verification and safe restore

The Day 14.0.2 helper still called `scripts.day14_freeze._state()`. That function opens a Chroma `PersistentClient`, calls `collection.get()` and `collection.count()`, then hashes the database. Opening Chroma is not a read-only filesystem operation: Chroma/SQLite/HNSW may update persistent files during initialization or recovery. Therefore the verifier could mutate the index it was checking.

Day 14.0.3 deliberately does **not** instantiate ChromaDB during verification. It:

- Recomputes source/config/test, registry, PDF, runtime, package, benchmark and prior-evaluation fingerprints without opening the vector store.
- Reads the frozen manifest and Chroma ZIP directly.
- Checks that the Chroma ZIP hash and every archived member match the immutable `index_files` inventory.
- Hashes the current Chroma directory as raw files and compares it exactly to the frozen inventory.
- Verifies all frozen artifact hashes.

## Non-mutating check

Stop FastAPI and Streamlit, then run:

```powershell
python -m tools.day14.verify_freeze --check
```

This command does not import `app.vector_store` or initialize Chroma.

## Restore the frozen index (only if the check reports live-index differences)

The original Day 14 freeze and its Chroma snapshot are the source of truth. Restore is explicit and does not rewrite the freeze:

```powershell
python -m tools.day14.verify_freeze --restore-index
```

The restore command:

1. Validates the Chroma snapshot ZIP against the original manifest and artifact hash.
2. Refuses to proceed if SQLite WAL/SHM/journal or lock sidecars suggest an open database.
3. Copies the current live Chroma directory to a timestamped backup beside the database directory (`chroma_db.pre_day14_restore_<UTC timestamp>`).
4. Extracts to a staging directory, verifies every file hash, then replaces the live Chroma directory with the exact frozen snapshot.
5. Does not delete the backup and does not modify any file in `reports/evaluation/day14_freeze`.

After restore, while services remain stopped, run:

```powershell
python -m tools.day14.verify_freeze --check
```

Only proceed to the benchmark first run if this non-mutating check passes. Do not run the original `scripts.day14_freeze --freeze` again.

## Important

If the frozen ZIP fails its own hash/member checks, do not restore; send the output. If the non-mutating check reports non-index differences, investigate those separately. Do not start FastAPI or Streamlit between a successful check and the benchmark run, because opening Chroma can legitimately change runtime database files after the freeze point. The evaluator should be run only after the freeze has been verified; its actual API interactions are part of the benchmark run and are separately recorded.
