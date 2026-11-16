import json
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.conversation.retail_contracts import RetailPlan
from app.main import create_app
from app.models.contracts import PipelineError
from app.retail_bi import (
    METRIC_CATALOG,
    answer_retail_question,
    catalog,
    deterministic_retail_plan,
    execute_retail_plan,
    load_verified_summary,
)


def ask(question):
    return answer_retail_question(question, Settings())


def synthetic_summary():
    return {
        'order_count_by_status': {'delivered': 0, 'canceled': 0},
        'delivered_merchandise_value': '0',
        'delivered_freight_value': '0',
        'delivered_payment_value': '0',
        'delivery': {'on_time': 0, 'measured': 0, 'missing_dates': 0},
        'monthly': [
            {'purchase_month': '2024-01', 'delivered_orders': 1, 'merchandise_value': '0'},
            {'purchase_month': '2024-02', 'delivered_orders': 1, 'merchandise_value': '10.00'},
        ],
        'categories': [
            {'source_category': 'beta', 'category_english': None, 'mapping_status': 'UNTRANSLATED', 'merchandise_value': '5'},
            {'source_category': None, 'category_english': None, 'mapping_status': 'MISSING_SOURCE_CATEGORY', 'merchandise_value': '0'},
            {'source_category': 'alpha', 'category_english': 'alpha', 'mapping_status': 'TRANSLATED', 'merchandise_value': '5'},
        ],
        'category_mapping_completeness': {
            'product_count': 0, 'translated_product_count': 0, 'untranslated_product_count': 0,
            'missing_source_category_product_count': 0, 'source_categories': 0,
        },
        'purchase_timestamp_exceptions': {'missing_delivered_orders': 0, 'invalid_delivered_orders': 0},
        'unmatched_rows': {'item_order': 0, 'delivered_item_product': 0, 'payment_order': 0},
    }


def test_catalog_documents_metric_contracts():
    entries = {entry['key']: entry for entry in catalog()}
    assert entries.keys() == METRIC_CATALOG.keys()
    for entry in entries.values():
        assert entry['name'] and entry['definition'] and entry['source']
        assert entry['aggregation_method'] and entry['order_cohort'] and entry['unit']
        assert isinstance(entry['known_limitations'], list)
        assert isinstance(entry['supports_time_comparison'], bool)
        assert isinstance(entry['grouping_dimensions'], list)
    assert entries['delivered_payment_value']['known_limitations'][0].startswith('Recorded payments are not assumed')
    assert entries['on_time_delivery_percentage']['aggregation_method'].startswith('on_time_deliveries / measured_deliveries')


def test_all_requested_business_question_categories_have_exact_answers():
    examples = {
        'How many orders do we have?': ('total_orders', 99_441),
        'How many orders were delivered?': ('delivered_orders', 96_478),
        'How many orders were cancelled?': ('cancelled_orders', 625),
        'Show the distribution of order statuses.': ('orders_by_status', None),
        'What percentage of orders were delivered?': ('delivered_orders_percentage', '97.0203'),
        'What is our delivered merchandise value?': ('delivered_merchandise_value', '13221498.11'),
        'How much freight was charged?': ('delivered_freight_value', '2198275.64'),
        'What is the recorded payment value?': ('delivered_payment_value', '15422461.77'),
        'Why does payment value differ from merchandise plus freight?': ('payment_reconciliation_explanation', None),
        'What is the average merchandise value per delivered order?': ('average_merchandise_per_delivered_order', '137.0415857501191981591658202'),
        'Which are the top five product categories by merchandise value?': ('category_merchandise_value', None),
        'Which category generates the highest merchandise value?': ('category_merchandise_value', None),
        'Show the bottom five categories.': ('category_merchandise_value', None),
        'What percentage of merchandise value comes from the top five categories?': ('category_merchandise_value', None),
        'How much merchandise value belongs to uncategorized products?': ('uncategorized_merchandise_value', '170726.63'),
        'Show monthly merchandise performance.': ('monthly_merchandise_value', None),
        'Which month had the highest merchandise value?': ('monthly_merchandise_value', None),
        'Compare November 2017 with October 2017.': ('monthly_merchandise_value', None),
        'What was the month-over-month change?': ('monthly_merchandise_value', None),
        'Which month had the highest number of delivered orders?': ('monthly_delivered_order_count', None),
        'What percentage of orders were delivered on time?': ('on_time_delivery_percentage', '93.2269'),
        'How many orders were delivered late?': ('late_deliveries', 6_534),
        'How many orders have missing delivery dates?': ('missing_delivery_dates', 8),
        'What is the measured delivery count?': ('measured_deliveries', 96_470),
    }
    for question, (metric, value) in examples.items():
        result = ask(question)
        assert result['metric_key'] == metric, question
        if value is not None:
            assert result['result'] == value, question


