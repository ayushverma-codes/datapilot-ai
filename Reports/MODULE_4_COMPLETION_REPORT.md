# Module 4 — Retail Domain Intelligence

## Requirements and audit

Module 4 covers the requested three assessment capabilities on arbitrary uploaded CSVs.

| Capability | Existing functionality reused | Module 4 implementation |
| --- | --- | --- |
| Product and category normalization | Module 2 quality evidence and Module 3 validated AI suggestions and approvals. The Olist path already supports translations from an authoritative CSV and distinguishes untranslated and missing categories. | The user selects a product or category field. Exact lookups use a separate validated mapping dataset and an explicit approval reason. The paginated output includes original and canonical values, counts, status and possible variants. Case and whitespace similarities and eligible Module 3 AI steps are review evidence. |
| Sales and inventory analytics | Authenticated, validated cleaned CSVs, the ingestion registry, profile revisions and deterministic aggregation conventions. The legacy retail assistant still reads the verified Olist reports for its existing catalog. | Explicit source fields drive nine metrics and exact-value filters. Results include a formula, inputs, join settings, record counts and gaps. Missing or invalid required evidence produces an unsupported result. |
| Dataset relationships and safe joins | User isolation, the cleaned artifact store, the SQLAlchemy database and existing configured limits. Olist retains its separate, already verified item and payment aggregation. | Discover possible keys from bounded samples, register explicit keys, validate full cardinality and preview actual matched record pairs. The registry persists source versions and content hashes. Aggregates reject any join that would change the number of contributions from a base record. |

Audit sources included `app/retail.py`, `app/retail_bi.py`, `tests/test_retail.py`, `tests/test_retail_bi.py`, the Phase 2 analytics and retail integration reports, the Phase 3 category and timestamp report, and the accepted Olist artifacts. The Olist calculation code, files and public analytics contract were preserved. The new uploaded dataset APIs are separate from the historical Olist assistant APIs.

## How to use the APIs

Upload and clean each input with the existing ingestion and cleaning routes. Authenticate to the following endpoints with the same owner. The final app documents request schemas in `/docs`.

| Method and path | Input and result |
| --- | --- |
| `POST /api/v1/retail/normalization` | Supply `file_id`, `column` and `entity` (`product` or `category`). An optional `mapping` names an owned validated mapping `file_id`, its `source_key` and `canonical_value` columns, and an `approval_reason`. Returns original values with `MAPPED`, `UNTRANSLATED`, `UNCERTAIN` or `MISSING_SOURCE` status. |
| `POST /api/v1/retail/relationships/discover` | Supply `left_file_id` and `right_file_id`. Returns unverified suggestions using up to 20 fields and the first 500 rows per file. Candidate evidence includes exact distinct value overlap and header similarity. |
| `POST /api/v1/retail/relationships` | Add equally sized `left_keys` and `right_keys` arrays (up to five fields), `join_type` (`left` or `inner`) and `cardinality` (`one_to_one`, `many_to_one` or `one_to_many`). Returns a stable relationship ID for the same versions and definition, plus full validation statistics. |
| `GET /api/v1/retail/relationships` | Lists the owner's registered definitions and their versions. |
| `GET /api/v1/retail/relationships/{relationship_id}/preview` | Returns a page of validated pairs as one-based data record numbers in the cleaned snapshots. Null on the right means unmatched. |
| `POST /api/v1/retail/metrics` | Supply a base `file_id`, requested `metrics`, and semantic `fields` such as `{"revenue":{"column":"Line amount"}}`. Use optional exact-match `filters` and an optional `relationship_id` to attach dimension fields on the right or filter on either side. |

The normalization result is a derived mapping and leaves both input artifacts intact. Missing or uncertain labels preserve their source text and have a null canonical value. Blank or duplicate mapping keys and ambiguous chains or cycles are rejected. A mapping table is treated as confirmed only when the caller explicitly selects it and supplies the approval reason. AI suggestions reference the original profiling version and retain their cleaning-plan status. Apply an AI proposal to produce a new cleaned CSV only through the Module 3 approval and cleaning flow.

