# Assessment example validation

## Scope and rubric source

The original **Naukr.AI AI Retail Data & Conversational Intelligence Workbench** PDF, document DQ-AI-CHAT-03, was read on pages 2–5. Page 5 defines nine categories totaling 100 points, including **Architecture + code quality (10)**. It calls for a 3–5 minute walkthrough covering cleaning, an ambiguous decision, retail, a multi-turn answer with evidence, and failure/recovery.

This pass started from tracked files at local main `b888e83` in an isolated verification directory. It used the existing Python 3.14.7 environment, no live AI key and no external model calls. Tests and fixtures were preserved. The HTTP examples below ran against the real FastAPI handlers with a fresh SQLite store and local test identities.

## Verified walkthrough

All paths in this table start with `/api/v1`. Authenticate with the token from the README. Use the file IDs, step IDs and conversation ID returned by the server. After upload, each example starts from the stated fixture without manually editing it.

| Scenario | Input and requests | Actual result and evidence |
| --- | --- | --- |
| Cleaning and evidence | Upload [teams.csv](../examples/teams.csv) as repeated multipart field `files` at `POST /ingestion/upload` (one part for this file). Read `GET /files/{file_id}/profile?kind=original` and `GET /files/{file_id}/cleaning-plan`, then call `POST /files/{file_id}/clean`. Read `GET /files/{file_id}/profile?kind=cleaned`, `GET /files/{file_id}/validation`, and `GET /files/{file_id}/download`. | Original and cleaned each contain 3 rows. Download has `North` instead of the whitespace-padded value. Validation passed and reports idempotency. Original bytes were unchanged. A foreign owner could not access the cleaned download. |
| Ambiguous decision | On that same file, inspect `GET /files/{file_id}/issues` and the plan. Leave normalization pending during the clean. To record a conservative decision, call `POST /cleaning-steps/{step_id}/reject` with `{"reason":"No authoritative category mapping"}`, then inspect `GET /files/{file_id}/approvals/history`. | `Blue` and `blue` have similar spelling but no authoritative mapping. The plan proposes `category_normalization` at medium risk, `AWAITING_APPROVAL`. It was skipped during cleaning. Both values survived, validation passed and the decision was recorded as `REJECTED`. |
| Retail | Upload and clean [sales](../examples/assessment_sales.csv) and [products](../examples/assessment_products.csv). Register the relationship and request metrics using the bodies below, or run the first [batch case](../cases.json). | No sales rows were duplicated. Clothing revenue **27**, Tools **10**, total **37**. Stock-out frequency **50.0000%** on the product snapshot. Return rate is `UNSUPPORTED` with missing-evidence entries. |
| Multi-turn and trace | `POST /conversations` with `{}`. Use `POST /assistant/query` for each turn. The first uses `question: "Show revenue by region"`, the returned `conversation_id` and `datasets: [{"file_id":"<sales file ID>","revision":1}]`. Follow with “Filter to West” and “Now show average revenue” using only `conversation_id` and `question`. | **37 → 30 → 15**. Traces show verified execution on 3 → 2 → 2 rows and retain West for the final metric change. Inspect `GET /assistant/traces/{trace_id}`. |
| Failure and recovery | With that conversation active, send “Sum revenue” using an explicit revision **2** of the same newly cleaned file, which is still revision **1**. Then send “Continue” using the same conversation ID and no new dataset scope. | HTTP **409** and `DATASET_VERSION_CONFLICT`. The error trace says `NOT_STARTED`. The conversation context version stayed the same. “Continue” recalculates the previous West average and returns **15**. |
| Model outage fallback | Run the checked [assistant outage test](../tests/test_assistant_analysis.py) using the PowerShell command below. It provides HTTP 503 twice through a mock transport. | Both attempts record `AI_PROVIDER_ERROR`. The trace uses `RULES`; the computed North sum is **12.50 across two matching records**. This is a separate fixture from the sales demo. |
| Batch and case isolation | Run the official command below against [cases.json](../cases.json). Run [batch regression tests](../tests/test_assessment_batch.py) for a deliberate unsafe fan-out before later valid checks. | **2/2 cases**, **5/5 chat checks**, **2/2 retail checks**. The failure test verifies `UNSAFE_METRIC_JOIN` and successful continuation in the same case and the next one. |

### Retail request bodies

Replace the angle-bracketed values with IDs from the API.

```text
POST /retail/relationships
{"left_file_id":"<sales ID>","right_file_id":"<products ID>","left_keys":["sku"],"right_keys":["product_code"],"cardinality":"many_to_one"}

POST /retail/metrics
{"file_id":"<sales ID>","relationship_id":"<relationship ID>","metrics":["total_revenue","category_performance","return_rate"],"fields":{"revenue":{"column":"revenue"},"category":{"column":"category","side":"right"}}}

POST /retail/metrics
{"file_id":"<products ID>","metrics":["stock_out_frequency"],"fields":{"on_hand":{"column":"on_hand"}}}
```

The first metric call intentionally supplies no return fields. The stock percentage comes from the product dataset by itself; it must not be counted once per sales line.

### Commands and evidence

Use the activated virtual environment or PowerShell alias from the [README](../README.md).

```powershell
python -m app.evaluate --input cases.json --output results.json --artifacts-dir ./evaluation_output
python -m pytest -q 'tests/test_assistant_analysis.py::test_ai_unavailable_exact_fallback_preserves_filter[outage-AI_PROVIDER_ERROR]' --basetemp "data/pytest-$((New-Guid).Guid.Substring(0,8))"
```

Local run output was retained in the isolated workspace under `data/scoring-suite/results.xml`, `data/scoring-examples-a4682403/asserted-examples.json`, and `data/scoring-evaluator/`; the official batch output was `results.json`. The conversation evaluator passed **13/13** and the recomputed enriched Olist report equaled the accepted JSON. No submission video was recorded.

## Test and documentation checks

The full suite passed **199 tests** with zero failures or skips. Targeted passes were **67** for core examples, **2** for Olist, and **1** for the specific outage parameter. There was one existing Starlette TestClient deprecation warning per run. An initial targeted command encountered permission errors with the Windows default temporary location. The documented, isolated `--basetemp` commands passed on retry. No runtime dependency was changed.

The README environment loader and exact Uvicorn host, port and worker arguments passed a live startup check. The HTML at `/docs` and the bearer scheme in `/openapi.json` were checked over HTTP. Mermaid accepted the README flowchart; local Markdown links and anchors were checked. The verification did not depend on paid API access.
