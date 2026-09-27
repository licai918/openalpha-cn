"""The strategy backtest's panel side: what it reads, at which instant, and what it refuses
(`V2-P6-007`).

The book itself is held to a hand-computed ledger in
`tests/unit/backtest/test_strategy_backtest.py`.
What this file holds is the join: that the quotes, scores, instants and benchmarks
`load_strategy_inputs` hands the book are the stored panel's own numbers, read through the gated
loaders, oriented and dated the way the book's contract says -- each checked against an
independent read of the same store rather than against the module's own output.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import (
    COMMIT,
    INDEX_LEVELS,
    PROBE_NEUTRALIZATION,
    PROBE_NEUTRALIZATIONS,
    PROBE_TRANSFORM,
    PROBE_TRANSFORMS,
    READ_AT,
    REVERSAL,
    TieredCorpus,
    stored_value,
    write_strategy_corpus,
    write_tiered_corpus,
)

from openalpha_cn.backtest.factor_ic import average_ranks
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    STRATEGY_BACKTEST_LIMITATION_CODES,
    component_key,
    limitation_codes_for,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_daily_bars, load_trading_calendar
from openalpha_cn.strategy_view import (
    PROTOCOL_BENCHMARKS,
    PROTOCOL_COSTS,
    PROTOCOL_PARTICIPATION_CAP,
    PROTOCOL_POSITION_CAPITAL,
    PROTOCOL_SLIPPAGE_RATE,
    StrategyRequestError,
    StrategyRunBlockedError,
    backtest_strategy,
    backtest_view,
    load_strategy_inputs,
    strategy_request,
)

SHANGHAI_1630_UTC_HOUR: Final[int] = 8


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view")
    panel = write_strategy_corpus(root)
    return PanelStore(root / "panel"), panel


@pytest.fixture(scope="module")
def late_corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view-late")
    panel = write_strategy_corpus(root, late=True)
    return PanelStore(root / "panel"), panel


def _request(panel: GeneratedPanel, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "components": ((REVERSAL.qualified_key, "raw", Decimal("1")),),
        "combine": "zscore_sum",
        "transform": None,
        "neutralization": None,
        "start": panel.sessions[1],
        "end": panel.sessions[-1],
        "as_of": READ_AT,
        "exchange": EXCHANGE,
        "rebalance_every_sessions": 3,
        "holding_count": 3,
        "buffer_rank": None,
        "max_industry_weight": None,
    }
    return strategy_request(**{**arguments, **overrides})


def test_the_measurement_defaults_are_the_research_protocols() -> None:
    """Section 2 of the selection-ready plan fixes these once; they are not searched."""
    assert Decimal("100000") == PROTOCOL_POSITION_CAPITAL
    assert Decimal("0.01") == PROTOCOL_PARTICIPATION_CAP
    assert PROTOCOL_COSTS.commission_rate == Decimal("0.00025")
    assert PROTOCOL_COSTS.minimum_commission == Decimal("5.00")
    assert PROTOCOL_COSTS.sell_stamp_duty_rate == Decimal("0.0005")
    assert PROTOCOL_COSTS.transfer_fee_rate == Decimal("0")
    assert Decimal("0.001") == PROTOCOL_SLIPPAGE_RATE
    assert PROTOCOL_BENCHMARKS == ("000905.SH", EQUAL_WEIGHT_ALL_A)


def test_every_signal_instant_is_1630_shanghai_on_its_own_session(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = corpus
    inputs = load_strategy_inputs(store, _request(panel))

    assert inputs.sessions == panel.sessions[1:]
    for day in inputs.sessions:
        instant = inputs.signal_instants[day].astimezone(UTC)
        assert (instant.date(), instant.hour, instant.minute) == (day, SHANGHAI_1630_UTC_HOUR, 30)


def test_a_quote_is_the_stored_bar_and_band_and_the_turnover_is_in_yuan(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Held against an independent `load_daily_bars` read of the same session.

    `daily.amount` is in thousands of yuan, so the turnover the participation cap reads is
    `amount x 1000`.
    """
    store, panel = corpus
    inputs = load_strategy_inputs(store, _request(panel))
    day = panel.sessions[3]
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(day.year,), as_of=READ_AT)
    stored = load_daily_bars(store, day=day, calendar=calendar, as_of=READ_AT, max_staleness=None)
    subject = sorted(stored)[0]
    bar = stored[subject]

    quote = inputs.quotes[day][subject]
    assert quote.bar.open == Decimal(str(bar.open))
    assert quote.bar.close == Decimal(str(bar.close))
    assert quote.bar.previous_close == Decimal(str(bar.pre_close))
    assert quote.bar.has_published_limits
    assert quote.turnover_yuan == Decimal(str(bar.amount)) * 1000
    assert quote.bar.suspended is False


