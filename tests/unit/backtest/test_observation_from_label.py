"""The bridge from a label to an `OutcomeObservation` (`V2-P1-017`).

`OutcomeValidator.validate` computes `observation.end_price / observation.start_price - 1`, and
`OutcomeObservation` takes those two as free positive floats. That expression is **correct** if
and only if both prices are on one adjustment scale, and it is the measured wrong path --
`-0.530973%` where the truth is `+2.742230%` -- if they are two raw closes across an ex-rights
day. Nothing in the observation says which it was given.

`observation_from_label` is the constructor that can answer for its prices: it takes them from
`WindowReturn`'s 后复权 closes, so the validator's own arithmetic reproduces the label exactly.
These tests pin that identity rather than the resemblance -- `==`, not `approx`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.backtest.validation import (
    LABEL_PROVENANCE_NOTE,
    OutcomeObservation,
    OutcomeValidator,
    observation_from_label,
)
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    LabelError,
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import SecurityLifecycle, StockUniverse
from openalpha_cn.domain.time import Timeline
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

CODE = "000001.SZ"
SHANGHAI = ZoneInfo("Asia/Shanghai")
ENTRY = date(2026, 6, 11)
EXIT = date(2026, 6, 12)
ADJUSTED_RETURN = 0.027422506154573423
UNADJUSTED_RETURN = -0.005309734513274433


def _bar(day: date, close: float, pre_close: float) -> DailyBar:
    return DailyBar(
        ts_code=CODE,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=10000.0,
    )


def _label(*, halted: bool = False) -> OutcomeLabel:
    calendar = build_trading_calendar(
        "SZSE",
        [
            CalendarDay(calendar_date=date(2026, 6, day), is_trading=day not in (6, 7, 13, 14))
            for day in range(1, 16)
        ],
    )
    window = build_label_window(
        as_of=datetime(2026, 6, 10, 8, 30, tzinfo=UTC),
        zone=SHANGHAI,
        horizon=parse_horizon("1d"),
        calendar=calendar,
    )
    bars = {ENTRY: _bar(ENTRY, 11.30, 11.32), EXIT: _bar(EXIT, 11.24, 10.94)}
    if halted:
        del bars[EXIT]
    return label_outcome(
        window,
        ts_code=CODE,
        bars=bars,
        factors=build_adjustment_history(
            CODE,
            [
                FactorObservation(ts_code=CODE, observed_on=ENTRY, factor=134.5794),
                FactorObservation(ts_code=CODE, observed_on=EXIT, factor=139.008),
            ],
        ),
        limits={
            day: PriceLimit(ts_code=CODE, trade_date=day, up_limit=1000.0, down_limit=0.01)
            for day in window.sessions
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=StockUniverse(
            snapshot_date=date(2026, 6, 30),
            securities=(
                SecurityLifecycle(ts_code=CODE, exchange="SZSE", listed_on=date(1991, 4, 3)),
            ),
        ),
    )


def _research(tmp_path: Path, now: datetime) -> ResearchRunResult:
    """A real `run_cycle` result, so the validator below is the real one.

    Rebuilt here rather than imported from `tests/unit/backtest/test_validation.py`, whose
    `research_result` fixture is local to that file. The `limit_up` evidence is what makes the
    decision come out `"watch"`, which is the branch that computes a realized return at all.
    """
    evidence = EvidenceSnapshot(
        subject=CODE,
        kind="limit_up",
        timeline=Timeline(event_time=now, available_time=now, ingested_time=now, revision_time=now),
        source_id="synthetic",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit-up.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.0, "pct_change": 10.0, "board_count": 1},
            "quality_flags": [],
        },
    )
    return ResearchEngine(
        repository=SQLiteRunRepository(tmp_path / "state.sqlite3"),
        memory=InMemoryResearchMemory(),
        clock=lambda: now,
        recovery_store=SQLiteRecoveryStore(tmp_path / "state.sqlite3"),
    ).run_cycle(
        ResearchRunRequest(
            run_id="run_label_bridge",
            mode="backtest",
            subject=CODE,
            as_of=now,
            evidence=(evidence,),
            code_commit="0123456789abcdef",
            config_digest="c" * 64,
            random_seed=7,
        )
    )


def test_the_validators_own_arithmetic_reproduces_the_label_exactly() -> None:
    """`end_price / start_price - 1` is what `OutcomeValidator` runs. On this observation it is
    the label's number to the last bit, and on the raw closes it would be `-0.53%`.
    """
    observation = observation_from_label(_label(), benchmark_return=0.01, transaction_cost=0.002)

    assert observation.end_price / observation.start_price - 1 == ADJUSTED_RETURN
    assert observation.end_price / observation.start_price - 1 != pytest.approx(UNADJUSTED_RETURN)


def test_running_the_real_validator_on_this_observation_returns_the_labels_own_number(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The identity above, asserted through `OutcomeValidator.validate` rather than beside it.

    The test before this one restates the validator's expression instead of calling it, so a
    validator that dropped the `- 1`, or squared the ratio, or read the prices in the other
    order would leave it green. This one runs the real method on the real observation and
    compares its `realized_return` against `OutcomeLabel.realized_return` -- the two numbers
    the whole bridge exists to make equal -- and against the stored `ValidationResult`'s own
    reconciliation of the attribution.
    """
    label = _label()
    result = OutcomeValidator().validate(
        research=_research(tmp_path, frozen_now),
        observation=observation_from_label(label, benchmark_return=0.01, transaction_cost=0.002),
    )

    assert result.realized_return == label.realized_return == ADJUSTED_RETURN
    assert result.net_active_return == pytest.approx(ADJUSTED_RETURN - 0.012, abs=1e-15)
    # The terms alone reconcile to nothing: since `V2-P5-005` they claim only the measured
    # cost, and the label's own move -- `realized - benchmark` -- is carried as the residual
    # instead of being split 20/30/50. Summing the terms *without* the residual was the
    # tautology that issue deleted, so it is the whole equation that is asserted here.
    assert sum(item.contribution for item in result.attribution) == -0.002
    assert result.unexplained_return == ADJUSTED_RETURN - 0.01
    assert sum(item.contribution for item in result.attribution) + result.unexplained_return == (
        pytest.approx(result.net_active_return, abs=1e-15)
    )
    assert result.observation_start == datetime(2026, 6, 11, 7, 0, tzinfo=UTC)


