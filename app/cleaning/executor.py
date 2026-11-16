import pandas as pd
from app.cleaning.transformation_registry import validate_operation


def execute_step(frame, step):
    spec, params = validate_operation(step.operation, step.columns, step.parameters, frame.columns)
    candidate = spec.function(frame.copy(deep=True), step.columns, params)
    if list(candidate.columns) != list(frame.columns) or not candidate.index.is_unique:
        raise ValueError('Transformation changed schema or duplicated source indexes')
    if not candidate.index.isin(frame.index).all() or len(candidate) > len(frame):
        raise ValueError('Transformation introduced rows')
    if not spec.removes_rows and not candidate.index.equals(frame.index):
        raise ValueError('Unexpected row removal or reordering')
    removed = frame.index.difference(candidate.index)
    if spec.removes_rows:
        expected = frame.dropna(how='all') if step.operation == 'remove_empty_rows' else frame.drop_duplicates()
        if not candidate.equals(expected):
            raise ValueError('Removal differs from the approved exact predicate')
    scope = step.columns or list(frame.columns)
    outside = [c for c in frame.columns if c not in scope]
    if outside and not candidate[outside].equals(frame.loc[candidate.index, outside]):
        raise ValueError('Transformation modified columns outside approved scope')
    before = frame.loc[candidate.index]
    equal = before.eq(candidate).fillna(False) | (before.isna() & candidate.isna())
    changed = (~equal).any(axis=1)
    affected = list(removed) + list(changed[changed].index)
    audit = {"step_id": step.id, "operation": step.operation, "status": 'COMPLETED',
             "input_rows": len(frame), "output_rows": len(candidate), "rows_affected": len(affected),
             "columns": scope, "removed_source_rows": [int(i) + 2 for i in removed],
             "changed_source_rows": [int(i) + 2 for i in changed[changed].index]}
    if step.operation == 'flag_outliers':
        from app.profiling.profile_generator import profile_column
        audit['flags'] = {c: profile_column(frame[c]).get('numeric', {}) for c in step.columns}
    return candidate, audit
