import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.quality.rule_engine import detect


def test_quality_evidence(service, ingest):
    data = b'code,mixed,when,category,empty,constant\n001,10,01/02/2025,Blue,,same\n001,20,2025-02-30,blue,,same\n003,oops,2025-01-20, Blue ,,same\n004,30,2025/01/21,Bluue,,same\n'
    file_id = ingest(data)
    categories = {i['category'] for i in service.get_issues('alice', file_id)}
    assert {'duplicate_identifiers', 'mixed_types', 'invalid_numeric', 'ambiguous_dates', 'invalid_dates', 'inconsistent_date_formats', 'whitespace', 'case_inconsistency', 'empty_column', 'constant_column', 'missing_values'} <= categories
    profile = service.get_profile('alice', file_id)
    assert profile['columns'][0]['inference']['potential_identifier']
    assert profile['columns'][0]['inference']['proposed_target_type'] == 'string'
    assert profile['columns'][2]['date']['invalid_count'] == 1
    assert profile['columns'][2]['date']['ambiguous_count'] == 1
    issues = {item['category']: item for item in service.get_issues('alice', file_id)}
    assert issues['invalid_numeric']['evidence'] == ['oops']
    mixed_type_evidence = {item['column']: item['evidence'][0] for item in service.get_issues('alice', file_id)
                           if item['category'] == 'mixed_types'}
    assert mixed_type_evidence == {
        'mixed': {'numeric_count': 3, 'date_count': 0, 'non_null_count': 4},
        'when': {'numeric_count': 0, 'date_count': 3, 'non_null_count': 4},
    }
    assert issues['invalid_dates']['evidence'] == ['2025-02-30']
    assert issues['ambiguous_dates']['evidence'] == ['01/02/2025']
    assert issues['duplicate_identifiers']['evidence'] == [{'value': '001', 'count': 2}]
    assert issues['whitespace']['evidence'] == [' Blue ']
    assert issues['case_inconsistency']['affected_count'] == 3
    assert issues['category_variants']['affected_count'] > 0
    assert {item['severity'] for item in issues.values()} == {'warning'}
    assert all(item['source'] == 'CODE' and item['recommended_action'] and item['status'] and item['evidence']
               for item in issues.values())


def test_outliers_not_removed(service, ingest):
    file_id = ingest(b'a\n1\n2\n2\n3\n3\n4\n10000\n')
    assert 'potential_outliers' in {i['category'] for i in service.get_issues('alice', file_id)}
    service.clean('alice', file_id)
    assert '10000' in service.download('alice', file_id).read_text()


def test_duplicate_headers_metadata(service, ingest):
    file_id = ingest(b'X,X,X__2\na,b,c\n')
    p = service.get_profile('alice', file_id)
    assert p['schema']['original_columns'] == ['X', 'X', 'X__2']
    assert len(set(p['schema']['columns'])) == 3
    assert p['schema']['duplicate_headers'] == {'X': 2}
    assert 'duplicate_headers' in {i['category'] for i in service.get_issues('alice', file_id)}


def test_profile_measurements_and_bounded_categorical_distribution(service, ingest):
    labels = [f'{i:04d}-' + 'x' * 220 for i in range(120)]
    body = '\n'.join(f'{[1, 2, 2, 4][i % 4]},{label}' for i, label in enumerate(labels))
    data = f'magnitude,label\n{body}\n'.encode()
    profile = service.get_profile('alice', ingest(data))
    assert (profile['row_count'], profile['column_count'], profile['file_size']) == (120, 2, len(data))
    assert profile['memory_bytes'] > 0
    assert profile['schema']['columns'] == ['magnitude', 'label']
    assert profile['parsing_issues'] == []
    measure, category = profile['columns']
    assert measure['inference']['physical_type'] == 'numeric'
    stats = measure['numeric']
    assert (stats['min'], stats['max'], stats['mean'], stats['median']) == (1, 4, 2.25, 2)
    assert stats['std'] == pytest.approx(1.0897247359)
    assert stats['quantiles'] == {'0.05': 1, '0.25': 1.75, '0.5': 2, '0.75': 2.5, '0.95': 4}
    assert category['unique_count'] == category['rare_category_count'] == 120
    assert category['cardinality_ratio'] == 1
    assert len(category['sample_values']) == 5
    assert len(category['top_values']) == len(category['rare_categories']) == 10
    assert all(len(item['value']) <= 200 and item['count'] == 1 for item in category['top_values'])


