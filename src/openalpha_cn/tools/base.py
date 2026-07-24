"""Stable research tool contracts."""

from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.time import ensure_aware


class ToolMetadata(BaseModel):
    """Non-secret tool identity and safety policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    tool_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    read_only: bool


class ToolRequest(BaseModel):
    """Point-in-time evidence lookup request."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    kind: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)


class ToolResult(BaseModel):
    """Explicit success or no-data tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    status: Literal["success", "no_data"]
    evidence_ids: tuple[str, ...] = ()
    no_data_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "success" and not self.evidence_ids:
            raise ValueError("success requires evidence_ids")
        if self.status == "no_data" and self.no_data_reason is None:
            raise ValueError("no_data requires no_data_reason")
        return self


class ResearchTool(Protocol):
    """Extension interface for bounded research tools."""

    @property
    def metadata(self) -> ToolMetadata:
        """Return tool policy metadata."""

    def execute(self, request: ToolRequest) -> ToolResult:
        """Execute a bounded, structured tool request."""
