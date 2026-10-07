"""What a risk flag is *worth*, read off the flag itself rather than inferred from a gate.

`V2-P4-006` built this module, `V2-P4-030` and `V2-P4-036` rebuilt it. The screen it serves used
to order by `confidence` and to read `risk_flags` only as a **count**
(`ScreeningCriteria.max_risk_flags`), which is the one reading that cannot separate the two
answers: a signal carrying `future_data` -- the flag `decisions/risk.py::RiskGate` refuses
outright -- and a signal carrying a cosmetic note both have a count of one, and the flagged one
sorted to the top on confidence alone.

## What `V2-P4-006` could not do from here, and what changed underneath it

`risk_flags` was an open string set, and three modules read disjoint closed subsets of it. This
module could not repair that -- writing the union down here would have been a **fourth** list,
correct on the day it was written and drifting the first time either gate changed -- so it did
the only honest thing available from inside `product/`: it held **no flag strings at all** and
obtained severity by *asking* the two shipped gates, driving a synthetic one-flag `SignalFrame`
through each and reading the verdict back out.

That indirection is gone, because the thing that forced it is gone. `V2-P4-030` closed the
vocabulary at the point the flags are written: `domain/risk_flag.py::RiskFlag` declares every
flag **with what it is worth**, `SignalFrame.risk_flags` names that enum, and both gates derive
their sets from it. Severity is no longer something to be inferred from behaviour -- it is
declared, and this module reads the declaration.

Three things fell out of that, and all three were load-bearing before:

- **`SHIPPED_RISK_GATES` is deleted** (`V2-P4-036`). It named the two gates as callables and
  called itself "the single source for what counts as severe", and **nothing read it**:
  `_verdicts` called the two module-level functions directly and `_rung` hardcoded exactly two
  verdicts. Measured on the previous commit, adding an always-blocking third gate and clearing
  the cache left `flag_severity('bogus-flag')` at `unrecognised`, and *emptying the registry
  entirely* left `flag_severity('future_data')` at `blocked`. A registry that decorative is
  worse than none: it invites a contributor to add a third gate the documented way and see
  nothing happen. It is not repaired by being wired up, because a closed vocabulary makes it
  redundant -- a gate does not get to have an opinion about what a flag is worth; it gets to
  decide what to *do* about a flag whose worth is declared.
- **The one-flag probe is deleted.** It existed because `DeliberationCommittee.review` was not
  total on `SignalFrame` -- handed an abstention it recomputed a direction it could never make
  `abstain` and died on its own output -- so `assess` could not call the committee on the signal
  it was given. `V2-P4-029` fixed that, and `assess` now asks both gates about the actual
  signal.
- **The memo is deleted.** `flag_severity` was `lru_cache(maxsize=512)`, bounded rather than
  unbounded because the strings came from request bodies and an unbounded memo over those is a
  leak whose size a caller chooses. There is nothing left to memoise: the answer is an enum
  lookup, and the key space is now ten members rather than every string a caller can type.

## The ladder

Five rungs, best first, declared in `SEVERITY_ORDER` and ranked by position:

- **`clear`** -- no flags at all.
- **`unrecognised`** -- the string is not a declared flag. See below; this is no longer
  reachable from a `SignalFrame`.
- **`reduced`** -- a caution that should cost the signal its place, not its life.
- **`severe`** -- the evidence is readable but the name should not be acted on.
- **`blocked`** -- the evidence itself is unusable.

`blocked` above `severe` because `RiskGate` is the runtime gate that actually stops a decision
while the committee is optional by design (PRD S41), so the two are not the same claim about the
same thing. `domain/risk_flag.py` carries the table of which gate says what for each rung.

**`unrecognised` survives as a rung but can no longer be reached through `assess`**, and that
is the whole point of `V2-P4-030` rather than an oversight. It used to mean "no shipped gate
names this string", and the flag that landed there most often was a **misspelling**: a payload
writing `future-data` instead of `future_data` was demoted from `blocked` to `unrecognised` and
therefore **promoted** up this screen. `SignalFrame` now refuses that string outright, so a
signal cannot carry a flag no gate names. `flag_severity` still answers `unrecognised`, because
it is a public function that takes a `str` and has to say *something* about one that is not a
flag -- and "this is not in the vocabulary" is the honest answer. The rung is kept rather than
removed because `ScreeningCriteria.worst_severity_admitted` is a field of a shipped request body
(`POST /api/v1/screen`) and dropping a value a caller may already send would break the endpoint
to record a fact.

## What this module still does not decide

Nothing here claims either gate is *right*, or that `blocked` really is worse than `severe` for
every caller -- only that a list needs one order and this is the one, written down in
`SEVERITY_ORDER` rather than implied.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.decisions.risk import RiskDecision, RiskGate
from openalpha_cn.domain.risk_flag import RISK_FLAGS_BY_VALUE, RiskFlag
from openalpha_cn.domain.signal import SignalFrame

__all__ = [
    "SEVERITY_ORDER",
    "SEVERITY_RANK",
    "GovernanceSeverity",
    "GovernanceVerdict",
    "assess",
    "flag_severity",
]

GovernanceSeverity = Literal["clear", "unrecognised", "reduced", "severe", "blocked"]
"""How much a signal's risk flags are worth to a screen, as a closed set. See this module's
docstring for what each rung means and why `blocked` sits above `severe`."""

SEVERITY_ORDER: Final[tuple[GovernanceSeverity, ...]] = (
    "clear",
    "unrecognised",
    "reduced",
    "severe",
    "blocked",
)
"""The ladder, best first. Declared rather than alphabetical, and ranked by position rather
than by a second hand-written mapping, so the order and the vocabulary cannot disagree."""

SEVERITY_RANK: Final[Mapping[GovernanceSeverity, int]] = {
    severity: index for index, severity in enumerate(SEVERITY_ORDER)
}
"""`SEVERITY_ORDER` as a sort key: ascending rank is improving-to-worsening governance."""


def flag_severity(flag: str) -> GovernanceSeverity:
    """What one risk flag is worth, read off `domain/risk_flag.py::RiskFlag`.

    Takes a bare `str` rather than a `RiskFlag` deliberately. Every caller inside this build
    hands it a member -- `SignalFrame.risk_flags` cannot hold anything else -- but this is a
    public function re-exported from `product/research.py`, and answering "is this string worth
    anything?" for an arbitrary string is a question worth being able to ask. `unrecognised` is
    that answer, and it is the only way to reach that rung now that the contract refuses the
    strings which used to land there.

    Uncached, unlike every previous version of this function. The answer was previously derived
    by building a `SignalFrame` and running a committee over it, which was worth memoising; it
    is now a dictionary lookup on a ten-member enum.
    """
    declared = RISK_FLAGS_BY_VALUE.get(flag)
    return "unrecognised" if declared is None else declared.severity


class GovernanceVerdict(BaseModel):
    """What the shipped gates say about one signal, with the flags that account for it.

    `driving_flags` is the point of this record and is why the screen reads *which* flags
    rather than how many: a verdict with no flags named is a number, and a number is what
    `max_risk_flags` already was.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: GovernanceSeverity
    driving_flags: tuple[RiskFlag, ...]
    """The signal's own flags that sit at `severity`, in the order the signal carries them.

    Empty exactly when `severity` is `clear`, because a rung above `clear` is reached by a flag
    and by nothing else.
    """
    gate_decision: RiskDecision
    """`RiskGate.evaluate`'s verdict on this signal, carried rather than summarised."""
    committee_decision: RiskDecision
    """`DeliberationCommittee.review`'s verdict on this signal, with no debate."""


