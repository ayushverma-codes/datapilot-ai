import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.config import Settings
from app.main import create_app
from app.storage.database import (Base, Conversation, ConversationMessage, SchemaMigration,
                                  User, database)


def _headers(token='alice-token'):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def memory_client(tmp_path):
    settings = Settings(storage_dir=tmp_path, api_tokens={
        'alice-token': 'alice', 'bob-token': 'bob'}, ai_enabled=False)
    with TestClient(create_app(settings)) as client:
        yield client, settings


def _conversation(client, headers=None):
    response = client.post('/api/v1/conversations', json={}, headers=headers or _headers())
    assert response.status_code == 201, response.text
    return response.json()['conversation_id']


def _clean_file(client, headers=None):
    response = client.post('/api/v1/ingestion/upload', headers=headers or _headers(), files={
        'files': ('sales.csv', b'region,amount\nNorth,10.00\nNorth,2.50\nSouth,4.00\nEast,8.00\n')})
    assert response.status_code == 201, response.text
    file_id = response.json()['files'][0]['file_id']
    assert client.post(f'/api/v1/files/{file_id}/clean', headers=headers or _headers()).json()['passed']
    return file_id


def _message(client, conversation_id, message, headers=None, **kwargs):
    return client.post(f'/api/v1/conversations/{conversation_id}/messages',
                       headers=headers or _headers(), json={'message': message, **kwargs})


def test_conversation_lifecycle_and_message_pagination(memory_client):
    client, _ = memory_client
    first = _conversation(client)
    second = _conversation(client)
    assert len(client.get('/api/v1/conversations', headers=_headers()).json()['conversations']) == 2
    assert client.get(f'/api/v1/conversations/{second}', headers=_headers()).json()['messages'] == []
    assert client.patch(f'/api/v1/conversations/{first}', headers=_headers(),
                        json={'title': 'Sales review'}).json()['title'] == 'Sales review'
    for text in ('first', 'second'):
        response = _message(client, first, text, analytics_domain='olist_retail')
        assert response.status_code == 422
    # Clarification questions are persisted as user and assistant messages.
    sent = _message(client, first, 'What about the other one?')
    assert sent.status_code == 200
    assert sent.json()['clarification_required'] is True
    assert sent.json()['intent'] == 'clarification'
    page = client.get(f'/api/v1/conversations/{first}?limit=1&offset=0', headers=_headers()).json()
    assert len(page['messages']) == 1
    assert page['messages'][0]['role'] == 'user'
    with client.app.state.service.sessions() as session:
        assert session.scalar(select(ConversationMessage.id).where(
            ConversationMessage.conversation_id == first)) is not None
    assert client.delete(f'/api/v1/conversations/{first}', headers=_headers()).status_code == 204
    with client.app.state.service.sessions() as session:
        assert session.scalar(select(ConversationMessage.id).where(
            ConversationMessage.conversation_id == first)) is None
    assert client.get(f'/api/v1/conversations/{first}', headers=_headers()).status_code == 404


def test_generic_context_ranking_filters_and_failed_query_preserve_context(memory_client):
    client, _ = memory_client
    file_id = _clean_file(client)
    conversation_id = _conversation(client)
    first = _message(client, conversation_id, 'What is the sum of amount by region?', file_id=file_id).json()
    assert first['context_updated'] is True
    assert first['structured_result']['value'] == '24.50'
    assert first['resolved_context']['active_file_id'] == file_id
    top = _message(client, conversation_id, 'Make it top two.').json()
    assert top['interpreted_query']['followup'] is True
    assert top['structured_result']['ranking'] == {'n': 2, 'direction': 'top', 'dimension': 'region'}
    assert [item['value'] for item in top['structured_result']['groups']] == ['North', 'East']
    filtered = _message(client, conversation_id, 'Only amount above 5.').json()
    assert filtered['resolved_context']['filters'] == [{'column': 'amount', 'operator': 'gt', 'value': '5'}]
    assert filtered['structured_result']['rows_matched'] == 2
    compared = _message(client, conversation_id, 'Compare both.').json()
    assert compared['structured_result']['operation'] == 'compare_previous_results'
    assert compared['structured_result']['previous_value'] == '24.50'
    assert compared['structured_result']['current_value'] == '18.00'
    assert compared['structured_result']['difference'] == '-6.50'
    old_context = compared['resolved_context']
    old_version = client.get(f'/api/v1/conversations/{conversation_id}', headers=_headers()).json()['context_version']
    failed = _message(client, conversation_id, 'Sum missing_measure.').json()
    assert failed['error']['code'] == 'UNSUPPORTED_QUESTION'
    assert failed['intent'] == 'unsupported'
    state = client.get(f'/api/v1/conversations/{conversation_id}', headers=_headers()).json()
    assert state['context'] == old_context
    assert state['context_version'] == old_version
    assert state['messages'][-1]['message_type'] == 'ERROR'