def test_structural_issue_identity_evidence_and_empty_dataset(service, ingest):
    file_id = ingest(b'a,a,b,b,c\n1,1,z,z,\n1,1,z,z,\n,,,,\n')
    profile = service.get_profile('alice', file_id)
    found = detect(profile, profile['run_id'], file_id)
    assert [issue.id for issue in found] == [issue.id for issue in detect(profile, profile['run_id'], file_id)]
    assert len({issue.id for issue in found}) == len(found)
    assert all(issue.evidence and issue.run_id == profile['run_id'] and issue.file_id == file_id for issue in found)
    by_category = {i.category: i for i in found}
    assert by_category['duplicate_rows'].evidence == [{'record_numbers': [2]}]
    assert by_category['empty_rows'].evidence == [{'record_numbers': [3]}]
    assert by_category['duplicate_rows'].affected_percentage == pytest.approx(100 / 3)
    assert {i.column for i in found if i.category == 'duplicate_headers'} == {'a', 'b'}
    missing = next(i for i in found if i.category == 'missing_values' and i.column == 'c')
    assert missing.affected_count == profile['row_count'] == 3
    assert missing.evidence == [{'record_numbers': [1, 2, 3]}]
    assert profile['missing_cells'] == 7
    empty = service.get_profile('alice', ingest(b'alpha,beta\n'))
    assert empty['row_count'] == empty['duplicate_rows'] == empty['empty_rows'] == 0
    assert all(c['inference']['physical_type'] == 'empty' and c['null_percentage'] == 0 for c in empty['columns'])


def test_date_ranges_offsets_and_consistent_ambiguous_formats(service, ingest):
    data = b'calendar,instant\n01/02/2024,2024-01-01T10:00:00+02:00\n02/03/2024,2024-01-01T09:00:00+00:00\n'
    file_id = ingest(data)
    calendar, instant = service.get_profile('alice', file_id)['columns']
    assert calendar['date']['valid_count'] == calendar['date']['ambiguous_count'] == 2
    assert calendar['date']['formats'] == {'%d/%m/%Y': 2, '%m/%d/%Y': 2}
    assert calendar['date']['min'] == '2024-01-02T00:00:00'
    assert calendar['date']['max'] == '2024-03-02T00:00:00'
    assert calendar['date']['format_outlier_count'] == 0
    assert instant['date']['min'] == '2024-01-01T10:00:00+02:00'
    assert instant['date']['max'] == '2024-01-01T09:00:00+00:00'
    assert all(issue['category'] != 'inconsistent_date_formats' for issue in service.get_issues('alice', file_id))


def test_whitespace_alone_is_not_a_capitalization_issue(service, ingest):
    file_id = ingest(b'label\nblue\n blue \n')
    categories = {issue['category'] for issue in service.get_issues('alice', file_id)}
    assert 'whitespace' in categories and 'case_inconsistency' not in categories


def test_profile_versions_describe_original_and_cleaned_artifacts(service, ingest):
    raw = 'key;key;label\nfoo;bar;café\nfoo;bar;café\n'.encode('cp1252')
    file_id = ingest(raw)
    original = service.get_profile('alice', file_id)
    assert service.clean('alice', file_id)['passed']
    cleaned = service.get_profile('alice', file_id, 'cleaned')
    assert original['revision'] == 0 and cleaned['revision'] == 1
    assert original['kind'] == 'original' and cleaned['kind'] == 'cleaned'
    assert original['file_id'] == cleaned['file_id'] == file_id
    assert original['run_id'] == cleaned['run_id']
    assert original['profile_id'] != cleaned['profile_id']
    assert original['duplicate_rows'] == 1 and cleaned['duplicate_rows'] == 0
    assert original['schema']['delimiter'] == ';' and cleaned['schema']['delimiter'] == ','
    assert original['schema']['encoding'] == 'cp1252' and cleaned['schema']['encoding'] == 'utf-8-sig'
    assert original['schema']['duplicate_headers'] == {'key': 2}
    assert not cleaned['schema']['duplicate_headers']
    proposal = service.propose('alice', file_id, 'normalize_strings', ['label'], {'mode': 'upper'}, 'review')
    service.decide('alice', [proposal['id']], 'APPROVED')
    assert service.clean('alice', file_id)['passed']
    latest = service.get_profile('alice', file_id, 'cleaned')
    assert latest['revision'] == 2 and latest['profile_id'] != cleaned['profile_id']
    assert service.get_profile('alice', file_id) == original
    artifacts = service.settings.storage_dir.rglob('*.json')
    reports = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in artifacts if p.parent.name == 'profiles'}
    assert reports == {p['profile_id']: p for p in (original, cleaned, latest)}
    assert next(service.settings.storage_dir.rglob(f'raw/{file_id}.csv')).read_bytes() == raw


def test_malformed_record_issue_has_stable_identity_and_trace(service):
    run = service.upload('alice', [('bad.csv', b'a,b\nx,y,z\n')])
    file_id = run['files'][0]['file_id']
    issue, = service.get_issues('alice', file_id)
    assert issue['id'] == uuid5(NAMESPACE_URL, f'{file_id}:None:malformed_records').hex
    assert issue['severity'] == 'error' and issue['affected_count'] == 1
    assert issue['evidence'] == [{'line': 2, 'expected': 2, 'actual': 3}]