def assess(signal: SignalFrame) -> GovernanceVerdict:
    """Rate one signal's flags, and name the ones that decided it.

    **About the signal itself**, which is new. Until `V2-P4-029` this function could not touch
    the signal it was given: `DeliberationCommittee.review` was not total on `SignalFrame`, and
    handed an abstention -- `direction="abstain"`, and therefore no `evidence_ids` by
    `SignalFrame.validate_conclusion` -- it raised *"directional signal requires evidence"* while
    building its own output. Every abstention in this build is such a signal and
    `ScreeningCriteria.directions` lists `abstain` as something a caller may screen for, so this
    function routed around the committee with a synthetic carrier of the flags. The committee
    accepts an abstention now, and the indirection is gone with it.

    Severity is the worst rung any of the signal's flags reaches, taken from the flags
    themselves. The two gate verdicts are carried beside it rather than used to derive it:
    they are what the shipped gates will actually *do* with this signal, which is a different
    and separately useful fact from what its flags are worth.
    `tests/unit/domain/test_risk_flag.py::
    test_both_gates_answer_about_every_declared_flag_and_agree_with_its_severity` is what holds
    the two readings together, and it is not vacuous despite both gates deriving their sets from
    the same enum: each gate maps a severity band to a decision independently, and the two
    deliberately differ on `severe`.
    """
    severity = _worst_severity(signal.risk_flags)
    driving = (
        ()
        if severity == "clear"
        else tuple(flag for flag in signal.risk_flags if flag.severity == severity)
    )
    return GovernanceVerdict(
        severity=severity,
        driving_flags=driving,
        gate_decision=RiskGate().evaluate(signal),
        committee_decision=DeliberationCommittee().review(signal=signal, results=()).risk_decision,
    )


def _worst_severity(flags: tuple[RiskFlag, ...]) -> GovernanceSeverity:
    """The worst rung any of `flags` reaches, or `clear` for none.

    Split out of `assess` so the rule is written in one place. Private, because this module
    declares an `__all__` and a public name outside it is an ambiguous surface. `max` over
    `SEVERITY_RANK` rather than a chain of `if`s, because the ordering already exists and a
    second encoding of it is exactly what `SEVERITY_ORDER`'s docstring refuses.
    """
    if not flags:
        return "clear"
    return max(flags, key=lambda flag: SEVERITY_RANK[flag.severity]).severity