The join registry pins both immutable artifact IDs, cleaning revisions and SHA-256 hashes. Later cleaning revisions do not change an existing relationship. Matching is exact and retains leading zeros. Keys with any blank component are unmatched. Validation measures distinct-key uniqueness, missing and unmatched rows, fan-out on both sides, maximum matches per row and projected output size before materializing row pairs. Many-to-many keys and violations of declared uniqueness are rejected. One-to-many previews can show expected multiplication, while aggregate metrics require every left row to appear exactly once. For category or store analysis, unmatched rows retained by a left join can contribute to the unclassified bucket, making reconciliations possible.

## Metric definitions

All metrics use the filtered cohort of validated cleaned records. Measures come from the base dataset. The optional right dataset can supply category and store fields or participate in explicit filters. Monetary units come from the source. When the caller maps line revenue, that field is authoritative. Otherwise, explicitly mapped unit price and units sold can establish line revenue.

| Metric | Formula |
| --- | --- |
| `total_revenue` | Sum confirmed line revenue, or sum unit price × units sold. |
| `order_count` | Count distinct nonblank order IDs. |
| `average_order_value` | Total revenue ÷ distinct order count. |
| `units_sold` | Sum declared nonnegative integer units. |
| `return_rate` | 100 × distinct orders with any explicitly returned record ÷ distinct orders. Provide both returned and non-returned status values. |
| `inventory_availability` | 100 × observations with on-hand units above zero ÷ inventory observations. |
| `stock_out_frequency` | 100 × observations with zero on-hand units ÷ inventory observations. |
| `category_performance` | Sum revenue by category, including an unclassified bucket. |
| `store_performance` | Sum revenue by store, including an unclassified bucket. |

Monetary arithmetic uses Decimal. Ratios use four decimal places and half-even rounding. Required inputs with missing or invalid values produce `UNSUPPORTED` and identify the field or role and missing record counts. Zero denominators are unsupported. The inventory cohort represents observations, such as product/store snapshots. Frequency measures observation share and cannot establish elapsed time or outage duration. Revenue only reflects the provided line amounts or extended prices. The input does not establish currency conversion, net revenue or profit.

## Verification

The initial retail-focused suite passed 40 tests, including the earlier Olist and retail assistant regressions. The final full suite passed **160 tests** with zero failures or skips in 98.47 seconds. It included the later unit-price basis and composite-key checks. Both runs reported one existing Starlette TestClient deprecation warning. `python -m pip check` passed.

The full suite ran with `python -m pytest -q -p no:cacheprovider tests --basetemp <isolated-test-folder> --junitxml <evidence-file>`. The focused suite selected `tests/test_retail_uploaded.py`, `tests/test_retail.py` and `tests/test_retail_bi.py`. Verification covers confirmed mappings, missing and untranslated categories, unresolved variants, duplicate keys, revenue and AOV, units, returns, inventory, missing fields, empty cohorts, owner isolation, version pinning, modified artifacts, unmatched keys, composite keys, one-to-many fan-out, and many-to-many rejection.

A live synthetic upload used Groq GPT-OSS 120B through the existing Module 3 analyzer and the local credential. The new retail endpoint showed one validated suggestion awaiting approval. The suggestion left canonical values unset and the original artifact unchanged. Mocked AI and disabled-AI paths also have tests.

The complete regenerated enriched Olist result equaled the accepted Phase 3 JSON, including monthly and category detail. The earlier accepted summary and all five handoff CSVs retained their recorded hashes. The regression retains 99,441 orders, 96,478 delivered orders, delivered merchandise of 13,221,498.11, delivered freight of 2,198,275.64 and delivered payments of 15,422,461.77.

## Files and limitations

New files are `app/retail_contracts.py`, `app/retail_engine.py`, `app/services/retail_service.py`, `tests/test_retail_uploaded.py` and this report. Modified files are `app/main.py`, `app/storage/database.py` and the development decision ledger. No files were deleted. The registry uses the existing additive initialization for core SQLAlchemy tables. No dependency was added.

