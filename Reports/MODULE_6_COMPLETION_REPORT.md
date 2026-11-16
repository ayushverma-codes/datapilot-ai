# Module 6 — Multi-Turn Conversational Memory

## Requirements and disposition

| Requirement | Implementation and verification |
| --- | --- |
| User and session identity, run, dataset and version | The existing owned conversation is the session. Its verified analysis records the run, file, cleaned revision, artifact and profile IDs. HTTP coverage checks session and trace ownership and a re-clean revision conflict. |
| Structured memory and persistence | The existing SQLAlchemy conversation JSON stores the validated plan and bounded verified result. The plan stores filters, grouping, metrics, sort and limit. The session retains user and assistant messages with trace references. Tests reopen the application on the same SQLite database and repeat the analysis. |
| Follow-up resolution | Explicit edits add, replace or remove filters, rerank, change aggregate metrics, repeat, or compare two values. A reference such as “that region” resolves from one verified value or asks for clarification. A new dataset requires a new grounded analysis. |
| Traceability | The assistant trace records the previous plan and trace ID for edits, the context operation, the resulting validated plan, source revisions and executed evidence. History and traces expose which context changed on each turn. |
| Failure and isolation | Clarifications, unsupported results and execution errors preserve the last successful analysis. Concurrent changes cause a conflict rather than overwriting a newer turn. Tests check another account, a different session of the same account, an ambiguous reference and a stale revision. |

## Existing functionality reused

Reviewed both decision logs, the historical conversation memory implementation report, Module 5 completion and release verification, SQLAlchemy models and migration, conversation service and fixtures, Module 5 assistant and tests, the legacy and newer API routes, and the cleaning and approval code used during the demo.

The legacy evaluator runs 13 cases through the historical conversation message endpoint. Four are Olist continuity cases (October to November, comparison of those periods, expanding the category ranking, evidence limits on reconciliation). Three exercise generic CSV ranking, filter addition and scalar comparison. One is an independent Olist metric change. The remaining five cover an ambiguous reference, missing context, unrelated greeting, dataset switch and preservation after a failed question. The evaluator checks case-specific classifications, metrics, periods, ranking sizes, filters, selected sources, computed values and context integrity. It does not exercise the newer assistant endpoint's persisted plans and traces, version conflicts, full filter edit sequence, account isolation, restart recovery, or concurrent writes. Separate legacy tests already cover ownership, restart, migration and concurrency.

Module 5 could read the legacy conversation context but could not persist its own turns. Module 6 adds that capability to `POST /api/v1/assistant/query` when it receives a conversation ID. It reuses the conversation tables, identity checks, verified assistant tools, relationship registry and provider. No changes were needed to the evaluation fixtures, older conversation endpoints or accepted Olist calculations.

## Implementation and demo fixes

- Each successful turn updates state using the conversation context version and persists both messages and the assistant trace in one transaction. A conflicting turn returns HTTP 409 and retains its failed trace. The previous revision is pinned on implicit follow-ups. Explicitly select a new revision and give a new question to resume after re-cleaning. A stored join goes through the same relationship and grain checks when reused.
- The rules edit a previously validated plan, then run it through the normal backend validation, execution and result checks. The rules use exact schema fields, explicit filter literals and uniquely resolved stored entities. Unsupported wording requests clarification. First turns and standalone questions retain Module 5's optional provider.
- FastAPI now describes CSV uploads as an array of binary multipart files and exposes configured Bearer authentication through Swagger's Authorize control. The existing token checks and local development mode are preserved. Tests verified the OpenAPI schema and the docs route.
- Automatic suggestions for new cleaning plans use trimmed keys and targets and skip groups with common encoding corruption markers. They preserve lexical selection after trimming and still require approval. The module does not guess a translation or rewrite historical CSVs or cleaning plans. New validation reports include per-step status and changed row counts so an identity conversion is distinguishable from a value change. CSV storage types remain strings.

## Verification

The final full development suite passed **193 tests**, with zero failures or skips and one existing Starlette TestClient deprecation warning, in 128.72 seconds. The suite includes all six new tests in `tests/test_module6_memory.py`, the historical evaluator (13/13), legacy conversation tests, assistant planning and joins, approval and cleaning regressions, and the accepted Olist analytics checks. The command was `python -m pytest -q -p no:cacheprovider tests --basetemp <unique-test-directory> --junitxml <evidence-file>`, using the development virtual environment. Evidence is preserved in `stage6-work/evaluation_output/module6-final-e7f584d2.xml`. Earlier full runs have separate evidence files.

- The first full run exposed three regression failures in existing cleaning, API and preview tests because the revised canonicalization preferred lowercase over the prior lexical choice. Restored lexical selection among trimmed spellings. A focused run also exposed that SQLAlchemy flushed the trace before the after-version field was added; the trace is now added after recording the conversation update. Historical verification files were preserved.
- A live GPT OSS 120B smoke test used synthetic schema and question data and one provider attempt. Groq produced an accepted plan for a total of 37. A local follow-up retained the dataset and filtered to a total of 30, with a verified trace and context version 2. The initial sandboxed request returned a provider error and the permitted network run passed. No dataset rows were sent to the provider.
- The Olist review CSV was ingested separately in development. Both date operations changed 99,224 rows each. Numeric conversion completed with zero changes because scores were already in canonical form. The new category candidates remained pending and targets had no edge whitespace or detected corruption. Validation and idempotency passed. All 99,224 rows and 7 columns remained. The original file SHA-256 stayed `012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7`.
- `python -m pip check` reported no broken requirements. The new runtime uses existing dependencies and configured storage paths.

