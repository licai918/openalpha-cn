"""The licence gate on an exported report (`V2-P5-022`).

PRD §5.9 `S72` ("Reports preserve source and redistribution restrictions") and §5.10 `S81`
("Immutable reports exportable without restricted raw payloads") are both scoped **IN-最小**,
and Implementation Decision 27 spells the minimum out in one sentence: 不导出 Tushare 原始
payload. Its gloss, translated: Tushare is `restricted`; for your own use that is harmless,
but do not write the raw data into a report you might share.

## Where a licence actually travels, measured rather than assumed

The obstacle recorded against this row was that **no licence field reaches pages ③ or ④** --
not the factor-experiment envelope, not the prediction register, not `construction_view` --
and the same gap is written into `ShortlistDetailPanel.tsx` in as many words ("`ShortlistAnswer`
carries no redistribution field, and a licence claim this payload cannot support would be
exactly the 'mirror implying more than the contract holds' defect"). All of that is true and
none of it blocks this row, because **this row is not about those surfaces**. It is about
reports, and a report's evidence is the one place in this repository where a licence does
travel: `EvidenceSnapshot` declares `source_license: str` and
`redistribution: Literal["allowed", "restricted", "unknown"]`, written by
`evidence/builder.py` straight off `ProviderMetadata`, and all three shipped providers declare
`redistribution="restricted"` (`providers/tushare.py:3422`, `akshare.py:48`, `chainlin.py:124`).

## What is withheld, and what is not, measured on the real adapter

`providers/tushare.py:4038` builds each record as `payload=cast(JsonValue, row)` -- the
Tushare response row, verbatim. That is the "原始 payload" Decision 27 names, and it is what
this gate withholds.

The same constructor builds `summary` as
`f"Tushare {descriptor.kind} record for {subject} on {date}."` -- a template written *here*,
carrying a dataset name, a subject code and a date, and no provider field values at all. So the
summary is retained, and this module says so out loud rather than deciding it quietly: the
boundary drawn is the one Decision 27 drew, between the provider's bytes and our own sentence
about them.

## `unknown` is not permission

`redistribution` has three terms and only one of them is a licence to redistribute. `unknown`
is treated exactly as `restricted`, which is the rule `contractState.ts` already applies on the
web face (`redistribution !== "allowed"`), and the fail-closed direction: the cost of
withholding something that was actually free is a missing payload, and the cost of the other
mistake is publishing somebody's licensed data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Final

import pytest

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.time import Timeline
from openalpha_cn.product.export import export_report

RAW_MARKER: Final[str] = "TUSHARE-RAW-ROW-MARKER"
"""A string that exists **only** inside a restricted payload.

