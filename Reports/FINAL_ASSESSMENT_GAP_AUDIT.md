# Final Naukr.AI assessment gap audit

## Baseline and scope

The assessment is **AI Retail Data & Conversational Intelligence Workbench**, under AI Engineering. The supplied A–H weights add up to 90 of the stated 100 points. This report does not assign the unspecified 10 points or predict an evaluator score.

Live GitHub `refs/heads/main` and local `main` and `origin/main` resolved to `46afd54dad9bf16cfa126ef4db6537730e7d2e1b`. The seven commits in the brief are present in order. The final local working tree has a separate `.gitignore` edit and an untracked `datapilot-ai.zip`. Tracked application and documentation files otherwise match published main. They were inspected without resetting, staging, or changing the final folder.

The audit used the development and root decision logs, Modules 1–7 completion reports, the previous final release report, the README, production code, tests, fixtures, environment example and requirements. Earlier reports are historical context. See [independent verification](FINAL_ASSESSMENT_VERIFICATION_REPORT.md) for executed checks.

## Requirement matrix at discovery

| Requirement | Points | Code evidence | Test evidence | Status | Required action |
| --- | ---: | --- | --- | --- | --- |
| A. Profile and clean CSVs, including plan, artifact, row and schema evidence | 18 | `ingestion_service.py`, `csv_reader.py`, profile generator, cleaning registry and validator | Ingestion, quality, cleaning, validation and API suites | Implemented but not independently verified | Reproduce tests and inspect a live cleaned artifact. |
| B. Retail intelligence, at least two capabilities | 14 | `retail_service.py`, `retail_engine.py`, Olist engine and catalog | Uploaded retail, Olist BI and accepted regression suites | Implemented but not independently verified | Reproduce three capabilities and document formulas and safe join grain. |
| B. Recommendation explorer (optional alternative) | Included above | No corresponding explorer | None | Not applicable | Disclose that this alternative is absent. |
| C. Structured conversational reasoning, memory and evidence | 20 | Assistant planner, tool validator, trace service and memory | Assistant intent, fallback, unsafe plan, revision, retail and memory tests | Implemented but not independently verified | Run grounded retail and uploaded-file examples. Distinguish provider-assisted intents from offline wording. |
| D. AI Engineering and safe execution | 12 | Pydantic plan, schema grounding, provider transport and deterministic execution | AI, assistant analysis and conversation evaluation | Implemented but not independently verified | Reproduce malformed provider output, timeout and injection checks. Keep orchestration architecture. |
| E. Batch evaluation and reliability | 10 | `app/evaluate.py` calls ingestion and chat; no joined retail case contract | Existing batch tests cover cleaning, chat and continuation | Partially implemented | Add explicit retail relationship and metric evaluation using existing production services. Provide a root case fixture for the official command. |
| F. Security and privacy | 6 | Auth, owner scopes, path checking, request guard and provider minimization | API, ingestion, assistant and reliability checks | Implemented but not independently verified | Reproduce boundary checks and document static tokens, data sent and timeout scope. |
| G. Automated tests | 5 | Existing test suite | 197 historically, to be reproduced | Implemented but not independently verified | Run the published suite, then run candidate regression. Add a test for the actual batch gap. |
| H. README and technical reasoning | 5 | README describes basics and API but lacks a complete rubric map, reproducible joined retail examples and unified walkthrough | Setup requires independent execution | Partially implemented | Write the assessment README and run its review paths. |
| Clean clone and accepted evidence | Cross-cutting | Tracked Olist fixture paths are directly referenced by runtime and tests | Full recomputed Olist report comparison and retail regression | Implemented but not independently verified | Verify all seven historical hashes in the network clone and recreate outputs separately. |

## Artifact classification

The two accepted Olist summaries and five cleaned handoff CSVs are tracked in published main despite the broad `evaluation_output/` ignore rule. The two summaries are used by the Olist assistant and regression. The five inputs are needed to reproduce the accepted output and run the real retail regression. The small examples under `examples/` are also tracked. Local runtime databases, outputs, raw demo datasets and ZIP exports are not prerequisites. The earlier ZIP omission is not a packaging gap in the published Git tree.

## Targeted remedy

The main code gap is batch orchestration for joined retail metrics. Extend the existing evaluator with opt-in retail checks that resolve case-local file indices, register joins and call the same metric service as the HTTP API. Keep existing cleaning/chat contracts and accepted Olist artifacts. The rest is release verification, examples and technical documentation, not a second analytics engine or an agent framework.
