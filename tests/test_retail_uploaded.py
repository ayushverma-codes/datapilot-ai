import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.retail import summarize_olist


AUTH = {'Authorization': 'Bearer alice'}
OTHER = {'Authorization': 'Bearer bob'}
ROOT = '/api/v1/retail'


@pytest.fixture
def retail(tmp_path):
    settings = Settings(storage_dir=tmp_path, api_tokens={'alice': 'alice', 'bob': 'bob'}, ai_enabled=False)
    with TestClient(create_app(settings)) as client:
        yield client, client.app.state.service


def add(service, text, name='arbitrary.csv', clean=True):
    [file] = service.upload('alice', [(name, text.encode())])['files']
    assert file['status'] != 'FAILED', file['error']
    if clean:
        assert service.clean('alice', file['file_id'])['passed']
    return file['file_id']


def post(client, endpoint, payload):
    return client.post(ROOT + endpoint, headers=AUTH, json=payload)


def value(response):
    assert response.status_code == 200, response.text
    return response.json()


def fields(**roles):
    return {role: {'column': col} for role, col in roles.items()}


def relationship(left, right, *, cardinality='many_to_one', join_type='left'):
    return {'left_file_id': left, 'right_file_id': right, 'left_keys': ['ref-A'], 'right_keys': ['ref B'],
            'cardinality': cardinality, 'join_type': join_type}


def test_normalization_statuses_approval_and_original_preservation(retail):
    client, service = retail
    raw = 'Label!,item\nBLUE,p1\nBlue,p2\nUnknown,p3\n,p4\nDresses,p5\n'
    source = add(service, raw, 'shelves.csv')
    master = add(service, 'Term,Approved\nDresses,Apparel\n', 'confirmed-terms.csv')
    spec = {'file_id': source, 'column': 'Label!', 'entity': 'category'}
    review = value(post(client, '/normalization', spec))
    assert review['status_counts'] == {'MISSING_SOURCE': 1, 'UNCERTAIN': 2, 'UNTRANSLATED': 2}
    assert all(row['canonical'] is None for row in review['values'])
    approval = {'file_id': master, 'source_key': 'Term', 'canonical_value': 'Approved',
                'approval_reason': 'Confirmed in the retailer catalog'}
    result = value(post(client, '/normalization', {**spec, 'mapping': approval}))
    rows = {row['original']: row for row in result['values']}
    assert rows['Dresses']['canonical'] == 'Apparel' and rows['Dresses']['status'] == 'MAPPED'
    assert rows['Unknown']['status'] == 'UNTRANSLATED' and rows['Unknown']['canonical'] is None
    assert rows['BLUE']['potential_variants'] == ['Blue'] and rows['BLUE']['status'] == 'UNCERTAIN'
    assert rows[None]['status'] == 'MISSING_SOURCE'
    assert result['evidence']['source_datasets']['mapping']['revision'] == 1
    assert result['mapping_approval'] == approval
    assert service.download('alice', source).read_bytes() == raw.encode()
    page = value(post(client, '/normalization', {**spec, 'entity': 'product', 'limit': 1}))
    assert page['entity'] == 'product' and len(page['values']) == 1 and page['next_offset'] == 1
    assert client.post(ROOT + '/normalization', headers=OTHER, json=spec).status_code == 404


@pytest.mark.parametrize('mapping,code', [
    ('Term,Approved\nA,B\nA,C\n', 'INVALID_MAPPING_KEYS'),
    ('Term,Approved\nA,B\nB,C\n', 'AMBIGUOUS_MAPPING'),
    ('Term,Approved\n,A\n', 'INVALID_MAPPING_KEYS'),
])
def test_invalid_mapping_rejected(retail, mapping, code):
    client, service = retail
    source = add(service, 'label\nA\n')
    master = add(service, mapping)
    response = post(client, '/normalization', {'file_id': source, 'column': 'label', 'entity': 'product',
                    'mapping': {'file_id': master, 'source_key': 'Term', 'canonical_value': 'Approved',
                                'approval_reason': 'Reviewed'}})
    assert response.status_code == 422 and response.json()['error']['code'] == code


