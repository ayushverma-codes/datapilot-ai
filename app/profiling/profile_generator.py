from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from itertools import islice
import math
import pandas as pd
from app.profiling.type_inference import infer, numeric, date_candidates


def bounded(value):
    return str(value)[:200]


def record_samples(mask):
    return list(islice((position for position, matches in enumerate(mask, 1) if matches), 5))


def date_sort_key(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=None) - datetime.min - (parsed.utcoffset() or timedelta())


def profile_column(series):
    values = series.dropna().astype('string')
    total, count = len(series), len(values)
    inference = infer(series)
    frequencies = values.value_counts()
    unique = len(frequencies)
    top = [{"value": bounded(v), "count": int(n)} for v, n in frequencies.head(10).items()]
    trimmed = values.str.strip()
    whitespace = values != trimmed
    spellings = frequencies.index.str.strip()
    normalized = spellings.str.casefold()
    distinct_spellings = normalized[~spellings.duplicated()]
    case_members = frequencies[normalized.isin(distinct_spellings[distinct_spellings.duplicated()])]
    groups = defaultdict(set)
    for value in case_members.index:
        key = value.strip().casefold()
        if (key in groups or len(groups) < 10) and len(groups[key]) < 10:
            groups[key].add(value)
    variants = [sorted(map(bounded, group)) for group in groups.values()]
    candidates = [value for value in frequencies.head(30).index if len(value) <= 200]
    fuzzy = []
    fuzzy_values = set()
    for i, left in enumerate(candidates):
        for right in candidates[i + 1:]:
            if left.casefold() != right.casefold() and SequenceMatcher(None, left.casefold(), right.casefold()).ratio() >= .85:
                fuzzy_values.update((left, right))
                if len(fuzzy) < 10:
                    fuzzy.append([left, right])
    rare = frequencies[frequencies <= max(1, count * .01)]
    result = {"name": str(series.name), "storage_type": str(series.dtype), "inference": inference,
              "total": total, "non_null_count": count, "null_count": total - count,
              "null_percentage": 100 * (total - count) / total if total else 0,
              "unique_count": unique, "cardinality_ratio": unique / count if count else 0,
              "top_values": top, "sample_values": [bounded(x) for x in values.head(5)],
              "missing_record_samples": record_samples(series.isna()) if count < total else [],
              "whitespace_count": int(whitespace.sum()),
              "whitespace_samples": [bounded(v) for v in values[whitespace].head(5)],
              "case_variants": variants, "case_variant_row_count": int(case_members.sum()),
              "suspicious_variants": fuzzy, "suspicious_variant_row_count": sum(int(frequencies[v]) for v in fuzzy_values),
              "rare_category_count": len(rare), "rare_categories": [bounded(v) for v in rare.head(10).index],
              "constant": bool(count and unique == 1), "empty": count == 0}
    if inference['potential_identifier'] and unique < count:
        result['duplicate_identifier_samples'] = [
            {"value": bounded(v), "count": int(n)} for v, n in frequencies[frequencies > 1].head(5).items()
        ]
    if inference['potential_numeric']:
        parsed_numbers = values.map(numeric)
        nums = parsed_numbers.dropna()
        # Scale before aggregation so extreme but finite values do not overflow
        # intermediate sums, variances, quantile interpolation or bin edges.
        scale = max(abs(float(nums.min())), abs(float(nums.max()))) or 1.0
        scaled = nums / scale
        q1, q3 = float(scaled.quantile(.25)), float(scaled.quantile(.75))
        lower, upper = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        outliers = nums[(scaled < lower) | (scaled > upper)]
        def unscale(number):
            value = float(number) * scale
            return value if math.isfinite(value) else None
        median = float(scaled.median())
        left = median - float(scaled.quantile(.05))
        right = float(scaled.quantile(.95)) - median
        minimum, maximum = float(nums.min()), float(nums.max())
        boundaries = []
        if minimum != maximum:
            for value in (minimum, maximum):
                occurrences = int((nums == value).sum())
                if occurrences >= 2 and occurrences / len(nums) >= .05:
                    boundaries.append({'value': value, 'count': occurrences, 'fraction': occurrences / len(nums)})
        context = ['The 1.5 IQR rule flags statistical extremes for review. Domain evidence is needed to determine validity.']
        if q1 == q3 and minimum != maximum:
            context.append('The middle half has zero spread, so the rule can flag ordinary values outside the central group.')
        if max(left, right) > 3 * min(left, right):
            tail = 'upper' if right > left else 'lower'
            context.append(f'The {tail} tail is longer. A skewed distribution can produce many valid extremes.')
        if boundaries:
            context.append('A frequently repeated boundary value may be a default, sentinel, or legitimate observation. Confirm its meaning before changing it.')
        result['numeric'] = {"min": float(nums.min()), "max": float(nums.max()), "mean": unscale(scaled.mean()),
                             "median": unscale(scaled.median()), "std": unscale(scaled.std(ddof=0)),
                             "quantiles": {str(q): unscale(scaled.quantile(q)) for q in [.05, .25, .5, .75, .95]},
                             "distribution": [{"scaled_interval": str(k), "count": int(v)} for k, v in pd.cut(scaled, bins=5, duplicates='drop').value_counts(sort=False).items()] if nums.max() != nums.min() else [{"value": float(nums.iloc[0]), "count": len(nums)}],
                             "distribution_scale": scale,
                             "invalid_count": count - len(nums),
                             "invalid_samples": [bounded(v) for v in values[parsed_numbers.isna()].head(5)],
                             "outlier_count": len(outliers),
                             "outlier_samples": [float(v) for v in outliers.head(5)], "lower_fence": unscale(lower), "upper_fence": unscale(upper),
                             "outlier_context": {"method": "1.5 IQR", "review_only": True, "frequent_boundaries": boundaries, "explanations": context}}
    if inference['potential_date']:
        parsed = values.map(date_candidates)
        formats = Counter()
        invalid_samples, ambiguous_samples = [], []
        valid_count = ambiguous_count = 0
        minimum = maximum = None
        for value, candidates in zip(values, parsed):
            if not candidates:
                if len(invalid_samples) < 5:
                    invalid_samples.append(bounded(value))
                continue
            valid_count += 1
            formats.update({fmt for _, fmt in candidates})
            dates = {date for date, _ in candidates}
            if len(dates) > 1:
                ambiguous_count += 1
                if len(ambiguous_samples) < 5:
                    ambiguous_samples.append(bounded(value))
            # Compare explicit offsets in UTC. Naive values retain their reported wall time.
            ordered = [(date_sort_key(date), date) for date in dates]
            start, end = min(ordered), max(ordered)
            minimum = start if minimum is None else min(minimum, start)
            maximum = end if maximum is None else max(maximum, end)
        result['date'] = {"valid_count": valid_count, "invalid_count": count - valid_count,
                          "invalid_samples": invalid_samples, "ambiguous_count": ambiguous_count,
                          "ambiguous_samples": ambiguous_samples, "formats": dict(sorted(formats.items())),
                          "format_outlier_count": valid_count - max(formats.values(), default=0),
                          "min": minimum[1] if minimum else None, "max": maximum[1] if maximum else None}
    return result


def generate_profile(frame, metadata, size=0):
    columns = [profile_column(frame[column]) for column in frame.columns]
    duplicates = frame.duplicated()
    empty = frame.isna().all(axis=1)
    return {"row_count": len(frame), "column_count": len(frame.columns), "file_size": size,
            "memory_bytes": int(frame.memory_usage(deep=True).sum()), "duplicate_rows": int(duplicates.sum()),
            "duplicate_record_samples": record_samples(duplicates),
            "empty_rows": int(empty.sum()), "empty_record_samples": record_samples(empty), "schema": metadata,
            "data_types": {str(c): str(frame[c].dtype) for c in frame.columns},
            "missing_cells": sum(c['null_count'] for c in columns),
            "parsing_issues": metadata.get('parsing_issues', []), "columns": columns}
