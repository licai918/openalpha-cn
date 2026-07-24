"""Shared evidence application flow used by CLI and HTTP interfaces."""

from collections.abc import Callable
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
