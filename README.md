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

The JSON files under `tests/fixtures/` are **synthetic regression fixtures**. They use fictional report text, invented institutions and invented financial values to exercise table layouts, period alignment, segment extraction, scope handling, and citation behaviour. They are not reproductions of bank reports and should not be treated as financial data. Some fixture filenames and test names retain historical issuer labels for test-path continuity.

## Architecture

- **Streamlit UI:** `streamlit_market_app.py`
- **Compatibility entry point:** `streamlit_app.py`
- **FastAPI application:** `app.api_day13_6:app`
- **Application modules:** PDF ingestion and chunking, embeddings, hybrid retrieval, document resolution, table reading, evidence verification, financial reasoning, conversation handling, and market-data integration are organized under `app/`.
- **Vector store:** ChromaDB is persisted locally at `~/FinanceAI_Local/chroma_db` by `app/vector_store.py`.
- **Local language model:** the current development setup uses Ollama with `qwen3:4b` for local LLM-backed responses.

The local ChromaDB database and source PDFs are not part of the public source repository. A fresh installation starts without your local documents or index; users must obtain reports from their publishers, ingest their own documents, and build a local index.

## AI-assisted development approach

I use AI as a **guided engineering assistant**, not as an autonomous developer or a substitute for understanding the project. I remain responsible for defining the intended behaviour, deciding how a change should fit into the application, reviewing the implementation, and verifying the result.

My prompts are deliberately specific. Rather than asking a broad question such as "build this feature for me", I break the work into small, testable instructions. I identify the relevant file or function, describe the inputs and expected outputs, specify the control flow and edge cases, and state what must not change.

For example, a prompt may look like this:

> In `tests/test_answering.py`, add a loop over these cases. For each case, call the specified helper with these inputs and assert the expected result. Around this particular operation, add a `try/except` block for the expected exceptions listed here. Let unexpected exceptions remain visible, and do not modify unrelated application logic.

AI can help draft a focused implementation, explain unfamiliar code, suggest tests, or investigate a failure. I then inspect the proposed changes, ask for clarification or revisions where needed, run the relevant tests, examine failures, and iterate until the behaviour matches the requirements. I do not treat generated code as correct merely because it looks plausible or runs once.

This workflow keeps the technical direction and acceptance criteria with me while using AI to accelerate implementation, debugging, and test development. The final responsibility for reviewing and validating the code remains mine.

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

**Latest owner-reported test run of the public upload copy:** 649 passed, 28 skipped, 0 failed, and 2 dependency warnings in 39.38 seconds, using Python 3.14.6 in the developer's Windows environment.

The 28 skips are intentional. They cover tests that require private Day 10.4, Day 11, Day 14, and Nordea benchmark datasets that are not distributed in this repository. Those tests retain their assertions and run when the corresponding private datasets are available in the developer's local workspace. This result was obtained from the prepared upload copy using an already configured local Python environment; a clean installation on a separate machine has not yet been independently verified.

The two warnings came from a ChromaDB telemetry deprecation and the installed `fitz`/PyMuPDF compatibility API. They did not cause test failures.

The `scripts/` and `tools/` folders include both useful development utilities and historical evaluation/freeze tools. Some of those tools require private local artifacts and are not part of the normal application startup path.

## Local data and privacy

The following are local development data, not source code, and should remain outside the public repository:

- `data/` — original PDFs, processed chunks, and private benchmark datasets.
- `reports/` — evaluation runs, source-document snapshots, database snapshots, and historical freezes.
- `evaluation/` — local retrieval evaluation output.
- The ChromaDB database under the user's home directory.

FinanceAI can process financial documents supplied by the user. Treat uploaded documents, extracted text, indexes, logs, and generated answers according to the confidentiality and licensing requirements of those documents. Public availability of a source PDF does not by itself establish permission to redistribute that PDF; obtain it from the publisher and ingest it locally.

## Production considerations

This is not a claim of production readiness. Before exposing it to the internet, review authentication and authorization, upload limits and file validation, rate limiting, secret management, network binding, logging and retention, database isolation and backups, dependency vulnerabilities, and model-output verification. Do not expose the local development server directly to the public internet.

## License

No open-source license has been selected for this repository yet. Until a license is added by the project owner, do not assume that the source code is licensed for reuse or redistribution.
