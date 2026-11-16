import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.retail import (
    INVALID_PURCHASE_MONTH,
    UNKNOWN_PURCHASE_MONTH,
    main,
    summarize_olist,
)


def write_csv(directory, name, headers, records):
    with (directory / name).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(records)


def make_dataset(directory, translations=None):
    write_csv(directory, 'olist_orders_dataset.csv', [
        'order_id', 'order_status', 'order_purchase_timestamp',
        'order_delivered_customer_date', 'order_estimated_delivery_date',
    ], [
        {'order_id': 'o1', 'order_status': 'delivered', 'order_purchase_timestamp': '2020-02-29T23:59:59',
         'order_delivered_customer_date': '', 'order_estimated_delivery_date': ''},
        {'order_id': 'o2', 'order_status': 'delivered', 'order_purchase_timestamp': '2020-03-01T00:00:00',
         'order_delivered_customer_date': '', 'order_estimated_delivery_date': ''},
        {'order_id': 'o3', 'order_status': 'delivered', 'order_purchase_timestamp': '',
         'order_delivered_customer_date': '', 'order_estimated_delivery_date': ''},
        {'order_id': 'o4', 'order_status': 'delivered', 'order_purchase_timestamp': 'not-a-date',
         'order_delivered_customer_date': '', 'order_estimated_delivery_date': ''},
    ])
    write_csv(directory, 'olist_products_dataset.csv', ['product_id', 'product_category_name'], [
        {'product_id': 'p1', 'product_category_name': 'translated_category'},
        {'product_id': 'p2', 'product_category_name': 'known_without_translation'},
        {'product_id': 'p3', 'product_category_name': ''},
        {'product_id': 'p4', 'product_category_name': 'previously_unseen_category'},
    ])
    write_csv(directory, 'product_category_name_translation.csv',
              ['product_category_name', 'product_category_name_english'],
              translations if translations is not None else [
                  {'product_category_name': 'translated_category',
                   'product_category_name_english': 'translated category'},
                  {'product_category_name': 'known_without_translation',
                   'product_category_name_english': ''},
              ])
    write_csv(directory, 'olist_order_items_dataset.csv',
              ['order_id', 'product_id', 'price', 'freight_value'], [
                  {'order_id': 'o1', 'product_id': 'p1', 'price': '10.00', 'freight_value': '1.00'},
                  {'order_id': 'o2', 'product_id': 'p2', 'price': '20.00', 'freight_value': '2.00'},
                  {'order_id': 'o3', 'product_id': 'p3', 'price': '30.00', 'freight_value': '3.00'},
                  {'order_id': 'o4', 'product_id': 'p4', 'price': '40.00', 'freight_value': '4.00'},
              ])
    write_csv(directory, 'olist_order_payments_dataset.csv', ['order_id', 'payment_value'], [])


def make_custom_dataset(directory, orders=(), products=(), translations=(), items=(), payments=()):
    write_csv(directory, 'olist_orders_dataset.csv', [
        'order_id', 'order_status', 'order_purchase_timestamp',
        'order_delivered_customer_date', 'order_estimated_delivery_date',
    ], orders)
    write_csv(directory, 'olist_products_dataset.csv', ['product_id', 'product_category_name'], products)
    write_csv(directory, 'product_category_name_translation.csv',
              ['product_category_name', 'product_category_name_english'], translations)
    write_csv(directory, 'olist_order_items_dataset.csv', [
        'order_id', 'order_item_id', 'product_id', 'price', 'freight_value',
    ], items)
    write_csv(directory, 'olist_order_payments_dataset.csv', [
        'order_id', 'payment_sequential', 'payment_type', 'payment_value',
    ], payments)


def order(order_id, status='delivered', purchase='2024-01-15T10:00:00', actual='', estimated=''):
    return {
        'order_id': order_id,
        'order_status': status,
        'order_purchase_timestamp': purchase,
        'order_delivered_customer_date': actual,
        'order_estimated_delivery_date': estimated,
    }


def item(order_id, item_id, product_id='p1', price='1.00', freight='0.10'):
    return {
        'order_id': order_id,
        'order_item_id': str(item_id),
        'product_id': product_id,
        'price': price,
        'freight_value': freight,
    }


