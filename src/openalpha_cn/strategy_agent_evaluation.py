"""The incremental-IC evaluator over stored agent and survivor records (`V2-P7-014`).

Plan C Task 2.7. The evaluator answers the one question plan C registered -- *does the agent's
score carry rank information about the forward label that Step 0's composite does not already
carry?* -- from **stored records only**: the agent batches the evening command filed and Step 0's
own `survivor_book_composite/v1` batches, read through the record-store Protocol, plus labels read
through `model_view.OutcomeLabels`' structural Protocol. It imports no `agents` and no `models`
(plan Review Focus 5): a report that could run an agent or call a model is not a measurement of
records filed before the outcome.

## What is measured, and what is not

- **Domain = Step 0's survivors ∩ the agent record's scored names.** The survivors come from
  `strategy_survivor_forward.record_scope(step0_record.batch, book=book)` -- the day's screen
  rebuilt from the stored composite -- and a name the agent scored but Step 0 did not keep as a
  survivor is outside the primary statistic. A name with no score (an abstention) is not zero: it
  leaves the cross section, the same rule `domain/agent_forward.score_of` states on the producer
  side (D3).
- **The factor is Step 0's stored composite**, read off the same record; the label is
  `OutcomeLabels`' forward return at the registered horizon, read at the report's `as_of`. An
  immature window is counted and never labelled: no price after `as_of` can enter a result.
- **The statistic** is `domain/incremental_ic.partial_rank_ic` -- the rank IC of the agent score
  after both it and the label are residualised on the factor's rank -- one value per session.
- **Primary and exploratory are never pooled.** The primary arm is the registered LLM family on
  the primary set at the registered horizon (`read_agent_protocol` pins it, D2). Every other arm
  the registration names is exploratory, reported separately, and controlled as one
  Benjamini-Yekutieli family at `q` -- the exploratory action is a labelled block, never a second
  try at the headline.

## The window: registered, not derived from the data

**The positions are the first `window_sessions` sessions Step 0 has filed with
`anchor_session <= session <= through`, in ascending order.** The anchor is C's registration's own
first forward session and `evaluate` takes it as a parameter (`anchor_session`); it is never
`min` of the sessions a store happens to hold. A record filed before the anchor -- by an earlier,
superseded or dry-run registration, or before the trial's window opened -- therefore cannot move
the window: the window is a pre-registered property of the registration, not a post-hoc reading
of the store. Step 0 files a record every session, so a day the LLM family filed nothing is a
position with a gap: it reduces `n` and never moves the later positions inward. A session whose
label window has not closed by `as_of` is not tested (`not_yet`), and the primary test is made
**once**, when every position has matured.

## What the evaluator refuses by name

The evaluator measures **what the registration registered**, so it refuses rather than silently
skip or read a record another configuration filed (`AgentEvaluationError`):

- **An agent record whose declaration names no `registration_sha256`, or another registration's.**
  Both C families carry the digest in their declaration's hyperparameters; a missing or foreign
  digest is refused by name, never skipped -- a skipped record would silently become a gap and
  hide that another registration's answer was in the store.
- **A Step 0 record that is not bound** (`strategy_survivor_registration.survivor_record_is_bound`
  with Step 0's book and its `RegisteredConfiguration`), or **not on time**:
  `registered_at(record) < registration_cutoff(calendar, session)`, with the calendar read off
  the labels reader. This is the same bound-and-on-time pair `agent_evening._step0_day` makes.
- **A duplicate record for one session**, of either family.

## The futility look (adopted by D5, 2026-11-12)

"Not measurable" (D3/D5) is a distinct outcome and never "no evidence": a session is measurable
when at least `min_scored` names survive the join of the two records and the label, and the
window is `not_measurable` when fewer than `min_measurable_sessions` of its positions are.

The futility look is taken at `interim_session` and may stop only on a **complete and measurable
interim**: every one of the first `interim_session` positions matured, and at least
`max(2, ceil(min_measurable_sessions * interim_session / window_sessions))` of them carrying a
value. When those hold and the interim's cumulative mean is at or below zero the outcome is
`futility_stop`; a thin or immature interim is `not_yet`. The stop is **sticky**: it is checked
before the window-full outcomes, because it was decided on the first interim positions' immutable
data, so a window that later fills or matures cannot erase it.

The user adopted this form and the one-sided reading below on 2026-11-12 (D5, "按推荐"), and the
decision-chain's §9 records the amendment that replaced the old two-test wording ("h1 ≥ 0.02 and
t ≥ 2, or h5 ≥ 0.06") with this one test. Every evaluation carries `adopted` -- the two adopted
rules -- so a report can state the rule it applied and no reader mistakes it for a post-hoc
choice.

## The test

The primary test is `outcome_statistics.sign_flip_test` (a two-sided randomization p over the
window's values), read one-sided the way `strategy_survivor_forward` reads it -- `p / 2` for a
positive mean, `1 - p / 2` below it. The protocol's `level` is applied to that one-sided reading;
the registered direction is "mean > 0". The sample size and the detectable mean are always
reported: `power.periods` is the measurable count and `power.mean` is the mean detectable at
`DETECTABLE_AT_POWER` from the measured dispersion (`domain/incremental_ic.detectable_mean`, the
survivor power block's formula), stated as a property of the design and never as observed power.

## The protocol (`read_agent_protocol`)

Read from the registration's `settings["agent_forward_protocol"]` as an
`agent-forward-protocol/v1` block, `extra="forbid"` and with **no default for any field** -- the
values are the user's registered decision, and a default would be one taken by code. Integers are
strict (a JSON `true` is not a count). `read_agent_protocol` additionally pins D2's one primary
statistic: the LLM family at the primary scoring set. The protocol's `secondary` block names the
exploratory arms; the primary arm may not appear there.
"""

from __future__ import annotations