The assertion this row is for is not "the `payload` field is absent" -- it is "the provider's
bytes are not in the artifact". A marker lets that be asserted over the serialised export
rather than over one field, so a copy of the payload smuggled into a summary, a note or a
debug field fails too.
"""


def _evidence_at(
    now: datetime,
    *,
    subject: str = "600519.SH",
    kind: str = "limit_up",
    redistribution: str = "allowed",
    source_license: str = "CC0-1.0",
    source_id: str = "synthetic.a-share",
    payload: object = None,
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject=subject,
        kind=kind,
        timeline=Timeline(event_time=now, available_time=now, ingested_time=now, revision_time=now),
        source_id=source_id,
        source_uri=f"fixture://{kind}/{subject}",
        source_license=source_license,
        redistribution=redistribution,  # type: ignore[arg-type]
        summary=f"Tushare {kind} record for {subject}.",
        payload=payload if payload is not None else {"facts": {"close": 10.5}},
    )


@pytest.fixture(name="report_for")
def _report_for(frozen_now: datetime) -> Callable[..., ResearchReport]:
    def _make(*evidence_ids: str) -> ResearchReport:
        return ResearchReport(
            run_id="run_export",
            subject="600519.SH",
            created_at=frozen_now,
            title="600519.SH evidence-linked research report",
            summary="watch: bullish; confidence=0.65; evidence=1",
            decision_id="dec_export",
            signal_id="sig_export",
            final_action="watch",
            evidence_ids=evidence_ids,
            risk_flags=(),
        )

    return _make


def test_an_unrestricted_payload_survives_the_export_verbatim(
    frozen_now: datetime, report_for: Callable[..., ResearchReport]
) -> None:
    """The gate must not be a blanket. A licence that permits redistribution permits it."""
    item = _evidence_at(frozen_now, redistribution="allowed", payload={"facts": {"close": 10.5}})
    export = export_report(report=report_for(item.evidence_id), evidence=(item,))

    (exported,) = export.evidence
    assert exported.body.disposition == "included"
    assert exported.body.payload == {"facts": {"close": 10.5}}  # type: ignore[union-attr]


@pytest.mark.parametrize("term", ["restricted", "unknown"])
def test_a_payload_the_licence_does_not_release_is_nowhere_in_the_artifact(
    frozen_now: datetime, report_for: Callable[..., ResearchReport], term: str
) -> None:
    """Both non-`allowed` terms, over the serialised bytes rather than over one field.

    `unknown` is in the parametrisation and not a separate case with a softer assertion,
    because "we do not know the licence" is not a licence -- the same rule the web face already
    applies as `redistribution !== "allowed"`.
    """
    item = _evidence_at(
        frozen_now,
        redistribution=term,
        source_license="tushare-terms",
        source_id="tushare",
        payload={"ts_code": "600519.SH", "note": RAW_MARKER},
    )
    export = export_report(report=report_for(item.evidence_id), evidence=(item,))

    assert RAW_MARKER not in export.model_dump_json()
    assert RAW_MARKER not in json.dumps(export.model_dump(mode="json"), ensure_ascii=False)


@pytest.mark.parametrize("term", ["restricted", "unknown"])
def test_a_withheld_payload_is_a_named_absence_rather_than_a_silent_one(
    frozen_now: datetime, report_for: Callable[..., ResearchReport], term: str
) -> None:
    """An export that just dropped the field would be a lie of omission.

    The reader has to be able to tell "this evidence had no payload" from "this evidence's
    payload was withheld, by this licence, from this source" -- and to go and ask the source
    for it. So the record keeps the licence, the source and a reason.
    """
    item = _evidence_at(
        frozen_now, redistribution=term, source_license="tushare-terms", source_id="tushare"
    )
    export = export_report(report=report_for(item.evidence_id), evidence=(item,))

    (exported,) = export.evidence
    assert exported.body.disposition == "withheld"
    assert exported.body.redistribution == term  # type: ignore[union-attr]
    assert exported.body.source_license == "tushare-terms"  # type: ignore[union-attr]
    assert exported.source_id == "tushare"
    assert exported.summary  # our own sentence, retained; see this module's docstring
    assert exported.body.reason  # type: ignore[union-attr]


def test_the_disposition_is_a_tag_and_not_a_guess_at_a_missing_payload(
    frozen_now: datetime, report_for: Callable[..., ResearchReport]
) -> None:
    """The fixture that separates the two answers this contract could confuse.

    `JsonValue` admits `null`, so an `allowed` evidence whose payload *is* `null` and a
    `restricted` evidence whose payload was **taken away** serialise identically under any
    design that signals withholding by absence. They must not: one is a fact about the
    evidence and the other is a fact about the licence. Reading `payload is None` as "withheld"
    is the `if (view.unallocated_weight)` defect in a new place.
    """
    permitted_null = _evidence_at(
        frozen_now, kind="theme", redistribution="allowed", payload={"facts": None}
    )
    withheld = _evidence_at(
        frozen_now, kind="capital", redistribution="restricted", payload={"facts": None}
    )
    export = export_report(
        report=report_for(permitted_null.evidence_id, withheld.evidence_id),
        evidence=(permitted_null, withheld),
    )

    dispositions = {item.evidence_id: item.body.disposition for item in export.evidence}
    assert dispositions[permitted_null.evidence_id] == "included"
    assert dispositions[withheld.evidence_id] == "withheld"


def test_evidence_the_report_cites_but_the_store_cannot_produce_is_named(
    frozen_now: datetime, report_for: Callable[..., ResearchReport]
) -> None:
    """A citation that resolves to nothing is reported, not dropped.

    An export whose evidence list is simply shorter than the report's citation list reads as
    "this report rested on two items" when it rested on three and one is gone. The report is
    immutable and its `evidence_ids` are part of its identity, so the mismatch is a fact about
    this export and belongs in it.
    """
    present = _evidence_at(frozen_now)
    export = export_report(
        report=report_for(present.evidence_id, "ev_vanished"), evidence=(present,)
    )

    assert [absent.evidence_id for absent in export.evidence_not_recovered] == ["ev_vanished"]
    assert export.evidence_not_recovered[0].reason


def test_evidence_the_store_holds_but_the_report_never_cited_is_not_exported(
    frozen_now: datetime, report_for: Callable[..., ResearchReport]
) -> None:
    """The export is the report's evidence, not the subject's.

    Handing `export_report` everything the store had for this subject and getting all of it
    back would widen the artifact past the thing being exported -- and would publish payloads
    no report ever rested on.
    """
    cited = _evidence_at(frozen_now, kind="limit_up")
    uncited = _evidence_at(frozen_now, kind="theme")
    export = export_report(report=report_for(cited.evidence_id), evidence=(cited, uncited))

    assert [item.evidence_id for item in export.evidence] == [cited.evidence_id]


def test_the_counts_are_derived_from_the_records_rather_than_stated_beside_them(
    frozen_now: datetime, report_for: Callable[..., ResearchReport]
) -> None:
    """A headline a reader can act on -- "two of five payloads are withheld" -- computed.

    Stated as a field it would be a second statement of the same fact with nothing keeping the
    two equal, which is this repository's most-repeated finding.
    """
    items = (
        _evidence_at(frozen_now, kind="limit_up", redistribution="allowed"),
        _evidence_at(frozen_now, kind="theme", redistribution="restricted"),
        _evidence_at(frozen_now, kind="capital", redistribution="unknown"),
    )
    export = export_report(report=report_for(*[item.evidence_id for item in items]), evidence=items)

    assert export.included_count == 1
    assert export.withheld_count == 2
    assert export.included_count + export.withheld_count == len(export.evidence)


def test_the_export_carries_the_report_it_is_an_export_of(
    frozen_now: datetime, report_for: Callable[..., ResearchReport]
) -> None:
    """`report_id` is content-derived, so the artifact names exactly one immutable report."""
    item = _evidence_at(frozen_now)
    report = report_for(item.evidence_id)
    export = export_report(report=report, evidence=(item,))

    assert export.report == report
    assert export.report.report_id == report.report_id
    assert export.schema_version == "research-report-export/v1"
