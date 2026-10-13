"""The sticky holding policy (`V2-P7-005`): who is sold, who is bought, and nothing else.

A holding is sold only when it **stops being eligible** (forced, never deferred) or **drops out of
the survivors** (discretionary, at most `max_replacements_per_session` a day, the worst composite
first; the rest wait for tomorrow). Two kinds of holding are left alone: one **already being
sold** (`pending_sells` -- a sell order that has not filled yet; selling it again would double the
order, and its slot is already free), and one whose **only** problem is a suspension (`frozen` --
it cannot trade until it resumes, and a halt is not evidence about the name; it is judged like any
other holding the first session it trades again). A vacancy is filled from the **entrants** --
survivors above the entry floor that are not already held, not already on order and not in
cool-down -- in the order the selection rule gives, skipping any name a lot of which the current
equity cannot buy (an affordability limit on *buying*; it never makes a holding ineligible).
The first build fills every vacancy at once; after that at most `max_replacements_per_session`
buys a day.

This is the half of the design the evidence rests on: staying put is what keeps the loser tail out
and the cost low (`docs/superpowers/specs/2026-10-13-p7-step0-survivor-book-design.md` section 3).
The function is pure -- state and screen in, decision out -- so the whole policy is testable with
hand-written fixtures and no panel.
"""

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.domain.survivor_book import (
    ScreenResult,
    SurvivorBookConfig,
    floor_share,
    lot_quantity,
)
from openalpha_cn.domain.survivor_selection import selection_rule

ExitReason = Literal["EXIT_ELIGIBILITY_LOST", "EXIT_SURVIVOR_LOST"]
EntryReason = Literal["INITIAL_BUILD", "ENTRY_REPLACEMENT"]


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class Cooldown(BaseModel):
    """A security that may not be bought again until `cooldown_sessions` have passed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str = Field(min_length=1)
    since_session: int = Field(ge=0)


class BookState(BaseModel):
    """Everything the policy needs to know about the book as of the signal session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session: int = Field(ge=0)
    """Ordinal of the signal session in the trading calendar; the adapter maps dates to it."""
    initialized: bool
    """False until the first build has been decided; the first build is not rate-limited."""
    equity: Decimal = Field(gt=0)
    holdings: tuple[Holding, ...] = ()
    pending_buys: tuple[str, ...] = ()
    """Securities with a live buy order. They fill a slot and cannot be chosen again."""
    pending_sells: tuple[str, ...] = ()
    """Held securities with a live sell order. Their slot counts as free and they are not sold
    again; the book may hold more than `holding_count` names while such a sell is unfilled."""
    cooldowns: tuple[Cooldown, ...] = ()

    @model_validator(mode="after")
    def _no_repeats_and_no_overlap(self) -> Self:
        held = [holding.ts_code for holding in self.holdings]
        if len(set(held)) != len(held):
            raise ValueError("a security is held more than once")
        if len(set(self.pending_buys)) != len(self.pending_buys):
            raise ValueError("a security has more than one pending buy")
        if set(held) & set(self.pending_buys):
            raise ValueError("a security is both held and on a pending buy")
        if len(set(self.pending_sells)) != len(self.pending_sells):
            raise ValueError("a security has more than one pending sell")
        if not set(self.pending_sells) <= set(held):
            raise ValueError("a pending sell names a security that is not held")
        return self


class ExitDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    reason: ExitReason


class EntryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ts_code: str
    reason: EntryReason


class BookDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session: int
    exits: tuple[ExitDecision, ...]
    """Forced exits first (by code), then discretionary ones (worst composite first)."""
    entries: tuple[EntryDecision, ...]
    """In the order the selection rule drew them."""
    deferred_exits: tuple[str, ...]
    """Survivor-lost holdings kept today because the replacement cap was reached."""
    near_exit: tuple[str, ...]
    """Holdings still in the survivors but in their worst `near_exit_fraction`, ordered by code."""
    frozen: tuple[str, ...] = ()
    """Holdings left alone today because their only problem is a suspension."""
    watch_list: tuple[str, ...]
    """The next names the rule would draw today, after today's entries -- not a promise about
    tomorrow, because the draw is salted with the session."""
    unaffordable: tuple[str, ...]
    """Entrants skipped because current equity cannot buy a lot of them."""
    target_holdings: tuple[str, ...]
    """Holdings after today's exits and entries, sorted; pending buys are not included."""


