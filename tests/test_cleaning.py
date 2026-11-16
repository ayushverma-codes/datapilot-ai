from dataclasses import replace
import pandas as pd
import pytest
from app.cleaning.transformation_registry import REGISTRY, validate_operation
from app.cleaning.executor import execute_step
from app.models.contracts import PipelineError, Step
from app.services.ingestion_service import IngestionService
from app.storage.database import UploadedFile


def test_pending_rejected_approved(service, ingest):
    file_id = ingest(b'label\nBlue\nblue\nGreen\n')
    plan = service.get_plan('alice', file_id)
    step = next(s for s in plan['steps'] if s['operation'] == 'category_normalization')
    assert step['status'] == 'AWAITING_APPROVAL'
    service.clean('alice', file_id)
    assert 'blue' in service.download('alice', file_id).read_text()
    service.decide('alice', [step['id']], 'REJECTED', 'distinct categories')
    service.clean('alice', file_id)
    assert 'blue' in service.download('alice', file_id).read_text()
    assert service.approval_history('alice', file_id)[0]['decision'] == 'REJECTED'
    assert next(s for s in service.get_plan('alice', file_id)['steps'] if s['operation'] == 'category_normalization')['id'] == step['id']


def test_approved_mapping_and_imputation(service, ingest):
    file_id = ingest(b'x,y\nBlue,a\nblue,\nGreen,b\n')
    proposed = service.propose('alice', file_id, 'fill_missing', ['y'], {'value': 'unknown'}, 'Explicit replacement')
    pending = [s['id'] for s in service.get_plan('alice', file_id)['steps'] if s['status'] == 'AWAITING_APPROVAL']
    service.decide('alice', pending, 'APPROVED')
    report = service.clean('alice', file_id)
    assert report['passed']
    output = service.download('alice', file_id).read_text()
    assert 'unknown' in output and 'blue' not in output
    assert len(service.approval_history('alice', file_id)) == len(pending)


@pytest.mark.parametrize('operation,columns,params', [
    ('exec', ['x'], {}), ('trim_whitespace', ['missing'], {}),
    ('trim_whitespace', ['x'], {'code': 'bad'}), ('remove_empty_rows', ['x'], {}),
    ('category_normalization', ['x'], {'mapping': {'a': 'b', 'b': 'c'}}),
    ('parse_dates', ['x'], {'format': 'execute'}),
])
def test_invalid_operations(operation, columns, params):
    with pytest.raises(PipelineError):
        validate_operation(operation, columns, params, ['x'])


def test_failure_isolated_and_no_partial_corruption(service, ingest):
    file_id = ingest(b'x,y\n1, a \noops,b\n')
    step = service.propose('alice', file_id, 'convert_numeric', ['x'], {}, 'Review')
    service.decide('alice', [step['id']], 'APPROVED')
    result = service.clean('alice', file_id)
    assert not result['passed']
    assert result['failed_steps'] == [step['id']]
    assert result['row_count_after'] == 2
    profile = service.get_profile('alice', file_id, 'cleaned')
    assert profile['columns'][1]['whitespace_count'] == 0
    assert profile['columns'][0]['sample_values'] == ['1', 'oops']
    with pytest.raises(PipelineError):
        service.download('alice', file_id)


def test_unexpected_row_loss_rejected(service, ingest, monkeypatch):
    file_id = ingest(b'x\n a \nb\n')
    monkeypatch.setitem(REGISTRY, 'trim_whitespace', replace(REGISTRY['trim_whitespace'], function=lambda f, c, p: f.iloc[:0]))
    result = service.clean('alice', file_id)
    assert not result['passed'] and result['row_count_after'] == 2


def test_persistence_and_recovery(service, ingest):
    file_id = ingest(b'x,y\n001, a \n001, a \n')
    ids = [s['id'] for s in service.get_plan('alice', file_id)['steps']]
    with service.sessions() as session:
        session.get(UploadedFile, file_id).status = 'CLEANING'
        session.commit()
    restarted = IngestionService(service.settings)
    try:
        assert [s['id'] for s in restarted.get_plan('alice', file_id)['steps']] == ids
        with pytest.raises(PipelineError):
            restarted.clean('alice', file_id)
        result = restarted.recover('alice', file_id)
        assert result['passed'] and result['row_count_after'] == 1
        assert restarted.get_validation('alice', file_id) == result
        assert '001,a' in restarted.download('alice', file_id).read_text()
    finally:
        restarted.engine.dispose()


def test_date_and_numeric_transforms(service, ingest):
    file_id = ingest(b'x,y\n1.00,2025/01/20\n1.50,2025/01/21\n1.50,2025/01/22\n')
    pending = [s['id'] for s in service.get_plan('alice', file_id)['steps'] if s['approval_required']]
    service.decide('alice', pending, 'APPROVED')
    result = service.clean('alice', file_id)
    assert result['passed'] and result['idempotent']
    output = service.download('alice', file_id).read_text()
    assert '1,2025-01-20T00:00:00' in output


def test_high_risk_blocked(service, ingest):
    service.settings.risk_overrides = {'trim_whitespace': 'high'}
    file_id = ingest(b'x\n a \n')
    step = service.get_plan('alice', file_id)['steps'][0]
    with pytest.raises(PipelineError) as exc:
        service.decide('alice', [step['id']], 'APPROVED')
    assert exc.value.code == 'HIGH_RISK_BLOCKED'


def test_interacting_steps_remove_new_duplicates(service, ingest):
    file_id = ingest(b'x\na\nb\n')
    step = service.propose('alice', file_id, 'category_normalization', ['x'], {'mapping': {'b': 'a'}}, 'Merge categories')
    service.decide('alice', [step['id']], 'APPROVED')
    report = service.clean('alice', file_id)
    assert report['passed'] and report['idempotent']
    assert report['row_count_after'] == 1
