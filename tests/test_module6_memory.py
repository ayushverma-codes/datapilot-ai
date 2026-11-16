import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.conversation.analysis_contracts import AnalyticalQuestion
from app.conversation.memory import ConversationService
from app.main import create_app
from app.models.contracts import PipelineError
from app.retail_contracts import RelationshipRequest
from app.services.assistant_service import AssistantService
from app.services.retail_service import RetailService


AUTH = {'Authorization': 'Bearer alice-token'}
BOB = {'Authorization': 'Bearer bob-token'}
SALES = b'region,category,revenue,units\nWest,A,20,4\nWest,B,10,1\nNorth,A,7,2\nSouth,B,3,1\n'
QUERY = '/api/v1/assistant/query'


def dataset(client, name='sales.csv', content=SALES):
    upload = client.post('/api/v1/ingestion/upload', headers=AUTH, files={'files': (name, content)})
    assert upload.status_code == 201, upload.text
    file = upload.json()['files'][0]
    assert client.post(f'/api/v1/files/{file["file_id"]}/clean', headers=AUTH).json()['passed']
    return file


def conversation(client):
    return client.post('/api/v1/conversations', headers=AUTH, json={}).json()['conversation_id']


def ask(client, cid, text, *files):
    response = client.post(QUERY, headers=AUTH,
                           json={'question': text, 'conversation_id': cid, 'datasets': [{'file_id': f} for f in files]})
    assert response.status_code == 200, response.text
    return response.json()


def settings(tmp_path):
    return Settings(storage_dir=tmp_path, ai_enabled=False, api_tokens={'alice-token': 'alice', 'bob-token': 'bob'})


