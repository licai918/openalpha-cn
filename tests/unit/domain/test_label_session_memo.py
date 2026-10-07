"""`V2-P6-022`: a label measured through `LabelSessionMemo` is the label measured without one.

A horizon-20 IC series labels every security over a window of 21 sessions on every prediction day,
so each `(security, session)` sits inside about 21 overlapping windows, and its registry and halt
verdict and its session return were derived afresh in every one of them: 114.9M `_session_refusals`
and 100.6M `session_returns` calls on one configuration of the research store. The memo keeps each
per-session answer for the windows that share it.

**What this file holds is that the memo is invisible.** Every window of a generated market is
labelled twice -- once with no memo, the arithmetic before `V2-P6-022`, and once through one memo
shared across every window -- and the two answers are compared as their full `repr`, every float
at full precision, every refusal's detail sentence, and every exception's type and message. The
market carries each shape a label can meet: a halt, a suspension that spans whole windows, a timed
halt into the close, a resumption, a missing bar, a listing and a delisting inside the range, a
registry snapshot the range runs past, a locked limit and an unpublished band, an ex-rights step,
`V2-P6-020`'s corroborated (both paths) and unknowable sessions, and three upstream defects that
refuse the run -- an unrecorded disagreement, a stale record and a factor series that stops short
-- plus a code the registry does not hold.

**And that it cannot serve an answer about other inputs.** Each entry is held beside the inputs it
was computed from and served only to a call that hands in those same inputs; the second half of
this file changes one input at a time under a warm memo -- a bar, the previous bar, the factor
series, the recorded decisions, the registry, the halt corpus -- and requires the answer the new
inputs give. A memo keyed on `(security, session)` alone fails every one of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.adjustment import AdjustmentHistory, FactorObservation
from openalpha_cn.domain.daily_prices import DailyBar, RecordedReturnPath
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    LABEL_REFUSAL_CODES,
    HaltCorpus,
    LabelSessionMemo,
    LabelWindow,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit, SuspensionRecord, build_suspension_day
from openalpha_cn.domain.stock_universe import SecurityLifecycle, StockUniverse
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGE = "SSE"
FIRST = date(2026, 3, 2)
LAST = date(2026, 7, 31)
CLOSED = frozenset({date(2026, 4, 6), date(2026, 5, 1), date(2026, 5, 4), date(2026, 6, 19)})

PLAIN = "600001.SH"
LISTS_LATE = "600002.SH"
DELISTS = "600003.SH"
SUSPENDED = "600004.SH"
HALTS = "600005.SH"
RECORDED = "600006.SH"
DEFECTS = "600007.SH"
LIMITS = "600008.SH"
SHORT_FACTORS = "600009.SH"
UNREGISTERED = "600010.SH"
CODES = (
    PLAIN,
    LISTS_LATE,
    DELISTS,
    SUSPENDED,
    HALTS,
    RECORDED,
    DEFECTS,
    LIMITS,
    SHORT_FACTORS,
    UNREGISTERED,
)


def _calendar() -> TradingCalendar:
    span = (LAST - FIRST).days + 1
    return build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(calendar_date=day, is_trading=day.weekday() < 5 and day not in CLOSED)
            for day in (FIRST + timedelta(days=offset) for offset in range(span))
        ],
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Market:
    calendar: TradingCalendar
    bars: Mapping[str, Mapping[date, DailyBar]]
    factors: Mapping[str, AdjustmentHistory]
    limits: Mapping[str, Mapping[date, PriceLimit]]
    halts: HaltCorpus
    universe: StockUniverse
    recorded: Mapping[tuple[str, date], RecordedReturnPath]


def _close(code_index: int, session_index: int) -> float:
    """A two-decimal close that moves every session, differently per security."""
    wobble = ((session_index * 7 + code_index * 3) % 11) - 5
    return round(10.0 + code_index + 0.13 * wobble + 0.01 * session_index, 2)


def _bar(code: str, day: date, close: float, pre_close: float, *, locked: bool = False) -> DailyBar:
    return DailyBar(
        ts_code=code,
        trade_date=day,
        open=close,
        high=close if locked else round(close * 1.01, 2),
        low=close if locked else round(close * 0.99, 2),
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=10000.0,
    )


def _market() -> _Market:
    calendar = _calendar()
    sessions = calendar.trading_days
    at = dict(enumerate(sessions))
    bars: dict[str, dict[date, DailyBar]] = {}
    factors: dict[str, AdjustmentHistory] = {}
    limits: dict[str, dict[date, PriceLimit]] = {}
    halt_rows: dict[date, list[SuspensionRecord]] = {}
    recorded: dict[tuple[str, date], RecordedReturnPath] = {}

    # Sessions each shape lands on, by index into the calendar.
    listing, delisting = 30, 50
    suspended = range(20, 46)
    single_halt, halt_into_close, halt_before_close, resumption, bare_gap = 10, 15, 16, 17, 25
    published_at, adjusted_at, unknowable_at = 12, 40, 60
    unrecorded_at, stale_at = 70, 33
    locked_up, locked_down, unbanded = (18, 19), (44,), (22, 23)
    factors_stop = 75
    ex_rights = {PLAIN: 35, RECORDED: 8, LIMITS: 52}

    def halt(code: str, index: int, kind: str, timing: str | None) -> None:
        halt_rows.setdefault(at[index], []).append(
            SuspensionRecord(ts_code=code, trade_date=at[index], suspend_type=kind, timing=timing)
        )

    for code_index, code in enumerate(CODES):
        steps: dict[int, float] = {}
        if code in ex_rights:
            steps[ex_rights[code]] = 1.25
        disagreements: set[int] = set()
        if code == RECORDED:
            disagreements = {published_at, adjusted_at, unknowable_at}
        if code == DEFECTS:
            disagreements = {unrecorded_at, stale_at}
        for index in disagreements:
            steps[index] = 2.0  # the factor moves and the published pre_close does not

        present = range(len(sessions))
        if code == LISTS_LATE:
            present = range(listing, len(sessions))
        if code == DELISTS:
            present = range(delisting)
        absent = set()
        if code == SUSPENDED:
            absent |= set(suspended)
            for index in suspended:
                halt(code, index, "S", None)
        if code == HALTS:
            absent |= {single_halt, bare_gap}
            halt(code, single_halt, "S", None)
            halt(code, halt_into_close, "S", "13:00-15:00")
            halt(code, halt_before_close, "S", "09:30-10:30")
            halt(code, resumption, "R", None)

        series: dict[date, DailyBar] = {}
        observations: list[FactorObservation] = []
        factor = 1.0
        previous_close: float | None = None
        for index in present:
            day = at[index]
            if index in steps:
                factor *= steps[index]
            if code != SHORT_FACTORS or index <= factors_stop:
                observations.append(FactorObservation(ts_code=code, observed_on=day, factor=factor))
            if index in absent:
                continue
            close = _close(code_index, index)
            if previous_close is None:
                pre_close = round(close * 0.98, 2)
            elif index in disagreements:
                pre_close = previous_close
            else:
                pre_close = round(previous_close / steps.get(index, 1.0), 2)
            locked = code == LIMITS and index in (*locked_up, *locked_down)
            series[day] = _bar(code, day, close, pre_close, locked=locked)
            if index in disagreements and code == RECORDED:
                path = {published_at: "published", adjusted_at: "adjusted"}.get(index)
                recorded[(code, day)] = RecordedReturnPath(
                    ts_code=code,
                    day=day,
                    close=close,
                    previous_close=previous_close,
                    implied_pre_close=previous_close / 2.0,
                    path=path,  # type: ignore[arg-type]
                )
            if code == DEFECTS and index == stale_at:
                recorded[(code, day)] = RecordedReturnPath(
                    ts_code=code,
                    day=day,
                    close=close + 0.01,
                    previous_close=previous_close,
                    implied_pre_close=previous_close / 2.0,
                    path="published",
                )
            previous_close = close
        bars[code] = series
        factors[code] = AdjustmentHistory(ts_code=code, observations=tuple(observations))
        bands: dict[date, PriceLimit] = {}
        for index in present:
            if code == LIMITS and index in unbanded:
                continue
            day = at[index]
            close = series[day].close if day in series else 10.0
            up, down = round(close * 1.1, 2), round(close * 0.9, 2)
            if code == LIMITS and index in locked_up:
                up = close
            if code == LIMITS and index in locked_down:
                down = close
            bands[day] = PriceLimit(ts_code=code, trade_date=day, up_limit=up, down_limit=down)
        limits[code] = bands

    lifecycles = []
    for code in CODES:
        if code == UNREGISTERED:
            continue
        lifecycles.append(
            SecurityLifecycle(
                ts_code=code,
                exchange=EXCHANGE,
                listed_on=at[listing] if code == LISTS_LATE else date(2001, 1, 1),
                delisted_on=at[delisting] if code == DELISTS else None,
            )
        )
    halts = halt_corpus_for_years(
        {day: build_suspension_day(day, rows) for day, rows in halt_rows.items()},
        years=(2026,),
    )
    return _Market(
        calendar=calendar,
        bars=bars,
        factors=factors,
        limits=limits,
        halts=halts,
        universe=StockUniverse(snapshot_date=at[len(sessions) - 12], securities=tuple(lifecycles)),
        recorded=recorded,
    )


def _windows(market: _Market, horizon_sessions: int) -> list[LabelWindow]:
    sessions = market.calendar.trading_days
    horizon = parse_horizon(f"{horizon_sessions}d")
    return [
        build_label_window(
            as_of=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=8.5),
            zone=SHANGHAI,
            horizon=horizon,
            calendar=market.calendar,
        )
        for day in sessions[: len(sessions) - horizon_sessions - 2]
    ]


def _answer(
    market: _Market,
    window: LabelWindow,
    code: str,
    *,
    memo: LabelSessionMemo | None,
    universe: StockUniverse | None = None,
    halts: HaltCorpus | None = None,
    bars: Mapping[date, DailyBar] | None = None,
    factors: AdjustmentHistory | None = None,
    recorded: Mapping[tuple[str, date], RecordedReturnPath] | None = None,
) -> str:
    """One label as a string that differs wherever the label or its refusal differs."""
    held = market.bars[code] if bars is None else bars
    try:
        label = label_outcome(
            window,
            ts_code=code,
            bars={day: held[day] for day in window.sessions if day in held},
            factors=market.factors[code] if factors is None else factors,
            limits={
                day: market.limits[code][day]
                for day in window.sessions
                if day in market.limits[code]
            },
            halts=market.halts if halts is None else halts,
            universe=market.universe if universe is None else universe,
            recorded=market.recorded if recorded is None else recorded,
            memo=memo,
        )
    except Exception as error:  # the exception *is* the answer being compared
        return f"raised {type(error).__name__}: {error}"
    return repr(label)


@pytest.fixture(scope="module")
def market() -> _Market:
    return _market()


def test_the_generated_market_reaches_every_shape_it_claims(market: _Market) -> None:
    """The comparison below is only as strong as the shapes it walks through, so they are
    counted: each refusal code and each raised fault must actually occur."""
    answers = [
        _answer(market, window, code, memo=None)
        for horizon in (1, 5, 20)
        for window in _windows(market, horizon)
        for code in CODES
    ]
    text = "\n".join(answers)
    for shape in (
        *(f"code={code!r}" for code in LABEL_REFUSAL_CODES),
        "recorded_sessions=(datetime.date(2026, 3, 18),)",  # published, rescaled
        "path='adjusted'",
        "raised PriceDataError",
        "and no upstream_defects record decides it",
        "A record for the session was judged on close",
        "raised AdjustmentHorizonError",
        "raised StockUniverseError",
        "window_return=WindowReturn(",
    ):
        assert shape in text, f"the generated market never produced {shape}"


@pytest.mark.parametrize("max_sessions", [1, 3, 32, 400])
@pytest.mark.parametrize("order", ["day_major", "security_major"])
def test_every_label_through_the_memo_is_the_label_without_it(
    market: _Market, max_sessions: int, order: str
) -> None:
    """Horizons 1, 5 and 20 through one shared memo, in two walk orders and at four bounds --
    from one session held, where almost every lookup misses, to the whole range."""
    memo = LabelSessionMemo(max_sessions=max_sessions)
    for horizon in (1, 5, 20):
        windows = _windows(market, horizon)
        pairs = (
            [(window, code) for window in windows for code in CODES]
            if order == "day_major"
            else [(window, code) for code in CODES for window in windows]
        )
        for window, code in pairs:
            expected = _answer(market, window, code, memo=None)
            assert _answer(market, window, code, memo=memo) == expected, (
                f"{code} over {window.entry_day}..{window.exit_day} at horizon {horizon}"
            )


# --- a warm memo never answers about other inputs ------------------------------------------


def _warm(market: _Market, code: str) -> tuple[LabelSessionMemo, LabelWindow]:
    """A memo that has labelled every 5-session window of `code`, and one of those windows."""
    memo = LabelSessionMemo(max_sessions=400)
    windows = _windows(market, 5)
    for window in windows:
        _answer(market, window, code, memo=memo)
    return memo, windows[3]


def _changed_bar(bar: DailyBar, factor: float) -> DailyBar:
    close = round(bar.close * factor, 2)
    return replace(bar, close=close, pct_chg=(close / bar.pre_close - 1.0) * 100.0)


Change = Callable[[_Market, LabelWindow], dict[str, object]]


def _a_bar(market: _Market, window: LabelWindow) -> dict[str, object]:
    held = dict(market.bars[PLAIN])
    held[window.sessions[2]] = _changed_bar(held[window.sessions[2]], 1.03)
    return {"bars": held}


def _the_previous_bar(market: _Market, window: LabelWindow) -> dict[str, object]:
    """The close the next link reads as its previous close moves by one tick, inside the
    `pre_close` tolerance, and the next session's own bar is the very object it was: only the
    previous close tells the next link's answer apart."""
    held = dict(market.bars[PLAIN])
    previous = held[window.sessions[1]]
    close = round(previous.close + 0.01, 2)
    held[window.sessions[1]] = replace(
        previous, close=close, pct_chg=(close / previous.pre_close - 1.0) * 100.0
    )
    return {"bars": held}


