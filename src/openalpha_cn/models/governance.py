"""Model capability registry, bounded retry policy, and durable usage accounting."""

import sqlite3
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path

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


class SQLiteModelUsageStore:
    """Append provider usage idempotently by provider request ID."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_usage (
                    request_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def append(self, record: ModelUsageRecord) -> None:
        payload = record.model_dump_json()
        with closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT payload FROM model_usage WHERE request_id = ?",
                (record.request_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError(f"model usage request_id conflicts: {record.request_id}")
                return
            connection.execute(
                "INSERT INTO model_usage (request_id, provider_id, payload) VALUES (?, ?, ?)",
                (record.request_id, record.provider_id, payload),
            )

    def list(self, *, provider_id: str | None = None) -> tuple[ModelUsageRecord, ...]:
        with closing(self._connect()) as connection:
            if provider_id is None:
                rows = connection.execute(
                    "SELECT payload FROM model_usage ORDER BY rowid"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload FROM model_usage
                    WHERE provider_id = ?
                    ORDER BY rowid
                    """,
                    (provider_id,),
                ).fetchall()
        return tuple(ModelUsageRecord.model_validate_json(row[0]) for row in rows)
