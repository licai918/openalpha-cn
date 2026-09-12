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


class SerializedEvidenceMismatchError(ValueError):
    """A serialized evidence item whose supplied `evidence_id` or `content_hash` does not match it.

    `OA-EVID-003`. `parse_serialized_evidence` raises this for the one refusal that is about
    identity, and a plain `ValueError` -- pydantic's `ValidationError` among them -- for every
    structural fault. A `ValueError` subclass for `LookAheadViolationError`'s reason
    (`domain/evidence.py`): a caller already catching `ValueError`, `openalpha research run` among
    them, keeps catching it unchanged, while `ResearchApiRequest.verify_serialized_evidence`
    (`api/app.py`) can hand every structural fault to pydantic and let exactly this one through.
    Before the type existed that validator had one `except ValueError` for both, and a tampered
    item reached the client as `extra_forbidden` rather than as this refusal.

    The message names the offending item's own position in the array, e.g. `"evidence[1]: ..."`
    (`D10` review Minor-1, `.superpowers/sdd/d10-review.md`). Before that report, a mismatch was
    reported with no index, and -- worse -- an unrelated structural fault sitting *earlier* in
    the same array could hide the mismatch entirely: `parse_serialized_evidence` built one item
    at a time and stopped at the first fault of *either* kind, so a structural fault at index 0
    meant the tampered item at index 1 was never even reached, and the caller fell back to
    `EvidenceSnapshot`'s own `extra="forbid"`, reporting `extra_forbidden` instead of this
    refusal. See `parse_serialized_evidence` for how that is now avoided.
    """


def parse_serialized_evidence(value: object) -> tuple[EvidenceSnapshot, ...]:
    """Verify serialized IDs/hashes and return trusted evidence models.

    A supplied `evidence_id` or `content_hash` that does not match the recomputed one raises
    `SerializedEvidenceMismatchError`, naming the item's own index in `value`; anything
    structural raises a plain `ValueError`, pydantic's `ValidationError` included.

    A mismatch is found **wherever it sits**, scanning left to right: an item that fails to
    validate structurally is remembered (only the first such fault) and skipped rather than
    raised on the spot, so a later item is still reached and checked for a mismatch. Only if the
    whole array turns up no mismatch is that remembered structural fault finally raised -- at
    which point it is exactly the fault `EvidenceSnapshot.model_validate` itself raised, so a
    caller falling back to field validation on the untouched body (`ResearchApiRequest
    .verify_serialized_evidence`, `api/app.py`) sees the same thing it always has. A mismatch
    found anywhere always wins over a structural fault found anywhere else, and the first
    mismatch found (lowest index) is the one reported -- consistent with this repository's other
    per-array refusals (`research_result_io.research_refusal_detail` also reports only the first
    faulty record).

    One item can still defeat this: a structurally invalid item's `evidence_id`/`content_hash`
    cannot be checked at all, because computing what they *should* be needs a successfully
    validated model. An item that is both edited and missing a required field is therefore
    reported structurally, not as a mismatch, exactly as before this change.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("serialized evidence must be an array")
    verified: list[EvidenceSnapshot] = []
    structural_fault: ValueError | None = None
    for index, raw in enumerate(value):
        if isinstance(raw, EvidenceSnapshot):
            verified.append(raw)
            continue
        if not isinstance(raw, Mapping):
            if structural_fault is None:
                structural_fault = ValueError("serialized evidence items must be objects")
            continue
        clean = dict(raw)
        supplied_id = clean.pop("evidence_id", None)
        supplied_hash = clean.pop("content_hash", None)
        try:
            item = EvidenceSnapshot.model_validate(clean)
        except ValueError as error:
            if structural_fault is None:
                structural_fault = error
            continue
        if supplied_id is not None and supplied_id != item.evidence_id:
            raise SerializedEvidenceMismatchError(
                f"evidence[{index}]: serialized evidence_id does not match evidence content"
            )
        if supplied_hash is not None and supplied_hash != item.content_hash:
            raise SerializedEvidenceMismatchError(
                f"evidence[{index}]: serialized content_hash does not match evidence content"
            )
        verified.append(item)
    if structural_fault is not None:
        raise structural_fault
    return tuple(verified)
