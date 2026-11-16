from hashlib import sha256

import pandas as pd
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.cleaning.plan_generator import generate_plan
from app.models.contracts import Suggestion
from app.profiling.type_inference import infer


def test_generic_identifier_patterns_and_measurements():
    samples = [
        pd.Series(['12341', '12342', '12341'], name='MemberID'),
        pd.Series(['1234', '1235', 'C1236', '1234'], name='arbitrary'),
        pd.Series(['10001A', '10002', 'POST', '10001A'], name='CatalogCode'),
        pd.Series([str(12600 + i) for i in range(50)] * 3, name='arbitrary'),
    ]
    for sample in samples:
        result = infer(sample)
        assert result['potential_identifier']
        assert result['proposed_target_type'] == 'string'
        assert not result['potential_numeric']
    for values in ([1, 2, 1, 3, -1], ['1e3', '2.4', '1e3', '0.25']):
        assert infer(pd.Series(values, name='measurement'))['semantic_type'] == 'potential_measurement'


def test_leading_zero_and_mixed_identifiers_are_preserved(service, ingest):
    file_id = ingest(b'arbitrary,other,opaque\n001,1001,11223\n002,1002,11224\n001,C1003,11223\n003,1001,11225\n')
    profile = service.get_profile('alice', file_id)
    assert all(c['inference']['potential_identifier'] for c in profile['columns'][:2])
    suggestion = Suggestion(column='arbitrary', operation='convert_numeric', reason='test', confidence=1)
    assert not any(s.source == 'AI' for s in generate_plan(file_id, profile, [], [suggestion]))
    assert not any(s['operation'] == 'convert_numeric' and s['columns'][0] in {'arbitrary', 'other'}
                   for s in service.get_plan('alice', file_id)['steps'])
    assert service.clean('alice', file_id)['passed']
    output = service.download('alice', file_id).read_bytes()
    assert b'001,C1003,11223' in output and b'002,1002,11224' in output


def test_outlier_context_and_warning_preserve_sentinel_and_tail(service, ingest):
    rows = [-1] * 80 + list(range(20)) + [10000]
    file_id = ingest(('arbitrary\n' + '\n'.join(map(str, rows)) + '\n').encode())
    profile = service.get_profile('alice', file_id)
    issue = next(i for i in service.get_issues('alice', file_id) if i['category'] == 'potential_outliers')
    assert issue['severity'] == 'warning'
    context = profile['columns'][0]['numeric']['outlier_context']
    assert context == issue['evidence'][-1] and context['review_only']
    assert context['frequent_boundaries'][0]['value'] == -1
    assert any('sentinel' in message for message in context['explanations'])
    assert any('longer' in message for message in context['explanations'])
    assert service.clean('alice', file_id)['passed']
    assert '-1\n' in service.download('alice', file_id).read_text()
    assert '10000\n' in service.download('alice', file_id).read_text()


def test_run_approval_transitions_and_reopen(service, ingest):
    file_id = ingest(b'value\n1.20\n2.30\n')
    file = service.get_plan('alice', file_id)
    step = next(s for s in file['steps'] if s['approval_required'])
    with service.sessions() as session:
        from app.storage.database import UploadedFile
        run_id = session.get(UploadedFile, file_id).run_id
    assert service.get_run('alice', run_id)['status'] == 'AWAITING_APPROVAL'
    service.clean('alice', file_id)
    assert service.get_run('alice', run_id)['status'] == 'AWAITING_APPROVAL'
    service.decide('alice', [step['id']], 'APPROVED')
    assert service.get_run('alice', run_id)['status'] == 'PROCESSING'
    assert service.clean('alice', file_id)['passed']
    assert service.get_run('alice', run_id)['status'] == 'COMPLETED'
    proposal = service.propose('alice', file_id, 'normalize_strings', ['value'], {'mode': 'lower'}, 'review')
    assert service.get_run('alice', run_id)['status'] == 'AWAITING_APPROVAL'
    service.decide('alice', [proposal['id']], 'REJECTED')
    assert service.get_run('alice', run_id)['status'] == 'COMPLETED'


