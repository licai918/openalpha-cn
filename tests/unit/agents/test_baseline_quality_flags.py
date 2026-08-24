"""What a baseline agent does with an evidence payload's `quality_flags` (`V2-P4-030`).

`agents/baseline.py::_quality_flags` is the producer half of the open-set defect. It used to
`str()` whatever a payload's `quality_flags` held and hand the result to
`SignalFrame.risk_flags`, which accepted anything -- so a payload writing `future-data` instead
of `future_data` was not ignored, it was **scored**, and scored *better* than the flag it was a
misspelling of: `unrecognised` outranks `blocked` on `product/governance.py`'s ladder, so the
candidate carrying the typo moved up a governed screen.

That path is reachable from outside the process. `EvidenceSnapshot.payload` is an unschema'd
`JsonValue`, and `POST /api/v1/research/run` takes evidence straight off a request body.

These tests exist because a mutation sweep found the refusal unheld: making `parse_risk_flag`
return a flag instead of raising, and making `_quality_flags` silently drop an undeclared string
instead of refusing, were the only two mutants of 25 that survived the suite. Both are the same
gap -- the choice between "refuse" and "drop" was unobservable, which meant the fail-closed
guard this issue claims to add was not actually guarded by anything.
"""

from datetime import datetime

import pytest

from openalpha_cn.agents.base import AgentContext, AgentResult
from openalpha_cn.agents.baseline import MarketAgent
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.risk_flag import RiskFlag
from openalpha_cn.domain.time import Timeline


def evidence_carrying(flags: list[str], now: datetime) -> EvidenceSnapshot:
    """One market-event snapshot whose only interesting field is `payload["quality_flags"]`."""
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(event_time=now, available_time=now, ingested_time=now, revision_time=now),
        source_id="synthetic.a-share",
        source_uri="fixture://quality-flags/000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Quality-flag fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": flags,
        },
    )


def analyze(flags: list[str], now: datetime) -> AgentResult:
    return MarketAgent().analyze(
        AgentContext(
            run_id="run-quality-flags",
            subject="000001.SZ",
            as_of=now,
            evidence=(evidence_carrying(flags, now),),
        )
    )


def test_a_declared_payload_flag_reaches_the_signal_as_a_member(
    plain_frozen_now: datetime,
) -> None:
    """The ordinary path, which is what makes the refusal below a refusal and not a breakage."""
    result = analyze([RiskFlag.source_uri_missing.value], plain_frozen_now)

    assert result.signal.risk_flags == (RiskFlag.source_uri_missing,)
    assert result.signal.risk_flags[0].severity == "reduced"


def test_an_undeclared_payload_flag_is_refused_by_name_rather_than_carried_or_dropped(
    plain_frozen_now: datetime,
) -> None:
    """The fail-closed half, and the direction that matters.

    Two wrong answers were available here and the suite could not tell them apart. Carrying the
    string is what the old code did, and it *promoted* the candidate. Dropping it silently would
    be the tempting repair and is just as bad in a quieter way: the caution the payload meant to
    raise disappears, and the candidate ends up on `clear` -- indistinguishable from evidence
    that had nothing to flag.

    So the assertion is not merely "it raises". It is that the message names the offending
    string, because the producer that wrote the typo is the only party who can fix it, and an
    error that says only "invalid risk flag" sends them looking through a payload by hand.
    """
    with pytest.raises(ValueError) as caught:
        analyze(["future-data"], plain_frozen_now)

    message = str(caught.value)
    assert "future-data" in message
    assert "future_data" in message, "the message must show the vocabulary it was measured against"


def test_one_undeclared_flag_refuses_the_whole_signal_rather_than_the_flag(
    plain_frozen_now: datetime,
) -> None:
    """A payload mixing a good flag with a bad one is refused, not partially honoured.

    This is the case a "drop the unknown ones" implementation passes and this one does not, and
    it is why the mutant that dropped silently was worth killing separately: a signal built from
    a payload the build could not fully read is a signal whose flag set is a guess.
    """
    with pytest.raises(ValueError, match="future-data"):
        analyze([RiskFlag.source_uri_missing.value, "future-data"], plain_frozen_now)
