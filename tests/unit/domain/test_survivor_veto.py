"""The rule-based entry veto (`V2-P7-027`, plan A) -- `market_bearish_5/v1`.

The rule is deliberately **the deterministic `MarketAgent`'s own rule**, not a second one
(R-A2 of `docs/superpowers/plans/2026-11-14-p7-plans-a-b.md`): one limit event is weighted
limit_up 0.65 / consecutive_board 0.85 / broken_board -0.65, and a name is vetoed on session t
when it has at least one event among the **five sessions ending at t** and the mean weight of
those events is below -0.15. The weights are the shared constants in `domain/limit_events.py`,
so this file and the agent cannot drift; the examples below are written from that arithmetic
and not from the implementation.

Two boundaries are pinned here because getting them wrong is silent. The window is the caller's
argument, newest first: an event in the sixth session back is outside it and is **ignored**
passing 5 sessions, while passing more than 5 is a **refusal** (the function cannot know which
five were meant, and guessing is worse than refusing). And the veto blocks entry only (R-A1):
this module names codes and nothing else, so `domain/survivor_policy.decide_book` can add them to
`blocked` without touching a held name's exit, freeze or report.
"""

from __future__ import annotations

from datetime import date
from typing import Final

import grimp
import pytest

from openalpha_cn.domain.limit_events import LimitEvent, LimitEventKind
from openalpha_cn.domain.survivor_veto import (
    VETO_LOOKBACK_SESSIONS,
    VETO_RULE_ID,
    vetoed_codes,
)

A: Final[str] = "000001.SZ"
B: Final[str] = "600000.SH"
C: Final[str] = "600519.SH"

SESSION: Final[date] = date(2024, 6, 24)


def event(ts_code: str, kind: LimitEventKind, sessions_back: int = 0) -> LimitEvent:
    """One event for `ts_code` `sessions_back` sessions before `SESSION` (0 = the newest)."""
    trade_date = date.fromordinal(SESSION.toordinal() - sessions_back)
    return LimitEvent(
        ts_code=ts_code,
        trade_date=trade_date,
        kind=kind,
        board_count=1,
        one_price=False,
        close=10.0,
        high=10.5,
        pct_chg=9.0,
        halted_sessions_skipped=0,
        board_count_is_lower_bound=False,
    )


def test_the_rule_id_and_the_lookback_are_the_ones_the_plan_freezes() -> None:
    """The two names a registration, a reading and a report all quote, spelled once."""
    assert VETO_RULE_ID == "market_bearish_5/v1"
    assert VETO_LOOKBACK_SESSIONS == 5


def test_one_broken_board_is_bearish_enough_to_veto_the_name() -> None:
    """A single -0.65 event has a mean of -0.65, below the -0.15 line."""
    assert vetoed_codes([[event(A, "broken_board")]]) == frozenset({A})


def test_a_broken_board_and_a_limit_up_average_to_zero_and_do_not_veto() -> None:
    """The plan's own example: (-0.65 + 0.65) / 2 = 0.0, which is not bearish."""
    assert vetoed_codes([[event(A, "limit_up"), event(A, "broken_board")]]) == frozenset()


def test_a_broken_board_and_a_consecutive_board_average_above_the_line() -> None:
    """( -0.65 + 0.85 ) / 2 = 0.10 > -0.15: the streak outweighs the break."""
    assert vetoed_codes([[event(A, "broken_board"), event(A, "consecutive_board")]]) == frozenset()


def test_two_broken_boards_veto_even_though_neither_session_alone_would_be_asked() -> None:
    """The mean is over every event in the window, not per session: -0.65 again."""
    assert vetoed_codes([[event(A, "broken_board")], [event(A, "broken_board")]]) == frozenset({A})


def test_no_events_at_all_veto_nobody() -> None:
    """An empty window is not bearish: the rule needs at least one event to speak about."""
    assert vetoed_codes([]) == frozenset()
    assert vetoed_codes([[], [], []]) == frozenset()


def test_an_event_in_the_sixth_session_back_is_outside_the_window_and_ignored() -> None:
    """The caller passes the five sessions ending at t; the sixth is not part of the argument.

    Built as six sessions newest-first so the boundary is visible: the only event sits in the
    last (oldest) one, so passing the five the rule reads finds nothing.
    """
    sessions = [[], [], [], [], [], [event(A, "broken_board", sessions_back=5)]]

    assert vetoed_codes(sessions[:VETO_LOOKBACK_SESSIONS]) == frozenset()


def test_an_event_in_the_fifth_session_back_is_still_inside_the_window() -> None:
    """Four empty sessions then the oldest one the argument may carry: still vetoed."""
    sessions: list[list[LimitEvent]] = [[], [], [], [], [event(A, "broken_board", sessions_back=4)]]

    assert vetoed_codes(sessions) == frozenset({A})


def test_more_than_the_lookback_sessions_passed_is_refused_rather_than_cut() -> None:
    """The at-most-five window is a contract: a sixth session cannot be silently dropped."""
    sessions: list[list[LimitEvent]] = [[] for _ in range(VETO_LOOKBACK_SESSIONS + 1)]

    with pytest.raises(ValueError, match="at most 5 sessions"):
        vetoed_codes(sessions)


def test_only_the_names_whose_mean_is_bearish_are_vetoed() -> None:
    """One cross-section, three readings: bearish, flat and bullish stay apart."""
    sessions = [
        [event(A, "broken_board"), event(B, "limit_up"), event(C, "consecutive_board")],
        [event(C, "consecutive_board")],
    ]

    assert vetoed_codes(sessions) == frozenset({A})


def test_the_result_is_a_frozenset_so_a_caller_cannot_mutate_the_decision() -> None:
    vetoed = vetoed_codes([[event(A, "broken_board")]])

    assert isinstance(vetoed, frozenset)


def test_survivor_veto_imports_nothing_numeric_and_no_sibling_subpackage() -> None:
    """Hard rule 2 read off the live graph, independent of the static contract list.

    `domain/` may import its own siblings (`limit_events` here) and nothing above it: the panel
    adapter and `decide_book`'s caller do the wiring, so this module stays pure and testable
    without a panel -- and it must not import `agents/`, which is the layer that reads the
    constants, rather than the reverse.
    """
    graph = grimp.build_graph("openalpha_cn", cache_dir=None, include_external_packages=True)
    upstream = graph.find_upstream_modules("openalpha_cn.domain.survivor_veto", as_package=False)
    forbidden = {"numpy", "pandas", "scipy", "sklearn", "duckdb", "sqlite3"}
    offenders = {
        module
        for module in upstream
        if module.split(".")[0] in forbidden
        or (module.startswith("openalpha_cn.") and not module.startswith("openalpha_cn.domain"))
    }
    assert not offenders, f"domain.survivor_veto reaches {sorted(offenders)}"
