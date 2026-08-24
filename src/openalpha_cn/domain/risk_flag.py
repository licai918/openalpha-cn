"""The risk flags a signal may carry, declared once with what each is worth (`V2-P4-030`).

`SignalFrame.risk_flags` was `tuple[str, ...]` with no vocabulary, and three modules each read a
closed subset of that open set:

- `decisions/risk.py::RiskGate` blocked on `{future_data, look_ahead_violation}` and reduced on
  `{redistribution_unknown, source_uri_missing, revised_after_initial_availability}`.
- `agents/committee.py::DeliberationCommittee` treated `{regulatory, data-quality, suspension}`
  as severe, from a set literal **inside `review`'s body** -- not an attribute, not a constant,
  so the only way to read it was to run a committee.
- `committee-disagreement`, which the committee raises about its own deliberation, was in
  neither, so `RiskGate` answered `pass` on a signal the committee had just marked as disputed.

The two gates' sets were measured **disjoint**. But the disagreement was the smaller half of the
defect. The set being *open* meant a producer that misspelled a flag did not fail: `future-data`
instead of `future_data` was worth `unrecognised`, which sits **above** `clear` but **below**
`reduced`, so the candidate carrying the typo of the most serious flag in the build sorted
*higher* than candidates whose flags were spelled correctly. `V2-P4-006` measured that from
inside `product/`, could not repair it there -- by the time a screen reads the string, the fact
that a flag was intended is gone -- and named the fix as closing the vocabulary at the point the
flags are written.

## One declaration, and the severity is part of it

This module is that fix, in `domain/run_mode.py`'s shape: one declaration, everything else names
it, and `tests/unit/domain/test_risk_flag.py::test_no_other_module_declares_the_risk_flag_set`
reads the source tree so a fourth copy fails without anybody registering it. The obvious repair
-- writing the union down somewhere and having the gates consult it -- is the one `V2-P4-006`
rejected as a *fourth* list, and it is rejected here too: `RiskGate._blocking_flags`,
`RiskGate._reducing_flags` and the committee's severe set are now **derived** from the enum
below rather than restated beside it, so there is nothing left to drift.

The severity rides on the member rather than in a `Mapping[RiskFlag, RiskFlagSeverity]` beside
it, and that is the load-bearing choice. A parallel mapping can be *incomplete* -- a member
added to the enum and forgotten in the map is a state Python permits, and it would fail exactly
the way the three old lists failed. Carrying the severity in the member's own value makes that
state unconstructable: `enum` cannot build a `RiskFlag` from a one-element tuple, so a flag
without a severity is a `TypeError` at import.

## Why a `StrEnum`

Same reason as `RunMode`, and it matters more here. `model_dump(mode="json")` emits the member's
**value**, so a flag serialises to the bare string it always did -- which is what makes this
narrowing move no stored `signal_id`, despite `signal_id` being a hash over the canonical JSON
of these fields (`domain/_identity.py`). `tests/unit/domain/test_risk_flag.py::
test_closing_the_vocabulary_moved_no_stored_signal_id` pins that against a fixed digest rather
than recomputing both sides, because a moved identity does not fail -- it silently re-identifies
every stored signal carrying a flag.

The one visible change is in the generated schema: `docs/api/schemas/signal-frame-v1.json` now
carries `risk_flags` as a `$ref` into `$defs` rather than an untyped `"items": {"type":
"string"}`. That is the point -- the published contract now says what the vocabulary *is*.

## What the three severities mean, and why there are only three

`clear` and `unrecognised` are rungs of `product/governance.py`'s ladder that no *flag* can
occupy: `clear` means a signal carries no flags at all, and `unrecognised` means a string is not
a flag. Both are properties of a signal or of a string, not of a member of this enum, so
`RiskFlagSeverity` is the three rungs that remain. `tests/unit/domain/test_risk_flag.py::
test_every_flag_declares_what_it_is_worth_and_cannot_be_added_without_one` holds that as a
strict subset relation against `SEVERITY_ORDER`, so the two vocabularies cannot drift apart.

Each gate maps a severity to its own decision, and the two deliberately differ on `severe`:

| severity  | `RiskGate` | committee | ladder rung |
|-----------|------------|-----------|-------------|
| `blocked` | `block`    | `block`   | `blocked`   |
| `severe`  | `reduce`   | `block`   | `severe`    |
| `reduced` | `reduce`   | `reduce`  | `reduced`   |

`severe` is what keeps the committee a distinguishable second opinion rather than a copy of the
runtime gate: a regulatory halt stops a *decision* without meaning the *evidence* is unusable,
which is why `RiskGate` reduces on it instead of blocking. Before this issue `RiskGate` answered
`pass` on all three `severe` flags and on `committee-disagreement` -- four fail-open holes, all
of them closed by the table above and none of them by weakening anything: every changed answer
moved toward refusal.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Literal

RiskFlagSeverity = Literal["reduced", "severe", "blocked"]
"""What one flag is worth, as a closed set.

