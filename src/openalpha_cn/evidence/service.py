"""Shared evidence application flow used by CLI and HTTP interfaces."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.evidence.builder import EvidenceBuilder
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderMetadata,
    ProviderRequest,
    utc_now,
)
from openalpha_cn.providers.file import FileProvider


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


def build_file_evidence(
    *,
    path: Path,
    as_of: datetime,
    metadata: ProviderMetadata,
    clock: Callable[[], datetime] = utc_now,
) -> EvidenceBuildResponse:
    """Read a user-owned file and build evidence through the shared service."""
    provider = FileProvider(path=path, metadata=metadata, clock=clock)
    batch = provider.fetch(ProviderRequest(dataset="events", as_of=as_of))
    return build_evidence(EvidenceBuildRequest(metadata=metadata, batch=batch))


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
