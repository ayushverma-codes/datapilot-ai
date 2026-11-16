"""Retail analytics for a directory of validated Olist CSV exports."""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.storage.file_storage import FileStorage

UNKNOWN_PURCHASE_MONTH = 'UNKNOWN_PURCHASE_TIMESTAMP'
INVALID_PURCHASE_MONTH = 'INVALID_PURCHASE_TIMESTAMP'


def rows(directory, table, required):
    filename = ('product_category_name_translation.csv' if table == 'translations'
                else f'olist_{table}_dataset.csv')
    with (directory / filename).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{filename} is missing columns: {", ".join(sorted(missing))}')
        for row in reader:
            if None in row or any(row[key] is None for key in required):
                raise ValueError(f'{filename}: malformed record ending at line {reader.line_num}')
            yield row


def index(records, key):
    result = {}
    for record in records:
        if not record[key] or record[key] in result:
            raise ValueError(f'Expected nonblank unique values for {key}')
        result[record[key]] = record
    return result


def purchase_month(timestamp):
    """Return a calendar-month key without coercing missing or invalid dates."""
    if not timestamp or not timestamp.strip():
        return UNKNOWN_PURCHASE_MONTH
    try:
        return datetime.fromisoformat(timestamp).date().isoformat()[:7]
    except ValueError:
        return INVALID_PURCHASE_MONTH


def category_mapping_status(source_category, translations):
    if not source_category:
        return 'MISSING_SOURCE_CATEGORY'
    translation = translations.get(source_category, {}).get('product_category_name_english')
    return 'TRANSLATED' if translation else 'UNTRANSLATED'


def summarize_olist(directory: Path):
    orders = index(rows(directory, 'orders', ('order_id', 'order_status', 'order_purchase_timestamp',
                                            'order_delivered_customer_date', 'order_estimated_delivery_date')), 'order_id')
    products = index(rows(directory, 'products', ('product_id', 'product_category_name')), 'product_id')
    translations = index(rows(directory, 'translations', ('product_category_name', 'product_category_name_english')),
                         'product_category_name')
    delivered = {key for key, row in orders.items() if row['order_status'] == 'delivered'}
    statuses = Counter(row['order_status'] for row in orders.values())
    monthly_orders = Counter()
    order_months = {}
    timestamp_order_counts = Counter()
    on_time = measured = 0
    for key in delivered:
        row = orders[key]
        month = purchase_month(row['order_purchase_timestamp'])
        order_months[key] = month
        monthly_orders[month] += 1
        if month in (UNKNOWN_PURCHASE_MONTH, INVALID_PURCHASE_MONTH):
            timestamp_order_counts[month] += 1
        actual, estimated = row['order_delivered_customer_date'], row['order_estimated_delivery_date']
        if actual and estimated:
            measured += 1
            on_time += datetime.fromisoformat(actual).date() <= datetime.fromisoformat(estimated).date()

    merchandise = defaultdict(Decimal)
    freight = Decimal(0)
    categories = defaultdict(Decimal)
    category_item_counts = Counter()
    unmatched = Counter()
    for row in rows(directory, 'order_items', ('order_id', 'product_id', 'price', 'freight_value')):
        if row['order_id'] not in orders:
            unmatched['item_order'] += 1
            continue
        if row['order_id'] not in delivered:
            continue
        price = Decimal(row['price'])
        month = order_months[row['order_id']]
        merchandise[month] += price
        freight += Decimal(row['freight_value'])
        product = products.get(row['product_id'])
        if product is None:
            unmatched['delivered_item_product'] += 1
        category = product['product_category_name'] if product else ''
        categories[category or None] += price
        category_item_counts[category or None] += 1

    payments = Decimal(0)
    for row in rows(directory, 'order_payments', ('order_id', 'payment_value')):
        if row['order_id'] not in orders:
            unmatched['payment_order'] += 1
        elif row['order_id'] in delivered:
            payments += Decimal(row['payment_value'])

    product_mapping_counts = Counter(
        category_mapping_status(product['product_category_name'], translations)
        for product in products.values()
    )
    monthly_rows = [{'purchase_month': month, 'delivered_orders': count,
                     'merchandise_value': str(merchandise[month])}
                    for month, count in sorted(monthly_orders.items())]
    category_rows = []
    for category, value in sorted(categories.items(), key=lambda item: (-item[1], item[0] or '')):
        category_rows.append({
            'source_category': category,
            'category_english': translations.get(category, {}).get('product_category_name_english'),
            'merchandise_value': str(value),
            'mapping_status': category_mapping_status(category, translations),
            'item_count': category_item_counts[category],
        })

    return {
        'order_count_by_status': dict(sorted(statuses.items())),
        'delivered_merchandise_value': str(sum(merchandise.values(), Decimal(0))),
        'delivered_freight_value': str(freight),
        'delivered_payment_value': str(payments),
        'delivery': {'on_time': on_time, 'measured': measured, 'missing_dates': len(delivered) - measured},
        'monthly': monthly_rows,
        'purchase_timestamp_exceptions': {
            'missing_delivered_orders': timestamp_order_counts[UNKNOWN_PURCHASE_MONTH],
            'invalid_delivered_orders': timestamp_order_counts[INVALID_PURCHASE_MONTH],
        },
        'categories': category_rows,
        'category_mapping_completeness': {
            'product_count': len(products),
            'translated_product_count': product_mapping_counts['TRANSLATED'],
            'untranslated_product_count': product_mapping_counts['UNTRANSLATED'],
            'missing_source_category_product_count': product_mapping_counts['MISSING_SOURCE_CATEGORY'],
            'source_categories': len({p['product_category_name'] for p in products.values()
                                      if p['product_category_name']}),
        },
        'unmatched_rows': {key: unmatched[key] for key in ('item_order', 'delivered_item_product', 'payment_order')},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.suffix.lower() != '.json' or output.is_relative_to(args.input_dir.resolve()):
        parser.error('Output must be a JSON path outside the input directory')
    FileStorage(output.parent).json(output, summarize_olist(args.input_dir))
    print(json.dumps({'output': str(output)}))


if __name__ == '__main__':
    main()
