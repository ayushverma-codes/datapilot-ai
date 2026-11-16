import json
from pathlib import Path

from app.evaluate import evaluate


def test_assessment_cases_run_joined_retail_and_chat_with_evidence(tmp_path):
    cases = Path(__file__).resolve().parents[1] / 'cases.json'
    result = evaluate(cases, tmp_path / 'result.json', tmp_path / 'artifacts')
    assert result['passed'] == result['case_count'] == 2
    case = result['results'][0]
    joined, inventory = case['retail_evaluation']
    categories = joined['result']['metrics']['category_performance']
    assert categories['value'] == [
        {'group': 'Clothing', 'revenue': '27', 'records': 2},
        {'group': 'Tools', 'revenue': '10', 'records': 1},
    ]
    evidence = categories['evidence']
    assert evidence['records_considered'] == 3
    assert evidence['source_datasets']['right']['revision'] == 1
    assert evidence['join']['relationship_id'] == joined['relationship']['relationship_id']
    assert joined['result']['metrics']['return_rate']['evidence']['missing_evidence']
    assert inventory['result']['metrics']['stock_out_frequency']['value'] == '50.0000'
    assert case['chat_evaluation'][2]['result']['result']['value'] == '15'
    assert case['chat_evaluation'][3]['status'] == 'NEEDS_CLARIFICATION'


def test_unsafe_retail_join_does_not_stop_other_checks_or_cases(tmp_path):
    (tmp_path / 'sales.csv').write_text('sku,revenue\nP1,20\nP2,10\n', encoding='utf-8')
    (tmp_path / 'products.csv').write_text('code,category\nP1,Tools\nP1,Clothing\nP2,Tools\n', encoding='utf-8')
    safe = {'file_index': 0, 'request': {'fields': {'revenue': {'column': 'revenue'}}},
            'expected_metrics': {'total_revenue': {'value': '30'}}}
    unsafe = {**safe, 'join': {'right_file_index': 1, 'left_keys': ['sku'], 'right_keys': ['code'],
                              'cardinality': 'one_to_many'}}
    cases = tmp_path / 'cases.json'
    cases.write_text(json.dumps({'cases': [
        {'id': 'blocked-fanout', 'files': ['sales.csv', 'products.csv'], 'retail': [unsafe, safe]},
        {'id': 'independent', 'files': ['sales.csv'], 'retail': [safe]},
    ]}), encoding='utf-8')
    result = evaluate(cases, tmp_path / 'result.json', tmp_path / 'artifacts')
    assert result['case_count'] == 2 and result['passed'] == 1
    blocked, continued = result['results'][0]['retail_evaluation']
    assert blocked['error']['code'] == 'UNSAFE_METRIC_JOIN'
    assert blocked['result'] is None and not blocked['passed']
    assert continued['passed'] and result['results'][1]['success']
