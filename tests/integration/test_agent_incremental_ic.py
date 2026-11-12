"""The incremental-IC evaluator over stored agent and survivor records (`V2-P7-014`).

Plan C Task 2.7. The evaluator measures **what was registered**: the registered primary family's
partial rank IC at the registered horizon, over Step 0's stored survivors, against labels read
through `model_view.OutcomeLabels` at the report's `as_of`; the exploratory arms are reported
separately and Benjamini-Yekutieli-controlled, and are never pooled into the primary statistic.

Every prediction record here is constructed in the test, not read from a research store, and the
labels are either a table standing in for the shared reader or -- in the one wiring test -- the
real `OutcomeLabels` over a panel generated at test time (`AGENTS.md` hard rule 6). No network, no
LLM, no real data.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final
from zoneinfo import ZoneInfo

import grimp
import pytest
from panel_fixtures import (
    AS_OF as PANEL_AS_OF,
)
from panel_fixtures import (
    EXCHANGE as PANEL_EXCHANGE,
)
from panel_fixtures import (
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.agent_forward import AGENT_LLM_FAMILY, AGENT_RULE_FAMILY
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.incremental_ic import detectable_mean, partial_rank_ic
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.model_view import LabelReach, OutcomeLabels
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_agent_evaluation import (
    AGENT_FORWARD_PROTOCOL_KEY,
    AGENT_FORWARD_PROTOCOL_SCHEMA,
    GAP_NO_AGENT_RECORD,
    NOT_MEASURABLE_WORDING,
    AgentEvaluation,
    AgentEvaluationError,
    AgentProtocol,
    evaluate,
    read_agent_protocol,
)
from openalpha_cn.strategy_survivor_registration import SURVIVOR_FAMILY

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

SESSIONS: Final[tuple[date, ...]] = (
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
    date(2026, 1, 12),
    date(2026, 1, 13),
    date(2026, 1, 14),
    date(2026, 1, 15),
    date(2026, 1, 16),
)
"""Ten weekday sessions, so an h=5 position exists inside the nine the evaluator reads."""
THROUGH: Final[date] = SESSIONS[5]
AS_OF: Final[datetime] = datetime(2026, 1, 17, 12, 0, tzinfo=SHANGHAI)
"""Noon the day after the last session: every h=1 window opened up to `THROUGH` has matured."""

PROTOCOL_BLOCK: Final[Mapping[str, object]] = {
    "schema_version": AGENT_FORWARD_PROTOCOL_SCHEMA,
    "primary_family": AGENT_LLM_FAMILY,
    "primary_set": "primary",
    "primary_horizon": 1,
    "secondary": [
        {"family": AGENT_RULE_FAMILY, "scoring_set": "coverage", "horizon": 1},
    ],
    "window_sessions": 6,
    "min_scored": 4,
    "min_measurable_sessions": 4,
    "interim_session": 2,
    "level": 0.05,
    "q": 0.1,
    "bootstrap_samples": 1000,
    "random_seed": 11,
}
"""Test values, not the registration's: the protocol is read, never defaulted."""


def protocol(**changes: object) -> AgentProtocol:
    """The test protocol, with `changes` applied to its settings block."""
    block: dict[str, object] = {**PROTOCOL_BLOCK, **changes}
    if "secondary" in changes and not isinstance(changes["secondary"], list):
        block["secondary"] = changes["secondary"]
    return read_agent_protocol({AGENT_FORWARD_PROTOCOL_KEY: block})


def test_the_evaluator_reaches_no_model_or_agent_module() -> None:
    """The evaluator reads stored records and the shared label reader; never a model or an agent.

    Plan Task 2.7's review rule: a report that could run an agent or call a model is not a
    measurement of records that were filed before the outcome. `grimp`'s upstream closure is the
    module's import reach, so an agent or a model anywhere in it fails here.
    """
    graph = grimp.build_graph("openalpha_cn", cache_dir=None)
    upstream = graph.find_upstream_modules("openalpha_cn.strategy_agent_evaluation")

    forbidden = ("openalpha_cn.agents", "openalpha_cn.models")
    reached = upstream & set(forbidden)
    assert not reached, f"strategy_agent_evaluation reaches {sorted(reached)}"
    direct = graph.find_modules_directly_imported_by("openalpha_cn.strategy_agent_evaluation")
    assert not any(module.startswith(forbidden) for module in direct)