def _a_missing_bar(market: _Market, window: LabelWindow) -> dict[str, object]:
    """The same registry and halt corpus, and one session's bar gone."""
    held = dict(market.bars[PLAIN])
    del held[window.sessions[2]]
    return {"bars": held}


def _the_factor_series(market: _Market, window: LabelWindow) -> dict[str, object]:
    history = market.factors[PLAIN]
    return {
        "factors": AdjustmentHistory(
            ts_code=PLAIN,
            observations=tuple(
                replace(entry, factor=entry.factor * 1.5) for entry in history.observations
            ),
        )
    }


def _the_registry(market: _Market, window: LabelWindow) -> dict[str, object]:
    return {
        "universe": StockUniverse(
            snapshot_date=market.universe.snapshot_date,
            securities=tuple(
                replace(entry, delisted_on=window.sessions[3]) if entry.ts_code == PLAIN else entry
                for entry in market.universe.securities
            ),
        )
    }


def _the_halts(market: _Market, window: LabelWindow) -> dict[str, object]:
    day = window.sessions[2]
    rows = [SuspensionRecord(ts_code=PLAIN, trade_date=day, suspend_type="S", timing=None)]
    return {
        "halts": halt_corpus_for_years(
            {**market.halts.days, day: build_suspension_day(day, rows)}, years=(2026,)
        )
    }


