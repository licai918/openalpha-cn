"""What a risk flag is *worth*, asked of the gates that already ship rather than restated.

`V2-P4-006`. The screen this module serves used to order by `confidence` and to read
`risk_flags` only as a **count** (`ScreeningCriteria.max_risk_flags`), which is the one reading
that cannot separate the two answers: a signal carrying `future_data` -- the flag
`decisions/risk.py::RiskGate` refuses outright -- and a signal carrying any cosmetic note some
evidence payload happened to set both have a count of one, and the flagged one sorted to the
top on confidence alone. `risk_flags` is an open string set, so "any cosmetic note" is not
hypothetical: `agents/baseline.py::_quality_flags` copies whatever strings a payload's
`quality_flags` holds, unchecked.

## The measurement this module exists because of

`V2-P4-005` recorded it as `KNOWN_RANKING_LIMITATIONS
.the_signals_own_risk_flags_are_an_open_set_and_two_gates_read_disjoint_subsets`, and it is
still true at this commit:

- `decisions/risk.py::RiskGate` blocks on `{future_data, look_ahead_violation}` and reduces on
  `{redistribution_unknown, source_uri_missing, revised_after_initial_availability}`.
- `agents/committee.py::DeliberationCommittee` treats `{regulatory, data-quality, suspension}`
  as severe.
- **Their intersection is empty**, and `committee-disagreement` -- a flag the committee raises
  about its own deliberation -- is in neither, so a signal explicitly marked as disputed reaches
  the runtime gate and gets `pass`.

Three closed subsets of one open set, and none of them agrees with another. The obvious repair
is to write the union down here and sort on it. That would be a **fourth** list, and a fourth
list is the defect rather than the fix: it would be correct on the day it was written and would
drift the first time either shipped gate changed, with nothing red.

## So this module holds no flag strings at all

`SHIPPED_RISK_GATES` is the single named source, and what it names is the two gates
**themselves**, as callables. Severity is obtained by *asking* them:

    flag_severity("future_data")   # -> "blocked",       because RiskGate says block
    flag_severity("regulatory")    # -> "severe",        because the committee says block
    flag_severity("source_uri_missing")      # -> "reduced",  because RiskGate says reduce
    flag_severity("committee-disagreement")  # -> "unrecognised"

Grep this file for `future_data` and the only occurrences are in this docstring. There is no
list to drift from, because there is no list: rename a member of `RiskGate._blocking_flags` and
the severity of that string moves here on the same commit, with no edit and no rediscovery.

`tests/unit/product/test_governed_screening.py::
test_no_shipped_risk_flag_is_written_in_executable_code_under_product` holds that property by
AST over this package's own source -- prose may name a flag, code may not -- because a single
well-meant literal is all it takes to lose it. It is `test_known_limitation_registries.py`'s
docstring/executable split, pointed at source rather than at tests.

## Reading the committee by behaviour, and why that is not a workaround

`RiskGate` exposes `evaluate(signal)`, so asking it is ordinary. The committee's severe set is a
**set literal inside `DeliberationCommittee.review`'s body** -- not a class attribute, not a
module constant -- so behaviour is the only public way to read it, and `review` is the public
way. Driven with `results=()` its `risk_decision` is a pure function of `signal.risk_flags`:
with no agent results there is no debate, so it adds no `committee-disagreement` of its own,
and the three perspectives reduce to `block` on a severe flag, `reduce` on any other flag and
`pass` on none.

"Pure function of `risk_flags`" is the load-bearing claim -- it is what makes a synthetic
one-flag probe a measurement of the flag rather than of the probe -- so it is measured rather
than asserted: `tests/unit/product/test_governed_screening.py::
test_both_shipped_gates_answer_about_the_flags_and_about_nothing_else_on_the_signal` varies
every other `SignalFrame` field on the probe and requires both verdicts to stand still.

The change this module would prefer is one line in `agents/committee.py`: promote that set
literal to a named module constant. That file is outside this issue's ownership, so the probe
is what is shipped; the probe is strictly more honest anyway, since it measures what the
committee *does* rather than what a constant beside it claims.

## The ladder, and why `unrecognised` is a rung rather than a synonym for `clear`

Five rungs, best first, declared in `SEVERITY_ORDER` and ranked by position:

- **`clear`** -- no flags at all. Both gates pass.
- **`unrecognised`** -- the signal carries a flag and **neither gate names it**. The committee's
  catch-all ("any flag at all is at least a reduction") is the only thing that sees it. This is
  exactly where `committee-disagreement` lands, and saying so is the finding: before this
  module a disputed signal sorted as though it were clean, and now it does not.
- **`reduced`** -- `RiskGate` reduces on it.
- **`severe`** -- the committee blocks on it.
- **`blocked`** -- `RiskGate` blocks on it.

`blocked` above `severe` because `RiskGate` is the runtime gate that actually stops a decision
while the committee is optional by design (PRD S41, `agents/committee.py`'s "remain optional"),
so the two `block` verdicts are not the same claim about the same thing.

A signal's severity is the **worst** rung any of its flags reaches, and both gates are set
intersections over the whole flag tuple, so the whole-signal verdict already *is* that maximum
-- `assess` calls each gate once and derives the rung from the pair, then names the flags that
account for it. `tests/unit/product/test_governed_screening.py::
test_a_signals_severity_is_the_worst_of_its_flags_taken_one_at_a_time` drives both readings and
requires them equal, which is the assertion that would go red if either gate ever stopped being
a set intersection.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import lru_cache
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.decisions.risk import RiskDecision, RiskGate
from openalpha_cn.domain.signal import SignalFrame

__all__ = [
    "SEVERITY_ORDER",
    "SEVERITY_RANK",
    "SHIPPED_RISK_GATES",
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


def _runtime_risk_gate(signal: SignalFrame) -> RiskDecision:
    """`decisions/risk.py::RiskGate`, asked about one signal."""
    return RiskGate().evaluate(signal)


def _deliberation_committee(signal: SignalFrame) -> RiskDecision:
    """`agents/committee.py::DeliberationCommittee`, asked about one signal and no debate.

    `results=()` is what makes this a reading of `signal.risk_flags` rather than of a debate
    that did not happen: with neither bulls nor bears the committee adds no
    `committee-disagreement` of its own, so the flags it votes on are exactly the ones handed
    to it.
    """
    return DeliberationCommittee().review(signal=signal, results=()).risk_decision


SHIPPED_RISK_GATES: Final[Mapping[str, Callable[[SignalFrame], RiskDecision]]] = {
    "runtime-risk-gate": _runtime_risk_gate,
    "deliberation-committee": _deliberation_committee,
}
"""**The single source for what counts as severe**: the two gates this build already ships.

