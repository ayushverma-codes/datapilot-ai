import pytest
from app.profiling.type_inference import date_candidates


@pytest.mark.parametrize('value,expected', [
    ('2025-01-20', [('2025-01-20T00:00:00', '%Y-%m-%d')]),
    ('2025-01-20 14:30:01', [('2025-01-20T14:30:01', 'ISO8601')]),
    ('2025-01-20T14:30:01+05:30', [('2025-01-20T14:30:01+05:30', 'ISO8601')]),
    ('2025-02-30', []),
    ('2025-01-20 99:00:00', []),
    ('2025/01/20', [('2025-01-20T00:00:00', '%Y/%m/%d')]),
    ('01/02/2025', [('2025-02-01T00:00:00', '%d/%m/%Y'), ('2025-01-02T00:00:00', '%m/%d/%Y')]),
])
def test_iso_fast_path_preserves_interpretation(value, expected):
    assert date_candidates(value) == expected
