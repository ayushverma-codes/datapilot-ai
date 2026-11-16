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
            yield None, category, profile[key], [], action
    for name, count in profile['schema'].get('duplicate_headers', {}).items():
        yield None, 'duplicate_headers', count, [name], 'review'
    for error in profile.get('parsing_issues', []):
        yield None, 'malformed_records', 1, [error], 'review'


@rule('columns')
def columns(profile):
    for c in profile['columns']:
        checks = [('null_count', 'missing_values', 'fill_missing'), ('whitespace_count', 'whitespace', 'trim_whitespace')]
        for key, category, action in checks:
            if c[key]:
                yield c['name'], category, c[key], c['sample_values'], action
        if c['empty']:
            yield c['name'], 'empty_column', c['total'], [], 'review'
        if c['constant']:
            yield c['name'], 'constant_column', c['non_null_count'], c['sample_values'], 'review'
        if c['inference']['physical_type'] == 'mixed' and not c['inference']['potential_identifier']:
            yield c['name'], 'mixed_types', c['non_null_count'], c['sample_values'], 'review'
        if c['inference']['potential_identifier'] and c['unique_count'] < c['non_null_count']:
            yield c['name'], 'duplicate_identifiers', c['non_null_count'] - c['unique_count'], c['top_values'][:5], 'review'
        if c['case_variants']:
            yield c['name'], 'case_inconsistency', sum(x['count'] for x in c['top_values'] if any(x['value'] in group for group in c['case_variants'])), c['case_variants'], 'category_normalization'
        if c['suspicious_variants']:
            yield c['name'], 'category_variants', 0, c['suspicious_variants'], 'review'
        if 'numeric' in c:
            for key, category, action in [('invalid_count', 'invalid_numeric', 'review'), ('outlier_count', 'potential_outliers', 'flag_outliers')]:
                if c['numeric'][key]:
                    evidence = []
                    if category == 'potential_outliers':
                        evidence = c['numeric'].get('outlier_samples', [])
                        if 'outlier_context' in c['numeric']:
                            evidence = [*evidence, c['numeric']['outlier_context']]
                    yield c['name'], category, c['numeric'][key], evidence, action
        if 'date' in c:
            for key, category in [('invalid_count', 'invalid_dates'), ('ambiguous_count', 'ambiguous_dates')]:
                if c['date'][key]:
                    yield c['name'], category, c['date'][key], c['sample_values'], 'review'
            if len(c['date']['formats']) > 1:
                yield c['name'], 'inconsistent_date_formats', c['date']['valid_count'], list(c['date']['formats']), 'review'


def detect(profile, run_id, file_id, enabled=None):
    issues = []
    for name, function in RULES.items():
        if enabled is not None and name not in enabled:
            continue
        for column, category, count, evidence, action in function(profile):
            issues.append(Issue(id=uuid5(NAMESPACE_URL, f'{file_id}:{column}:{category}').hex,
                                run_id=run_id, file_id=file_id, column=column, category=category,
                                description='Potential statistical extremes; review with domain context' if category == 'potential_outliers' else category.replace('_', ' ').capitalize(), affected_count=count,
                                affected_percentage=min(100, 100 * count / profile['row_count']) if profile['row_count'] else 0,
                                evidence=evidence[:10], recommended_action=action))
    return issues
