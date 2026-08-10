"""REST + SDK equivalence for the panel plane's three read-side answers (`V2-P1-016`).

The roadmap's acceptance is one phrase -- "REST + SDK equivalence" -- and the shape is
`test_portfolio_interfaces.py`'s and `test_validation_interfaces.py`': the *same input* is
driven once through `OpenAlphaSDK` and once through the real HTTP app, and the two answers
have to be the same answer. Every HTTP test here goes through `TestClient` against
`create_app()`; nothing calls an endpoint body as a plain function, because a status code is
not observable from inside the function that raises it.

## "The same answer" is not "the same shape"

Two of these three endpoints answer *different questions about the same panel*, and they are
allowed to disagree. `GET /api/v1/panel/health` asks "is this panel sick"; `GET
/api/v1/panel/gate` asks "may **this request** read it". The gate has a refusal of its own,
`unverified_daily_coverage`, which is not a health code at all -- so a panel with nothing
wrong with it refuses a request that named no session, and both answers are right
(`test_the_health_endpoint_and_the_gate_disagree_about_one_panel_and_both_are_right`, the
HTTP twin of `test_cli_panel.py`'s CLI one). The equivalence asserted here is always
REST-against-SDK for the *same* endpoint, never one endpoint against another.

## Nothing here touches the network

The panel read side never constructs a provider: it reads a `PanelStore` and nothing else.
The panels come from `tests/panel_fixtures.py`, whose `write_generated_panel` drives the real
`panel_ingest` writers, so every write-time guard has run over everything asserted here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import panel_fixtures
import pytest
from fastapi.testclient import TestClient
from panel_fixtures import AS_OF, EXCHANGE, YEAR, generate_panel, write_generated_panel

from openalpha_cn.api.app import PANEL_HTTP_STATUS, create_app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import (
    HEALTH_CATEGORIES,
    HEALTH_CODE_CATEGORY,
    PANEL_HEALTH_CODES,
)
from openalpha_cn.panel_gate import UNVERIFIED_DAILY_COVERAGE, PanelGateError
from openalpha_cn.panel_view import (
    PanelRequestError,
    clearance_payload,
    health_report_payload,
    readiness_payload,
)
from openalpha_cn.sdk import OpenAlphaSDK

SECRET_TOKEN = "sk-panel-http-token-must-not-leak-77241"
"""Deliberately distinct from `test_cli_panel.py`'s and `tests/unit/test_cli.py`'s, so a leak
assertion here cannot pass because some other test happened to scrub one of those."""

FIXTURE_DATASETS: tuple[str, ...] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    SUSPENSION_DATASET,
)
"""The six datasets `panel_health_report` needs in scope for all three of its session-scoped
cross-checks to run; `panel_gate.SESSION_SCOPED_CROSS_CHECKS` is specified against them."""

FIXTURE_SESSION: date = generate_panel().sessions[-1]
"""The newest session the generated panel carries, read off the generator rather than restated
-- `test_cli_panel.py`'s rule, for its reason: a literal here would agree with the fixture's
window only until someone edits the generator."""


# --- the two faces, over one panel ------------------------------------------------------------


def seed_panel(runtime_dir: Path, *shapes: str) -> PanelStore:
    """Write a `tests/panel_fixtures.py` panel where both faces read it.

    One directory, not two: the panel *is* the input to every question asked here, so the SDK
    and the app have to be looking at the same one. (`test_portfolio_interfaces.py` can afford
    two, because a portfolio transition is computed from its request alone.)
    """
    store = PanelStore(runtime_dir / "panel")
    write_generated_panel(store, generate_panel(shapes=shapes))
    return store


def faces(runtime_dir: Path) -> tuple[OpenAlphaSDK, TestClient]:
    return OpenAlphaSDK(runtime_dir=runtime_dir), TestClient(create_app(runtime_dir=runtime_dir))


def query(
    *,
    datasets: Sequence[str] = FIXTURE_DATASETS,
    years: Sequence[int] = (YEAR,),
    sessions: Sequence[date] = (FIXTURE_SESSION,),
    as_of: datetime = AS_OF,
    exchange: str = EXCHANGE,
    calendar: bool = True,
    index_codes: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "dataset": list(datasets),
        "year": [str(year) for year in years],
        "session": [day.isoformat() for day in sessions],
        "as_of": as_of.isoformat(),
        "exchange": exchange,
        "calendar": calendar,
        "index_code": list(index_codes),
    }


def sdk_arguments(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """The same query, as the SDK's own keywords.

    Derived from the HTTP parameters rather than restated, so an equivalence test cannot pass
    by asking the two faces two different questions.
    """
    return {
        "datasets": tuple(parameters["dataset"]),
        "years": tuple(int(year) for year in parameters["year"]),
        "sessions": tuple(date.fromisoformat(day) for day in parameters["session"]),
        "as_of": datetime.fromisoformat(parameters["as_of"]),
        "exchange": parameters["exchange"],
        "with_calendar": parameters["calendar"],
        "index_codes": tuple(parameters["index_code"]),
    }


def without(parameters: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {name: value for name, value in parameters.items() if name not in keys}


# --- equivalence: the same input, the same answer ----------------------------------------------


def test_sdk_and_rest_report_the_same_panel_health(tmp_path: Path) -> None:
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query()

    report = sdk.panel_health(**sdk_arguments(parameters))
    response = client.get("/api/v1/panel/health", params=parameters)

    assert response.status_code == 200
    assert response.json() == health_report_payload(report)
    # ... and the answer itself, not only the rendering of it.
    assert report.is_clean is True
    assert response.json()["is_clean"] is True
    assert response.json()["counts_by_severity"] == {"blocking": 0, "warning": 0, "notice": 0}
    assert [entry["dataset"] for entry in response.json()["datasets"]] == list(FIXTURE_DATASETS)
    assert {check["name"] for check in response.json()["cross_checks"] if check["ran"]} >= {
        "close_agreement",
        "unpriced_explained",
        "return_paths",
    }


def test_sdk_and_rest_grant_the_same_clearance_and_state_the_same_width(tmp_path: Path) -> None:
    """A clearance is not a list of names, and neither face may flatten it into one: `cleared`
    hands back `ClearedDataset` records carrying the years, the sessions a cross-check actually
    opened and the caveats still open outside them. A bare name is exactly as wide as its
    reader assumes, which is how `V2-P1-013`'s review found Task 29's wrong number reachable
    through a *cleared* gate."""
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query()

    clearance = sdk.panel_clearance(**sdk_arguments(parameters))
    response = client.get("/api/v1/panel/gate", params=parameters)

    assert response.status_code == 200
    assert response.json() == clearance_payload(clearance)
    assert clearance.is_blocked is False
    assert response.json()["is_blocked"] is False
    assert response.json()["blocks"] == []
    cleared = {entry["dataset"]: entry for entry in response.json()["cleared"]}
    assert set(cleared) == set(FIXTURE_DATASETS)
    assert cleared[ADJ_FACTOR_DATASET]["years"] == [YEAR]
    assert cleared[ADJ_FACTOR_DATASET]["corroborated_sessions"] == [FIXTURE_SESSION.isoformat()]
    assert cleared[ADJ_FACTOR_DATASET]["caveats"] == [UNVERIFIED_DAILY_COVERAGE]
    assert cleared[DAILY_DATASET]["caveats"] == []


def test_sdk_and_rest_refuse_the_same_request_and_the_refusal_is_not_a_success(
    tmp_path: Path,
) -> None:
    """The fail-closed gate's whole point, over HTTP. `adj_factor` publishes daily and its
    requirement waives `required_dates`, so with no session named nothing in the report can see
    a hole in it -- and an endpoint that ran the gate, was refused, and still answered `200`
    would be no gate at all."""
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query(sessions=())

    clearance = sdk.panel_clearance(**sdk_arguments(parameters))
    response = client.get("/api/v1/panel/gate", params=parameters)

    assert response.status_code == PANEL_HTTP_STATUS["blocked"] == 409
    assert response.json() == clearance_payload(clearance)
    assert clearance.is_blocked is True
    assert response.json()["is_blocked"] is True
    # The refusal carries its reasons rather than a bare status line: a caller told "409" and
    # nothing else cannot act on it.
    assert response.json()["cleared"] is None
    assert [block["code"] for block in response.json()["blocks"]] == [UNVERIFIED_DAILY_COVERAGE]
    assert response.json()["blocked_datasets"] == [ADJ_FACTOR_DATASET]
    assert clearance.blocking_codes() == frozenset({UNVERIFIED_DAILY_COVERAGE})


def test_sdk_and_rest_report_the_same_dataset_readiness(tmp_path: Path) -> None:
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = without(query(), "session")

    readiness = sdk.panel_readiness(**without(sdk_arguments(query(sessions=())), "sessions"))
    response = client.get("/api/v1/panel/readiness", params=parameters)

    assert response.status_code == 200
    assert response.json() == readiness_payload(readiness)
    assert response.json()["all_ready"] is True
    assert response.json()["blocked_datasets"] == []
    assert [entry["dataset"] for entry in response.json()["datasets"]] == list(FIXTURE_DATASETS)
    assert [entry.state for entry in readiness] == ["ready"] * len(FIXTURE_DATASETS)
    # The whole waiver census, as a table. "ready with no issues" means nothing on its own --
    # `V2-P1-006`'s Critical was a partition that was `ready` with `issues == []` while
    # answering -0.530973% against a true +2.742251% -- so the questions that were never put
    # have to be on the response, and this pins which ones they are on a healthy panel rather
    # than that the key exists. `adj_factor` is the one that matters: `daily` cadence with
    # `required_dates` waived is exactly the shape the gate refuses uncorroborated.
    assert {entry["dataset"]: entry["checks_waived"] for entry in response.json()["datasets"]} == {
        TRADING_CALENDAR_DATASET: ["required_dates", "max_staleness"],
        STOCK_BASIC_DATASET: ["required_dates", "required_subjects", "max_staleness"],
        ADJ_FACTOR_DATASET: ["required_dates", "required_subjects"],
        DAILY_DATASET: ["required_subjects"],
        DAILY_BASIC_DATASET: ["required_subjects"],
        SUSPENSION_DATASET: ["required_dates", "required_subjects", "max_staleness"],
    }


def test_sdk_and_rest_report_the_same_unreadable_panel(tmp_path: Path) -> None:
    """A real hole in the panel, not a fact about the request: the catalog still registers the
    `daily` partition and its Parquet file is gone.

    None of `tests/panel_fixtures.py`'s nineteen shapes can produce a `blocking` or a `warning`
    finding -- every one of them writes a panel the real `panel_ingest` writers accept, which
    is what makes them fixtures -- so a defect of the *panel* has to be dug by hand. This is
    the smallest one that is unambiguously the panel's fault and not the request's.
    """
    store = seed_panel(tmp_path)
    next((store.root / DAILY_DATASET / str(YEAR)).glob("*.parquet")).unlink()
    sdk, client = faces(tmp_path)
    parameters = query()

    report = sdk.panel_health(**sdk_arguments(parameters))
    clearance = sdk.panel_clearance(**sdk_arguments(parameters))
    health = client.get("/api/v1/panel/health", params=parameters)
    gate = client.get("/api/v1/panel/gate", params=parameters)

    assert health.status_code == 200
    assert health.json() == health_report_payload(report)
    assert health.json()["is_clean"] is False
    assert "partition_file_missing" in {finding["code"] for finding in health.json()["findings"]}
    assert gate.status_code == 409
    assert gate.json() == clearance_payload(clearance)
    assert "partition_file_missing" in {block["code"] for block in gate.json()["blocks"]}
    assert DAILY_DATASET in gate.json()["blocked_datasets"]


# --- the status-code table ---------------------------------------------------------------------


def test_the_http_status_of_every_panel_situation_is_the_declared_table(tmp_path: Path) -> None:
    """The whole table, asserted as a table.

    Three codes and each one says something different. `200` is "this face answered"; for the
    gate it also says "you may read", which is why a refusal must not wear it. `409` is "the
    panel's state stands in the way" -- the gate refused, or the calendar this request asked to
    be judged against is not stored. `422` is "the request cannot be put at all", which no
    amount of re-fetching fixes; it is the CLI's `bad_request` under the code this app already
    uses for a well-formed request it cannot accept.

    A `notice` is deliberately absent from the non-2xx half. `V2-P1-011` measured
    `ambiguous_filing` firing on 8.15% of `income`'s real filings, 1.29% of `balancesheet`'s,
    15.80% of `cashflow`'s and 13.70% of `fina_indicator`'s; a face that answered non-2xx on
    those would fail on every honest financial panel and be switched off.
    """
    seed_panel(tmp_path, "financials.same_day_duplicate_versions")
    _, client = faces(tmp_path)
    clean = query()
    naive = query() | {"as_of": AS_OF.replace(tzinfo=None).isoformat()}

    situations: dict[tuple[str, str], int] = {
        ("health", "a clean panel"): client.get("/api/v1/panel/health", params=clean).status_code,
        ("health", "a notice-only panel"): client.get(
            "/api/v1/panel/health",
            params=query(datasets=("income",), sessions=(), calendar=False),
        ).status_code,
        ("health", "a request the report cannot put"): client.get(
            "/api/v1/panel/health", params=query(datasets=("not_a_dataset",), sessions=())
        ).status_code,
        ("readiness", "a clean panel"): client.get(
            "/api/v1/panel/readiness", params=without(clean, "session")
        ).status_code,
        ("readiness", "a request the report cannot put"): client.get(
            "/api/v1/panel/readiness",
            params=without(query(datasets=("not_a_dataset",)), "session"),
        ).status_code,
        ("gate", "a cleared request"): client.get("/api/v1/panel/gate", params=clean).status_code,
        ("gate", "a notice-only panel"): client.get(
            "/api/v1/panel/gate",
            params=query(datasets=("income",), sessions=(), calendar=False),
        ).status_code,
        ("gate", "a blocked request"): client.get(
            "/api/v1/panel/gate", params=query(sessions=())
        ).status_code,
        ("gate", "a request the gate cannot put"): client.get(
            "/api/v1/panel/gate", params=without(query(), "dataset")
        ).status_code,
        ("gate", "a calendar that is not stored"): client.get(
            "/api/v1/panel/gate", params=query(exchange="SSE")
        ).status_code,
        ("gate", "a naive as_of"): client.get("/api/v1/panel/gate", params=naive).status_code,
    }

    assert situations == {
        ("health", "a clean panel"): 200,
        ("health", "a notice-only panel"): 200,
        ("health", "a request the report cannot put"): 422,
        ("readiness", "a clean panel"): 200,
        ("readiness", "a request the report cannot put"): 422,
        ("gate", "a cleared request"): 200,
        ("gate", "a notice-only panel"): 200,
        ("gate", "a blocked request"): 409,
        ("gate", "a request the gate cannot put"): 422,
        ("gate", "a calendar that is not stored"): 409,
        ("gate", "a naive as_of"): 422,
    }


def test_a_notice_only_panel_clears_with_a_success_and_still_carries_its_notices(
    tmp_path: Path,
) -> None:
    """A cleared verdict is a verdict, not silence: the notices ride on the clearance."""
    seed_panel(tmp_path, "financials.same_day_duplicate_versions")
    sdk, client = faces(tmp_path)
    parameters = query(datasets=("income",), sessions=(), calendar=False)

    clearance = sdk.panel_clearance(**sdk_arguments(parameters))
    response = client.get("/api/v1/panel/gate", params=parameters)

    assert response.status_code == 200
    assert response.json() == clearance_payload(clearance)
    assert response.json()["is_blocked"] is False
    codes = {notice["code"] for notice in response.json()["notices"]}
    assert codes
    assert codes <= {"ambiguous_filing", "duplicate_versions", "revised_rows"}
    assert {notice["severity"] for notice in response.json()["notices"]} == {"notice"}


def test_the_health_endpoint_and_the_gate_disagree_about_one_panel_and_both_are_right(
    tmp_path: Path,
) -> None:
    """`/panel/health` answers "is this panel sick"; `/panel/gate` answers "may this request
    read it". The gate's own refusal, `unverified_daily_coverage`, is not a health code, so a
    panel with nothing wrong with it still refuses a request that named no session. Pinned
    together because the temptation is to make one endpoint's status code the other's."""
    seed_panel(tmp_path)
    _, client = faces(tmp_path)
    parameters = query(sessions=())

    health = client.get("/api/v1/panel/health", params=parameters)
    gate = client.get("/api/v1/panel/gate", params=parameters)

    assert health.status_code == 200
    assert health.json()["is_clean"] is True
    assert health.json()["findings"] == []
    assert gate.status_code == 409
    assert gate.json()["is_blocked"] is True
    # The one code they differ over is the gate's own, and the health report cannot even emit
    # it: `PANEL_HEALTH_CODES` does not contain it, so `findings_with_code` refuses the question.
    assert [block["code"] for block in gate.json()["blocks"]] == [UNVERIFIED_DAILY_COVERAGE]
    assert UNVERIFIED_DAILY_COVERAGE not in PANEL_HEALTH_CODES


def test_the_gate_files_its_own_refusal_under_a_heading_the_health_table_has_no_entry_for(
    tmp_path: Path,
) -> None:
    """`unverified_daily_coverage` is not in `HEALTH_CODE_CATEGORY` -- it is not a health code
    at all -- so a facet that groups blocks by category has to read the *gate's* table, which
    is total over both halves. Grouping through `HEALTH_CODE_CATEGORY` would raise a `KeyError`
    on exactly the code this gate exists to issue, and dropping the unmapped code would hide
    the only block on the response."""
    seed_panel(tmp_path)
    _, client = faces(tmp_path)

    response = client.get("/api/v1/panel/gate", params=query(sessions=()))

    assert UNVERIFIED_DAILY_COVERAGE not in HEALTH_CODE_CATEGORY
    grouped = response.json()["blocks_by_category"]
    assert set(grouped) == set(HEALTH_CATEGORIES)
    assert grouped["unanswerable"] == [UNVERIFIED_DAILY_COVERAGE]
    assert sum(len(codes) for codes in grouped.values()) == len(response.json()["blocks"])


# --- the three dunders -------------------------------------------------------------------------


@pytest.mark.parametrize("sessions", [(FIXTURE_SESSION,), ()])
def test_neither_face_consumes_a_clearance_as_a_collection(
    tmp_path: Path, sessions: tuple[date, ...]
) -> None:
    """`DependencyClearance.__bool__`, `__len__` and `__iter__` all raise -- **including on a
    clearance that cleared**, which is Task 36's deliberate choice: an accessor that answered
    on a healthy panel and raised on a sick one would pass every test written against the first
    and fail only in production.

    So the trap is asserted live here, and then both faces are driven over the same clearance.
    Any `if clearance:`, `len(clearance)` or `for x in clearance` on either path -- including
    inside a serializer that probed the object for a length -- turns into a `PanelGateError`,
    which is a `500` from the app and an exception from the SDK. Both parametrisations run
    because the cleared one is the case a naive implementation passes.
    """
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query(sessions=sessions)

    clearance = sdk.panel_clearance(**sdk_arguments(parameters))

    with pytest.raises(PanelGateError, match="a clearance is a verdict, not a collection"):
        bool(clearance)
    with pytest.raises(PanelGateError, match="a clearance is a verdict, not a collection"):
        len(clearance)  # type: ignore[arg-type]
    with pytest.raises(PanelGateError, match="a clearance is a verdict, not a collection"):
        list(clearance)  # type: ignore[call-overload]

    response = client.get("/api/v1/panel/gate", params=parameters)
    assert response.status_code in {200, 409}
    assert response.json() == clearance_payload(clearance)


def test_the_blocked_clearance_still_refuses_its_own_cleared_accessor(tmp_path: Path) -> None:
    """`cleared` raises when blocked and `cleared_or_none` is the merged shape under a name
    that says what it is. Both faces read the second and never the first, which is why a `409`
    has a body at all."""
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query(sessions=())

    clearance = sdk.panel_clearance(**sdk_arguments(parameters))

    with pytest.raises(PanelGateError, match=r"this request is blocked by \['unverified"):
        _ = clearance.cleared
    assert clearance.cleared_or_none is None
    assert client.get("/api/v1/panel/gate", params=parameters).json()["cleared"] is None


# --- request faults, and the calendar ----------------------------------------------------------


def test_a_request_naming_no_dataset_is_a_bad_request_and_not_a_refusal(tmp_path: Path) -> None:
    """A check that inspected nothing must not report a verdict, on either face.

    `require_datasets` already refuses this and `panel_health_report` does not -- a health
    request naming nothing comes back `is_clean=True` over zero datasets, which is the empty
    success in its purest form -- so the refusal lives in the shared request resolver and
    covers all three endpoints. Over HTTP the parameter is required, so the app's own
    validation answers first; through the SDK the resolver is what stands there.
    """
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)

    response = client.get("/api/v1/panel/gate", params=without(query(), "dataset"))

    assert response.status_code == 422
    assert "dataset" in json.dumps(response.json())
    with pytest.raises(PanelRequestError, match="named no dataset at all"):
        sdk.panel_health(**sdk_arguments(query()) | {"datasets": ()})


def test_a_dataset_with_no_declared_cadence_is_a_bad_request_on_all_three_endpoints(
    tmp_path: Path,
) -> None:
    seed_panel(tmp_path)
    _, client = faces(tmp_path)
    parameters = query(datasets=("not_a_dataset",), sessions=())

    for path, sent in (
        ("/api/v1/panel/readiness", without(parameters, "session")),
        ("/api/v1/panel/health", parameters),
        ("/api/v1/panel/gate", parameters),
    ):
        response = client.get(path, params=sent)
        assert response.status_code == 422, path
        assert "not_a_dataset" in json.dumps(response.json()), path


def test_a_naive_as_of_is_refused_rather_than_localised(tmp_path: Path) -> None:
    """The same refusal the CLI makes. A naive instant reaching `PanelStore` raises out of the
    middle of a rule table; refused here it names the field."""
    seed_panel(tmp_path)
    _, client = faces(tmp_path)

    naive = query() | {"as_of": AS_OF.replace(tzinfo=None).isoformat()}

    response = client.get("/api/v1/panel/health", params=naive)

    assert response.status_code == 422
    assert "timezone-aware" in json.dumps(response.json())
    # The same instant, stated with its offset, is answered -- so the refusal is about the
    # missing zone and not about the value.
    assert client.get("/api/v1/panel/health", params=query()).status_code == 200


def test_a_calendar_that_is_not_stored_names_both_ways_out(tmp_path: Path) -> None:
    """The fixture stores the SZSE calendar. Asking for SSE's is a request this panel cannot
    answer, and the message has to name the two ways out the CLI names -- build it, or state on
    the record that this run has no calendar."""
    seed_panel(tmp_path)
    _, client = faces(tmp_path)

    response = client.get("/api/v1/panel/gate", params=query(exchange="SSE"))

    assert response.status_code == 409
    detail = json.dumps(response.json())
    assert "SSE" in detail
    assert "calendar=false" in detail


def test_a_run_with_no_calendar_is_stated_on_the_record_and_changes_the_verdict(
    tmp_path: Path,
) -> None:
    """`calendar` has no default on either face. `DependencyRequest` makes every field that
    decides how hard the panel is examined mandatory for this reason: `calendar=None` is a
    legitimate answer and has to be one somebody gave, because it switches off every
    session-scoped cross-check and the gate then refuses a daily-cadence dataset nothing
    corroborated."""
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query(calendar=False)

    clearance = sdk.panel_clearance(**sdk_arguments(parameters))
    response = client.get("/api/v1/panel/gate", params=parameters)

    assert response.status_code == 409
    assert response.json() == clearance_payload(clearance)
    assert UNVERIFIED_DAILY_COVERAGE in {block["code"] for block in response.json()["blocks"]}
    assert {check["name"] for check in response.json()["report"]["cross_checks"] if check["ran"]}


@pytest.mark.parametrize(
    "path", ["/api/v1/panel/readiness", "/api/v1/panel/health", "/api/v1/panel/gate"]
)
def test_omitting_the_calendar_decision_is_refused_rather_than_defaulted(
    tmp_path: Path, path: str
) -> None:
    seed_panel(tmp_path)
    _, client = faces(tmp_path)

    response = client.get(path, params=without(query(), "calendar"))

    assert response.status_code == 422
    assert "calendar" in json.dumps(response.json())


def test_the_sdk_has_no_default_for_the_calendar_decision_either(tmp_path: Path) -> None:
    """The same refusal one face over. If `with_calendar` carried a default, the most
    permissive request would also be the easiest one to build -- and `with_calendar=True` on a
    runtime directory with no stored calendar refuses outright, so the default would not even
    be the permissive one consistently. `exchange` is mandatory for the same reason: a request
    judged against SSE's calendar and one judged against SZSE's are different questions."""
    seed_panel(tmp_path)
    sdk, _ = faces(tmp_path)
    stated = sdk_arguments(query())

    for method in (sdk.panel_health, sdk.panel_clearance):
        with pytest.raises(TypeError, match="with_calendar"):
            method(**without(stated, "with_calendar"))  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="exchange"):
            method(**without(stated, "exchange"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="with_calendar"):
        sdk.panel_readiness(**without(stated, "with_calendar", "sessions"))  # type: ignore[arg-type]


# --- what the panel plane does not record ------------------------------------------------------


def test_a_panel_built_with_the_halt_guard_waived_is_indistinguishable_from_one_that_was_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open residue `V2-P1-015` left, pinned rather than described.

    `write_daily_panel(halts=None)` switches off `_refuse_unexplained_thin_sessions`, the
    strongest guard that writer has, and **stores no trace of having done so**: `halts` feeds
    that guard and nothing else, so the two builds produce the same partitions and the same
    catalog records. Nothing this issue's read faces can look at distinguishes them, and a
    field on this response claiming otherwise would be invented. So the gap is asserted here as
    an equality: if a later issue records the waiver at write time, this test fails and says
    where to update it.

    The equality is only evidence if the two builds really differed, and an equality cannot
    show that -- a generator that ignored `halts=False` would pass this test with both stores
    written the same way. So the argument the writer actually received is observed as well:
    `None` for the waived build, and a non-empty corpus for the guarded one.
    """
    handed: list[object] = []
    real_writer = panel_fixtures.write_daily_panel

    def observed_writer(store: PanelStore, **arguments: Any) -> Any:
        handed.append(arguments["halts"])
        return real_writer(store, **arguments)

    monkeypatch.setattr(panel_fixtures, "write_daily_panel", observed_writer)

    guarded, waived = tmp_path / "guarded", tmp_path / "waived"
    seed_panel(guarded)
    write_generated_panel(PanelStore(waived / "panel"), generate_panel(), halts=False)

    guarded_corpus, waived_corpus = handed
    assert waived_corpus is None
    assert guarded_corpus is not None
    assert len(guarded_corpus) > 0  # type: ignore[arg-type]

    _, guarded_client = faces(guarded)
    _, waived_client = faces(waived)
    parameters = query()

    for path, sent in (
        ("/api/v1/panel/readiness", without(parameters, "session")),
        ("/api/v1/panel/health", parameters),
        ("/api/v1/panel/gate", parameters),
    ):
        with_guard = guarded_client.get(path, params=sent)
        without_guard = waived_client.get(path, params=sent)
        assert with_guard.status_code == without_guard.status_code, path
        assert with_guard.json() == without_guard.json(), path


