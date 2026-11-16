from typing import Literal

from pydantic import Field, model_validator

from app.models.contracts import Contract


class RetailQuestion(Contract):
    question: str = Field(min_length=1, max_length=2000)


class RetailPlan(Contract):
    operation: Literal['metric', 'uncategorized', 'distribution', 'top_k', 'bottom_k', 'rank', 'contribution',
                       'compare_periods', 'mom_change', 'explain_reconciliation']
    metric: str = Field(min_length=1, max_length=100)
    dimension: Literal['category', 'month', 'status', 'mapping_status'] | None = None
    n: int | None = Field(default=None, ge=1, le=20)
    period_a: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')
    period_b: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')

    @model_validator(mode='after')
    def validate_periods(self):
        if self.operation == 'compare_periods' and (not self.period_a or not self.period_b):
            raise ValueError('Period comparisons require two YYYY-MM periods')
        return self
