# Module 5 — AI Conversational Assistant

## Audit and requirements

The repository already had a working single CSV assistant for filtered counts and aggregates, an Olist assistant over accepted summaries, and conversation memory with a 13-scenario evaluator. The evaluator verifies follow-up behavior and state preservation. The broader analytical intents, safe joins, dataset revisions and complete traces needed separate verification. The audit covered the assistant and memory code and contracts, the API, Module 3's Olist and generic conversation reports, the evaluator and its JSON fixtures, and the Module 4 retail service and tests.

| Requirement | Existing behavior reused | Verified Module 5 behavior |
| --- | --- | --- |
| Natural-language interpretation | Existing optional OpenAI-compatible planning and deterministic fallbacks. | Structured intents for inspection, row projection, filters, sorting, grouping, aggregation, top and bottom groups, distinct values, statistics, comparisons and retail metrics. |
| Structured plans and grounding | Pydantic contracts, cleaned profiles and Module 3 provider configuration. | Scoped dataset alias and revision, typed fields, filter operators, joins, cohorts, metrics, sort, limits and explicit clarification or unsupported outcomes. Prompts contain schema metadata and optional compact analytical context. |
| Plan validation | Owner-scoped ingestion, versioned profiles and the Module 4 relationship registry. | Check artifact versions and schema, constrain operations and types, reject model filter literals without support in the question or prior scoped filters, and require every base row to contribute exactly once when joined. |
| Tool execution | Existing CSV parsing, filter and aggregation primitives, retail calculations and join validation. | The backend performs the calculation and caps output. No model response can supply executable Python or SQL. |
| Verification and response | Existing deterministic analytical foundations. | Verify source shape, result structure and cohort counts. Render final answers from computed results. Missing required information produces explicit refusal or clarification. |
| Trace | Existing SQLAlchemy persistence and session ownership. | Persist the original question, requested scope, generated and validated plans, provider attempts, selected versions, bound fields, joins, filters, operations, record counts, bounded output, final answer and status. |

LangGraph is absent from the dependency file and development environment. The request uses the existing synchronous architecture and adds no dependencies or agents. The previous Olist assistant and conversation message APIs retain their contracts. The Module 5 assistant can read context from an owned existing conversation. Conversation state updates remain with the conversation service.

## API

Use the existing authenticated `POST /api/v1/assistant/query` endpoint. The extended request format selects cleaned uploads and optionally fixes a revision:

```json
{
  "question": "Show the top 5 regions by total amount",
  "datasets": [{"file_id": "<cleaned-file-id>", "revision": 1}]
}
```

For a join, register the relationship through Module 4 and pass its ID in `relationship_ids`. A request can scope up to five datasets and three relationships. With multiple sources, name the dataset or use the grounded aliases `d1` through `d5`. An owned conversation can supply its active dataset and compact query context through `conversation_id`. If selection remains ambiguous, the response requests clarification.

The extended response returns `trace_id`, `status`, `intent`, the validated `plan`, a bounded structured `result`, the deterministic `answer` and `evidence`. Get the stored audit record with `GET /api/v1/assistant/traces/{trace_id}`. Rejected plans return a structured error with the trace ID. The older `file_id` request still returns the legacy result shape and now includes a trace ID.

Supported statistics are nonblank count, null count, minimum, maximum, mean, median and population standard deviation. Comparisons apply two declared cohorts to the same filtered base and report A minus B. Row and group previews cap at 50 entries. Sort values use numeric ordering on numeric fields, lexical ordering on text fields and place blanks last. Labels and text cells in previews cap at 200 characters. Group aggregation permits up to 1,000 groups, and artifact reading uses existing byte, row and cell limits.

## Verification

The initial focused run passed 35 tests and failed two because the new verifier expected a metrics envelope while the retail adapter returned metric keys at the top level. The adapter now returns the expected envelope, and the full suite covered the fix.

The final full suite passed **187 tests** in 122.53 seconds, with zero failures or skips and one existing Starlette TestClient deprecation warning. The command was `python -m pytest -q -p no:cacheprovider tests --basetemp <isolated-test-folder> --junitxml <evidence-file>`. Related tests are `tests/test_assistant_analysis.py`, `tests/test_conversation.py`, `tests/test_conversation_memory.py`, `tests/test_conversation_memory_evaluation.py`, `tests/test_retail_uploaded.py` and the existing Olist regressions. `python -m pip check` passed.

The new tests cover all structured intents; top and bottom ordering; bounded previews; valid, malformed and unsupported plans; unknown fields and wrong types; absent and unsafe relationships; stale and concurrently changed revisions; missing retail evidence; clarification; timeout, outage and rate limit fallback; dataset cells containing injection text; trace isolation after restart; context ownership; and result verification failure. The older conversation-memory evaluator continues to run independently of the new analytical tests.

Live authenticated HTTP tests used the local Groq credential and synthetic data. Initial live plans with an array for a grouping field and an object for a filter column were rejected before execution. The prompt now shows the exact expected grouping and filter structures. GPT-OSS 120B then computed the highest department total over a validated join (**Clothing, 14.20**), and Qwen 3.8 27B compared two cohorts (**North minus South, 8.50**). Both results were verified from execution and retrieved from their stored traces. No row values were sent to the assistant planner. The region names in the question were provided by the caller.