def test_remove_filter_and_ambiguous_followup_preserve_context(memory_client):
    client, _ = memory_client
    file_id = _clean_file(client)
    conversation_id = _conversation(client)
    _message(client, conversation_id, 'What is the sum of amount?', file_id=file_id)
    filtered = _message(client, conversation_id, 'Only amount above 5.').json()
    assert len(filtered['resolved_context']['filters']) == 1
    ambiguous = _message(client, conversation_id, 'What about the other one?').json()
    assert ambiguous['clarification_required'] is True
    assert ambiguous['context_updated'] is False
    removed = _message(client, conversation_id, 'Remove that filter.').json()
    assert removed['resolved_context']['filters'] == []
    assert removed['structured_result']['rows_matched'] == 4


def test_owner_isolation_for_conversations_messages_and_files(memory_client):
    client, _ = memory_client
    conversation_id = _conversation(client)
    assert client.get(f'/api/v1/conversations/{conversation_id}', headers=_headers('bob-token')).status_code == 404
    assert client.patch(f'/api/v1/conversations/{conversation_id}', headers=_headers('bob-token'),
                        json={'title': 'steal'}).status_code == 404
    assert client.delete(f'/api/v1/conversations/{conversation_id}', headers=_headers('bob-token')).status_code == 404
    assert _message(client, conversation_id, 'What about November?', headers=_headers('bob-token')).status_code == 404

    file_id = _clean_file(client)
    bob_conversation = _conversation(client, _headers('bob-token'))
    cross_file = _message(client, bob_conversation, 'How many rows?', headers=_headers('bob-token'),
                          file_id=file_id)
    assert cross_file.status_code == 404
    saved = client.get(f'/api/v1/conversations/{bob_conversation}', headers=_headers('bob-token')).json()
    assert saved['context']['active_file_id'] is None


def test_retail_october_november_comparison_and_new_metric(memory_client):
    client, _ = memory_client
    conversation_id = _conversation(client)
    october = _message(client, conversation_id,
        'What was the merchandise value in October 2017?', analytics_domain='olist_retail').json()
    assert october['structured_result']['result']['merchandise_value'] == '648247.65'
    november = _message(client, conversation_id, 'What about November?').json()
    assert november['structured_result']['result']['merchandise_value'] == '987765.37'
    assert november['resolved_context']['metric']['id'] == october['resolved_context']['metric']['id']
    compared = _message(client, conversation_id, 'Compare both.').json()
    assert compared['intent'] == 'comparison'
    assert compared['structured_result']['result']['period_a'] == '2017-11'
    assert compared['structured_result']['result']['period_b'] == '2017-10'
    assert compared['structured_result']['result']['difference_a_minus_b'] == '339517.72'
    assert compared['resolved_context']['time_context']['comparison_period'] == '2017-10'
    new_metric = _message(client, conversation_id, 'What is the on-time delivery percentage?').json()
    assert new_metric['resolved_context']['metric']['id'] == 'on_time_delivery_percentage'
    assert new_metric['interpreted_query']['followup'] is False
    assert new_metric['intent'] == 'new_query'


def test_retail_top_five_to_ten_and_explanation(memory_client):
    client, _ = memory_client
    conversation_id = _conversation(client)
    first = _message(client, conversation_id, 'Show the top five categories by merchandise value.',
                      analytics_domain='olist_retail').json()
    assert len(first['structured_result']['result']) == 5
    expanded = _message(client, conversation_id, 'Make it top ten.').json()
    assert len(expanded['structured_result']['result']) == 10
    assert expanded['resolved_context']['metric']['id'] == first['resolved_context']['metric']['id']
    reconciliation = _message(client, conversation_id,
        'Why does payment value differ from merchandise plus freight?').json()
    assert reconciliation['structured_result']['result']['business_cause_established'] is False
    explained = _message(client, conversation_id, 'Explain it in more detail.').json()
    assert explained['intent'] == 'explanation'
    assert explained['structured_result']['result']['business_cause_established'] is False
    assert 'does not establish why' in explained['structured_result']['answer']