def test_status_distribution_and_category_rankings_are_deterministic():
    summary, _ = load_verified_summary()
    distribution = ask('Show the distribution of order statuses.')
    assert distribution['result'] == [
        {'status': 'delivered', 'orders': 96_478},
        {'status': 'shipped', 'orders': 1_107},
        {'status': 'canceled', 'orders': 625},
        {'status': 'unavailable', 'orders': 609},
        {'status': 'invoiced', 'orders': 314},
        {'status': 'processing', 'orders': 301},
        {'status': 'created', 'orders': 5},
        {'status': 'approved', 'orders': 2},
    ]

    top = ask('Which are the top five product categories by merchandise value?')
    assert [(r['rank'], r['dimension_value'], r['value']) for r in top['result']] == [
        (1, 'beleza_saude', '1233131.72'),
        (2, 'relogios_presentes', '1166176.98'),
        (3, 'cama_mesa_banho', '1023434.76'),
        (4, 'esporte_lazer', '954852.55'),
        (5, 'informatica_acessorios', '888724.61'),
    ]
    bottom = ask('Show the bottom five categories.')
    assert [r['dimension_value'] for r in bottom['result']] == [
        'seguros_e_servicos', 'fashion_roupa_infanto_juvenil', 'cds_dvds_musicais',
        'casa_conforto_2', 'flores',
    ]
    assert top['result'][0]['category_english'] == 'health_beauty'
    assert summary['categories'][0]['source_category'] == 'beleza_saude'


def test_category_percentages_null_bucket_and_untranslated_categories_are_preserved():
    contribution = ask('What percentage of merchandise value comes from the top five categories?')
    assert contribution['result']['merchandise_value'] == '5266320.62'
    assert contribution['result']['total_merchandise_value'] == '13221498.11'
    assert contribution['result']['percentage'] == '39.8315'
    categories = ask('Show merchandise value by category.')
    rows = categories['result']
    assert next(row for row in rows if row['source_category'] is None)['merchandise_value'] == '170726.63'
    untranslated = {row['source_category'] for row in rows if row['mapping_status'] == 'UNTRANSLATED'}
    assert untranslated == {'pc_gamer', 'portateis_cozinha_e_preparadores_de_alimentos'}
    mapping = ask('Show category mapping completeness.')
    assert mapping['result'] == {
        'product_count': 32_951, 'translated_product_count': 32_328,
        'untranslated_product_count': 13, 'missing_source_category_product_count': 610,
        'source_categories': 73,
    }
    uncategorized = ask('How much merchandise value belongs to uncategorized products?')
    assert uncategorized['result'] == '170726.63'


def test_month_comparisons_and_rankings_use_verified_values():
    highest = ask('Which month had the highest merchandise value?')
    assert highest['result'][0]['dimension_value'] == '2017-11'
    assert highest['result'][0]['value'] == '987765.37'
    order_month = ask('Which month had the highest number of delivered orders?')
    assert order_month['result'][0]['dimension_value'] == '2017-11'
    assert order_month['result'][0]['value'] == 7_289

    comparison = ask('Compare November 2017 with October 2017.')
    assert comparison['result'] == {
        'period_a': '2017-11', 'value_a': '987765.37',
        'period_b': '2017-10', 'value_b': '648247.65',
        'difference_a_minus_b': '339517.72',
        'percentage_change_from_b_to_a': '52.3747',
        'percentage_change_status': 'CALCULATED',
    }
    mom = ask('What was the month-over-month change?')
    assert mom['result']['period_a'] == '2018-08'
    assert mom['result']['period_b'] == '2018-07'
    assert mom['result']['difference_a_minus_b'] == '-29376.82'
    assert mom['result']['percentage_change_from_b_to_a'] == '-3.3846'


def test_verified_delivery_financial_and_reference_metrics_have_evidence():
    assert ask('What percentage of orders were delivered on time?')['result'] == '93.2269'
    assert ask('How many orders were delivered late?')['result'] == 6_534
    assert ask('How many orders have missing delivery dates?')['result'] == 8
    assert ask('What is the measured delivery count?')['result'] == 96_470
    assert ask('What is our delivered merchandise value?')['result'] == '13221498.11'
    assert ask('How much freight was charged?')['result'] == '2198275.64'
    assert ask('What is the recorded payment value?')['result'] == '15422461.77'
    unmatched = ask('Show unmatched references.')
    assert unmatched['result'] == {'item_order': 0, 'delivered_item_product': 0, 'payment_order': 0}
    assert unmatched['classification'] == 'VERIFIED'
    assert unmatched['source_dataset'] == 'Olist cleaned handoff'
    assert unmatched['source_artifacts'] == [
        'evaluation_output/module2/olist-summary.json',
        'evaluation_output/module2/olist-summary-phase3.json',
    ]
    assert unmatched['definition'] and unmatched['calculation_evidence']


