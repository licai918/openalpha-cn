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

## The window, the gaps and the test

The protocol has no anchor field, so the window is derived from what is stored: **the positions
are the first `window_sessions` sessions Step 0 has filed at or after the agent store's first
filed session and at or before `through`, in ascending order.** Step 0 files a record every
session, so a day the LLM family filed nothing is a position with a gap: it reduces `n` and never
moves the later positions inward -- the window is a property of Step 0's stored sessions, not of
which days the LLM answered. A session whose label window has not closed by `as_of` is not tested
(`not_yet`), and the primary test is made **once**, when every position has matured.

"Not measurable" (D3/D5) is a distinct outcome and never "no evidence": a session is measurable
when at least `min_scored` names survive the join of the two records and the label, and the
window is `not_measurable` when fewer than `min_measurable_sessions` of its positions are. The
futility look stops at `interim_session`: a cumulative mean at or below zero over the first
`interim_session` positions of an incomplete window is `futility_stop`.

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
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
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
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_registration import SHANGHAI
from openalpha_cn.strategy_survivor_forward import record_scope
from openalpha_cn.strategy_survivor_registration import SURVIVOR_FAMILY

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

DETECTABLE_IC_BASIS: Final = (
    "the mean detectable at probability DETECTABLE_AT_POWER from the window's measured "
    "dispersion, at the protocol's one-sided level; a property of the design, never observed "
    "power and never a result"
)
"""What every printed detectable mean is: the survivor power block's sentence, one plane over."""

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
    report is a measurement of records rather than a rendering.
    """

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
        agent_values.append(scored[code] if scored[code] is not None else 0.0)
        factor_values.append(factor[code] if factor[code] is not None else 0.0)
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


def _empty_reading(family: str, scoring_set: str, horizon: int) -> ArmReading:
    return _arm_reading((), family=family, scoring_set=scoring_set, horizon=horizon)


def evaluate(
    *,
    protocol: AgentProtocol,
    agent_store: RecordReader,
    step0_store: RecordReader,
    labels: LabelSource,
    book: SurvivorBookConfig,
    through: date,
    as_of: datetime,
) -> AgentEvaluation:
    """The registered evaluation over stored records; see the module docstring for every rule."""
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
    agent_sessions: list[date] = []
    for record_id in agent_store.list_ids():
        record = agent_store.get(record_id)
        if record is None:
            continue
        family = record.batch.artifact.declaration.family
        scoring_set = _scoring_set_of(record)
        if family not in {AGENT_LLM_FAMILY, AGENT_RULE_FAMILY} or scoring_set is None:
            continue
        session = _session_of(record)
        held = agent_index.setdefault((family, scoring_set), {})
        if session in held:
            raise AgentEvaluationError(
                f"{session.isoformat()} carries two {family} records on the {scoring_set!r} "
                "scoring set; a family files one record per session and scoring set"
            )
        held[session] = record
        agent_sessions.append(session)

    primary_family, primary_set, primary_horizon = protocol.primary_arm()
    primary_empty = _empty_reading(primary_family, primary_set, primary_horizon)
    if not agent_sessions:
        return AgentEvaluation(
            outcome="not_yet",
            primary=primary_empty,
            exploratory=(),
            sessions=0,
            measurable=0,
            missing=(),
            power=_power_of((), protocol),
        )

    anchor = min(agent_sessions)
    ordered = sorted(session for session in step0_index if anchor <= session <= through)
    positions = ordered[: protocol.window_sessions]
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
    outcome: AgentOutcome
    if len(positions) < protocol.window_sessions:
        # The window is not full yet. The futility look is the one decision D5 makes before it
        # is: a cumulative mean at or below zero over the interim positions stops the trial.
        outcome = "not_yet"
        if len(positions) >= protocol.interim_session:
            interim = tuple(
                session.value
                for session in primary_sessions[: protocol.interim_session]
                if session.value is not None
            )
            if interim and statistics.fmean(interim) <= 0.0:
                outcome = "futility_stop"
    elif not matured:
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