@pytest.mark.parametrize(
    "change",
    [_a_bar, _the_previous_bar, _a_missing_bar, _the_factor_series, _the_registry, _the_halts],
    ids=lambda change: change.__name__.lstrip("_"),
)
def test_a_warm_memo_answers_for_the_inputs_it_is_handed(market: _Market, change: Change) -> None:
    memo, window = _warm(market, PLAIN)
    changed = change(market, window)
    expected = _answer(market, window, PLAIN, memo=None, **changed)  # type: ignore[arg-type]

    assert expected != _answer(market, window, PLAIN, memo=None), "the change changes nothing"
    assert _answer(market, window, PLAIN, memo=memo, **changed) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("drop", ["published", "adjusted"])
def test_a_warm_memo_answers_for_the_recorded_decisions_it_is_handed(
    market: _Market, drop: str
) -> None:
    """A decision withdrawn after the memo was warmed turns the session back into the refusal
    an unrecorded disagreement is."""
    memo = LabelSessionMemo(max_sessions=400)
    windows = _windows(market, 5)
    for window in windows:
        _answer(market, window, RECORDED, memo=memo)
    (key,) = [
        key for key, value in market.recorded.items() if value.path == drop and key[0] == RECORDED
    ]
    window = next(window for window in windows if key[1] in window.sessions[1:])
    kept = {other: value for other, value in market.recorded.items() if other != key}
    expected = _answer(market, window, RECORDED, memo=None, recorded=kept)

    assert "no upstream_defects record decides it" in expected
    assert _answer(market, window, RECORDED, memo=memo, recorded=kept) == expected