def test_conversations_and_context_survive_application_restart(tmp_path):
    settings = Settings(storage_dir=tmp_path, api_tokens={'token': 'alice'}, ai_enabled=False)
    with TestClient(create_app(settings)) as first_client:
        file_id = _clean_file(first_client, {'Authorization': 'Bearer token'})
        conversation_id = _conversation(first_client, {'Authorization': 'Bearer token'})
        answer = _message(first_client, conversation_id, 'What is the sum of amount?',
                         {'Authorization': 'Bearer token'}, file_id=file_id).json()
        assert answer['context_updated'] is True
    with TestClient(create_app(settings)) as second_client:
        restored = second_client.get(f'/api/v1/conversations/{conversation_id}',
                                     headers={'Authorization': 'Bearer token'}).json()
        assert restored['context']['generic_query']['operation'] == 'sum'
        assert restored['context']['active_file_id'] == file_id
        assert [item['role'] for item in restored['messages']] == ['user', 'assistant']


def test_additive_migration_preserves_existing_module1_tables(tmp_path):
    url = f'sqlite:///{tmp_path / "legacy.db"}'
    from sqlalchemy import create_engine
    engine = create_engine(url)
    legacy_tables = [table for table in Base.metadata.sorted_tables
                     if table.name not in {'conversations', 'conversation_messages', 'schema_migrations'}]
    Base.metadata.create_all(engine, tables=legacy_tables)
    with engine.begin() as connection:
        connection.execute(User.__table__.insert().values(id='legacy-user'))
    engine.dispose()
    migrated_engine, sessions = database(url)
    try:
        names = set(inspect(migrated_engine).get_table_names())
        assert {'ingestion_runs', 'uploaded_files', 'conversations', 'conversation_messages'} <= names
        with sessions() as session:
            assert session.scalar(select(SchemaMigration.version)) == 1
            assert session.get(User, 'legacy-user') is not None
    finally:
        migrated_engine.dispose()


def test_context_window_is_bounded_and_does_not_store_result_payload(memory_client):
    client, _ = memory_client
    file_id = _clean_file(client)
    conversation_id = _conversation(client)
    for index in range(8):
        _message(client, conversation_id, f'How many rows? turn {index}', file_id=file_id)
    detail = client.get(f'/api/v1/conversations/{conversation_id}?limit=100', headers=_headers()).json()
    assistant = next(item for item in detail['messages'] if item['role'] == 'assistant')
    assert set(assistant['query_result_reference']) <= {'metric_id', 'operation', 'period', 'evidence'}
    service = client.app.state.conversations
    built = service.build_context('alice', conversation_id)
    assert built['window_message_count'] == 12
    assert len(built['recent_messages']) <= 12


def test_invalid_ai_plan_is_rejected_and_does_not_overwrite_memory(memory_client):
    client, settings = memory_client
    file_id = _clean_file(client)
    conversation_id = _conversation(client)
    first = _message(client, conversation_id, 'What is the sum of amount?', file_id=file_id).json()
    before = first['resolved_context']
    settings.ai_enabled = True
    settings.ai_key = 'mock-key'
    settings.ai_model = 'mock-model'

    def responder(request):
        body = request.content.decode('utf-8')
        assert '10.00' not in body and 'North,10.00' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'operation': 'sum', 'column': 'private_column', 'group_by': None, 'filters': []})}}]})

    result = client.app.state.conversations.send_message('alice', conversation_id,
        'What is the maximum amount?', file_id=file_id, transport=httpx.MockTransport(responder))
    assert result['error']['code'] == 'INVALID_QUERY_PLAN'
    assert result['context_updated'] is False
    after = client.get(f'/api/v1/conversations/{conversation_id}', headers=_headers()).json()
    assert after['context'] == before
    assert after['messages'][-1]['message_type'] == 'ERROR'


def test_unknown_persisted_metric_context_is_rejected(memory_client):
    client, _ = memory_client
    conversation_id = _conversation(client)
    service = client.app.state.conversations
    with service.sessions() as session:
        from sqlalchemy import update
        session.execute(update(Conversation).where(Conversation.id == conversation_id).values(
            active_analytics_domain='olist_retail',
            context={'analytics_domain': 'olist_retail', 'metric': {'id': 'invented_metric', 'name': 'invented'}}))
        session.commit()
    response = _message(client, conversation_id, 'What about November?', analytics_domain='olist_retail')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'INVALID_CONVERSATION_CONTEXT'


def test_concurrent_turns_serialize_and_increment_context_version(memory_client):
    client, _ = memory_client
    file_id = _clean_file(client)
    conversation_id = _conversation(client)
    manager = client.app.state.conversations
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(manager.send_message, 'alice', conversation_id, prompt, file_id)
                   for prompt in ('What is the sum of amount?', 'What is the average amount?')]
        answers = [future.result() for future in futures]
    assert all(answer['context_updated'] for answer in answers)
    detail = client.get(f'/api/v1/conversations/{conversation_id}?limit=20', headers=_headers()).json()
    assert detail['context_version'] == 2
    assert len(detail['messages']) == 4