def test_single_http_upload_isolates_unrelated_files_and_failure(tmp_path):
    with TestClient(create_app(Settings(storage_dir=tmp_path, dev_mode=True))) as client:
        response = client.post('/api/v1/ingestion/upload', files=[
            ('files', ('labels.csv', b'label\nword\nother\n')),
            ('files', ('prices.csv', b'amount,kind\n1.25,each\n2.50,box\n')),
            ('files', ('broken.csv', b'a,b\n1,2,3\n')),
        ])
        assert response.status_code == 201
        run = response.json()
        assert run['status'] == 'AWAITING_APPROVAL'
        files = run['files']
        assert {f['run_id'] for f in files} == {run['run_id']}
        assert len({f['file_id'] for f in files}) == len(files) == 3
        first, second, failed = [f'/api/v1/files/{f["file_id"]}' for f in files]
        assert files[2]['status'] == 'FAILED' and files[2]['error']
        assert client.get(f'{failed}/profile').status_code == 409
        assert client.get(f'{failed}/cleaning-plan').status_code == 409
        first_profile = client.get(f'{first}/profile').json()
        second_profile = client.get(f'{second}/profile').json()
        assert [c['name'] for c in first_profile['columns']] == ['label']
        assert [c['name'] for c in second_profile['columns']] == ['amount', 'kind']
        first_plan = client.get(f'{first}/cleaning-plan').json()
        second_plan = client.get(f'{second}/cleaning-plan').json()
        assert first_plan['plan_id'] != second_plan['plan_id']
        assert all(not s['approval_required'] for s in first_plan['steps'])
        step = next(s for s in second_plan['steps'] if s['approval_required'])
        assert step['columns'] == ['amount']
        assert client.post(f'{first}/clean').json()['passed']
        assert client.post(f'/api/v1/cleaning-steps/{step["id"]}/reject', json={}).status_code == 200
        assert client.post(f'{second}/clean').json()['passed']
        final = client.get(f"/api/v1/ingestion/{run['run_id']}").json()
        assert final['status'] == 'PARTIALLY_COMPLETED'
        assert [f['status'] for f in final['files']] == ['COMPLETED', 'COMPLETED', 'FAILED']


def test_http_multipart_has_no_fixed_file_count(tmp_path):
    settings = Settings(storage_dir=tmp_path, dev_mode=True, ai_enabled=False)
    parts = [('files', ('invalid.txt', b'x'))] * 1000
    parts.append(('files', ('last.csv', b'arbitrary\n')))
    parts += [('descriptions', (None, ''))] * 1000
    parts.append(('descriptions', (None, 'The last file')))
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/v1/ingestion/upload', files=parts)
        assert response.status_code == 201, response.text
        files = response.json()['files']
    assert len({file['file_id'] for file in files}) == 1001
    assert files[-1]['status'] == 'CLEANING_PLANNED'
    assert files[-1]['description'] == 'The last file'
    assert all(file['error']['code'] == 'INVALID_EXTENSION' for file in files[:-1])


def test_http_upload_descriptions_oversize_and_raw_integrity(tmp_path):
    settings = Settings(storage_dir=tmp_path, api_tokens={'token': 'alice'},
                        max_file_bytes=64, ai_enabled=False)
    utf16 = 'some label\tother\nα\tz\n'.encode('utf-16')
    cp1252 = 'ville;note\nParis;café\n'.encode('cp1252')
    oversize = b'x\n' + b'a' * 68 + b'A\n'
    url = '/api/v1/ingestion/upload'
    headers = {'Authorization': 'Bearer token', 'Idempotency-Key': 'upload-1'}
    parts = [
        ('files', ('same.csv', utf16)),
        ('files', ('same.csv', cp1252)),
        ('files', ('large.csv', oversize)),
        ('descriptions', (None, 'First dataset')),
        ('descriptions', (None, '')),
        ('descriptions', (None, 'Oversized dataset')),
    ]
    with TestClient(create_app(settings)) as client:
        response = client.post(url, files=parts, headers=headers)
        assert response.status_code == 201, response.text
        run = response.json()
        first, second, failed = run['files']
        assert run['status'] == 'PARTIALLY_COMPLETED'
        assert first['filename'] == second['filename'] == 'same.csv'
        assert failed['status'] == 'FAILED'
        assert failed['error']['code'] == 'FILE_TOO_LARGE'
        assert failed['size'] == len(oversize)
        assert failed['checksum'] == sha256(oversize).hexdigest()
        assert failed['description'] == 'Oversized dataset'
        assert client.post(url, files=parts, headers=headers).json() == run
        changed = parts.copy()
        changed[2] = ('files', ('large.csv', oversize[:-2] + b'B\n'))
        conflict = client.post(url, files=changed, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
        changed = parts.copy()
        changed[3] = ('descriptions', (None, 'Changed description'))
        assert client.post(url, files=changed, headers=headers).status_code == 409
        invalid = client.post(url, files=parts[:-1], headers=headers)
        assert invalid.status_code == 422
        assert invalid.json()['error']['code'] == 'INVALID_DESCRIPTIONS'
        for file, data, encoding, delimiter in ((first, utf16, 'utf-16', '\t'),
                                                 (second, cp1252, 'cp1252', ';')):
            file_id = file['file_id']
            raw = next(tmp_path.rglob(f'raw/{file_id}.csv'))
            assert raw.read_bytes() == data
            assert file['size'] == len(data)
            assert file['checksum'] == sha256(data).hexdigest()
            route = f'/api/v1/files/{file_id}'
            profile = client.get(route + '/profile', headers=headers).json()
            assert profile['schema']['encoding'] == encoding
            assert profile['schema']['delimiter'] == delimiter
            assert 'description' not in profile['schema']
            assert client.post(route + '/clean', headers=headers).json()['passed']
            assert raw.read_bytes() == data
        assert not list(tmp_path.rglob(f"raw/{failed['file_id']}.csv"))
    with TestClient(create_app(settings)) as client:
        files = client.get(f"/api/v1/ingestion/{run['run_id']}/files", headers=headers).json()
        assert files[0]['description'] == 'First dataset'
        assert 'description' not in files[1]
        assert files[2]['description'] == 'Oversized dataset'
