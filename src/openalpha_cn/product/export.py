"""The shareable form of an immutable report, with the provider's bytes taken out of it.

`V2-P5-022`. PRD §5.9 `S72` ("Reports preserve source and redistribution restrictions") and
§5.10 `S81` ("Immutable reports exportable without restricted raw payloads") are both scoped
**IN-最小**, and Implementation Decision 27 states that minimum as one sentence: 不导出 Tushare
原始 payload. Its gloss, translated: Tushare is `restricted`; for your own use that is
harmless, but do not write the raw data into a report you might share.

`tests/unit/product/test_report_export.py` is the measurement of every claim below, and
`tests/integration/test_report_export_interfaces.py` holds the three product faces to one
artifact.

## Where the licence comes from

`EvidenceSnapshot` carries `source_license` and
`redistribution: Literal["allowed", "restricted", "unknown"]`, written by `evidence/builder.py`
straight off the provider's own `ProviderMetadata`. All three shipped providers declare
`redistribution="restricted"`. That is the only place in this repository where a per-row
licence travels: shortlist answers, factor-experiment envelopes, the prediction register and
`construction_view` carry none, which is why this module gates *reports* -- the surface whose
rows do carry one -- and makes no licence claim anywhere else.

## What is withheld

`payload`, and only `payload`. Measured on the real adapter: `providers/tushare.py` builds each
record with `payload=cast(JsonValue, row)`, the upstream response row verbatim -- that is the
"原始 payload" Decision 27 names. The same constructor builds `summary` as
`f"Tushare {kind} record for {subject} on {date}."`, a template written in this repository
carrying a dataset name, a subject and a date and no provider field values, so the summary is
kept. The boundary is Decision 27's, between the provider's bytes and our own sentence about
them, and it is stated here rather than left implicit.

## Withheld is a tag, never an absence

`ExportedEvidence.body` is a discriminated union, not a nullable `payload`. `JsonValue` admits
`null`, so an unrestricted evidence whose payload *is* `null` and a restricted evidence whose
payload was taken away are byte-identical under any design that signals withholding by
absence -- and they are different facts, one about the evidence and one about the licence. The
same reasoning `panelState.ts` gives for keeping `idle` and `empty` apart.

A withheld record keeps the licence, the source and a reason, so a reader can tell what was
removed and go and ask the source for it. An export that simply dropped the field would be a
lie of omission, and this repository's rule is that an absence is named.

## `unknown` is not permission

Only `"allowed"` releases a payload. `"unknown"` is treated exactly as `"restricted"`, which is
the rule the web face already applies (`contractState.ts`, `redistribution !== "allowed"`) and
the fail-closed direction: withholding something that turned out to be free costs a missing
payload; the other mistake publishes somebody's licensed data.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, computed_field

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.json_value import thaw_json
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.time import Timeline
from openalpha_cn.domain.versioning import ContractVersions, single_version

__all__ = [
    "REPORT_EXPORT_VERSIONS",
    "ExportedEvidence",
    "IncludedPayload",
    "ReportExport",
    "UnrecoverableEvidence",
    "WithheldPayload",
    "export_report",
]

RELEASING_TERM: Final[Literal["allowed"]] = "allowed"
"""The one `redistribution` term that lets a payload leave this process.

A named constant rather than a literal at the comparison, so the rule has somewhere to be
read and the two non-releasing terms have somewhere to be listed against it.

Typed `Literal["allowed"]` and not `str`, and that is the fail-closed half: it lets mypy
narrow the other branch to `Literal["restricted", "unknown"]`, which is exactly
`WithheldPayload.redistribution`. A fourth term added to `EvidenceSnapshot` therefore fails
type-checking *here*, at the gate, instead of arriving as a release nobody voted for --
the arrangement `evidence/builder.py::_REDISTRIBUTION_FLAGS` already uses one layer down.
"""

WITHHELD_REASON: Final[str] = (
    "the provider's licence does not permit redistribution of this payload; "
    "request it from the source named here"
)
"""Why the payload is gone, and what to do about it.

Fixed text rather than per-provider prose: the reason is the same fact every time -- the
licence -- and the fields beside it already say *whose* licence and *which* terms.
"""


class IncludedPayload(BaseModel):
    """The evidence's payload, released because its licence releases it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: Literal["included"] = "included"
    payload: JsonValue