import dataclasses
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openalpha_cn.backtest.multiple_testing import (
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
)
from openalpha_cn.backtest.outcome_statistics import RandomizationTest, sign_flip_test
from openalpha_cn.domain.agent_forward import AGENT_LLM_FAMILY, AGENT_RULE_FAMILY
from openalpha_cn.domain.horizon import MAX_HORIZON_COUNT, ResearchHorizon, parse_horizon
from openalpha_cn.domain.incremental_ic import (
    MIN_NAMES,
    SessionIC,
    detectable_mean,
    non_overlapping,
    partial_rank_ic_with_reason,
)
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_agent_forward import REGISTRATION_HYPERPARAMETER
from openalpha_cn.strategy_registration import (
    SHANGHAI,
    RegisteredConfiguration,
    registered_at,
    registration_cutoff,
)
from openalpha_cn.strategy_survivor_forward import record_scope
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_FAMILY,
    survivor_record_is_bound,
)

AGENT_FORWARD_PROTOCOL_KEY: Final = "agent_forward_protocol"
"""The key of the registration's `settings` that holds the evaluator's protocol."""
AGENT_FORWARD_PROTOCOL_SCHEMA: Final = "agent-forward-protocol/v1"

PRIMARY_SCORING_SET: Final = "primary"
"""The registered primary set (D2). The coverage set is permanently exploratory."""

PRIMARY_HORIZON: Final[int] = 1
"""The registered primary horizon (D2): the partial rank IC is read at one session.

The stored record's horizon is 5d (one record serves h=1 and h=5), but the *test* is the h=1
read: a 5-session primary would overlap its own labels and would be a second statistic. The
protocol reader and the public dataclass both refuse it; `h=5` may only appear as an exploratory
arm."""

DETECTABLE_AT_POWER: Final[float] = 0.8
"""The target power the detectable mean is stated at: the P7 route's own 0.8."""

NOT_MEASURABLE_WORDING: Final = (
    "not measurable: fewer than min_measurable_sessions of the window's positions carry the "
    "primary statistic; this is not 'no evidence', and no p-value is computed"
)
"""D3/D5: the outcome that must never be read as a negative result."""

MINIMUM_TEST_SAMPLE: Final[int] = 2
"""`outcome_statistics.MINIMUM_INTERVAL_SAMPLE_SIZE`: below two values there is no test."""

ADOPTED_FUTILITY_WORDING: Final = (
    "the adopted futility rule (D5, user 2026-11-12): at forward session 60, once all 60 interim "
    "positions have matured and at least the pro-rated measurable floor carries a value, a "
    "cumulative mean at or below zero stops the trial and the stop is sticky; the form was "
    "adopted as recommended, and a thin or immature interim is not a stop"
)
"""D5's adopted futility form, with its guards restated. Plan C's decision D5 (2026-11-12)."""
ADOPTED_ONE_SIDED_WORDING: Final = (
    "the adopted one-sided reading (D5, user 2026-11-12): a two-sided sign-flip p-value halved "
    "for a positive mean and 1 - p/2 below it, applied to the registered direction mean > 0 at "
    "alpha = 0.05; adopted as recommended, and the h=5 and rule-family reads are exploratory"
)
"""D5's adopted one-sided reading of the two-sided sign-flip test."""
D5_WORDINGS: Final[tuple[str, ...]] = (
    ADOPTED_FUTILITY_WORDING,
    ADOPTED_ONE_SIDED_WORDING,
)
"""Every adopted D5 rule an evaluation carries: both, always populated."""

ADOPTED_WINDOW_SESSIONS: Final[int] = 120
"""D5: the forward window -- 120 forward sessions after C's registration commit."""
ADOPTED_INTERIM_SESSION: Final[int] = 60
"""D5: the one futility look is at forward session 60."""
ADOPTED_MIN_SCORED: Final[int] = 10
"""D3: a session is measurable with at least ten scored primary names after the join."""
ADOPTED_MIN_MEASURABLE_SESSIONS: Final[int] = 60
"""D3: fewer than 60 measurable of the 120 positions is `not_measurable`, its own outcome."""
ADOPTED_LEVEL: Final[float] = 0.05
"""D5: one-sided alpha = 0.05 for the one primary test."""
ADOPTED_Q: Final[float] = 0.10
"""D5: the exploratory family's Benjamini-Yekutieli false discovery rate."""
ADOPTED_SECONDARY_ARMS: Final[tuple[tuple[str, str, int], ...]] = (
    (AGENT_RULE_FAMILY, "primary", PRIMARY_HORIZON),
    (AGENT_LLM_FAMILY, "primary", 5),
    (AGENT_LLM_FAMILY, "coverage", PRIMARY_HORIZON),
)
"""D5: "everything else" is exploratory, controlled as one family at `q`.

The three arms name the rule family, the h=5 read and the coverage set once each -- the plan's
sentence "the rule family, h=5 and the coverage set are exploratory" enumerates the dimensions,
not the full product. The horizon of an exploratory arm is the record's own 5d read at h=5; the
coverage set's arm stays at the primary h=1 so it is the same reading on the other set."""

DETECTABLE_IC_BASIS: Final = (
    "the mean detectable at probability DETECTABLE_AT_POWER from the window's measured "
    "dispersion, at the protocol's one-sided level; a property of the design, never observed "
    "power and never a result"
)
"""What every printed detectable mean is: the survivor power block's sentence, one plane over."""

DRY_WEEK_SCRATCH_MARKER: Final = "c-dry-week-scratch.json"
"""The marker file a D7 dry-week scratch runtime carries at its root (`V2-P7-013`).

The dry week measures the model family's operational behaviour before C's registration exists
(D7: at most five evenings, at most 600 model requests each, operational metrics only). Its
journal is not a forward record: no prediction is filed and no score is kept, so its scratch
runtime must never be read as C's. `scripts/agent_evening.py --dry-week` writes this marker;
`scripts/agent_report.py` and `evaluate` refuse a runtime that holds it, rather than silently
measuring an evening that was never a prediction."""
DRY_WEEK_SCRATCH_SCHEMA: Final = "openalpha-c-dry-week-scratch/v1"
"""The marker's schema; anything else in the marker file is a refusal, not a scratch runtime."""


