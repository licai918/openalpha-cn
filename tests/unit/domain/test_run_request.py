"""`ResearchRunRequest`'s point-in-time guard raises a typed violation (V2-P0B-014).

Companion to `tests/unit/backtest/test_replay.py`, which covers the other raise site
(`ReplayCase.validate_point_in_time`) and the classification logic in
`ReplayRunner.run()`. This file only proves that `domain/run_request.py`'s own guard --
the one `domain/` is allowed to raise without any upward import -- uses
`LookAheadViolationError`, not a bare `ValueError`.
"""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.evidence import EvidenceSnapshot, LookAheadViolationError
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.time import Timeline


def test_look_ahead_violation_error_is_a_value_error() -> None:
    """Every existing `except ValueError` call site must keep catching this unchanged."""
    assert issubclass(LookAheadViolationError, ValueError)


def test_evidence_not_visible_at_as_of_raises_look_ahead_violation_error(
    evidence,
    frozen_now,
) -> None:
    """`validate_evidence` raises `LookAheadViolationError`, not a bare `ValueError`.

    pydantic's `@model_validator(mode="after")` re-wraps whatever the validator raises
    into its own `pydantic.ValidationError` (also a `ValueError` subclass, so existing
    `except ValueError` call sites are unaffected), but preserves the original exception
    object at `errors()[0]["ctx"]["error"]` -- that is what must be
    `LookAheadViolationError`, which is what `ReplayRunner.run()`'s classification
    actually inspects (see `_is_look_ahead_violation` in `backtest/replay.py`).
    """
    base: EvidenceSnapshot = evidence(
        kind="limit_up",
        facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
    )
    not_yet_visible = base.model_copy(
        update={
            "timeline": Timeline(
                event_time=frozen_now,
                available_time=frozen_now + timedelta(hours=1),
                ingested_time=frozen_now + timedelta(hours=1),
                revision_time=frozen_now + timedelta(hours=1),
            )
        }
    )

    with pytest.raises(ValidationError) as exc_info:
        ResearchRunRequest(
            run_id="run_look_ahead",
            mode="replay",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(not_yet_visible,),
            code_commit="0123456789abcdef",
            config_digest="d" * 64,
            random_seed=7,
        )

    underlying = exc_info.value.errors()[0]["ctx"]["error"]
    assert isinstance(underlying, LookAheadViolationError)


def test_evidence_revised_after_as_of_is_refused_as_a_look_ahead(evidence, frozen_now) -> None:
    """A record that first became available an hour before `as_of` but carries a version revised
    an hour after it is not what the request could have seen, so the whole request is refused --
    with the same typed violation and the same sentence as a record not yet available at all.

    "not visible" is still the accurate word: visibility now waits for both clocks, and the
    refusal is a statement about visibility rather than about which clock was late. The same
    record is accepted at its revision instant, so the refusal is attributable to the revision.
    """
    base: EvidenceSnapshot = evidence(
        kind="limit_up",
        facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
    )
    revised_later = base.model_copy(
        update={
            "timeline": Timeline(
                event_time=frozen_now - timedelta(hours=2),
                available_time=frozen_now - timedelta(hours=1),
                ingested_time=frozen_now + timedelta(hours=1),
                revision_time=frozen_now + timedelta(hours=1),
            )
        }
    )

    def request(as_of: datetime) -> ResearchRunRequest:
        return ResearchRunRequest(
            run_id="run_revised_later",
            mode="replay",
            subject="000001.SZ",
            as_of=as_of,
            evidence=(revised_later,),
            code_commit="0123456789abcdef",
            config_digest="d" * 64,
            random_seed=7,
        )

    with pytest.raises(ValidationError, match="evidence is not visible at request as_of") as caught:
        request(frozen_now)

    assert isinstance(caught.value.errors()[0]["ctx"]["error"], LookAheadViolationError)
    assert request(frozen_now + timedelta(hours=1)).evidence == (revised_later,)