def payment(order_id, sequence, payment_type, value):
    return {
        'order_id': order_id,
        'payment_sequential': str(sequence),
        'payment_type': payment_type,
        'payment_value': value,
    }


def test_category_statuses_retain_original_categories_and_values(tmp_path):
    make_dataset(tmp_path)
    report = summarize_olist(tmp_path)
    categories = {row['source_category']: row for row in report['categories']}

    assert categories['translated_category']['mapping_status'] == 'TRANSLATED'
    assert categories['translated_category']['category_english'] == 'translated category'
    assert categories['known_without_translation']['mapping_status'] == 'UNTRANSLATED'
    assert categories['previously_unseen_category']['mapping_status'] == 'UNTRANSLATED'
    assert categories[None]['mapping_status'] == 'MISSING_SOURCE_CATEGORY'
    assert categories[None]['merchandise_value'] == '30.00'
    assert categories[None]['item_count'] == 1
    assert sum(Decimal(row['merchandise_value']) for row in report['categories']) == Decimal('100.00')
    assert report['category_mapping_completeness'] == {
        'product_count': 4,
        'translated_product_count': 1,
        'untranslated_product_count': 2,
        'missing_source_category_product_count': 1,
        'source_categories': 3,
    }


def test_missing_and_invalid_timestamps_have_reconcilable_buckets(tmp_path):
    make_dataset(tmp_path)
    report = summarize_olist(tmp_path)
    monthly = {row['purchase_month']: row for row in report['monthly']}

    assert monthly['2020-02']['delivered_orders'] == 1
    assert monthly['2020-03']['delivered_orders'] == 1
    assert monthly[UNKNOWN_PURCHASE_MONTH]['delivered_orders'] == 1
    assert monthly[INVALID_PURCHASE_MONTH]['delivered_orders'] == 1
    assert monthly[UNKNOWN_PURCHASE_MONTH]['merchandise_value'] == '30.00'
    assert monthly[INVALID_PURCHASE_MONTH]['merchandise_value'] == '40.00'
    assert report['purchase_timestamp_exceptions'] == {
        'missing_delivered_orders': 1,
        'invalid_delivered_orders': 1,
    }
    assert sum(row['delivered_orders'] for row in monthly.values()) == 4
    assert sum(Decimal(row['merchandise_value']) for row in monthly.values()) == Decimal('100.00')


def test_duplicate_translation_entries_are_rejected(tmp_path):
    make_dataset(tmp_path, [
        {'product_category_name': 'translated_category', 'product_category_name_english': 'first'},
        {'product_category_name': 'translated_category', 'product_category_name_english': 'second'},
    ])
    with pytest.raises(ValueError, match='Expected nonblank unique values'):
        summarize_olist(tmp_path)


def test_all_statuses_unknown_and_missing_status_are_counted(tmp_path):
    statuses = ['approved', 'canceled', 'created', 'delivered', 'invoiced',
                'processing', 'shipped', 'unavailable', 'future_status', '']
    make_custom_dataset(tmp_path, orders=[order(f'o{i}', status) for i, status in enumerate(statuses)])

    report = summarize_olist(tmp_path)

    assert report['order_count_by_status'] == {
        '': 1, 'approved': 1, 'canceled': 1, 'created': 1, 'delivered': 1,
        'future_status': 1, 'invoiced': 1, 'processing': 1, 'shipped': 1, 'unavailable': 1,
    }
    assert sum(report['order_count_by_status'].values()) == 10
    assert report['order_count_by_status']['delivered'] == 1


def test_duplicate_order_ids_are_rejected(tmp_path):
    make_custom_dataset(tmp_path, orders=[order('same'), order('same', 'canceled')])

    with pytest.raises(ValueError, match='Expected nonblank unique values for order_id'):
        summarize_olist(tmp_path)


def test_empty_dataset_has_zero_totals_and_empty_aggregates(tmp_path):
    make_custom_dataset(tmp_path)

    report = summarize_olist(tmp_path)

    assert report['order_count_by_status'] == {}
    assert report['delivered_merchandise_value'] == '0'
    assert report['delivered_freight_value'] == '0'
    assert report['delivered_payment_value'] == '0'
    assert report['delivery'] == {'on_time': 0, 'measured': 0, 'missing_dates': 0}
    assert report['monthly'] == []
    assert report['categories'] == []
    assert report['purchase_timestamp_exceptions'] == {
        'missing_delivered_orders': 0, 'invalid_delivered_orders': 0,
    }
    assert report['category_mapping_completeness']['product_count'] == 0


