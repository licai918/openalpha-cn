from datetime import datetime

import pytest

from openalpha_cn.domain.time import Timeline
from openalpha_cn.evidence.builder import _NORMALIZERS, EvidenceBuilder
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)


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


@pytest.fixture
def record(plain_frozen_now: datetime):
    def _make(*, kind: str, payload: dict[str, object]) -> ProviderRecord:
        return ProviderRecord(
            subject="000001.SZ",
            kind=kind,
            timeline=Timeline(
                event_time=plain_frozen_now,
                available_time=plain_frozen_now,
                ingested_time=plain_frozen_now,
                revision_time=plain_frozen_now,
            ),
            source_uri=f"fixture://{kind}/000001.SZ",
            summary=f"Synthetic {kind} record.",
            payload=payload,
        )

    return _make


@pytest.fixture
def batch(plain_frozen_now: datetime):
    def _make(item: ProviderRecord) -> ProviderBatch:
        return ProviderBatch(
            provider_id="synthetic.a-share",
            request=ProviderRequest(dataset="events", as_of=plain_frozen_now),
            fetched_at=plain_frozen_now,
            status="success",
            records=(item,),
        )

    return _make


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
    record,
    batch,
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


def test_builder_adds_quality_flags_without_losing_source_facts(record, batch) -> None:
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


def test_builder_rejects_invalid_or_unknown_evidence_kinds(record, batch) -> None:
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


def test_an_unsupported_kind_names_the_seven_kinds_this_build_normalizes(record, batch) -> None:
    """`V2-P5-043`. The refusal used to be `unsupported evidence kind: filing` and stop there.

    Measured through `openalpha evidence build` on a CSV whose only fault was `kind=filing`: a
    full rich traceback whose last line named the rejected kind and nothing else. A caller
    holding a file of their own has no way to learn the vocabulary from that -- the seven keys
    live in `_NORMALIZERS`, which is not a document anybody outside this repository reads -- so
    the refusal now carries them, which is this repository's own rule for a refusal
    (`create_app`: "naming the specific variable, never a bare traceback").

    The vocabulary is asserted against `_NORMALIZERS` itself rather than against a literal, so
    an eighth kind added there and left out of the message goes red here instead of shipping a
    refusal that names six of seven ways out.
    """
    with pytest.raises(ValueError) as caught:
        EvidenceBuilder().build(
            batch=batch(record(kind="filing", payload={"claim": "future"})),
            metadata=metadata(),
        )

    message = str(caught.value)
    assert "unsupported evidence kind: filing" in message
    assert ", ".join(_NORMALIZERS) in message
    assert tuple(_NORMALIZERS) == (
        "limit_up",
        "broken_board",
        "consecutive_board",
        "disclosure",
        "theme",
        "catalyst",
        "capital",
    )