def test_retail_reuses_validated_ai_suggestion_without_applying_it(retail, monkeypatch):
    from app.ai.analyzer import analyze
    import app.services.ingestion_service as ingestion

    client, service = retail
    service.settings.ai_enabled = service.settings.ai_include_samples = True
    service.settings.ai_key = 'mock'
    service.settings.ai_model = 'mock'
    response = {'suggestions': [{'column': 'hue', 'operation': 'category_normalization',
                                 'parameters': {'mapping': {'blue': 'Blue'}}, 'reason': 'Review case variant',
                                 'confidence': .9, 'risk': 'low'}]}
    handler = httpx.MockTransport(lambda _: httpx.Response(
        200, json={'choices': [{'message': {'content': json.dumps(response)}}]}))
    monkeypatch.setattr(ingestion, 'analyze', lambda profile, issues, settings: analyze(profile, issues, settings, handler))
    file_id = add(service, 'hue,ref\nBlue,x\nblue,y\n')
    report = value(post(client, '/normalization', {'file_id': file_id, 'column': 'hue', 'entity': 'category'}))
    [suggestion] = report['ai_suggestions']
    assert suggestion['status'] == 'AWAITING_APPROVAL' and suggestion['evidence']['revision'] == 0
    assert all(item['canonical'] is None for item in report['values'])


def test_sales_metrics_formulas_filters_and_missing_roles(retail):
    client, service = retail
    file_id = add(service, 'invoice,line dollars,Count?,Outcome,Branch,Hue\n'
                  'a,2.10,3,keep,north,blue\na,0.20,1,back,north,blue\nb,9,2,keep,south,green\n')
    request = {
        'file_id': file_id, 'metrics': ['total_revenue', 'order_count', 'average_order_value', 'units_sold', 'return_rate',
                                       'inventory_availability', 'category_performance', 'store_performance'],
        'fields': fields(order_id='invoice', revenue='line dollars', quantity='Count?', return_status='Outcome',
                         store='Branch', category='Hue'),
        'returned_values': ['back'], 'non_returned_values': ['keep'],
    }
    metrics = value(post(client, '/metrics', request))['metrics']
    assert {name: metrics[name]['value'] for name in request['metrics'][:5]} == {
        'total_revenue': '11.30', 'order_count': 2, 'average_order_value': '5.6500', 'units_sold': 6,
        'return_rate': '50.0000'}
    assert metrics['inventory_availability']['status'] == 'UNSUPPORTED'
    assert metrics['inventory_availability']['evidence']['missing_evidence'] == [
        {'role': 'on_hand', 'reason': 'FIELD_MAPPING_REQUIRED'}]
    assert metrics['category_performance']['value'] == [
        {'group': 'blue', 'revenue': '2.30', 'records': 2}, {'group': 'green', 'revenue': '9', 'records': 1}]
    assert [row['group'] for row in metrics['store_performance']['value']] == ['north', 'south']
    filters = [{'column': 'Branch', 'values': ['north']}]
    north = value(post(client, '/metrics', {**request, 'filters': filters}))['metrics']
    assert north['total_revenue']['value'] == '2.30'
    evidence = north['total_revenue']['evidence']
    assert evidence['records_considered'] == 2 and evidence['join'] is None and evidence['formula']
    assert evidence['source_datasets']['left']['revision'] == 1
    assert evidence['source_fields']['revenue']['column'] == 'line dollars'
    assert evidence['filters'][0]['values'] == ['north']