class WithheldPayload(BaseModel):
    """The payload's place, with the licence that kept it out.

    Carries `redistribution` and `source_license` rather than only a message, so a reader can
    act on it: `restricted` and `unknown` are different situations -- the second may become the
    first once somebody reads the terms -- and collapsing them into one sentence would delete
    that difference.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: Literal["withheld"] = "withheld"
    redistribution: Literal["restricted", "unknown"]
    source_license: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class ExportedEvidence(BaseModel):
    """One cited evidence item as it appears in a shareable artifact.

    Every field except `body` is a fact this repository wrote: the identity, the four clocks,
    which provider it came from, under what licence, and our own one-line summary. `body` is
    the only place the provider's own bytes could be, and it is the only thing the licence
    gate acts on.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    content_hash: str
    subject: str
    kind: str
    timeline: Timeline
    source_id: str
    source_uri: str | None
    source_license: str
    redistribution: Literal["allowed", "restricted", "unknown"]
    summary: str
    body: Annotated[IncludedPayload | WithheldPayload, Field(discriminator="disposition")]


class UnrecoverableEvidence(BaseModel):
    """A citation this export could not resolve.

    `ResearchReport.evidence_ids` is part of the report's content-derived identity, so a
    shorter evidence list is a discrepancy rather than a smaller answer: it would read as
    "this report rested on two items" when it rested on three and one is no longer reachable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    reason: Literal["not_recoverable_from_the_store_at_the_report_clock"] = (
        "not_recoverable_from_the_store_at_the_report_clock"
    )


class ReportExport(BaseModel):
    """One immutable report plus its evidence, licence-filtered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-report-export/v1"] = "research-report-export/v1"
    report: ResearchReport
    evidence: tuple[ExportedEvidence, ...]
    evidence_not_recovered: tuple[UnrecoverableEvidence, ...]

    @computed_field(return_type=int)  # type: ignore[prop-decorator]
    @property
    def included_count(self) -> int:
        """How many payloads travelled, counted off the records rather than stated beside them."""
        return sum(1 for item in self.evidence if item.body.disposition == "included")

    @computed_field(return_type=int)  # type: ignore[prop-decorator]
    @property
    def withheld_count(self) -> int:
        """How many payloads the licence kept out."""
        return sum(1 for item in self.evidence if item.body.disposition == "withheld")


REPORT_EXPORT_VERSIONS: ContractVersions[ReportExport] = single_version(
    "research-report-export", ReportExport
)


def _body(item: EvidenceSnapshot) -> IncludedPayload | WithheldPayload:
    """Decide one payload's disposition from one licence term, fail-closed."""
    if item.redistribution == RELEASING_TERM:
        # `thaw_json` for the reason `EvidenceSnapshot.serialize_payload` exists: the
        # snapshot freezes its payload into `mappingproxy`/tuple on construction so its
        # content hash cannot be edited out from under it, and those are not JSON values.
        return IncludedPayload(payload=thaw_json(item.payload))
    return WithheldPayload(
        redistribution=item.redistribution,
        source_license=item.source_license,
        reason=WITHHELD_REASON,
    )


def export_report(
    *, report: ResearchReport, evidence: tuple[EvidenceSnapshot, ...]
) -> ReportExport:
    """Assemble the shareable form of `report` from whatever evidence was recoverable.

    Pure: the caller resolves the evidence and this decides what may leave. Keeping the store
    lookup out means the rule can be measured against every licence term without a database,
    and means the three product faces share one implementation of the rule rather than three
    readings of it.

    `evidence` is filtered *down* to what the report cites -- handing this function everything
    the store held for a subject must not widen the artifact past the report, and above all
    must not publish payloads no report ever rested on. Order follows `report.evidence_ids`,
    which is part of the report's identity, so two exports of one report are byte-identical.
    """
    by_id = {item.evidence_id: item for item in evidence}
    exported: list[ExportedEvidence] = []
    missing: list[UnrecoverableEvidence] = []
    for evidence_id in report.evidence_ids:
        item = by_id.get(evidence_id)
        if item is None:
            missing.append(UnrecoverableEvidence(evidence_id=evidence_id))
            continue
        exported.append(
            ExportedEvidence(
                evidence_id=item.evidence_id,
                content_hash=item.content_hash,
                subject=item.subject,
                kind=item.kind,
                timeline=item.timeline,
                source_id=item.source_id,
                source_uri=item.source_uri,
                source_license=item.source_license,
                redistribution=item.redistribution,
                summary=item.summary,
                body=_body(item),
            )
        )
    return ReportExport(
        report=report,
        evidence=tuple(exported),
        evidence_not_recovered=tuple(missing),
    )
