from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AIExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=80)
    input: str = Field(min_length=1, max_length=100_000)
    instructions: Optional[str] = Field(default=None, max_length=20_000)
    required_capabilities: list[str] = Field(default_factory=lambda: ["text"], max_length=10)
    output_format: Literal["text", "json"] = "text"
    output_schema: Optional[dict[str, Any]] = Field(default=None, max_length=100)
    preferred_provider: Optional[str] = Field(default=None, max_length=50)
    preferred_model: Optional[str] = Field(default=None, max_length=150)
    project_id: Optional[UUID] = None
    workspace_id: Optional[UUID] = None
    feature: str = Field(min_length=1, max_length=100)
    request_id: UUID = Field(default_factory=uuid4)
    idempotency_key: Optional[str] = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=30)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=180)

    @field_validator("required_capabilities")
    @classmethod
    def normalize_capabilities(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip().lower() for value in values))
        if not normalized or any(not value or len(value) > 50 for value in normalized):
            raise ValueError("At least one valid capability is required")
        return normalized

    @model_validator(mode="after")
    def validate_output_schema(self):
        if self.output_schema is not None and self.output_format != "json":
            raise ValueError("output_schema requires output_format=json")
        return self


class AIErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool


class AIExecutionResponse(BaseModel):
    execution_id: UUID
    request_id: UUID
    task: str
    success: bool
    output: Optional[str] = None
    output_format: str
    provider: Optional[str] = None
    model: Optional[str] = None
    duration_ms: Optional[int] = None
    usage: Optional[dict[str, Any]] = None
    estimated_cost_usd: Optional[float] = None
    retry_count: int = 0
    fallback_from: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: Optional[AIErrorResponse] = None
