"""Validate and execute the assistant's allow-listed analytical tools."""

from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from app.conversation.assistant import _state, _value, accumulate, matches_value
from app.models.contracts import PipelineError
from app.retail_contracts import MetricsRequest
from app.retail_engine import cell, compute_metrics, number, require_base_grain

NUMERIC = {'sum', 'average', 'min', 'max'}
RANGE = {'gt', 'gte', 'lt', 'lte'}


def bounded_number(raw, column):
    try:
        return number(raw)
    except ValueError as exc:
        raise PipelineError('NON_NUMERIC_VALUE', f'Column {column!r} requires a supported finite number') from exc


def validate_tools(plan, schemas):
    """Bind every plan field and enforce type and operator restrictions before execution."""
    used = {'left': set(), 'right': set()}

    def bind(ref, numeric=False):
        schema = schemas.get(ref.side, {})
        if ref.column not in schema:
            raise PipelineError('INVALID_QUERY_PLAN', 'The plan references an unavailable column',
                                details={'side': ref.side, 'column': ref.column})
        if numeric and schema[ref.column] != 'numeric':
            raise PipelineError('INVALID_QUERY_TYPE', 'This operation requires a numeric column',
                                details={'side': ref.side, 'column': ref.column})
        used[ref.side].add(ref.column)

    if plan.column:
        bind(plan.column, plan.intent == 'statistics' or plan.operation in NUMERIC)
    if plan.group_by:
        bind(plan.group_by)
    for ref in plan.columns:
        bind(ref)
    if len({(ref.side, ref.column) for ref in plan.columns}) != len(plan.columns):
        raise PipelineError('INVALID_QUERY_PLAN', 'Projected columns must be distinct')
    if plan.sort and plan.sort.field:
        bind(plan.sort.field)
    for condition in [*plan.filters, *plan.cohort_a, *plan.cohort_b]:
        compare = condition.operator in RANGE
        bind(condition, compare)
        if compare:
            bounded_number(condition.value, condition.column)
        if plan.intent == 'retail' and condition.operator not in ('eq', 'is_null'):
            raise PipelineError('INVALID_QUERY_PLAN', 'Retail metrics accept exact or null filters')
    for role, ref in plan.fields.items():
        bind(ref, role in ('revenue', 'unit_price', 'quantity', 'on_hand'))
        if ref.side != 'left' and role not in ('category', 'store'):
            raise PipelineError('INVALID_QUERY_PLAN', 'Retail measures must come from the base dataset')
    return {key: sorted(columns) for key, columns in used.items() if columns}


def display(value):
    return value if value is None or len(value) <= 200 else value[:199] + '…'


def run_tools(plan, frames, schemas, used, sources, join_config=None, joined=None):
    require_base_grain(joined)
    scanned = len(frames['left'])
    if plan.intent == 'inspect':
        entries = [{'column': display(name), 'type': kind} for name, kind in schemas['left'].items()]
        return {'rows_scanned': scanned, 'rows_matched': scanned, 'column_count': len(entries),
                'columns': entries[:plan.limit], 'truncated': len(entries) > plan.limit}
    if plan.intent == 'retail':
        request = MetricsRequest(file_id=sources['left']['file_id'], metrics=plan.metrics, fields=plan.fields,
                                 filters=[{'side': f.side, 'column': f.column, 'values': [f.value]} for f in plan.filters],
                                 returned_values=plan.returned_values, non_returned_values=plan.non_returned_values)
        results = compute_metrics(frames, sources, request, join_config, joined)
        for metric in results.values():
            if isinstance(metric['value'], list):
                metric['group_count'] = len(metric['value'])
                metric['value'] = [{**group, 'group': display(group['group'])} for group in metric['value'][:plan.limit]]
        matched = {entry['evidence']['records_considered'] for entry in results.values()}
        if len(matched) != 1:
            raise PipelineError('RESULT_VERIFICATION_FAILED', 'Retail tools returned inconsistent cohort counts')
        return {'rows_scanned': scanned, 'rows_matched': matched.pop(), 'metrics': results}

    arrays = {(side, col): frames[side][col].array for side, cols in used.items() for col in cols}

    def read(ref, pair):
        index = pair[0 if ref.side == 'left' else 1]
        return cell(arrays[ref.side, ref.column][index]) if index is not None else None

    def matches(pair, filters):
        return all(matches_value(read(condition, pair), condition, bounded_number) for condition in filters)

    pairs = joined.pairs() if joined else ((i, None) for i in range(scanned))
    cohort = [pair for pair in pairs if matches(pair, plan.filters)]
    with localcontext() as ctx:
        ctx.prec = 64
        ctx.rounding = ROUND_HALF_EVEN
        data = summarize(plan, cohort, schemas, read, matches)
    return {'rows_scanned': scanned, 'rows_matched': len(cohort), **data}


