"""Deterministic operations on validated retail dataset snapshots."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from itertools import islice
import re

import pandas as pd

from app.models.contracts import PipelineError


def require_columns(frame, columns):
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise PipelineError('UNKNOWN_RETAIL_COLUMNS', 'Source columns are missing', details={'columns': missing})


def cell(value):
    return None if pd.isna(value) else value


def key_rows(frame, columns):
    for values in frame[columns].itertuples(index=False, name=None):
        key = tuple(map(cell, values))
        yield key if all(value is not None and value.strip() for value in key) else None


@dataclass
class ValidatedJoin:
    left_keys: list
    right_index: dict
    join_type: str
    stats: dict

    def pairs(self):
        for left, key in enumerate(self.left_keys):
            matches = self.right_index.get(key, ())
            if matches:
                for right in matches:
                    yield left, right
            elif self.join_type == 'left':
                yield left, None


def validate_join(left, right, request, row_limit):
    require_columns(left, request.left_keys)
    require_columns(right, request.right_keys)
    left_keys = list(key_rows(left, request.left_keys))
    right_index = defaultdict(list)
    right_missing = 0
    for row, key in enumerate(key_rows(right, request.right_keys)):
        if key is None:
            right_missing += 1
        else:
            right_index[key].append(row)
    left_counts = Counter(key for key in left_keys if key is not None)
    shared = left_counts.keys() & right_index.keys()
    matched_left = sum(left_counts[key] for key in shared)
    matched_right = sum(len(right_index[key]) for key in shared)
    matched_pairs = sum(left_counts[key] * len(right_index[key]) for key in shared)
    left_missing = len(left_keys) - sum(left_counts.values())
    left_duplicates = sum(count - 1 for count in left_counts.values())
    right_duplicates = sum(len(rows) - 1 for rows in right_index.values())
    left_shape = 'many' if left_duplicates else 'one'
    right_shape = 'many' if right_duplicates else 'one'
    stats = {
        'observed_cardinality': f'{left_shape}_to_{right_shape}',
        'left_rows': len(left), 'right_rows': len(right),
        'left_missing_keys': left_missing, 'right_missing_keys': right_missing,
        'left_duplicate_key_rows': left_duplicates, 'right_duplicate_key_rows': right_duplicates,
        'left_unmatched_rows': len(left) - matched_left, 'right_unmatched_rows': len(right) - matched_right,
        'left_fanout_rows': matched_pairs - matched_left, 'right_fanout_rows': matched_pairs - matched_right,
        'max_matches_per_left_row': max((len(right_index[key]) for key in shared), default=0),
        'max_matches_per_right_row': max((left_counts[key] for key in shared), default=0),
        'output_rows': matched_pairs + (len(left) - matched_left if request.join_type == 'left' else 0),
    }
    expect_left, expect_right = request.cardinality.split('_to_')
    if ((left_duplicates and right_duplicates) or (expect_left == 'one' and left_duplicates)
            or (expect_right == 'one' and right_duplicates)):
        raise PipelineError('UNSAFE_JOIN_CARDINALITY', 'Keys violate the declared uniqueness or many-to-many is unsafe',
                            details=stats)
    if stats['output_rows'] > row_limit:
        raise PipelineError('JOIN_ROW_LIMIT', 'Projected join exceeds the configured row limit', details=stats)
    return ValidatedJoin(left_keys, right_index, request.join_type, stats)


def discover(left, right):
    """Bounded overlap hints. Registration always validates the complete keys."""
    def candidates(frame, profile):
        priority = {c['name'] for c in profile['columns'] if c['inference']['potential_identifier']}
        return sorted(frame.columns, key=lambda name: name not in priority)[:20]

    def shapes(frame, columns):
        return {name: {value for raw in frame[name].head(500) if (value := cell(raw)) is not None and value.strip()}
                for name in columns}

    left_frame, left_profile = left
    right_frame, right_profile = right
    hints = []
    for lcol, lvalues in shapes(left_frame, candidates(left_frame, left_profile)).items():
        for rcol, rvalues in shapes(right_frame, candidates(right_frame, right_profile)).items():
            overlap = len(lvalues & rvalues)
            same_name = re.sub(r'\W', '', lcol.casefold()) == re.sub(r'\W', '', rcol.casefold())
            fraction = overlap / min(len(lvalues), len(rvalues)) if lvalues and rvalues else 0
            if overlap and (same_name or (overlap >= 2 and fraction >= .6)):
                hints.append({'left_keys': [lcol], 'right_keys': [rcol], 'sample_distinct_overlap': overlap,
                              'sample_overlap_fraction': round(fraction, 4), 'matching_header': same_name,
                              'status': 'UNVERIFIED'})
    return sorted(hints, key=lambda h: (-h['matching_header'], -h['sample_overlap_fraction'],
                                        -h['sample_distinct_overlap'], h['left_keys'], h['right_keys']))[:20]


def normalize(source, mapping, spec, offset, limit):
    require_columns(source, [spec.column])
    approved = {}
    if mapping is not None:
        config = spec.mapping
        if config.source_key == config.canonical_value:
            raise PipelineError('INVALID_MAPPING', 'Mapping requires separate source and canonical fields')
        fields = [config.source_key, config.canonical_value]
        require_columns(mapping, fields)
        for source_key, target in mapping[fields].itertuples(index=False, name=None):
            key, target = cell(source_key), cell(target)
            if key is None or not key.strip() or key in approved:
                raise PipelineError('INVALID_MAPPING_KEYS', 'Mapping source keys must be nonblank and unique')
            if target is not None and not target.strip():
                target = None
            approved[key] = target
        if any(target in approved and approved[target] not in (None, target)
               for target in approved.values() if target is not None):
            raise PipelineError('AMBIGUOUS_MAPPING', 'Mapping chains and cycles require a single confirmed canonical value')

    counts = Counter(cell(value) for value in source[spec.column])
    variants = defaultdict(list)
    ordered = sorted(counts, key=lambda value: (value is not None, value or ''))
    for value in ordered:
        if value and value.strip():
            variants[value.strip().casefold()].append(value)
    statuses = Counter()
    entries = []
    for index, original in enumerate(ordered):
        count = counts[original]
        group = variants.get(original.strip().casefold(), []) if original else []
        canonical = approved.get(original)
        if original is None or not original.strip():
            status = 'MISSING_SOURCE'
        elif canonical is not None:
            status = 'MAPPED'
        elif len(group) > 1:
            status = 'UNCERTAIN'
        else:
            status = 'UNTRANSLATED'
        statuses[status] += count
        if offset <= index < offset + limit:
            entries.append({'original': original, 'canonical': canonical, 'status': status, 'records': count,
                            'potential_variants': list(islice((v for v in group if v != original), 5))})
    return {'values': entries, 'status_counts': dict(statuses), 'distinct_values': len(counts),
            'offset': offset, 'next_offset': offset + limit if offset + limit < len(counts) else None}


METRICS = {
    'total_revenue': (('revenue',), 'Sum declared line revenue in the source currency.'),
    'order_count': (('order_id',), 'Count distinct order identifiers in the filtered cohort.'),
    'average_order_value': (('revenue', 'order_id'), 'Sum declared line revenue / count distinct order identifiers.'),
    'units_sold': (('quantity',), 'Sum declared nonnegative integer units sold.'),
    'return_rate': (('order_id', 'return_status'), '100 × orders with at least one returned record / distinct orders.'),
    'inventory_availability': (('on_hand',), '100 × observations with on-hand quantity > 0 / inventory observations.'),
    'stock_out_frequency': (('on_hand',), '100 × observations with on-hand quantity = 0 / inventory observations.'),
    'category_performance': (('revenue', 'category'), 'Sum declared line revenue by category, including an unclassified bucket.'),
    'store_performance': (('revenue', 'store'), 'Sum declared line revenue by store, including an unclassified bucket.'),
}


def number(value, integer=False):
    # Bound Decimal complexity and preserve exact sums for the configured row limit.
    if value is None or len(value) > 64 or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
        raise ValueError
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError from exc
    if not result.is_finite() or not -12 <= result.as_tuple().exponent <= 24 or result.adjusted() >= 24:
        raise ValueError
    if integer and (result < 0 or result != result.to_integral_value()):
        raise ValueError
    return result


def require_base_grain(joined):
    if joined and (joined.stats['left_fanout_rows'] or joined.stats['output_rows'] != joined.stats['left_rows']):
        raise PipelineError('UNSAFE_METRIC_JOIN', 'Metrics require a join that preserves every base row exactly once',
                            details=joined.stats)


def compute_metrics(frames, sources, request, join_config=None, joined=None):
    require_base_grain(joined)
    filters = [(rule.side, rule.column, set(rule.values)) for rule in request.filters]
    for side, col, _ in filters:
        if side not in frames:
            raise PipelineError('INVALID_RETAIL_FILTER', 'Right-side filters require a registered relationship')
        require_columns(frames[side], [col])

    refs = {(ref.side, ref.column) for ref in request.fields.values()} | {(side, col) for side, col, _ in filters}
    values = {(side, col): frames[side][col].array for side, col in refs
              if side in frames and col in frames[side].columns}

    def read(side, col, pair):
        index = pair[0] if side == 'left' else pair[1]
        return cell(values[side, col][index]) if index is not None else None

    pairs = joined.pairs() if joined else ((index, None) for index in range(len(frames['left'])))
    cohort = [pair for pair in pairs if all(read(side, col, pair) in allowed for side, col, allowed in filters)]
    results = {}
    for metric in request.metrics:
        roles, formula = METRICS[metric]
        if 'revenue' in roles and 'revenue' not in request.fields:
            if 'unit_price' in request.fields and 'quantity' in request.fields:
                roles = ('unit_price', 'quantity', *roles[1:])
                formula = formula.replace('declared line revenue', '(unit price × units sold)')
        fields = {role: request.fields[role].model_dump() for role in roles if role in request.fields}
        evidence = {'source_datasets': sources, 'source_fields': fields,
                    'filters': [rule.model_dump() for rule in request.filters], 'join': join_config,
                    'formula': formula, 'records_considered': len(cohort), 'missing_evidence': []}
        if metric == 'return_rate':
            evidence['return_status_values'] = {'returned': request.returned_values, 'not_returned': request.non_returned_values}
        missing = evidence['missing_evidence']
        for role in roles:
            if role not in fields:
                missing.append({'role': role, 'reason': 'FIELD_MAPPING_REQUIRED'})
            elif (fields[role]['side'], fields[role]['column']) not in values:
                missing.append({'role': role, 'reason': 'SOURCE_FIELD_NOT_FOUND'})
            elif role not in ('category', 'store') and fields[role]['side'] != 'left':
                missing.append({'role': role, 'reason': 'MEASURE_FIELDS_REQUIRE_BASE_DATASET'})
        if metric == 'return_rate' and not (request.returned_values and request.non_returned_values):
            missing.append({'role': 'return_status', 'reason': 'EXPLICIT_RETURN_STATUS_VALUES_REQUIRED'})
        if not missing:
            with localcontext() as context:
                context.prec = 96
                context.rounding = ROUND_HALF_EVEN
                result = aggregate(metric, cohort, fields, read, request, missing)
        else:
            result = None
        if joined and joined.stats['left_unmatched_rows']:
            missing.append({'reason': 'UNMATCHED_BASE_ROWS_RETAINED', 'records': joined.stats['left_unmatched_rows']})
        results[metric] = {'status': 'OK' if result is not None else 'UNSUPPORTED',
                           'value': result, 'evidence': evidence}
    return results


def aggregate(metric, cohort, fields, read, request, missing):
    total = Decimal(0)
    orders, returned = set(), set()
    groups = defaultdict(lambda: [Decimal(0), 0])
    invalid = Counter()
    positive = zero = 0
    for pair in cohort:
        row = {role: read(ref['side'], ref['column'], pair) for role, ref in fields.items()}
        for role in ('revenue', 'unit_price', 'quantity', 'on_hand'):
            if role in row:
                try:
                    row[role] = number(row[role], integer=role in ('quantity', 'on_hand'))
                except ValueError:
                    invalid[role] += 1
                    row[role] = None
        if 'order_id' in row:
            order = row['order_id']
            if order is None or not order.strip():
                invalid['order_id'] += 1
            else:
                orders.add(order)
        if 'return_status' in row:
            if row['return_status'] in request.returned_values:
                returned.add(row['order_id'])
            elif row['return_status'] not in request.non_returned_values:
                invalid['return_status'] += 1
        if row.get('on_hand') is not None:
            positive += row['on_hand'] > 0
            zero += row['on_hand'] == 0
        line_amount = row.get('revenue')
        if row.get('unit_price') is not None and row.get('quantity') is not None:
            line_amount = row['unit_price'] * row['quantity']
        amount = row.get('quantity') if metric == 'units_sold' else line_amount
        if amount is not None:
            total += amount
        if metric in ('category_performance', 'store_performance') and line_amount is not None:
            group = row['category' if metric == 'category_performance' else 'store']
            group = group if group and group.strip() else None
            groups[group][0] += line_amount
            groups[group][1] += 1
            if len(groups) > 1000:
                missing.append({'reason': 'GROUP_LIMIT_EXCEEDED', 'limit': 1000})
                return None
    if invalid:
        missing.extend({'role': role, 'reason': 'MISSING_OR_INVALID_VALUES', 'records': count}
                       for role, count in sorted(invalid.items()))
        return None

    def ratio(numerator, denominator):
        if not denominator:
            missing.append({'reason': 'ZERO_DENOMINATOR'})
            return None
        return format((Decimal(numerator) / denominator).quantize(Decimal('.0001')), 'f')

    if metric == 'order_count':
        return len(orders)
    if metric == 'average_order_value':
        return ratio(total, len(orders))
    if metric == 'return_rate':
        return ratio(100 * len(returned), len(orders))
    if metric == 'inventory_availability':
        return ratio(100 * positive, len(cohort))
    if metric == 'stock_out_frequency':
        return ratio(100 * zero, len(cohort))
    if metric in ('category_performance', 'store_performance'):
        if None in groups:
            missing.append({'reason': 'UNCLASSIFIED_DIMENSION', 'records': groups[None][1]})
        return [{'group': group, 'revenue': format(value, 'f'), 'records': count}
                for group, (value, count) in sorted(groups.items(), key=lambda item: (item[0] is None, item[0] or ''))]
    return int(total) if metric == 'units_sold' else format(total, 'f')
