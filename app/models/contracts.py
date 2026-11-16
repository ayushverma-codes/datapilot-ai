from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Issue(Contract):
    id: str
    run_id: str
    file_id: str
    column: str | None = None
    category: str
    description: str
    severity: Literal["info", "warning", "error"] = "warning"
    affected_count: int
    affected_percentage: float
    evidence: list[Any] = Field(default_factory=list)
    source: str = "CODE"
    recommended_action: str = "review"
    status: str = "DETECTED"


class Step(Contract):
    id: str
    file_id: str
    issue_ids: list[str] = Field(default_factory=list)
    operation: str
    columns: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str
    source: Literal["CODE", "AI"] = "CODE"
    risk: Literal["low", "medium", "high"]
    approval_required: bool
    expected_impact: str
    status: str


class Decision(Contract):
    reason: str = Field(default="", max_length=2000)


class BatchDecision(Decision):
    step_ids: list[str] = Field(min_length=1, max_length=1000)


class Suggestion(Contract):
    column: str
    operation: Literal["category_normalization", "convert_numeric", "parse_dates", "fill_missing", "normalize_strings"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(max_length=2000)
    confidence: float = Field(ge=0, le=1)
    risk: Literal["low", "medium", "high"] = "medium"


class AIResponse(Contract):
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=30)


class PipelineError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}