def test_a_decision_that_took_no_position_reports_zero_beside_a_note_describing_a_move(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """Recorded because it reads as a contradiction and is not one.

    `OutcomeValidator.validate` computes a realized return only for `"watch"`: `"avoid"` and
    `"abstain"` took no position, so nothing was realized and `0.0` is the position's return.
    The provenance note travels from the label regardless and still describes the security
    moving +2.74%, so a stored `ValidationResult` can carry `realized_return=0.0` next to a
    note quoting a non-zero number. Both are right and they answer different questions -- what
    the decision earned, and what the security did -- which is why `V2-P1-017` deliberately did
    not merge them.
    """
    research = _research(tmp_path, frozen_now)
    observation = observation_from_label(_label(), benchmark_return=0.0, transaction_cost=0.0)
    abstained = research.model_copy(
        update={"decision": research.decision.model_copy(update={"final_action": "abstain"})}
    )

    result = OutcomeValidator().validate(research=abstained, observation=observation)

    assert research.decision.final_action == "watch"
    assert result.realized_return == 0.0
    assert "0.027422506154573423" in result.data_quality_notes[0]


def test_the_two_prices_are_the_backward_adjusted_closes_and_not_the_raw_ones() -> None:
    observation = observation_from_label(_label(), benchmark_return=0.0, transaction_cost=0.0)

    assert observation.start_price == 11.30 * 134.5794
    assert observation.end_price == 11.24 * 139.008


def test_the_window_is_dated_at_the_two_session_closes_in_the_exchanges_timezone() -> None:
    observation = observation_from_label(_label(), benchmark_return=0.0, transaction_cost=0.0)

    assert observation.observation_start == datetime(2026, 6, 11, 7, 0, tzinfo=UTC)
    assert observation.observation_end == datetime(2026, 6, 12, 7, 0, tzinfo=UTC)


def test_the_provenance_of_the_two_prices_travels_with_the_observation() -> None:
    """`data_quality_notes` is persisted on `ValidationResult`, so the note is what a stored
    result carries to say which of the three return paths its two prices came from.
    """
    observation = observation_from_label(
        _label(),
        benchmark_return=0.0,
        transaction_cost=0.0,
        data_quality_notes=("Synthetic outcome.",),
    )

    assert observation.data_quality_notes[0].startswith(LABEL_PROVENANCE_NOTE)
    assert "2026-06-11..2026-06-12" in observation.data_quality_notes[0]
    assert "1d" in observation.data_quality_notes[0]
    assert observation.data_quality_notes[1:] == ("Synthetic outcome.",)


def test_a_refused_label_cannot_be_turned_into_an_observation() -> None:
    with pytest.raises(LabelError, match="carries 1 refusal"):
        observation_from_label(_label(halted=True), benchmark_return=0.0, transaction_cost=0.0)


def test_the_free_float_constructor_is_still_there_and_still_says_nothing_about_its_prices() -> (
    None
):
    """The hazard this bridge exists beside, stated as a test rather than as a comment.

    `OutcomeObservation` accepts any two positive floats, so a caller passing raw closes across
    an ex-rights day builds a perfectly valid observation whose realized return has the wrong
    sign. That is why `observation_from_label` exists; it is not why the plain constructor was
    removed, because the replay corpus and every existing caller build one directly.
    """
    raw = OutcomeObservation(
        observation_start=datetime(2026, 6, 11, 7, 0, tzinfo=UTC),
        observation_end=datetime(2026, 6, 12, 7, 0, tzinfo=UTC),
        start_price=11.30,
        end_price=11.24,
        benchmark_return=0.0,
        transaction_cost=0.0,
    )

    assert raw.end_price / raw.start_price - 1 == pytest.approx(UNADJUSTED_RETURN)
    assert raw.data_quality_notes == ()
