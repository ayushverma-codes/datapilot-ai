# Final Naukr.AI assessment verification

## Readiness

The **assessment candidate is ready for review**. The published `main` independently passed its original 197 tests from a fresh network clone. The candidate closes the missing joined-retail batch evaluation path and adds a reproducible assessment README. Candidate results do not describe features already published on GitHub. No commit or push was performed.

The supplied A–H sections allocate 90 of the stated 100 points. These are rubric weights, not awarded scores. This report does not invent the missing 10 points or predict an exact assessment score.

| Area | Official points | Independently verified evidence | Status and remaining boundary |
| --- | ---: | --- | --- |
| A. Data profiling and cleaning | 18 | Full ingestion/profile/validation suites and live original/cleaned profiles, plan, downloaded data and raw checksum; coverage includes malformed and header-only files, all-null columns, mixed types, duplicate identifiers and idempotency | Verified. Inputs must satisfy configured bounds. |
| B. Retail intelligence | 14 | Normalization API, exact approved mapping tests, pinned explicit relationship tests, live category and stock/missing-data evaluation, complete Olist comparison | Verified for three alternatives. No recommendation explorer. |
| C. Conversational assistant | 20 | Full intent and memory tests; live 37 → 30 → 15 multi-turn computation, clarification, missing return inputs, joined retail trace and revision/ownership refusals | Verified for supported intents. Offline grammar is narrower than provider-assisted planning. |
| D. Safe plan execution and AI Engineering | 12 | Local controlled provider for joined retail questions, structured plan and source evidence; invalid JSON and outage retry/fallback; unsafe plan rejected with 422 and no execution; mock timeout and prompt-injection cases in suite | Verified for implemented orchestrator. External provider availability and quality were not measured in this audit. |
| E. Batch and reliability | 10 | Official command passed 2/2 cases, 5/5 chat checks and 2/2 retail checks. Tests verify join fan-out/failed-case isolation, deterministic values and artifact/trace evidence | Verified in candidate. This additional retail case support is not in the published baseline. |
| F. Security and privacy | 6 | Ownership across file, trace and session tests; live foreign owner 404, validated instructions and revisions; provider payload checks and current security suite | Verified for stated application boundaries. Static secrets, local SQLite, no built-in TLS or rate limiting, and heuristic opt-in sample redaction. |
| G. Automated tests | 5 | Published baseline 197 passed, candidate 199 passed. Two new evaluator tests; no existing assertion weakened | Verified. One existing Starlette TestClient deprecation warning in each environment. |
| H. Documentation and reasoning | 5 | Complete assessment README, exact official command, Mermaid architecture, provider/data sharing boundaries, metrics, test references, setup and walkthrough | Verified for documented paths and scope; Swagger HTML/schema checked by HTTP. |

## Source provenance and artifacts

`git ls-remote` and an actual GitHub clone confirmed `46afd54dad9bf16cfa126ef4db6537730e7d2e1b` on main. Local main, its tracking ref and the remote agreed. The seven historical commits are intact. The local release folder's existing `.gitignore` edit and untracked ZIP were left in place. The earlier ZIP omission did not affect the Git clone.

All seven Olist Git blobs matched the historical SHA-256 manifest exactly. The first working-copy hash check flagged CRLF checkout conversion on Windows. A binary Git blob check and a newline-normalized comparison verified that only the working-tree line endings differed. No accepted fixture was rewritten. The freshly recomputed enriched Olist JSON matched the accepted report in full, including source counts, category distributions, delivered merchandise, freight and payments. The checkout includes all fixtures required by the Olist reports and tests; external raw downloads are unnecessary.

## Changes and tests

- `app/evaluate.py` is the only runtime source update. Opt-in retail checks resolve indices within the current case, validate declarations through the production relationship contract, call the existing retail metric service, and capture statuses, exact expected values, errors and versioned evidence. Failed retail checks do not abort later checks or cases.
- The root `cases.json` makes the exact assessment command usable and combines two small synthetic retail CSVs with existing examples. The prior `examples/cases.json` is untouched.
- `tests/test_assessment_batch.py` adds two tests. One asserts joined category revenue, right-side revision evidence, stock-outs, a missing return metric and chat results. The other rejects fan-out and proves later checks and cases still run.
- The README adds the rubric map, actual architecture, precise AI boundary, data transfer rules, definitions, setup and verifiable API walkthrough. No provider, dashboard, agent framework or dependency was added.

## Clean installation and execution

Windows, Python **3.14.7**, fresh virtual environment from the network clone. Selected resolved versions were FastAPI 0.163.1, Starlette 1.10.0, Uvicorn 0.54.0, pandas 3.1.1, SQLAlchemy 2.1.8, Pydantic 2.14.3, python-multipart 0.0.34, HTTPX 0.28.1 and pytest 9.2.0. `pip check` reported no broken requirements. The baseline clone and its candidate source copy used this fresh environment. The existing development environment was used only for the separate targeted check.

Commands run from the relevant repository or candidate root:

```powershell
git ls-remote https://github.com/ayushverma-codes/datapilot-ai.git refs/heads/main
git clone --single-branch --branch main https://github.com/ayushverma-codes/datapilot-ai.git C:\Projects\naukrAI\assessment-clone
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
python -m pytest -q -p no:cacheprovider tests --basetemp <isolated-directory> --junitxml <evidence-file>
python -m app.evaluate --input cases.json --output results.json --artifacts-dir ./evaluation_output
python -m scripts.evaluate_conversation_memory
python -m app.retail --input-dir evaluation_output/olist/handoff/cleaned --output data/assessment-evaluators/olist.json
```

