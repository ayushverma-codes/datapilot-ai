import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from app.evaluate import evaluate
from app.models.contracts import PipelineError


def test_full_http_workflow_and_isolation(tmp_path):
    settings = Settings(storage_dir=tmp_path, api_tokens={'alice-token': 'alice', 'bob-token': 'bob'}, ai_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        auth = {'Authorization': 'Bearer alice-token', 'Idempotency-Key': 'one'}
        bob = {'Authorization': 'Bearer bob-token'}
        assert client.get('/health').status_code == 200
        response = client.post('/api/v1/ingestion/upload', headers=auth, files=[('files', ('one.csv', b'x,y\nBlue, a \nblue,b\n')), ('files', ('two.csv', b'q\nvalue\n'))])
        assert response.status_code == 201, response.text
        run = response.json()
        file_id = run['files'][0]['file_id']
        for path in [f'/api/v1/ingestion/{run["run_id"]}', f'/api/v1/ingestion/{run["run_id"]}/files', f'/api/v1/ingestion/{run["run_id"]}/report',
                     *[f'/api/v1/files/{file_id}/{suffix}' for suffix in ['profile', 'issues', 'cleaning-plan', 'approvals', 'approvals/history']]]:
            assert client.get(path, headers=auth).status_code == 200
            assert client.get(path, headers=bob).status_code == 404
            assert client.get(path, headers={'X-User-ID': 'alice'}).status_code == 401
        step = client.get(f'/api/v1/files/{file_id}/approvals', headers=auth).json()[0]
        assert client.post(f'/api/v1/cleaning-steps/{step["id"]}/approve', headers=bob, json={}).status_code == 404
        assert client.post('/api/v1/cleaning-steps/approve', headers=auth, json={'step_ids': [step['id']], 'reason': 'Reviewed'}).status_code == 200
        result = client.post(f'/api/v1/files/{file_id}/clean', headers=auth)
        assert result.status_code == 200 and result.json()['passed']
        assert client.get(f'/api/v1/files/{file_id}/validation', headers=auth).json()['passed']
        output = client.get(f'/api/v1/files/{file_id}/download', headers=auth)
        assert output.status_code == 200 and b'Blue,a' in output.content
        assert client.get(f'/api/v1/files/{file_id}/download', headers=bob).status_code == 404
        assert client.post(f'/api/v1/files/{file_id}/clean', headers=bob).status_code == 404
    # A new app instance opens the same DB and artifacts.
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/api/v1/files/{file_id}/download', headers=auth).content == output.content
        assert client.get(f'/api/v1/files/{file_id}/approvals/history', headers=auth).json()[0]['decision'] == 'APPROVED'


def test_batch_approval_atomicity(service, ingest):
    file_id = ingest(b'x\nBlue\nblue\n')
    step = next(s for s in service.get_plan('alice', file_id)['steps'] if s['approval_required'])
    with pytest.raises(PipelineError):
        service.decide('alice', [step['id'], 'missing'], 'APPROVED')
    assert next(s for s in service.get_plan('alice', file_id)['steps'] if s['id'] == step['id'])['status'] == 'AWAITING_APPROVAL'
    assert service.approval_history('alice', file_id) == []


def test_evaluation_continues_and_persists(tmp_path):
    (tmp_path / 'odd.csv').write_text('alpha,beta\n x ,1\n x ,1\n', encoding='utf-8')
    (tmp_path / 'other.csv').write_text('nothing\nanything\n', encoding='utf-8')
    cases = {'cases': [{'id': 'missing', 'files': ['missing.csv']}, {'id': 'works', 'files': ['odd.csv', 'other.csv']}, {'id': 'invalid'}]}
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(cases))
    output = tmp_path / 'results.json'
    result = evaluate(path, output, tmp_path / 'artifacts')
    assert result['case_count'] == 3 and result['passed'] == 1
    assert result['results'][0]['error']['code'] == 'FileNotFoundError'
    assert result['results'][1]['report']['files'][0]['validation']['row_count_after'] == 1
    assert json.loads(output.read_text()) == result
    assert (tmp_path / 'artifacts' / 'metadata.db').exists()


def test_auth_default_and_dev_identity(tmp_path):
    with TestClient(create_app(Settings(storage_dir=tmp_path / 'secure'))) as client:
        assert client.get('/api/v1/ingestion/anything').status_code == 401
    with TestClient(create_app(Settings(storage_dir=tmp_path / 'dev', dev_mode=True))) as client:
        result = client.post('/api/v1/ingestion/upload', headers={'X-User-ID': 'admin'}, files={'files': ('x.csv', b'x\na\n')})
        assert result.status_code == 201
        assert client.app.state.service.get_run('local-development-user', result.json()['run_id'])


def test_api_request_limit(tmp_path):
    with TestClient(create_app(Settings(storage_dir=tmp_path, dev_mode=True, max_request_bytes=20))) as client:
        result = client.post('/api/v1/ingestion/upload', files={'files': ('a.csv', b'x\na\n')})
        assert result.status_code == 413
        assert result.json()['error']['code'] == 'REQUEST_TOO_LARGE'
