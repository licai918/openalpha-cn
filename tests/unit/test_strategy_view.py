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

import math
import statistics
from array import array
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

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
    write_two_year_corpus,
)

from openalpha_cn import strategy_view
from openalpha_cn.backtest import strategy_backtest
from openalpha_cn.backtest.execution import (
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.backtest.factor_ic import (
    FactorICSpec,
    FactorICStudy,
    average_ranks,
    ic_cross_section,
)
from openalpha_cn.backtest.factor_tradeability import CNY_PER_TURNOVER_UNIT
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    EQUAL_WEIGHT_ALL_A_HELD,
    MODEL_COMPONENT,
    STRATEGY_BACKTEST_LIMITATION_CODES,
    ICObservation,
    ScoreRow,
    SessionQuote,
    StrategyInputs,
    component_key,
    limitation_codes_for,
    run_strategy_backtest,
    usable_fit,
    walk_forward_fits,
)
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import halt_corpus_for_years
from openalpha_cn.domain.price_limits import TradingState
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.model_view import (
    UNFILED_CONFIG_DIGEST,
    LabelReach,
    ModelRunRequest,
    OutcomeLabels,
    feature_cross_section,
    training_panel,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import load_factor_observations
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_price_limits,
    load_suspensions,
    load_trading_calendar,
    session_publication_instant,
)
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
SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)


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
    assert PROTOCOL_BENCHMARKS == ("000905.SH", EQUAL_WEIGHT_ALL_A_HELD)


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
    """No longer the protocol's (`V2-P6-024`), still read unchanged for a caller naming it."""
    store, panel = corpus
    inputs = load_strategy_inputs(
        store, _request(panel, benchmarks=("000905.SH", EQUAL_WEIGHT_ALL_A))
    )
    day = panel.sessions[4]
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(day.year,), as_of=READ_AT)
    stored = load_daily_bars(store, day=day, calendar=calendar, as_of=READ_AT, max_staleness=None)
    expected = statistics.fmean(bar.close / bar.pre_close - 1.0 for bar in stored.values())

    assert inputs.benchmark_returns[EQUAL_WEIGHT_ALL_A][day] == Decimal(repr(expected))


def _held_by_hand(
    quotes: Mapping[date, Mapping[str, SessionQuote]], period: Sequence[date]
) -> tuple[Decimal, int]:
    """`EQUAL_WEIGHT_ALL_A_HELD` over one period, restated from the quotes the view hands the
    book: a member has a quote on the signal day and, at the execution session, is not halted and
    opens below its published limit-up; it is worth its last close x factor over its open x
    factor there. No recorded path in this panel, so no correction."""
    signal_day, trade_day, *_ = period
    returns: list[Decimal] = []
    for subject in quotes[signal_day]:
        entry = quotes[trade_day].get(subject)
        if entry is None or entry.bar.suspended:
            continue
        assert entry.bar.up_limit is not None
        if entry.bar.open >= entry.bar.up_limit:
            continue
        last = next(quotes[day][subject] for day in reversed(period[1:]) if subject in quotes[day])
        returns.append(last.bar.close * last.adj_factor / (entry.bar.open * entry.adj_factor) - 1)
    return sum(returns, Decimal(0)) / len(returns), len(returns)