Named callables and not a set of strings, which is the whole design -- see this module's
docstring. Every severity below is derived by calling these; a third gate arriving is one entry
here and no change to anything that reads a severity.
"""

_PROBE_AS_OF: Final[datetime] = datetime(2000, 1, 1, tzinfo=UTC)
"""A fixed instant for the one-flag probe. Neither gate reads `as_of`; that is measured by
`test_both_shipped_gates_answer_about_the_flags_and_about_nothing_else_on_the_signal` rather
than assumed here."""


def _probe(flags: tuple[str, ...]) -> SignalFrame:
    """The most boring valid `SignalFrame` carrying exactly `flags`, and nothing to read."""
    return SignalFrame(
        subject="governance-probe",
        as_of=_PROBE_AS_OF,
        direction="neutral",
        strength=0.0,
        confidence=0.0,
        horizon="1d",
        evidence_ids=("evd_governance_probe",),
        risk_flags=flags,
    )


def _rung(gate: RiskDecision, committee: RiskDecision) -> GovernanceSeverity:
    """The ladder as a function of the two gates' verdicts, and the only place it is decided."""
    if gate == "block":
        return "blocked"
    if committee == "block":
        return "severe"
    if gate == "reduce":
        return "reduced"
    if committee == "reduce":
        return "unrecognised"
    return "clear"


