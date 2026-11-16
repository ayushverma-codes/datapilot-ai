from uuid import NAMESPACE_URL, uuid5
from app.models.contracts import Step
from app.cleaning.transformation_registry import REGISTRY, validate_operation


def generate_plan(file_id, profile, issues, suggestions, overrides=None):
    steps = []
    overrides = overrides or {}
    available = [c['name'] for c in profile['columns']]

    def add(operation, columns, params, reason, source='CODE', issue_ids=None, ai_risk='medium'):
        validate_operation(operation, columns, params, available)
        levels = ['low', 'medium', 'high']
        risk = max([REGISTRY[operation].risk, overrides.get(operation, 'low'), ai_risk if source == 'AI' else 'low'], key=levels.index)
        step = Step(id=uuid5(NAMESPACE_URL, f'{file_id}:{len(steps)}:{operation}:{columns}').hex,
                    file_id=file_id, issue_ids=issue_ids or [], operation=operation, columns=columns,
                    parameters=params, reason=reason, source=source, risk=risk, approval_required=risk != 'low',
                    expected_impact='Flag for review; no data mutation' if operation == 'flag_outliers' else f'Apply {operation} to selected scope; record changed rows',
                    status='AUTO_APPROVED' if risk == 'low' else 'AWAITING_APPROVAL')
        steps.append(step)

    for issue in issues:
        if issue.category == 'whitespace':
            add('trim_whitespace', [issue.column], {}, issue.description, issue_ids=[issue.id])
    # Include these even if initially clean: trimming can create empty/duplicate rows.
    for operation, category in [('remove_empty_rows', 'empty_rows'), ('remove_exact_duplicates', 'duplicate_rows')]:
        add(operation, [], {}, 'Remove only fully empty or exactly identical records after formatting', issue_ids=[i.id for i in issues if i.category == category])
    for c in profile['columns']:
        inference = c['inference']
        if inference['physical_type'] == 'numeric' and not inference['potential_identifier']:
            add('convert_numeric', [c['name']], {}, 'Review numeric interpretation before canonical decimal formatting')
        if 'date' in c and not c['date']['invalid_count'] and not c['date']['ambiguous_count']:
            add('parse_dates', [c['name']], {}, 'Review date interpretation before ISO formatting')
    for issue in issues:
        if issue.category == 'potential_outliers':
            add('flag_outliers', [issue.column], {}, 'Statistical outliers may be legitimate; retain values', issue_ids=[issue.id])
        if issue.category == 'case_inconsistency':
            mapping = {v: group[0] for group in issue.evidence if all(len(value) < 200 for value in group)
                       for v in group if v != group[0]}
            if mapping:
                add('category_normalization', [issue.column], {'mapping': mapping}, 'Review possible case-equivalent categories', issue_ids=[issue.id])
    for suggestion in suggestions:
        if suggestion.operation == 'convert_numeric' and any(
            c['name'] == suggestion.column and c['inference']['potential_identifier'] for c in profile['columns']
        ):
            continue
        add(suggestion.operation, [suggestion.column], suggestion.parameters, suggestion.reason, 'AI', ai_risk=suggestion.risk)
    # Run row removal last so approved formatting/mappings cannot create duplicates
    # that would only disappear on a second pass.
    tail = {'remove_empty_rows': 1, 'remove_exact_duplicates': 2}
    return sorted(steps, key=lambda step: tail.get(step.operation, 0))
