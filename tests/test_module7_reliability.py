import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.evaluate import evaluate
from app.main import create_app


AUTH = {'Authorization': 'Bearer local-test-token'}
UPLOAD = '/api/v1/ingestion/upload'


def settings(path, **overrides):
    return Settings(storage_dir=path, api_tokens={'local-test-token': 'test-user'}, ai_enabled=False, **overrides)


def test_request_guard_rejects_before_read_and_times_out_on_slow_authenticated_body(tmp_path):
    app = create_app(settings(tmp_path, body_timeout=0.05))

    async def smoke():
        read = False

        async def slow():
            nonlocal read
            read = True
            yield b'{"question":'
            await asyncio.sleep(1)
            yield b'"sum amount"}'

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            denied = await client.post(UPLOAD, content=slow())
            assert denied.status_code == 401 and denied.headers['www-authenticate'] == 'Bearer'
            assert read is False
            unicode_denial = await client.post(UPLOAD, content=slow(), headers={'Authorization': b'Bearer \xff'})
            assert unicode_denial.status_code == 401 and read is False
            late = await client.post('/api/v1/assistant/query', content=slow(),
                                     headers={**AUTH, 'Content-Type': 'application/json'})
            assert read and late.status_code == 408
            assert late.json()['error']['code'] == 'REQUEST_TIMEOUT'
            assert (await client.get('/health')).json() == {'status': 'ok'}

    try:
        asyncio.run(smoke())
    finally:
        app.state.service.engine.dispose()


def test_multipart_resource_caps_and_structured_json_errors(tmp_path):
    with TestClient(create_app(settings(tmp_path, max_multipart_parts=2))) as client:
        too_many = client.post(UPLOAD, headers=AUTH, files=[('files', (f'{i}.csv', b'x\na\n')) for i in range(3)])
        assert too_many.status_code == 400 and 'error' in too_many.json()
        malformed = client.post('/api/v1/assistant/query', headers={**AUTH, 'Content-Type': 'application/json'},
                                content=b'{"question": "secret-canary",')
        assert malformed.status_code == 422 and malformed.json()['error']['code'] == 'INVALID_JSON'
        invalid = client.post('/api/v1/assistant/query', headers=AUTH, json={'secret-canary': 'secret-canary'})
        assert invalid.status_code == 422 and invalid.json()['error']['code'] == 'INVALID_REQUEST'
        assert all('secret-canary' not in response.text for response in (malformed, invalid))
        assert client.post(UPLOAD, headers=AUTH, files={'files': ('ok.csv', b'x\na\n')}).status_code == 201


def test_unexpected_api_failure_is_sanitized(tmp_path, monkeypatch):
    config = settings(tmp_path, ai_key='key-canary')
    assert 'key-canary' not in repr(config) and 'local-test-token' not in repr(config)
    app = create_app(config)

    def fail(*args):
        raise RuntimeError('private path and token canary')

    monkeypatch.setattr(app.state.service, 'get_run', fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/api/v1/ingestion/example', headers=AUTH)
        assert response.status_code == 500
        assert response.json()['error']['code'] == 'INTERNAL_ERROR'
        assert 'canary' not in response.text


def test_batch_evaluation_preserves_trace_evidence_and_continues(tmp_path):
    fixtures = tmp_path / 'fixtures'
    fixtures.mkdir()
    raw = b'region,amount\nWest,20\nNorth,7\n'
    (fixtures / 'sales.csv').write_bytes(raw)
    (fixtures / 'bad.csv').write_bytes(b'row,value\n2,3,4\n')
    (tmp_path / 'outside.csv').write_bytes(b'do not read\nsecret-canary\n')
    specification = {'cases': [
        {'id': 'outside', 'files': ['../outside.csv']},
        {'id': 'partial', 'files': ['bad.csv', 'sales.csv'], 'chat': [
            {'question': 'How many rows', 'file_index': 0, 'expected_status': 'FAILED'},
            {'question': 'Sum amount', 'file_index': 1, 'expected_value': '27'},
        ]},
        {'id': 'valid', 'files': ['sales.csv'], 'chat': [
            {'question': 'Sum amount by region', 'expected_value': '27'},
            {'question': 'Filter to West', 'expected_value': '20'},
            {'question': 'What about the other one?', 'expected_status': 'NEEDS_CLARIFICATION'},
            {'question': 'Repeat', 'expected_value': '20'},
        ]},
    ]}
    case_file = fixtures / 'cases.json'
    case_file.write_text(json.dumps(specification), encoding='utf-8')
    outcomes = []
    for index in (1, 2):
        destination = tmp_path / f'run{index}'
        output = destination / 'results.json'
        root = destination / 'artifacts'
        report = evaluate(case_file, output, root)
        assert report['case_count'] == 3 and report['passed'] == 1
        rejected, partial, valid = report['results']
        assert rejected['error']['code'] == 'INVALID_CASE_PATH'
        assert partial['validation'][0] is None and partial['validation'][1]['passed']
        assert all(turn['passed'] for turn in partial['chat_evaluation'])
        assert valid['dataset'][0]['filename'] == 'sales.csv'
        assert valid['profile_before'][0]['row_count'] == valid['profile_after'][0]['row_count'] == 2
        first, west, unclear, repeat = valid['chat_evaluation']
        assert west['trace']['context_resolution']['previous_trace_id'] == first['trace']['trace_id']
        assert unclear['status'] == 'NEEDS_CLARIFICATION'
        assert repeat['trace']['context_resolution']['previous_trace_id'] == west['trace']['trace_id']
        assert valid['cleaning_plan'][0]['steps'] and isinstance(valid['issues'][0], list)
        references = valid['artifacts']['files'][0]
        assert all(not Path(path).is_absolute() and (root / path).is_file() for path in references.values())
        assert (root / references['raw']).read_bytes() == raw
        assert 'secret-canary' not in output.read_text(encoding='utf-8')
        outcomes.append([(turn['status'], (turn['result']['result'] or {}).get('value'))
                         for turn in valid['chat_evaluation']])
    assert outcomes[0] == outcomes[1]
