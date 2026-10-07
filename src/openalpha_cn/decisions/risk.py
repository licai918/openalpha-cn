"""Deterministic risk gate for structured research signals."""

from typing import Final, Literal

from openalpha_cn.domain.risk_flag import RiskFlag, flags_with_severity
from openalpha_cn.domain.signal import SignalFrame

RiskDecision = Literal["pass", "reduce", "block"]


class RiskGate:
    """Map explicit signal risk flags to a stable gate decision.

    ## The flags are read off the vocabulary, not restated here (`V2-P4-030`)

    Both sets below used to be hand-written `frozenset` literals, and they were two of the three
    disjoint declarations of a vocabulary that had no single source. They are derived from
    `domain/risk_flag.py::RiskFlag` now, so a flag added there reaches this gate on the same
    commit with no edit here and nothing to rediscover.

    The derivation is not a rename of the old sets. `_reducing_flags` now takes the `severe`
    band as well as the `reduced` one, which is the fail-open hole this issue closes: a
    `regulatory` flag, a `suspension` or a `data-quality` defect used to reach this gate and
    return **`pass`**, because the only module that named those three was the *optional*
    deliberation committee. So did `committee-disagreement`, a flag the committee raises about
    its own deliberation and therefore the one string in the build guaranteed to be spelled
    correctly. Four flags that meant "do not act on this name" cleared the gate that decides
    whether a name is acted on.

    They reduce rather than block, and the distinction is the point of having two gates.
    `blocked` means the *evidence* is unusable -- a `future_data` reading could not have been
    made at the instant it claims, so nothing may rest on it. `severe` means the evidence is
    fine and the *name* should not be traded; that is a judgement the committee is designed to
    make and PRD S41 keeps optional, so the runtime gate sizes down rather than refusing
    outright. `tests/unit/domain/test_risk_flag.py::
    test_both_gates_answer_about_every_declared_flag_and_agree_with_its_severity` drives every
    declared flag through both gates and holds that split.
    """

    _blocking_flags: Final[frozenset[RiskFlag]] = flags_with_severity("blocked")
    _reducing_flags: Final[frozenset[RiskFlag]] = flags_with_severity("severe") | (
        flags_with_severity("reduced")
    )

    def evaluate(self, signal: SignalFrame) -> RiskDecision:
        """Return pass, reduce, or block without hiding the signal."""
        flags = set(signal.risk_flags)
        if flags & self._blocking_flags:
            return "block"
        if flags & self._reducing_flags:
            return "reduce"
        return "pass"
