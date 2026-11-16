"""Run the deterministic multi-turn memory evaluation from its checked-in scenario set."""

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


SCENARIOS = ROOT / 'examples' / 'conversation_memory_evaluation.json'


def _value_at(mapping, dotted_path):
    value = mapping
    for component in dotted_path.split('.'):
        value = value[component]
    return value


def run_evaluation():
    spec = json.loads(SCENARIOS.read_text(encoding='utf-8'))
    groups = {}
    scenario_results = []
    with tempfile.TemporaryDirectory(prefix='memory-eval-') as scratch:
        settings = Settings(storage_dir=Path(scratch), dev_mode=True, ai_enabled=False)
        with TestClient(create_app(settings)) as client:
            fixture_ids = {}
            for key, filename in [('sales', 'memory_sales.csv'), ('sales_alt', 'memory_sales_alt.csv')]:
                content = (ROOT / 'examples' / filename).read_bytes()
                upload = client.post('/api/v1/ingestion/upload', files={'files': (filename, content)})
                upload.raise_for_status()
                file_id = upload.json()['files'][0]['file_id']
                cleaned = client.post(f'/api/v1/files/{file_id}/clean')
                cleaned.raise_for_status()
                fixture_ids[key] = file_id

            for scenario in spec['scenarios']:
                created = client.post('/api/v1/conversations', json={})
                created.raise_for_status()
                conversation_id = created.json()['conversation_id']
                previous_context = None
                last_body = None
                turns = list(scenario['history']) + [{
                    'message': scenario['message'],
                    'domain': scenario.get('domain'),
                    'file_key': scenario.get('file_key'),
                }]
                for turn in turns:
                    body = {'message': turn['message']}
                    if turn.get('domain'):
                        body['analytics_domain'] = turn['domain']
                    if turn.get('file_key'):
                        body['file_id'] = fixture_ids[turn['file_key']]
                    if last_body:
                        previous_context = last_body.get('resolved_context')
                    response = client.post(
                        f'/api/v1/conversations/{conversation_id}/messages', json=body)
                    body_result = response.json()
                    last_body = body_result

                expected = scenario['expect']
                checks = {}
                if 'followup' in expected:
                    checks['followup_intent'] = bool(body_result.get('interpreted_query', {}).get('followup')) == expected['followup']
                if 'intent' in expected:
                    checks['intent_classification'] = body_result.get('intent') == expected['intent']
                if 'metric_id' in expected:
                    checks['metric_preservation'] = body_result.get('resolved_context', {}).get('metric', {}).get('id') == expected['metric_id']
                if 'period' in expected:
                    checks['time_period'] = body_result.get('resolved_context', {}).get('time_context', {}).get('period') == expected['period']
                if 'ranking_n' in expected:
                    context_n = body_result.get('resolved_context', {}).get('ranking', {}).get('n')
                    result_grouping = (body_result.get('structured_result') or {}).get('grouping') or {}
                    checks['ranking_modification'] = context_n == expected['ranking_n'] and (
                        result_grouping.get('n', expected['ranking_n']) == expected['ranking_n'])
                if 'filters' in expected:
                    checks['filter_preservation'] = body_result.get('resolved_context', {}).get('filters') == expected['filters']
                if 'clarification_required' in expected:
                    checks['ambiguity_detection'] = body_result.get('clarification_required') == expected['clarification_required']
                if 'context_updated' in expected:
                    checks['context_integrity'] = body_result.get('context_updated') == expected['context_updated']
                if 'result_path' in expected:
                    actual = _value_at(body_result.get('structured_result') or {}, expected['result_path'])
                    if actual != expected['result'] and str(actual) != str(expected['result']):
                        checks['result'] = False
                    else:
                        checks['result'] = True
                if 'result_length' in expected:
                    structured = body_result.get('structured_result') or {}
                    result_value = structured.get('result')
                    if result_value is None:
                        result_value = structured.get('groups')
                    checks['result_length'] = len(result_value or []) == expected['result_length']
                if 'answer_contains' in expected:
                    checks['answer_limit'] = expected['answer_contains'].casefold() in (
                        body_result.get('structured_result', {}).get('answer', '').casefold())
                if 'active_file_key' in expected:
                    checks['dataset_change'] = body_result.get('resolved_context', {}).get('active_file_id') == fixture_ids[expected['active_file_key']]
                if 'error_code' in expected:
                    checks['failed_query_error'] = body_result.get('error', {}).get('code') == expected['error_code']
                if expected.get('preserve_context'):
                    checks['context_corruption'] = body_result.get('resolved_context') == previous_context
                    checks['context_integrity'] = checks.get('context_integrity', True) and checks['context_corruption']

                for measure in scenario['measures']:
                    check_name = measure
                    if measure == 'unrelated_message':
                        check_name = 'intent_classification'
                    elif measure == 'context_integrity':
                        check_name = ('context_corruption' if expected.get('preserve_context') else
                                      'dataset_change' if 'active_file_key' in expected else
                                      'context_integrity')
                    value = checks.get(check_name, False)
                    group = groups.setdefault(measure, {'passed': 0, 'total': 0})
                    group['total'] += 1
                    group['passed'] += int(value)
                scenario_results.append({'id': scenario['id'], 'passed': all(checks.values()), 'checks': checks})

    summary = {name: {'passed': row['passed'], 'total': row['total'],
                      'accuracy': round(row['passed'] / row['total'], 4) if row['total'] else None}
               for name, row in groups.items()}
    return {'evaluation': spec['name'], 'scenario_count': len(scenario_results),
            'passed_scenarios': sum(item['passed'] for item in scenario_results),
            'scenario_accuracy': round(sum(item['passed'] for item in scenario_results) / len(scenario_results), 4),
            'metrics': summary, 'scenarios': scenario_results}


if __name__ == '__main__':
    result = run_evaluation()
    print(json.dumps(result, indent=2))
