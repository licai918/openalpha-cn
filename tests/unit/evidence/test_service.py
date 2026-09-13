"""Tests for `evidence/service.py`'s provider-agnostic build flow (V2-P0B-011).

Before this task, `build_file_evidence` imported the concrete `FileProvider` and
hardcoded `ProviderRequest(dataset="events", ...)` inside `evidence/service.py` itself,
binding the shared evidence-build service to one specific provider implementation and to
one specific dataset name. `build_provider_evidence` replaces it: it accepts any
`providers.base.DataProvider`-conforming object and a caller-supplied `dataset`, so
`evidence/service.py` depends only on the provider Protocol, never on `FileProvider`
(the file-specific import moved to `cli.py`/`sdk.py`, the two callers that actually need
to read a file). `FakeDataProvider` below deliberately is not `FileProvider` -- proving
the service works against *any* Protocol-conforming double is the point.
"""

from datetime import UTC, datetime

from openalpha_cn.evidence.service import build_provider_evidence
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
)


class FakeDataProvider:
    """A minimal `DataProvider`-conforming double that is not `FileProvider`."""

    def __init__(self, *, metadata: ProviderMetadata, record: ProviderRecord) -> None:
        self._metadata = metadata
        self._record = record
        self.requested: ProviderRequest | None = None

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        self.requested = request
        return ProviderBatch(
            provider_id=self._metadata.provider_id,
            request=request,
            fetched_at=datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
            status="success",
            records=(self._record,),
        )


def _metadata() -> ProviderMetadata:
    return ProviderMetadata(
        provider_id="fake.provider",
        display_name="Fake provider",
        source_license="user-supplied",
        redistribution="restricted",
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input",
        failure_semantics="Malformed input raises ProviderFailure.",
    )


def _record() -> ProviderRecord:
    return ProviderRecord(
        subject="000001.SZ",
        kind="limit_up",
        timeline={
            "event_time": "2026-07-24T09:30:00+00:00",
            "available_time": "2026-07-24T10:00:00+00:00",
            "ingested_time": "2026-07-24T10:01:00+00:00",
            "revision_time": "2026-07-24T10:00:00+00:00",
        },
        source_uri="fixture://000001.SZ",
        summary="Visible before the request clock.",
        payload={"close": 10.5, "pct_change": 0.1, "board_count": 2},
    )


def test_build_provider_evidence_accepts_any_data_provider_and_caller_supplied_dataset() -> None:
    metadata = _metadata()
    record = _record()
    provider = FakeDataProvider(metadata=metadata, record=record)
    as_of = datetime(2026, 7, 24, 11, 0, tzinfo=UTC)

    response = build_provider_evidence(provider=provider, dataset="custom-dataset", as_of=as_of)

    assert provider.requested is not None
    assert provider.requested.dataset == "custom-dataset"
    assert provider.requested.as_of == as_of
    assert len(response.items) == 1
    assert response.items[0].subject == "000001.SZ"


def test_build_provider_evidence_uses_the_providers_own_metadata() -> None:
    """The service must read `provider.metadata` itself, not require a caller to pass a
    second, possibly-inconsistent copy alongside the provider object."""
    metadata = _metadata()
    record = _record()
    provider = FakeDataProvider(metadata=metadata, record=record)

    response = build_provider_evidence(
        provider=provider,
        dataset="events",
        as_of=datetime(2026, 7, 24, 11, 0, tzinfo=UTC),
    )

    assert response.items[0].source_license == metadata.source_license
    assert response.items[0].redistribution == metadata.redistribution
