"""Shared provider contracts and explicit failure semantics."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.json_value import canonical_json_bytes, freeze_json, thaw_json
from openalpha_cn.domain.time import Timeline, ensure_aware, is_visible_at

ProviderFailureCategory = Literal[
    "authentication",
    "configuration",
    "invalid_response",
    "rate_limit",
    "upstream",
]


class ProviderFailure(RuntimeError):
    """A structured provider failure that can never masquerade as empty data."""

    def __init__(
        self,
        *,
        provider_id: str,
        category: ProviderFailureCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.category = category
        self.retryable = retryable


class ProviderMetadata(BaseModel):
    """Licensing, credentials, cache, freshness, and failure policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    source_license: str = Field(min_length=1, max_length=256)
    redistribution: Literal["allowed", "restricted", "unknown"]
    credential_env_vars: tuple[str, ...]
    supported_datasets: tuple[str, ...] = ()
    caching_policy: Literal["local-permitted", "prohibited", "provider-defined"]
    rate_limit: str = Field(min_length=1, max_length=512)
    freshness: str = Field(min_length=1, max_length=512)
    failure_semantics: str = Field(min_length=1, max_length=1000)


class ProviderRequest(BaseModel):
    """A deterministic point-in-time provider request."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset: str = Field(min_length=1, max_length=128)
    as_of: datetime
    subjects: tuple[str, ...] = ()

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)


class ProviderRecord(BaseModel):
    """One normalized provider row before evidence-specific interpretation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["provider-record/v1"] = "provider-record/v1"
    subject: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    timeline: Timeline
    source_uri: str | None = Field(default=None, max_length=2048)
    summary: str = Field(min_length=1, max_length=4000)
    payload: JsonValue

    @model_validator(mode="after")
    def freeze_payload(self) -> Self:
        canonical_json_bytes(self.payload)
        object.__setattr__(self, "payload", freeze_json(self.payload))
        return self

    @field_serializer("payload")
    def serialize_payload(self, value: JsonValue) -> JsonValue:
        return thaw_json(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def record_id(self) -> str:
        """Return a stable content-derived provider record ID."""
        return stable_model_id(prefix="rec", model=self)


class ProviderBatch(BaseModel):
    """An explicit success or no-data result for one provider request."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["provider-batch/v1"] = "provider-batch/v1"
    provider_id: str = Field(min_length=1, max_length=128)
    request: ProviderRequest
    fetched_at: datetime
    status: Literal["success", "no_data"]
    records: tuple[ProviderRecord, ...] = ()
    no_data_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("fetched_at")
    @classmethod
    def normalize_fetched_at(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.status == "success":
            if not self.records:
                raise ValueError("success requires at least one record")
            if self.no_data_reason is not None:
                raise ValueError("success cannot include no_data_reason")
        else:
            if self.records:
                raise ValueError("no_data cannot include records")
            if self.no_data_reason is None:
                raise ValueError("no_data requires no_data_reason")
        if any(not is_visible_at(record.timeline, self.request.as_of) for record in self.records):
            raise ValueError("provider batch contains records unavailable at request as_of")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def payload_digest(self) -> str:
        """Return the full SHA-256 digest of the serialized provider records."""
        payloads = [
            record.model_dump(mode="json", exclude_computed_fields=True) for record in self.records
        ]
        return f"sha256:{sha256(canonical_json_bytes(payloads)).hexdigest()}"


class DataProvider(Protocol):
    """The stable extension interface implemented by every data provider."""

    @property
    def metadata(self) -> ProviderMetadata:
        """Return provider policy metadata."""

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        """Fetch a point-in-time batch or raise ``ProviderFailure``."""


def utc_now() -> datetime:
    """Return the current UTC clock for injectable provider defaults."""
    return datetime.now(UTC)