def test_financial_totals_sum_items_and_payments_independently(tmp_path):
    orders = [order('split'), order('no_payment'), order('cancelled', 'canceled'), order('shipped', 'shipped')]
    items = [
        item('split', 1, price='10.10', freight='1.05'),
        item('split', 2, price='0.20', freight='0.02'),
        item('no_payment', 1, price='3.33', freight='0.67'),
        item('cancelled', 1, price='100.00', freight='5.00'),
        item('shipped', 1, price='50.00', freight='2.00'),
    ]
    payments = [
        payment('split', 1, 'credit_card', '5.15'),
        payment('split', 2, 'voucher', '2.00'),
        payment('split', 3, 'boleto', '1.00'),
        payment('cancelled', 1, 'credit_card', '105.00'),
        payment('shipped', 1, 'boleto', '52.00'),
    ]
    make_custom_dataset(tmp_path, orders=orders, items=items, payments=payments)

    report = summarize_olist(tmp_path)

    assert report['delivered_merchandise_value'] == '13.63'
    assert report['delivered_freight_value'] == '1.74'
    assert report['delivered_payment_value'] == '8.15'
    assert Decimal(report['delivered_payment_value']) - Decimal(report['delivered_merchandise_value']) - Decimal(
        report['delivered_freight_value']
    ) == Decimal('-7.22')
    assert report['order_count_by_status']['delivered'] == 2
    assert report['order_count_by_status']['canceled'] == 1
    assert report['order_count_by_status']['shipped'] == 1
    assert report['monthly'][0]['delivered_orders'] == 2  # Two items in 'split' still count as one order.


def test_duplicate_item_and_payment_keys_are_row_summed_without_join_multiplication(tmp_path):
    # Duplicate keys stay distinct source rows; aggregation scans item and payment files independently.
    make_custom_dataset(
        tmp_path,
        orders=[order('o')],
        items=[item('o', 1, price='2.25'), item('o', 1, price='2.25')],
        payments=[payment('o', 1, 'credit_card', '1.00'), payment('o', 1, 'voucher', '0.50')],
    )

    report = summarize_olist(tmp_path)

    assert report['delivered_merchandise_value'] == '4.50'
    assert report['delivered_freight_value'] == '0.20'
    assert report['delivered_payment_value'] == '1.50'


def test_missing_order_and_product_references_are_counted_and_excluded_or_uncategorized(tmp_path):
    make_custom_dataset(
        tmp_path,
        orders=[order('o')],
        items=[item('o', 1, product_id='absent', price='7.00'), item('missing_order', 1, price='50.00')],
        payments=[payment('missing_order', 1, 'voucher', '50.00')],
    )

    report = summarize_olist(tmp_path)

    assert report['unmatched_rows'] == {
        'item_order': 1,
        'delivered_item_product': 1,
        'payment_order': 1,
    }
    assert report['delivered_merchandise_value'] == '7.00'
    assert report['delivered_freight_value'] == '0.10'
    assert report['delivered_payment_value'] == '0'
    assert report['categories'][0]['source_category'] is None
    assert report['categories'][0]['merchandise_value'] == '7.00'


def test_delivery_calendar_comparison_missing_dates_and_boundary(tmp_path):
    orders = [
        order('early', actual='2024-01-02T23:59:59', estimated='2024-01-03T00:00:00'),
        order('same_calendar_day', actual='2024-01-03T23:59:59', estimated='2024-01-03T00:00:00'),
        order('late', actual='2024-01-04T00:00:00', estimated='2024-01-03T23:59:59'),
        order('missing_actual', actual='', estimated='2024-01-03T00:00:00'),
        order('missing_estimated', actual='2024-01-03T00:00:00', estimated=''),
    ]
    make_custom_dataset(tmp_path, orders=orders)

    report = summarize_olist(tmp_path)

    assert report['delivery'] == {'on_time': 2, 'measured': 3, 'missing_dates': 2}


