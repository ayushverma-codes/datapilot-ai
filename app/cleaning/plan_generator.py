import json
import re
from uuid import NAMESPACE_URL, uuid5
from app.models.contracts import Step
from app.cleaning.transformation_registry import REGISTRY, validate_operation

RISK_LEVELS = ['low', 'medium', 'high']
# Common UTF-8 bytes misread as Windows-1252, or a decoder replacement character.
SUSPECT_TEXT = re.compile(r'[\ufffd\x80-\x9f]|[ÃÂ][\u00a0-\u00bf]|â[€™€]')
ISSUE_CATEGORIES = {
    'convert_numeric': {'mixed_types', 'invalid_numeric'},
    'parse_dates': {'invalid_dates', 'ambiguous_dates', 'inconsistent_date_formats'},
    'category_normalization': {'case_inconsistency', 'category_variants'},
    'normalize_strings': {'case_inconsistency'},
}


def related_issue_ids(issues, operation, columns):
    return [issue.id for issue in issues if issue.column in columns and
            (issue.recommended_action == operation or issue.category in ISSUE_CATEGORIES.get(operation, ()))]


def case_mapping(groups):
    mapping = {}
    for group in groups:
        if any(len(value) >= 200 or SUSPECT_TEXT.search(value) for value in group):
            continue
        trimmed = {value.strip() for value in group}
        if not trimmed or any(not value or any(ord(char) < 32 for char in value) for value in trimmed):
            continue
        if len({value.casefold() for value in trimmed}) != 1:
            continue
        # Whitespace is applied earlier. Preserve lexical selection among cleaned spellings.
        canonical = min(trimmed)
        mapping.update({value: canonical for value in sorted(trimmed - {canonical})})
    return mapping


def generate_plan(file_id, profile, issues, suggestions, overrides=None):
    steps = []
    overrides = overrides or {}
    available = [c['name'] for c in profile['columns']]
    step_ids = set()

    def add(operation, columns, params, reason, source='CODE', issue_ids=None, ai_risk='medium'):
        validate_operation(operation, columns, params, available)
        risk = max([REGISTRY[operation].risk, overrides.get(operation, 'low'),
                    max(ai_risk, 'medium', key=RISK_LEVELS.index) if source == 'AI' else 'low'], key=RISK_LEVELS.index)
        identity = json.dumps([file_id, source, operation, columns, params, risk], sort_keys=True)
        step_id = uuid5(NAMESPACE_URL, identity).hex
        if step_id in step_ids:
            return
        step_ids.add(step_id)
        step = Step(id=step_id,
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
            add('convert_numeric', [c['name']], {}, 'Review numeric interpretation before canonical decimal formatting',
                issue_ids=related_issue_ids(issues, 'convert_numeric', [c['name']]))
        if 'date' in c and not c['date']['invalid_count'] and not c['date']['ambiguous_count']:
            add('parse_dates', [c['name']], {}, 'Review date interpretation before ISO formatting',
                issue_ids=related_issue_ids(issues, 'parse_dates', [c['name']]))
    for issue in issues:
        if issue.category == 'potential_outliers':
            add('flag_outliers', [issue.column], {}, 'Statistical outliers may be legitimate; retain values', issue_ids=[issue.id])
        if issue.category == 'case_inconsistency':
            mapping = case_mapping(issue.evidence)
            if mapping:
                add('category_normalization', [issue.column], {'mapping': mapping}, 'Review possible case-equivalent categories', issue_ids=[issue.id])
    for suggestion in suggestions:
        if suggestion.operation == 'convert_numeric' and any(
            c['name'] == suggestion.column and c['inference']['potential_identifier'] for c in profile['columns']
        ):
            continue
        add(suggestion.operation, [suggestion.column], suggestion.parameters, suggestion.reason, 'AI',
            issue_ids=related_issue_ids(issues, suggestion.operation, [suggestion.column]), ai_risk=suggestion.risk)
    # Run row removal last so approved formatting/mappings cannot create duplicates
    # that would only disappear on a second pass.
    tail = {'remove_empty_rows': 1, 'remove_exact_duplicates': 2}
    return sorted(steps, key=lambda step: tail.get(step.operation, 0))
