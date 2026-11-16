# Module 3 — AI-Assisted Data Cleaning

## Scope and requirements

Module 3 consumes a registered original CSV, its Module 2 profile and issues, and the configured cleaning policy. It persists the plan and approval decisions before executing registered Python transformations. The outputs include an immutable cleaned CSV, execution logs, the new profile and validation report, and the remaining quality issues. Download requires a passing validation report.

| Requirement | Existing behavior reused and changes made | Verification |
| --- | --- | --- |
| A. Plan before mutation | Reused stored cleaning plans and their step contract. New generated step IDs are stable for equivalent operation, scope, parameters, source and risk on the same file. Existing stored IDs remain valid. Steps carry available related issue IDs and include reason, scope, parameters, risk, source and status. | `test_generated_ids_and_revision_artifacts`, `test_persistence_and_recovery` |
| B. AI-assisted review | Reused the OpenAI-compatible analyzer and Pydantic plus registry validation. Added bounded column interpretations, issue-linked suggestions and rejection of mappings without exact visible evidence. Code and SQL have no operation in the registry. AI explains its recommendations in the step reason. | `test_bad_ai_falls_back`, `test_groq_semantic_and_category_response`, live Groq check |
| C. Selective approval | Reused automatic approval for low-risk code operations, explicit review for medium risk, the existing high-risk block and approval history. Risk overrides apply to user proposals as well. Execution logs report the authorization state. | `test_pending_rejected_approved`, `test_approved_mapping_and_imputation`, `test_high_risk_blocked`, `test_batch_approval_atomicity` |
| D. Execution | Reused the registry for trim, string case, exact duplicate and fully empty row removal, Decimal and date parsing, approved category mappings and explicit missing-value fills, and outlier flags. Immutable files are published after writing and flushing a sibling temporary file. Numeric output expansion is bounded. | `test_date_and_numeric_transforms`, `test_numeric_expansion_is_bounded`, `test_outlier_flagging_preserves_values`, `test_invalid_operations`, `test_immutable_artifact_is_published_complete_and_never_replaced` |
| E. Validation | Reused Module 2 profiles, raw SHA-256 verification, CSV round-trip validation, explicit row removal predicates, schema and scope checks, and idempotency replay. Reports now show null totals for each column on both sides. Later revisions replay previously authorized failures so they cannot silently disappear. | `test_generated_ids_and_revision_artifacts`, `test_failed_operation_cannot_disappear_from_later_revision`, `test_approval_after_safe_revision_is_reproducible`, `tests/test_validation.py` |
| F. AI reliability | Preserved disabled mode and deterministic fallback. Added structured provider, rate limit, timeout and response errors. Requests send complete JSON with a metadata budget of 3,200 bytes and cap completion at 1,200 tokens. Default timeout is 15 seconds and the default is two attempts. HTTP 429 and other permanent HTTP 4xx statuses stop retrying. | `test_disabled`, `test_ai_timeout_and_valid_response`, `test_unavailable_ai_pipeline`, `test_groq_budget_and_rate_limit`, live Groq check |

## Development and verification

The final development run passed **147 tests** with zero failures and one existing Starlette TestClient deprecation warning. The command was `python -m pytest -q -p no:cacheprovider tests --basetemp <isolated-test-folder> --junitxml <evidence-file>`. Dependency verification with `python -m pip check` passed.

Production and test files modified are listed in the release manifest. This report was created. The development decision ledger records the preserved approval policy and changes. No files were deleted. Historical evidence was retained.

The suite runs with AI disabled by default and replaces the provider with controlled HTTP responses in AI tests. The full run also exercises the HTTP upload and cleaning workflow, access isolation, existing Module 1 and 2 functionality, and the real Olist regression. The first focused run exposed Windows path failures caused by long temporary filenames for atomic publication. Short sibling filenames and a Windows rename that refuses an existing destination addressed that regression. The next full run passed 145 tests before the final risk override and numeric and outlier checks were added. A subsequent run passed 146 tests and failed the new outlier check. The fixture used entirely unique whole numbers, which trigger the existing identifier heuristic. The fixture was corrected to exercise decimal measurements.

Live development checks used the existing local credential and a synthetic CSV with category variants, a numeric column and dates. Both requested models returned three valid column interpretations. GPT-OSS returned four accepted suggestions, including a category mapping that passed validation after explicit approval. Qwen returned three accepted suggestions, covering string, date and numeric normalization. Both allowed deterministic cleaning while the AI steps were pending and preserved the original bytes. One invalid provider response was handled by the bounded retry. The mock tests cover limit and failure conditions without consuming provider quota.

## Groq demo setup and limits

For an optional Groq run, set `AI_ENABLED=true`, `AI_PROVIDER=openai-compatible`, `AI_BASE_URL=https://api.groq.com/openai/v1`, and `AI_MODEL=openai/gpt-oss-120b` or `qwen/qwen3.8-27b`. Load the `AI_API_KEY` entry from the local ignored `.env` into the app process environment. The existing settings read process environment variables. The default deterministic run works with `AI_ENABLED=false`.

Start the application from the project root after configuring the environment. The PowerShell example assumes a local `.env` and the virtual environment described in the README.

