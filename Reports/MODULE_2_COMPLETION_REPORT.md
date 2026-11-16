# Module 2 — Data profiling and quality assessment

## Scope and audit

Module 2 builds on the profiling and quality engine already used by the Module 1 upload and cleaning flows. The engine profiles arbitrary CSV schemas, preserves the raw upload, and writes a machine-readable report per profile revision.

The existing engine already calculated file dimensions, memory, schema, missing and duplicate totals, column inference, frequency samples, numerical statistics and date candidates. It already enforced configurable ingestion limits, preserved originals, and treated statistical outliers as warnings. The audit identified empty or unrelated issue evidence, inaccurate counts for variants, duplicate issue IDs for different duplicate headers, random IDs for malformed records and cleaned profiles that described the original CSV's serialization.

## Requirements and verification coverage

| Requirement | Implementation and test evidence |
| --- | --- |
| File profile | Size uses the artifact's byte length. Counts cover records, columns, missing cells, empty records and exact duplicates. The profile includes the schema and deep DataFrame memory estimate. `test_profile_measurements_and_bounded_categorical_distribution` and `test_structural_issue_identity_evidence_and_empty_dataset` verify these fields. |
| Column profile | Existing type inference and exact null, distinct and cardinality counts are reused. Samples and frequencies remain bounded. Header-only, all-null and high-cardinality columns are covered by the same tests. |
| Numerical profile | Minimum, maximum, mean, median, population standard deviation and five quantiles are asserted against known values. Invalid numerical evidence samples the values that failed parsing. Existing extreme-number and outlier regressions cover numerical safety and preservation. |
| Categorical profile | Frequency summaries and rare counts are bounded in the response. Whitespace samples show affected values. Capitalization groups count matching records across the column, and spelling variants count records in the compared candidates. `test_quality_evidence`, `test_whitespace_alone_is_not_a_capitalization_issue` and the high-cardinality test cover these paths. |
| Date profile | Date candidates provide valid and invalid counts, candidate formats, ambiguous examples and range endpoints. Range endpoints account for explicit time zone offsets. A format inconsistency counts records outside the most widely supported format. Shared ambiguity alone gives an ambiguity issue. `test_date_ranges_offsets_and_consistent_ambiguous_formats` and `test_quality_evidence` cover the distinctions. |
| Quality issue contract | The existing issue model supplies column scope, category, severity, affected count and percentage, evidence, detection source, recommended action and status. Stable UUIDs are derived from file, column and category. Separate duplicate header names have separate IDs. Malformed records carry a stable ID and the parser's line details. Structural rows have bounded record numbers, and duplicate identifier evidence shows repeated values. |
| Before and after | One profiler runs on both sides. Reports contain a profile ID, run ID, file ID, kind and revision. Revision zero identifies the original. Cleaned profiles use metadata from the emitted CSV. `test_profile_versions_describe_original_and_cleaned_artifacts` verifies two cleaned revisions, immutable JSON reports and byte-for-byte preservation of the original CSV. |
| Configurable bounds | The existing CSV byte, row, column and cell limits apply before profiling. Ingestion tests cover arbitrary schemas, configured limits and a 12,000-row dataset. The 200,000-row boundary check is recorded below. |

## Evidence and limits

Record numbers for profile evidence are one-based within the profiled data and exclude the header. Malformed record evidence uses physical CSV line numbers. Parsing stops on a malformed record, so its issue records one detected failure and retains the existing zero percentage when the dataset row denominator is unknown.

Frequency responses include up to ten most frequent values, ten rare examples and five samples per column. Spelling comparisons cover up to 30 frequent short values. Case evidence caps the number and size of examples, while the count uses the complete capitalization groups. Clipped case examples are unsuitable for constructing an exact cleaning mapping. Statistical outliers remain review warnings and their values are preserved.

Type inference remains heuristic. Ambiguous date ranges include plausible interpretations and keep the ambiguity evidence. Legacy stored profiles gain the available identity and revision fields on retrieval. Newly generated profiles also include the expanded evidence and corrected cleaned schema metadata.

## Verification

The full development suite passed with **137 tests, zero failures and one existing Starlette TestClient deprecation warning**. The command was `python -m pytest -q -p no:cacheprovider tests`, using an isolated test directory and runtime database. The first run of the expanded evidence assertion exposed that the fixture produced mixed-type issues in two columns. The assertion now checks each column separately and the full suite passed again.

A direct upload and profiling check at the default 200,000-row limit passed on a single-column, 1,200,008-byte CSV. It verified the row count, exact duplicates beyond the first row, file size and bounded evidence. This check covers that input shape.

An HTTP check passed for multipart upload, structural issue evidence, cleaning and retrieval of both profile revisions. The original used a semicolon delimiter and the cleaned artifact used a comma. A second retrieval confirmed the original profile was preserved.

## Release verification

The full suite ran from the release folder and passed **137 tests**, including the real Olist regression. The existing Starlette TestClient deprecation warning was reported. Dependency checks passed in the existing verification environment.

The release app started under Uvicorn and returned HTTP 200 from `/health`. A multipart upload verified duplicate-row evidence over HTTP, a successful cleaning run, the unchanged original profile, and the cleaned profile's revision, row count and delimiter. Verification used isolated runtime storage.
