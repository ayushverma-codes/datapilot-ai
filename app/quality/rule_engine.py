from uuid import NAMESPACE_URL, uuid5
from app.models.contracts import Issue


RULES = {}


def rule(name):
    def register(function):
        RULES[name] = function
        return function
    return register


@rule('structural')
def structural(profile):
    for key, category, action in [('duplicate_rows', 'duplicate_rows', 'remove_exact_duplicates'), ('empty_rows', 'empty_rows', 'remove_empty_rows')]:
        if profile[key]:
            yield None, category, profile[key], [{'record_numbers': profile.get(key.replace('_rows', '_record_samples'), [])}], action
    for name, count in profile['schema'].get('duplicate_headers', {}).items():
        yield name, 'duplicate_headers', profile['row_count'], [{'header': name[:200], 'occurrences': count}], 'review'
    for error in profile.get('parsing_issues', []):
        yield None, 'malformed_records', 1, [error], 'review'


@rule('columns')
def columns(profile):
    for c in profile['columns']:
        if c['null_count']:
            yield c['name'], 'missing_values', c['null_count'], [{'record_numbers': c.get('missing_record_samples', [])}], 'fill_missing'
        if c['whitespace_count']:
            yield c['name'], 'whitespace', c['whitespace_count'], c.get('whitespace_samples', c['sample_values']), 'trim_whitespace'
        if c['empty']:
            yield c['name'], 'empty_column', c['total'], [{'non_null_count': 0}], 'review'
        if c['constant']:
            yield c['name'], 'constant_column', c['non_null_count'], c['sample_values'], 'review'
        if c['inference']['physical_type'] == 'mixed' and not c['inference']['potential_identifier']:
            counts = c['inference']['evidence']
            evidence = [{'numeric_count': counts['numeric_count'], 'date_count': counts['date_count'],
                         'non_null_count': c['non_null_count']}, *c['sample_values']]
            yield c['name'], 'mixed_types', c['non_null_count'], evidence, 'review'
        if c['inference']['potential_identifier'] and c['unique_count'] < c['non_null_count']:
            yield c['name'], 'duplicate_identifiers', c['non_null_count'] - c['unique_count'], c.get('duplicate_identifier_samples', c['top_values'][:5]), 'review'
        if c['case_variants']:
            yield c['name'], 'case_inconsistency', c['case_variant_row_count'], c['case_variants'], 'category_normalization'
        if c['suspicious_variants']:
            yield c['name'], 'category_variants', c['suspicious_variant_row_count'], c['suspicious_variants'], 'review'
        if 'numeric' in c:
            for key, category, action in [('invalid_count', 'invalid_numeric', 'review'), ('outlier_count', 'potential_outliers', 'flag_outliers')]:
                if c['numeric'][key]:
                    evidence = c['numeric'].get('invalid_samples', [])
                    if category == 'potential_outliers':
                        evidence = c['numeric'].get('outlier_samples', [])
                        if 'outlier_context' in c['numeric']:
                            evidence = [*evidence, c['numeric']['outlier_context']]
                    yield c['name'], category, c['numeric'][key], evidence, action
        if 'date' in c:
            for key, category in [('invalid_count', 'invalid_dates'), ('ambiguous_count', 'ambiguous_dates')]:
                if c['date'][key]:
                    yield c['name'], category, c['date'][key], c['date'].get(key.replace('_count', '_samples'), c['sample_values']), 'review'
            if c['date']['format_outlier_count']:
                yield c['name'], 'inconsistent_date_formats', c['date']['format_outlier_count'], [c['date']['formats']], 'review'


def make_issue(run_id, file_id, column, category, count, row_count, evidence, action, *, description=None, severity='warning'):
    return Issue(id=uuid5(NAMESPACE_URL, f'{file_id}:{column}:{category}').hex,
                 run_id=run_id, file_id=file_id, column=column, category=category,
                 description=description or category.replace('_', ' ').capitalize(),
                 severity=severity, affected_count=count,
                 affected_percentage=min(100, 100 * count / row_count) if row_count else 0,
                 evidence=evidence[:10], recommended_action=action)


def detect(profile, run_id, file_id, enabled=None):
    issues = []
    for name, function in RULES.items():
        if enabled is not None and name not in enabled:
            continue
        for column, category, count, evidence, action in function(profile):
            description = 'Potential statistical extremes; review with domain context' if category == 'potential_outliers' else None
            issues.append(make_issue(run_id, file_id, column, category, count, profile['row_count'],
                                     evidence, action, description=description,
                                     severity='error' if category == 'malformed_records' else 'warning'))
    return issues
