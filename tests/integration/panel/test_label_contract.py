"""The label contract over a real stored panel (`V2-P1-017`).

`tests/unit/domain/test_labels.py` drives the contract from hand-built domain carriers, which
is where the measured `000001.SZ` ex-rights numbers live. This file asks a different question:
does the same contract still work when every value reaches it through the real writers and the
real readers -- `write_generated_panel` into a `PanelStore`, then `load_trading_calendar`,
`load_stock_universe`, `load_adjustment_histories`, `load_suspensions`, `load_daily_bars` and
`load_price_limits` back out?

The window used by most of it spans four sessions of `V2-P1-014`'s generated panel and carries
three of its measured shapes at once: the limit-free sentinel on its entry, a timed
interruption in its middle, and the ex-rights factor step on its exit. So one label exercises
"a session with no published limit does not refuse", "an intraday halt traded", and "the return
is right across a factor step" against a panel that passed every write-time guard.

Nothing here is checked in: `write_generated_panel` builds the store under `tmp_path`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    AS_OF,
    DELISTED_SECURITY,
    EXCHANGE,
    YEAR,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.domain.adjustment import (
    AdjustmentHistory,
    FactorObservation,
    build_adjustment_history,
)
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    REFUSAL_DELISTED,
    REFUSAL_HALTED_INTO_THE_CLOSE,
    REFUSAL_HALTED_SESSION,
    REFUSAL_MISSING_BAR,
    REFUSAL_UNPUBLISHED_BAND,
    LabelError,
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit, TradingState
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_price_limits,
    load_stock_universe,
    load_suspensions,
    load_trading_calendar,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
BEFORE_THE_WINDOW = date(2026, 1, 4)
"""The Sunday before the generated panel's first session, used as a prediction day so the
entry lands on `panel.sessions[0]`."""

SHAPES = (
    "daily.close_moves_between_sessions",
    "daily.ex_rights_session",
    "price_limits.limit_free_sentinel",
    "suspension.timed_interruption",
    "universe.delisted_security",
)


def _labelled(
    tmp_path: Path,
    *,
    code: str,
    prediction_day: date,
    horizon: str,
    shapes: tuple[str, ...] = SHAPES,
    factors: AdjustmentHistory | None = None,
) -> tuple[GeneratedPanel, OutcomeLabel]:
    """Store a generated panel, read every input back out of it, and label one security."""
    panel = generate_panel(shapes=shapes)
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)

    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(YEAR,), as_of=AS_OF)
    window = build_label_window(
        as_of=datetime(
            prediction_day.year, prediction_day.month, prediction_day.day, 8, 30, tzinfo=UTC
        ),
        zone=SHANGHAI,
        horizon=parse_horizon(horizon),
        calendar=calendar,
    )
    bars: dict[date, DailyBar] = {}
    limits: dict[date, PriceLimit] = {}
    for day in window.sessions:
        session_bars = load_daily_bars(
            store, day=day, calendar=calendar, as_of=AS_OF, max_staleness=None
        )
        if code in session_bars:
            bars[day] = session_bars[code]
        session_limits = load_price_limits(
            store, day=day, calendar=calendar, as_of=AS_OF, max_staleness=None
        )
        if code in session_limits:
            limits[day] = session_limits[code]
    histories = load_adjustment_histories(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)
    label = label_outcome(
        window,
        ts_code=code,
        bars=bars,
        factors=histories[code] if factors is None else factors,
        limits=limits,
        halts=halt_corpus_for_years(
            load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None), years=(YEAR,)
        ),
        universe=load_stock_universe(store, years=(YEAR,), as_of=AS_OF, max_staleness=None),
    )
    return panel, label


def test_a_window_across_the_generated_ex_rights_step_is_labelled_on_both_correct_paths(
    tmp_path: Path,
) -> None:
    """The chained `close/pre_close` path and the telescoped factor path both give 20.75%.

    The naive `close_exit / close_entry - 1` gives 15.00% over the same four sessions, which is
    5.75 points away and about 16 times the bound the two correct paths were allowed. The panel
    was built with the ex-rights step on the exit session, so an implementation that read the
    raw closes lands on 15.00% here rather than merely drifting.
    """
    panel, label = _labelled(
        tmp_path, code="000001.SZ", prediction_day=BEFORE_THE_WINDOW, horizon="3d"
    )
    computed = label.window_return

    assert label.is_labelled
    assert label.window.sessions == panel.sessions[:4]
    assert computed is not None
    assert computed.published == pytest.approx(0.2075, abs=1e-12)
    assert computed.adjusted == pytest.approx(0.2075, abs=1e-12)
    assert computed.unadjusted == pytest.approx(0.15, abs=1e-12)
    assert computed.disagreement <= computed.tolerance
    assert abs(computed.unadjusted - computed.adjusted) > 15.0 * computed.tolerance
    assert label.realized_return == computed.adjusted


def test_the_limit_free_session_on_that_windows_entry_neither_touches_nor_refuses(
    tmp_path: Path,
) -> None:
    """`price_limits.limit_free_sentinel` publishes (99999.999, 0.01) on the entry session.

    All four `LimitTouch` flags fall out `False` from the arithmetic rather than from a branch,
    which is what `PriceLimit`'s docstring claims and what this checks against a stored row.
    """
    _, label = _labelled(tmp_path, code="000001.SZ", prediction_day=BEFORE_THE_WINDOW, horizon="3d")

    assert label.entry_touch is not None
    assert not any(
        (
            label.entry_touch.at_up,
            label.entry_touch.at_down,
            label.entry_touch.one_price_up,
            label.entry_touch.one_price_down,
        )
    )
    assert label.refusals == ()


def test_the_timed_interruption_inside_that_window_traded_and_did_not_refuse(
    tmp_path: Path,
) -> None:
    """An `S` row with a `suspend_timing` is `TradingState.interrupted`, has a bar, and is
    labelled. Reading it as a halt would drop 1,343 names on a 2015-07-08-shaped session.
    """
    panel, label = _labelled(
        tmp_path, code="000001.SZ", prediction_day=BEFORE_THE_WINDOW, horizon="3d"
    )
    halts = load_suspensions(
        PanelStore(tmp_path / "panel"), years=(YEAR,), as_of=AS_OF, max_staleness=None
    )

    interrupted_on = panel.sessions[2]
    assert halts[interrupted_on].state_of("000001.SZ") is TradingState.interrupted
    assert interrupted_on in label.window.sessions
    assert label.is_labelled
    # It is labelled because the halt falls strictly between the two ends, not because a timed
    # halt is always harmless: the generator writes `13:00-15:00`, which is the shape the next
    # test refuses when the window's exit lands on it.
    assert interrupted_on not in (label.window.entry_day, label.window.exit_day)
    assert halts[interrupted_on].timing_of("000001.SZ") == "13:00-15:00"


def test_the_same_interruption_refuses_once_the_window_exits_on_it(tmp_path: Path) -> None:
    """`13:00-15:00` is what the generator writes and what 30 of 2015-07-08's 31 timed rows
    carry: halted from the lunch break to the bell, so the session's `close` is the last print
    before 13:00 and a position could not have been closed at it.

    The two-session window from the same prediction day exits on that session instead of
    holding through it, and the same stored rows then produce a refusal rather than a number.
    """
    panel, label = _labelled(
        tmp_path, code="000001.SZ", prediction_day=BEFORE_THE_WINDOW, horizon="2d"
    )

    assert label.window.exit_day == panel.sessions[2]
    assert [(item.code, item.day) for item in label.refusals] == [
        (REFUSAL_HALTED_INTO_THE_CLOSE, panel.sessions[2])
    ]
    assert label.window_return is None


def test_a_whole_day_halt_read_back_from_the_store_refuses_and_names_the_absent_bar(
    tmp_path: Path,
) -> None:
    """The generated panel's one untimed halt withholds `601318.SH`'s bar on `sessions[4]`, so
    both facts are true of that session and both are reported.
    """
    panel, label = _labelled(
        tmp_path, code="601318.SH", prediction_day=date(2026, 1, 6), horizon="2d"
    )

    halted_on = panel.sessions[4]
    assert label.window.exit_day == halted_on
    assert [(item.code, item.day) for item in label.refusals] == [
        (REFUSAL_HALTED_SESSION, halted_on),
        (REFUSAL_MISSING_BAR, halted_on),
    ]


def test_a_security_the_registry_terminated_cannot_be_labelled_over_the_window(
    tmp_path: Path,
) -> None:
    """`universe.delisted_security` files a termination dated on the window's first session,
    and `delist_date` is exclusive -- so the name is gone from the first session onward.

    A terminated name is absent from three datasets at once here: the registry reports it
    delisted, `daily` has no bar for it, and `stk_limit` publishes no band. All three are
    reported rather than collapsed into one, because a survivorship hole and a short fetch are
    indistinguishable once they have been merged -- `explain_unpriced`'s argument, one layer
    up.
    """
    _, label = _labelled(
        tmp_path,
        code=DELISTED_SECURITY,
        prediction_day=BEFORE_THE_WINDOW,
        horizon="1d",
        # The generator emits no adj_factor rows for the terminated name, which is itself the
        # shape `KNOWN_ADJUSTMENT_LIMITATIONS.suspension_is_invisible` warns about from the
        # other side. A one-row history stands in; `label_outcome` never reads it, because a
        # refused window computes no return at all -- which is the property being checked.
        factors=build_adjustment_history(
            DELISTED_SECURITY,
            [
                FactorObservation(
                    ts_code=DELISTED_SECURITY, observed_on=date(2026, 1, 5), factor=1.0
                )
            ],
        ),
    )

    assert {item.code for item in label.refusals} == {
        REFUSAL_DELISTED,
        REFUSAL_MISSING_BAR,
        REFUSAL_UNPUBLISHED_BAND,
    }
    assert len(label.refusals) == 6


def test_a_corpus_read_for_the_wrong_year_raises_instead_of_reporting_a_quiet_market(
    tmp_path: Path,
) -> None:
    """`load_suspensions` is year-keyed and the same rows that carry the panel's one untimed
    halt say nothing at all when the caller asks for the year before.

    Read as a bare mapping that is indistinguishable from "no halts in this window", which is
    how `601318.SH`'s refused window would have come back labelled. The span the read covered
    travels with it, so it raises.
    """
    panel = generate_panel(shapes=SHAPES)
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(YEAR,), as_of=AS_OF)
    window = build_label_window(
        as_of=datetime(2026, 1, 6, 8, 30, tzinfo=UTC),
        zone=SHANGHAI,
        horizon=parse_horizon("2d"),
        calendar=calendar,
    )
    rows = load_suspensions(store, years=(YEAR,), as_of=AS_OF, max_staleness=None)

    with pytest.raises(LabelError, match="a partition nobody opened"):
        label_outcome(
            window,
            ts_code="601318.SH",
            bars={},
            factors=load_adjustment_histories(
                store, years=(YEAR,), as_of=AS_OF, max_staleness=None
            )["601318.SH"],
            limits={},
            halts=halt_corpus_for_years(rows, years=(YEAR - 1,)),
            universe=load_stock_universe(store, years=(YEAR,), as_of=AS_OF, max_staleness=None),
        )


def test_the_window_steps_over_a_weekday_the_stored_calendar_reports_closed(
    tmp_path: Path,
) -> None:
    """`calendar.mid_window_weekday_closure` shuts Thursday 2026-01-08, so a three-session
    window from Monday the 5th ends on Friday the 9th rather than on the Thursday.
    """
    _, label = _labelled(
        tmp_path,
        code="000001.SZ",
        prediction_day=BEFORE_THE_WINDOW,
        horizon="3d",
        shapes=("calendar.mid_window_weekday_closure", "daily.close_moves_between_sessions"),
    )

    assert label.window.sessions == (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 9),
    )
    assert label.is_labelled