def test_a_lower_is_better_factor_is_negated_and_every_row_is_stamped_at_its_build(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """`reversal_1d/v1` is `lower_is_better`, so every stored value reaches the book negated.

    Only signal days carry rows (every third session from the first), and each row's two clocks
    are the build's own instant, 16:30 Shanghai on its session.
    """
    store, panel = corpus
    inputs = load_strategy_inputs(store, _request(panel))
    subjects = tuple(panel.securities)
    signal_days = {inputs.sessions[index] for index in (0, 3, 6)}

    assert REVERSAL.direction == "lower_is_better"
    assert {row.signal_day for row in inputs.scores} == signal_days
    for row in inputs.scores:
        index = panel.sessions.index(row.signal_day)
        assert row.component == component_key(REVERSAL.qualified_key, "raw")
        assert row.value == -stored_value(subjects, row.subject, index)
        assert row.available_time == row.revision_time == inputs.signal_instants[row.signal_day]


def test_the_equal_weight_benchmark_is_the_mean_stored_session_return(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = corpus
    inputs = load_strategy_inputs(store, _request(panel))
    day = panel.sessions[4]
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(day.year,), as_of=READ_AT)
    stored = load_daily_bars(store, day=day, calendar=calendar, as_of=READ_AT, max_staleness=None)
    expected = statistics.fmean(bar.close / bar.pre_close - 1.0 for bar in stored.values())

    assert inputs.benchmark_returns[EQUAL_WEIGHT_ALL_A][day] == Decimal(repr(expected))


def test_the_index_benchmark_is_close_over_previous_close(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = corpus
    inputs = load_strategy_inputs(store, _request(panel))
    levels = INDEX_LEVELS["000905.SH"]
    index = 4

    assert inputs.benchmark_returns["000905.SH"][panel.sessions[index]] == Decimal(
        repr(levels[index] / levels[index - 1] - 1.0)
    )


def test_a_backtest_over_the_stored_panel_trades_at_the_next_sessions_stored_open(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Every fill of rebalance k is on the session after signal day k, at that session's open."""
    store, panel = corpus
    result = backtest_strategy(store, _request(panel))
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(2026,), as_of=READ_AT)

    assert [period.start for period in result.periods] == [
        panel.sessions[1],
        panel.sessions[4],
        panel.sessions[7],
    ]
    assert result.periods[-1].end == panel.sessions[-1]
    assert set(result.limitations) == set(limitation_codes_for("static"))
    for period in result.periods:
        trade_day = calendar.next_trading_day(period.start)
        stored = load_daily_bars(
            store, day=trade_day, calendar=calendar, as_of=READ_AT, max_staleness=None
        )
        for fill in period.fills:
            assert fill.day == trade_day
            assert fill.price == Decimal(str(stored[fill.subject].open))
    assert result.periods[0].fills, "the first rebalance must buy something"


def test_the_first_rebalance_buys_the_lowest_stored_reversal_values(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Negation means the book wants the smallest stored `reversal_1d` values."""
    store, panel = corpus
    result = backtest_strategy(store, _request(panel))
    subjects = tuple(panel.securities)
    by_value = sorted(subjects, key=lambda subject: stored_value(subjects, subject, 1))

    assert {fill.subject for fill in result.periods[0].fills if fill.side == "buy"} <= set(
        by_value[:3]
    )


def test_a_cross_section_built_after_the_signal_instant_is_refused_as_look_ahead(
    late_corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Builds stamped 17:00 Shanghai are not knowable at 16:30: blocked, never silently used."""
    store, panel = late_corpus
    with pytest.raises(StrategyRunBlockedError, match="not visible"):
        backtest_strategy(store, _request(panel))


def test_a_later_rebuild_on_a_signal_day_is_not_traded_in_place_of_the_on_time_build(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Every session carries its 16:30 build and a reversed 17:00 re-run of it.

    The book trades on what was knowable at the signal: the 16:30 rows, not the re-run that the
    evaluation-instant read also returns. Taking the later one would be look-ahead the book then
    refuses; taking it silently would be worse.
    """
    root = tmp_path_factory.mktemp("strategy-view-rebuilt")
    panel = write_strategy_corpus(root, rebuilt_late=True)
    store = PanelStore(root / "panel")
    inputs = load_strategy_inputs(store, _request(panel))
    subjects = tuple(panel.securities)

    for row in inputs.scores:
        index = panel.sessions.index(row.signal_day)
        assert row.available_time == inputs.signal_instants[row.signal_day]
        assert row.value == -stored_value(subjects, row.subject, index)
    assert backtest_strategy(store, _request(panel)).periods


@pytest.fixture(scope="module")
def tiered(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, TieredCorpus]:
    root = tmp_path_factory.mktemp("strategy-view-tiers")
    corpus = write_tiered_corpus(root)
    return PanelStore(root / "panel"), corpus


@pytest.mark.parametrize("tier", ["processed", "neutralized"])
def test_a_processed_or_neutralized_tier_is_read_end_to_end_and_traded(
    tiered: tuple[PanelStore, TieredCorpus], tier: str
) -> None:
    """Each derived tier, written by its own plane's writer, read back by the view and traded.

    The scores the book receives are held to the writer's own rows -- negated, because
    `reversal_1d/v1` is `lower_is_better` -- on exactly the signal days, and the first rebalance
    buys from the top of that ranking. The processed tier needs the transform, the neutralized
    tier needs both; the probe specs are the ones an eight-name panel clears.
    """
    store, corpus = tiered
    panel = corpus.panel
    request = _request(
        panel,
        components=((REVERSAL.qualified_key, tier, Decimal("1")),),
        transform=PROBE_TRANSFORM.qualified_key,
        neutralization=PROBE_NEUTRALIZATION.qualified_key if tier == "neutralized" else None,
        transforms=PROBE_TRANSFORMS,
        neutralizations=PROBE_NEUTRALIZATIONS,
    )
    written = corpus.processed if tier == "processed" else corpus.neutralized
    inputs = load_strategy_inputs(store, request)
    signal_days = [inputs.sessions[index] for index in (0, 3, 6)]

    assert {(row.signal_day, row.subject): row.value for row in inputs.scores} == {
        (day, subject): -value for day in signal_days for subject, value in written[day].items()
    }
    assert all(row.component == f"{REVERSAL.qualified_key}@{tier}" for row in inputs.scores)
    assert len(written[signal_days[0]]) >= 3

    first = backtest_strategy(store, request).periods[0]
    best = sorted(written[signal_days[0]], key=lambda subject: written[signal_days[0]][subject])
    assert {fill.subject for fill in first.fills if fill.side == "buy"} <= set(best[:3])
    assert first.fills


def test_the_rendered_answer_carries_every_number_as_a_string_and_every_limitation(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = corpus
    body = backtest_view(backtest_strategy(store, _request(panel)))
    first = body["periods"][0]  # type: ignore[index]

    assert isinstance(first["net_return"], str)
    assert set(body["limitation_details"]) == STRATEGY_BACKTEST_LIMITATION_CODES  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"components": ((REVERSAL.qualified_key, "processed", Decimal("1")),)}, "--transform"),
        (
            {
                "components": ((REVERSAL.qualified_key, "neutralized", Decimal("1")),),
                "transform": "cross_section_standard/v1",
            },
            "--neutralization",
        ),
        ({"transform": "cross_section_standard/v1"}, "--transform"),
        ({"components": (("no_such_factor/v1", "raw", Decimal("1")),)}, "no_such_factor"),
        ({"as_of": datetime(2026, 1, 16, 7, 0, tzinfo=UTC)}, "publication instant"),
        ({"end": date(2026, 1, 6)}, "must be before"),
        ({"holding_count": 0}, "holding_count"),
        ({"combine": "mean"}, "combine"),
    ],
)
def test_a_request_that_cannot_be_put_is_refused_before_the_store_is_touched(
    corpus: tuple[PanelStore, GeneratedPanel], overrides: dict[str, Any], message: str
) -> None:
    _, panel = corpus
    with pytest.raises(StrategyRequestError, match=message):
        _request(panel, **overrides)


def test_a_prediction_source_without_a_prediction_store_is_refused(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = corpus
    request = _request(panel, components=(), prediction_ids=("prd_" + "0" * 24,))
    with pytest.raises(StrategyRequestError, match="prediction store"):
        load_strategy_inputs(store, request)


def test_an_unheld_prediction_is_blocked(corpus: tuple[PanelStore, GeneratedPanel]) -> None:
    store, panel = corpus
    request = _request(panel, components=(), prediction_ids=("prd_" + "0" * 24,))
    with pytest.raises(StrategyRunBlockedError, match="no prediction is held"):
        load_strategy_inputs(store, request, predictions=lambda _: None)


def test_the_industry_cap_reads_the_stored_membership_at_each_signal(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Every generated security sits in one level-one industry, so a cap of one name in four
    leaves the first rebalance holding exactly one name where the uncapped book holds four."""
    store, panel = corpus
    capped = _request(panel, holding_count=4, max_industry_weight=Decimal("0.25"))
    inputs = load_strategy_inputs(store, capped)
    industries = inputs.industries[inputs.sessions[0]]

    assert len(set(industries.values())) == 1
    assert set(industries) == set(panel.securities)
    assert len(backtest_strategy(store, capped).periods[0].holdings) == 1
    uncapped = _request(panel, holding_count=4)
    assert len(backtest_strategy(store, uncapped).periods[0].holdings) == 4


# --- V2-P6-014: the two dynamic sources over the stored panel ------------------------------------
#
# The range is s1..s9 (2026-01-06 .. 01-16), signals every two sessions: s1, s3, s5, s7. The
# panel holds no calendar year before 2026, so there is no lookback and every window starts at
# s1. A 1d label for prediction day t enters on t+1 and exits on t+2, so it is known at the
# 16:30 of s_k only when t <= s_(k-2).

TRAILING: Final[dict[str, Any]] = {
    "components": ((REVERSAL.qualified_key, "raw"),),
    "ic_window_sessions": 5,
    "min_ic_observations": 1,
    "ic_method": "spearman",
    "horizon_sessions": 1,
    "negative_ic": "keep_sign",
    "min_ic_securities": 3,
}
WALK_FORWARD: Final[dict[str, Any]] = {
    "family": "cross_sectional_rank",
    "features": (f"{REVERSAL.qualified_key}@raw",),
    "seed": 0,
    "code_commit": COMMIT,
    "train_sessions": 5,
    "refit_every_sessions": 2,
    "embargo_sessions": 1,
    "horizon_sessions": 1,
}


def _dynamic(panel: GeneratedPanel, **source: Any) -> Any:
    return _request(panel, components=(), rebalance_every_sessions=2, **source)


def _independent_rank_ic(store: PanelStore, panel: GeneratedPanel, index: int) -> float:
    """The oriented 1d rank IC of the build on `panel.sessions[index]`, computed without the
    module: stored values against close-to-close returns read straight off `load_daily_bars`,
    ranked by `average_ranks` and correlated by `statistics.correlation`."""
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(2026,), as_of=READ_AT)
    entry, exit_ = panel.sessions[index + 1], panel.sessions[index + 2]
    first = load_daily_bars(store, day=entry, calendar=calendar, as_of=READ_AT, max_staleness=None)
    last = load_daily_bars(store, day=exit_, calendar=calendar, as_of=READ_AT, max_staleness=None)
    names = [name for name in panel.securities if name in first and name in last]
    values = [stored_value(tuple(panel.securities), name, index) for name in names]
    returns = [last[name].close / first[name].close - 1.0 for name in names]
    raw = statistics.correlation(list(average_ranks(values)), list(average_ranks(returns)))
    return -raw if REVERSAL.direction == "lower_is_better" else raw


def test_a_trailing_ic_weight_counts_exactly_the_labels_closed_by_each_signal(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Known ICs at s1, s3, s5, s7 in a 5-session window: none; s1's; s1..s3; s3..s5.

    s1 has nothing to weigh, so the first period is held. s3's weight is s1's IC alone, which is
    held against a rank IC computed here from the stored bars rather than by the module.
    """
    store, panel = corpus
    result = backtest_strategy(store, _dynamic(panel, trailing_ic=TRAILING))

    counts = [period.ic_weights[0].observations for period in result.periods]
    assert counts == [0, 1, 3, 3]
    assert [period.held for period in result.periods] == [True, False, False, False]
    assert result.periods[1].ic_weights[0].mean_ic == pytest.approx(
        _independent_rank_ic(store, panel, 1), abs=1e-12
    )
    assert "a_trailing_ic_weight_is_the_mean_of_the_ics_known_at_the_signal" in (result.limitations)


def test_every_trailing_ic_is_dated_no_earlier_than_its_labels_exit(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Each observation's `known_at` is the 16:30 of the session two after its prediction day;
    only days whose label can exit by the last signal (s7) are priced at all: s1 .. s5."""
    store, panel = corpus
    inputs = load_strategy_inputs(store, _dynamic(panel, trailing_ic=TRAILING))

    days = [item.prediction_day for item in inputs.ic_observations]
    assert days == list(panel.sessions[1:6])
    for item in inputs.ic_observations:
        exit_day = panel.sessions[panel.sessions.index(item.prediction_day) + 2]
        assert item.known_at == inputs.signal_instants[exit_day]
    assert inputs.lookback_sessions == ()


def test_a_walk_forward_model_holds_until_its_first_fit_and_then_ranks_the_column(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Refits on s1, s3, s5, s7 with a 5-session window, embargo 1, horizon 1.

    A refit at calendar position p may train only on prediction day p-4, so s1 and s3 cannot
    fit (no calendar before s1; no closed label), s5 trains on s1 and s7 on s3. On s5 and s7 the
    one-column `cross_sectional_rank` fit orders the market exactly by the stored column, in the
    orientation its learned coefficient says, and every score row carries the signal instant.
    """
    store, panel = corpus
    request = _dynamic(panel, walk_forward=WALK_FORWARD)
    inputs = load_strategy_inputs(store, request)
    result = backtest_strategy(store, request)
    s1, s3, s5, s7 = (panel.sessions[index] for index in (1, 3, 5, 7))

    assert [fit.refit_day for fit in result.model_fits] == [s1, s3, s5, s7]
    assert [fit.refusal is None for fit in result.model_fits] == [False, False, True, True]
    assert [period.held for period in result.periods] == [True, True, False, False]
    assert [p.model_fit.refit_day if p.model_fit else None for p in result.periods] == [
        None,
        None,
        s5,
        s7,
    ]
    subjects = tuple(panel.securities)
    for day in (s5, s7):
        fit = inputs.fit_for_day[day]
        assert fit.artifact is not None
        (coefficient,) = (value for _, value in fit.artifact.parameters)
        rows = [row for row in inputs.scores if row.signal_day == day]
        assert {(row.available_time, row.revision_time) for row in rows} == {
            (inputs.signal_instants[day], inputs.signal_instants[day])
        }
        index = panel.sessions.index(day)
        by_score = [row.subject for row in sorted(rows, key=lambda row: -row.value)]
        by_column = sorted(
            (row.subject for row in rows),
            key=lambda name: stored_value(subjects, name, index),
            reverse=coefficient > 0,
        )
        assert by_score == by_column
        embargo_day = panel.sessions[index - 1]
        assert fit.labels_known_at is not None
        assert fit.labels_known_at < inputs.signal_instants[embargo_day]


def test_a_walk_forward_fit_trains_on_exactly_one_prediction_day_here(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """s5's fit trains on s1 alone (all eight names labelled); s7's on s3 alone."""
    store, panel = corpus
    result = backtest_strategy(store, _dynamic(panel, walk_forward=WALK_FORWARD))
    fitted = [fit for fit in result.model_fits if fit.refusal is None]

    assert [fit.prediction_day_count for fit in fitted] == [1, 1]
    assert [fit.labels_known_at for fit in fitted] == [
        load_strategy_inputs(store, _dynamic(panel, walk_forward=WALK_FORWARD)).signal_instants[
            panel.sessions[index]
        ]
        for index in (3, 5)
    ]


@pytest.mark.parametrize(
    ("walk_forward", "message"),
    [
        ({"features": (f"{REVERSAL.qualified_key}@neutralized:x:y",)}, "neutralized"),
        ({"family": "linear"}, "linear"),
        (
            {"family": "boosted_rank_trees", "hyperparameters": {"tree_count": 50}},
            "cannot be declared",
        ),
        ({"features": ("nonexistent/v1@raw",)}, "nonexistent"),
    ],
)
def test_a_walk_forward_model_that_cannot_be_declared_is_a_bad_request(
    corpus: tuple[PanelStore, GeneratedPanel], walk_forward: dict[str, Any], message: str
) -> None:
    _, panel = corpus
    with pytest.raises(StrategyRequestError, match=message):
        _dynamic(panel, walk_forward={**WALK_FORWARD, **walk_forward})


def test_a_request_level_transform_beside_a_walk_forward_source_is_refused(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    _, panel = corpus
    with pytest.raises(StrategyRequestError, match="each feature names its own transform"):
        _dynamic(
            panel,
            walk_forward=WALK_FORWARD,
            transform=PROBE_TRANSFORM.qualified_key,
            transforms=PROBE_TRANSFORMS,
        )


def test_a_trailing_ic_run_that_never_knows_an_ic_is_blocked_not_reported_flat(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = corpus
    never = {**TRAILING, "min_ic_observations": 5}
    with pytest.raises(StrategyRunBlockedError, match="no scores on any"):
        backtest_strategy(store, _dynamic(panel, trailing_ic=never))
