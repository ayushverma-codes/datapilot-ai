from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models.contracts import Contract

DatasetID = Annotated[str, Field(min_length=1, max_length=64)]
Column = Annotated[str, Field(min_length=1)]
Metric = Literal['total_revenue', 'order_count', 'average_order_value', 'units_sold', 'return_rate',
                 'inventory_availability', 'stock_out_frequency', 'category_performance', 'store_performance']
Role = Literal['revenue', 'unit_price', 'order_id', 'quantity', 'return_status', 'on_hand', 'category', 'store']


class DatasetPair(Contract):
    left_file_id: DatasetID
    right_file_id: DatasetID

    @model_validator(mode='after')
    def distinct(self):
        if self.left_file_id == self.right_file_id:
            raise ValueError('Select two distinct datasets')
        return self


class RelationshipRequest(DatasetPair):
    left_keys: list[Column] = Field(min_length=1, max_length=5)
    right_keys: list[Column] = Field(min_length=1, max_length=5)
    join_type: Literal['left', 'inner'] = 'left'
    cardinality: Literal['one_to_one', 'many_to_one', 'one_to_many']

    @model_validator(mode='after')
    def paired_keys(self):
        if (len(self.left_keys) != len(self.right_keys) or len(set(self.left_keys)) != len(self.left_keys)
                or len(set(self.right_keys)) != len(self.right_keys)):
            raise ValueError('Provide equally sized lists of distinct join columns')
        return self


class ApprovedMapping(Contract):
    file_id: DatasetID
    source_key: Column
    canonical_value: Column
    approval_reason: str = Field(min_length=1, max_length=1000, pattern=r'\S')


class NormalizationRequest(Contract):
    file_id: DatasetID
    column: Column
    entity: Literal['product', 'category']
    mapping: ApprovedMapping | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class FieldRef(Contract):
    column: Column
    side: Literal['left', 'right'] = 'left'


class RetailFilter(FieldRef):
    values: list[Annotated[str, Field(max_length=1000)] | None] = Field(min_length=1, max_length=50)


class MetricsRequest(Contract):
    file_id: DatasetID
    relationship_id: DatasetID | None = None
    metrics: list[Metric] = Field(min_length=1, max_length=9)
    fields: dict[Role, FieldRef] = Field(default_factory=dict)
    filters: list[RetailFilter] = Field(default_factory=list, max_length=10)
    returned_values: list[str] = Field(default_factory=list, max_length=20)
    non_returned_values: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def unambiguous(self):
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError('Metric names must be unique')
        if set(self.returned_values) & set(self.non_returned_values):
            raise ValueError('Return status values must be disjoint')
        return self
