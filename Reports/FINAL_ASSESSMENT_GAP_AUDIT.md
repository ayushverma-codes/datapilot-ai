# Final Naukr.AI assessment gap audit

## Baseline and scope

The assessment is **AI Retail Data & Conversational Intelligence Workbench**, under AI Engineering. The requirements below are based on pages 2–4 of the assignment PDF, and all nine weights on page 5 are included. Points are rubric weights, not an awarded or predicted score.

At the original audit, live GitHub `refs/heads/main` and local `main` and `origin/main` resolved to `46afd54dad9bf16cfa126ef4db6537730e7d2e1b`. The seven commits in the brief are present in order. The final local working tree has a separate `.gitignore` edit and an untracked `datapilot-ai.zip`. Tracked application and documentation files otherwise match published main. They were inspected without resetting, staging, or changing the final folder.

The audit used the development and root decision logs, Modules 1–7 completion reports, the previous final release report, the README, production code, tests, fixtures, environment example and requirements. Earlier reports are historical context. See [independent verification](FINAL_ASSESSMENT_VERIFICATION_REPORT.md) for executed checks.

## Current requirement matrix

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

## Artifact classification

The two accepted Olist summaries and five cleaned handoff CSVs are tracked in published main despite the broad `evaluation_output/` ignore rule. The two summaries are used by the Olist assistant and regression. The five inputs are needed to reproduce the accepted output and run the real retail regression. The small examples under `examples/` are also tracked. Local runtime databases, outputs, raw demo datasets and ZIP exports are not prerequisites. The earlier ZIP omission is not a packaging gap in the published Git tree.

## Prior targeted remedy

The original code gap was batch orchestration for joined retail metrics. That gap was closed with opt-in retail checks that resolve case-local indices, register joins and call the same metric service as the HTTP API. Existing cleaning/chat contracts and accepted Olist artifacts were preserved.

## Architecture and code quality

The application boundary in `app/main.py` authenticates and dispatches typed requests. The service layer owns orchestration and state transitions. Profiling, the cleaning registry and executor, `app/retail_engine.py`, and `app/conversation/analysis_tools.py` contain the work the API and batch evaluator reuse. `app/storage/` separates database metadata from atomic file writes. `PipelineError` and explicit contracts make expected refusals inspectable at the HTTP boundary. The shared services, injected settings and mock provider transport support isolated tests.

The architecture favors predictable local runs over distributed processing. Dataframes, synchronous work after the body is read, filesystem artifacts and a single-worker SQLite setup limit production concurrency and require operating controls. Passing tests provides evidence for those design paths. It does not determine how many architecture points the evaluator awards.

The scoring correction and repeatable walkthrough are documented in [example validation](ASSESSMENT_EXAMPLE_VALIDATION.md). A recorded 3–5 minute walkthrough remains a separate submission item.
