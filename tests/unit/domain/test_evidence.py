from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.evidence import EvidenceSnapshot, FreezePayloadMixin
from openalpha_cn.domain.time import Timeline
from openalpha_cn.providers.base import ProviderRecord


def timeline() -> Timeline:
    return Timeline(
        event_time=datetime(2026, 7, 24, 9, 30, tzinfo=UTC),
        available_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
        ingested_time=datetime(2026, 7, 24, 9, 36, tzinfo=UTC),
        revision_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
    )


def snapshot(payload: dict[str, Any]) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=timeline(),
        source_id="synthetic.limit-up",
        source_uri="fixture://limit-up/2026-07-24/000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="The stock reached its daily price limit.",
        payload=payload,
    )


def test_evidence_identity_is_stable_across_payload_key_order() -> None:
    first = snapshot({"price": 12.34, "board_count": 2})
    second = snapshot({"board_count": 2, "price": 12.34})

    assert first.content_hash == second.content_hash
    assert first.evidence_id == second.evidence_id
    assert first.evidence_id.startswith("ev_")
    assert len(first.content_hash) == 64


def test_evidence_payload_is_deeply_immutable() -> None:
    item = snapshot({"tags": ["robotics"], "metrics": {"board_count": 2}})
    payload = cast(Mapping[str, Any], item.payload)

    with pytest.raises(TypeError):
        payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        cast(Mapping[str, Any], payload["metrics"])["board_count"] = 3  # type: ignore[index]
    with pytest.raises(AttributeError):
        cast(tuple[str, ...], payload["tags"]).append("ai")  # type: ignore[attr-defined]


def test_evidence_serializes_payload_as_json_data() -> None:
    item = snapshot({"tags": ["robotics"], "metrics": {"board_count": 2}})

    dumped = item.model_dump(mode="json")

    assert dumped["payload"] == {
        "tags": ["robotics"],
        "metrics": {"board_count": 2},
    }
    assert dumped["content_hash"] == item.content_hash
    assert dumped["evidence_id"] == item.evidence_id


def test_evidence_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceSnapshot(
            subject="000001.SZ",
            kind="limit_up",
            timeline=timeline(),
            source_id="synthetic.limit-up",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Known fields only.",
            payload={},
            unexpected=True,
        )


def test_evidence_visibility_uses_information_availability() -> None:
    item = snapshot({"price": 12.34})

    assert item.visible_at(datetime(2026, 7, 24, 9, 34, tzinfo=UTC)) is False
    assert item.visible_at(datetime(2026, 7, 24, 9, 35, tzinfo=UTC)) is True


def test_freeze_payload_is_one_implementation_both_hosts_inherit() -> None:
    """`ProviderRecord.freeze_payload` (`providers/base.py`) and `EvidenceSnapshot.freeze_payload`
    (this module) were two function bodies an AST comparison found byte-identical. `V2-P5-071`
    recorded the same finding and merged nothing, noting only that "consolidating needs a
    mixin" -- this is that mixin.

    `is` rather than `==`: two independently written bodies that agree on every case the other
    `EvidenceSnapshot`/`ProviderRecord` tests probe are still two call sites to keep in sync by
    hand, and nothing above would tell that apart from one shared implementation. `is` cannot be
    fooled that way -- either both classes name the same function object or they do not.

    `is` alone still passes for a different accident, flagged in this release's last review: the
    same function object assigned to a class's own attribute (`freeze_payload =
    FreezePayloadMixin.freeze_payload`, spelled out by hand on each host) rather than inherited.
    That still leaves two statements an editor has to keep in sync, and `is` alone cannot see the
    difference -- the object on the right of `is` is identical either way. The loop below reads
    each host's own `__dict__`, which a merely-assigned attribute would populate exactly like a
    restated `def` would, rather than the attribute lookup that inheritance also satisfies: it
    fails unless *neither* host defines `freeze_payload` for itself.
    """
    assert ProviderRecord.freeze_payload is FreezePayloadMixin.freeze_payload
    assert EvidenceSnapshot.freeze_payload is FreezePayloadMixin.freeze_payload

    for host_type in (ProviderRecord, EvidenceSnapshot):
        assert "freeze_payload" not in vars(host_type), (
            f"{host_type.__name__} defines its own freeze_payload instead of inheriting "
            "FreezePayloadMixin's"
        )
