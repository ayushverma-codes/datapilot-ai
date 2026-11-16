from collections import Counter, defaultdict
from difflib import SequenceMatcher
import math
import pandas as pd
from app.profiling.type_inference import infer, numeric, date_candidates


def bounded(value):
    return str(value)[:200]


def profile_column(series):
    values = series.dropna().astype(str)
    total, count = len(series), len(values)
    inference = infer(series)
    frequencies = values.value_counts()
    top = [{"value": bounded(v), "count": int(n)} for v, n in frequencies.head(10).items()]
    groups = defaultdict(set)
    for value in frequencies.head(100).index:
        groups[value.strip().casefold()].add(value)
    variants = [sorted(map(bounded, group)) for group in groups.values() if len(group) > 1][:10]
    candidates = list(frequencies.head(30).index)
    fuzzy = []
    for i, left in enumerate(candidates):
        for right in candidates[i + 1:]:
            if left.casefold() != right.casefold() and SequenceMatcher(None, left.casefold(), right.casefold()).ratio() >= .85:
                fuzzy.append([bounded(left), bounded(right)])
    result = {"name": str(series.name), "storage_type": str(series.dtype), "inference": inference,
              "total": total, "non_null_count": count, "null_count": total - count,
              "null_percentage": 100 * (total - count) / total if total else 0,
              "unique_count": int(values.nunique()), "cardinality_ratio": values.nunique() / count if count else 0,
              "top_values": top, "sample_values": [bounded(x) for x in values.head(5)],
              "whitespace_count": int((values != values.str.strip()).sum()),
              "case_variants": variants, "suspicious_variants": fuzzy[:10],
              "rare_category_count": int((frequencies <= max(1, count * .01)).sum()),
              "rare_categories": [bounded(v) for v in frequencies[frequencies <= max(1, count * .01)].head(10).index],
              "constant": bool(count and values.nunique() == 1), "empty": count == 0}
    if inference['potential_numeric']:
        nums = pd.to_numeric(values.map(numeric), errors='coerce').dropna()
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
                             "invalid_count": count - len(nums), "outlier_count": len(outliers),
                             "outlier_samples": [float(v) for v in outliers.head(5)], "lower_fence": unscale(lower), "upper_fence": unscale(upper),
                             "outlier_context": {"method": "1.5 IQR", "review_only": True, "frequent_boundaries": boundaries, "explanations": context}}
    if inference['potential_date']:
        parsed = values.map(date_candidates)
        valid = [v[0][0] for v in parsed if v]
        result['date'] = {"valid_count": len(valid), "invalid_count": count - len(valid),
                          "ambiguous_count": sum(len({date for date, _ in v}) > 1 for v in parsed),
                          "formats": dict(Counter(fmt for v in parsed for _, fmt in v)),
                          "min": min(valid) if valid else None, "max": max(valid) if valid else None}
    return result


def generate_profile(frame, metadata, size=0):
    columns = [profile_column(frame[column]) for column in frame.columns]
    return {"row_count": len(frame), "column_count": len(frame.columns), "file_size": size,
            "memory_bytes": int(frame.memory_usage(deep=True).sum()), "duplicate_rows": int(frame.duplicated().sum()),
            "empty_rows": int(frame.isna().all(axis=1).sum()), "schema": metadata,
            "data_types": {str(c): str(frame[c].dtype) for c in frame.columns},
            "missing_cells": int(frame.isna().sum().sum()), "parsing_issues": metadata.get('parsing_issues', []), "columns": columns}
