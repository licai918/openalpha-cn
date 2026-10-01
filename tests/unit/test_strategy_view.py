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

import dataclasses
import json
import math
import statistics
from array import array
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import (
    COMMIT,
    FIRST_FACTOR_YEAR,
    INDEX_LEVELS,
    PROBE_NEUTRALIZATION,
    PROBE_NEUTRALIZATIONS,
    PROBE_TRANSFORM,
    PROBE_TRANSFORMS,
    READ_AT,
    REVERSAL,
    TWO_YEAR_FACTOR_YEAR_START,
    TieredCorpus,
    stored_value,
    write_first_factor_year_corpus,
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
    TIER_ADMITTED_CODES,
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
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import halt_corpus_for_years
from openalpha_cn.domain.price_limits import TradingState
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendar
from openalpha_cn.feature_matrix import (
    FEATURE_DATE_ZONE,
    FeatureColumn,
    FeatureMatrixBlockedError,
    FeatureMatrixRequest,
    load_feature_cross_section,
)
from openalpha_cn.model_view import (
    MODEL_DATE_ZONE,
    UNFILED_CONFIG_DIGEST,
    LabelReach,
    ModelPanelUnreadableError,
    ModelRunRequest,
    OutcomeLabels,
    feature_cross_section,
    training_panel,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    factor_manifest_dataset,
    factor_observation_dataset,
    load_factor_observations,
    load_processed_factor_observations,
)
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
    StrategyPanelUnreadableError,
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


def _held_members_by_hand(
    quotes: Mapping[date, Mapping[str, SessionQuote]], period: Sequence[date]
) -> dict[str, tuple[Decimal, Decimal]]:
    """Each member's `(entry mark, last mark)` over one period, restated from the quotes the view
    hands the book: a member has a quote on the signal day and, at the execution session, is not
    halted and opens below its published limit-up; it enters at open x factor and its last mark
    is its last close x factor in the period. No recorded path in this panel, so no correction."""
    signal_day, trade_day, *_ = period
    members: dict[str, tuple[Decimal, Decimal]] = {}
    for subject in quotes[signal_day]:
        entry = quotes[trade_day].get(subject)
        if entry is None or entry.bar.suspended:
            continue
        assert entry.bar.up_limit is not None
        if entry.bar.open >= entry.bar.up_limit:
            continue
        last = next(quotes[day][subject] for day in reversed(period[1:]) if subject in quotes[day])
        members[subject] = (
            entry.bar.open * entry.adj_factor,
            last.bar.close * last.adj_factor,
        )
    return members


def _held_by_hand(
    quotes: Mapping[date, Mapping[str, SessionQuote]],
    period: Sequence[date],
    previous: Sequence[date] | None,
) -> tuple[Decimal, int]:
    """`EQUAL_WEIGHT_ALL_A_HELD` over one period (round 2): the previous period's members, worth
    their last mark / entry at the signal close, are sold at the execution open at open x factor
    (their last mark when they have no quote there); the proceeds go into this period's members
    equally, worth their last mark / entry at the end."""
    members = _held_members_by_hand(quotes, period)
    before = after = Decimal(1)
    if previous is not None:
        sold = _held_members_by_hand(quotes, previous)
        before = sum((last / entry for entry, last in sold.values()), Decimal(0))
        opens = quotes[period[1]]
        after = sum(
            (
                (opens[s].bar.open * opens[s].adj_factor if s in opens else last) / entry
                for s, (entry, last) in sold.items()
            ),
            Decimal(0),
        )
    held = sum((last / entry for entry, last in members.values()), Decimal(0))
    return after * held / (before * len(members)) - 1, len(members)


def test_the_held_benchmark_is_the_notional_book_bought_at_each_open(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The protocol's default now: every period's value is the hand restatement's, and some
    period's overnight factor is not 1 -- the panel's names gap between close and open."""
    store, panel = corpus
    request = _request(panel)
    result = backtest_strategy(store, request)
    inputs = load_strategy_inputs(store, request)

    assert set(request.spec.benchmarks) == {"000905.SH", EQUAL_WEIGHT_ALL_A_HELD}
    assert EQUAL_WEIGHT_ALL_A_HELD not in inputs.benchmark_returns
    previous: Sequence[date] | None = None
    without_overnight: list[Decimal] = []
    for period in result.periods:
        span = inputs.sessions[
            inputs.sessions.index(period.start) : inputs.sessions.index(period.end) + 1
        ]
        expected, members = _held_by_hand(inputs.quotes, span, previous)
        without_overnight.append(_held_by_hand(inputs.quotes, span, None)[0])
        assert members > 0
        assert period.benchmark_members == members
        assert period.benchmark_returns[EQUAL_WEIGHT_ALL_A_HELD] == expected.quantize(
            Decimal("0.0000000001")
        )
        previous = span
    assert [p.benchmark_returns[EQUAL_WEIGHT_ALL_A_HELD] for p in result.periods] != [
        value.quantize(Decimal("0.0000000001")) for value in without_overnight
    ]


def test_the_held_benchmark_is_computed_once_per_store_range_instant_and_exchange(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`V2-P6-024`: every configuration sharing a range and a schedule shares the benchmark,
    so a process computes each period once. The served answer is the fresh one; another
    configuration on the same schedule computes nothing; another range, or a store whose catalog
    moved, computes afresh."""
    store, panel = corpus
    computed: list[date] = []
    real = strategy_backtest._held_step

    def counted(quotes: Any, sessions: Sequence[date], *rest: Any) -> Any:
        computed.append(sessions[0])
        return real(quotes, sessions, *rest)

    monkeypatch.setattr(strategy_backtest, "_held_step", counted)
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


# --- V2-P6-025: a factor store that begins a year after every other dataset does ----------------
#
# The research store prices 2013 onward and builds its factor tiers from 2015-01-05 alone; the
# protocol's stage-2 lookbacks open on 2015-01-06. The walk-forward source asked the model plane
# for the year before its first training day, the model plane read the factor tiers over every
# year it was handed, and the factor store -- which holds no 2014 on purpose -- refused every
# configuration by `partition_missing`. Here the factor store begins on 2027-01-04 while the
# calendar, registry, prices and halts hold 2026 too, and the store it is held against holds the
# same builds plus December 2026's.

FACTOR_START_SIGNAL: Final[date] = date(2027, 1, 13)
"""The seventh session after the first build: a lookback of seven sessions opens on it exactly."""
FACTOR_START_BEFORE: Final[date] = date(2027, 1, 6)
"""Two sessions after the first build: the same lookback opens on 2026-12-24, before it."""
FACTOR_START_KINDS: Final[dict[str, dict[str, Any]]] = {
    "trailing_ic": {
        "components": (),
        "trailing_ic": {**TRAILING, "ic_window_sessions": 8},
    },
    "walk_forward": {"components": (), "walk_forward": TWO_YEAR_WALK_FORWARD},
}


@pytest.fixture(scope="module")
def factor_year_start(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view-factor-year-start")
    panel = write_two_year_corpus(root, builds_from=TWO_YEAR_FACTOR_YEAR_START)
    return PanelStore(root / "panel"), panel


def _factor_start_request(panel: GeneratedPanel, kind: str, *, start: date) -> Any:
    return strategy_request(
        combine="zscore_sum",
        transform=None,
        neutralization=None,
        start=start,
        end=panel.sessions[-1],
        as_of=panel.as_of,
        exchange=EXCHANGE,
        rebalance_every_sessions=4,
        holding_count=3,
        buffer_rank=None,
        max_industry_weight=None,
        benchmarks=(EQUAL_WEIGHT_ALL_A,),
        **FACTOR_START_KINDS[kind],
    )


def _model_run(store: PanelStore, request: Any, *, start: date, end: date) -> ModelRunRequest:
    """The run a refit whose window is `start..end` hands the model plane, taken from the feed
    `load_strategy_inputs` builds for `request` rather than restated here, so it cannot drift from
    `_ModelFeed._run` (the year before `start` through `end`'s, as `_refit` asks)."""
    feed = load_strategy_inputs(store, request, stream=True).feed
    assert isinstance(feed, strategy_view._ModelFeed)
    return feed._run(start=start, end=end, first_year=start.year - 1, last_year=end.year)


def test_the_factor_store_begins_a_year_after_every_other_dataset(
    factor_year_start: tuple[PanelStore, GeneratedPanel],
) -> None:
    """And a build's day, a prediction day and a factor partition's year are one zone's: the
    model plane reads only the years its range spans on the strength of it."""
    store, _ = factor_year_start

    assert MODEL_DATE_ZONE.key == FEATURE_DATE_ZONE == DEFAULT_DATE_TIMEZONE
    assert store.registered_years(factor_observation_dataset(REVERSAL)) == (2027,)
    assert store.registered_years(factor_manifest_dataset(REVERSAL)) == (2027,)
    for dataset in (TRADING_CALENDAR_DATASET, ADJ_FACTOR_DATASET, DAILY_DATASET):
        assert store.registered_years(dataset) == TWO_YEARS


@pytest.mark.parametrize("kind", sorted(FACTOR_START_KINDS))
def test_a_lookback_opening_on_the_first_factor_year_is_measured_as_if_the_year_before_were_held(
    factor_year_start: tuple[PanelStore, GeneratedPanel],
    two_years: tuple[PanelStore, GeneratedPanel],
    kind: str,
) -> None:
    """The protocol's stage-2 shape: the lookback opens on the factor store's first build.

    Measured, not refused -- and answered exactly as a store that also holds the year before's
    builds answers it, period for period and fit for fit, so that year contributes nothing."""
    store, panel = factor_year_start
    held, _ = two_years
    request = _factor_start_request(panel, kind, start=FACTOR_START_SIGNAL)

    answered = backtest_strategy(store, request)

    assert load_strategy_inputs(store, request).lookback_sessions[0] == (TWO_YEAR_FACTOR_YEAR_START)
    assert any(not period.held for period in answered.periods)
    if kind == "walk_forward":
        assert any(fit.refusal is None for fit in answered.model_fits)
    assert backtest_view(answered) == backtest_view(backtest_strategy(held, request))


@pytest.mark.parametrize("kind", sorted(FACTOR_START_KINDS))
def test_a_lookback_reaching_before_the_first_factor_year_is_refused_naming_the_partition(
    factor_year_start: tuple[PanelStore, GeneratedPanel], kind: str
) -> None:
    """A window that does ask about 2026's sessions is refused, never read as an empty year: the
    store cannot say whether a 2026 build would have scored, so it is not told that none did."""
    store, panel = factor_year_start
    request = _factor_start_request(panel, kind, start=FACTOR_START_BEFORE)

    with pytest.raises(StrategyPanelUnreadableError, match=r"year=2026 .*partition_missing"):
        backtest_strategy(store, request)


def test_a_cross_section_reads_the_year_before_only_when_its_instant_could_draw_on_it(
    factor_year_start: tuple[PanelStore, GeneratedPanel],
    two_years: tuple[PanelStore, GeneratedPanel],
) -> None:
    """At 16:30 on 2027-01-04 the newest visible build is that day's own, and the answer does not
    depend on 2026. At 09:00 the same morning it is 2026-12-31's -- the year before is read, and a
    store that does not hold it refuses by name rather than answering with nothing."""
    store, panel = factor_year_start
    held, _ = two_years
    run = _model_run(
        store,
        _factor_start_request(panel, "walk_forward", start=FACTOR_START_SIGNAL),
        start=TWO_YEAR_FACTOR_YEAR_START,
        end=TWO_YEAR_FACTOR_YEAR_START,
    )
    evening = session_publication_instant(TWO_YEAR_FACTOR_YEAR_START)
    morning = datetime.combine(TWO_YEAR_FACTOR_YEAR_START, time(9, 0), tzinfo=SHANGHAI)

    answered = feature_cross_section(store, run, as_of=evening)
    assert answered.as_of == evening
    assert answered == feature_cross_section(held, run, as_of=evening)
    assert feature_cross_section(held, run, as_of=morning).as_of == session_publication_instant(
        date(2026, 12, 31)
    )
    with pytest.raises(ModelPanelUnreadableError, match=r"year=2026 .*partition_missing"):
        feature_cross_section(store, run, as_of=morning)


def test_a_training_panel_opening_on_the_first_factor_year_is_the_one_a_fuller_store_labels(
    factor_year_start: tuple[PanelStore, GeneratedPanel],
    two_years: tuple[PanelStore, GeneratedPanel],
) -> None:
    store, panel = factor_year_start
    held, _ = two_years
    run = _model_run(
        store,
        _factor_start_request(panel, "walk_forward", start=FACTOR_START_SIGNAL),
        start=TWO_YEAR_FACTOR_YEAR_START,
        end=date(2027, 1, 8),
    )
    deadline = session_publication_instant(FACTOR_START_SIGNAL)

    answered = training_panel(store, run, deadline=deadline)

    assert answered is not None
    first = panel.sessions.index(TWO_YEAR_FACTOR_YEAR_START)
    assert {example.label.window.prediction_day for example in answered.examples} == set(
        panel.sessions[first : first + 5]
    )
    assert answered == training_panel(held, run, deadline=deadline)


def test_every_cross_section_across_the_year_boundary_is_the_whole_range_reads_newest_build(
    two_years: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Morning and evening of every session from 12-28 to 01-08, on the store holding both years:
    the cross section is the newest build at or before the instant among every build of both
    years, read here with one whole-range load, value for value."""
    store, panel = two_years
    run = _model_run(
        store,
        _factor_start_request(panel, "walk_forward", start=FACTOR_START_SIGNAL),
        start=date(2026, 12, 28),
        end=date(2027, 1, 8),
    )
    stored = load_factor_observations(store, REVERSAL, years=TWO_YEARS, as_of=panel.as_of)
    days = [day for day in panel.sessions if date(2026, 12, 28) <= day <= date(2027, 1, 8)]
    asked = [
        instant
        for day in days
        for instant in (
            datetime.combine(day, time(9, 0), tzinfo=SHANGHAI),
            session_publication_instant(day),
        )
    ]

    for instant in asked:
        newest = max(row.as_of for row in stored if row.as_of <= instant)
        expected = {
            row.subject: row.value
            for row in stored
            if row.as_of == newest and row.coverage == "computed" and row.value is not None
        }
        section = feature_cross_section(store, run, as_of=instant)
        assert section.as_of == newest
        assert {
            row.ts_code: row.values[0]
            for row in section.cross_section.rows
            if row.values[0] is not None
        } == expected


# --- V2-P6-025 (2): a calendar year that is not yet knowable at a build's instant ---------------
#
# The real `trade_cal` dates every row of year Y knowable from Y-01-01 00:00 Shanghai, so its 2016
# partition cannot be read at any 2015 instant (`not_yet_knowable`, judged per partition). The
# model plane cuts each training cross section at its build's own instant and read the calendar
# over every year of the run -- the year after included, because the run's labels reach it -- so
# the first real-store refit was refused on 2016's partition at a 2015-01-06 build. Here the
# calendar is dated the real provider's way and every other byte of the two-year corpus is kept.


@pytest.fixture(scope="module")
def yearly_calendar(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view-yearly-calendar")
    panel = write_two_year_corpus(root, calendar_published_yearly=True)
    return PanelStore(root / "panel"), panel


def test_the_next_years_calendar_is_not_yet_knowable_at_a_december_build(
    yearly_calendar: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The real store's shape, reproduced: at 16:30 on 2026-12-31 the 2026 calendar reads and a
    read naming 2027 as well is refused whole."""
    store, _ = yearly_calendar
    instant = session_publication_instant(BOUNDARY_SIGNAL)

    assert load_trading_calendar(store, exchange=EXCHANGE, years=(2026,), as_of=instant)
    with pytest.raises(Exception, match="not_yet_knowable"):
        load_trading_calendar(store, exchange=EXCHANGE, years=TWO_YEARS, as_of=instant)


@pytest.mark.parametrize("kind", sorted(TWO_YEAR_KINDS))
def test_a_run_across_the_year_boundary_is_answered_as_if_the_calendar_were_known_early(
    yearly_calendar: tuple[PanelStore, GeneratedPanel],
    two_years: tuple[PanelStore, GeneratedPanel],
    kind: str,
) -> None:
    """Training windows in December whose labels and deadlines reach January: measured, and
    answered period for period as the corpus whose whole calendar was knowable from its first
    day -- the 2027 partition's clocks change no answer, because no cross section cut in 2026
    resolves its session from a 2027 row."""
    store, panel = yearly_calendar
    fuller, _ = two_years
    request = _two_year_request(panel, kind)

    answered = backtest_strategy(store, request)

    assert backtest_view(answered) == backtest_view(backtest_strategy(fuller, request))
    if kind == "walk_forward":
        assert any(fit.refusal is None for fit in answered.model_fits)


def test_a_cross_section_resolves_its_session_on_the_calendar_its_own_instant_could_read(
    yearly_calendar: tuple[PanelStore, GeneratedPanel],
    two_years: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point in time still holds: the calendar is read at the build's instant, over the years
    at or before it -- never at the run's later `as_of` -- and the answer is the fuller store's."""
    from openalpha_cn import feature_matrix

    store, panel = yearly_calendar
    fuller, _ = two_years
    run = _model_run(
        store, _two_year_request(panel, "walk_forward"), start=BOUNDARY_START, end=BOUNDARY_TRADE
    )
    assert run.years == TWO_YEARS
    reads: list[tuple[tuple[int, ...], datetime]] = []
    real = feature_matrix.load_trading_calendar

    def recorded(*args: Any, **kwargs: Any) -> Any:
        reads.append((tuple(kwargs["years"]), kwargs["as_of"]))
        return real(*args, **kwargs)

    for instant in (
        session_publication_instant(BOUNDARY_SIGNAL),
        datetime.combine(BOUNDARY_TRADE, time(9, 0), tzinfo=SHANGHAI),
        session_publication_instant(BOUNDARY_TRADE),
    ):
        expected = feature_cross_section(fuller, run, as_of=instant)
        monkeypatch.setattr(feature_matrix, "load_trading_calendar", recorded)
        answered = feature_cross_section(store, run, as_of=instant)
        monkeypatch.setattr(feature_matrix, "load_trading_calendar", real)

        assert answered == expected
        assert reads[-1] == (
            tuple(year for year in TWO_YEARS if year <= answered.as_of.astimezone(SHANGHAI).year),
            answered.as_of,
        )
    assert [years for years, _ in reads] == [(2026,), (2026,), TWO_YEARS]


# --- V2-P6-025 review: the fallbacks, and two processed columns a year apart -------------------


def test_a_run_naming_no_year_its_reads_could_use_is_refused_by_the_partition_it_named(
    factor_year_start: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Both narrowings fall back to the years the run declared when none of them is one the
    answer could come from, so a refusal stays the partition's rather than turning into an
    empty answer: a training range in 2027 declaring only 2026, and a cross section at a 2027
    instant declaring only 2028."""
    store, panel = factor_year_start
    run = _model_run(
        store,
        _factor_start_request(panel, "walk_forward", start=FACTOR_START_SIGNAL),
        start=TWO_YEAR_FACTOR_YEAR_START,
        end=date(2027, 1, 8),
    )

    with pytest.raises(ModelPanelUnreadableError, match=r"year=2026 .*partition_missing"):
        training_panel(
            store,
            dataclasses.replace(run, years=(2026,)),
            deadline=session_publication_instant(FACTOR_START_SIGNAL),
        )
    with pytest.raises(ModelPanelUnreadableError, match=r"year=2028 .*partition_missing"):
        feature_cross_section(
            store,
            dataclasses.replace(run, years=(2028,)),
            as_of=session_publication_instant(date(2027, 1, 8)),
        )


PROCESSED_TO_DECEMBER: Final = PROBE_TRANSFORM.model_copy(update={"key": "probe_zscore_december"})
"""A second transform of the same raw builds, written only through 2026-12-31."""


@pytest.fixture(scope="module")
def two_processed(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view-two-processed")
    panel = write_two_year_corpus(
        root,
        processed={PROBE_TRANSFORM: date(2027, 1, 22), PROCESSED_TO_DECEMBER: BOUNDARY_SIGNAL},
    )
    return PanelStore(root / "panel"), panel


def test_two_processed_columns_whose_newest_builds_sit_in_two_years_walk_back_one_at_a_time(
    two_processed: tuple[PanelStore, GeneratedPanel],
) -> None:
    """At 16:30 on 2027-01-04 one processed column's newest build is that day's and the other's
    is 2026-12-31's, which only a walk back into 2026 finds: each column walks on its own, and the
    pair is refused naming both instants. At 09:00 the same morning both walk back to
    2026-12-31, and the cross section carries each column's stored values there."""
    store, panel = two_processed
    january = FeatureColumn(definition=REVERSAL, tier="processed", transform=PROBE_TRANSFORM)
    december = FeatureColumn(definition=REVERSAL, tier="processed", transform=PROCESSED_TO_DECEMBER)
    evening = session_publication_instant(BOUNDARY_TRADE)
    year_end = session_publication_instant(BOUNDARY_SIGNAL)
    morning = datetime.combine(BOUNDARY_TRADE, time(9, 0), tzinfo=SHANGHAI)

    def section(as_of: datetime, *columns: FeatureColumn) -> Any:
        request = FeatureMatrixRequest(
            columns=columns, years=TWO_YEARS, exchange=EXCHANGE, as_ofs=(as_of,)
        )
        return load_feature_cross_section(store, request, as_of=as_of)

    assert section(evening, january).as_of == evening
    assert section(evening, december).as_of == year_end
    with pytest.raises(FeatureMatrixBlockedError) as refused:
        section(evening, january, december)
    assert evening.astimezone(UTC).isoformat() in str(refused.value)
    assert year_end.astimezone(UTC).isoformat() in str(refused.value)

    shared = section(morning, january, december)
    assert shared.as_of == year_end
    for index, column in enumerate(
        sorted((january, december), key=lambda column: column.feature_id)
    ):
        stored = {
            row.subject: row.value
            for row in load_processed_factor_observations(
                store, REVERSAL, column.transform, years=TWO_YEARS, as_of=panel.as_of
            )
            if row.as_of == year_end
            and row.coverage in TIER_ADMITTED_CODES["processed"]
            and row.value is not None
        }
        assert stored
        assert {
            row.ts_code: row.values[index]
            for row in shared.cross_section.rows
            if row.values[index] is not None
        } == stored


# --- V2-P6-025 (3): a walk-forward re-read each factor year once per cross section --------------
#
# With the calendar fixed, the research-store probe ran twenty minutes inside its first refit's
# training matrix. Each training cross section re-read every declared column's whole year
# partition at its own instant: 15.3 s for one processed year of one factor (686,039 rows,
# measured), times 21 columns, times the ~446 cross sections of a 488-session window, and again
# for every later refit and every scored signal day. That is days of reading for one source.


@pytest.mark.parametrize("kind", ["walk_forward"])
def test_a_walk_forward_run_reads_each_factor_partition_once(
    two_years: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """Every refit and every scored signal day across the year boundary, one read per factor
    partition -- and the answer is still the whole-range reference's
    (`test_across_a_year_boundary_the_streamed_run_is_the_whole_range_reference`)."""
    from openalpha_cn import feature_matrix

    store, panel = two_years
    reads: list[tuple[str, tuple[int, ...]]] = []
    real = feature_matrix.load_factor_observations

    def counted(*args: Any, **kwargs: Any) -> Any:
        reads.append((args[1].qualified_key, tuple(kwargs["years"])))
        return real(*args, **kwargs)

    monkeypatch.setattr(feature_matrix, "load_factor_observations", counted)
    result = backtest_strategy(store, _two_year_request(panel, kind))

    assert sum(fit.refusal is None for fit in result.model_fits) >= 2
    assert sorted(reads) == [(REVERSAL.qualified_key, (2026,)), (REVERSAL.qualified_key, (2027,))]


# --- V2-P6-025: the brief's shape -- factors from Y, prices from Y-1, a run starting in Y+2 ------
#
# `strategy_fixtures.write_first_factor_year_corpus`: every session of 2025, 2026 and 2027 priced,
# January 2028 too, factor builds from 2026's first session, and the calendar dated the real
# provider's way. A run starting on 2028-01-10 with a window opening on 2026-01-05 reaches back
# three calendar years (`_lookback`), so its first refit asks the model plane for 2025 as well --
# the stage-2 configs' 2014 -- while every scored day and every IC's label reads at its own
# instant across two year boundaries.

RESEARCH_START: Final[date] = date(2028, 1, 10)
RESEARCH_FIRST_BUILD: Final[date] = date(FIRST_FACTOR_YEAR, 1, 5)


@pytest.fixture(scope="module")
def research_shape(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("strategy-view-research-shape")
    panel = write_first_factor_year_corpus(root)
    return PanelStore(root / "panel"), panel


@pytest.fixture(scope="module")
def research_shape_fuller(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[PanelStore, GeneratedPanel]:
    """The same corpus with factor builds from 2025 as well."""
    root = tmp_path_factory.mktemp("strategy-view-research-shape-fuller")
    panel = write_first_factor_year_corpus(root, builds_from_year=FIRST_FACTOR_YEAR - 1)
    return PanelStore(root / "panel"), panel


@pytest.fixture(scope="module")
def research_shape_without_2027(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[PanelStore, GeneratedPanel]:
    """The same corpus with 2027's raw observation partition deleted afterwards."""
    root = tmp_path_factory.mktemp("strategy-view-research-shape-without-2027")
    panel = write_first_factor_year_corpus(root)
    store = PanelStore(root / "panel")
    assert store.remove_partition(factor_observation_dataset(REVERSAL), FIRST_FACTOR_YEAR + 1)
    return store, panel


def _research_request(panel: GeneratedPanel, kind: str) -> Any:
    window = panel.sessions.index(RESEARCH_START) - panel.sessions.index(RESEARCH_FIRST_BUILD) + 1
    source = (
        {"walk_forward": {**WALK_FORWARD, "train_sessions": window, "refit_every_sessions": 4}}
        if kind == "walk_forward"
        else {"trailing_ic": {**TRAILING, "ic_window_sessions": window}}
    )
    return strategy_request(
        combine="zscore_sum",
        transform=None,
        neutralization=None,
        components=(),
        start=RESEARCH_START,
        end=panel.sessions[-1],
        as_of=panel.as_of,
        exchange=EXCHANGE,
        rebalance_every_sessions=4,
        holding_count=3,
        buffer_rank=None,
        max_industry_weight=None,
        benchmarks=(EQUAL_WEIGHT_ALL_A,),
        **source,
    )


def test_the_research_shape_is_the_research_stores(
    research_shape: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Factor partitions from Y, every other dataset from Y-1; a lookback opening on Y's first
    session over three calendar years; and Y's calendar unreadable at any instant of Y-1."""
    store, panel = research_shape
    request = _research_request(panel, "walk_forward")

    assert store.registered_years(factor_observation_dataset(REVERSAL)) == (2026, 2027, 2028)
    for dataset in (TRADING_CALENDAR_DATASET, ADJ_FACTOR_DATASET, DAILY_DATASET):
        assert store.registered_years(dataset) == (2025, 2026, 2027, 2028)
    inputs = load_strategy_inputs(store, request, stream=True)
    assert inputs.lookback_sessions[0] == RESEARCH_FIRST_BUILD
    feed = inputs.feed
    assert isinstance(feed, strategy_view._ModelFeed)
    assert feed._years == (2025, 2026, 2027, 2028)
    with pytest.raises(Exception, match="not_yet_knowable"):
        load_trading_calendar(
            store,
            exchange=EXCHANGE,
            years=(2025, 2026),
            as_of=session_publication_instant(date(2025, 12, 31)),
        )


@pytest.mark.parametrize("kind", ["trailing_ic", "walk_forward"])
def test_a_run_from_y_plus_2_whose_lookback_opens_on_y_is_the_one_a_fuller_store_answers(
    research_shape: tuple[PanelStore, GeneratedPanel],
    research_shape_fuller: tuple[PanelStore, GeneratedPanel],
    kind: str,
) -> None:
    """Measured, not refused, and period for period, fit for fit, IC for IC the answer of a
    store that also holds Y-1's builds: the year before the factor store begins contributes
    nothing to a window that opens on its first build."""
    store, panel = research_shape
    fuller, _ = research_shape_fuller
    request = _research_request(panel, kind)

    answered = backtest_strategy(store, request)

    assert any(not period.held for period in answered.periods)
    if kind == "walk_forward":
        assert sum(fit.refusal is None for fit in answered.model_fits) >= 2
    assert backtest_view(answered) == backtest_view(backtest_strategy(fuller, request))


@pytest.mark.parametrize("kind", ["trailing_ic", "walk_forward"])
def test_a_factor_partition_deleted_inside_the_build_range_is_refused_by_name(
    research_shape_without_2027: tuple[PanelStore, GeneratedPanel], kind: str
) -> None:
    """2027 sits between the factor store's first and last builds and every window of the run
    spans it: its absence is refused, never read as a year that held nothing."""
    store, panel = research_shape_without_2027

    with pytest.raises(StrategyPanelUnreadableError, match=r"year=2027 .*partition_missing"):
        backtest_strategy(store, _research_request(panel, kind))


# --- V2-P6-025 (4): the registry read once per run, not once per cross section ----------------
#
# With each factor partition read once, the forest-light probe completed in 8,544 s, and the
# largest remaining cost was the security registry: each training and scored cross section read
# it at its own instant, 1.6 s and 713 DuckDB statements a call on the research store. The
# equivalence of the read-once answer at every instant is held in
# `tests/integration/panel/test_event_dated_visible_reads.py`; this holds that a walk-forward
# takes it, and the year-boundary whole-range reference above still holds its answer.


def test_a_walk_forward_run_reads_the_registry_once_per_span_of_years(
    research_shape: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    from openalpha_cn import feature_matrix
    from openalpha_cn.panel_ingest import StockUniverseReader

    store, panel = research_shape
    made: list[StockUniverseReader] = []
    asked: list[datetime] = []

    class Recorded(StockUniverseReader):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

        def at(self, as_of: datetime) -> Any:
            asked.append(as_of)
            return super().at(as_of)

    real = feature_matrix.load_stock_universe
    per_instant: list[datetime] = []

    def counted(*args: Any, **kwargs: Any) -> Any:
        per_instant.append(kwargs["as_of"])
        return real(*args, **kwargs)

    monkeypatch.setattr(feature_matrix, "StockUniverseReader", Recorded)
    monkeypatch.setattr(feature_matrix, "load_stock_universe", counted)
    result = backtest_strategy(store, _research_request(panel, "walk_forward"))

    assert sum(fit.refusal is None for fit in result.model_fits) >= 2
    assert per_instant == []
    assert made
    assert all(reader.reads == 1 for reader in made)
    assert len(asked) > 10 * len(made)


# --- V2-P6-026: walk-forward fits shared across the configurations one process measures ---------
#
# A research stage runs many strategies over one walk-forward source, and every one refitted the
# same models. `backtest_strategy(fit_cache=...)` serves a refit from a cache keyed by everything
# the fit reads (`strategy_view.WalkForwardFitKey`); each test below holds an answer served from a
# shared cache to the answer of the same request run alone, byte for byte, and counts the refits
# the shared run actually made.


def _answer(result: Any) -> str:
    """The whole answer as the CLI prints it, as one string: byte-identical means equal here."""
    return json.dumps(backtest_view(result), sort_keys=True)


def _counted_refits(monkeypatch: pytest.MonkeyPatch) -> tuple[list[date], list[date]]:
    """The refit days `walk_forward_fits` fitted, and the first day of every training panel
    labelled, from here on: the two costs a refit is made of."""
    fitted: list[date] = []
    labelled: list[date] = []
    real_fits = strategy_view.walk_forward_fits
    real_panel = strategy_view.training_panel

    def fits(*args: Any, **kwargs: Any) -> Any:
        fitted.extend(kwargs["refit_days"])
        return real_fits(*args, **kwargs)

    def panel(store: PanelStore, run: ModelRunRequest, **kwargs: Any) -> Any:
        labelled.append(run.start)
        return real_panel(store, run, **kwargs)

    monkeypatch.setattr(strategy_view, "walk_forward_fits", fits)
    monkeypatch.setattr(strategy_view, "training_panel", panel)
    return fitted, labelled


def _walk_forward_request(panel: GeneratedPanel, model: Mapping[str, Any], **rules: Any) -> Any:
    return _request(
        panel, components=(), walk_forward=dict(model), **{"rebalance_every_sessions": 2, **rules}
    )


STRATEGY_VARIANTS: Final[tuple[dict[str, Any], ...]] = (
    {},
    {"holding_count": 4, "max_industry_weight": Decimal("0.25")},
    {"rebalance_every_sessions": 3, "buffer_rank": 5},
    {"holding_count": 2, "rebalance_every_sessions": 1, "buffer_rank": 3},
)
"""Step 2b's shape at fixture scale: one source, the holding, rebalance, buffer and cap varied."""


def test_strategies_of_one_walk_forward_source_share_its_fits_and_answer_as_if_alone(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four strategies of one model, run in turn over one cache: each answer is its answer with
    a fresh cache, and only the first run refits -- every later one makes no fit and labels no
    training panel."""
    store, panel = corpus
    requests = [_walk_forward_request(panel, WALK_FORWARD, **rules) for rules in STRATEGY_VARIANTS]
    alone = [
        _answer(backtest_strategy(store, request, fit_cache=strategy_view.WalkForwardFitCache()))
        for request in requests
    ]
    fitted, labelled = _counted_refits(monkeypatch)
    cache = strategy_view.WalkForwardFitCache()
    shared: list[str] = []
    refits: list[int] = []
    panels: list[int] = []
    for request in requests:
        before = (len(fitted), len(labelled))
        result = backtest_strategy(store, request, fit_cache=cache)
        shared.append(_answer(result))
        refits.append(len(fitted) - before[0])
        panels.append(len(labelled) - before[1])

    assert shared == alone
    first = backtest_strategy(store, requests[0])
    assert refits[0] == len(first.model_fits) > 0
    assert sum(fit.refusal is None for fit in first.model_fits) >= 2  # real fits, not refusals
    assert panels[0] > 0
    assert refits[1:] == [0, 0, 0]
    assert panels[1:] == [0, 0, 0]


def test_a_run_past_the_cached_refits_fits_only_the_new_ones_as_if_alone(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shorter run fills the cache; a longer one is served its refits and fits the rest on a
    training window it never built incrementally -- and still answers as it does alone."""
    store, panel = corpus
    model = {**WALK_FORWARD, "refit_every_sessions": 1}
    short = _walk_forward_request(panel, model, end=panel.sessions[6])
    long = _walk_forward_request(panel, model)
    alone = _answer(backtest_strategy(store, long))
    cache = strategy_view.WalkForwardFitCache()
    held = {fit.refit_day for fit in backtest_strategy(store, short, fit_cache=cache).model_fits}
    fitted, _ = _counted_refits(monkeypatch)

    answered = backtest_strategy(store, long, fit_cache=cache)

    assert _answer(answered) == alone
    new = [fit.refit_day for fit in answered.model_fits if fit.refit_day not in held]
    assert new and fitted == new
    assert any(fit.refusal is None for fit in answered.model_fits if fit.refit_day in new)


MODEL_BASE: Final[dict[str, Any]] = {**WALK_FORWARD, "train_sessions": 7}
TREES: Final[dict[str, Any]] = {
    **MODEL_BASE,
    "family": "boosted_rank_trees",
    "hyperparameters": {
        "tree_count": 3,
        "max_depth": 1,
        "learning_rate": 0.1,
        "min_leaf_securities": 2,
    },
}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(MODEL_BASE, {**MODEL_BASE, "seed": 1}, id="seed"),
        pytest.param(MODEL_BASE, {**MODEL_BASE, "code_commit": "fedcba7654321"}, id="code_commit"),
        pytest.param(MODEL_BASE, {**MODEL_BASE, "missing": "drop_security"}, id="missing"),
        pytest.param(MODEL_BASE, {**MODEL_BASE, "train_sessions": 8}, id="train_sessions"),
        pytest.param(MODEL_BASE, {**MODEL_BASE, "embargo_sessions": 2}, id="embargo_sessions"),
        pytest.param(
            TREES,
            {**TREES, "hyperparameters": {**TREES["hyperparameters"], "tree_count": 4}},
            id="hyperparameter",
        ),
    ],
)
def test_models_that_differ_in_anything_a_fit_reads_never_share_one(
    corpus: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
    first: dict[str, Any],
    second: dict[str, Any],
) -> None:
    """The second model, run after the first over one cache, refits every refit it would alone
    and answers as it would alone. `train_sessions` and `embargo_sessions` are not in the model's
    declaration, which is all of the model the daily path's key held (`V2-P6-011` round 11)."""
    store, panel = corpus
    request = _walk_forward_request(panel, second)
    alone = backtest_strategy(store, request)
    cache = strategy_view.WalkForwardFitCache()
    backtest_strategy(store, _walk_forward_request(panel, first), fit_cache=cache)
    fitted, _ = _counted_refits(monkeypatch)

    answered = backtest_strategy(store, request, fit_cache=cache)

    assert _answer(answered) == _answer(alone)
    assert fitted == [fit.refit_day for fit in alone.model_fits]


@pytest.fixture(scope="module")
def corpus_twin(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    """`corpus` written again, to a second store."""
    root = tmp_path_factory.mktemp("strategy-view-twin")
    panel = write_strategy_corpus(root)
    return PanelStore(root / "panel"), panel


def test_one_request_over_two_stores_never_shares_a_fit(
    corpus: tuple[PanelStore, GeneratedPanel],
    corpus_twin: tuple[PanelStore, GeneratedPanel],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same request read out of another store refits everything, though this one was written
    from the same corpus: a cache cannot see that two stores hold the same rows, so the store is
    in the key."""
    store, panel = corpus
    other, _ = corpus_twin
    request = _walk_forward_request(panel, WALK_FORWARD)
    alone = backtest_strategy(other, request)
    cache = strategy_view.WalkForwardFitCache()
    backtest_strategy(store, request, fit_cache=cache)
    fitted, _ = _counted_refits(monkeypatch)

    assert _answer(backtest_strategy(other, request, fit_cache=cache)) == _answer(alone)
    assert fitted == [fit.refit_day for fit in alone.model_fits]


def test_a_later_start_whose_refits_read_the_same_window_is_served_them(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting on s3 instead of s1 moves no refit's window here -- the stored calendar opens on
    s0 for both, and both read 2026 alone -- so s3's, s5's and s7's fits are the earlier run's,
    and the later start answers as it does alone."""
    store, panel = corpus
    later = _walk_forward_request(panel, WALK_FORWARD, start=panel.sessions[3])
    alone = _answer(backtest_strategy(store, later))
    cache = strategy_view.WalkForwardFitCache()
    backtest_strategy(store, _walk_forward_request(panel, WALK_FORWARD), fit_cache=cache)
    fitted, _ = _counted_refits(monkeypatch)

    assert _answer(backtest_strategy(store, later, fit_cache=cache)) == alone
    assert fitted == []


def test_a_start_whose_training_window_reads_other_years_never_shares_a_fit(
    research_shape: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two starts ten sessions apart refit on the same two sessions of January Y+2 (where a build
    lands on every session), on the same thirty-session window opening in November of Y+1, and
    do not read it alike: the run from Y+2's first session reaches one calendar year back
    (`_lookback`), so its window's labels are read without Y's halts and adjustment factors --
    the year before the window opens -- which the run from December of Y+1 reads. Its fits are
    not the other's."""
    store, panel = research_shape
    first = next(day for day in panel.sessions if day.year == FIRST_FACTOR_YEAR + 2)
    at = panel.sessions.index(first)
    model = {**WALK_FORWARD, "train_sessions": 30, "refit_every_sessions": 10}

    def request(start: date) -> Any:
        return strategy_request(
            combine="zscore_sum",
            transform=None,
            neutralization=None,
            components=(),
            start=start,
            end=panel.sessions[at + 15],
            as_of=panel.as_of,
            exchange=EXCHANGE,
            rebalance_every_sessions=5,
            holding_count=3,
            buffer_rank=None,
            max_industry_weight=None,
            benchmarks=(EQUAL_WEIGHT_ALL_A,),
            walk_forward=model,
        )

    december, january = request(panel.sessions[at - 10]), request(first)
    alone = backtest_strategy(store, january)
    assert [fit.refit_day for fit in alone.model_fits] == [first, panel.sessions[at + 10]]
    assert any(fit.refusal is None for fit in alone.model_fits)
    cache = strategy_view.WalkForwardFitCache()
    earlier = backtest_strategy(store, december, fit_cache=cache)
    assert {fit.refit_day for fit in alone.model_fits} <= {
        fit.refit_day for fit in earlier.model_fits
    }
    fitted, _ = _counted_refits(monkeypatch)

    assert _answer(backtest_strategy(store, january, fit_cache=cache)) == _answer(alone)
    assert fitted == [first, panel.sessions[at + 10]]


def test_a_bounded_cache_refits_what_it_evicted_and_answers_as_if_alone(
    corpus: tuple[PanelStore, GeneratedPanel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two fits held, four asked for in order: every one was evicted before it was asked again,
    so the second strategy refits all four -- and its answer does not move."""
    store, panel = corpus
    requests = [_walk_forward_request(panel, WALK_FORWARD, **rules) for rules in STRATEGY_VARIANTS]
    alone = _answer(backtest_strategy(store, requests[1]))
    cache = strategy_view.WalkForwardFitCache(max_fits=2)
    backtest_strategy(store, requests[0], fit_cache=cache)
    assert len(cache) == 2
    fitted, _ = _counted_refits(monkeypatch)

    assert _answer(backtest_strategy(store, requests[1], fit_cache=cache)) == alone
    assert len(fitted) == 4
    assert len(cache) == 2


def _refused_fit(day: date) -> Any:
    return strategy_backtest.WalkForwardFit(
        refit_day=day,
        fitted=None,
        artifact=None,
        refusal="none",
        labels_known_at=None,
        example_count=0,
        prediction_day_count=0,
    )


def test_the_fit_cache_evicts_the_least_recently_used_fit() -> None:
    days = [date(2026, 1, day) for day in (5, 6, 7)]
    keys: list[Any] = [object() for _ in days]
    cache = strategy_view.WalkForwardFitCache(max_fits=2)
    cache[keys[0]] = _refused_fit(days[0])
    cache[keys[1]] = _refused_fit(days[1])
    assert cache.get(keys[0]) == _refused_fit(days[0])  # now the most recently used
    cache[keys[2]] = _refused_fit(days[2])

    assert list(cache) == [keys[0], keys[2]]
    assert keys[1] not in cache
    with pytest.raises(ValueError, match="at least one"):
        strategy_view.WalkForwardFitCache(max_fits=0)
