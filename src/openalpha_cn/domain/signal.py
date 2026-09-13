"""Structured research signal contracts."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.horizon import COUNTABLE_HORIZON_PATTERN
from openalpha_cn.domain.risk_flag import RiskFlag
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions


class SignalFrame(BaseModel):
    """An immutable, evidence-linked research conclusion or abstention."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["signal-frame/v1"] = "signal-frame/v1"
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    direction: Literal["bullish", "bearish", "neutral", "abstain"]
    strength: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    horizon: str = Field(pattern=COUNTABLE_HORIZON_PATTERN)
    """How far ahead this conclusion reaches, as `<count>d` -- see `domain/horizon.py`.

    Constrained rather than normalised, and it stays a `str` on purpose. `signal_id` hashes the
    canonical JSON of these fields, so replacing the type or rewriting an accepted value would
    move the identity of every stored signal; restricting the *domain* moves none, because
    every value that was already well formed serialises to the bytes it always did. The
    original `min_length=1, max_length=64` admitted `'whenever'`, which nothing downstream
    could turn into a return window -- which is the gap `V2-P1-017` closed by attaching
    `HORIZON_PATTERN`.

    `V2-P4-001` narrows it a second time, from four units to the one that has a session
    count, so that any two horizons a signal carries are comparable (PRD D36) and every one of
    them can be turned into the return window that scores it. `COUNTABLE_HORIZON_PATTERN`'s
    docstring holds the argument; `schema_version` stays at `signal-frame/v1` because a
    narrowing moves no `signal_id`, and this one is measured not to
    (`tests/unit/domain/test_contract_identity.py::test_narrowing_the_signal_horizon_moved_no_stored_signal_id`).
    A stored v1 frame carrying a calendar horizon is the one casualty, and it is not silently
    reinterpreted: `storage/migrations.py`'s identity rewrite finds it and refuses by name,
    because converting `3m` into a session count needs a constant this repository has not
    measured.
    """
    evidence_ids: tuple[str, ...] = ()
    confirmation_conditions: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    risk_flags: tuple[RiskFlag, ...] = ()
    """The cautions this signal carries, from the closed vocabulary in `domain/risk_flag.py`.

    `tuple[str, ...]` until `V2-P4-030`, which is the field the open-set defect lived in. Three
    modules read closed subsets of it and none of them agreed; worse, a producer that misspelled
    a flag was not refused -- `future-data` instead of `future_data` was worth `unrecognised`,
    a rung *above* `clear` and *below* `reduced`, so a typo of the most serious flag in the
    build **promoted** the candidate carrying it up a governed screen. `V2-P4-006` measured that
    and named this field as the place it had to be closed.

    Narrowing rather than re-versioning, and `schema_version` stays at `signal-frame/v1` for
    `domain/horizon.py`'s reason: `signal_id` hashes the canonical JSON of these fields, and
    `RiskFlag` is a `StrEnum`, so every value that was already well formed serialises to the
    bytes it always did and no stored identity moves. That is measured rather than assumed --
    `tests/unit/domain/test_risk_flag.py::test_closing_the_vocabulary_moved_no_stored_signal_id`
    asserts a fixed digest, because an identity that moved would not fail.

    The one casualty is a stored `run_recovery` payload carrying a flag no shipped writer
    produces, which `SQLiteRecoveryStore.get` re-validates through this model and would now
    refuse. Unlike the horizon narrowing -- which outlawed `3m`, a value the contract had
    genuinely accepted and which no constant could convert -- nothing this build ever *wrote* is
    outlawed here, so there is no rewrite to perform and no `storage/migrations.py` pass to
    perform it: every flag `evidence/builder.py` and the committee emit is declared, held by
    `tests/unit/evidence/test_builder.py` and by `test_risk_flag.py`'s gate audit. See
    `KNOWN_SCREENING_LIMITATIONS
    .a_recovery_row_carrying_a_caller_injected_flag_is_refused_rather_than_migrated`.
    """
    abstention_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_conclusion(self) -> Self:
        if self.direction == "abstain":
            if self.abstention_reason is None:
                raise ValueError("abstention_reason is required when direction is abstain")
            if self.strength != 0:
                raise ValueError("strength must be zero when direction is abstain")
        else:
            if not self.evidence_ids:
                raise ValueError("directional signal requires evidence")
            if self.abstention_reason is not None:
                raise ValueError("abstention_reason is only valid when direction is abstain")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def signal_id(self) -> str:
        """Return the stable content-derived signal identifier."""
        return stable_model_id(prefix="sig", model=self)


SIGNAL_FRAME_VERSIONS: ContractVersions[SignalFrame] = ContractVersions(
    name="signal-frame",
    current_version="signal-frame/v1",
    versions={"signal-frame/v1": SignalFrame},
)