def test_a_warm_memo_answers_for_a_link_across_a_closed_session(market: _Market) -> None:
    """The same bar, the same previous close and the same factor series, and a different
    previous session: a calendar that closes one session of the window makes the link skip it,
    and the link's `previous_day` is then the only input that moved."""
    memo, window = _warm(market, PLAIN)
    closed = window.sessions[2]
    span = (LAST - FIRST).days + 1
    calendar = build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(
                calendar_date=day,
                is_trading=day.weekday() < 5 and day not in CLOSED and day != closed,
            )
            for day in (FIRST + timedelta(days=offset) for offset in range(span))
        ],
    )
    shifted = build_label_window(
        as_of=datetime.combine(window.prediction_day, datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=8.5),
        zone=SHANGHAI,
        horizon=window.horizon,
        calendar=calendar,
    )
    held = dict(market.bars[PLAIN])
    before = held[window.sessions[1]]
    close = held[closed].close
    held[window.sessions[1]] = replace(
        before, close=close, pct_chg=(close / before.pre_close - 1.0) * 100.0
    )
    expected = _answer(market, shifted, PLAIN, memo=None, bars=held)

    assert f"previous_day={window.sessions[1]!r}" in expected
    assert _answer(market, shifted, PLAIN, memo=memo, bars=held) == expected


def test_a_memo_bound_below_one_session_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one session"):
        LabelSessionMemo(max_sessions=0)


def test_the_memo_holds_no_more_sessions_than_its_bound(market: _Market) -> None:
    memo = LabelSessionMemo(max_sessions=4)
    for window in _windows(market, 20):
        for code in CODES:
            _answer(market, window, code, memo=memo)

    assert memo.session_count <= 4