def summarize(plan, cohort, schemas, read, matches):
    if plan.intent == 'rows':
        if plan.sort:
            ref = plan.sort.field
            values = [(read(ref, pair), pair) for pair in cohort]
            valid = [(raw, pair) for raw, pair in values if raw is not None and raw.strip()]
            blanks = [pair for raw, pair in values if raw is None or not raw.strip()]
            numeric = schemas[ref.side][ref.column] == 'numeric'
            ordered = sorted(valid, key=lambda entry: bounded_number(entry[0], ref.column) if numeric else entry[0],
                             reverse=plan.sort.direction == 'desc')
            cohort = [pair for _, pair in ordered] + blanks
        return {'columns': [ref.model_dump() for ref in plan.columns],
                'rows': [[display(read(ref, pair)) for ref in plan.columns] for pair in cohort[:plan.limit]],
                'truncated': len(cohort) > plan.limit}
    if plan.intent == 'distinct':
        values = {read(plan.column, pair) for pair in cohort}
        values = sorted((value for value in values if value is not None and value.strip()),
                        reverse=bool(plan.sort and plan.sort.direction == 'desc'))
        return {'distinct_count': len(values), 'values': [display(value) for value in values[:plan.limit]],
                'truncated': len(values) > plan.limit}
    if plan.intent == 'statistics':
        values = [read(plan.column, pair) for pair in cohort]
        nums = sorted(bounded_number(value, plan.column.column) for value in values
                      if value is not None and value.strip())
        n = len(nums)
        result = {'count': n, 'null_count': len(values) - n}
        if n:
            mean = sum(nums, Decimal(0)) / n
            median = (nums[n // 2] + nums[(n - 1) // 2]) / 2
            sd = (sum(((num - mean) ** 2 for num in nums), Decimal(0)) / n).sqrt()
            result.update(min=str(nums[0]), max=str(nums[-1]), mean=str(mean), median=str(median),
                          population_stddev=str(sd))
        return {'statistics': result}

    def add(state, pair):
        raw = read(plan.column, pair) if plan.column else None
        accumulate(state, raw, plan.operation, plan.column.column if plan.column else None, bounded_number)

    state = _state()
    groups = {}
    if plan.intent == 'compare':
        states = [_state(), _state()]
        for pair in cohort:
            for index, filters in enumerate((plan.cohort_a, plan.cohort_b)):
                if matches(pair, filters):
                    add(states[index], pair)
        a, b = [_value(item, plan.operation) for item in states]
        return {'cohort_a': {'value': a, 'records': states[0]['rows']},
                'cohort_b': {'value': b, 'records': states[1]['rows']},
                'difference_a_minus_b': str(Decimal(a) - Decimal(b)) if a is not None and b is not None else None}
    for pair in cohort:
        add(state, pair)
        if plan.group_by:
            group = read(plan.group_by, pair)
            if group not in groups:
                if len(groups) >= 1000:
                    raise PipelineError('ANALYTICAL_GROUP_LIMIT', 'Filter the query to at most 1,000 groups')
                groups[group] = _state()
            add(groups[group], pair)
    result = {'value': _value(state, plan.operation)}
    if plan.operation in NUMERIC:
        result['usable_values'] = state['numeric_count']
    if plan.group_by:
        entries = [(name, _value(item, plan.operation), item['rows']) for name, item in groups.items()]
        entries.sort(key=lambda item: (item[0] is not None, item[0] or ''))
        if plan.sort:
            valid = sorted((item for item in entries if item[1] is not None),
                           key=lambda item: Decimal(item[1]), reverse=plan.sort.direction == 'desc')
            entries = valid + [item for item in entries if item[1] is None]
        result.update(groups=[{'group': display(name), 'value': value, 'records': rows}
                              for name, value, rows in entries[:plan.limit]],
                      group_count=len(entries), truncated=len(entries) > plan.limit)
    return result
