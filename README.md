# FinanceAI

FinanceAI is a local-first financial document intelligence application. It combines PDF ingestion, retrieval-augmented question answering, structured financial-table handling, evidence checks, and an optional market-data integration in a FastAPI + Streamlit application.

> **Project status:** portfolio / development project. This README describes the current local application; it does not claim that the system has completed a production security review or independent financial validation.

## What it does

- Ingests financial PDF reports and makes their contents searchable.
- Retrieves relevant report passages and table content to support financial questions.
- Uses evidence checks and report/page references to make answers easier to verify.
- Supports report-aware questions about metrics, periods, issuers, and financial-statement scope.
- Supports company/report overviews and comparisons across reports, including follow-up questions.
- Offers a market-data integration through Twelve Data when an API key is configured.
- Provides a Streamlit interface backed by a FastAPI service.

The test suite contains report-layout regression examples for Nordea, SEB, Handelsbanken, Jyske, and Danske reports. These fixtures are focused examples, not a guarantee that every document layout or question is supported.

## Architecture

- **Streamlit UI:** `streamlit_market_app.py`
- **Compatibility entry point:** `streamlit_app.py`
- **FastAPI application:** `app.api_day13_6:app`
- **Application modules:** PDF ingestion and chunking, embeddings, hybrid retrieval, document resolution, table reading, evidence verification, financial reasoning, conversation handling, and market-data integration are organized under `app/`.
- **Vector store:** ChromaDB is persisted locally at `~/FinanceAI_Local/chroma_db` by `app/vector_store.py`.
- **Local language model:** the current development setup uses Ollama with `qwen3:4b` for local LLM-backed responses.

The local ChromaDB database and source PDFs are not part of the public source repository. A fresh installation starts without your local documents or index; users must ingest their own documents.

## Requirements

The project was developed and tested in a Windows environment using Python 3.14.6. Runtime and development dependencies are pinned in:

- `requirements.txt`
- `requirements-dev.txt`

The local LLM workflow requires Ollama and the `qwen3:4b` model. Market-data functionality requires a Twelve Data API key. These services and credentials are configured separately from Python package installation.

## Setup (Windows PowerShell)

From the project root:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

If PowerShell blocks virtual-environment activation, use the interpreter directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Install Ollama separately, start it, and make the model available:

```powershell
ollama pull qwen3:4b
```

## Configuration

`.env.market.example` is a documentation template; it is **not automatically loaded** by the application. Set the required values in the shell or your deployment environment. For example, in PowerShell:

```powershell
$env:TWELVE_DATA_API_KEY = "YOUR_TWELVE_DATA_API_KEY"
$env:TWELVE_DATA_PLAN = "basic"
$env:TWELVE_DATA_ALLOW_OTC = "0"
$env:FINANCE_AI_API_URL = "http://127.0.0.1:8000"
```

`FINANCE_AI_MARKET_ACCESS_TOKEN` is an optional application-level token if that protection is enabled in your configuration. Never commit real API keys, access tokens, `.env` files, or Streamlit secrets.

## Run the application

Start the API in the first terminal:

```powershell
python -m uvicorn app.api_day13_6:app --host 127.0.0.1 --port 8000
```

Start the Streamlit interface in a second terminal:

```powershell
python -m streamlit run streamlit_market_app.py
```

The Streamlit app connects to the API at `FINANCE_AI_API_URL` (the example above uses the local default). Keep both processes running while using the application.

## Tests and evaluation

Run the tests from the project root:

```powershell
python -m pytest -q
```

**Latest owner-reported local verification after the public-test adjustment:** 677 tests passed, 2 dependency warnings, in 33.62 seconds. This run was performed in the developer's original Windows workspace, where private local evaluation files were available. The warnings came from ChromaDB's telemetry code and the installed `fitz`/PyMuPDF compatibility API.

**Private Day 11 benchmark handling:** `tests/test_day11_12_tools.py` uses the local `data/evaluation/day11_50_candidate.json` benchmark. Only tests that require that benchmark skip when the JSON is absent (as it will be in a clean public clone). Their original assertions still run in the original local workspace where the benchmark exists. Generic tests in that module remain runnable without the benchmark.

`tests/test_day13_5_freeze.py` builds a disposable synthetic project under pytest's temporary directory; it does not require the real sealed benchmark or historical report-freeze folders and remains unchanged. Other tests are retained. A full test run from a fresh public clone has **not** yet been verified, so the local 677-pass result must not be represented as a clean-clone result.

The `scripts/` and `tools/` folders include both useful development utilities and historical evaluation/freeze tools. Some of those tools require private local artifacts and are not part of the normal application startup path.

## Local data and privacy

The following are local development data, not source code, and should remain outside the public repository:

- `data/` — original PDFs, processed chunks, and private benchmark datasets.
- `reports/` — evaluation runs, source-document snapshots, database snapshots, and historical freezes.
- `evaluation/` — local retrieval evaluation output.
- The ChromaDB database under the user's home directory.

The application may process financial documents supplied by the user. Treat uploaded documents, extracted text, indexes, logs, and generated answers according to the confidentiality and licensing requirements of those documents.

## Production considerations

This is not a claim of production readiness. Before exposing it to the internet, review authentication and authorization, upload limits and file validation, rate limiting, secret management, network binding, logging and retention, database isolation and backups, dependency vulnerabilities, and model-output verification. Do not expose the local development server directly to the public internet.

## License

No open-source license has been selected for this repository yet. Until a license is added by the project owner, do not assume that the source code is licensed for reuse or redistribution.
