"""Immutable, content-addressed evidence contracts."""

from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol

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


class _FrozenPayloadHost(Protocol):
    """The shape `FreezePayloadMixin.freeze_payload` needs from whatever it is mixed into.

    `payload` is a plain attribute here, not a read-only `@property` the way
    `NoteLookupMixin`'s `_NotedRegistry.notes` (`domain/factor.py`) is. That distinction mattered
    there because mypy treats a `@dataclass(frozen=True)` field as a read-only descriptor for
    Protocol matching, and a plain Protocol variable demands a *settable* host -- a mismatch
    strict mode reported as "expected settable variable, got read-only attribute". Neither host
    here is a dataclass: both `ProviderRecord` and `EvidenceSnapshot` are Pydantic `BaseModel`s
    with `model_config = ConfigDict(frozen=True, ...)`, and this project enables no
    `pydantic.mypy` plugin (no `plugins` entry under `[tool.mypy]`), so mypy has no special
    knowledge of Pydantic's own frozen config -- it sees `payload: JsonValue` on each host as an
    ordinary settable class attribute, exactly as it would on an unfrozen one. A plain Protocol
    variable therefore matches both hosts structurally. Verified with a standalone
    `mypy --strict` probe before writing this, rather than assumed from `NoteLookupMixin`'s
    precedent -- the two mixins' hosts are a different kind of class and the same shape does not
    automatically transfer.
    """

    payload: JsonValue


class FreezePayloadMixin:
    """Supplies `freeze_payload` to a Pydantic model with a `payload: JsonValue` field.

    `ProviderRecord.freeze_payload` (`providers/base.py`) and this class's `freeze_payload` were
    two function bodies an AST comparison found byte-identical. `V2-P5-071` recorded the same
    finding and merged nothing, noting only that "consolidating needs a mixin" -- this is that
    mixin.

    No `import-linter` contract forces the two hosts apart the way one forced `_board` and
    `average_ranks` into two restatements pinned equal by a test instead of merged (see that
    commit's own reasoning). `providers/base.py` already imports several names from
    `openalpha_cn.domain` (`_identity`, `json_value`, `panel_batch`, `time`, `versioning`), and
    `domain-purity` (`pyproject.toml`) only forbids the reverse -- `openalpha_cn.domain`
    importing a sibling subpackage such as `openalpha_cn.providers`. Living here, in `domain`,
    and imported by `providers/base.py` is therefore the one direction this contract allows;
    defining it in `providers` and having this module import it back would break `domain-purity`
    the instant `EvidenceSnapshot` needed it.

    Placed in `domain/evidence.py` rather than a new module, mirroring where `NoteLookupMixin`
    lives (`domain/factor.py`, alongside `FactorRegistry`, one of its own three hosts): one
    shared method does not earn a module of its own when one of its two hosts already has a
    home that satisfies the layering rule.

    `__slots__ = ()` declares no state of its own -- the same reasoning `NoteLookupMixin` uses,
    though it buys nothing for these two hosts specifically: `pydantic.BaseModel.__slots__`
    already includes `"__dict__"`, so a Pydantic model carries one regardless of what is mixed
    into it (checked directly: `BaseModel.__slots__` names it). Declared anyway because it costs
    nothing and keeps this mixin safe if it is ever combined with a `__slots__`-based host
    instead of a Pydantic one.
    """

    __slots__ = ()

    @model_validator(mode="after")
    def freeze_payload(self: _FrozenPayloadHost) -> _FrozenPayloadHost:
        """Freeze `payload` into a deeply immutable structure, refusing one that cannot be
        canonically serialized.

        `mode="after"` validators are collected from a Pydantic model's whole MRO, so declaring
        this once here registers it for both `ProviderRecord` and `EvidenceSnapshot` without
        either subclass writing its own wrapper. `canonical_json_bytes(self.payload)` runs first
        and its return value is discarded -- its only job here is to raise on a payload
        `json.dumps(..., allow_nan=False)` cannot serialize (a `NaN`/`Infinity` float, or a
        non-JSON-shaped value) before the line below commits to freezing it. The write goes
        through `object.__setattr__` rather than plain assignment because both hosts are
        `ConfigDict(frozen=True)`, which would otherwise reject the very mutation this validator
        exists to perform.
        """
        canonical_json_bytes(self.payload)
        object.__setattr__(self, "payload", freeze_json(self.payload))
        return self


class EvidenceSnapshot(FreezePayloadMixin, BaseModel):
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
