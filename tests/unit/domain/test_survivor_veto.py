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

from dataclasses import FrozenInstanceError
from datetime import date
from typing import Final

import grimp
import pytest

from openalpha_cn.domain.limit_events import LimitEvent, LimitEventKind
from openalpha_cn.domain.survivor_veto import (
    VETO_LOOKBACK_SESSIONS,
    VETO_RULE_ID,
    VetoDetail,
    veto_details,
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


# --- `veto_details`: the same answer, per name, with the evidence behind it -------------------
#
# Plan A's daily annotation (Task 2) prints *why* a name is vetoed, not only that it is: the
# event count and the mean weight are the two numbers the rule's arithmetic is made of. The
# refactor must not become a second rule, so the equality below is the contract -- the details
# select exactly the codes `vetoed_codes` selects, on every example in this file.


def test_veto_details_names_only_the_securities_that_carry_an_event() -> None:
    """A name with no event is absent, not present with a zero mean: it has no reading."""
    details = veto_details([[event(A, "limit_up"), event(B, "broken_board")], []])

    assert set(details) == {A, B}
    assert details[A].ts_code == A


def test_veto_details_agrees_with_vetoed_codes_on_every_example_of_the_rule() -> None:
    """The plan's contract: the vetoed names are exactly the details' vetoed names."""
    fixtures = (
        [[event(A, "broken_board")]],
        [[event(A, "limit_up"), event(A, "broken_board")]],
        [[event(A, "broken_board"), event(A, "consecutive_board")]],
        [[event(A, "broken_board")], [event(A, "broken_board")]],
        [[event(A, "broken_board"), event(B, "limit_up"), event(C, "consecutive_board")]],
        [],
    )
    for sessions in fixtures:
        details = veto_details(sessions)

        assert {detail.ts_code for detail in details.values() if detail.vetoed} == vetoed_codes(
            sessions
        )


def test_veto_details_counts_events_and_averages_their_weights() -> None:
    """The two numbers are the rule's own: the event count and the mean of the weights."""
    details = veto_details(
        [[event(A, "limit_up"), event(B, "broken_board")], [event(A, "broken_board")]]
    )

    assert details[A].event_count == 2
    assert details[A].mean_weight == 0.0
    assert details[A].vetoed is False
    assert details[B].event_count == 1
    assert details[B].mean_weight == -0.65
    assert details[B].vetoed is True


def test_veto_details_refuses_more_than_the_lookback_sessions_like_vetoed_codes() -> None:
    """A sixth session is refused by the same name and in the same words, not cut here."""
    sessions: list[list[LimitEvent]] = [[] for _ in range(VETO_LOOKBACK_SESSIONS + 1)]

    with pytest.raises(ValueError, match="at most 5 sessions"):
        vetoed_codes(sessions)
    with pytest.raises(ValueError, match="at most 5 sessions"):
        veto_details(sessions)


def test_the_shared_window_refusal_names_the_function_the_caller_used() -> None:
    """The shared guard keeps each caller's own wording: `vetoed_codes` said "vetoed_codes ..."."""
    sessions: list[list[LimitEvent]] = [[] for _ in range(VETO_LOOKBACK_SESSIONS + 1)]

    with pytest.raises(ValueError) as codes:
        vetoed_codes(sessions)
    with pytest.raises(ValueError) as details:
        veto_details(sessions)

    assert str(codes.value).startswith("vetoed_codes reads at most 5 sessions")
    assert str(details.value).startswith("veto_details reads at most 5 sessions")


def test_veto_detail_is_frozen_so_a_printed_row_cannot_be_edited_after_the_reading() -> None:
    detail = veto_details([[event(A, "broken_board")]])[A]

    assert isinstance(detail, VetoDetail)
    with pytest.raises(FrozenInstanceError):
        detail.vetoed = False  # type: ignore[misc]


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