def test_no_response_and_no_log_line_carries_a_provider_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The panel read side never constructs a provider, and this pins that it stays that way:
    a face that reached for one to fill a field would put the credential inside this process
    and one exception message away from a response body."""
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET_TOKEN)
    seed_panel(tmp_path)
    _, client = faces(tmp_path)
    parameters = query()

    with caplog.at_level(logging.DEBUG):
        bodies = [
            client.get("/api/v1/panel/readiness", params=without(parameters, "session")).text,
            client.get("/api/v1/panel/health", params=parameters).text,
            client.get("/api/v1/panel/gate", params=parameters).text,
            client.get("/api/v1/panel/gate", params=query(sessions=())).text,
            client.get("/api/v1/panel/gate", params=query(exchange="SSE")).text,
        ]

    for body in bodies:
        assert SECRET_TOKEN not in body
    assert SECRET_TOKEN not in caplog.text


# --- the SDK's own shape -----------------------------------------------------------------------


def test_the_sdk_hands_back_the_objects_rather_than_their_rendering(tmp_path: Path) -> None:
    """The SDK is a Python API, so it returns `PanelHealthReport`, `DependencyClearance` and
    `DatasetReadiness` themselves -- every accessor those carry (`findings_with_code`,
    `blocks_for`, `unverified`, `cleared_for`) is the point of using it instead of HTTP. The
    JSON in this file's other tests is what `panel_view` makes of them, not what the SDK is."""
    seed_panel(tmp_path)
    sdk, _ = faces(tmp_path)
    arguments = sdk_arguments(query())

    report = sdk.panel_health(**arguments)
    clearance = sdk.panel_clearance(**arguments)
    readiness = sdk.panel_readiness(**without(arguments, "sessions"))

    assert report.dataset(ADJ_FACTOR_DATASET).is_ready is True
    assert report.findings_with_code("date_gap") == ()
    assert clearance.cleared_for(ADJ_FACTOR_DATASET).corroborates(FIXTURE_SESSION) is True
    assert clearance.cleared_for(ADJ_FACTOR_DATASET).corroborates(date(2026, 1, 5)) is False
    assert clearance.unverified(ADJ_FACTOR_DATASET) == ("required_dates", "required_subjects")
    assert clearance.cleared_with_caveat(UNVERIFIED_DAILY_COVERAGE) == (
        clearance.cleared_for(ADJ_FACTOR_DATASET),
    )
    assert [entry.dataset for entry in readiness] == list(FIXTURE_DATASETS)
    # The refusals JSON cannot carry: a question about a dataset this request never named, and
    # one about a code this gate cannot issue, are both errors rather than an empty answer.
    with pytest.raises(PanelGateError, match="was not one of the datasets this request named"):
        clearance.blocks_for("income")
    with pytest.raises(PanelGateError, match="is not one of the codes this gate can issue"):
        clearance.blocks_with_code("parition_missing")