def refuse_a_dry_week_runtime(runtime_dir: Path) -> None:
    """Refuse a runtime marked as a D7 dry-week scratch: it is never evaluated.

    The dry week is operational measurement before registration; reading it as C's forward
    sample would treat a run C never made as a prediction. A marker that cannot be read is a
    refusal too (fail closed): a runtime whose status cannot be stated is not evaluated.
    """
    marker = Path(runtime_dir) / DRY_WEEK_SCRATCH_MARKER
    if not marker.exists():
        return
    try:
        body = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AgentEvaluationError(
            f"{runtime_dir} carries a {DRY_WEEK_SCRATCH_MARKER} that cannot be read "
            f"({error}); a runtime whose dry-week status cannot be stated is not evaluated"
        ) from error
    if not isinstance(body, Mapping) or body.get("schema") != DRY_WEEK_SCRATCH_SCHEMA:
        raise AgentEvaluationError(
            f"{runtime_dir} carries a {DRY_WEEK_SCRATCH_MARKER} that is not a "
            f"{DRY_WEEK_SCRATCH_SCHEMA} document; the evaluator does not read a runtime whose "
            "status it cannot verify"
        )
    raise AgentEvaluationError(
        f"{runtime_dir} is a D7 dry-week scratch runtime (marked {DRY_WEEK_SCRATCH_MARKER}): "
        "its evenings measured only the model family's operational behaviour and filed no "
        "prediction, so they are never part of C's forward sample and this evaluator refuses "
        "to read them as one"
    )


# --- the registration's companion blocks (D1/D7, read by the writer, the report and the tests) --

AGENT_FORWARD_POWER_KEY: Final = "agent_forward_power"
"""The settings key of the D1 power decision the registration freezes."""
AGENT_FORWARD_POWER_SCHEMA: Final = "agent-forward-power/v1"
"""The power block's schema: the chosen sample, its verdict, and the report it came from."""
AGENT_FORWARD_STEP0_KEY: Final = "agent_forward_step0"
"""The settings key naming the Step 0 registration C reads (its book, identity and seed)."""
AGENT_FORWARD_STEP0_SCHEMA: Final = "agent-step0-registration/v1"
AGENT_FORWARD_ANCHOR_KEY: Final = "agent_forward_anchor"
"""The settings key of C's registered first forward session (the evaluator's window anchor)."""
AGENT_FORWARD_ANCHOR_SCHEMA: Final = "agent-forward-anchor/v1"

_HEX_SHA256: Final = r"[0-9a-f]{64}"


class _PowerBlock(BaseModel):
    """The `agent-forward-power/v1` block: the D1 decision the registration freezes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-forward-power/v1"]
    sample: Literal["n300", "census"]
    verdict: Literal["testable", "not_testable"]
    register_untestable: bool
    report_sha256: Annotated[str, Field(pattern=_HEX_SHA256)]
    measurable_sessions: Annotated[int, Field(strict=True, ge=0)]
    detectable_mean: Annotated[float, Field(strict=True, ge=0)] | None

    @model_validator(mode="after")
    def _an_untestable_set_is_recorded_and_nothing_else_is(self) -> _PowerBlock:
        if self.verdict == "not_testable" and not self.register_untestable:
            raise ValueError(
                "the power block says not_testable but does not record the --register-untestable "
                "decision; D1 registers an untestable set only as the user's explicit choice"
            )
        if self.verdict == "testable" and self.register_untestable:
            raise ValueError(
                "the power block records --register-untestable but the chosen set is testable; "
                "the flag exists for the not_testable branch only"
            )
        return self


class _Step0Block(BaseModel):
    """The `agent-step0-registration/v1` block: which Step 0 registration C reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-step0-registration/v1"]
    path: Annotated[str, Field(min_length=1)]
    registration_sha256: Annotated[str, Field(pattern=_HEX_SHA256)]
    config_id: Annotated[str, Field(min_length=1)]
    seed: Annotated[int, Field(strict=True)]


class _AnchorBlock(BaseModel):
    """The `agent-forward-anchor/v1` block: C's registered first forward session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-forward-anchor/v1"]
    session: date


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentForwardPower:
    """The D1 power decision: the registered sample, its verdict and the report it came from."""

    sample: Literal["n300", "census"]
    verdict: Literal["testable", "not_testable"]
    register_untestable: bool
    report_sha256: str
    measurable_sessions: int
    detectable_mean: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentForwardStep0:
    """The Step 0 registration C reads: its committed path, identity and book seed."""

    path: str
    registration_sha256: str
    config_id: str
    seed: int


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentForwardAnchor:
    """C's registered first forward session: the evaluator's window anchor."""

    session: date


def _block_of(settings: Mapping[str, object], key: str, schema: str) -> Mapping[str, object]:
    """The named block of a registration's settings, or a refusal naming why there is none."""
    if not isinstance(settings, Mapping):
        raise AgentEvaluationError(
            f"the registration's settings are {type(settings).__name__}, not an object, so they "
            f"carry no {key} block"
        )
    if key not in settings:
        raise AgentEvaluationError(
            f"the registration's settings carry no {key} block; C's registration records the "
            f"{schema} decision the evaluator and the report read, and is not produced without one"
        )
    block = settings[key]
    if not isinstance(block, Mapping):
        raise AgentEvaluationError(
            f"the registration's {key} is {type(block).__name__}, not a {schema} object"
        )
    return block