Discovery is a bounded recommendation and can miss legitimate keys. Validate an explicit composite pair when the hints omit it. Current preview joins support left and inner types. The engine conservatively rejects joins when both sides have duplicate nonblank keys, even if the repeated groups do not match. Artifact size, rows and cells use existing processing limits. Grouped output supports up to 1,000 distinct groups. Amounts support scientific notation within a bounded Decimal range. Normalization produces reviewable results on request. The uploaded catalog is caller supplied and no translation service is assumed.

Deferred work includes demand forecasting, product recommendations, segmentation, basket analysis, product affinity, automated replenishment and profitability prediction.

## Proposed release copy manifest

The copy requires user approval. The seven files below are the complete Module 4 payload. Runtime databases, virtual environments, secrets, datasets and historical evidence are excluded. Verify hashes, run installation and regression tests, and exercise the running app from the final demo folder after approval.

| File | Purpose | Source path | Destination path |
| --- | --- | --- | --- |
| `app/main.py` | Expose authenticated APIs for uploaded retail datasets. | `C:/Projects/naukrAI/stage6-work/app/main.py` | `C:/Projects/naukrAI/datapilot-ai/app/main.py` |
| `app/storage/database.py` | Persist the owner-scoped retail relationship registry. | `C:/Projects/naukrAI/stage6-work/app/storage/database.py` | `C:/Projects/naukrAI/datapilot-ai/app/storage/database.py` |
| `app/retail_contracts.py` | Validate explicit field mappings, join keys, filters and requests. | `C:/Projects/naukrAI/stage6-work/app/retail_contracts.py` | `C:/Projects/naukrAI/datapilot-ai/app/retail_contracts.py` |
| `app/retail_engine.py` | Calculate metrics, validate joins and resolve confirmed entity mappings. | `C:/Projects/naukrAI/stage6-work/app/retail_engine.py` | `C:/Projects/naukrAI/datapilot-ai/app/retail_engine.py` |
| `app/services/retail_service.py` | Use validated snapshots, record versions and reuse Module 3 proposals. | `C:/Projects/naukrAI/stage6-work/app/services/retail_service.py` | `C:/Projects/naukrAI/datapilot-ai/app/services/retail_service.py` |
| `tests/test_retail_uploaded.py` | Verify all three capabilities and complete Olist output equivalence. | `C:/Projects/naukrAI/stage6-work/tests/test_retail_uploaded.py` | `C:/Projects/naukrAI/datapilot-ai/tests/test_retail_uploaded.py` |
| `Reports/MODULE_4_COMPLETION_REPORT.md` | Document formulas, usage, verification and the release manifest. | `C:/Projects/naukrAI/MODULE_4_COMPLETION_REPORT.md` | `C:/Projects/naukrAI/datapilot-ai/Reports/MODULE_4_COMPLETION_REPORT.md` |

## Final demo verification

The user approved the seven-file release manifest. All destination SHA-256 hashes matched the approved source files. Installing the release requirements into the existing ignored local environment resolved every requirement, and `pip check` passed. The full suite ran from `datapilot-ai` with isolated runtime storage and passed **160 tests** with one existing Starlette TestClient deprecation warning, including the complete accepted Olist comparison.

Loopback Uvicorn checks verified health and API documentation, multipart upload and cleaning, confirmed category mapping, sales and stock-out calculations, relationship discovery and registration, unmatched-row evidence, blocked unsafe cardinality and access isolation. After a server restart, the stored relationship returned the same metrics. The live Groq run used synthetic data and returned one pending category suggestion without assigning any canonical values. The HTTP checks verified that the original uploaded artifacts remained intact.

The release requirements and app do not require development paths. The five Olist input CSVs and both accepted summaries matched their verified hashes. The release Git diff contains seven small source, test and report files. The existing ignore rules cover the local environment, credentials, databases and new runtime outputs.
