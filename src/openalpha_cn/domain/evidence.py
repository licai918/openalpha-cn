"""Immutable, content-addressed evidence contracts."""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    field_serializer,
    model_validator,
)

from openalpha_cn.domain.time import Timeline, is_visible_at


def _freeze(value: JsonValue) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return cast(JsonValue, value)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _thaw(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


class EvidenceSnapshot(BaseModel):
    """One immutable evidence item with stable identity and four time clocks."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    schema_version: Literal["evidence-snapshot/v1"] = "evidence-snapshot/v1"
    subject: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    timeline: Timeline
    source_id: str = Field(min_length=1, max_length=128)
    source_uri: str | None = Field(default=None, max_length=2048)
    source_license: str = Field(min_length=1, max_length=128)
    redistribution: Literal["allowed", "restricted", "unknown"]
    summary: str = Field(min_length=1, max_length=4000)
    payload: JsonValue

    @model_validator(mode="after")
    def freeze_payload(self) -> Self:
        _canonical_json(self.payload)
        object.__setattr__(self, "payload", _freeze(self.payload))
        return self

    @field_serializer("payload")
    def serialize_payload(self, value: JsonValue) -> JsonValue:
        return _thaw(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """Return the SHA-256 digest of the canonical structured payload."""
        return sha256(_canonical_json(self.payload)).hexdigest()

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def evidence_id(self) -> str:
        """Return a stable evidence ID derived from provenance and content."""
        identity = "|".join(
            [
                self.subject,
                self.kind,
                self.source_id,
                self.timeline.available_time.isoformat(),
                self.content_hash,
            ]
        )
        return f"ev_{sha256(identity.encode()).hexdigest()[:24]}"

    def visible_at(self, as_of: datetime) -> bool:
        """Return whether this evidence was available at ``as_of``."""
        return is_visible_at(self.timeline, as_of)