def read_agent_forward_power(settings: Mapping[str, object]) -> AgentForwardPower:
    """The `agent_forward_power` block of C's registered settings (D1)."""
    block = _block_of(settings, AGENT_FORWARD_POWER_KEY, AGENT_FORWARD_POWER_SCHEMA)
    try:
        parsed = _PowerBlock.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise AgentEvaluationError(
            f"the registration's {AGENT_FORWARD_POWER_KEY} is not a valid "
            f"{AGENT_FORWARD_POWER_SCHEMA} block: {problems}"
        ) from error
    return AgentForwardPower(
        sample=parsed.sample,
        verdict=parsed.verdict,
        register_untestable=parsed.register_untestable,
        report_sha256=parsed.report_sha256,
        measurable_sessions=parsed.measurable_sessions,
        detectable_mean=parsed.detectable_mean,
    )


def read_agent_forward_step0(settings: Mapping[str, object]) -> AgentForwardStep0:
    """The `agent_forward_step0` block: the committed Step 0 registration C reads."""
    block = _block_of(settings, AGENT_FORWARD_STEP0_KEY, AGENT_FORWARD_STEP0_SCHEMA)
    try:
        parsed = _Step0Block.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise AgentEvaluationError(
            f"the registration's {AGENT_FORWARD_STEP0_KEY} is not a valid "
            f"{AGENT_FORWARD_STEP0_SCHEMA} block: {problems}"
        ) from error
    return AgentForwardStep0(
        path=parsed.path,
        registration_sha256=parsed.registration_sha256,
        config_id=parsed.config_id,
        seed=parsed.seed,
    )


def read_agent_forward_anchor(settings: Mapping[str, object]) -> AgentForwardAnchor:
    """The `agent_forward_anchor` block: C's registered first forward session."""
    block = _block_of(settings, AGENT_FORWARD_ANCHOR_KEY, AGENT_FORWARD_ANCHOR_SCHEMA)
    try:
        parsed = _AnchorBlock.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise AgentEvaluationError(
            f"the registration's {AGENT_FORWARD_ANCHOR_KEY} is not a valid "
            f"{AGENT_FORWARD_ANCHOR_SCHEMA} block: {problems}"
        ) from error
    return AgentForwardAnchor(session=parsed.session)


AgentOutcome = Literal[
    "not_yet",
    "candidate_increment",
    "no_increment_detected",
    "futility_stop",
    "not_measurable",
]
"""The five outcomes; `not_yet` is the only one a window not yet matured may produce."""

GAP_NO_AGENT_RECORD: Final = "no_agent_record"
GAP_NOT_MATURED: Final = "not_matured"
GAP_TOO_FEW_SCORED_NAMES: Final = "too_few_scored_names"
GAP_NO_FACTOR_SCORE: Final = "no_factor_score"


class AgentEvaluationError(ValueError):
    """A protocol or an evaluation the arithmetic refuses by name rather than answering wrongly."""


class ExploratoryArm(BaseModel):
    """One permanently exploratory arm: a (family, scoring set, horizon) the report reads.

    A pydantic model for the reason `read_agent_protocol` is closed and strict: the arm is a
    registered declaration, and a `horizon` of `true` or `"5"` is not one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: str
    scoring_set: Literal["primary", "coverage"]
    horizon: Annotated[int, Field(strict=True, ge=1, le=MAX_HORIZON_COUNT)]

    @model_validator(mode="after")
    def _the_family_is_one_of_the_two_agent_families(self) -> ExploratoryArm:
        if self.family not in {AGENT_LLM_FAMILY, AGENT_RULE_FAMILY}:
            raise ValueError(
                f"the exploratory arm names family {self.family!r}; C's two families are "
                f"{sorted({AGENT_LLM_FAMILY, AGENT_RULE_FAMILY})}"
            )
        return self


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentProtocol:
    """The registered evaluator protocol; see the module docstring. No field has a default."""

    primary_family: str
    primary_set: str
    primary_horizon: int
    secondary: tuple[ExploratoryArm, ...]
    window_sessions: int
    min_scored: int
    min_measurable_sessions: int
    interim_session: int
    level: float
    q: float
    bootstrap_samples: int
    random_seed: int

    def __post_init__(self) -> None:
        """D2's pin, restated on the value a caller may have built directly.

        `read_agent_protocol` is the only supported reader, but the dataclass is public and a
        caller that constructs one by hand must not be able to test a second primary statistic.
        """
        if self.primary_family != AGENT_LLM_FAMILY:
            raise AgentEvaluationError(
                f"the protocol's primary_family is {self.primary_family!r}; D2 registers the one "
                f"primary statistic on {AGENT_LLM_FAMILY!r}, and the rule family and every other "
                "family are exploratory"
            )
        if self.primary_set != PRIMARY_SCORING_SET:
            raise AgentEvaluationError(
                f"the protocol's primary_set is {self.primary_set!r}; the primary statistic is "
                "made on the primary set, and the coverage set is exploratory"
            )
        if self.primary_horizon != PRIMARY_HORIZON:
            raise AgentEvaluationError(
                f"the protocol's primary_horizon is {self.primary_horizon!r}; D2 registers the "
                f"one primary statistic at h={PRIMARY_HORIZON} (the stored record's 5d horizon "
                "is read at h=1), and a longer primary horizon would overlap its own labels and "
                "be a second statistic; h>1 belongs in `secondary` and is exploratory"
            )

    @property
    def arms(self) -> tuple[ExploratoryArm, ...]:
        """Every exploratory arm, in the registered order."""
        return self.secondary

    def window_positions(self, available: int) -> int:
        """`min(available, window_sessions)`: the positions read before the window is satisfied."""
        return min(available, self.window_sessions)

    def primary_arm(self) -> tuple[str, str, int]:
        """The primary `(family, scoring_set, horizon)`: never reported as secondary."""
        return (self.primary_family, self.primary_set, self.primary_horizon)


_Count = Annotated[int, Field(strict=True, ge=1)]
_Horizon = Annotated[int, Field(strict=True, ge=1, le=MAX_HORIZON_COUNT)]


class _ProtocolBlock(BaseModel):
    """The `agent-forward-protocol/v1` block: every field required, nothing defaulted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent-forward-protocol/v1"]
    primary_family: str
    primary_set: Literal["primary", "coverage"]
    primary_horizon: _Horizon
    secondary: tuple[ExploratoryArm, ...]
    window_sessions: _Count
    min_scored: _Count
    min_measurable_sessions: _Count
    interim_session: _Count
    level: Annotated[float, Field(strict=True, gt=0, lt=0.5)]
    q: Annotated[float, Field(strict=True, gt=0, lt=1)]
    bootstrap_samples: Annotated[int, Field(strict=True, ge=100, le=100_000)]
    random_seed: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def _the_window_and_the_arms_are_one_registered_design(self) -> _ProtocolBlock:
        if self.primary_family != AGENT_LLM_FAMILY:
            raise ValueError(
                f"primary_family is {self.primary_family!r}; D2 registers the one primary "
                f"statistic on {AGENT_LLM_FAMILY!r}, not on another family"
            )
        if self.primary_set != PRIMARY_SCORING_SET:
            raise ValueError(
                f"primary_set is {self.primary_set!r}; the primary statistic is made on the "
                "primary set, and the coverage set is exploratory"
            )
        if self.primary_horizon != PRIMARY_HORIZON:
            raise ValueError(
                f"primary_horizon is {self.primary_horizon!r}; D2's one primary statistic is "
                f"the h={PRIMARY_HORIZON} read (the stored record's 5d horizon is read at h=1); "
                "h>1 may only appear as an exploratory arm in `secondary`"
            )
        if self.window_sessions < MINIMUM_TEST_SAMPLE:
            raise ValueError(
                f"window_sessions is {self.window_sessions}; a window of fewer than "
                f"{MINIMUM_TEST_SAMPLE} sessions has no test to make"
            )
        if self.min_scored < MIN_NAMES:
            raise ValueError(
                f"min_scored is {self.min_scored}; a partial rank IC needs at least {MIN_NAMES} "
                "names, so a smaller floor would declare measurable a session the statistic "
                "cannot be computed on"
            )
        if self.min_measurable_sessions < MINIMUM_TEST_SAMPLE:
            raise ValueError(
                f"min_measurable_sessions is {self.min_measurable_sessions}; a sign-flip test "
                f"needs at least {MINIMUM_TEST_SAMPLE} values"
            )
        if self.min_measurable_sessions > self.window_sessions:
            raise ValueError(
                f"min_measurable_sessions is {self.min_measurable_sessions} of "
                f"{self.window_sessions} window sessions; a floor above the window makes the "
                "outcome not_measurable by construction"
            )
        if not 1 <= self.interim_session < self.window_sessions:
            raise ValueError(
                f"interim_session is {self.interim_session} of {self.window_sessions}; the "
                "futility look must fall strictly inside the window"
            )
        primary_arm = (self.primary_family, self.primary_set, self.primary_horizon)
        seen = set()
        for arm in self.secondary:
            key = (arm.family, arm.scoring_set, arm.horizon)
            if key == primary_arm:
                raise ValueError(
                    f"secondary names the primary arm {primary_arm}; the one primary statistic "
                    "is tested once and never repeated as an exploratory arm"
                )
            if key in seen:
                raise ValueError(f"secondary names {key} twice")
            seen.add(key)
        return self