The three rungs of `product/governance.py::SEVERITY_ORDER` that a flag can occupy. See this
module's docstring for why `clear` and `unrecognised` are not among them.
"""


class RiskFlag(StrEnum):
    """Every risk flag this build can attach to a signal, with what it is worth.

    Members are grouped by severity, worst first, and ordered oldest-first within a group so
    that additions read as additions. Order is not semantic: nothing sorts flags by declaration
    position, and a signal's severity is the worst of its flags rather than the first.
    """

    _severity: RiskFlagSeverity

    def __new__(cls, value: str, severity: RiskFlagSeverity) -> "RiskFlag":
        """Build one member from its wire string and its severity.

        The custom `__new__` is what makes the severity inseparable from the member -- see this
        module's docstring. It also has to exist mechanically: `StrEnum.__new__` would hand a
        two-element tuple to `str()` as `(object, encoding)` and fail.
        """
        member = str.__new__(cls, value)
        member._value_ = value
        member._severity = severity
        return member

    @property
    def severity(self) -> RiskFlagSeverity:
        """What this flag is worth, on `product/governance.py`'s ladder."""
        return self._severity

    # -- blocked: the evidence itself is unusable, so no decision may rest on it --------------

    future_data = ("future_data", "blocked")
    """Evidence dated after the `as_of` it was read at. A point-in-time integrity failure."""

    look_ahead_violation = ("look_ahead_violation", "blocked")
    """A reading that could not have been made at the instant it claims.

    See `backtest/replay.py`, which counts them on a frozen corpus.
    """

    # -- severe: the evidence is readable, but this name should not be acted on ---------------

    regulatory = ("regulatory", "severe")
    """A regulatory action against the subject."""

    data_quality = ("data-quality", "severe")
    """A declared defect in the subject's reported figures.

    Hyphenated, unlike its neighbours, because that is the string the committee has always
    matched and a rename would move the `signal_id` of every stored signal carrying it. The
    inconsistency is the vocabulary's history rather than a decision; declaring it here is what
    stops it being rediscovered.
    """

    suspension = ("suspension", "severe")
    """The subject is suspended from trading."""

    # -- reduced: a caution that should cost the signal its place, not its life ---------------

    redistribution_restricted = ("redistribution_restricted", "reduced")
    """The provider's licence restricts redistribution of this evidence.

    Written by `evidence/builder.py` as `f"redistribution_{metadata.redistribution}"`, and it is
    the only redistribution flag this build can actually produce: all three shipped providers
    (`providers/akshare.py`, `providers/tushare.py`, `providers/chainlin.py`) declare
    `redistribution="restricted"`. It was named by **no** gate before this issue, so the flag
    every real run writes was worth `unrecognised` while `redistribution_unknown` -- the one
    `RiskGate` did name -- could not be generated at all. That inversion is the clearest thing
    the open set had already cost, and it was found by measuring the writers rather than by
    reading the readers.
    """

    redistribution_unknown = ("redistribution_unknown", "reduced")
    """The provider states no redistribution terms."""

    source_uri_missing = ("source_uri_missing", "reduced")
    """The record carries no `source_uri`, so the claim cannot be traced back."""

    revised_after_initial_availability = ("revised_after_initial_availability", "reduced")
    """The record was revised after it first became available."""

    committee_disagreement = ("committee-disagreement", "reduced")
    """The deliberation committee could not separate the bull and bear cases.

    Raised by `agents/committee.py::DeliberationCommittee.review` about its own deliberation, so
    it is the one flag in the build guaranteed to be spelled correctly -- and `RiskGate` had
    never heard of it, which made it the sharpest of the fail-open holes: a signal explicitly
    recorded as disputed reached the runtime gate and cleared it.

    `reduced` rather than `severe`. A close debate is a reason to size down, not a reason to
    refuse: calling it severe would make the committee block on every signal it deliberated
    where `|debate_net| < 0.35`, which is a gate on the committee's own uncertainty rather than
    on the subject.
    """


RISK_FLAGS: Final[tuple[RiskFlag, ...]] = tuple(RiskFlag)
"""Every declared flag, in declaration order.

Derived from the enum rather than restated, so it cannot disagree with it. Exists for the
audits and the gates that iterate the vocabulary without importing `enum` machinery.
"""


RISK_FLAGS_BY_VALUE: Final[Mapping[str, RiskFlag]] = {flag.value: flag for flag in RISK_FLAGS}
"""Every declared flag by its wire string, for the two callers that start from an untrusted one.

Built from the enum rather than written out, so it holds no flag literals of its own -- which is
what keeps `tests/unit/domain/test_risk_flag.py::test_no_other_module_declares_the_risk_flag_set`
able to name this module as the only declaration.

It exists rather than calling `RiskFlag(value)` because `mypy` reads the custom `__new__` above
as the constructor's signature and rejects the one-argument value lookup that `EnumMeta.__call__`
actually performs. A `Mapping` is also the better shape for `product/governance.py`, which needs
"not a flag" as a value rather than as an exception.
"""


def parse_risk_flag(value: str) -> RiskFlag:
    """Resolve one wire string to its declared flag, refusing anything outside the vocabulary.

    The error names the offending string and the vocabulary, because the caller this exists for
    -- `agents/baseline.py::_quality_flags`, reading an evidence payload written elsewhere -- is
    exactly the one that needs to know *which* flag it spelled wrong. Under the open set it got
    no error at all: the misspelling was carried through and scored **above** the flag it was a
    misspelling of.
    """
    flag = RISK_FLAGS_BY_VALUE.get(value)
    if flag is None:
        declared = ", ".join(sorted(RISK_FLAGS_BY_VALUE))
        raise ValueError(f"{value!r} is not a declared risk flag; declared flags are: {declared}")
    return flag


def flags_with_severity(severity: RiskFlagSeverity) -> frozenset[RiskFlag]:
    """Every flag worth `severity`, for a gate that acts on a whole band at once.

    The one function both gates go through, so that "which flags block" is answered in a single
    place and a gate cannot quietly acquire a set of its own. `decisions/risk.py::RiskGate` and
    `agents/committee.py` differ in the *decision* they attach to a band, never in the band's
    membership.
    """
    return frozenset(flag for flag in RISK_FLAGS if flag.severity == severity)
