# Module 1 — Data ingestion and preprocessing

**Status: Complete.** All 19 requirements in the Module 1 specification passed verification on Windows, Python 3.14.7 and SQLite. See the [project README](../README.md) for installation and API access.

## Input and output

`POST /api/v1/ingestion/upload` accepts one or more CSVs as repeated multipart `files` parts. CSVs can have different schemas and use the same original filename. Optional `descriptions` follow file order, with an empty part for an undescribed file. The server derives the owner from the authenticated request.

A successful multipart request returns a run ID and independent file records with file IDs, original filenames, byte sizes, SHA-256 checksums, statuses and structured errors. Owner-scoped run and file routes expose the registry to callers and downstream modules. Accepted uploads are written to unique raw artifact paths and retain their original bytes through cleaning.

## Implementation

| File | Responsibility |
| --- | --- |
| [app/main.py](../app/main.py) | Multipart registration under the request-byte limit, optional descriptions and complete hashing of oversized spooled files. |
| [app/ingestion/csv_reader.py](../app/ingestion/csv_reader.py) | Filename and size checks, encoding and delimiter detection, duplicate header handling and strict record parsing. |
| [app/services/ingestion_service.py](../app/services/ingestion_service.py) | Run/file registration, per-file isolation, metadata and replay handling. |
| [app/storage/database.py](../app/storage/database.py) and [app/storage/file_storage.py](../app/storage/file_storage.py) | SQLAlchemy metadata and separate raw artifact storage. |

The update removed the multipart parser's fixed file count, added persisted descriptions, made oversized metadata and replay checks use complete file bytes, and rejected malformed BOM-marked uploads. It reused the existing ingestion, profiling, storage and validation pipeline.

## Verification

| Check | Result |
| --- | --- |
| Focused ingestion and regression tests | 72 passed. |
| Full suite from the release folder | 131 passed, zero failures or skips. The existing Starlette TestClient deprecation warning was observed. |
| Installation and API smoke | Fresh dependency installation, `pip check`, SQLite startup, health, docs and multipart OpenAPI checks passed. |
| Evaluators | Conversation 13/13 and batch 2/2. |

[Ingestion tests](../tests/test_ingestion.py), [multipart tests](../tests/test_module1_audit.py) and [API/evaluation tests](../tests/test_api_evaluation.py) exercise single and mixed-file requests, a 1,001-file request, distinct IDs, duplicate filenames and headers, supported encodings and delimiters, header-only files, malformed records, configured limits, original-byte integrity, restart persistence, replay conflict and owner isolation.

## Limits and future work

The current service processes bounded CSV uploads in-process. Supported delimiter characters are comma, semicolon, tab and pipe. Large requests are bounded by the configured byte limits and available resources. Excel and Parquet, cloud object storage, streaming, scheduled imports, distributed execution and incremental ingestion are future work.