def read_agent_protocol(settings: Mapping[str, object]) -> AgentProtocol:
    """The protocol in a registration's `settings`, or `AgentEvaluationError` naming why not."""
    if not isinstance(settings, Mapping):
        raise AgentEvaluationError(
            f"the registration's settings are {type(settings).__name__}, not an object, so they "
            f"carry no {AGENT_FORWARD_PROTOCOL_KEY} block"
        )
    if AGENT_FORWARD_PROTOCOL_KEY not in settings:
        raise AgentEvaluationError(
            f"the registration's settings carry no {AGENT_FORWARD_PROTOCOL_KEY} block; the "
            "evaluator reads its window, its arms, its floors and its test from the registered "
            "protocol, and is not produced without one"
        )
    block = settings[AGENT_FORWARD_PROTOCOL_KEY]
    if not isinstance(block, Mapping):
        raise AgentEvaluationError(
            f"the registration's {AGENT_FORWARD_PROTOCOL_KEY} is {type(block).__name__}, not a "
            f"{AGENT_FORWARD_PROTOCOL_SCHEMA} object"
        )
    try:
        parsed = _ProtocolBlock.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'the block'}: {item['msg']}"
            for item in error.errors()
        )
        raise AgentEvaluationError(
            f"the registration's {AGENT_FORWARD_PROTOCOL_KEY} is not a valid "
            f"{AGENT_FORWARD_PROTOCOL_SCHEMA} block: {problems}"
        ) from error
    return AgentProtocol(
        primary_family=parsed.primary_family,
        primary_set=parsed.primary_set,
        primary_horizon=parsed.primary_horizon,
        secondary=parsed.secondary,
        window_sessions=parsed.window_sessions,
        min_scored=parsed.min_scored,
        min_measurable_sessions=parsed.min_measurable_sessions,
        interim_session=parsed.interim_session,
        level=parsed.level,
        q=parsed.q,
        bootstrap_samples=parsed.bootstrap_samples,
        random_seed=parsed.random_seed,
    )


# --- the injected readers ----------------------------------------------------------------------


class RecordReader(Protocol):
    """What reading stored predictions needs of a store: the file prediction store."""

    def list_ids(self) -> tuple[str, ...]: ...

    def get(self, record_id: str) -> PredictionRecord | None: ...


class LabelSource(Protocol):
    """What the evaluator needs of a label reader: `model_view.OutcomeLabels` structurally.

    A Protocol rather than an import of the face, the same seam `strategy_survivor_forward` uses:
    an evaluator that could reach `model_view` could reach a product face, and the registered
    report is a measurement of records rather than a rendering. `calendar` is the registration's
    own calendar: the on-time check of a Step 0 record is `registration_cutoff`'s, and it needs
    the sessions the record's cutoff falls on.
    """

    @property
    def calendar(self) -> TradingCalendar: ...

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow: ...

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None: ...