def test_followup_edits_persist_with_trace_and_dataset_revision(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        file = dataset(client)
        cid = conversation(client)
        first = ask(client, cid, 'Show revenue by region', file['file_id'])
        assert first['result']['value'] == '40'
        filtered = ask(client, cid, 'Filter to West')
        assert filtered['result']['value'] == '30'
        top = ask(client, cid, 'Now show the top five categories')
        assert top['plan']['group_by']['column'] == 'category'
        assert top['plan']['limit'] == 5 and top['plan']['sort']['direction'] == 'desc'
        assert top['plan']['filters'] == filtered['plan']['filters']
        assert top['result']['rows_matched'] == 2
        trace = client.get(f'/api/v1/assistant/traces/{top["trace_id"]}', headers=AUTH).json()
        assert trace['context_resolution']['previous_trace_id'] == filtered['trace_id']
        assert trace['context_resolution']['operation'] == 'change_ranking'
        assert trace['conversation']['context_version_after'] == 3

    with TestClient(create_app(config)) as client:
        path = f'/api/v1/conversations/{cid}'
        history = client.get(path, headers=AUTH).json()
        state = history['context']['analysis']
        assert state['source']['run_id'] == file['run_id'] and state['source']['revision'] == 1
        assert state['plan'] == top['plan'] and state['result'] == top['result']
        assert [message['role'] for message in history['messages']] == ['user', 'assistant'] * 3
        assert history['messages'][-1]['query_result_reference']['trace_id'] == top['trace_id']
        pronoun = ask(client, cid, 'Keep that region')
        assert pronoun['plan']['filters'] == top['plan']['filters']
        added = ask(client, cid, 'Also where category = A')
        assert added['result']['value'] == '20' and len(added['plan']['filters']) == 2
        replaced = ask(client, cid, 'Only where region = North')
        assert replaced['result']['value'] == '7' and len(replaced['plan']['filters']) == 2
        metric = ask(client, cid, 'Now show average units')
        assert metric['result']['value'] == '2' and metric['plan']['column']['column'] == 'units'
        assert ask(client, cid, 'Remove the region filter')['result']['rows_matched'] == 2
        assert ask(client, cid, 'Clear all filters')['result']['rows_matched'] == 4
        assert ask(client, cid, 'Repeat the previous analysis')['result']['rows_matched'] == 4
        assert client.get(path, headers=BOB).status_code == 404
        assert client.get(f'/api/v1/assistant/traces/{metric["trace_id"]}', headers=BOB).status_code == 404
        assert client.post(QUERY, headers=BOB, json={'question': 'Repeat', 'conversation_id': cid}).status_code == 404
        other = conversation(client)
        assert ask(client, other, 'Repeat')['status'] == 'NEEDS_CLARIFICATION'
        assert client.get(f'/api/v1/conversations/{other}', headers=AUTH).json()['context_version'] == 0


def test_comparison_replaces_conflicting_filter_and_ambiguous_reference_preserves_state(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        fid = dataset(client)['file_id']
        cid = conversation(client)
        ask(client, cid, 'Sum revenue where region = West', fid)
        compare = ask(client, cid, 'Actually, compare North and South')
        assert compare['plan']['filters'] == []
        assert compare['result']['cohort_a']['value'] == '7'
        assert compare['result']['cohort_b']['value'] == '3'
        assert ask(client, cid, 'Only that region')['status'] == 'NEEDS_CLARIFICATION'
        history = client.get(f'/api/v1/conversations/{cid}', headers=AUTH).json()
        assert history['context_version'] == 2
        assert history['context']['analysis']['trace_id'] == compare['trace_id']
        assert history['messages'][-1]['message_type'] == 'CLARIFICATION'


def test_dataset_switch_and_stale_revision(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        fid = dataset(client)['file_id']
        other = dataset(client, 'returns.csv', b'region,revenue\nWest,100\nEast,40\n')['file_id']
        cid = conversation(client)
        ask(client, cid, 'Show revenue by region where region = West', fid)
        plan = client.get(f'/api/v1/files/{fid}/cleaning-plan', headers=AUTH).json()
        step = next(step for step in plan['steps'] if step['operation'] == 'convert_numeric')
        assert client.post(f'/api/v1/cleaning-steps/{step["id"]}/approve', headers=AUTH, json={}).status_code == 200
        assert client.post(f'/api/v1/files/{fid}/clean', headers=AUTH).json()['passed']
        stale = client.post(QUERY, headers=AUTH, json={'conversation_id': cid, 'question': 'Continue'})
        assert stale.status_code == 409
        assert stale.json()['error']['code'] == 'DATASET_VERSION_CONFLICT'
        switched = ask(client, cid, 'Show revenue by region from returns.csv', fid, other)
        assert switched['result']['value'] == '140' and switched['plan']['filters'] == []
        assert ask(client, cid, 'Repeat')['evidence']['source_datasets']['left']['file_id'] == other


def test_concurrent_state_change_does_not_overwrite_new_turn(service, ingest, monkeypatch):
    fid = ingest(SALES)
    assert service.clean('alice', fid)['passed']
    conversations = ConversationService(service, service.settings)
    assistant = AssistantService(service, RetailService(service), conversations)
    cid = conversations.create('alice')['conversation_id']
    execute = assistant._execute
    entered = False

    def interleave(user, request, trace, transport, context):
        nonlocal entered
        if not entered:
            entered = True
            assistant.analyze('alice', AnalyticalQuestion(question='How many rows', conversation_id=cid,
                                                           datasets=[{'file_id': fid}]))
        execute(user, request, trace, transport, context)

    monkeypatch.setattr(assistant, '_execute', interleave)
    with pytest.raises(PipelineError, match='conversation changed') as conflict:
        assistant.analyze('alice', AnalyticalQuestion(question='Show revenue by region', conversation_id=cid,
                                                       datasets=[{'file_id': fid}]))
    assert conflict.value.code == 'CONVERSATION_CONFLICT'
    assert assistant.trace('alice', conflict.value.details['trace_id'])['status'] == 'FAILED'
    history = conversations.get('alice', cid)
    assert len(history['messages']) == 2
    assert history['context']['analysis']['plan']['operation'] == 'row_count'


def test_ai_plan_and_validated_join_are_available_on_followup(service, ingest):
    left = ingest(b'key,amount\nx,10\ny,20\n', 'sales.csv')
    right = ingest(b'ref,department\nx,Clothing\ny,Tools\n', 'departments.csv')
    for fid in (left, right):
        assert service.clean('alice', fid)['passed']
    retail = RetailService(service)
    registered = retail.register('alice', RelationshipRequest(
        left_file_id=left, right_file_id=right, left_keys=['key'], right_keys=['ref'], cardinality='many_to_one'))
    service.settings.ai_enabled = True
    service.settings.ai_model = service.settings.ai_key = 'mock'
    conversations = ConversationService(service, service.settings)
    assistant = AssistantService(service, retail, conversations)
    cid = conversations.create('alice')['conversation_id']
    plan = {'intent': 'aggregate', 'dataset': 'd1', 'revision': 1, 'relationship': 'j1',
            'operation': 'sum', 'column': {'column': 'amount'}, 'group_by': {'side': 'right', 'column': 'department'}}
    provider = httpx.MockTransport(lambda request: httpx.Response(
        200, json={'choices': [{'message': {'content': json.dumps(plan)}}]}))
    first = assistant.analyze('alice', AnalyticalQuestion(
        question='Sum amount by department', conversation_id=cid, datasets=[{'file_id': left}],
        relationship_ids=[registered['relationship_id']]), provider)
    assert first['result']['value'] == '30'
    followup = assistant.analyze('alice', AnalyticalQuestion(question='Show top one', conversation_id=cid))
    assert followup['result']['groups'] == [{'group': 'Tools', 'value': '20', 'records': 1}]
    assert followup['evidence']['join']['relationship_id'] == registered['relationship_id']
    trace = assistant.trace('alice', followup['trace_id'])
    assert trace['context_resolution']['previous_trace_id'] == first['trace_id']
    assert trace['plan_source'] == 'CONVERSATION_RULES'


def test_demo_openapi_and_cleaning_evidence(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        schema = client.get('/openapi.json').json()
        upload = schema['paths']['/api/v1/ingestion/upload']['post']
        body = upload['requestBody']['content']['multipart/form-data']['schema']
        model = schema['components']['schemas'][body['$ref'].split('/')[-1]]
        assert model['properties']['files']['items'] == {'type': 'string', 'format': 'binary'}
        assert schema['components']['securitySchemes']['HTTPBearer']['scheme'] == 'bearer'
        assert schema['paths']['/api/v1/files/{file_id}/cleaning-plan/steps']['post']['security'] == [{'HTTPBearer': []}]
        assert client.get('/docs').status_code == 200

        raw = 'title,amount,review_date\n" Good ",1.20,2025/02/01\ngood,2.30,2025/02/02\nÃ³timo,3.40,2025/02/03\nÃ³TIMO,4.50,2025/02/04\n'.encode()
        file = dataset(client, 'reviews.csv', raw)
        fid = file['file_id']
        assert file['checksum'] == hashlib.sha256(raw).hexdigest()
        plan = client.get(f'/api/v1/files/{fid}/cleaning-plan', headers=AUTH).json()
        mapping = next(step for step in plan['steps'] if step['operation'] == 'category_normalization')
        assert mapping['status'] == 'AWAITING_APPROVAL'
        assert mapping['parameters']['mapping'] == {'good': 'Good'}
        step_ids = [step['id'] for step in plan['steps'] if step['operation'] in ('convert_numeric', 'parse_dates')]
        assert len(step_ids) == 2
        assert client.post('/api/v1/cleaning-steps/approve', headers=AUTH, json={'step_ids': step_ids}).status_code == 200
        report = client.post(f'/api/v1/files/{fid}/clean', headers=AUTH).json()
        assert report['passed']
        changed = {step['operation']: step['rows_affected'] for step in report['executed_steps']}
        assert changed['convert_numeric'] == changed['parse_dates'] == 4
        assert 'category_normalization' not in changed
        cleaned = client.get(f'/api/v1/files/{fid}/download', headers=AUTH).content.decode()
        assert 'Good,1.2,2025-02-01' in cleaned and 'Ã³timo,3.4,2025-02-03' in cleaned
