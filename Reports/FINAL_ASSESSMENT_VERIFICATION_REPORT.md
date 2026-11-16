# Final Naukr.AI assessment verification

## Readiness

The original readiness pass reproduced 197 tests from a fresh network clone at `46afd54`, then verified the joined-retail batch addition. Local tracked baseline `b888e83` contains that addition. The current pass updates scoring and examples and independently reproduces 199 tests. No commit or push was performed for the current pass.

The nine official weights below were checked against page 5 of the assignment PDF and total 100. They are rubric weights, not awarded or predicted scores.

| Assessment category | Points | Implemented functionality | Source evidence | Test/evaluation evidence | Status |
| --- | ---: | --- | --- | --- | --- |
| Data profiling + cleaning correctness | 18 | Profiles CSVs and detects issues before an approval plan; validates row/schema deltas and keeps original bytes. | [ingestion_service.py](../app/services/ingestion_service.py) | [Evidence](../tests/test_cleaning.py) | Verified. Pending mappings are skipped; validation does not infer business meaning. |
| Retail-domain intelligence | 14 | Implements normalization, explicit safe relationships, and sales/inventory metrics. | [retail_engine.py](../app/retail_engine.py) | [Evidence](../tests/test_retail_uploaded.py) | Verified. No recommendation explorer; missing inputs return evidence instead of estimates. |
| Chat assistant grounding + multi-turn reasoning | 20 | Uses scoped dataset versions, computed answers and persisted conversation traces; continues filters across turns. | [assistant_service.py](../app/services/assistant_service.py) | [Evidence](../tests/test_module6_memory.py) | Verified for supported intents. Offline wording is narrower than model-assisted planning. |
| Safe plan execution + AI engineering | 12 | Validates typed plans, orchestrates deterministic tools, and bounds provider retries and fallbacks. | [analysis_contracts.py](../app/conversation/analysis_contracts.py) | [Evidence](../tests/test_assistant_analysis.py) | Verified with mock providers and offline tests; no external provider quality claim. |
| Batch evaluator + reliability | 10 | Runs cleaning, joined retail and chat through production services and continues after failed cases. | [evaluate.py](../app/evaluate.py) | [Evidence](../tests/test_assessment_batch.py) | Verified. IDs and timestamps differ between runs. |
| Architecture + code quality | 10 | Separates API, services, contracts, deterministic engines and persistence. API and batch reuse services; structured errors cross the boundary. | [main.py](../app/main.py) | [Evidence](../tests/test_api_evaluation.py) | Verified design paths. In-process processing and single-worker SQLite limit concurrency. |
| Security / privacy / data isolation | 6 | Authenticates before body parsing, enforces ownership and input/query limits, and minimizes model payloads. | [analysis_planner.py](../app/conversation/analysis_planner.py) | [Evidence](../tests/test_module7_reliability.py) | Verified for local deployment. Static tokens and heuristic opt-in sample redaction. |
| Automated tests | 5 | Covers profiling, mutations, integrity, safe joins, chat context, refusals and provider failure. | [conftest.py](../tests/conftest.py) | [Evidence](../tests/) | Verified. No production load or security certification. |
| README + technical reasoning | 5 | Documents setup, validated examples, AI boundaries and trade-offs. | [README.md](../README.md) | [Evidence](../Reports/ASSESSMENT_EXAMPLE_VALIDATION.md) | Verified walkthrough commands and local links. The video is a separate submission item. |
| **TOTAL** | **100** | | | | |

## Source provenance and artifacts

`git ls-remote` and an actual GitHub clone confirmed `46afd54dad9bf16cfa126ef4db6537730e7d2e1b` on main. Local main, its tracking ref and the remote agreed. The seven historical commits are intact. The local release folder's existing `.gitignore` edit and untracked ZIP were left in place. The earlier ZIP omission did not affect the Git clone.

All seven Olist Git blobs matched the historical SHA-256 manifest exactly. The first working-copy hash check flagged CRLF checkout conversion on Windows. A binary Git blob check and a newline-normalized comparison verified that only the working-tree line endings differed. No accepted fixture was rewritten. The freshly recomputed enriched Olist JSON matched the accepted report in full, including source counts, category distributions, delivered merchandise, freight and payments. The checkout includes all fixtures required by the Olist reports and tests; external raw downloads are unnecessary.

## Previous assessment release changes and tests

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

## Current rubric and walkthrough correction

The original assignment PDF (DQ-AI-CHAT-03, pages 2–5) confirms a separate **10-point Architecture + code quality** category. The README and assessment reports now include it. See the [gap audit](FINAL_ASSESSMENT_GAP_AUDIT.md) for the application/service, contracts and persistence rationale, and [example validation](ASSESSMENT_EXAMPLE_VALIDATION.md) for the current run and concrete Swagger workflow. This correction changes documentation only. The existing implementation, tests and accepted Olist fixtures were reused.

| Current validation | Result |
| --- | --- |
| Full suite | **199 passed**, zero failures or skips, one existing Starlette TestClient deprecation warning, 146.43 s. |
| Targeted examples | **67 passed** for cleaning, retail, assistant, memory and batch; **2 passed** for Olist; **1 passed** for mocked provider outage. These selections overlap the full suite. |
| Batch and conversation | **2/2 batch cases, 5/5 chat checks, 2/2 retail checks, 13/13 conversation scenarios**; entire recomputed Olist JSON matched. |
| Setup and API | README environment loader, Uvicorn startup on port 8000, health, Swagger HTML and OpenAPI verified. Authenticated examples asserted against the production handlers. |
| Documentation | Exact nine-row 100-point matrices, local Markdown links, heading anchors and Mermaid flowchart syntax checked. |

The initial targeted run hit Windows permissions errors when pytest used its default temporary directory. A retry using fresh workspace-local `--basetemp` paths passed. The README uses that working command. The current pass did not alter application code or tests.
