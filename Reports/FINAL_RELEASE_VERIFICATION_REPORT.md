# DataPilot AI — Final Release Verification

## Verification environment and payload

The proposed release was assembled in an isolated folder, `C:/Projects/naukrAI/module7-clean`. The verified runtime and test payload comprised 87 files. The assembly started with the tracked release tree, added the proposed Module 7 overlays and compared SHA-256 for every copied file. The two new reports are documentation additions to that candidate.

The clean environment used Windows, Python 3.14.7, a newly created virtual environment, and a new SQLite database under relative storage. The first dependency installation attempt was blocked by the sandbox's network restriction. A permitted installation into the fresh environment succeeded from the unchanged requirements file. It resolved FastAPI 0.163.1, Starlette 1.10.0, Uvicorn 0.54.0, SQLAlchemy 2.1.8, pandas 3.1.1, Pydantic 2.14.3, python-multipart 0.0.34, HTTPX 0.28.1 and pytest 9.2.0. The development virtual environment's installed packages were not reused. The clean interpreter passed `pip check`.

The seven tracked Olist fixtures were retained and checked against the historical Stage 4 SHA-256 values, not merely the copy source. The candidate included no live `.env`, development virtual environment, existing local SQLite state, raw demo datasets, historical execution logs, or Git metadata. AI was disabled and no credential was needed to evaluate the candidate. The process ran without `DATABASE_URL` or `PYTHONPATH` overrides. No startup, test or evaluator command needed a development source path.

## Actual results

| Check | Result |
| --- | --- |
| Fresh install and dependency consistency | PASS. `python -m pip check` reported no broken requirements. |
| Full suite | PASS. 197 passed, zero failures or skips, and one existing Starlette TestClient deprecation warning in 139.52 seconds. |
| Batch cleaning and conversational evaluator | PASS. 2/2 cases and 5/5 chat checks. Output includes file evidence, after profiles, validation, structured plans, and per-turn traces. |
| Historical conversation evaluator | PASS. 13/13 cases. |
| Accepted Olist regression | PASS. All seven fixture hashes matched. Recomputed enriched report matched the full accepted JSON structurally, including financial totals and grouping. |
| Uvicorn and SQLite bootstrap | PASS. Started from the candidate with relative storage, no prior database and a generated local bearer token. Health, Swagger HTML and OpenAPI authentication were verified over HTTP. |
| Sample workflow and owner boundary | PASS. Mixed multipart upload, a child file failure, idempotent run replay, unchanged raw bytes, successful clean, wrong-user download rejection, computed revenue 37 and West follow-up 30, with a verified trace. |

## Reproducing the checks

Activate a fresh environment from the release root, use the configuration and startup instructions in the README, and run the following commands. Use a new output directory for each run.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.evaluate --input examples/cases.json --output data/module7/batch.json --artifacts-dir data/module7/artifacts
.\.venv\Scripts\python.exe -m scripts.evaluate_conversation_memory
.\.venv\Scripts\python.exe -m app.retail --input-dir evaluation_output/olist/handoff/cleaned --output data/module7/olist-recomputed.json
```

The isolated candidate's `data/module7-tests.xml`, `data/evaluators/`, `data/live-smoke/uvicorn.log` and dependency freeze are local verification evidence. They are excluded from the copy. Test coverage is not a formal security assessment. Python 3.11, PostgreSQL, and any hosted deployment remain outside the verified environment.

## Verification from the final demo folder

The user approved the ten files in the [Module 7 completion report](MODULE_7_COMPLETION_REPORT.md). Only those destinations were updated in `C:/Projects/naukrAI/datapilot-ai`. All 89 files in the candidate inventory matched immediately after copying. The README line endings were then normalized to LF for Git, and this report was updated with the release results. Final source and destination hashes were verified again for the ten approved files. The existing local `.gitignore` edit and Git metadata were preserved.

- The release virtual environment satisfied `requirements.txt`, and `pip check` found no broken requirements. The fresh installation was verified separately in the clean environment above.
- The full suite passed **197 tests** in 146.21 seconds, with zero failures or skips and one existing Starlette TestClient deprecation warning. JUnit evidence is in `evaluation_output/module7-release-704e41bb/tests.xml`.
- The batch evaluator passed **2/2 cases and 5/5 chat checks**, the historical conversation evaluator passed **13/13 scenarios**, and the full recomputed Olist output matched the accepted enriched report. Evidence is in `evaluation_output/module7-eval-4f9d6329/`.
- A new Uvicorn process started using relative storage and generated test credentials. The HTTP check verified the docs, an unauthorized 401 and a real slow-body 408, mixed uploads, idempotent replay, raw file integrity, cleaning, ownership and a verified analytical follow-up from 37 to 30. Logs are in `evaluation_output/module7-http-c67045b9/`.

The local secret, virtual environment, database and new evidence paths were confirmed ignored. The seven existing Olist regression fixtures remain unchanged. No commit or push was performed.
