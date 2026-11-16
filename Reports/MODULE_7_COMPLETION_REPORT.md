# Module 7 — Reliability, Security and Evaluation

## Audit and requirements

Reviewed the development and root decision history, Module 6 implementation and release evidence, the historical Stage 4 security and recovery reports, Stage 5 preflight and packaging evidence, the 125-test baseline inventory, and the current tests. Inspected the batch and conversation evaluators, Olist fixtures and regression, request routing and authentication, AI transports, storage, SQLAlchemy transactions, recovery, logs and environment files. The current release baseline had 193 tests. The historic baseline describes earlier coverage and was not used as evidence that the current candidate passed. No Docker configuration was present or required.

| Requirement | Existing coverage and Module 7 work |
| --- | --- |
| Request errors, deadlines and limits | Existing byte, row, column and cell limits were retained. Module 7 authenticates protected requests before their body is read, bounds body receive time and multipart file and field counts, and returns sanitized structured 401, 408, 422 and 500 responses. The deadline covers receipt of the body, not cancellation of synchronous work. |
| Provider reliability and minimization | Reused existing network timeouts, bounded retries, validated responses, bounded schema prompts and deterministic fallback. Existing tests exercise unavailable providers, malformed plans, schema grounding, refusal and prompt injection from dataset content. No new inference was required for the offline release evaluators. |
| Storage safety and transaction handling | Reused immutable raw artifacts, atomic writes, the approval transaction, artifact/report revisions, recovery and idempotency. Cleaning failures now log the file ID and error class rather than exception text. The regression suite and live smoke test check failure isolation and raw integrity. |
| Authorization and input security | Reused ownership checks and file and path validation. Early bearer authorization protects upload parsing; wrong-account reads stay 404. Settings omit secrets from their representation. Batch fixture paths must stay within the case directory. |
| Safe, grounded execution | Reused the normal cleaning and assistant services, operation allowlists, deterministic tool execution and verified trace storage. No raw query or generated Python is executed. |
| Batch evaluation | Extended the original CLI and preserved its cleaning report. New evidence includes the dataset, before and after profiles, issues, cleaning plan, validation, chat evaluation and relative artifact paths. Chat expectations accept statuses and optional exact scalar values. Cases and individual cleaning and chat failures are isolated. |
| Reproducibility and documentation | The README covers install, environment, security, provider configuration, model data exposure, API, evaluation and limitations. The sample environment contains placeholders and disabled AI defaults. The clean candidate uses the tracked release baseline and only the proposed overlays. |

## Tests and evidence

- Development focused suite: `tests/test_module7_reliability.py` and `tests/test_api_evaluation.py`, **9 passed**. New cases exercise rejection before any body read, a malformed bearer header, a stalled body, multipart resource limits, sanitized malformed JSON/validation/internal errors, a rejected external fixture path, continuation after a failed upload or chat turn, relative artifact references, raw integrity and repeatable scalar results.
- See [final release verification](FINAL_RELEASE_VERIFICATION_REPORT.md) for the clean environment, full suite, evaluator results and live workflow.

## Limitations and future work

API tokens are static operator-managed shared secrets. This release has no account service, token expiration, rate limiting or built-in TLS. `DEV_MODE` is for local use and defaults to false. The server is intended for one local worker with SQLite. API timeouts cover slow request bodies, while provider timeouts and request and processing bounds limit other work. A client disconnect does not undo a committed request. Spreadsheet formula text is preserved in CSV data. Optional sample redaction is heuristic, and questions or schema names may contain sensitive content. Random IDs and timestamps vary between evaluation runs. No infrastructure security audit, PostgreSQL validation, CI/CD, load testing, distributed processing, new observability stack or deployment was added.

No source or historical evidence files were deleted. The application updates use existing dependencies. The only new test module is `tests/test_module7_reliability.py`. New reports and the README candidate are staged in development. Release-wide evidence and local runtime files stay outside the tracked update.

## Proposed release-copy manifest

The following table is the entire requested copy. The release retains its existing Git metadata, source, tests, documentation and seven Olist regression fixtures. The proposal introduces no new datasets, databases, secrets, environments or historical verification files.

| File | Purpose | Source path | Destination path |
| --- | --- | --- | --- |
| `app/config.py` | Configure request receive time and multipart resource caps; protect secret representations. | `C:/Projects/naukrAI/stage6-work/app/config.py` | `C:/Projects/naukrAI/datapilot-ai/app/config.py` |
| `app/main.py` | Enforce early bearer authorization and body limits, and sanitize HTTP errors. | `C:/Projects/naukrAI/stage6-work/app/main.py` | `C:/Projects/naukrAI/datapilot-ai/app/main.py` |
| `app/evaluate.py` | Evaluate cleaning and chat with production services and retain artifact and trace evidence. | `C:/Projects/naukrAI/stage6-work/app/evaluate.py` | `C:/Projects/naukrAI/datapilot-ai/app/evaluate.py` |
| `app/services/ingestion_service.py` | Log cleaning failure types without including exception text. | `C:/Projects/naukrAI/stage6-work/app/services/ingestion_service.py` | `C:/Projects/naukrAI/datapilot-ai/app/services/ingestion_service.py` |
| `tests/test_module7_reliability.py` | Reproduce the new reliability, security and evaluator acceptance cases. | `C:/Projects/naukrAI/stage6-work/tests/test_module7_reliability.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_module7_reliability.py` |
| `examples/cases.json` | Demonstrate cleaning and a five-turn grounded chat evaluation. | `C:/Projects/naukrAI/stage6-work/examples/cases.json` | `C:/Projects/naukrAI/datapilot-ai/examples/cases.json` |
| `.env.example` | Document the new limits with placeholder-only credentials. | `C:/Projects/naukrAI/stage6-work/.env.example` | `C:/Projects/naukrAI/datapilot-ai/.env.example` |
| `README.md` | Document operating boundaries, model configuration and reproducible evaluation. | `C:/Projects/naukrAI/stage6-work/release-candidate/README.md` | `C:/Projects/naukrAI/datapilot-ai/README.md` |
| `Reports/MODULE_7_COMPLETION_REPORT.md` | Record the audit, requirements, tests and copy manifest. | `C:/Projects/naukrAI/MODULE_7_COMPLETION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/MODULE_7_COMPLETION_REPORT.md` |
| `Reports/FINAL_RELEASE_VERIFICATION_REPORT.md` | Record clean installation, full regression, evaluators and smoke results. | `C:/Projects/naukrAI/FINAL_RELEASE_VERIFICATION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/FINAL_RELEASE_VERIFICATION_REPORT.md` |
