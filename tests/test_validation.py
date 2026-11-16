from dataclasses import replace
from app.cleaning.transformation_registry import REGISTRY
from app.cleaning.plan_generator import generate_plan
from app.models.contracts import Step
from app.ingestion.csv_reader import read_csv
from app.profiling.profile_generator import generate_profile
from app.validation.validator import validate


def test_validator_detects_unexplained_loss(service):
    original, metadata = read_csv(b'x\na\nb\n', service.settings)
    cleaned = original.iloc[:1].copy()
    report = validate(generate_profile(original, metadata), generate_profile(cleaned, metadata), original, cleaned, [], [], [])
    assert not report['passed'] and 'UNEXPLAINED_ROW_CHANGE' in report['errors']


def test_validator_detects_non_idempotent_transform(service, ingest, monkeypatch):
    file_id = ingest(b'x\n a \nb\n')
    def append(frame, columns, params):
        frame[columns[0]] = frame[columns[0]] + '!'
        return frame
    monkeypatch.setitem(REGISTRY, 'trim_whitespace', replace(REGISTRY['trim_whitespace'], function=append))
    result = service.clean('alice', file_id)
    assert not result['passed'] and 'NON_IDEMPOTENT_PLAN' in result['errors']


def test_scope_changes_rejected(service, ingest, monkeypatch):
    file_id = ingest(b'x,y\n a ,keep\nb,also-keep\n')
    def wrong_column(frame, columns, params):
        frame['y'] = 'corrupted'
        return frame
    monkeypatch.setitem(REGISTRY, 'trim_whitespace', replace(REGISTRY['trim_whitespace'], function=wrong_column))
    report = service.clean('alice', file_id)
    assert not report['passed']
    assert service.get_profile('alice', file_id, 'cleaned')['columns'][1]['sample_values'] == ['keep', 'also-keep']


def test_arbitrary_bracket_headers_and_extreme_numbers(service, ingest):
    file_id = ingest(b'[any],{other}\n1e308,a\n-1e308,b\n1e308,c\n')
    profile = service.get_profile('alice', file_id)
    assert profile['columns'][0]['numeric']['min'] == -1e308
    assert profile['columns'][0]['numeric']['max'] == 1e308
    assert service.clean('alice', file_id)['passed']


def test_raw_tampering_prevents_cleaning(service, ingest):
    import pytest
    from app.models.contracts import PipelineError
    file_id = ingest(b'x\n a \n')
    raw = next(service.settings.storage_dir.rglob(f'raw/{file_id}.csv'))
    raw.write_bytes(b'x\ntampered\n')
    with pytest.raises(PipelineError):
        service.clean('alice', file_id)


def test_approval_after_safe_revision_is_reproducible(service, ingest):
    file_id = ingest(b'x,y\nBlue, a \nblue, a \nGreen,b\n')
    first = service.clean('alice', file_id)
    assert first['row_count_after'] == 3
    step = next(s for s in service.get_plan('alice', file_id)['steps'] if s['operation'] == 'category_normalization')
    service.decide('alice', [step['id']], 'APPROVED')
    second = service.clean('alice', file_id)
    assert second['passed'] and second['row_count_after'] == 2
    assert second == service.clean('alice', file_id)