def test_the_sdk_reads_the_same_panel_directory_the_cli_and_the_app_do(tmp_path: Path) -> None:
    """One `runtime_dir` names one panel, at `runtime_dir/panel`. Three faces disagreeing about
    where the store lives would make every equivalence in this file a coincidence."""
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    empty = OpenAlphaSDK(runtime_dir=tmp_path / "elsewhere")
    parameters = query()

    assert sdk.panel_health(**sdk_arguments(parameters)).is_clean is True
    assert client.get("/api/v1/panel/health", params=parameters).json()["is_clean"] is True
    # A second SDK pointed at a directory with no panel under it sees an empty panel, not this
    # one. `calendar=False` because there is no stored calendar there either, and that refusal
    # would arrive before any report could be produced.
    elsewhere = empty.panel_health(**sdk_arguments(query(calendar=False)))
    assert elsewhere.is_clean is False
    assert elsewhere.blocked_datasets == FIXTURE_DATASETS


def _codes(findings: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(finding["code"]) for finding in findings}


def test_a_year_the_panel_never_held_is_reported_by_both_faces(tmp_path: Path) -> None:
    """Readiness is asserted over the year the caller says should be there, never the years
    the store happens to hold -- reading the stored years back would make `partition_missing`
    unreachable by construction.

    Two codes, not one, and the second is the interesting one: with no partition there is also
    no coverage record, so the field census has nothing to check against and reports
    `field_missing` rather than passing. A face that reported only the first would understate
    how little is known about that year.
    """
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    parameters = query(years=(YEAR - 1,), sessions=(), calendar=False)

    readiness = sdk.panel_readiness(**without(sdk_arguments(parameters), "sessions"))
    response = client.get("/api/v1/panel/readiness", params=without(parameters, "session"))

    assert response.status_code == 200
    assert response.json() == readiness_payload(readiness)
    assert response.json()["all_ready"] is False
    assert response.json()["blocked_datasets"] == list(FIXTURE_DATASETS)
    assert [entry["state"] for entry in response.json()["datasets"]] == ["blocked"] * len(
        FIXTURE_DATASETS
    )
    assert _codes(issue for entry in response.json()["datasets"] for issue in entry["issues"]) == {
        "partition_missing",
        "field_missing",
    }
    assert datetime.fromisoformat(response.json()["as_of"]) == AS_OF
    assert AS_OF.tzinfo is UTC