def decide_book(
    *, config: SurvivorBookConfig, screen: ScreenResult, state: BookState
) -> BookDecision:
    held = sorted(holding.ts_code for holding in state.holdings)
    active = [code for code in held if code not in set(state.pending_sells)]
    if len(active) + len(state.pending_buys) > config.holding_count:
        raise ValueError(
            f"{len(active)} holdings not being sold and {len(state.pending_buys)} pending buys "
            f"exceed holding_count {config.holding_count}"
        )
    verdicts = {verdict.ts_code: verdict for verdict in screen.verdicts}
    survivors = {name.ts_code: name for name in screen.survivors}
    cap = config.max_replacements_per_session

    def composite_of(code: str) -> float:
        value = verdicts[code].composite
        if value is None:  # an eligible verdict always carries one; the screen enforces it
            raise ValueError(f"{code}: an eligible verdict without a composite")
        return value

    frozen = [
        code for code in active if code in verdicts and verdicts[code].reasons == ("suspended",)
    ]
    forced = [
        code
        for code in active
        if (code not in verdicts or not verdicts[code].eligible) and code not in frozen
    ]
    lost = sorted(
        (
            code
            for code in active
            if code in verdicts and verdicts[code].eligible and code not in survivors
        ),
        key=lambda code: (composite_of(code), code),
    )
    exits = tuple(
        ExitDecision(ts_code=code, reason="EXIT_ELIGIBILITY_LOST") for code in forced
    ) + tuple(ExitDecision(ts_code=code, reason="EXIT_SURVIVOR_LOST") for code in lost[:cap])
    deferred = tuple(lost[cap:])
    leaving = {decision.ts_code for decision in exits}

    occupied = len(active) - len(exits) + len(state.pending_buys)
    vacancies = max(config.holding_count - occupied, 0)
    allowance = vacancies if not state.initialized else min(vacancies, cap)

    cooling = {
        cooldown.ts_code
        for cooldown in state.cooldowns
        if state.session - cooldown.since_session < config.cooldown_sessions
    }
    blocked = set(held) | set(state.pending_buys) | cooling
    ranked = screen.survivors
    entrant_count = len(ranked) - floor_share(len(ranked), config.entry_floor_fraction)
    entrants = [name for name in ranked[:entrant_count] if name.ts_code not in blocked]

    unit_budget = config.sizing_budget(state.equity)
    affordable: list[str] = []
    unaffordable: list[str] = []
    drawn = selection_rule(config.selection_rule).order(
        [name.ts_code for name in entrants], seed=config.seed, session=state.session
    )
    for code in drawn:
        if lot_quantity(unit_budget, survivors[code].close, config.lot_size) == 0:
            unaffordable.append(code)
        else:
            affordable.append(code)

    reason: EntryReason = "ENTRY_REPLACEMENT" if state.initialized else "INITIAL_BUILD"
    entries = tuple(EntryDecision(ts_code=code, reason=reason) for code in affordable[:allowance])
    watch_list = tuple(affordable[allowance : allowance + config.holding_count])

    near_cutoff = len(ranked) - floor_share(len(ranked), config.near_exit_fraction)
    near_exit = tuple(
        code for code in active if code in survivors and survivors[code].rank > near_cutoff
    )
    target = sorted((set(active) - leaving) | {entry.ts_code for entry in entries})
    return BookDecision(
        session=state.session,
        exits=exits,
        entries=entries,
        deferred_exits=deferred,
        near_exit=near_exit,
        frozen=tuple(frozen),
        watch_list=watch_list,
        unaffordable=tuple(unaffordable),
        target_holdings=tuple(target),
    )
