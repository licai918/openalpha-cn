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


class LookAheadViolationError(ValueError):
    """Raised when evidence would not have been visible at the moment it is used.

    V2-P0B-014 / audit F46. Two independent point-in-time guards check exactly this --
    `ResearchRunRequest.validate_evidence` (`domain/run_request.py`) and
    `ReplayCase.validate_point_in_time` (`backtest/replay.py`) -- and both raise this type
    rather than a bare `ValueError`. Before this type existed, `ReplayRunner.run()`
    recognised a look-ahead violation by matching the substrings "look-ahead" and "not
    visible" against `str(error)`. That was silently fragile two ways: any routine message
    rewrite (translation, added context, rewording) zeroed
    `ReplayReport.look_ahead_violations` without any test noticing, since the frozen-corpus
    test only asserts the count; and any unrelated `ValueError` that happened to contain
    either substring was miscounted as a look-ahead violation. Classifying by
    `isinstance`/`except` against this type instead of parsing the message removes both
    failure modes: the wording is free to change, and nothing else can be mistaken for it.

    It subclasses `ValueError` on purpose -- not `Exception` directly -- so every call site
    that already wrote `except ValueError` (or a tuple including it) keeps catching this
    exactly as it caught the bare `ValueError` it replaces, with no behavior change.

    Lives here, next to `EvidenceSnapshot.visible_at`, rather than in a new module: both
    raise sites are checking that exact predicate, and `domain/` -- the one package in this
    repository with no upward dependencies -- must be able to raise it without importing
    anything from `backtest/`.
    """


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
