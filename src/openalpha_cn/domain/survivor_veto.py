"""The rule-based entry veto (`V2-P7-027`, plan A): `market_bearish_5/v1`.

## What it is, in one sentence

A survivor is **vetoed** on session `t` when it has at least one limit event among the five
sessions ending at `t` and the mean weight of those events is below `-0.15` -- that is, when the
existing deterministic market reading of its own events is *bearish*.

## Why this is the agent's rule and not a new one

R-A2 of `docs/superpowers/plans/2026-11-14-p7-plans-a-b.md` fixes the rule as the one
`agents/baseline.py::MarketAgent` already applies: limit_up 0.65, consecutive_board 0.85,
broken_board -0.65, bearish below -0.15. The weights and the line are read from
`domain/limit_events.py`'s shared constants (`MARKET_EVENT_WEIGHTS`,
`MARKET_BEARISH_THRESHOLD`) rather than restated here, so "the veto is the agent's rule" is a
property of the code rather than a comment that a later edit can falsify. The agent reads the
same constants; this module is the pure rule that turns one security's event history into a
yes/no, the way `domain/labels.py` turns prices into a label.

## The window is the caller's argument, newest first

`events_by_session` is the five sessions ending at `t`, **newest first**. The function reads
every event it is given and refuses a sixth session: a caller that passes six has assembled a
window the rule does not define, and silently dropping one end would make the answer depend on
which end was dropped. An event older than the window is therefore not "ignored by arithmetic"
-- it is never passed, and `tests/unit/domain/test_survivor_veto.py` pins both sides of that
boundary. The mean is over **events**, not sessions: two broken boards in two sessions average
-0.65 just as one does, and one break beside one limit-up averages 0.0, which is not bearish.

## What a caller does with the answer, and what it must not do (R-A1)

The veto **blocks entry only**. R-A1 fixes that it is not an `EligibilityReason`: forcing an exit
would sell a held name at an uncapped cost and move the liquidity and loser-decile populations
the P7 measurements depend on. So `domain/survivor_policy.decide_book` adds these codes to the
set an entrant must not be drawn from, and a held name the veto names is not sold, frozen or
reported differently. This module names codes and nothing else; it has no book, no orders and no
report.

## `veto_details` is the same answer with the arithmetic shown

The daily annotation (`V2-P7-027`, Task 2) prints *why* a name is vetoed beside the name: the
event count and the mean weight the threshold was compared against. `veto_details` is that
reading, and it shares `vetoed_codes`' window refusal and its arithmetic rather than restating
them, so the two answers cannot drift: every name `veto_details` marks `vetoed` is a name
`vetoed_codes` returns. A name with no event in the window is absent from the mapping -- there
is no reading to print -- which is also why the equality below is over `detail.vetoed` rather
than over every key.

## Layering

Pure `Sequence`/`Mapping`/`dict`/`math.fsum`/`dataclass` plus `domain/limit_events.py`'s
vocabulary. No panel read, no store, no clock, no numeric stack, and no import of `agents/` --
the agent imports the constants from `domain/`, never the reverse (`AGENTS.md` hard rule 2).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from openalpha_cn.domain.limit_events import (
    MARKET_BEARISH_THRESHOLD,
    MARKET_EVENT_WEIGHTS,
    LimitEvent,
)

VETO_RULE_ID: Final[str] = "market_bearish_5/v1"
"""The rule's registered id: plan A's `entry_veto` names it, and a reading/report quotes it."""

VETO_LOOKBACK_SESSIONS: Final[int] = 5
"""How many sessions ending at `t` the rule reads -- five, the agent's own horizon unit."""


class SurvivorVetoError(ValueError):
    """Raised when the veto is asked to read more sessions than its rule defines."""


@dataclass(frozen=True, slots=True)
class VetoDetail:
    """One security's reading: how many events it had, their mean weight, and the answer.

    `event_count` and `mean_weight` are the two numbers the rule is made of, so an annotation
    that prints them prints the arithmetic rather than a second explanation of it; `vetoed` is
    the same comparison `vetoed_codes` makes. Frozen because the answer is a record once taken.
    """

    ts_code: str
    event_count: int
    mean_weight: float
    vetoed: bool


def _event_weights(
    caller: str, events_by_session: Sequence[Sequence[LimitEvent]]
) -> dict[str, list[float]]:
    """Every name's event weights over the window, after the window's own refusal.

    Shared by `vetoed_codes` and `veto_details` so the two cannot disagree about what a legal
    window is. `caller` is the public name the refusal names itself by -- the guard is one rule,
    but each function's sentence is the one that function has always written.
    """
    if len(events_by_session) > VETO_LOOKBACK_SESSIONS:
        raise SurvivorVetoError(
            f"{caller} reads at most {VETO_LOOKBACK_SESSIONS} sessions, newest first; got "
            f"{len(events_by_session)} -- the caller's window is longer than the rule's, and "
            "cutting one end here would make the answer depend on which end was cut"
        )
    weights: dict[str, list[float]] = {}
    for session_events in events_by_session:
        for event in session_events:
            weights.setdefault(event.ts_code, []).append(MARKET_EVENT_WEIGHTS[event.kind])
    return weights


def vetoed_codes(events_by_session: Sequence[Sequence[LimitEvent]]) -> frozenset[str]:
    """Every security the rule vetoes, given the five sessions ending at `t` newest first.

    Returns the empty set for an empty window (the rule needs at least one event to speak
    about), and raises `SurvivorVetoError` -- a `ValueError` -- when more than
    `VETO_LOOKBACK_SESSIONS` sessions are passed, rather than cutting the window for the caller.
    The answer is a `frozenset` so a decision cannot be mutated after it is taken.
    """
    return frozenset(
        ts_code
        for ts_code, values in _event_weights("vetoed_codes", events_by_session).items()
        if math.fsum(values) / len(values) < MARKET_BEARISH_THRESHOLD
    )


def veto_details(events_by_session: Sequence[Sequence[LimitEvent]]) -> Mapping[str, VetoDetail]:
    """Every security with at least one event in the window, newest first as the caller passed it.

    The mapping is the rule's own arithmetic per name: `event_count` events, their `mean_weight`
    (a `math.fsum` over the event weights, as `vetoed_codes` computes it), and `vetoed` the same
    `mean_weight < MARKET_BEARISH_THRESHOLD` comparison. A name with no event is absent rather
    than present with a zero mean, and a window longer than the rule's raises
    `SurvivorVetoError` through the shared refusal -- so
    `{detail.ts_code for detail in veto_details(x).values() if detail.vetoed} == vetoed_codes(x)`
    holds for every legal window.
    """
    details: dict[str, VetoDetail] = {}
    for ts_code, values in _event_weights("veto_details", events_by_session).items():
        mean_weight = math.fsum(values) / len(values)
        details[ts_code] = VetoDetail(
            ts_code=ts_code,
            event_count=len(values),
            mean_weight=mean_weight,
            vetoed=mean_weight < MARKET_BEARISH_THRESHOLD,
        )
    return details