# --- the protocol ----------------------------------------------------------------------------


def test_the_protocol_is_read_from_the_settings_block_with_no_defaults() -> None:
    """`read_agent_protocol` takes every field from the registration's `settings`, or refuses.

    A default here would be a decision taken by code rather than registered (D5's whole point),
    so a block missing any key is refused by name, and an unknown key is refused.
    """
    read = read_agent_protocol({AGENT_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)})

    assert read.primary_family == AGENT_LLM_FAMILY
    assert read.primary_set == "primary"
    assert read.primary_horizon == 1
    assert [(arm.family, arm.scoring_set, arm.horizon) for arm in read.secondary] == [
        (AGENT_RULE_FAMILY, "coverage", 1)
    ]
    assert read.window_sessions == 6
    assert read.min_scored == 4
    assert read.min_measurable_sessions == 4
    assert read.interim_session == 2
    assert (read.level, read.q, read.bootstrap_samples, read.random_seed) == (0.05, 0.1, 1000, 11)


@pytest.mark.parametrize("key", sorted(PROTOCOL_BLOCK))
def test_a_block_missing_any_key_is_refused_by_name(key: str) -> None:
    block = {name: value for name, value in PROTOCOL_BLOCK.items() if name != key}

    with pytest.raises(AgentEvaluationError, match=key):
        read_agent_protocol({AGENT_FORWARD_PROTOCOL_KEY: block})


def test_settings_without_a_protocol_block_are_refused_by_name() -> None:
    for settings in ({}, {AGENT_FORWARD_PROTOCOL_KEY: None}, {AGENT_FORWARD_PROTOCOL_KEY: [1]}):
        with pytest.raises(AgentEvaluationError, match=AGENT_FORWARD_PROTOCOL_KEY):
            read_agent_protocol(settings)


def test_the_primary_arm_is_pinned_to_the_registered_llm_primary_set() -> None:
    """D2: the ONE primary statistic is the LLM family at h=1 on the primary set.

    The rule family, the coverage set and every other horizon are permanently exploratory, so a
    block that names another primary arm is refused rather than quietly tested as if it were the
    registered one.
    """
    with pytest.raises(AgentEvaluationError, match="primary_family"):
        protocol(primary_family=AGENT_RULE_FAMILY)
    with pytest.raises(AgentEvaluationError, match="primary_set"):
        protocol(primary_set="coverage")
    with pytest.raises(AgentEvaluationError, match="secondary"):
        protocol(
            secondary=[
                {"family": AGENT_LLM_FAMILY, "scoring_set": "primary", "horizon": 1},
            ]
        )


def test_the_primary_horizon_is_pinned_to_one_session() -> None:
    """D2's h=1 is part of the ONE statistic: h=5 is exploratory only.

    The registered record's horizon is 5d and one record is read at both h=1 and h=5, so a block
    that names `primary_horizon: 5` would make the primary statistic the overlapping reading and
    leave the h=1 read unreported. Both the settings reader and the public dataclass refuse it.
    """
    with pytest.raises(AgentEvaluationError, match="primary_horizon"):
        protocol(primary_horizon=5)

    read = read_agent_protocol({AGENT_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)})
    with pytest.raises(AgentEvaluationError, match="primary_horizon"):
        AgentProtocol(
            primary_family=read.primary_family,
            primary_set=read.primary_set,
            primary_horizon=5,
            secondary=read.secondary,
            window_sessions=read.window_sessions,
            min_scored=read.min_scored,
            min_measurable_sessions=read.min_measurable_sessions,
            interim_session=read.interim_session,
            level=read.level,
            q=read.q,
            bootstrap_samples=read.bootstrap_samples,
            random_seed=read.random_seed,
        )
    # h=5 stays legal where D2 puts it: an exploratory arm.
    assert (
        protocol(secondary=[{"family": AGENT_RULE_FAMILY, "scoring_set": "coverage", "horizon": 5}])
        .secondary[0]
        .horizon
        == 5
    )


# --- the stored records, the labels, and the evaluator ---------------------------------------

SECURITIES: Final[tuple[str, ...]] = (
    "600001.SH",
    "600002.SH",
    "600003.SH",
    "600004.SH",
    "600005.SH",
    "600006.SH",
    "600007.SH",
    "600008.SH",
)
STEP0_SCORES: Final[Mapping[str, float]] = {
    code: float(index) for index, code in enumerate(SECURITIES)
}
"""Eight names scored 0..7, so `record_scope` drops the single worst decile (one name)."""
SURVIVORS: Final[tuple[str, ...]] = tuple(sorted(SECURITIES[1:]))
"""The seven survivors, best first: every name of `SECURITIES` but the score-0 one."""
BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=7)


