import json
import httpx
import pytest
from app.ai.analyzer import analyze
from app.config import Settings


def test_disabled(service, ingest):
    file_id = ingest(b'x\na\n')
    assert analyze(service.get_profile('alice', file_id), [], service.settings)[1]['status'] == 'disabled'


@pytest.mark.parametrize('body', [
    'not json',
    '{"suggestions":[{"column":"x","operation":"exec","reason":"x","confidence":1}]}',
    '{"suggestions":[{"column":"not-here","operation":"convert_numeric","reason":"x","confidence":1}]}',
    '{"suggestions":[{"column":"x","operation":"normalize_strings","parameters":{"code":"x"},"reason":"x","confidence":1}]}',
    '{"suggestions":[{"column":"x","operation":"fill_missing","parameters":{"value":"invented"},"reason":"x","confidence":1}]}',
])
def test_bad_ai_falls_back(service, ingest, body):
    file_id = ingest(b'x\nsecret-value\n')
    service.settings.ai_enabled = True
    service.settings.ai_key = 'test-key'
    service.settings.ai_model = 'test-model'
    requests = []
    def handler(request):
        requests.append(request)
        assert b'secret-value' not in request.content
        return httpx.Response(200, json={'choices': [{'message': {'content': body}}]})
    suggestions, status = analyze(service.get_profile('alice', file_id), [], service.settings, httpx.MockTransport(handler))
    assert suggestions == [] and status['status'] == 'fallback'
    assert len(requests) == 2


def test_ai_timeout_and_valid_response(service, ingest):
    file_id = ingest(b'x\nABC\n')
    service.settings.ai_enabled = True
    service.settings.ai_key = 'test-key'
    service.settings.ai_model = 'test-model'
    def timeout(request):
        raise httpx.ReadTimeout('test timeout')
    assert analyze(service.get_profile('alice', file_id), [], service.settings, httpx.MockTransport(timeout))[1]['status'] == 'fallback'
    body = {'suggestions': [{'column': 'x', 'operation': 'normalize_strings', 'parameters': {'mode': 'lower'}, 'reason': 'Possible formatting', 'confidence': .99, 'risk': 'low'}]}
    suggestions, status = analyze(service.get_profile('alice', file_id), [], service.settings,
                                  httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(body)}}]})))
    assert status['status'] == 'completed'
    from app.cleaning.plan_generator import generate_plan
    plan = generate_plan(file_id, service.get_profile('alice', file_id), [], suggestions)
    assert next(s for s in plan if s.source == 'AI').approval_required


def test_unavailable_ai_pipeline(service, ingest):
    service.settings.ai_enabled = True
    service.settings.ai_key = ''
    file_id = ingest(b'x\n a \n')
    assert service.clean('alice', file_id)['passed']
