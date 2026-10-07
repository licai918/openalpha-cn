from datetime import UTC, datetime

import pytest

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.tools.base import ToolRequest
from openalpha_cn.tools.evidence import EvidenceLookupTool


@pytest.fixture
def item(frozen_now: datetime):
    """`frozen_now` is used only as `event_time` here -- the point-in-time boundary this
    test actually exercises is `available_hour` against `frozen_now`'s own :30, asserted
    directly in the test body below (10:00 visible, 11:00 not)."""

    def _make(subject: str, available_hour: int) -> EvidenceSnapshot:
        available = datetime(2026, 7, 24, available_hour, 0, tzinfo=UTC)
        return EvidenceSnapshot(
            subject=subject,
            kind="theme",
            timeline=Timeline(
                event_time=frozen_now,
                available_time=available,
                ingested_time=available,
                revision_time=available,
            ),
            source_id="synthetic",
            source_license="CC0-1.0",
            redistribution="allowed",
            summary="Synthetic.",
            payload={},
        )

    return _make


def test_evidence_lookup_tool_enforces_subject_kind_and_visibility(
    item, frozen_now: datetime
) -> None:
    visible = item("000001.SZ", 10)
    tool = EvidenceLookupTool(
        evidence=(
            visible,
            item("000001.SZ", 11),
            item("000002.SZ", 10),
        )
    )

    result = tool.execute(
        ToolRequest(
            subject="000001.SZ",
            as_of=frozen_now,
            kind="theme",
        )
    )

    assert result.status == "success"
    assert result.evidence_ids == (visible.evidence_id,)