```powershell
$keyEntry = Get-Content -LiteralPath .env | Where-Object { $_ -match '^AI_API_KEY=' } | Select-Object -First 1
if (-not $keyEntry) { throw 'Add AI_API_KEY to your local .env file first.' }
$env:AI_API_KEY = $keyEntry.Substring('AI_API_KEY='.Length)
$env:AI_ENABLED = 'true'
$env:AI_PROVIDER = 'openai-compatible'
$env:AI_BASE_URL = 'https://api.groq.com/openai/v1'
$env:AI_MODEL = 'openai/gpt-oss-120b'
$env:AI_INCLUDE_SAMPLES = 'false'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Groq lists free-tier limits of **30 requests/minute, 1,000 requests/day, 8,000 tokens/minute and 200,000 tokens/day** for each requested model. Organization quotas can differ. Leave sample sharing disabled for sensitive inputs. The provider receives column names even when sample sharing is off. When sample sharing is explicitly enabled, the analyzer limits the number and length of samples and applies heuristic redaction. Exact category mapping suggestions require visible, unredacted examples. See [Groq rate limits](https://console.groq.com/docs/rate-limits) and [Groq JSON and reasoning settings](https://console.groq.com/docs/reasoning).

## Limitations and future work

Wide files can exceed the AI metadata budget and are reviewed deterministically across their full schema. An approval authorizes the specified operation and parameters only. A registered transform that fails remains in the validation report, and the affected revision cannot be downloaded. The cleaned artifact is a CSV and type comparisons report the inferred types. Individual files are published atomically on a filesystem that supports the required rename or link operation. The filesystem and database do not share a transaction, so interruption before the metadata commit can leave an unreferenced artifact.

Future work remains the approval dashboard, rollback UI, learned policies, confidence calibration and recommendations based on historical approvals.

## Proposed release copy manifest

Each file is required by the existing cleaning path, its verification suite, or the module report. The update uses existing dependencies and excludes runtime databases, credentials, virtual environments, large datasets and historical verification evidence. The destination report belongs under `Reports`. After approval, verify source/destination hashes, dependencies, the full suite and a running application from the final `datapilot-ai` folder.

| File | Purpose | Source path | Destination path |
| --- | --- | --- | --- |
| `app/ai/analyzer.py` | Bound and validate AI requests and responses, with Groq settings and structured fallback. | `C:/Projects/naukrAI/stage6-work/app/ai/analyzer.py` | `C:/Projects/naukrAI/datapilot-ai/app/ai/analyzer.py` |
| `app/models/contracts.py` | Validate structured suggestions and column interpretations. | `C:/Projects/naukrAI/stage6-work/app/models/contracts.py` | `C:/Projects/naukrAI/datapilot-ai/app/models/contracts.py` |
| `app/cleaning/plan_generator.py` | Generate stable step IDs, attach related issues, and require review for AI. | `C:/Projects/naukrAI/stage6-work/app/cleaning/plan_generator.py` | `C:/Projects/naukrAI/datapilot-ai/app/cleaning/plan_generator.py` |
| `app/cleaning/executor.py` | Record exact value changes and distinguish a missing cell from literal text. | `C:/Projects/naukrAI/stage6-work/app/cleaning/executor.py` | `C:/Projects/naukrAI/datapilot-ai/app/cleaning/executor.py` |
| `app/cleaning/transformation_registry.py` | Bound canonical decimal expansion for extreme inputs. | `C:/Projects/naukrAI/stage6-work/app/cleaning/transformation_registry.py` | `C:/Projects/naukrAI/datapilot-ai/app/cleaning/transformation_registry.py` |
| `app/services/ingestion_service.py` | Expose AI outcomes, preserve failures across revisions, and record authorization. | `C:/Projects/naukrAI/stage6-work/app/services/ingestion_service.py` | `C:/Projects/naukrAI/datapilot-ai/app/services/ingestion_service.py` |
| `app/storage/file_storage.py` | Publish complete immutable artifacts without replacing existing files. | `C:/Projects/naukrAI/stage6-work/app/storage/file_storage.py` | `C:/Projects/naukrAI/datapilot-ai/app/storage/file_storage.py` |
| `app/validation/validator.py` | Include per-column null counts in before and after reports. | `C:/Projects/naukrAI/stage6-work/app/validation/validator.py` | `C:/Projects/naukrAI/datapilot-ai/app/validation/validator.py` |
| `tests/test_ai.py` | Verify response validation, privacy defaults, Groq request limits and fallback. | `C:/Projects/naukrAI/stage6-work/tests/test_ai.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_ai.py` |
| `tests/test_cleaning.py` | Verify authorization, stable IDs, immutable artifacts, validation and edge cases. | `C:/Projects/naukrAI/stage6-work/tests/test_cleaning.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_cleaning.py` |
| `Reports/MODULE_3_COMPLETION_REPORT.md` | Document assignment coverage, verification and release steps. | `C:/Projects/naukrAI/MODULE_3_COMPLETION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/MODULE_3_COMPLETION_REPORT.md` |

## Final demo verification

The user approved the 11-file copy. Every destination SHA-256 matched its source. A fresh local virtual environment installed the release's existing requirements, and `pip check` passed. The full suite ran from `datapilot-ai` using isolated runtime storage and passed **147 tests** with one existing Starlette TestClient deprecation warning. It included the real Olist regression.

Uvicorn served the release application on loopback. HTTP checks passed with AI disabled and again with live Groq GPT-OSS 120B using synthetic input. The live run returned four validated suggestions. Both checks covered health and docs, multipart upload, pending and approved steps, ownership checks, idempotent cleaning, profile revision history, validation, download and original artifact integrity.

The existing Git ignore rules cover the local virtual environment, environment file, databases and new runtime outputs. No dataset files were included in this update. Git staging, commit and push are separate user actions.