def _verdicts(flags: tuple[str, ...]) -> tuple[RiskDecision, RiskDecision]:
    """Both gates' verdicts on a flag tuple, asked of a canonical carrier of it.

    Deliberately **not** cached, unlike `flag_severity` below. Its key would be a whole flag
    tuple, and `POST /api/v1/screen` accepts caller-supplied `risk_flags`, so an unbounded
    memo on it is a map whose key space a request body chooses -- growth this process would
    never reclaim. Once per result costs one `SignalFrame` and one committee pass, which is
    small beside the run the result came from.
    """
    probe = _probe(flags)
    return _runtime_risk_gate(probe), _deliberation_committee(probe)


@lru_cache(maxsize=512)
def flag_severity(flag: str) -> GovernanceSeverity:
    """What one risk flag is worth, measured by putting it alone in front of both gates.

    Cached because the answer is a pure function of the string and `assess` asks it once per
    flag per result, while a whole screen carries only a handful of distinct flags between
    them. **Bounded** rather than `functools.cache`, for the reason `_verdicts` gives: the
    strings come from request bodies, and an unbounded memo over those is a leak a caller
    chooses the size of. `test_the_ladder_is_declared_once_and_every_rung_is_reachable`
    asserts the bound exists.
    """
    return _rung(*_verdicts((flag,)))


class GovernanceVerdict(BaseModel):
    """What the shipped gates say about one signal, with the flags that account for it.

    `driving_flags` is the point of this record and is why the screen reads *which* flags
    rather than how many: a verdict with no flags named is a number, and a number is what
    `max_risk_flags` already was.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: GovernanceSeverity
    driving_flags: tuple[str, ...]
    """The signal's own flags that sit at `severity`, in the order the signal carries them.

    Empty exactly when `severity` is `clear`, because a rung above `clear` is reached by a flag
    and by nothing else.
    """
    gate_decision: RiskDecision
    """`RiskGate.evaluate`'s verdict on this signal's flags, carried rather than summarised."""
    committee_decision: RiskDecision
    """`DeliberationCommittee.review`'s verdict on this signal's flags, with no debate."""


def assess(signal: SignalFrame) -> GovernanceVerdict:
    """Ask both shipped gates about one signal's flags and name the ones that decided it.

    **About the flags, on a canonical probe, rather than about the signal itself.** That is a
    measured requirement and not a stylistic preference: `DeliberationCommittee.review` is not
    total on `SignalFrame`. Handed an abstaining signal -- `direction="abstain"`, and therefore
    `evidence_ids=()` by `SignalFrame.validate_conclusion` -- it recomputes a direction from
    `adjusted_strength`, never reproduces `abstain`, and dies constructing its own
    `DeliberationOutcome` with *"directional signal requires evidence"*. Every abstention in
    this build is such a signal, `ScreeningCriteria.directions` lists `abstain` as a thing a
    caller may screen for, and PRD S42 makes explicit abstention a guarantee -- so a screen
    that called the committee on the signal it was given would raise on the one outcome the
    product promises to show.

    `tests/unit/product/test_governed_screening.py::
    test_the_shipped_committee_cannot_be_asked_about_an_abstaining_signal_at_all` pins that
    refusal on the real class, so it is red the day somebody repairs it upstream. The probe
    costs nothing in fidelity because both gates are functions of `risk_flags` alone, which
    `test_both_shipped_gates_answer_about_the_flags_and_about_nothing_else_on_the_signal`
    measures on nine otherwise-unrelated signals.
    """
    gate, committee = _verdicts(signal.risk_flags)
    severity = _rung(gate, committee)
    driving = (
        ()
        if severity == "clear"
        else tuple(flag for flag in signal.risk_flags if flag_severity(flag) == severity)
    )
    return GovernanceVerdict(
        severity=severity,
        driving_flags=driving,
        gate_decision=gate,
        committee_decision=committee,
    )
