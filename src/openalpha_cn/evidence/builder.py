"""A-share-native evidence normalization from provider records."""

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.json_value import thaw_json
from openalpha_cn.providers.base import ProviderBatch, ProviderMetadata, ProviderRecord

EvidenceFamily = Literal["market_event", "disclosure", "theme", "catalyst", "capital"]


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
            raise ValueError(f"unsupported evidence kind: {record.kind}")
        family, facts_model = normalizer
        facts = thaw_json(record.payload)
        facts_model.model_validate(facts)

        quality_flags: list[str] = []
        if metadata.redistribution != "allowed":
            quality_flags.append(f"redistribution_{metadata.redistribution}")
        if record.source_uri is None:
            quality_flags.append("source_uri_missing")
        if record.timeline.revision_time > record.timeline.available_time:
            quality_flags.append("revised_after_initial_availability")

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
