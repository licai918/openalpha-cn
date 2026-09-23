"""Outcome validation and attribution contracts."""

import math
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.versioning import ContractVersions, IdentityRewriteRequiredError


class AttributionTerm(BaseModel):
    """One additive contribution to a validated net active return."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    category: Literal["rule", "factor", "agent", "model"]
    """Which kind of thing this contribution is claimed for.

    `model` is `V2-P4-001`'s addition and it is not a synonym for `agent`: an agent is an LLM
    deliberation step (`agents/`), a model is a fitted quantitative predictor
    (`V2-P4-011`'s `AlphaModel`, kept strictly separate from the LLM `ModelProvider` for the
    reason `V2-P4-011` gives). Without it, a P4 ranking driven by a fitted model would have
    had to book its contribution under one of the other three, and the report would have said
    an agent earned what a model earned.
    """
    name: str = Field(min_length=1, max_length=128)
    contribution: float


class ValidationResult(BaseModel):
    """An immutable outcome and attribution record."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["validation-result/v2"] = "validation-result/v2"
    signal_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    observation_start: datetime
    observation_end: datetime
    realized_return: float
    benchmark_return: float
    transaction_cost: float = Field(ge=0)
    attribution: tuple[AttributionTerm, ...]
    unexplained_return: float = 0.0
    """The part of `net_active_return` that no `AttributionTerm` claims (`V2-P4-001`).

    **Whose residual, against what**: this is the attribution's own leftover, measured against
    this result's `net_active_return`. It is not a regression residual and it is emphatically
    not `domain/factor_neutralization.py`'s neutralisation residual -- that one is a
    cross-sectional quantity per name per day, this one is one number for one decision over
    one window.

    Explicit because the alternative is what `backtest/validation.py` did until `V2-P5-005`
    deleted it: the last agent term absorbed `net_active_return` minus everything already
    allocated, so the reconciliation check below passed *by construction* -- it could never
    fail, and therefore had never measured anything. With the leftover in its own field the
    check has a free variable removed from it: a term set that does not add up has to say so
    in a number a report can print, rather than hiding inside whichever term happened to be
    last.

    Defaulting to `0.0` rather than being required, because a caller whose terms already sum
    to `net_active_return` would otherwise have to write `0.0` by hand; a default that is *the
    honest value for those callers* is not a lenient default. `OutcomeValidator` no longer
    relies on it -- since `V2-P5-005`/`V2-P5-006` it states the residual on every result, and
    a decision that held a position states a non-zero one.
    """
    confidence: float = Field(ge=0, le=1)
    data_quality_notes: tuple[str, ...] = ()

    @field_validator("observation_start", "observation_end")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_window_and_attribution(self) -> Self:
        if self.observation_end <= self.observation_start:
            raise ValueError("observation_end must be after observation_start")
        contribution = sum(item.contribution for item in self.attribution) + self.unexplained_return
        if not math.isclose(contribution, self.net_active_return, abs_tol=1e-9):
            raise ValueError("attribution does not reconcile with net_active_return")
        return self

    @computed_field(return_type=float)  # type: ignore[prop-decorator]
    @property
    def net_active_return(self) -> float:
        """Return realized return less benchmark return and transaction cost."""
        return self.realized_return - self.benchmark_return - self.transaction_cost

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def validation_id(self) -> str:
        """Return the stable content-derived validation identifier."""
        return stable_model_id(prefix="val", model=self)


class AttributionTermV1(BaseModel):
    """The frozen `validation-result/v1` attribution term: three categories, no `model`."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    category: Literal["rule", "factor", "agent"]
    name: str = Field(min_length=1, max_length=128)
    contribution: float


class ValidationResultV1(BaseModel):
    """The frozen `validation-result/v1` shape, kept so a stored v1 row can still be read.

    Read by `storage/migrations.py::rewrite_contract_identities`, which is the only thing that
    may advance one of these rows -- see `refuse_validation_result_v1_upgrade`. It differs
    from `ValidationResult` in three places: the `schema_version` literal, the narrower
    `AttributionTermV1` category set, and the absence of `unexplained_return`.

    The `net_active_return` computed field and the reconciliation validator are reproduced
    rather than omitted: without them this class would accept a v1 payload the v1 build would
    have rejected, and a migration that silently repaired a broken row while claiming to
    re-version it is worse than one that refuses.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["validation-result/v1"] = "validation-result/v1"
    signal_id: str = Field(min_length=1, max_length=128)
    decision_id: str = Field(min_length=1, max_length=128)
    observation_start: datetime
    observation_end: datetime
    realized_return: float
    benchmark_return: float
    transaction_cost: float = Field(ge=0)
    attribution: tuple[AttributionTermV1, ...]
    confidence: float = Field(ge=0, le=1)
    data_quality_notes: tuple[str, ...] = ()

    @field_validator("observation_start", "observation_end")
    @classmethod
    def normalize_datetimes(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_window_and_attribution(self) -> Self:
        if self.observation_end <= self.observation_start:
            raise ValueError("observation_end must be after observation_start")
        net = self.realized_return - self.benchmark_return - self.transaction_cost
        contribution = sum(item.contribution for item in self.attribution)
        if not math.isclose(contribution, net, abs_tol=1e-9):
            raise ValueError("attribution does not reconcile with net_active_return")
        return self


def refuse_validation_result_v1_upgrade(old: BaseModel) -> BaseModel:
    """Refuse to advance a v1 result at read time; the storage migration must do it.

    `validation_results.validation_id` is this model's own content address and the table's
    unique key, so roadmap section 8's rule applies exactly as it does to the decision ledger.
    There is a second reason here too, and it is not the same one: this model's `decision_id`
    field points at a ledger row whose own address the same migration is moving, so a result
    upgraded in isolation would keep a `decision_id` that no longer resolves -- correct
    version, dangling reference.
    """
    raise IdentityRewriteRequiredError(
        contract="validation-result", found_version=getattr(old, "schema_version", None)
    )


VALIDATION_RESULT_VERSIONS: ContractVersions[ValidationResult] = ContractVersions(
    name="validation-result",
    current_version="validation-result/v2",
    versions={
        "validation-result/v1": ValidationResultV1,
        "validation-result/v2": ValidationResult,
    },
    upgrades={"validation-result/v1": refuse_validation_result_v1_upgrade},
)
