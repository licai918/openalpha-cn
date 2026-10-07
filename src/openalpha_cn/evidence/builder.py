"""A-share-native evidence normalization from provider records."""

from typing import Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.json_value import thaw_json
from openalpha_cn.domain.risk_flag import RiskFlag
from openalpha_cn.providers.base import ProviderBatch, ProviderMetadata, ProviderRecord

EvidenceFamily = Literal["market_event", "disclosure", "theme", "catalyst", "capital"]

_REDISTRIBUTION_FLAGS: Final[dict[Literal["restricted", "unknown"], RiskFlag]] = {
    "restricted": RiskFlag.redistribution_restricted,
    "unknown": RiskFlag.redistribution_unknown,
}
"""Which flag a non-`allowed` redistribution term earns (`V2-P4-030`).

This was `f"redistribution_{metadata.redistribution}"`, and the f-string is how the vocabulary
drifted without anybody noticing. `EvidenceSnapshot.redistribution` is
`Literal["allowed", "restricted", "unknown"]`, so the expression could produce
`redistribution_restricted` or `redistribution_unknown` -- and `decisions/risk.py::RiskGate`
named only the second. All three shipped providers declare `restricted`, so **every flag this
build actually wrote for redistribution was one no gate had heard of**, while the one the gate
named could not be produced at all.

A `dict` keyed on the two non-`allowed` terms rather than a formatted string, because that is
what makes the pair checkable: the key type is the contract's own `Literal` minus `allowed`, so
a fourth redistribution term added to `EvidenceSnapshot` fails `mypy` here instead of silently
minting an undeclared flag at runtime.
"""


class _Facts(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class _LimitUpFacts(_Facts):
    close: float = Field(gt=0)
    pct_change: float
    board_count: int = Field(ge=1)


class _BrokenBoardFacts(_Facts):
    high: float = Field(gt=0)
    close: float = Field(gt=0)
    open_count: int = Field(ge=1)


class _ConsecutiveBoardFacts(_LimitUpFacts):
    board_count: int = Field(ge=2)


class _DisclosureFacts(_Facts):
    announcement_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)


class _ThemeFacts(_Facts):
    theme: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)


class _CatalystFacts(_Facts):
    headline: str = Field(min_length=1)
    catalyst_type: str = Field(min_length=1)


class _CapitalFacts(_Facts):
    net_inflow: float
    unit: str = Field(min_length=1)


_NORMALIZERS: dict[str, tuple[EvidenceFamily, type[_Facts]]] = {
    "limit_up": ("market_event", _LimitUpFacts),
    "broken_board": ("market_event", _BrokenBoardFacts),
    "consecutive_board": ("market_event", _ConsecutiveBoardFacts),
    "disclosure": ("disclosure", _DisclosureFacts),
    "theme": ("theme", _ThemeFacts),
    "catalyst": ("catalyst", _CatalystFacts),
    "capital": ("capital", _CapitalFacts),
}
"""Every evidence kind this build normalizes, and the family and facts model each earns.

**It is also the vocabulary `_build_one`'s refusal prints** (`V2-P5-043`). That refusal used to
be `unsupported evidence kind: filing` and stop there, which names the rejected kind and no way
out: the seven keys are declared here and nowhere a caller reads, so somebody holding a file of
their own could only guess. Reading the message off this dict rather than restating the seven
in a literal is the same rule `_REDISTRIBUTION_FLAGS` above is written for -- an eighth kind
added here reaches the refusal in the same commit, instead of shipping a message that names
seven of eight ways out.
"""


class EvidenceBuilder:
    """Validate provider facts and attach durable source and quality metadata."""

    def build(
        self,
        *,
        batch: ProviderBatch,
        metadata: ProviderMetadata,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Build normalized evidence snapshots from a provider batch."""
        if batch.provider_id != metadata.provider_id:
            raise ValueError("provider batch and metadata IDs do not match")
        if batch.status == "no_data":
            return ()
        return tuple(self._build_one(record=record, metadata=metadata) for record in batch.records)

    @staticmethod
    def _build_one(
        *,
        record: ProviderRecord,
        metadata: ProviderMetadata,
    ) -> EvidenceSnapshot:
        normalizer = _NORMALIZERS.get(record.kind)
        if normalizer is None:
            raise ValueError(
                f"unsupported evidence kind: {record.kind}; this build normalizes "
                f"{', '.join(_NORMALIZERS)}"
            )
        family, facts_model = normalizer
        facts = thaw_json(record.payload)
        facts_model.model_validate(facts)

        quality_flags: list[str] = []
        if metadata.redistribution != "allowed":
            quality_flags.append(_REDISTRIBUTION_FLAGS[metadata.redistribution].value)
        if record.source_uri is None:
            quality_flags.append(RiskFlag.source_uri_missing.value)
        if record.timeline.revision_time > record.timeline.available_time:
            quality_flags.append(RiskFlag.revised_after_initial_availability.value)

        return EvidenceSnapshot(
            subject=record.subject,
            kind=record.kind,
            timeline=record.timeline,
            source_id=metadata.provider_id,
            source_uri=record.source_uri,
            source_license=metadata.source_license,
            redistribution=metadata.redistribution,
            summary=record.summary,
            payload=cast(
                JsonValue,
                {
                    "schema": "a-share-evidence/v1",
                    "family": family,
                    "facts": facts,
                    "quality_flags": quality_flags,
                },
            ),
        )
