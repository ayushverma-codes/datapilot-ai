from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.conversation.analysis_contracts import AnalysisPlan
from app.conversation.contracts import QueryFilter
from app.models.contracts import Contract


class ConversationCreate(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator('title')
    @classmethod
    def nonblank_title(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Conversation title cannot be blank')
        return value.strip() if value is not None else value


class ConversationUpdate(Contract):
    title: str = Field(min_length=1, max_length=120)

    @field_validator('title')
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError('Conversation title cannot be blank')
        return value.strip()


class ConversationMessageRequest(Contract):
    message: str = Field(min_length=1, max_length=2000)
    file_id: str | None = Field(default=None, min_length=1, max_length=64)
    analytics_domain: Literal['generic_csv', 'olist_retail'] | None = None

    @model_validator(mode='after')
    def validate_selection(self):
        if self.analytics_domain == 'generic_csv' and not self.file_id:
            return self
        if self.file_id and self.analytics_domain == 'olist_retail':
            raise ValueError('Retail analytics does not accept a file_id')
        return self


class TimeContext(Contract):
    year: int | None = Field(default=None, ge=2000, le=2100)
    period: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')
    prior_periods: list[str] = Field(default_factory=list, max_length=2)
    comparison_period: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}$')


class RankingContext(Contract):
    n: int = Field(ge=1, le=50)
    direction: Literal['top', 'bottom'] = 'top'


class GenericQueryContext(Contract):
    operation: Literal['row_count', 'sum', 'average', 'min', 'max', 'count_distinct']
    column: str | None = Field(default=None, max_length=512)
    group_by: str | None = Field(default=None, max_length=512)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=20)


class VerifiedAnalysis(Contract):
    source: dict[str, Any]
    relationship_id: str | None = None
    plan: AnalysisPlan
    result: dict[str, Any]
    trace_id: str


class AnalyticalContext(Contract):
    active_file_id: str | None = Field(default=None, max_length=64)
    analysis: VerifiedAnalysis | None = None
    analytics_domain: Literal['generic_csv', 'olist_retail'] | None = None
    metric: dict[str, str] | None = None
    aggregation_operation: str | None = Field(default=None, max_length=40)
    time_context: TimeContext = Field(default_factory=TimeContext)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=20)
    grouping: str | None = Field(default=None, max_length=512)
    ranking: RankingContext | None = None
    comparison: dict[str, Any] | None = None
    generic_query: GenericQueryContext | None = None
    last_query_type: str | None = Field(default=None, max_length=40)
    last_successful_query_id: str | None = Field(default=None, max_length=64)
    result_reference: dict[str, Any] | None = None


class ConversationMessageView(Contract):
    message_id: str
    role: Literal['user', 'assistant', 'system']
    content: str
    created_at: str
    message_type: str
    structured_context: dict[str, Any] = Field(default_factory=dict)
    query_result_reference: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