## Files and limitations

The manifest lists all created or modified release files. The resolver, test file, completion report and README candidate were created in development. The development decision log is updated separately. No files were deleted.

Deterministic follow-up wording is intentionally limited to supported changes. A comparison needs one explicit or unambiguous field. Free-form references without enough evidence ask for clarification. A legacy conversation can still supply its earlier simple aggregate context to the newer assistant; use the same endpoint throughout a demo to retain the newer plan and trace history. Existing stored cleaning plans need separate review or a fresh upload to receive updated suggestions. Suspicious source text remains available for human review. No PostgreSQL integration run was performed.

Future work remains semantic memory, summarization, context compression, multiple analytical threads and cross-session preferences. No vector database or new dependency was introduced.

## Proposed release-copy manifest

Only these files are proposed for the demo workspace. Runtime data, local secrets, virtual environments, local databases and verification evidence are excluded. The existing Git ignore rules already cover the local dataset and runtime paths. Release testing from the demo folder follows approval.

| File | Purpose | Source path | Destination path |
| --- | --- | --- | --- |
| `app/conversation/followup.py` | Resolve deterministic edits and references against a verified plan. | `C:/Projects/naukrAI/stage6-work/app/conversation/followup.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/followup.py` |
| `app/conversation/memory_contracts.py` | Validate the persisted extended analysis context. | `C:/Projects/naukrAI/stage6-work/app/conversation/memory_contracts.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/memory_contracts.py` |
| `app/conversation/memory.py` | Save versioned state and trace-linked message pairs. | `C:/Projects/naukrAI/stage6-work/app/conversation/memory.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/memory.py` |
| `app/conversation/analysis_planner.py` | Support the schema-grounded initial grouping question offline. | `C:/Projects/naukrAI/stage6-work/app/conversation/analysis_planner.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/analysis_planner.py` |
| `app/services/assistant_service.py` | Execute and persist versioned assistant turns and trace context edits. | `C:/Projects/naukrAI/stage6-work/app/services/assistant_service.py` | `C:/Projects/naukrAI/datapilot-ai/app/services/assistant_service.py` |
| `app/main.py` | Document multipart uploads and bearer authentication in Swagger. | `C:/Projects/naukrAI/stage6-work/app/main.py` | `C:/Projects/naukrAI/datapilot-ai/app/main.py` |
| `app/cleaning/plan_generator.py` | Prevent malformed automatic category targets while preserving selective approval. | `C:/Projects/naukrAI/stage6-work/app/cleaning/plan_generator.py` | `C:/Projects/naukrAI/datapilot-ai/app/cleaning/plan_generator.py` |
| `app/services/ingestion_service.py` | Include per-operation evidence in new cleaning validation reports. | `C:/Projects/naukrAI/stage6-work/app/services/ingestion_service.py` | `C:/Projects/naukrAI/datapilot-ai/app/services/ingestion_service.py` |
| `tests/test_module6_memory.py` | Reproduce the new workflow, isolation, restart, concurrency and demo defect checks. | `C:/Projects/naukrAI/stage6-work/tests/test_module6_memory.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_module6_memory.py` |
| `README.md` | Give reviewers the Swagger and multi-turn demo steps. | `C:/Projects/naukrAI/stage6-work/release-candidate/README.md` | `C:/Projects/naukrAI/datapilot-ai/README.md` |
| `Reports/MODULE_6_COMPLETION_REPORT.md` | Record requirements, results and this manifest. | `C:/Projects/naukrAI/MODULE_6_COMPLETION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/MODULE_6_COMPLETION_REPORT.md` |

## Release verification after approval

The user approved the 11 listed destination files. Source and destination SHA-256 hashes matched. The release virtual environment satisfied `requirements.txt` and `pip check` found no broken requirements. From the release directory, **193 tests passed** in 131.07 seconds, with one existing Starlette TestClient deprecation warning and zero failures or skips. Local JUnit evidence is in `evaluation_output/module6-release-2b6f6575.xml`.

A live Uvicorn smoke check from the release directory used an isolated relative storage directory. It verified health, the Swagger HTML and OpenAPI upload/auth schemas, multipart upload with different schemas, cleanup reports, a conversation continuing after a server process restart, the prior trace reference, and account isolation. An attempted visual browser check timed out before rendering, so the picker was verified at the OpenAPI level only.

The Git change list matched the approved manifest. No new local CSVs, runtime databases, credentials, virtual environment files, or verification evidence were added. Seven previously tracked Olist regression fixtures under `evaluation_output/` were retained. Git metadata was preserved. Git review, commit and push remain with the user.
