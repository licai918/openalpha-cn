"""Immutable, content-addressed evidence contracts."""

from datetime import datetime
from hashlib import sha256
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    field_serializer,
    model_validator,
)

from openalpha_cn.domain.json_value import canonical_json_bytes, freeze_json, thaw_json
from openalpha_cn.domain.time import Timeline, is_visible_at
from openalpha_cn.domain.versioning import ContractVersions


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
        canonical_json_bytes(self.payload)
        object.__setattr__(self, "payload", freeze_json(self.payload))
        return self

    @field_serializer("payload")
    def serialize_payload(self, value: JsonValue) -> JsonValue:
        return thaw_json(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """Return the SHA-256 digest of the canonical structured payload."""
        return sha256(canonical_json_bytes(self.payload)).hexdigest()

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


EVIDENCE_SNAPSHOT_VERSIONS: ContractVersions[EvidenceSnapshot] = ContractVersions(
    name="evidence-snapshot",
    current_version="evidence-snapshot/v1",
    versions={"evidence-snapshot/v1": EvidenceSnapshot},
)