The audit used a disposable `.env` copied from `.env.example`, loaded with the README's PowerShell parser. It used generated local tokens, a relative storage directory and no development database or API key. Live Uvicorn bound to localhost on an available port. The test uploaded the two fixtures, checked schema and cleaning before and after, compared the cleaned CSV, verified unchanged raw bytes, exercised normalization and a joined retail metric, ran an offline Olist retail question, and verified both regular and joined analytical traces. A local mock HTTP planner exercised 503 retries, invalid JSON and unsafe output. The first smoke harness expected HTTP 400 for an unsafe plan; the actual contract is 422. A dedicated recheck verified the code and the trace's `NOT_STARTED` execution state. This was a smoke assertion correction, not an application change.

| Run | Result |
| --- | --- |
| Published GitHub clone | 197 passed, zero failures or skips, one Starlette deprecation warning, 149.55 s. Reproduced historical count. |
| Development targeted batch/reliability | 6 passed, one existing warning. |
| Candidate full suite | 199 passed, zero failures or skips, one warning, 135.27 s. Increase of two from new tests. |
| Candidate targeted regression | 37 passed in 33.61 s. Included assessment batch, analytical plans, memory and both real Olist comparison tests. |
| Official evaluator | 2/2 cases, 5/5 chat checks, 2/2 retail checks. |
| Historical conversation evaluator | 13/13 scenarios. |
| Olist | All seven Git blob hashes match; recomputed enriched report equals accepted JSON. |
| Uvicorn | Health, Swagger HTML/schema, profiles and cleaned artifact; owner isolation, version refusal, unsupported return-rate evidence and a traced category answer from a validated join. |

Local evidence is in `assessment-clone/data/assessment-baseline/results.xml`, `assessment-candidate/data/assessment-tests/results.xml`, `assessment-candidate/results.json`, `assessment-candidate/data/assessment-evaluators/` and `assessment-candidate/data/assessment-live/`. The generated results, credentials and logs are not part of the copy proposal.

## Proposed local release copy

The user approved the following eight files for promotion to the final workspace. The existing seven module reports, configuration files, user files and accepted fixtures needed no update. Release verification is recorded below.

| Source path | Destination path | Purpose |
| --- | --- | --- |
| `C:/Projects/naukrAI/stage6-work/app/evaluate.py` | `C:/Projects/naukrAI/datapilot-ai/app/evaluate.py` | Joined retail batch checks |
| `C:/Projects/naukrAI/stage6-work/cases.json` | `C:/Projects/naukrAI/datapilot-ai/cases.json` | Exact assessment command input |
| `C:/Projects/naukrAI/stage6-work/examples/assessment_sales.csv` | `C:/Projects/naukrAI/datapilot-ai/examples/assessment_sales.csv` | Synthetic sales inputs |
| `C:/Projects/naukrAI/stage6-work/examples/assessment_products.csv` | `C:/Projects/naukrAI/datapilot-ai/examples/assessment_products.csv` | Synthetic relationship and inventory inputs |
| `C:/Projects/naukrAI/stage6-work/tests/test_assessment_batch.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_assessment_batch.py` | Joined batch and error isolation regressions |
| `C:/Projects/naukrAI/stage6-work/release-candidate/ASSESSMENT_README.md` | `C:/Projects/naukrAI/datapilot-ai/README.md` | Technical evaluator documentation |
| `C:/Projects/naukrAI/stage6-work/release-candidate/Reports/FINAL_ASSESSMENT_GAP_AUDIT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/FINAL_ASSESSMENT_GAP_AUDIT.md` | Baseline and requirements audit |
| `C:/Projects/naukrAI/stage6-work/release-candidate/Reports/FINAL_ASSESSMENT_VERIFICATION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/FINAL_ASSESSMENT_VERIFICATION_REPORT.md` | Reproducible results and exact copy list |

## Final demo and submission folder

The eight approved files, including the shorter presentation README, were copied to `C:/Projects/naukrAI/datapilot-ai`. All eight destination SHA-256 hashes matched their sources. The existing `.gitignore` edit was preserved. The seven accepted Olist fixtures remained unchanged.

| Check executed from the final folder | Result |
| --- | --- |
| Requirements and `pip check` | Existing environment satisfied the requirements. No broken dependencies. |
| Full suite | **199 passed** in 140.86 seconds, zero failures or skips, and one existing Starlette TestClient deprecation warning. |
| Evaluators | Batch **2/2 cases, 5/5 chat checks, 2/2 retail checks**; historical conversation **13/13 scenarios**. |
| Olist | The recomputed enriched report matched the entire accepted JSON. |
| Live README workflow | Uvicorn and Swagger schema, authenticated upload, profiling, clean download, raw byte preservation, joined category revenue 27 and 10, explicit missing return metric, owner isolation, and verified conversational values 37 → 30 → 15. |

The full-suite JUnit report is in `evaluation_output/assessment-release-75c8ce9b/tests.xml`, the evaluator output is under `evaluation_output/assessment-eval-4d0d5b80/`, and the live log is under `evaluation_output/assessment-http-d6924c1e/`. These local evidence files are ignored. No commit or push was made.
