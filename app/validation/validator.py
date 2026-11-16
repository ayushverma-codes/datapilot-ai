from app.cleaning.executor import execute_step


def validate(before, after, original, cleaned, steps, logs, remaining):
    errors = []
    expected_loss = sum(log['input_rows'] - log['output_rows'] for log in logs if log['status'] == 'COMPLETED')
    if before['row_count'] - after['row_count'] != expected_loss:
        errors.append('UNEXPLAINED_ROW_CHANGE')
    if list(original.columns) != list(cleaned.columns):
        errors.append('UNEXPLAINED_SCHEMA_CHANGE')
    failures = [log['step_id'] for log in logs if log['status'] == 'FAILED']
    if failures:
        errors.append('EXECUTION_FAILURES')
    repeated = cleaned.copy()
    try:
        for step in steps:
            if step.status == 'COMPLETED':
                repeated, _ = execute_step(repeated, step)
        idempotent = repeated.equals(cleaned)
    except Exception:
        idempotent = False
    if not idempotent:
        errors.append('NON_IDEMPOTENT_PLAN')
    return {'passed': not errors, 'errors': errors, 'idempotent': idempotent,
            'row_count_before': before['row_count'], 'row_count_after': after['row_count'], 'explained_row_loss': expected_loss,
            'column_count_before': before['column_count'], 'column_count_after': after['column_count'],
            'data_types_before': before['data_types'], 'data_types_after': after['data_types'],
            'inferred_types_before': {c['name']: c['inference']['physical_type'] for c in before['columns']},
            'inferred_types_after': {c['name']: c['inference']['physical_type'] for c in after['columns']},
            'missing_cells_before': before['missing_cells'], 'missing_cells_after': after['missing_cells'],
            'duplicates_before': before['duplicate_rows'], 'duplicates_after': after['duplicate_rows'],
            'failed_steps': failures, 'unresolved_issues': [i.model_dump() for i in remaining]}