# --- the readings ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class StatisticTest:
    """The randomization test once made, its one-sided reading, and which of the two ran."""

    two_sided_p_value: float
    one_sided_p_value: float
    exact: bool
    sign_patterns: int
    random_seed: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ArmReading:
    """One arm of the registration, read over the window: counts, a descriptive mean, its test.

    `missing` is positions with no record for this arm; `unmeasured` is positions with a record
    whose cross section was too thin, degenerate or not yet matured. An exploratory arm also
    carries its family-adjusted `q_value` and the `rejected` verdict, once the family ran.
    """

    family: str
    scoring_set: str
    horizon: int
    positions: int
    measurable: int
    missing: int
    unmeasured: int
    mean: float | None
    test: StatisticTest | None
    q_value: float | None = None
    rejected: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DetectableIC:
    """`n` and the mean detectable at `power` from the measured dispersion.

    `basis` is the sentence every printed power carries: a property of the design, not a result,
    and not observed power -- the p-value already says everything the window's own sd could.
    """

    periods: int
    sd: float | None
    level: float
    power: float
    mean: float | None
    basis: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentEvaluation:
    """The evaluator's answer: one of five outcomes, the primary reading, the exploratory arms.

    `sessions` is the window's positions read, `measurable` the primary arm's measurable count
    (the test's `n`), and `missing` names the positions with no primary record -- a gap reduces
    `n` and never moves the window. `power` always carries the sample size, even before the
    window matured.
    """

    outcome: AgentOutcome
    primary: ArmReading
    exploratory: tuple[ArmReading, ...]
    sessions: int
    measurable: int
    missing: tuple[SessionIC, ...]
    power: DetectableIC
    adopted: tuple[str, ...]
    """The adopted D5 rules (`D5_WORDINGS`): the futility form and the one-sided reading the user
    adopted on 2026-11-12. Always populated, never empty."""


# --- the evaluator -----------------------------------------------------------------------------


def _scoring_set_of(record: PredictionRecord) -> str | None:
    declared = dict(record.batch.artifact.declaration.hyperparameters).get("scoring_set")
    return declared if isinstance(declared, str) else None


def _session_of(record: PredictionRecord) -> date:
    return record.batch.as_of.astimezone(SHANGHAI).date()


def _window_matured(*, session: date, horizon: int, labels: LabelSource, as_of: datetime) -> bool:
    """Whether the session's horizon-session label window closed by the report's `as_of`.

    Maturity is a property of the **session**, not of whether a record was filed for it: a
    missing LLM day is a gap on a matured position, never a reason to move the window.
    """
    window = labels.window(
        session_publication_instant(session), horizon=parse_horizon(f"{horizon}d")
    )
    return as_of >= session_publication_instant(window.exit_day)


def _session_ic(
    session: date,
    *,
    family: str,
    scoring_set: str,
    horizon: int,
    step0_record: PredictionRecord,
    agent_index: Mapping[tuple[str, str], Mapping[date, PredictionRecord]],
    labels: LabelSource,
    protocol: AgentProtocol,
    book: SurvivorBookConfig,
    as_of: datetime,
) -> SessionIC:
    """One session's partial rank IC for one arm, or the reason it has none."""
    scope = record_scope(step0_record.batch, book=book)
    record = agent_index.get((family, scoring_set), {}).get(session)
    if record is None:
        return SessionIC(session=session, n_scored=0, value=None, reason=GAP_NO_AGENT_RECORD)

    scored = {
        prediction.ts_code: prediction.score
        for prediction in record.batch.predictions
        if prediction.score is not None
    }
    domain = [code for code in scope.survivors if code in scored]
    if not _window_matured(session=session, horizon=horizon, labels=labels, as_of=as_of):
        return SessionIC(session=session, n_scored=len(domain), value=None, reason=GAP_NOT_MATURED)

    window = labels.window(
        session_publication_instant(session), horizon=parse_horizon(f"{horizon}d")
    )
    factor = {
        prediction.ts_code: prediction.score
        for prediction in step0_record.batch.predictions
        if prediction.score is not None
    }
    agent_values: list[float] = []
    factor_values: list[float] = []
    label_values: list[float] = []
    for code in domain:
        if code not in factor:
            continue
        labelled: OutcomeLabel | None = labels.label(code, window)
        if labelled is None or not labelled.is_labelled:
            continue
        # Both dicts were built with `score is not None`, so an abstention never reaches this
        # cross section and is never scored as zero (D3): a zero is a number, and an abstention
        # is not one.
        agent_values.append(scored[code])
        factor_values.append(factor[code])
        label_values.append(labelled.realized_return)
    if len(agent_values) < protocol.min_scored:
        return SessionIC(
            session=session,
            n_scored=len(agent_values),
            value=None,
            reason=GAP_TOO_FEW_SCORED_NAMES,
        )
    value, reason = partial_rank_ic_with_reason(agent_values, factor_values, label_values)
    return SessionIC(session=session, n_scored=len(agent_values), value=value, reason=reason)


def _arm_reading(
    sessions: Sequence[SessionIC], *, family: str, scoring_set: str, horizon: int
) -> ArmReading:
    values = [item.value for item in sessions if item.value is not None]
    missing = sum(1 for item in sessions if item.reason == GAP_NO_AGENT_RECORD)
    return ArmReading(
        family=family,
        scoring_set=scoring_set,
        horizon=horizon,
        positions=len(sessions),
        measurable=len(values),
        missing=missing,
        unmeasured=len(sessions) - len(values) - missing,
        mean=statistics.fmean(values) if values else None,
        test=None,
    )


