import re
from datetime import datetime
import math

NUMERIC = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')
DATE_SHAPE = re.compile(r'^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}(?:[ T].*)?$')
FORMATS = ('%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%m-%d-%Y')


def numeric(value):
    value = str(value).strip()
    if not NUMERIC.fullmatch(value):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def date_candidates(value):
    value = str(value).strip()
    if not DATE_SHAPE.match(value):
        return []
    # ISO timestamps are common in large datasets. Parse them directly rather
    # than raising six strptime exceptions per cell before reaching fromisoformat.
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?', value):
        try:
            parsed = datetime.fromisoformat(value)
            return [(parsed.isoformat(), '%Y-%m-%d' if len(value) == 10 else 'ISO8601')]
        except ValueError:
            return []
    result = []
    for fmt in FORMATS:
        try:
            result.append((datetime.strptime(value, fmt).isoformat(), fmt))
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value)
        if not result:
            result.append((parsed.isoformat(), 'ISO8601'))
    except ValueError:
        pass
    return result


def infer(series):
    values = series.dropna().astype(str).str.strip()
    count = len(values)
    nums = values.map(numeric)
    numeric_count = int(nums.notna().sum())
    dates = values.map(date_candidates)
    date_count = sum(bool(v) for v in dates)
    date_shapes = sum(bool(DATE_SHAPE.match(v.strip())) for v in values)
    leading_zeros = int(values.str.match(r'^0\d+$').sum())
    distinct = values.nunique()
    cardinality = distinct / count if count else 0
    digit_ratio = float(values.str.fullmatch(r'\d+').mean()) if count else 0
    tokens = set(re.findall(r'[a-z]+', re.sub(r'([a-z])([A-Z])', r'\1 \2', str(series.name)).lower()))
    header_hint = bool(tokens & {'id', 'identifier', 'code', 'no', 'sku', 'ref', 'reference', 'key'})
    alpha_digit = values.str.fullmatch(r'(?:[A-Za-z]+\d+[A-Za-z0-9]*|\d+[A-Za-z][A-Za-z0-9]*)')
    mixed_ratio = float(alpha_digit.mean()) if count else 0
    # Scientific notation and signed quantities should not count as code patterns.
    mixed_ratio = float((alpha_digit & nums.isna()).mean()) if mixed_ratio else 0
    token_ratio = float(values.str.fullmatch(r'[A-Za-z0-9]{1,32}').mean()) if count else 0
    widths = values[values.str.fullmatch(r'\d{4,32}')].str.len().value_counts()
    fixed_width_ratio = float(widths.iloc[0] / count) if not widths.empty else 0
    identifier = bool(count and (
        leading_zeros or (digit_ratio == 1 and cardinality >= .95) or
        (header_hint and token_ratio >= .95 and (digit_ratio >= .5 or mixed_ratio > 0 or distinct >= 2)) or
        (token_ratio >= .95 and mixed_ratio >= .01 and digit_ratio + mixed_ratio >= .8 and distinct >= 3) or
        (digit_ratio == 1 and fixed_width_ratio >= .98 and distinct >= 20 and values.value_counts().iloc[0] / count <= .1)
    ))
    physical = 'empty' if not count else 'numeric' if numeric_count == count else 'date' if date_count == count else 'mixed' if (numeric_count / count >= .5 or date_shapes / count >= .5) else 'string'
    semantic = 'potential_identifier' if identifier else 'potential_date' if count and date_shapes / count >= .5 else 'potential_measurement' if count and numeric_count / count >= .8 else 'categorical' if count and cardinality < .5 else 'text'
    return {"physical_type": physical, "semantic_type": semantic, "proposed_target_type": 'string' if identifier else physical,
            "confidence": .95 if leading_zeros else .65 if identifier else .8 if physical != 'mixed' else .5,
            "evidence": {"numeric_count": numeric_count, "date_count": date_count, "date_shape_count": date_shapes,
                         "leading_zero_count": leading_zeros, "cardinality_ratio": cardinality, "digit_ratio": digit_ratio,
                         "identifier_header_hint": header_hint, "mixed_alphanumeric_ratio": mixed_ratio,
                         "compact_token_ratio": token_ratio, "fixed_digit_width_ratio": fixed_width_ratio},
            "potential_identifier": identifier, "potential_numeric": bool(not identifier and count and numeric_count / count >= .5),
            "potential_date": bool(count and date_shapes / count >= .5)}