def return_of(ts_code: str, session: date) -> float:
    """A label table: a permutation of the eight names' factor order, and varying by session.

    The permutation is load-bearing: Step 0's score orders the names by index, so a label that
    ordered them the same way would be perfectly explained by the factor and leave no residual
    for the partial IC to measure. `(index * 5) % 8` visits all eight indices, so each name's
    return is distinct and the label's order is the factor's order transposed.
    """
    return ((SECURITIES.index(ts_code) * 5) % 8) / 100.0 + (SESSIONS.index(session) % 3) / 1000.0


class FakeLabels:
    """A stand-in for `model_view.OutcomeLabels`: the same structural `window`/`label` reader.

    The one wiring test at the end of this file drives the real `OutcomeLabels` over a generated
    panel; every arithmetic test here answers from a table so it can plant an increment exactly.
    """

    def __init__(self, *, horizon_sessions: bool = True) -> None:
        self._horizon_sessions = horizon_sessions

    def window(self, instant: datetime, *, horizon: Any) -> Any:
        session = instant.astimezone(SHANGHAI).date()
        index = SESSIONS.index(session)
        exit_index = index + 1 + horizon.sessions
        exit_day = SESSIONS[exit_index] if exit_index < len(SESSIONS) else date(2027, 1, 1)
        return SimpleNamespace(session=session, exit_day=exit_day)

    def label(self, ts_code: str, window: Any) -> Any:
        if ts_code not in SECURITIES:
            return None
        return SimpleNamespace(is_labelled=True, realized_return=return_of(ts_code, window.session))


class FakeStore:
    """A stored-record reader with the two methods both stores are read through."""

    def __init__(self, records: Sequence[PredictionRecord]) -> None:
        self._records = {f"record-{index:03d}": record for index, record in enumerate(records)}

    def list_ids(self) -> tuple[str, ...]:
        return tuple(self._records)

    def get(self, record_id: str) -> PredictionRecord | None:
        return self._records.get(record_id)


def record_for(
    *, session: date, scores: Mapping[str, float], family: str, scoring_set: str | None
) -> PredictionRecord:
    """One stored batch as a `PredictionRecord`, with no store and no panel behind it."""
    as_of = session_publication_instant(session)
    hyperparameters: tuple[tuple[str, Any], ...] = ()
    if scoring_set is not None:
        hyperparameters = (("scoring_set", scoring_set),)
    declaration = AlphaModelDeclaration(
        name="c-primary" if scoring_set == "primary" else f"c-{scoring_set or 'step0'}",
        family=family,
        horizon="5d" if scoring_set is not None else "20d",
        feature_version="test-config",
        seed=7,
        code_commit="d2b74f7",
        hyperparameters=hyperparameters,
    )
    batch = PredictionBatch(
        as_of=as_of,
        predicted_at=as_of,
        artifact=AlphaModelArtifact(
            declaration=declaration,
            feature_ids=("test/v1",),
            training_cutoff=as_of,
            training_example_count=1,
        ),
        predictions=tuple(
            Prediction(ts_code=code, score=score) for code, score in sorted(scores.items())
        ),
    )
    return PredictionRecord(
        batch=batch,
        outcome_known_at=as_of + timedelta(days=3),
        recorded_at=as_of,
    )


def step0_records(sessions: Sequence[date] = SESSIONS) -> tuple[PredictionRecord, ...]:
    return tuple(
        record_for(session=session, scores=STEP0_SCORES, family=SURVIVOR_FAMILY, scoring_set=None)
        for session in sessions
    )


def agent_records(
    session_scores: Mapping[date, Mapping[str, float]],
    *,
    family: str = AGENT_LLM_FAMILY,
    scoring_set: str = "primary",
) -> tuple[PredictionRecord, ...]:
    return tuple(
        record_for(
            session=session,
            scores=scores,
            family=family,
            scoring_set=scoring_set,
        )
        for session, scores in sorted(session_scores.items())
    )


