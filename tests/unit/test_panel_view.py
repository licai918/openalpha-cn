"""The rules of the shared panel rendering, without a store (`V2-P1-016`).

`tests/integration/test_panel_interfaces.py` drives the same functions through the real HTTP
app and the SDK over a real panel. What that cannot do cheaply is exercise a *shape the
fixtures cannot produce* -- a report with no dataset in it, a clearance carrying two blocks in
different categories -- and those are exactly the shapes the totality rules here are about.
Every object below is constructed directly, which is possible because the three payload
builders are pure functions of frozen dataclasses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from openalpha_cn.api.app import PANEL_HTTP_STATUS, create_app
from openalpha_cn.panel.catalog import DatasetReadiness, ReadinessIssue
from openalpha_cn.panel_doctor import (
    HEALTH_CATEGORIES,
    HEALTH_CODE_CATEGORY,
    HEALTH_SEVERITIES,
    PanelHealthReport,
)
from openalpha_cn.panel_gate import (
    GATE_CODE_CATEGORIES,
    UNVERIFIED_DAILY_COVERAGE,
    DependencyClearance,
    DependencyRequest,
    GateBlock,
)
from openalpha_cn.panel_view import (
    PanelRequestError,
    clearance_payload,
    health_report_payload,
    panel_request,
    panel_store,
    readiness_payload,
)

AS_OF = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)


def _report() -> PanelHealthReport:
    return PanelHealthReport(
        as_of=AS_OF,
        datasets=(),
        cross_dataset_findings=(),
        cross_checks=(),
        limitations=(),
    )


def _request() -> DependencyRequest:
    return DependencyRequest(
        datasets=("adj_factor", "daily"),
        as_of=AS_OF,
        years=(2026,),
        sessions=(),
        calendar=None,
    )


def _block(code: str) -> GateBlock:
    return GateBlock(
        code=code,
        dataset="adj_factor",
        datasets=("adj_factor",),
        category=GATE_CODE_CATEGORIES[code],
        severity="blocking",
        detail=f"synthetic {code}",
    )


def _clearance(*codes: str) -> DependencyClearance:
    return DependencyClearance(
        request=_request(),
        report=_report(),
        blocks=tuple(_block(code) for code in codes),
        notices=(),
        unverified_checks=(),
        cleared_or_none=None if codes else (),
    )


def _readiness(dataset: str, *, state: str) -> DatasetReadiness:
    issues = (
        ()
        if state == "ready"
        else (ReadinessIssue(code="partition_missing", dataset=dataset, detail="synthetic"),)
    )
    return DatasetReadiness(
        dataset=dataset,
        as_of=AS_OF,
        state="ready" if state == "ready" else "blocked",
        issues=issues,
        years_present=(2026,),
        row_count=1,
        subject_count=1,
        last_event_time=AS_OF,
        last_event_date=date(2026, 1, 16),
        checks_waived=(),
    )


def test_the_severity_census_is_total_over_the_declared_severities() -> None:
    """A severity with no findings must read `0`, not be missing. Otherwise "no blocking
    findings" and "the blocking key was never emitted" are the same observation for a consumer,
    which is the whole reason this count is built from the table rather than from the findings
    that happen to be present."""
    payload = health_report_payload(_report())

    assert payload["counts_by_severity"] == dict.fromkeys(sorted(HEALTH_SEVERITIES), 0)
    assert set(payload["counts_by_severity"]) == HEALTH_SEVERITIES  # type: ignore[arg-type]
    assert payload["is_clean"] is True
    assert payload["findings"] == []


def test_the_block_census_is_total_over_the_declared_categories() -> None:
    payload = clearance_payload(_clearance(UNVERIFIED_DAILY_COVERAGE))

    assert set(payload["blocks_by_category"]) == HEALTH_CATEGORIES  # type: ignore[arg-type]
    assert payload["blocks_by_category"] == {
        "missing": [],
        "stale": [],
        "duplicate": [],
        "revised": [],
        "inconsistent": [],
        "unanswerable": [UNVERIFIED_DAILY_COVERAGE],
    }


def test_the_gates_own_refusal_has_no_entry_in_the_health_categories_table() -> None:
    """The reason `blocks_by_category` groups off `GateBlock.category` and not off a lookup in
    `HEALTH_CODE_CATEGORY`: `unverified_daily_coverage` is not a health code, because it is not
    a fault of the panel. A grouping that looked it up there would raise; one that skipped
    unmapped codes would silently drop the only block on the response."""
    assert UNVERIFIED_DAILY_COVERAGE not in HEALTH_CODE_CATEGORY
    assert GATE_CODE_CATEGORIES[UNVERIFIED_DAILY_COVERAGE] == "unanswerable"

    payload = clearance_payload(_clearance(UNVERIFIED_DAILY_COVERAGE, "partition_missing"))

    assert payload["blocks_by_category"]["unanswerable"] == [UNVERIFIED_DAILY_COVERAGE]  # type: ignore[index]
    assert payload["blocks_by_category"]["missing"] == ["partition_missing"]  # type: ignore[index]


def test_a_cleared_clearance_renders_an_empty_list_and_not_a_null() -> None:
    """`cleared: null` means refused and `cleared: []` means cleared over nothing. Collapsing
    the two is the merge `DependencyClearance` refuses `bool()` and `len()` to prevent, and a
    payload that emitted `null` for both would reintroduce it on the wire."""
    assert clearance_payload(_clearance())["cleared"] == []
    assert clearance_payload(_clearance("partition_missing"))["cleared"] is None
    assert clearance_payload(_clearance())["is_blocked"] is False
    assert clearance_payload(_clearance("partition_missing"))["is_blocked"] is True


def test_a_readiness_report_over_no_dataset_is_refused_rather_than_rendered() -> None:
    """`all_ready` over no dataset is vacuously `True`. That payload is the empty success in
    its purest form, so the rendering refuses it as well as the request resolver."""
    with pytest.raises(PanelRequestError, match="over no dataset at all is not a verdict"):
        readiness_payload(())


def test_the_readiness_payload_names_every_blocked_dataset_in_request_order() -> None:
    entries = (
        _readiness("adj_factor", state="ready"),
        _readiness("daily", state="blocked"),
        _readiness("daily_basic", state="blocked"),
    )

    payload = readiness_payload(entries)

    assert payload["all_ready"] is False
    assert payload["blocked_datasets"] == ["daily", "daily_basic"]
    assert [entry["dataset"] for entry in payload["datasets"]] == [  # type: ignore[attr-defined]
        "adj_factor",
        "daily",
        "daily_basic",
    ]
    assert readiness_payload(entries[:1])["all_ready"] is True


def test_a_naive_as_of_is_refused_before_the_store_is_touched(tmp_path: Path) -> None:
    with pytest.raises(PanelRequestError, match="timezone-aware"):
        panel_request(
            panel_store(tmp_path),
            datasets=("daily",),
            years=(2026,),
            sessions=(),
            index_codes=(),
            as_of=datetime(2026, 1, 17, 4, 0),
            exchange="SZSE",
            with_calendar=True,
        )


def test_a_request_naming_no_dataset_is_refused_before_any_verdict(tmp_path: Path) -> None:
    with pytest.raises(PanelRequestError, match="named no dataset at all"):
        panel_request(
            panel_store(tmp_path),
            datasets=(),
            years=(2026,),
            sessions=(),
            index_codes=(),
            as_of=AS_OF,
            exchange="SZSE",
            with_calendar=False,
        )


def test_the_resolved_request_de_duplicates_every_repeated_parameter(tmp_path: Path) -> None:
    """Repeated query parameters are how a list arrives over HTTP and how `--dataset` arrives
    on the command line, and a name given twice must not make the panel be assessed twice."""
    request = panel_request(
        panel_store(tmp_path),
        datasets=("daily", "adj_factor", "daily"),
        years=(2026, 2026, 2025),
        sessions=(date(2026, 1, 16), date(2026, 1, 16)),
        index_codes=("000300.SH", "000300.SH"),
        as_of=AS_OF,
        exchange="SZSE",
        with_calendar=False,
    )

    assert request.datasets == ("daily", "adj_factor")
    assert request.years == (2026, 2025)
    assert request.sessions == (date(2026, 1, 16),)
    assert request.index_codes == ("000300.SH",)
    assert request.calendar is None


def test_the_panel_store_sits_beside_the_rest_of_the_runtime_directory(tmp_path: Path) -> None:
    assert panel_store(tmp_path).root == tmp_path / "panel"


def test_the_http_status_table_keeps_a_refusal_out_of_the_success_class() -> None:
    """The table, entry by entry. `blocked` must not be 2xx -- an endpoint that ran the gate,
    was refused and still answered `200` would be no gate at all -- and it must not be the same
    code as `bad_request`, because "the panel is not in a state you may read" and "your request
    cannot be put at all" have different remedies."""
    assert PANEL_HTTP_STATUS == {
        "answered": 200,
        "blocked": 409,
        "panel_unreadable": 409,
        "bad_request": 422,
    }
    assert not 200 <= PANEL_HTTP_STATUS["blocked"] < 300
    assert PANEL_HTTP_STATUS["blocked"] != PANEL_HTTP_STATUS["bad_request"]


def test_building_the_app_does_not_create_a_panel_directory(tmp_path: Path) -> None:
    """`PanelStore.__init__` makes its own directory, so each face builds one per call rather
    than at construction time. `api/app.py` runs `app = create_app()` at module scope, so a
    store built there would create `./runtime/panel` in whatever directory the process happened
    to start in -- merely importing the app must not lay down a panel plane."""
    create_app(runtime_dir=tmp_path)

    assert not (tmp_path / "panel").exists()
    assert panel_store(tmp_path).root.is_dir()