def test_payment_difference_explanation_uses_phase1_facts_without_claiming_cause():
    difference = ask('What is the payment reconciliation difference?')
    assert difference['metric_key'] == 'payment_reconciliation_difference'
    assert difference['result'] == '2688.02'
    response = ask('Why does payment value differ from merchandise plus freight?')
    assert response['classification'] == 'EXPLANATION'
    assert response['result'] == {
        'difference': '2688.02',
        'orders_above_item_charges': 362,
        'amount_above_item_charges': '3032.24',
        'orders_below_item_charges': 208,
        'amount_below_item_charges': '344.22',
        'orders_balanced': 95_908,
        'orders_without_payment_rows': 1,
        'missing_payment_order_difference': '-143.46',
        'business_cause_established': False,
    }
    assert 'does not establish why' in response['answer']
    assert response['calculation_evidence']['source_report'] == 'MODULE2_PAYMENT_RECONCILIATION.md'


def test_top_k_limit_ties_and_zero_denominators():
    with pytest.raises(PipelineError) as limit:
        deterministic_retail_plan('Show top 21 categories.')
    assert limit.value.code == 'TOP_K_LIMIT'
    with pytest.raises(PipelineError) as zero_limit:
        deterministic_retail_plan('Show top 0 categories.')
    assert zero_limit.value.code == 'TOP_K_LIMIT'

    summary = synthetic_summary()
    tied = execute_retail_plan(summary, ['synthetic-summary.json'], RetailPlan(
        operation='top_k', metric='category_merchandise_value', dimension='category', n=3))
    assert [row['dimension_value'] for row in tied['result']] == ['alpha', 'beta', None]
    contribution = execute_retail_plan(summary, ['synthetic-summary.json'], RetailPlan(
        operation='contribution', metric='category_merchandise_value', dimension='category', n=2))
    assert contribution['result']['percentage'] is None
    assert 'zero' in contribution['answer']
    assert execute_retail_plan(summary, ['synthetic-summary.json'], RetailPlan(
        operation='metric', metric='delivered_orders_percentage'))['result'] is None

    compare = execute_retail_plan(summary, ['synthetic-summary.json'], RetailPlan(
        operation='compare_periods', metric='monthly_merchandise_value', dimension='month',
        period_a='2024-02', period_b='2024-01'))
    assert compare['result']['difference_a_minus_b'] == '10.00'
    assert compare['result']['percentage_change_from_b_to_a'] is None
    assert compare['result']['percentage_change_status'] == 'ZERO_BASELINE'


def test_missing_periods_and_unsupported_questions_are_explicit():
    with pytest.raises(PipelineError) as missing:
        ask('Compare November 2016 with October 2016.')
    assert missing.value.code == 'PERIOD_NOT_AVAILABLE'
    assert '2016-11' in missing.value.details['missing_periods']
    with pytest.raises(PipelineError) as unsupported:
        ask('What was our profit margin?')
    assert unsupported.value.code == 'UNSUPPORTED_QUESTION'


def test_ai_planner_failure_falls_back_and_invalid_metric_is_rejected(tmp_path):
    settings = Settings(storage_dir=tmp_path, ai_enabled=True, api_tokens={},
                        ai_key='test-key', ai_model='test-model')
    requests = []

    def unavailable(request):
        requests.append(request)
        return httpx.Response(503)

    fallback = answer_retail_question('How many orders were delivered?', settings,
                                      httpx.MockTransport(unavailable))
    assert fallback['result'] == 96_478
    assert fallback['plan_source'] == 'RULES'
    assert len(requests) == settings.ai_attempts
    assert '13221498.11' not in requests[0].content.decode()

    bad_plan = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': '{"operation":"metric","metric":"invented_profit","dimension":null}'}}]
    }))
    with pytest.raises(PipelineError) as rejected:
        answer_retail_question('What is our profit margin?', settings, bad_plan)
    assert rejected.value.code == 'UNSUPPORTED_RETAIL_METRIC'


def test_valid_ai_plan_uses_catalog_without_receiving_analytics_values(tmp_path):
    settings = Settings(storage_dir=tmp_path, ai_enabled=True, api_tokens={},
                        ai_key='test-key', ai_model='test-model')
    requests = []

    def handler(request):
        requests.append(request)
        assert '13221498.11' not in request.content.decode()
        return httpx.Response(200, json={'choices': [{'message': {'content':
            '{"operation":"metric","metric":"delivered_merchandise_value","dimension":null}'}}]})

    response = answer_retail_question('How much merchandise did delivered orders generate?', settings,
                                      httpx.MockTransport(handler))
    assert response['result'] == '13221498.11'
    assert response['plan_source'] == 'AI'
    assert len(requests) == 1


def test_retail_catalog_and_query_api_are_authenticated(tmp_path):
    settings = Settings(storage_dir=tmp_path, dev_mode=True)
    with TestClient(create_app(settings)) as client:
        response = client.get('/api/v1/assistant/retail/metrics')
        assert response.status_code == 200
        assert 'delivered_payment_value' in {item['key'] for item in response.json()['metrics']}
        answer = client.post('/api/v1/assistant/retail/query', json={
            'question': 'How many orders were delivered?'
        })
        assert answer.status_code == 200
        assert answer.json()['result'] == 96_478
        assert answer.json()['metric_name'] == 'Delivered orders'
        assert answer.json()['classification'] == 'VERIFIED'