def planted_scores(
    sessions: Sequence[date], *, include_outside_survivors: bool = False
) -> dict[date, dict[str, float]]:
    """The agent scores a real increment would produce: on the label's order, among survivors."""
    planted: dict[date, dict[str, float]] = {}
    for session in sessions:
        scores = {code: return_of(code, session) for code in SURVIVORS}
        if include_outside_survivors:
            dropped = SECURITIES[0]
            scores[dropped] = -return_of(dropped, session)
        planted[session] = scores
    return planted


def independent_scores(
    sessions: Sequence[date], *, constant: int = 0
) -> dict[date, dict[str, float]]:
    """Agent scores with no relation to the label: the null the evaluator must not detect."""
    return {
        session: {
            code: float((SECURITIES.index(code) * 5 + SESSIONS.index(session) * 3) % 7)
            for code in SURVIVORS
        }
        for session in sessions
    }


def evaluate_with(
    *records: Sequence[PredictionRecord],
    step0: Sequence[PredictionRecord] | None = None,
    **changes: object,
) -> AgentEvaluation:
    agent = [record for group in records if group is not None for record in group]
    return evaluate(
        protocol=protocol(**changes) if changes else protocol(),
        agent_store=FakeStore(agent),
        step0_store=FakeStore(step0_records() if step0 is None else step0),
        labels=FakeLabels(),
        book=BOOK,
        through=THROUGH,
        as_of=AS_OF,
    )


def test_known_increment_is_detected_and_none_is_not() -> None:
    """A planted increment passes the registered rule; an unrelated score does not.

    The planted store has each session's primary partial rank IC at 1.0, so the window's mean is
    1.0 and the sign-flip p is far below the level. The independent store's values average near
    zero and its one-sided p is above the level, so the outcome is `no_increment_detected` -- not
    `not_measurable`, which is a different finding.
    """
    detected = evaluate_with(agent_records(planted_scores(SESSIONS[:6])))
    assert detected.outcome == "candidate_increment"
    assert detected.primary.mean == pytest.approx(1.0)
    assert detected.primary.test is not None
    assert detected.primary.test.one_sided_p_value <= 0.05
    assert detected.measurable == 6

    nothing = evaluate_with(agent_records(independent_scores(SESSIONS[:6])))
    assert nothing.outcome == "no_increment_detected"
    assert nothing.primary.test is not None
    assert nothing.primary.test.one_sided_p_value > 0.05


def test_names_outside_survivors_are_excluded() -> None:
    """The domain is Step 0's survivors, and the score-0 name the agent answered is not one.

    The agent record carries the dropped name with a score pointing the other way, so including
    it would move the statistic. The window's mean is held to the partial rank IC computed
    directly on survivors -- not to a value read back from the evaluator.
    """
    evaluation = evaluate_with(
        agent_records(planted_scores(SESSIONS[:6], include_outside_survivors=True))
    )

    expected = [
        partial_rank_ic(
            [return_of(code, session) for code in SURVIVORS],
            [STEP0_SCORES[code] for code in SURVIVORS],
            [return_of(code, session) for code in SURVIVORS],
        )
        for session in SESSIONS[:6]
    ]
    assert all(value == pytest.approx(1.0) for value in expected)
    assert evaluation.primary.mean == pytest.approx(statistics.fmean(expected))  # type: ignore[arg-type]
    assert evaluation.measurable == 6


def test_before_the_window_only_counts_and_descriptive_means_no_p_value() -> None:
    """Two of six positions: the report may describe them and may not test them.

    A pre-registered window that has not filled yet gets no p-value and no outcome word beyond
    `not_yet` -- the mean is a point estimate, exactly as the survivor forward layer's
    `DESCRIPTIVE_WORDING` says of every number before its window.
    """
    evaluation = evaluate_with(
        agent_records(planted_scores(SESSIONS[:2])),
        step0=step0_records(SESSIONS[:2]),
    )

    assert evaluation.outcome == "not_yet"
    assert evaluation.sessions == 2
    assert evaluation.primary.measurable == 2
    assert evaluation.primary.mean == pytest.approx(1.0)
    assert evaluation.primary.test is None, "a p-value before the window would be a second look"