def test_inventory_and_refusing_partial_or_empty_calculations(retail):
    client, service = retail
    file_id = add(service, 'bin,Free units\nA,2\nB,0\nC,0\nD,1\n')
    body = {'file_id': file_id, 'metrics': ['inventory_availability', 'stock_out_frequency'],
            'fields': fields(on_hand='Free units')}
    metrics = value(post(client, '/metrics', body))['metrics']
    assert all(metric['value'] == '50.0000' for metric in metrics.values())
    empty = value(post(client, '/metrics', {**body, 'filters': [{'column': 'bin', 'values': ['none']}]}))['metrics']
    assert empty['inventory_availability']['evidence']['missing_evidence'] == [{'reason': 'ZERO_DENOMINATOR'}]
    missing = value(post(client, '/metrics', {**body, 'fields': fields(on_hand='no such field')}))['metrics']
    assert missing['stock_out_frequency']['evidence']['missing_evidence'][0]['reason'] == 'SOURCE_FIELD_NOT_FOUND'
    partial = add(service, 'bin,Free units\nA,0\nB,\nC,-1\nD,1.5\n')
    invalid = value(post(client, '/metrics', {**body, 'file_id': partial}))['metrics']
    assert invalid['stock_out_frequency']['value'] is None
    assert invalid['stock_out_frequency']['evidence']['missing_evidence'][0]['records'] == 3


def test_alternate_revenue_basis_is_auditable(retail):
    client, service = retail
    file_id = add(service, 'receipt,cost,count,category\na,1.25,2,A\nb,2.00,3,B\n')
    response = value(post(client, '/metrics', {'file_id': file_id,
                     'metrics': ['total_revenue', 'average_order_value', 'category_performance'],
                     'fields': fields(unit_price='cost', quantity='count', order_id='receipt', category='category')}))
    metrics = response['metrics']
    assert metrics['total_revenue']['value'] == '8.50'
    assert metrics['average_order_value']['value'] == '4.2500'
    assert set(metrics['total_revenue']['evidence']['source_fields']) == {'unit_price', 'quantity'}
    assert 'unit price × units sold' in metrics['total_revenue']['evidence']['formula']
    assert metrics['category_performance']['value'][0]['revenue'] == '2.50'


def test_join_registry_metrics_version_pinning_and_owner_isolation(retail):
    client, service = retail
    left = add(service, 'ref-A,revenue\n001,10\n001,15\n002,40\n,7\n004,5\n', 'ledger.csv')
    right = add(service, 'ref B,Dept\n001,Apparel\n002,Shoes\n003,Unused\n,None\n', 'lookup.csv')
    body = relationship(left, right)
    discovered = value(post(client, '/relationships/discover', {'left_file_id': left, 'right_file_id': right}))
    assert discovered['candidates'][0]['status'] == 'UNVERIFIED'
    assert discovered['candidates'][0]['left_keys'] == ['ref-A']
    registered = value(post(client, '/relationships', body))
    assert value(post(client, '/relationships', body)) == registered
    relid = registered['relationship_id']
    endpoint = ROOT + f'/relationships/{relid}/preview'
    stats = registered['validation']
    assert stats['observed_cardinality'] == 'many_to_one'
    assert stats['left_missing_keys'] == stats['right_missing_keys'] == 1
    assert stats['left_unmatched_rows'] == stats['right_unmatched_rows'] == 2 and stats['output_rows'] == 5
    assert value(client.get(endpoint + '?offset=1&limit=2', headers=AUTH))['matches'] == [
        {'left_record': 2, 'right_record': 1}, {'left_record': 3, 'right_record': 2}]
    assert client.get(endpoint, headers=OTHER).status_code == 404
    assert value(client.get(ROOT + '/relationships', headers=OTHER)) == {'relationships': []}

    metrics = {'file_id': left, 'relationship_id': relid, 'metrics': ['total_revenue', 'category_performance'],
               'fields': {**fields(revenue='revenue'), 'category': {'side': 'right', 'column': 'Dept'}}}
    output = value(post(client, '/metrics', metrics))['metrics']
    assert output['total_revenue']['value'] == '77'
    assert output['category_performance']['value'][-1] == {'group': None, 'revenue': '12', 'records': 2}
    assert output['category_performance']['evidence']['join']['relationship_id'] == relid
    proposal = service.propose('alice', right, 'normalize_strings', ['Dept'], {'mode': 'lower'}, 'Confirm')
    service.decide('alice', [proposal['id']], 'APPROVED')
    assert service.clean('alice', right)['passed']
    assert value(post(client, '/metrics', metrics))['metrics'] == output
    assert value(post(client, '/relationships', body))['sources']['right']['revision'] == 2
    pinned_version = registered['sources']['right']['artifact_id']
    path = next(service.settings.storage_dir.rglob(f'cleaned/{pinned_version}.csv'))
    path.write_bytes(path.read_bytes() + b'999,Tampered\n')
    assert client.get(endpoint, headers=AUTH).json()['error']['code'] == 'RETAIL_ARTIFACT_CHANGED'


