"""Deterministic risk gate for structured research signals."""

from typing import Literal

from openalpha_cn.domain.signal import SignalFrame

RiskDecision = Literal["pass", "reduce", "block"]


class RiskGate:
    """Map explicit signal risk flags to a stable gate decision."""

    _blocking_flags = frozenset({"future_data", "look_ahead_violation"})
    _reducing_flags = frozenset(
        {
            "redistribution_unknown",
            "source_uri_missing",
            "revised_after_initial_availability",
        }
    )

    def evaluate(self, signal: SignalFrame) -> RiskDecision:
        """Return pass, reduce, or block without hiding the signal."""
        flags = set(signal.risk_flags)
        if flags & self._blocking_flags:
            return "block"
        if flags & self._reducing_flags:
            return "reduce"
        return "pass"