def test_too_few_measurable_sessions_is_not_measurable_not_no_evidence() -> None:
    """A complete, matured window with two measurable positions is `not_measurable`.

    The floor is the registration's, and the outcome is its own category: `no_increment_detected`
    would claim a test was made, and 'no evidence' would claim the opposite of what two values
    can say.
    """
    evaluation = evaluate_with(
        agent_records(planted_scores(SESSIONS[:2])),
        step0=step0_records(SESSIONS[:6]),
    )

    assert evaluation.outcome == "not_measurable"
    assert evaluation.measurable == 2
    assert evaluation.primary.measurable < evaluation.sessions
    assert evaluation.primary.test is None
    assert "not measurable" in NOT_MEASURABLE_WORDING
    assert "no evidence" in NOT_MEASURABLE_WORDING, (
        "the wording names the reading it refuses, so a report cannot upgrade it"
    )


def test_a_missing_llm_day_is_a_gap_that_reduces_n_without_moving_the_window() -> None:
    """The window is Step 0's stored sessions; a day the LLM filed nothing is position two's gap.

    The later positions stay where they were -- the sample is a property of Step 0's calendar,
    not of which days the model answered -- so `sessions` is unchanged, `measurable` is one
    smaller, and the missing session is named with its own reason.
    """
    gap = SESSIONS[2]
    incomplete = {
        session: scores
        for session, scores in planted_scores(SESSIONS[:6]).items()
        if session != gap
    }

    gapped = evaluate_with(agent_records(incomplete), step0=step0_records(SESSIONS[:6]))
    complete = evaluate_with(
        agent_records(planted_scores(SESSIONS[:6])), step0=step0_records(SESSIONS[:6])
    )

    assert gapped.sessions == complete.sessions == 6, "the window did not move"
    assert gapped.measurable == complete.measurable - 1
    assert [item.session for item in gapped.missing] == [gap]
    assert gapped.missing[0].reason == GAP_NO_AGENT_RECORD
    assert gapped.outcome == "candidate_increment", (
        "a gap reduces n; it does not cancel the window's test"
    )


def test_a_non_positive_cumulative_mean_at_the_interim_stops_as_futility() -> None:
    """D5: at the interim look, a cumulative mean at or below zero stops 'futility'.

    The agent here is the anti-signal -- its per-session partial IC is -1.0 -- so the interim
    mean is below zero and the reason for stopping is a statement about the design's futility,
    not a negative result at the window.
    """
    anti = {
        session: {code: -return_of(code, session) for code in SURVIVORS} for session in SESSIONS[:3]
    }
    evaluation = evaluate_with(agent_records(anti), step0=step0_records(SESSIONS[:3]))

    assert evaluation.sessions == 3
    assert evaluation.primary.mean == pytest.approx(-1.0)
    assert evaluation.outcome == "futility_stop"
    assert evaluation.primary.test is None


def test_coverage_set_is_reported_separately_never_pooled() -> None:
    """Two exploratory arms, their own readings and BY verdicts, and the primary untouched.

    The rule family's coverage record is an anti-signal here (its partial IC is -1.0), so a
    pooled mean would be zero: the primary reading staying at 1.0 is the measurement that the
    two sets were not pooled. The h=5 arm is read on **every fifth session from the window's
    anchor** -- the non-overlapping rule D5 names -- which is two positions of the six.
    """
    coverage = {
        session: {code: -return_of(code, session) for code in SURVIVORS} for session in SESSIONS[:6]
    }
    baseline = evaluate_with(agent_records(planted_scores(SESSIONS[:6])))
    with_coverage = evaluate_with(
        agent_records(planted_scores(SESSIONS[:6])),
        agent_records(coverage, family=AGENT_RULE_FAMILY, scoring_set="coverage"),
        agent_records(
            planted_scores(SESSIONS[:6]), family=AGENT_RULE_FAMILY, scoring_set="primary"
        ),
        secondary=[
            {"family": AGENT_RULE_FAMILY, "scoring_set": "coverage", "horizon": 1},
            {"family": AGENT_RULE_FAMILY, "scoring_set": "primary", "horizon": 5},
        ],
    )

    assert with_coverage.outcome == baseline.outcome == "candidate_increment"
    assert with_coverage.primary.scoring_set == "primary"
    assert with_coverage.primary.mean == pytest.approx(1.0)
    assert with_coverage.primary.mean == baseline.primary.mean
    assert with_coverage.primary.test is not None and baseline.primary.test is not None
    assert (
        with_coverage.primary.test.one_sided_p_value == baseline.primary.test.one_sided_p_value
    ), "an exploratory arm may not move the primary statistic"

    arms = {(arm.scoring_set, arm.horizon): arm for arm in with_coverage.exploratory}
    assert set(arms) == {("coverage", 1), ("primary", 5)}
    coverage_arm = arms[("coverage", 1)]
    assert coverage_arm.family == AGENT_RULE_FAMILY
    assert coverage_arm.positions == 6
    assert coverage_arm.mean == pytest.approx(-1.0)
    assert coverage_arm.q_value is not None
    assert coverage_arm.rejected is False, (
        "the registered direction is mean > 0; an anti-signal is reported and never a discovery"
    )

    fifth = arms[("primary", 5)]
    assert fifth.positions == 2, "h=5 takes every fifth session from the anchor"
    assert fifth.measurable == 1, "the last window position's h=5 window is not matured yet"
    assert fifth.test is None


