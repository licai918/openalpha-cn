"""Model capability registry, bounded retry policy, and usage-accounting contract."""

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.models.base import ModelMetadata


class ModelRegistry:
    """Resolve immutable provider/model capabilities without storing secrets."""

    def __init__(self, entries: tuple[ModelMetadata, ...] = ()) -> None:
        self._entries: dict[tuple[str, str], ModelMetadata] = {}
        for entry in entries:
            self.register(entry)

    def register(self, metadata: ModelMetadata) -> None:
        key = (metadata.provider_id, metadata.model)
        if key in self._entries:
            raise ValueError(f"model already registered: {key[0]}/{key[1]}")
        self._entries[key] = metadata

    def resolve(self, provider_id: str, model: str) -> ModelMetadata:
        try:
            return self._entries[(provider_id, model)]
        except KeyError as error:
            raise KeyError(f"unknown model: {provider_id}/{model}") from error

    def list(self) -> tuple[ModelMetadata, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))


class ModelRetryPolicy(BaseModel):
    """Bounded exponential retry policy for transient transport failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=8)
    base_delay_seconds: float = Field(default=0.5, ge=0)
    max_delay_seconds: float = Field(default=8.0, ge=0)

    def delay(self, attempt: int) -> float:
        return float(min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1))))


class ModelUsageRecord(BaseModel):
    """One provider-reported token usage and estimated-cost record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=256)
    provider_id: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    attempts: int = Field(ge=1)
    estimated_cost: Decimal = Field(ge=0)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)


class ModelUsageStore(Protocol):
    """Extension contract for durable per-request model usage accounting.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives beside the
    `ModelUsageRecord` model it manages and its consumer (`OpenAICompatibleProvider`,
    `models/openai_compatible.py`), in the `models/` package, not in `storage/`.
    `SQLiteModelUsageStore`'s full public surface is exactly `append`/`list`, so this
    Protocol declares both -- unlike some other storage Protocols in this codebase, there
    was no wider surface to narrow.

    `SQLiteModelUsageStore` itself moved to `storage/models.py` (V2-P0B-011): ADR-0001's
    guardrail forbids `openalpha_cn.models` from importing `sqlite3` directly
    (`models-no-infra-imports` in `pyproject.toml`). Typing `OpenAICompatibleProvider
    .usage_store` against this Protocol instead of the concrete class means `models/`
    never needs to import `storage/` at all to use it -- structural typing satisfies the
    parameter without a runtime dependency in either direction.
    """

    def append(self, record: ModelUsageRecord) -> None:
        """Append one usage record idempotently by provider request ID."""

    def list(self, *, provider_id: str | None = None) -> tuple[ModelUsageRecord, ...]:
        """List recorded usage, optionally filtered by provider ID."""