def _test_of(values: Sequence[float], protocol: AgentProtocol) -> StatisticTest:
    test: RandomizationTest = sign_flip_test(
        tuple(values),
        bootstrap_samples=protocol.bootstrap_samples,
        random_seed=protocol.random_seed,
    )
    mean = statistics.fmean(values)
    one_sided = test.p_value / 2 if mean > 0 else 1 - test.p_value / 2
    return StatisticTest(
        two_sided_p_value=test.p_value,
        one_sided_p_value=one_sided,
        exact=test.exact,
        sign_patterns=test.sign_patterns,
        random_seed=test.random_seed,
    )


def evaluate(
    *,
    protocol: AgentProtocol,
    agent_store: RecordReader,
    step0_store: RecordReader,
    labels: LabelSource,
    book: SurvivorBookConfig,
    registered_sha256: str,
    step0_registered: RegisteredConfiguration,
    anchor_session: date,
    through: date,
    as_of: datetime,
    agent_runtime_dir: Path | None = None,
) -> AgentEvaluation:
    """The registered evaluation over stored records; see the module docstring for every rule.

    `registered_sha256` is C's registration content digest: every agent record of the two C
    families must declare it in its `registration_sha256` hyperparameter. `step0_registered` is
    Step 0's `RegisteredConfiguration`: every Step 0 record the window reads must be bound to it
    and registered before its cutoff. `anchor_session` is C's registration's first forward
    session; the window starts there and is never derived from a store's earliest session.
    `agent_runtime_dir`, when given, is C's runtime directory: a runtime marked as a D7 dry-week
    scratch is refused by name (its evening was never a filed prediction).
    """
    if agent_runtime_dir is not None:
        refuse_a_dry_week_runtime(agent_runtime_dir)
    step0_index: dict[date, PredictionRecord] = {}
    for record_id in step0_store.list_ids():
        record = step0_store.get(record_id)
        if record is None or record.batch.artifact.declaration.family != SURVIVOR_FAMILY:
            continue
        session = _session_of(record)
        if session in step0_index:
            raise AgentEvaluationError(
                f"{session.isoformat()} carries two {SURVIVOR_FAMILY} records; a session has one "
                "stored composite, and the evaluator does not choose between them"
            )
        step0_index[session] = record

    agent_index: dict[tuple[str, str], dict[date, PredictionRecord]] = {}
    for record_id in agent_store.list_ids():
        record = agent_store.get(record_id)
        if record is None:
            continue
        family = record.batch.artifact.declaration.family
        if family not in {AGENT_LLM_FAMILY, AGENT_RULE_FAMILY}:
            continue
        session = _session_of(record)
        # The record must be C's registration's, by the digest the producer wrote into the
        # declaration. A record from a superseded or dry-run registration is refused by name:
        # silently skipping it would leave its session a gap and hide that another
        # registration's answer was in the store.
        declared = dict(record.batch.artifact.declaration.hyperparameters).get(
            REGISTRATION_HYPERPARAMETER
        )
        if not isinstance(declared, str) or not declared.strip():
            raise AgentEvaluationError(
                f"{session.isoformat()} carries a {family} record with no "
                f"{REGISTRATION_HYPERPARAMETER!r} hyperparameter; a record that cannot name the "
                "registration that produced it is not auditable, so the evaluator refuses it "
                "rather than reading it"
            )
        if declared != registered_sha256:
            raise AgentEvaluationError(
                f"{session.isoformat()} carries a {family} record declared under the "
                f"registration {declared}, not this registration's {registered_sha256}; the "
                "evaluator measures what this registration filed and does not read another "
                "registration's answers"
            )
        scoring_set = _scoring_set_of(record)
        if scoring_set is None:
            continue
        held = agent_index.setdefault((family, scoring_set), {})
        if session in held:
            raise AgentEvaluationError(
                f"{session.isoformat()} carries two {family} records on the {scoring_set!r} "
                "scoring set; a family files one record per session and scoring set"
            )
        held[session] = record

    primary_family, primary_set, primary_horizon = protocol.primary_arm()
    # The window's anchor is C's registration's, never the earliest session a store happens to
    # hold: a record filed before the registration (or by another one) must not move the window.
    anchor = anchor_session
    ordered = sorted(session for session in step0_index if anchor <= session <= through)
    positions = ordered[: protocol.window_sessions]
    # Every Step 0 record the statistic reads must be the registered survivor configuration's
    # own: its declaration is `survivor_declaration(book, step0_registered)` whole. A record of
    # another configuration would make the residualisation's factor someone else's number. It
    # must also be on time -- registered before `registration_cutoff`, the call auction that
    # prices the trade it drives -- which is the same pair of checks the evening command makes.
    for session in positions:
        record = step0_index[session]
        if not survivor_record_is_bound(record, book, step0_registered):
            raise AgentEvaluationError(
                f"Step 0's {session.isoformat()} record is not bound to the registered survivor "
                f"configuration {step0_registered.config_id} "
                f"({step0_registered.registration_sha256}); its declaration is another "
                "configuration's, and the evaluator residualises on the registered factor only"
            )
        cutoff = registration_cutoff(labels.calendar, session)
        if not registered_at(record) < cutoff:
            raise AgentEvaluationError(
                f"Step 0's {session.isoformat()} record was registered at "
                f"{registered_at(record).isoformat()}, at or after its cutoff "
                f"{cutoff.isoformat()}; it is not a prediction made before the trade it drives, "
                "and the evaluator reads only an on-time record"
            )
    primary_sessions = tuple(
        _session_ic(
            session,
            family=primary_family,
            scoring_set=primary_set,
            horizon=primary_horizon,
            step0_record=step0_index[session],
            agent_index=agent_index,
            labels=labels,
            protocol=protocol,
            book=book,
            as_of=as_of,
        )
        for session in positions
    )
    primary = _arm_reading(
        primary_sessions,
        family=primary_family,
        scoring_set=primary_set,
        horizon=primary_horizon,
    )

    matured = all(
        _window_matured(session=session, horizon=primary_horizon, labels=labels, as_of=as_of)
        for session in positions
    )
    satisfied = len(positions) == protocol.window_sessions and matured
    # The futility look may only be taken on a complete, measurable interim: every interim
    # position matured, and at least the pro-rated measurability floor of them carrying a value.
    # A thin or immature interim is `not_yet` -- its answer is that nothing can be said yet, not
    # that the trial is futile.
    interim_floor = max(
        MINIMUM_TEST_SAMPLE,
        math.ceil(
            protocol.min_measurable_sessions * protocol.interim_session / protocol.window_sessions
        ),
    )
    interim_positions = positions[: protocol.interim_session]
    interim_values = tuple(
        item.value
        for item in primary_sessions[: protocol.interim_session]
        if item.value is not None
    )
    interim_complete = len(positions) >= protocol.interim_session and all(
        _window_matured(session=session, horizon=primary_horizon, labels=labels, as_of=as_of)
        for session in interim_positions
    )
    outcome: AgentOutcome
    if (
        interim_complete
        and len(interim_values) >= interim_floor
        and statistics.fmean(interim_values) <= 0.0
    ):
        # The stop is sticky: it was decided on the first `interim_session` positions, whose
        # data never changes, so a window that fills or matures later cannot erase it. This
        # branch therefore stands before the window-full ones, not inside the not-full one.
        outcome = "futility_stop"
    elif len(positions) < protocol.window_sessions or not matured:
        outcome = "not_yet"
    elif primary.measurable < protocol.min_measurable_sessions:
        outcome = "not_measurable"
    else:
        values = tuple(item.value for item in primary_sessions if item.value is not None)
        test = _test_of(values, protocol)
        primary = dataclasses.replace(primary, test=test)
        if (
            primary.mean is not None
            and primary.mean > 0
            and test.one_sided_p_value <= protocol.level
        ):
            outcome = "candidate_increment"
        else:
            outcome = "no_increment_detected"

    exploratory: list[ArmReading] = []
    for arm in protocol.secondary:
        arm_sessions = tuple(
            _session_ic(
                session,
                family=arm.family,
                scoring_set=arm.scoring_set,
                horizon=arm.horizon,
                step0_record=step0_index[session],
                agent_index=agent_index,
                labels=labels,
                protocol=protocol,
                book=book,
                as_of=as_of,
            )
            for session in positions
        )
        selected = (
            non_overlapping(
                arm_sessions, horizon=arm.horizon, anchor_ordinal=positions[0].toordinal()
            )
            if positions
            else ()
        )
        reading = _arm_reading(
            selected,
            family=arm.family,
            scoring_set=arm.scoring_set,
            horizon=arm.horizon,
        )
        if satisfied:
            values = tuple(item.value for item in selected if item.value is not None)
            if len(values) >= MINIMUM_TEST_SAMPLE:
                reading = dataclasses.replace(reading, test=_test_of(values, protocol))
        exploratory.append(reading)

    return AgentEvaluation(
        outcome=outcome,
        primary=primary,
        exploratory=_control_exploratory(tuple(exploratory), protocol) if satisfied else (),
        sessions=len(positions),
        measurable=primary.measurable,
        missing=tuple(item for item in primary_sessions if item.reason == GAP_NO_AGENT_RECORD),
        power=_power_of(primary_sessions, protocol),
        adopted=D5_WORDINGS,
    )


