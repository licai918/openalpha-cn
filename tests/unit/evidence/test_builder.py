from datetime import UTC, datetime

import pytest

from openalpha_cn.domain.time import Timeline
from openalpha_cn.evidence.builder import EvidenceBuilder
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)

NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def metadata() -> ProviderMetadata:
    return ProviderMetadata(
        provider_id="synthetic.a-share",
        display_name="Synthetic A-share fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="frozen-fixture",
        failure_semantics="Invalid fixture records raise validation errors.",
    )


def record(*, kind: str, payload: dict[str, object]) -> ProviderRecord:
    return ProviderRecord(
        subject="000001.SZ",
        kind=kind,
        timeline=Timeline(
            event_time=NOW,
            available_time=NOW,
            ingested_time=NOW,
            revision_time=NOW,
        ),
        source_uri=f"fixture://{kind}/000001.SZ",
        summary=f"Synthetic {kind} record.",
        payload=payload,
    )


def batch(item: ProviderRecord) -> ProviderBatch:
    return ProviderBatch(
        provider_id="synthetic.a-share",
        request=ProviderRequest(dataset="events", as_of=NOW),
        fetched_at=NOW,
        status="success",
        records=(item,),
    )


@pytest.mark.parametrize(
    ("kind", "payload", "family"),
    [
        ("limit_up", {"close": 10.5, "pct_change": 9.99, "board_count": 1}, "market_event"),
        (
            "broken_board",
            {"high": 10.5, "close": 9.8, "open_count": 2},
            "market_event",
        ),
        (
            "consecutive_board",
            {"close": 10.5, "pct_change": 10.01, "board_count": 3},
            "market_event",
        ),
        (
            "disclosure",
            {"announcement_id": "ann-1", "title": "Annual report", "category": "periodic"},
            "disclosure",
        ),
        ("theme", {"theme": "机器人", "score": 0.82}, "theme"),
        (
            "catalyst",
            {"headline": "New policy published", "catalyst_type": "policy"},
            "catalyst",
        ),
        ("capital", {"net_inflow": 1200000, "unit": "CNY"}, "capital"),
    ],
)
def test_builder_normalizes_all_v1_a_share_evidence_families(
    kind: str,
    payload: dict[str, object],
    family: str,
) -> None:
    item = EvidenceBuilder().build(
        batch=batch(record(kind=kind, payload=payload)),
        metadata=metadata(),
    )

    assert len(item) == 1
    assert item[0].kind == kind
    assert item[0].source_id == "synthetic.a-share"
    assert item[0].source_license == "CC0-1.0"
    assert item[0].payload["schema"] == "a-share-evidence/v1"
    assert item[0].payload["family"] == family
    assert item[0].payload["facts"] == payload


def test_builder_adds_quality_flags_without_losing_source_facts() -> None:
    source = record(
        kind="limit_up",
        payload={"close": 10.5, "pct_change": 9.99, "board_count": 1},
    ).model_copy(update={"source_uri": None})
    restricted = metadata().model_copy(update={"redistribution": "restricted"})

    item = EvidenceBuilder().build(batch=batch(source), metadata=restricted)[0]

    assert item.payload["quality_flags"] == (
        "redistribution_restricted",
        "source_uri_missing",
    )


def test_builder_rejects_invalid_or_unknown_evidence_kinds() -> None:
    with pytest.raises(ValueError, match="board_count"):
        EvidenceBuilder().build(
            batch=batch(record(kind="consecutive_board", payload={"board_count": 1})),
            metadata=metadata(),
        )

    with pytest.raises(ValueError, match="unsupported evidence kind"):
        EvidenceBuilder().build(
            batch=batch(record(kind="vision_only", payload={"claim": "future"})),
            metadata=metadata(),
        )