def test_sample_size_and_detectable_ic_are_always_printed() -> None:
    """`n` and the mean detectable at 0.8 are on the answer before and after the window.

    The detectable mean is the survivor power block's formula (`domain.incremental_ic`), applied
    to the window's measured dispersion and stated as a property of this design; it is not
    observed power, which is a monotone function of the p-value and says nothing new.
    """
    evaluation = evaluate_with(agent_records(independent_scores(SESSIONS[:6])))

    assert evaluation.power.periods == evaluation.measurable
    assert evaluation.power.sd is not None and evaluation.power.sd > 0
    assert evaluation.power.mean is not None
    expected = detectable_mean(
        sd=evaluation.power.sd,
        periods=evaluation.power.periods,
        level=0.05,
        power=0.8,
    )
    assert evaluation.power.mean == pytest.approx(expected)
    assert "design" in evaluation.power.basis
    assert "observed power" in evaluation.power.basis

    # two positions with a positive, non-identical pair of values: positive so the interim look
    # does not stop, non-identical so the design has a dispersion to state a resolution from
    before_scores: dict[date, dict[str, float]] = {}
    for index, session in enumerate(SESSIONS[:2]):
        scores = {code: return_of(code, session) for code in SURVIVORS}
        if index == 0:
            first, second = SURVIVORS[0], SURVIVORS[1]
            scores[first], scores[second] = scores[second], scores[first]
        before_scores[session] = scores
    before = evaluate_with(
        agent_records(before_scores),
        step0=step0_records(SESSIONS[:2]),
    )
    assert before.outcome == "not_yet"
    assert before.power.periods == 2, "sample size is printed before the window too"
    assert before.power.mean is not None


def test_the_evaluator_reads_labels_through_the_shared_outcome_labels_reader(
    tmp_path: Path,
) -> None:
    """The wiring: a panel generated at test time, `model_view.OutcomeLabels`, two real sessions.

    Every arithmetic test above stands in for the label reader; this one drives the real shared
    reader over a generated panel (hard rule 6) so the evaluator's Protocol is held to the class
    the plan names. The panel prices move between sessions (`daily.close_moves_between_sessions`),
    so the labels are not all equal and the statistic is defined.
    """
    panel = generate_panel(shapes=("daily.close_moves_between_sessions",))
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(
        store,
        panel,
        datasets=(
            "trade_cal",
            "stock_basic",
            "adj_factor",
            "daily",
            "daily_basic",
            "stk_limit",
            "suspend_d",
        ),
    )
    sessions = panel.sessions[:2]
    codes = panel.securities
    labels = OutcomeLabels(
        store, LabelReach(as_of=PANEL_AS_OF, years=(2026,), exchange=PANEL_EXCHANGE)
    )
    evaluation = evaluate(
        protocol=protocol(window_sessions=2, min_measurable_sessions=2, interim_session=1),
        agent_store=FakeStore(
            agent_records(
                {
                    session: {
                        code: float((codes.index(code) * 3 + sessions.index(session)) % 7)
                        for code in codes
                    }
                    for session in sessions
                }
            )
        ),
        step0_store=FakeStore(
            tuple(
                record_for(
                    session=session,
                    scores={code: float((codes.index(code) * 5) % 8) for code in codes},
                    family=SURVIVOR_FAMILY,
                    scoring_set=None,
                )
                for session in sessions
            )
        ),
        labels=labels,
        book=BOOK,
        through=sessions[-1],
        as_of=PANEL_AS_OF,
    )

    assert evaluation.sessions == 2
    assert evaluation.measurable == 2, "both labels were read and both cross sections are defined"
    assert evaluation.primary.mean is not None
    assert evaluation.outcome in {"candidate_increment", "no_increment_detected"}