def _power_of(sessions: Sequence[SessionIC], protocol: AgentProtocol) -> DetectableIC:
    """The sample size and the detectable mean, always present; see the module docstring.

    The `sd` is the window's own measured dispersion and is **described**, not fed back into a
    test: it is what the detectable mean is stated from, and that mean is a statement about the
    design's resolution. A window whose values are all identical has no dispersion to state a
    resolution from, so its detectable mean is absent rather than zero.
    """
    values = tuple(item.value for item in sessions if item.value is not None)
    sd = statistics.stdev(values) if len(values) >= MINIMUM_TEST_SAMPLE else None
    detectable = (
        detectable_mean(
            sd=sd,
            periods=len(values),
            level=protocol.level,
            power=DETECTABLE_AT_POWER,
        )
        if sd is not None and sd > 0
        else None
    )
    return DetectableIC(
        periods=len(values),
        sd=sd,
        level=protocol.level,
        power=DETECTABLE_AT_POWER,
        mean=detectable,
        basis=DETECTABLE_IC_BASIS,
    )


def _arm_id(family: str, scoring_set: str, horizon: int) -> str:
    """One arm's hypothesis id in the exploratory family: never a date, never a p-value."""
    return f"{family}|{scoring_set}|{horizon}d"


def _control_exploratory(
    readings: Sequence[ArmReading], protocol: AgentProtocol
) -> tuple[ArmReading, ...]:
    """The exploratory arms as one Benjamini-Yekutieli family at `q` (arbitrary dependence).

    The family size is the number of registered arms, not the number that produced a p-value: an
    arm too thin to test is withheld rather than shrinking the denominator, which is the
    conservative reading `backtest.multiple_testing` documents.
    """
    tested = [reading for reading in readings if reading.test is not None]
    if not tested:
        return tuple(readings)
    report = control_false_discovery_rate(
        MultipleTestingRequest(
            tests=tuple(
                HypothesisTest(
                    hypothesis_id=_arm_id(reading.family, reading.scoring_set, reading.horizon),
                    p_value=reading.test.one_sided_p_value if reading.test else 0.0,
                    test="sign_flip_test",
                )
                for reading in tested
            ),
            family_size=len(readings),
            false_discovery_rate=protocol.q,
            dependence="arbitrary",
        )
    )
    verdicts = {verdict.hypothesis_id: verdict for verdict in report.verdicts}
    controlled: list[ArmReading] = []
    for reading in readings:
        verdict = verdicts.get(_arm_id(reading.family, reading.scoring_set, reading.horizon))
        if verdict is None:
            controlled.append(reading)
        else:
            controlled.append(
                dataclasses.replace(reading, q_value=verdict.q_value, rejected=verdict.rejected)
            )
    return tuple(controlled)