@pytest.mark.parametrize('actual,estimated', [
    ('', '2024-01-03T00:00:00'),
    ('2024-01-03T00:00:00', ''),
])
def test_each_missing_delivery_date_is_unmeasured(tmp_path, actual, estimated):
    make_custom_dataset(tmp_path, orders=[order('missing_date', actual=actual, estimated=estimated)])

    assert summarize_olist(tmp_path)['delivery'] == {'on_time': 0, 'measured': 0, 'missing_dates': 1}


@pytest.mark.parametrize('actual,estimated', [
    ('not-a-date', '2024-01-03T00:00:00'),
    ('2024-01-03T00:00:00', '2024-02-30T00:00:00'),
])
def test_invalid_delivery_dates_fail_explicitly(tmp_path, actual, estimated):
    make_custom_dataset(tmp_path, orders=[order('bad_date', actual=actual, estimated=estimated)])

    with pytest.raises(ValueError):
        summarize_olist(tmp_path)


def test_json_output_decimal_precision_stable_ordering_and_repeatability(tmp_path, monkeypatch):
    make_custom_dataset(
        tmp_path,
        orders=[order('later', purchase='2024-02-01T00:00:00'), order('earlier')],
        products=[{'product_id': 'p2', 'product_category_name': 'zeta'},
                  {'product_id': 'p1', 'product_category_name': 'alpha'}],
        items=[item('later', 1, 'p2', '0.10', '0.01'), item('earlier', 1, 'p1', '0.20', '0.02')],
    )
    first = summarize_olist(tmp_path)
    second = summarize_olist(tmp_path)

    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert first['delivered_merchandise_value'] == '0.30'
    assert first['delivered_freight_value'] == '0.03'
    assert list(first['order_count_by_status']) == sorted(first['order_count_by_status'])
    assert [row['purchase_month'] for row in first['monthly']] == ['2024-01', '2024-02']
    assert [row['source_category'] for row in first['categories']] == ['alpha', 'zeta']

    output = tmp_path.parent / 'retail-report.json'
    monkeypatch.setattr('sys.argv', ['retail', '--input-dir', str(tmp_path), '--output', str(output)])
    main()
    assert json.loads(output.read_text(encoding='utf-8')) == first


def test_malformed_and_missing_columns_fail_with_clear_errors(tmp_path):
    make_custom_dataset(tmp_path, orders=[order('o')])
    (tmp_path / 'olist_orders_dataset.csv').write_text('order_id,order_status\no,delivered\n', encoding='utf-8')
    with pytest.raises(ValueError, match='missing columns'):
        summarize_olist(tmp_path)

    (tmp_path / 'olist_orders_dataset.csv').write_text(
        'order_id,order_status,order_purchase_timestamp,order_delivered_customer_date,order_estimated_delivery_date\n'
        '"o,delivered,2024-01-01,,,\n', encoding='utf-8'
    )
    with pytest.raises(csv.Error):
        summarize_olist(tmp_path)


def test_real_cleaned_olist_regression():
    root = Path(__file__).resolve().parents[1]
    directory = root / 'evaluation_output' / 'olist' / 'handoff' / 'cleaned'
    assert directory.is_dir(), 'release must include the cleaned Olist handoff used for real-data regression'

    report = summarize_olist(directory)

    assert sum(report['order_count_by_status'].values()) == 99_441
    assert report['order_count_by_status']['delivered'] == 96_478
    assert report['delivered_merchandise_value'] == '13221498.11'
    assert report['delivered_freight_value'] == '2198275.64'
    assert report['delivered_payment_value'] == '15422461.77'
    assert report['delivery'] == {'on_time': 89_936, 'measured': 96_470, 'missing_dates': 8}
    assert sum(Decimal(row['merchandise_value']) for row in report['monthly']) == Decimal('13221498.11')
    assert sum(Decimal(row['merchandise_value']) for row in report['categories']) == Decimal('13221498.11')
    assert report['category_mapping_completeness'] == {
        'product_count': 32_951,
        'translated_product_count': 32_328,
        'untranslated_product_count': 13,
        'missing_source_category_product_count': 610,
        'source_categories': 73,
    }
    assert report['purchase_timestamp_exceptions'] == {
        'missing_delivered_orders': 0, 'invalid_delivered_orders': 0,
    }