def test_the_held_benchmark_is_the_book_bought_at_each_open_and_held(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The protocol's default now: every period's value is the hand restatement's."""
    store, panel = corpus
    request = _request(panel)
    result = backtest_strategy(store, request)
    inputs = load_strategy_inputs(store, request)

    assert set(request.spec.benchmarks) == {"000905.SH", EQUAL_WEIGHT_ALL_A_HELD}
    assert EQUAL_WEIGHT_ALL_A_HELD not in inputs.benchmark_returns
    for period in result.periods:
        span = inputs.sessions[
            inputs.sessions.index(period.start) : inputs.sessions.index(period.end) + 1
        ]
        expected, members = _held_by_hand(inputs.quotes, span)
        assert members > 0
        assert period.benchmark_returns[EQUAL_WEIGHT_ALL_A_HELD] == expected.quantize(
            Decimal("0.0000000001")
        )


def test_the_held_benchmark_is_computed_once_per_store_range_instant_and_exchange(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-024`: every configuration sharing a range and a schedule shares the benchmark,
    so a process computes each period once. The served answer is the fresh one; another
    configuration on the same schedule computes nothing; another range, or a store whose catalog
    moved, computes afresh."""
    store, panel = corpus
    computed: list[date] = []
    real = strategy_backtest.held_equal_weight_period

    def counted(quotes: Any, sessions: Sequence[date]) -> Any:
        computed.append(sessions[0])
        return real(quotes, sessions)

    monkeypatch.setattr(strategy_backtest, "held_equal_weight_period", counted)
    strategy_view.forget_held_benchmarks()
    fresh = backtest_strategy(store, _request(panel))
    assert len(computed) == len(fresh.periods) == 3

    served = backtest_strategy(store, _request(panel))
    other_book = backtest_strategy(store, _request(panel, holding_count=2))
    assert len(computed) == 3
    assert served == fresh
    assert [p.benchmark_returns[EQUAL_WEIGHT_ALL_A_HELD] for p in other_book.periods] == [
        p.benchmark_returns[EQUAL_WEIGHT_ALL_A_HELD] for p in fresh.periods
    ]

    strategy_view.forget_held_benchmarks()
    again = backtest_strategy(store, _request(panel))
    assert len(computed) == 6
    assert again == fresh

    backtest_strategy(store, _request(panel, start=panel.sessions[2]))
    assert len(computed) == 9

    stamps = store.partition_stamps()
    monkeypatch.setattr(
        store, "partition_stamps", lambda **_: (*stamps, ("daily", 2099, "moved", ()))
    )
    moved = backtest_strategy(store, _request(panel))
    assert len(computed) == 12
    assert moved == fresh


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
# panel holds no calendar year before 2026, so the lookback is 2026's own s0 alone -- which
# carries no factor build, so every IC and every training day is still s1 or later. A 1d label
# for prediction day t enters on t+1 and exits on t+2, so it is known at the 16:30 of s_k only
# when t <= s_(k-2).

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
    assert inputs.lookback_sessions == (panel.sessions[0],)


def test_a_walk_forward_model_holds_until_its_first_fit_and_then_ranks_the_column(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Refits on s1, s3, s5, s7 with a 5-session window, embargo 1, horizon 1.

    A refit at calendar position p may train only on prediction day p-4, so s1 and s3 cannot
    fit (no prediction day that early has a build), s5 trains on s1 and s7 on s3. On s5 and s7 the
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
    with pytest.raises(StrategyRunBlockedError, match="could not answer on any of the 4"):
        backtest_strategy(store, _dynamic(panel, trailing_ic=never))


# --- V2-P6-014 fix round 1: streaming, determinism and the per-instant IC series -----------------

KINDS: Final[dict[str, dict[str, Any]]] = {
    "static": {},
    "trailing_ic": {"components": (), "trailing_ic": TRAILING},
    "walk_forward": {"components": (), "walk_forward": WALK_FORWARD},
}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_streamed_run_is_the_materialised_run_period_for_period(
    corpus: tuple[PanelStore, GeneratedPanel], kind: str
) -> None:
    """`backtest_strategy` streams (the book asks for each signal day's scores as it books the
    period); `load_strategy_inputs` drains the same feed into the four score fields. Same periods,
    same fills, same ledger, same weights and fits -- the whole answer, byte for byte."""
    store, panel = corpus
    request = _request(panel, rebalance_every_sessions=2, **KINDS[kind])
    streamed = backtest_strategy(store, request)
    materialised = run_strategy_backtest(load_strategy_inputs(store, request), request.spec)

    assert load_strategy_inputs(store, request, stream=True).feed is not None
    assert backtest_view(streamed) == backtest_view(materialised)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_two_runs_of_one_request_answer_identically(
    corpus: tuple[PanelStore, GeneratedPanel], kind: str
) -> None:
    store, panel = corpus
    request = _request(panel, rebalance_every_sessions=2, **KINDS[kind])

    assert backtest_view(backtest_strategy(store, request)) == backtest_view(
        backtest_strategy(store, request)
    )


def test_the_walk_forward_feed_holds_one_training_window_and_never_the_history(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """After every refit the feed's labelled days all sit inside that refit's window."""
    store, panel = corpus
    request = _dynamic(panel, walk_forward={**WALK_FORWARD, "refit_every_sessions": 1})
    feed = load_strategy_inputs(store, request, stream=True).feed
    assert feed is not None
    held: list[tuple[date, ...]] = []
    for day in panel.sessions[1:8]:
        feed.fit_on(day)
        held.append(tuple(sorted(feed._window)))  # type: ignore[attr-defined]

    for day, window in zip(panel.sessions[1:8], held, strict=True):
        position = panel.sessions.index(day)
        assert all(position - 5 < panel.sessions.index(kept) <= position for kept in window)
    assert max(len(window) for window in held) <= 5
    examples = [
        example
        for rows in feed._window.values()  # type: ignore[attr-defined]
        for example in rows
    ]
    assert examples
    assert all(example.label.window_return.per_session == () for example in examples)
    assert all(math.isfinite(example.target) for example in examples)


def test_a_value_the_tier_does_not_admit_is_counted_by_the_census_and_never_scored() -> None:
    """A processed `imputed` row carries a number no security produced (`TIER_ADMITTED_CODES`):
    it is kept for an IC's census and never becomes a score or a correlation term."""
    from openalpha_cn.strategy_view import _Section

    section = _Section(
        build=READ_AT,
        tier="processed",
        subjects=("000001.SZ", "000002.SZ", "000003.SZ"),
        values=array("d", (0.5, 0.0, math.nan)),
        present=bytes((1, 1, 0)),
        coverage=("processed", "imputed", "input_missing"),
    )

    assert list(section.admitted()) == [("000001.SZ", 0.5)]
    assert section.rows() == [
        ("000001.SZ", 0.5, "processed"),
        ("000002.SZ", 0.0, "imputed"),
        ("000003.SZ", None, "input_missing"),
    ]


def test_a_build_that_admits_nothing_is_not_a_days_cross_section(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two builds on one day, both visible at its 16:30: the later stores only excluded rows
    (no value), the earlier admitted ones. The day's section is the earlier build -- the newest
    build that carries a cross section -- rather than an empty one that happens to be newer."""
    from openalpha_cn import strategy_view

    store, panel = corpus
    day = panel.sessions[3]
    early = datetime(day.year, day.month, day.day, 8, 0, tzinfo=UTC)
    late = datetime(day.year, day.month, day.day, 8, 20, tzinfo=UTC)

    def stored(*_: object) -> list[Any]:
        return [
            strategy_view._Observed(
                subject="000001.SZ", as_of=early, value=0.1, coverage="computed"
            ),
            strategy_view._Observed(
                subject="000002.SZ", as_of=early, value=0.2, coverage="computed"
            ),
            strategy_view._Observed(
                subject="000001.SZ", as_of=late, value=None, coverage="input_missing"
            ),
        ]

    monkeypatch.setattr(strategy_view, "_tier_rows", stored)
    sections = strategy_view._year_sections(
        store,
        _request(panel),
        REVERSAL,
        "raw",
        day.year,
        days=frozenset({day}),
        instants={day: late + timedelta(hours=1)},
        names={},
    )

    assert sections[day].build == early
    assert list(sections[day].admitted()) == [("000001.SZ", 0.1), ("000002.SZ", 0.2)]


def test_a_factor_feed_drops_each_signal_days_scores_once_handed_over(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The streaming contract: the book asks about a day once, and after it has, the feed holds
    nothing for that day -- what stays resident is the days not yet booked, never the booked."""
    store, panel = corpus
    request = _request(panel, rebalance_every_sessions=2)
    feed = load_strategy_inputs(store, request, stream=True).feed
    assert feed is not None
    signal_days = list(panel.sessions[1:9:2])

    first = feed.rows_on(signal_days[0])
    assert first
    held = feed._rows  # type: ignore[attr-defined]
    assert signal_days[0] not in held
    assert set(held) == set(signal_days[1:])
    assert feed.rows_on(signal_days[0]) == []


def _ic_series_request(panel: GeneratedPanel, **overrides: Any) -> Any:
    from openalpha_cn.strategy_view import ic_series_request

    arguments: dict[str, Any] = {
        "factor": REVERSAL.qualified_key,
        "tier": "raw",
        "transform": None,
        "neutralization": None,
        "horizon_sessions": 1,
        "ic_method": "spearman",
        "min_securities": 3,
        "start": panel.sessions[1],
        "end": panel.sessions[7],
        "as_of": READ_AT,
        "exchange": EXCHANGE,
    }
    return ic_series_request(**{**arguments, **overrides})


def test_the_ic_series_is_one_point_per_day_held_against_an_independent_rank_ic(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """s1..s7, 1d labels. Every stored row is in the census, by its own code. On the days every
    name is admitted and labelled the point's IC is the rank IC computed here from
    `load_daily_bars` closes over all eight names. On s2 and s3 the halted 601318.SH has no
    label and is counted unlabelled; on s5 it has no previous close and the stored build codes
    it `insufficient_history`, which the census counts as excluded rather than dropping."""
    from openalpha_cn.strategy_view import factor_ic_series

    store, panel = corpus
    series = factor_ic_series(store, _ic_series_request(panel))

    assert [point.prediction_day for point in series.points] == list(panel.sessions[1:8])
    compared = 0
    for point in series.points:
        index = panel.sessions.index(point.prediction_day)
        census = point.census
        excluded = dict(census.excluded_by_coverage)
        assert point.point.coverage == "measured"
        assert point.n_securities == census.admitted_count == point.point.sample_size
        assert census.subject_count == 8
        shape = (point.n_securities, census.unlabelled_count, excluded["insufficient_history"])
        if index in (2, 3):
            assert shape == (7, 1, 0)
        elif index == 5:
            assert shape == (7, 0, 1)
        else:
            assert shape == (8, 0, 0)
            assert point.ic == pytest.approx(_independent_rank_ic(store, panel, index), abs=1e-12)
            compared += 1
    assert compared == 4


def test_the_ic_series_and_the_trailing_weights_are_one_computation(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The trailing-IC source's observation for a day is the series' point for that day."""
    from openalpha_cn.strategy_view import factor_ic_series

    store, panel = corpus
    series = factor_ic_series(store, _ic_series_request(panel, end=panel.sessions[5]))
    inputs = load_strategy_inputs(store, _dynamic(panel, trailing_ic=TRAILING))

    assert [(item.prediction_day, item.ic) for item in inputs.ic_observations] == [
        (point.prediction_day, point.ic) for point in series.points
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ic_method": "kendall"}, "ic_method"),
        ({"tier": "processed"}, "transform"),
        ({"min_securities": 2}, "min_securities"),
        ({"horizon_sessions": 0}, "horizon_sessions"),
        ({"end": date(2026, 1, 5), "start": date(2026, 1, 9)}, "before"),
        ({"factor": "nonexistent/v1"}, "nonexistent"),
    ],
)
def test_an_ic_series_that_cannot_be_asked_is_a_bad_request(
    corpus: tuple[PanelStore, GeneratedPanel], overrides: dict[str, Any], message: str
) -> None:
    _, panel = corpus
    with pytest.raises(StrategyRequestError, match=message):
        _ic_series_request(panel, **overrides)


def test_an_ic_series_whose_last_label_has_not_closed_by_the_reading_instant_is_blocked(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """s9's 1d label exits two sessions after the panel's last one: not knowable at READ_AT."""
    from openalpha_cn.strategy_view import factor_ic_series

    store, panel = corpus
    with pytest.raises(StrategyRunBlockedError, match="has not closed"):
        factor_ic_series(store, _ic_series_request(panel, end=panel.sessions[-1]))


# --- V2-P6-014 fix round 2: across a calendar year, against whole-range reads --------------------
#
# Every narrowing the streamed feeds make is a year window (a tier-year read, the label reader's
# years, a quote's adjustment years, a scoring cross section's years, a training batch's years),
# so they can only differ from a whole-range read across a year boundary. The corpus below is
# priced 2026-01-05 .. 2027-01-22, and each kind is held against a reference assembled here from
# the public whole-range readers alone. Signals every four sessions from 2026-12-15 fall on
# 12-15, 12-21, 12-25, 12-31, 01-07, 01-13, 01-19: the 12-31 signal trades on 2027-01-04.

BOUNDARY_START: Final[date] = date(2026, 12, 15)
BOUNDARY_SIGNAL: Final[date] = date(2026, 12, 31)
BOUNDARY_TRADE: Final[date] = date(2027, 1, 4)
TWO_YEARS: Final[tuple[int, ...]] = (2026, 2027)
TWO_YEAR_TRAILING: Final[dict[str, Any]] = {**TRAILING, "ic_window_sessions": 10}
TWO_YEAR_WALK_FORWARD: Final[dict[str, Any]] = {
    **WALK_FORWARD,
    "train_sessions": 8,
    "refit_every_sessions": 4,
}
TWO_YEAR_KINDS: Final[dict[str, dict[str, Any]]] = {
    "static": {"components": ((REVERSAL.qualified_key, "raw", Decimal("1")),)},
    "trailing_ic": {"components": (), "trailing_ic": TWO_YEAR_TRAILING},
    "walk_forward": {"components": (), "walk_forward": TWO_YEAR_WALK_FORWARD},
}


@pytest.fixture(scope="module")
def two_years(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view-two-years")
    panel = write_two_year_corpus(root)
    return PanelStore(root / "panel"), panel


def _two_year_request(panel: GeneratedPanel, kind: str) -> Any:
    return strategy_request(
        combine="zscore_sum",
        transform=None,
        neutralization=None,
        start=BOUNDARY_START,
        end=panel.sessions[-1],
        as_of=panel.as_of,
        exchange=EXCHANGE,
        rebalance_every_sessions=4,
        holding_count=3,
        buffer_rank=None,
        max_industry_weight=None,
        benchmarks=(EQUAL_WEIGHT_ALL_A,),
        **TWO_YEAR_KINDS[kind],
    )


def _reference_board(code: str) -> Literal["main", "star", "growth", "bse"]:
    if code.endswith(".BJ"):
        return "bse"
    if code.startswith(("688", "689")):
        return "star"
    return "growth" if code.startswith(("300", "301")) else "main"


def _reference_quotes(
    store: PanelStore, request: Any, calendar: TradingCalendar, sessions: Sequence[date]
) -> tuple[dict[date, dict[str, SessionQuote]], dict[date, Decimal]]:
    """Every session's quotes and equal-weight return, read with WHOLE-RANGE adjustment factors
    and halts -- the reads the streamed quotes narrow to a year window."""
    adjustments = load_adjustment_histories(
        store, years=TWO_YEARS, as_of=request.as_of, max_staleness=None
    )
    halts = halt_corpus_for_years(
        load_suspensions(store, years=TWO_YEARS, as_of=request.as_of, max_staleness=None),
        years=TWO_YEARS,
    )
    quotes: dict[date, dict[str, SessionQuote]] = {}
    equal: dict[date, Decimal] = {}
    for day in sessions:
        bars = load_daily_bars(
            store, day=day, calendar=calendar, as_of=request.as_of, max_staleness=None
        )
        limits = load_price_limits(
            store, day=day, calendar=calendar, as_of=request.as_of, max_staleness=None
        )
        equal[day] = Decimal(
            repr(statistics.fmean(bar.close / bar.pre_close - 1.0 for bar in bars.values()))
        )
        quotes[day] = {}
        for code, bar in bars.items():
            limit, history = limits.get(code), adjustments.get(code)
            if limit is None or history is None:
                continue
            state = halts.state_on(day, code)
            quotes[day][code] = SessionQuote(
                bar=MarketBar(
                    subject=code,
                    trade_date=day,
                    board=_reference_board(code),
                    previous_close=Decimal(str(bar.pre_close)),
                    open=Decimal(str(bar.open)),
                    high=Decimal(str(bar.high)),
                    low=Decimal(str(bar.low)),
                    close=Decimal(str(bar.close)),
                    suspended=(
                        suspended_at_the_close(state, halts.timing_on(day, code))
                        or state is TradingState.interrupted
                    ),
                    is_st=False,
                    **published_limit_fields(limit),
                ),
                turnover_yuan=Decimal(str(bar.amount)) * CNY_PER_TURNOVER_UNIT,
                adj_factor=Decimal(str(history.factor_on(day))),
            )
    return quotes, equal


def _whole_range_builds(
    store: PanelStore, request: Any, instants: Mapping[date, datetime]
) -> dict[date, tuple[datetime, list[Any]]]:
    """Each day's build (the newest at or before its 16:30, else the earliest), whole range."""
    by_day: dict[date, dict[datetime, list[Any]]] = {}
    for row in load_factor_observations(store, REVERSAL, years=TWO_YEARS, as_of=request.as_of):
        by_day.setdefault(row.as_of.astimezone(SHANGHAI).date(), {}).setdefault(
            row.as_of, []
        ).append(row)
    chosen: dict[date, tuple[datetime, list[Any]]] = {}
    for day, builds in by_day.items():
        admitting = {
            instant: rows
            for instant, rows in builds.items()
            if any(row.coverage == "computed" and row.value is not None for row in rows)
        }
        if day not in instants or not admitting:
            continue
        visible = [instant for instant in admitting if instant <= instants[day]]
        build = max(visible) if visible else min(admitting)
        chosen[day] = (build, admitting[build])
    return chosen


def _reference_inputs(store: PanelStore, request: Any, kind: str) -> StrategyInputs:
    """The kind's whole answer assembled from the public whole-range readers alone."""
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=TWO_YEARS, as_of=request.as_of)
    sessions = calendar.trading_days_between(request.start, request.end)
    signal_days = sessions[:-1:4]
    source = request.source
    needed = (
        source.trailing_ic.ic_window_sessions - 1
        if source.trailing_ic is not None
        else source.walk_forward.train_sessions - 1
        if source.walk_forward is not None
        else 0
    )
    before = tuple(day for day in calendar.trading_days if day < request.start)
    lookback = before[-needed:] if needed else ()
    full = lookback + sessions
    instants = {day: session_publication_instant(day) for day in full}
    quotes, equal = _reference_quotes(store, request, calendar, sessions)
    base: dict[str, Any] = {
        "source": source,
        "sessions": sessions,
        "signal_instants": instants,
        "quotes": quotes,
        "benchmark_returns": {EQUAL_WEIGHT_ALL_A: equal},
        "lookback_sessions": lookback,
    }
    if source.walk_forward is None:
        builds = _whole_range_builds(store, request, instants)
        key = component_key(REVERSAL.qualified_key, "raw")
        scores = tuple(
            ScoreRow(
                component=key,
                subject=row.subject,
                signal_day=day,
                value=-row.value,
                available_time=builds[day][0],
                revision_time=builds[day][0],
            )
            for day in signal_days
            if day in builds
            for row in builds[day][1]
            if row.coverage == "computed" and row.value is not None
        )
        if source.trailing_ic is None:
            return StrategyInputs(scores=scores, **base)
        spec = source.trailing_ic
        position = {day: index for index, day in enumerate(full)}
        first = max(position[signal_days[0]] - spec.ic_window_sessions + 1, 0)
        last = position[signal_days[-1]] - spec.horizon_sessions - 1
        reader = OutcomeLabels(
            store, LabelReach(as_of=request.as_of, years=TWO_YEARS, exchange=EXCHANGE)
        )
        study = FactorICStudy(
            FactorICSpec(
                definition=REVERSAL,
                method=spec.ic_method,
                min_securities=spec.min_ic_securities,
                min_as_ofs=2,
            )
        )
        observations = []
        for day in full[first : last + 1]:
            if day not in builds:
                continue
            build, rows = builds[day]
            window = reader.window(build, horizon=parse_horizon(f"{spec.horizon_sessions}d"))
            labels = {
                row.subject: label
                for row in rows
                if row.coverage == "computed" and row.value is not None
                if (label := reader.label(row.subject, window)) is not None
            }
            point = study.measure(
                ic_cross_section(
                    as_of=build,
                    tier="raw",
                    rows=[(row.subject, row.value, row.coverage) for row in rows],
                    labels=labels,
                )
            )
            observations.append(
                ICObservation(
                    component=key,
                    prediction_day=day,
                    known_at=max(build, instants[window.exit_day]),
                    ic=point.ic,
                )
            )
        return StrategyInputs(scores=scores, ic_observations=tuple(observations), **base)
    spec = source.walk_forward
    model = request.model
    position = {day: index for index, day in enumerate(full)}
    refit_days = tuple(
        day for day in sessions[:: spec.refit_every_sessions] if day <= signal_days[-1]
    )
    last_refit = position[refit_days[-1]]
    newest = last_refit - spec.embargo_sessions - spec.horizon_sessions - 2
    run = ModelRunRequest(
        declaration=model.declaration,
        columns=request.columns,
        missing=spec.missing,
        start=full[max(position[sessions[0]] - spec.train_sessions + 1, 0)],
        end=full[newest],
        as_of=request.as_of,
        years=TWO_YEARS,
        exchange=EXCHANGE,
        horizon=parse_horizon(f"{spec.horizon_sessions}d"),
        minimum_scored_ratio=0.0,
        shelf_life=None,
        config_digest=UNFILED_CONFIG_DIGEST,
        declared_feature_version=None,
    )
    panel = training_panel(store, run, deadline=instants[full[last_refit - spec.embargo_sessions]])
    assert panel is not None
    fits = walk_forward_fits(
        model,
        panel.examples,
        feature_ids=panel.feature_ids,
        spec=spec,
        calendar=full,
        instants=instants,
        refit_days=refit_days,
    )
    rows: list[ScoreRow] = []
    fit_for_day: dict[date, Any] = {}
    for day in signal_days:
        fit = usable_fit(fits, signal_day=day)
        if fit is None or fit.fitted is None:
            continue
        section = feature_cross_section(store, run, as_of=instants[day])
        batch = fit.fitted.predict(
            section.cross_section, predicted_at=instants[day], shelf_life=None
        )
        fit_for_day[day] = fit
        rows.extend(
            ScoreRow(
                component=MODEL_COMPONENT,
                subject=prediction.ts_code,
                signal_day=day,
                value=prediction.score,
                available_time=instants[day],
                revision_time=instants[day],
            )
            for prediction in batch.predictions
            if prediction.score is not None
        )
    return StrategyInputs(scores=tuple(rows), model_fits=fits, fit_for_day=fit_for_day, **base)


@pytest.mark.parametrize("kind", sorted(TWO_YEAR_KINDS))
def test_across_a_year_boundary_the_streamed_run_is_the_whole_range_reference(
    two_years: tuple[PanelStore, GeneratedPanel], kind: str
) -> None:
    """Period for period, fill for fill, weight for weight: the streamed run over 2026-12-15 ..
    2027-01-22 equals the same book fed by whole-range reads. One period signals on 12-31 and
    trades on 2027-01-04, and every source actually ranks on at least one signal day."""
    store, panel = two_years
    request = _two_year_request(panel, kind)
    streamed = backtest_strategy(store, request)
    reference = run_strategy_backtest(_reference_inputs(store, request, kind), request.spec)

    assert backtest_view(streamed) == backtest_view(reference)
    boundary = next(period for period in streamed.periods if period.start == BOUNDARY_SIGNAL)
    assert boundary.end.year == 2027
    assert {fill.day for fill in boundary.fills} <= {BOUNDARY_TRADE}
    assert any(not period.held for period in streamed.periods)
    assert any(period.fills for period in streamed.periods if period.start.year == 2027)
    if kind != "walk_forward":
        assert boundary.fills


def test_a_period_across_a_year_boundary_loads_each_years_adjustment_factors_once(
    two_years: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 12-31 signal trades on 2027-01-04, and each order reads the signal session's quote
    (2026, the participation cap) and the trade session's (2027). Two year slots keep both
    resident: the whole run loads the adjustment factors twice, once per year."""
    from openalpha_cn import strategy_view

    store, panel = two_years
    loads: list[tuple[int, ...]] = []
    real = strategy_view.load_adjustment_histories

    def counted(*args: Any, **kwargs: Any) -> Any:
        loads.append(tuple(kwargs["years"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(strategy_view, "load_adjustment_histories", counted)
    result = backtest_strategy(store, _two_year_request(panel, "static"))

    boundary = next(period for period in result.periods if period.start == BOUNDARY_SIGNAL)
    assert boundary.fills
    assert loads == [(2026,), (2026, 2027)]


def test_a_held_training_window_return_refuses_the_tolerance_its_chain_would_give() -> None:
    """`_held_example` drops `per_session`, the one input of `WindowReturn.tolerance`: the held
    return refuses the property rather than answering 0.0, and keeps every other number."""
    from alpha_model_fixtures import training_example

    from openalpha_cn.strategy_view import _held_example

    example = training_example(
        ts_code="000001.SZ",
        prediction_day=date(2026, 6, 1),
        features=(0.5,),
        target=0.02,
        horizon="5d",
    )
    held = _held_example(example)
    returned = held.label.window_return
    assert returned is not None and example.label.window_return is not None

    assert returned.per_session == ()
    assert held.target == example.target
    assert returned.adjusted == example.label.window_return.adjusted
    assert held.label.window.exit_day == example.label.window.exit_day
    with pytest.raises(AttributeError, match="tolerance"):
        _ = returned.tolerance
    assert _held_example(held) is held
