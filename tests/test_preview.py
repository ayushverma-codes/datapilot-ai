import pytest
from app.models.contracts import PipelineError


def test_preview_is_read_only_and_owner_scoped(service, ingest):
    file_id = ingest(b'category,other\nBlue,a\nblue,b\n')
    service.clean('alice', file_id)
    before = service.download('alice', file_id).read_bytes()
    plan = service.get_plan('alice', file_id)
    preview = service.preview_pending('alice', file_id)
    step = preview['steps'][0]
    assert step['preview_succeeded'] and step['rows_affected'] == 1
    assert step['examples'] == [{'before': {'category': 'blue'}, 'after': {'category': 'Blue'}}]
    assert service.download('alice', file_id).read_bytes() == before
    assert service.get_plan('alice', file_id) == plan
    assert service.approval_history('alice', file_id) == []
    with pytest.raises(PipelineError) as exc:
        service.preview_pending('bob', file_id)
    assert exc.value.status == 404


def test_failed_preview_leaves_plan_pending(service, ingest):
    file_id = ingest(b'x\n1\nbad\n')
    proposed = service.propose('alice', file_id, 'convert_numeric', ['x'], {}, 'Explicit proposal')
    preview = service.preview_pending('alice', file_id)
    assert preview['steps'][0]['preview_succeeded'] is False
    assert next(s for s in service.get_plan('alice', file_id)['steps'] if s['id'] == proposed['id'])['status'] == 'AWAITING_APPROVAL'
