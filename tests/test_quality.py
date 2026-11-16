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