def test_one_to_many_preview_fanout_and_many_to_many_rejected(retail):
    client, service = retail
    left = add(service, 'ref-A,revenue\nx,10\ny,11\n')
    right = add(service, 'ref B,event\nx,ship\nx,return\n')
    body = relationship(left, right, cardinality='one_to_many')
    relation = value(post(client, '/relationships', body))
    assert relation['validation']['left_fanout_rows'] == 1
    assert relation['validation']['output_rows'] == 3
    metrics = {'file_id': left, 'relationship_id': relation['relationship_id'], 'metrics': ['total_revenue'],
               'fields': fields(revenue='revenue')}
    error = post(client, '/metrics', metrics).json()['error']
    assert error['code'] == 'UNSAFE_METRIC_JOIN'
    # Inner output count can equal the left count despite both dropping and duplicating rows.
    inner = value(post(client, '/relationships', {**body, 'join_type': 'inner'}))
    assert inner['validation']['output_rows'] == 2
    assert post(client, '/metrics', {**metrics, 'relationship_id': inner['relationship_id']}).json()['error']['code'] == 'UNSAFE_METRIC_JOIN'
    duplicate = add(service, 'ref-A,revenue\nx,10\nx,11\n')
    response = post(client, '/relationships', {**body, 'left_file_id': duplicate})
    assert response.json()['error']['details']['observed_cardinality'] == 'many_to_many'
    assert response.json()['error']['code'] == 'UNSAFE_JOIN_CARDINALITY'
    assert post(client, '/relationships', {**body, 'right_keys': ['missing']}).json()['error']['code'] == 'UNKNOWN_RETAIL_COLUMNS'
    assert post(client, '/relationships', {**body, 'cardinality': 'many_to_one'}).json()['error']['code'] == 'UNSAFE_JOIN_CARDINALITY'


def test_inner_drop_and_unvalidated_dataset_are_rejected(retail):
    client, service = retail
    left = add(service, 'ref-A,revenue\nx,10\ny,20\n')
    right = add(service, 'ref B,metadata\nx,ship\n')
    rel = value(post(client, '/relationships', relationship(left, right, join_type='inner')))
    metrics = {'file_id': left, 'relationship_id': rel['relationship_id'], 'metrics': ['total_revenue'],
               'fields': fields(revenue='revenue')}
    assert post(client, '/metrics', metrics).json()['error']['code'] == 'UNSAFE_METRIC_JOIN'
    unvalidated = add(service, 'z\nx\n', clean=False)
    assert post(client, '/metrics', {'file_id': unvalidated, 'metrics': ['total_revenue']}).json()['error']['code'] == 'RETAIL_DATASET_NOT_VALIDATED'


def test_composite_join_matches_exact_components(retail):
    client, service = retail
    left = add(service, 'key,branch,amount\n01,A,5\n01,B,6\n01,,7\n')
    right = add(service, 'item,location,label\n01,B,north\n01,A,south\n01,,unverified\n1,A,another\n')
    rel = value(post(client, '/relationships', {'left_file_id': left, 'right_file_id': right,
                     'left_keys': ['key', 'branch'], 'right_keys': ['item', 'location'], 'cardinality': 'one_to_one'}))
    response = client.get(ROOT + f"/relationships/{rel['relationship_id']}/preview", headers=AUTH)
    assert value(response)['matches'] == [{'left_record': 1, 'right_record': 2}, {'left_record': 2, 'right_record': 1},
                                         {'left_record': 3, 'right_record': None}]


def test_olist_matches_complete_accepted_enriched_summary():
    root = Path(__file__).resolve().parents[1] / 'evaluation_output'
    accepted = json.loads((root / 'module2' / 'olist-summary-phase3.json').read_text(encoding='utf-8'))
    assert summarize_olist(root / 'olist' / 'handoff' / 'cleaned') == accepted
