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
    '{}',
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


def test_groq_budget_and_rate_limit(service, ingest):
    file_id = ingest((','.join('測試' * 40 + str(i) for i in range(25)) + '\n' + ','.join(['x'] * 25) + '\n').encode())
    service.settings.ai_enabled = True
    service.settings.ai_key = 'test-key'
    service.settings.ai_model = 'openai/gpt-oss-120b'
    service.settings.ai_url = 'https://api.groq.com/openai/v1'
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body['max_completion_tokens'] == 1200 and body['reasoning_effort'] == 'low'
        message = body['messages'][1]['content']
        assert len(message.encode()) <= 3200
        assert json.loads(message)['columns']
        return httpx.Response(429)

    suggestions, status = analyze(service.get_profile('alice', file_id), [], service.settings, httpx.MockTransport(handler))
    assert suggestions == [] and status == {'status': 'fallback', 'error': 'AI_RATE_LIMITED'}
    assert len(requests) == 1


@pytest.mark.parametrize('samples,finish,expected', [(True, 'stop', 'completed'), (False, 'stop', 'fallback'),
                                                     (True, 'length', 'fallback')])
def test_groq_semantic_and_category_response(service, ingest, samples, finish, expected):
    file_id = ingest(b'color\nBlue\nblue\nGreen\n')
    service.settings.ai_enabled = True
    service.settings.ai_include_samples = samples
    service.settings.ai_key = 'test-key'
    service.settings.ai_model = 'qwen/qwen3.8-27b'
    service.settings.ai_url = 'https://api.groq.com/openai/v1'
    body = {'interpretations': [{'column': 'color', 'interpretation': 'Likely a product color'}],
            'suggestions': [{'column': 'color', 'operation': 'category_normalization',
                             'parameters': {'mapping': {'blue': 'Blue'}}, 'reason': 'Capitalization variant',
                             'confidence': .9, 'risk': 'low'}]}

    def handler(request):
        assert json.loads(request.content)['reasoning_effort'] == 'none'
        return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(body)}}]})

    suggestions, status = analyze(service.get_profile('alice', file_id), [], service.settings, httpx.MockTransport(handler))
    assert status['status'] == expected
    if expected == 'completed':
        assert status['interpretations'] == body['interpretations']
        assert suggestions[0].parameters == {'mapping': {'blue': 'Blue'}}
    else:
        assert suggestions == [] and status['error'] == 'AI_INVALID_RESPONSE'
