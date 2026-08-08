"""Shared evidence application flow used by CLI and HTTP interfaces."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.evidence.builder import EvidenceBuilder
from openalpha_cn.providers.base import (
    DataProvider,
    ProviderBatch,
    ProviderMetadata,
    ProviderRequest,
)


class EvidenceStore(Protocol):
    """Extension contract for durable point-in-time evidence storage.

    Mirrors the `runtime.memory.ResearchMemory` precedent: the Protocol lives beside the
    evidence service layer (`evidence/`) that both `sdk.py` and `api/app.py` already
    import from for building evidence, not in `storage/`. `ParquetEvidenceStore`'s full
    public surface is exactly `append`/`query`, so this Protocol declares both -- unlike
    the other storage Protocols in this task, there was no wider surface to narrow.
    """

    def append(self, items: tuple[EvidenceSnapshot, ...]) -> Path:
        """Write one content-addressed batch of evidence and return its location."""

    def query(
        self,
        *,
        as_of: datetime,
        subject: str | None = None,
        kind: str | None = None,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Return evidence available by ``as_of``, ordered deterministically."""


class EvidenceBuildRequest(BaseModel):
    """Provider batch and policy metadata accepted by the build service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metadata: ProviderMetadata
    batch: ProviderBatch


class EvidenceBuildResponse(BaseModel):
    """Versioned evidence items returned by every public interface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvidenceSnapshot, ...]


def build_evidence(request: EvidenceBuildRequest) -> EvidenceBuildResponse:
    """Build evidence through the shared normalization path."""
    items = EvidenceBuilder().build(batch=request.batch, metadata=request.metadata)
    return EvidenceBuildResponse(items=items)


def build_provider_evidence(
    *,
    provider: DataProvider,
    dataset: str,
    as_of: datetime,
) -> EvidenceBuildResponse:
    """Fetch one point-in-time batch through any `DataProvider` and build evidence.

    Depends only on the `DataProvider` Protocol (`providers/base.py`), never on a
    concrete provider implementation such as `FileProvider` -- callers that need a
    specific provider (`cli.py`, `sdk.py` construct `FileProvider` for their user-owned
    file inputs) build it themselves and inject it here. `dataset` is likewise supplied
    by the caller instead of being hardcoded, so this function stays usable for any
    dataset name a caller's provider actually serves.
    """
    batch = provider.fetch(ProviderRequest(dataset=dataset, as_of=as_of))
    return build_evidence(EvidenceBuildRequest(metadata=provider.metadata, batch=batch))


def parse_serialized_evidence(value: object) -> tuple[EvidenceSnapshot, ...]:
    """Verify serialized IDs/hashes and return trusted evidence models."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("serialized evidence must be an array")
    verified: list[EvidenceSnapshot] = []
    for raw in value:
        if isinstance(raw, EvidenceSnapshot):
            verified.append(raw)
            continue
        if not isinstance(raw, Mapping):
            raise ValueError("serialized evidence items must be objects")
        clean = dict(raw)
        supplied_id = clean.pop("evidence_id", None)
        supplied_hash = clean.pop("content_hash", None)
        item = EvidenceSnapshot.model_validate(clean)
        if supplied_id is not None and supplied_id != item.evidence_id:
            raise ValueError("serialized evidence_id does not match evidence content")
        if supplied_hash is not None and supplied_hash != item.content_hash:
            raise ValueError("serialized content_hash does not match evidence content")
        verified.append(item)
    return tuple(verified)
