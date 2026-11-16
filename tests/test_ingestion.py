import hashlib
import pytest
from app.config import Settings
from app.ingestion.csv_reader import read_csv
from app.models.contracts import PipelineError


@pytest.mark.parametrize('data,rows,columns', [
    (b'x\nvalue\n', 1, 1),
    (b'a,b,c\n1,hello,yes\n2,,no\n', 2, 3),
    ('any name,\u03b1\n001,ok\n'.encode('utf-8-sig'), 1, 2),
    ('ville;note\nParis;caf\u00e9\n'.encode('cp1252'), 1, 2),
    ('one\ttwo\nx\ty\n'.encode('utf-16'), 1, 2),
    (b'a|b\nx|y\n', 1, 2),
    (b'a,b\n"hello, there","two\nlines"\n', 1, 2),
    (b'only,header\n', 0, 2),
    (b'x,x,x__2\n1,2,3\n', 1, 3),
    (b'x,y\n,\n\n', 2, 2),
    (b'a,b\nNA,null\n', 1, 2),
])
def test_generic_inputs(service, ingest, data, rows, columns):
    file_id = ingest(data)
    p = service.get_profile('alice', file_id)
    assert (p['row_count'], p['column_count']) == (rows, columns)
    assert service.clean('alice', file_id)['passed']


@pytest.mark.parametrize('name,data,code', [
    ('empty.csv', b'', 'EMPTY_FILE'),
    ('whitespace.csv', b'\n ', 'EMPTY_FILE'),
    ('bad.csv', b'a,b\n1,2,3\n', 'MALFORMED_CSV'),
    ('short.csv', b'a,b\n1\n', 'MALFORMED_CSV'),
    ('quote.csv', b'a,b\n"unclosed,2\n', 'MALFORMED_CSV'),
    ('binary.csv', b'a\n\x00\x01\n', 'BINARY_CONTENT'),
    ('truncated-utf16.csv', b'\xff\xfeA', 'INVALID_ENCODING'),
    ('invalid-utf8-bom.csv', b'\xef\xbb\xbfx\n\xff\n', 'INVALID_ENCODING'),
    ('../escape.csv', b'a\nx\n', 'UNSAFE_FILENAME'),
    ('C:\\escape.csv', b'a\nx\n', 'UNSAFE_FILENAME'),
    ('file.txt', b'a\nx\n', 'INVALID_EXTENSION'),
    ('fake.csv', b'{"a": 1}', 'NOT_CSV'),
])
def test_invalid_independent_files(service, name, data, code):
    run = service.upload('alice', [(name, data), ('good.csv', b'arbitrary\nvalue\n')])
    assert run['status'] == 'PARTIALLY_COMPLETED'
    bad, good = run['files']
    assert bad['error']['code'] == code
    assert good['status'] != 'FAILED'
    assert service.clean('alice', good['file_id'])['passed']
    if code == 'MALFORMED_CSV':
        assert service.get_issues('alice', bad['file_id'])[0]['category'] == 'malformed_records'


def test_limits(service):
    service.settings.max_file_bytes = 3
    assert service.upload('alice', [('a.csv', b'a\n123\n')])['files'][0]['error']['code'] == 'FILE_TOO_LARGE'
    service.settings.max_file_bytes = 100
    service.settings.max_rows = 1
    assert service.upload('alice', [('a.csv', b'a\n1\n2\n')])['files'][0]['error']['code'] == 'PROCESSING_LIMIT'
    service.settings.max_columns = 1
    assert service.upload('alice', [('a.csv', b'a,b\n1,2\n')])['files'][0]['error']['code'] == 'COLUMN_LIMIT'


def test_large_bounded_file(service, ingest):
    content = ('random,measurement\n' + ''.join(f'key-{i},{i % 137}\n' for i in range(12000))).encode()
    file_id = ingest(content)
    p = service.get_profile('alice', file_id)
    assert p['row_count'] == 12000
    assert len(p['columns'][0]['sample_values']) <= 5
    assert len(p['columns'][0]['top_values']) <= 10
    assert service.clean('alice', file_id)['passed']


def test_idempotent_requests_and_raw(service):
    files = [('a.csv', b'identifier,label\n001, x \n001, x \n')]
    run = service.upload('alice', files, 'request-1')
    repeated = service.upload('alice', files, 'request-1')
    assert run['run_id'] == repeated['run_id']
    with pytest.raises(PipelineError) as exc:
        service.upload('alice', [('b.csv', b'k\nv\n')], 'request-1')
    assert exc.value.status == 409
    file_id = run['files'][0]['file_id']
    raw = next(service.settings.storage_dir.rglob(f'raw/{file_id}.csv'))
    checksum = hashlib.sha256(raw.read_bytes()).hexdigest()
    result = service.clean('alice', file_id)
    assert result['passed'] and result['idempotent']
    assert result['row_count_after'] == 1
    output = service.download('alice', file_id).read_bytes()
    assert b'001,x' in output
    assert service.clean('alice', file_id) == result
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == checksum
    assert service.download('alice', file_id).read_bytes() == output


def test_multiple_unrelated_schemas(service):
    run = service.upload('alice', [('first.csv', b'xyz\nword\n'), ('second.csv', b'1,?,!,four\n2,a,b,c\n'), ('third.csv', b'A;B\nx;y\n')])
    assert len({f['file_id'] for f in run['files']}) == 3
    assert [service.get_profile('alice', f['file_id'])['column_count'] for f in run['files']] == [1, 4, 2]
    for f in run['files']:
        assert service.clean('alice', f['file_id'])['passed']


def test_registry_replay_and_service_isolation(service):
    files = [('same.csv', b'a\nx\n'), ('same.csv', b'b,c\ny,z\n')]
    first = service.upload('alice', files, 'shared-key')
    assert service.upload('alice', files, 'shared-key', descriptions=['', '']) == first
    second = service.upload('alice', files, 'another-key')
    third = service.upload('bob', files, 'shared-key')
    assert len({run['run_id'] for run in (first, second, third)}) == 3
    assert len({file['file_id'] for run in (first, second, third) for file in run['files']}) == 6
    with pytest.raises(PipelineError) as exc:
        service.get_run('bob', first['run_id'])
    assert exc.value.status == 404
    with pytest.raises(PipelineError) as exc:
        service.get_profile('bob', first['files'][0]['file_id'])
    assert exc.value.status == 404
