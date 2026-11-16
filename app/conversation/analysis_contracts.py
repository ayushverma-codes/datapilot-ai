from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.conversation.contracts import QueryFilter
from app.models.contracts import Contract
from app.retail_contracts import DatasetID, FieldRef, Metric, Role


class DatasetSelection(Contract):
    file_id: DatasetID
    revision: int | None = Field(default=None, ge=1)


class AnalyticalQuestion(Contract):
    question: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    datasets: list[DatasetSelection] = Field(default_factory=list, max_length=5)
    relationship_ids: list[DatasetID] = Field(default_factory=list, max_length=3)
    conversation_id: DatasetID | None = None

    @model_validator(mode='after')
    def scope(self):
        if len({item.file_id for item in self.datasets}) != len(self.datasets):
            raise ValueError('Dataset selections must be distinct')
        if not self.datasets and not self.conversation_id:
            raise ValueError('Select datasets or an owned conversation')
        return self


class AnalysisFilter(QueryFilter):
    side: Literal['left', 'right'] = 'left'


class AnalysisSort(Contract):
    field: FieldRef | None = None
    direction: Literal['asc', 'desc'] = 'asc'


class AnalysisPlan(Contract):
    intent: Literal['inspect', 'rows', 'aggregate', 'distinct', 'statistics', 'compare', 'retail', 'clarify', 'unsupported']
    dataset: str | None = Field(default=None, pattern=r'^d[1-5]$')
    revision: int | None = Field(default=None, ge=1)
    relationship: str | None = Field(default=None, pattern=r'^j[1-3]$')
    operation: Literal['row_count', 'sum', 'average', 'min', 'max', 'count_distinct'] | None = None
    column: FieldRef | None = None
    columns: list[FieldRef] = Field(default_factory=list, max_length=12)
    group_by: FieldRef | None = None
    filters: list[AnalysisFilter] = Field(default_factory=list, max_length=10)
    cohort_a: list[AnalysisFilter] = Field(default_factory=list, max_length=5)
    cohort_b: list[AnalysisFilter] = Field(default_factory=list, max_length=5)
    sort: AnalysisSort | None = None
    limit: int = Field(default=20, ge=1, le=50)
    metrics: list[Metric] = Field(default_factory=list, max_length=9)
    fields: dict[Role, FieldRef] = Field(default_factory=dict)
    returned_values: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=20)
    non_returned_values: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def intent_fields(self):
        intent = self.intent
        if intent in ('clarify', 'unsupported'):
            if self.model_dump(exclude_defaults=True) != {'intent': intent}:
                raise ValueError('A nonexecuting intent must not include execution instructions')
            return self
        if self.dataset is None or self.revision is None:
            raise ValueError('Select a dataset alias and exact cleaned revision')
        if (intent in ('aggregate', 'compare')) != bool(self.operation):
            raise ValueError('Only aggregate and compare require an operation')
        uses_column = intent in ('distinct', 'statistics') or self.operation not in (None, 'row_count')
        if uses_column != bool(self.column):
            raise ValueError('Specify a single metric or distinct field only where required')
        if (intent == 'rows') != bool(self.columns):
            raise ValueError('Only row listing requires explicit projected columns')
        if self.group_by and intent != 'aggregate':
            raise ValueError('Only aggregates accept an explicit grouping column')
        if intent == 'compare':
            if not self.cohort_a or not self.cohort_b or self.cohort_a == self.cohort_b:
                raise ValueError('Comparisons require two different cohorts')
        elif self.cohort_a or self.cohort_b:
            raise ValueError('Only comparisons support cohorts')
        if self.sort:
            if intent == 'rows' and self.sort.field is None:
                raise ValueError('Row sorting requires an explicit field')
            if intent != 'rows' and (self.sort.field is not None or (intent != 'distinct' and not self.group_by)):
                raise ValueError('Only rows, distinct values or grouped aggregates support sorting')
        if intent == 'retail':
            if not self.metrics or len(set(self.metrics)) != len(self.metrics):
                raise ValueError('Select distinct retail metric names')
            if set(self.returned_values) & set(self.non_returned_values):
                raise ValueError('Return status values must be disjoint')
        elif self.metrics or self.fields or self.returned_values or self.non_returned_values:
            raise ValueError('Retail metric fields require the retail intent')
        if intent == 'inspect' and (self.filters or self.relationship):
            raise ValueError('Inspection describes one complete dataset')
        return self
