import csv
import io
import json
import re
from collections import Counter
from pathlib import PureWindowsPath
import pandas as pd
from app.models.contracts import PipelineError


def validate_file(name, size, settings):
    if not name or len(name) > 255 or PureWindowsPath(name).name != name or any(c in name for c in '/\\\x00:') or name in {'.', '..'}:
        raise PipelineError("UNSAFE_FILENAME", "Upload requires a safe basename")
    if not name.lower().endswith('.csv'):
        raise PipelineError("INVALID_EXTENSION", "Only CSV files are accepted")
    if size > settings.max_file_bytes:
        raise PipelineError("FILE_TOO_LARGE", "File exceeds configured byte limit")
    if not size:
        raise PipelineError("EMPTY_FILE", "CSV file is empty")


def decode(content):
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig'
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError as exc:
        if content.startswith((b'\xff\xfe', b'\xfe\xff', b'\xef\xbb\xbf')):
            raise PipelineError("INVALID_ENCODING", "CSV does not match its Unicode byte-order mark") from exc
        encoding = 'cp1252'
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError as exc:
            raise PipelineError("INVALID_ENCODING", "Cannot decode CSV safely") from exc
    if any(ord(c) < 32 and c not in '\r\n\t' for c in text):
        raise PipelineError("BINARY_CONTENT", "File contains binary control characters")
    if text.lstrip().lower().startswith(('<!doctype', '<html')):
        raise PipelineError("NOT_CSV", "Content appears to be a non-CSV document")
    if text.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            raise PipelineError("NOT_CSV", "Content appears to be a JSON document")
    return text, encoding


def read_csv(content, settings, delimiter=None):
    text, encoding = decode(content)
    if not text.strip():
        raise PipelineError("EMPTY_FILE", "CSV has no header")
    if delimiter is not None and delimiter not in [',', ';', '\t', '|']:
        raise PipelineError("INVALID_DELIMITER", "Delimiter must be comma, semicolon, tab or pipe")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=',;\t|').delimiter
        except csv.Error:
            # Header-only fallback; strict width validation below still catches broken rows.
            first = text.splitlines()[0]
            delimiter = max(',;\t|', key=first.count) if any(x in first for x in ',;\t|') else ','
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    try:
        headers = next(reader)
        if not headers:
            raise PipelineError("EMPTY_HEADER", "CSV requires a header")
        if len(headers) > settings.max_columns:
            raise PipelineError("COLUMN_LIMIT", "CSV exceeds column limit")
        columns, used = [], set()
        for index, header in enumerate(headers):
            base = header or f'unnamed_{index + 1}'
            key = base
            suffix = 2
            while key in used:
                key = f'{base}__{suffix}'
                suffix += 1
            used.add(key)
            columns.append(key)
        records = []
        for record in reader:
            if record == []:
                record = [''] * len(headers)
            if len(record) != len(headers):
                raise PipelineError("MALFORMED_CSV", "Record width differs from header", details={"line": reader.line_num, "expected": len(headers), "actual": len(record)})
            if len(records) >= settings.max_rows or (len(records) + 1) * len(headers) > settings.max_cells:
                raise PipelineError("PROCESSING_LIMIT", "CSV exceeds row or cell limit")
            # Only physically empty cells are null. Literal NA/null strings remain data.
            records.append([value if value != '' else None for value in record])
    except csv.Error as exc:
        raise PipelineError("MALFORMED_CSV", "CSV quoting or field size is invalid", details={"line": reader.line_num}) from exc
    frame = pd.DataFrame(records, columns=columns, dtype='string')
    counts = Counter(headers)
    metadata = {"encoding": encoding, "delimiter": delimiter, "original_columns": headers,
                "columns": columns, "normalized_columns": [re.sub(r'\W+', '_', h.strip().lower()).strip('_') or f'column_{i+1}' for i, h in enumerate(headers)],
                "duplicate_headers": {h: n for h, n in counts.items() if n > 1}, "parsing_issues": []}
    return frame, metadata
