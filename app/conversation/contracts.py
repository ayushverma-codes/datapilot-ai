from typing import Literal

from pydantic import Field, model_validator

from app.models.contracts import Contract


class QueryFilter(Contract):
    column: str = Field(min_length=1, max_length=512)
    operator: Literal['eq', 'ne', 'contains', 'gt', 'gte', 'lt', 'lte', 'is_null', 'is_not_null']
    value: str | None = Field(default=None, max_length=1000)

    @model_validator(mode='after')
    def require_comparison_value(self):
        if self.operator in {'is_null', 'is_not_null'} and self.value is not None:
            raise ValueError('Null filters do not accept a value')
        if self.operator not in {'is_null', 'is_not_null'} and self.value is None:
            raise ValueError('Comparison filters require a value')
        return self


class QueryPlan(Contract):
    operation: Literal['row_count', 'sum', 'average', 'min', 'max', 'count_distinct']
    column: str | None = Field(default=None, max_length=512)
    group_by: str | None = Field(default=None, max_length=512)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=20)


class AssistantQuestion(Contract):
    file_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=2000)