## Files and limitations

Created the analytical contracts, planner, tools, orchestration service, assistant tests and this report. Modified the existing assistant, API, SQLAlchemy database definitions, retail join guard and the development decision ledger. No files were deleted. The trace table uses the existing additive SQLAlchemy core-table initialization.

AI can interpret more natural-language phrasing than the offline parser. Offline rules support a narrow set of exact source names, aggregate and schema questions, descriptive statistics, distinct values, and simple filters. Unrecognized wording requests clarification. The planner sends at most 4,800 bytes of JSON metadata and caps output at 1,400 tokens, with bounded retries and no retry for rate limit responses. Wide schemas can omit fields from the prompt. Filter literals require the exact value, and unsupported operators or speculative business questions are refused. A user may specify the relationship explicitly to expose right-side schema. The extended assistant accepts the selected current base revision and preserves the registered right-side snapshot. Existing conversation follow-up handling and the historical Olist assistant remain separate from this traced uploaded-dataset workflow.

Deferred work includes multiple agents, self-correcting planning, advanced optimization, visualizations, prediction, streaming and caching.

## Proposed release copy manifest

Each entry is required by the assistant, its verification, or this report. The payload excludes dependencies already installed locally, virtual environments, credentials, databases, datasets and historical evidence. After approval, verify destination hashes and run the installation check, full suite and HTTP workflow from `datapilot-ai`.

| File | Purpose | Source path | Destination path |
| --- | --- | --- | --- |
| `app/main.py` | Expose the extended assistant request and owner-scoped trace retrieval. | `C:/Projects/naukrAI/stage6-work/app/main.py` | `C:/Projects/naukrAI/datapilot-ai/app/main.py` |
| `app/storage/database.py` | Persist assistant traces in the existing database. | `C:/Projects/naukrAI/stage6-work/app/storage/database.py` | `C:/Projects/naukrAI/datapilot-ai/app/storage/database.py` |
| `app/conversation/assistant.py` | Reuse provider transport, filtering and aggregation with the legacy API. | `C:/Projects/naukrAI/stage6-work/app/conversation/assistant.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/assistant.py` |
| `app/conversation/analysis_contracts.py` | Define strict scope, plan, intent and query limit contracts. | `C:/Projects/naukrAI/stage6-work/app/conversation/analysis_contracts.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/analysis_contracts.py` |
| `app/conversation/analysis_planner.py` | Ground the plan in bounded schemas and provide exact fallback rules. | `C:/Projects/naukrAI/stage6-work/app/conversation/analysis_planner.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/analysis_planner.py` |
| `app/conversation/analysis_tools.py` | Validate tools and run deterministic analytics. | `C:/Projects/naukrAI/stage6-work/app/conversation/analysis_tools.py` | `C:/Projects/naukrAI/datapilot-ai/app/conversation/analysis_tools.py` |
| `app/services/assistant_service.py` | Bind versions, verify results, render answers and persist traces. | `C:/Projects/naukrAI/stage6-work/app/services/assistant_service.py` | `C:/Projects/naukrAI/datapilot-ai/app/services/assistant_service.py` |
| `app/retail_engine.py` | Share the existing join grain guard with the assistant. | `C:/Projects/naukrAI/stage6-work/app/retail_engine.py` | `C:/Projects/naukrAI/datapilot-ai/app/retail_engine.py` |
| `tests/test_assistant_analysis.py` | Verify intents, safety, provider failures and persisted traces. | `C:/Projects/naukrAI/stage6-work/tests/test_assistant_analysis.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_assistant_analysis.py` |
| `Reports/MODULE_5_COMPLETION_REPORT.md` | Document the interface, verification and release manifest. | `C:/Projects/naukrAI/MODULE_5_COMPLETION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/MODULE_5_COMPLETION_REPORT.md` |

## Final demo verification

The user approved the ten-file release manifest. Every destination SHA-256 matched its source. The existing release environment satisfied the requirements, and `pip check` passed. The full suite from `datapilot-ai` passed **187 tests** with one existing Starlette TestClient deprecation warning, including the conversation evaluator and Olist regression.

Uvicorn ran from the release folder with isolated storage. The AI-disabled HTTP check verified upload and cleaning, a filtered aggregate, rejection of a stale revision, a clarification response, owner-only trace retrieval and trace persistence after restart. The live synthetic Groq workflows verified a joined top department result with GPT-OSS 120B and a two-cohort comparison with Qwen 3.8 27B. Both stored a verified trace. Two earlier live GPT-OSS responses omitted the relationship property and were rejected before execution. An explicit request to set the relationship to j1 produced the verified joined result.

The Git diff contains only the approved source, test and report files. The five cleaned Olist CSVs and two accepted summaries matched the verified source hashes. Existing Git ignore rules cover secrets, the local environment, databases and generated runtime files. No absolute development paths are needed by the application.
