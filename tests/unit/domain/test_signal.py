from datetime import datetime

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.signal import SignalFrame


def test_signal_requires_evidence_for_a_directional_conclusion(
    plain_frozen_now: datetime,
) -> None:
    AS_OF = plain_frozen_now
    with pytest.raises(ValidationError, match="directional signal requires evidence"):
        SignalFrame(
            subject="000001.SZ",
            as_of=AS_OF,
            direction="bullish",
            strength=0.6,
            confidence=0.7,
            horizon="5d",
            evidence_ids=(),
        )


def test_signal_requires_an_explicit_reason_when_abstaining(plain_frozen_now: datetime) -> None:
    AS_OF = plain_frozen_now
    with pytest.raises(ValidationError, match="abstention_reason is required"):
        SignalFrame(
            subject="000001.SZ",
            as_of=AS_OF,
            direction="abstain",
            strength=0,
            confidence=0,
            horizon="5d",
        )


def test_signal_is_immutable_versioned_and_content_addressed(plain_frozen_now: datetime) -> None:
    AS_OF = plain_frozen_now
    signal = SignalFrame(
        subject="000001.SZ",
        as_of=AS_OF,
        direction="bullish",
        strength=0.6,
        confidence=0.7,
        horizon="5d",
        evidence_ids=("ev_123",),
        confirmation_conditions=("volume remains above its 20-day median",),
        invalidation_conditions=("closes below the event-day low",),
        risk_flags=("event_concentration",),
    )

    assert signal.schema_version == "signal-frame/v1"
    assert signal.signal_id.startswith("sig_")
    assert signal.model_copy().signal_id == signal.signal_id
    with pytest.raises(ValidationError, match="Instance is frozen"):
        signal.confidence = 0.8


def test_signal_rejects_naive_as_of_and_inconsistent_abstention(
    plain_frozen_now: datetime,
) -> None:
    AS_OF = plain_frozen_now
    with pytest.raises(ValidationError, match="timezone-aware"):
        SignalFrame(
            subject="000001.SZ",
            as_of=datetime(2026, 7, 24, 10, 0),
            direction="abstain",
            strength=0,
            confidence=0,
            horizon="5d",
            abstention_reason="No visible evidence.",
        )

    with pytest.raises(ValidationError, match="strength must be zero"):
        SignalFrame(
            subject="000001.SZ",
            as_of=AS_OF,
            direction="abstain",
            strength=0.1,
            confidence=0,
            horizon="5d",
            abstention_reason="No visible evidence.",
        )
