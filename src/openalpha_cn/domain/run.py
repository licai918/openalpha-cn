"""Reproduction and checkpoint contracts for research runs."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.domain.time import ensure_aware


class VersionRef(BaseModel):
    """A named component version captured for reproduction."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    component: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=256)


class ArtifactDigest(BaseModel):
    """A named SHA-256 digest for a provider payload or other artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckpointRecord(BaseModel):
    """An immutable recovery checkpoint reference."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    recorded_at: datetime
    state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)


class RunManifest(BaseModel):
    """Everything required to reproduce and recover one research run."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["run-manifest/v1"] = "run-manifest/v1"
    run_id: str = Field(min_length=1, max_length=128)
    mode: Literal["live", "replay", "backtest"]
    as_of: datetime
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_payload_digests: tuple[ArtifactDigest, ...] = ()
    model_versions: tuple[VersionRef, ...] = ()
    prompt_versions: tuple[VersionRef, ...] = ()
    random_seed: int
    environment: tuple[VersionRef, ...] = ()
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "running", "succeeded", "failed", "interrupted"]
    checkpoints: tuple[CheckpointRecord, ...] = ()

    @field_validator("as_of", "started_at", "finished_at")
    @classmethod
    def normalize_datetimes(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_aware(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        is_terminal = self.status in {"succeeded", "failed", "interrupted"}
        if is_terminal and self.finished_at is None:
            raise ValueError("finished_at is required for a terminal run")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if any(item.recorded_at < self.started_at for item in self.checkpoints):
            raise ValueError("checkpoint cannot precede started_at")
        return self
