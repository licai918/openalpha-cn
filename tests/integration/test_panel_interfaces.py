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
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import panel_fixtures
import pytest
from fastapi.testclient import TestClient
from panel_fixtures import (
    AS_OF,
    EXCHANGE,
    INDEX_CODE,
    YEAR,
    generate_panel,
    write_generated_panel,
)

from openalpha_cn.api.app import PANEL_HTTP_STATUS, create_app
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET
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
    PanelUnreadableError,
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

FIXTURE_SESSIONS: tuple[date, ...] = generate_panel().sessions
"""Every session the generated panel carries, for the same reason and one magnitude further.

A test that asserts *which* dates a `date_gap` names has to get the whole list from the same
place the panel did, or it is asserting that the generator has not changed rather than that
the payload carried the dates.
"""

ABSENT_INDEX_CODE: str = "399905.SZ"
"""An index the fixture panel does not hold, beside `panel_fixtures.INDEX_CODE`, which it does.

`index_weight` is the one dataset whose verdict turns on `index_codes`:
`panel_ingest.index_weight_requirement` puts the named index in `required_subjects` and waives
`required_dates`, so naming an index the partition lacks is `subject_missing` and naming the
one it holds is `ready`. That difference is what makes a face that silently drops the
parameter observable at all -- with no index code named, `required_subjects` is `None` and
every panel answers the same way.
"""


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
    # The three durations, in the unit the field name claims, asserted as numbers rather than
    # as "present". `_seconds` renders a `timedelta`, and `timedelta.days` is the wrong
    # attribute that type-checks: it would answer 4 for adj_factor's four-day bound (right by
    # coincidence of magnitude), 0 for a 21-hour event age, and -348 for the calendar's
    # published horizon. This issue's review found this a surviving mutant that an
    # existence-only assertion cannot see -- 86,400x wrong and still "a float".
    by_dataset = {entry["dataset"]: entry for entry in response.json()["datasets"]}
    assert by_dataset[ADJ_FACTOR_DATASET]["cadence"] == "daily"
    assert by_dataset[ADJ_FACTOR_DATASET]["max_staleness_seconds"] == 4 * 24 * 60 * 60
    assert by_dataset[ADJ_FACTOR_DATASET]["event_age_seconds"] == 21 * 60 * 60
    assert by_dataset[ADJ_FACTOR_DATASET]["fetch_age_seconds"] == 0.0
    assert by_dataset[SUSPENSION_DATASET]["cadence"] == "event_driven"
    assert by_dataset[SUSPENSION_DATASET]["max_staleness_seconds"] is None
    assert by_dataset[SUSPENSION_DATASET]["event_age_seconds"] == 189 * 60 * 60
    # `trade_cal` is published in advance, so its newest event is in the *future* at `as_of`
    # and the age is negative -- which `timedelta.days` would floor to a different number
    # rather than merely rescale, and which a rendering that clamped at zero would erase.
    assert by_dataset[TRADING_CALENDAR_DATASET]["event_age_seconds"] == -30_024_000.0
    assert by_dataset[TRADING_CALENDAR_DATASET]["event_age_seconds"] < 0


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
    # The whole unverified-check census on the clearance, named check by check. The dataset
    # names alone are the weaker half: "adj_factor was not fully verified" is the sentence a
    # caller can already read off `caveats`, and *which* questions went unasked is what tells
    # them whether the gap matters for the read they are about to do. A rendering that carried
    # the dataset and dropped the check list would satisfy every existence assertion.
    assert response.json()["unverified_checks"] == [
        {"dataset": TRADING_CALENDAR_DATASET, "checks": ["required_dates", "max_staleness"]},
        {
            "dataset": STOCK_BASIC_DATASET,
            "checks": ["required_dates", "required_subjects", "max_staleness"],
        },
        {"dataset": ADJ_FACTOR_DATASET, "checks": ["required_dates", "required_subjects"]},
        {"dataset": DAILY_DATASET, "checks": ["required_subjects"]},
        {"dataset": DAILY_BASIC_DATASET, "checks": ["required_subjects"]},
        {
            "dataset": SUSPENSION_DATASET,
            "checks": ["required_dates", "required_subjects", "max_staleness"],
        },
    ]
    # ... and it is the same census `/panel/readiness` reports as `checks_waived`, which is the
    # only reason the two faces may be read together.
    assert clearance.unverified(ADJ_FACTOR_DATASET) == ("required_dates", "required_subjects")


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

    Two of `tests/panel_fixtures.py`'s shapes now do produce a `blocking` and a `warning`
    (`V2-P2-000`), and this case is deliberately not rewritten onto them. Both of those are
    defects the writers *accept* -- a factor series that disagrees with the prices, a filing
    announced after the read -- and what this asserts is the other kind: the catalog still
    registers the `daily` partition and its Parquet file is gone. A store that lost a file is
    not a shape and never will be, so it stays dug by hand.
    """
    store = seed_panel(tmp_path)
    next((store.root / DAILY_DATASET / str(YEAR)).glob("*.parquet")).unlink()
    sdk, client = faces(tmp_path)
    parameters = query()

    report = sdk.panel_health(**sdk_arguments(parameters))
    clearance = sdk.panel_clearance(**sdk_arguments(parameters))
    readiness = sdk.panel_readiness(**without(sdk_arguments(query(sessions=())), "sessions"))
    health = client.get("/api/v1/panel/health", params=parameters)
    gate = client.get("/api/v1/panel/gate", params=parameters)
    ready = client.get("/api/v1/panel/readiness", params=without(parameters, "session"))

    assert health.status_code == 200
    assert health.json() == health_report_payload(report)
    assert health.json()["is_clean"] is False
    assert "partition_file_missing" in {finding["code"] for finding in health.json()["findings"]}
    assert gate.status_code == 409
    assert gate.json() == clearance_payload(clearance)
    assert "partition_file_missing" in {block["code"] for block in gate.json()["blocks"]}
    assert DAILY_DATASET in gate.json()["blocked_datasets"]

    # --- the counts, as counts ------------------------------------------------------------
    #
    # `counts_by_severity` is the field a dashboard reads instead of the findings list, and
    # this issue's review found that every assertion on it in the repository compared against
    # all-zeros or against `> 0`. Neither can see a census that counts a finding twice or
    # counts the datasets it names rather than the finding -- both of which report a sick
    # panel as sicker, in the direction nobody double-checks. So: the exact numbers, and their
    # agreement with the list they are a census of.
    assert health.json()["counts_by_severity"] == {"blocking": 3, "warning": 3, "notice": 0}
    assert len(health.json()["findings"]) == 6
    assert sum(health.json()["counts_by_severity"].values()) == len(health.json()["findings"])
    assert [finding["code"] for finding in health.json()["findings"]] == [
        "partition_file_missing",
        "date_gap",
        "field_missing",
        "check_unavailable",
        "check_unavailable",
        "check_unavailable",
    ]

    # --- one block, field by field --------------------------------------------------------
    #
    # The `409` body is the whole justification for answering `409` rather than a bare status
    # line, and the commit that introduced it claims it "carries every block with its code,
    # category, severity, both sides of a cross-dataset finding and its detail". Before this
    # issue's review, no test in the repository indexed a block by anything but `code`, so a
    # renderer that dropped `severity` or `category` kept every test green while making a
    # `warning` and a `blocking` refusal indistinguishable to a client.
    blocks = {(block["code"], len(block["datasets"])): block for block in gate.json()["blocks"]}
    partition = blocks[("partition_file_missing", 1)]
    assert set(partition) == {
        "code",
        "category",
        "severity",
        "dataset",
        "datasets",
        "detail",
        "year",
    }
    assert partition["category"] == "missing"
    assert partition["severity"] == "blocking"
    assert partition["dataset"] == DAILY_DATASET
    assert partition["datasets"] == [DAILY_DATASET]
    assert partition["year"] == YEAR
    assert partition["detail"].startswith(
        f"{DAILY_DATASET} year={YEAR} is registered in the catalog but its Parquet file is missing:"
    )
    # "Both sides of a cross-dataset finding": the widest block here names four datasets, and
    # a renderer that carried only `dataset` would report a check between four as a fault of
    # one. It is also a `warning` rather than a `blocking`, which is what makes `severity`
    # load-bearing on this body rather than a constant.
    cross = blocks[("check_unavailable", 4)]
    assert cross["severity"] == "warning"
    assert cross["category"] == "unanswerable"
    assert cross["dataset"] == DAILY_DATASET
    assert cross["datasets"] == [
        DAILY_DATASET,
        ADJ_FACTOR_DATASET,
        STOCK_BASIC_DATASET,
        SUSPENSION_DATASET,
    ]

    # --- one readiness issue, field by field ----------------------------------------------
    #
    # `missing_dates` is the field that says *which* sessions are gone, and ten of them are
    # gone here -- every session the fixture holds. Read off the generator, so this asserts
    # that the payload carried the dates rather than that the generator has not changed.
    assert ready.status_code == 200
    assert ready.json() == readiness_payload(readiness)
    assert ready.json()["blocked_datasets"] == [DAILY_DATASET]
    daily = next(entry for entry in ready.json()["datasets"] if entry["dataset"] == DAILY_DATASET)
    gap = next(issue for issue in daily["issues"] if issue["code"] == "date_gap")
    assert set(gap) == {"code", "dataset", "detail", "year", "missing_dates", "missing_items"}
    assert gap["missing_dates"] == [day.isoformat() for day in FIXTURE_SESSIONS]
    assert len(gap["missing_dates"]) == 10
    assert gap["missing_items"] == []
    fields = next(issue for issue in daily["issues"] if issue["code"] == "field_missing")
    assert fields["missing_dates"] == []
    assert len(fields["missing_items"]) == 10
    assert {"close", "open", "high", "low", "vol"} <= set(fields["missing_items"])


# --- the request reaches the verdict, parameter by parameter -----------------------------------
#
# Everything above drives the two faces with the *same* request and compares the answers, which
# is what "REST + SDK equivalence" means -- and which, on its own, cannot see a parameter that
# neither face passes on. This issue's review made the point concretely: a face that ignored
# `index_codes`, or one that judged every request against a hard-coded exchange, kept the whole
# suite green, because both sides of every equivalence were asking with the same value.
#
# So the tests below pick, for each such parameter, two values that make the panel answer
# *differently*, and pin both answers through both faces.


def index_weight_query(index_codes: Sequence[str]) -> dict[str, Any]:
    """One dataset, no session, no calendar, and the index codes under test.

    `index_weight` alone, because it is the only dataset whose requirement is built from
    `index_codes`; `calendar=False` because none of its checks is session-scoped, so the
    calendar would only add a second reason for the same answer.
    """
    return query(
        datasets=(INDEX_WEIGHT_DATASET,),
        years=(YEAR,),
        sessions=(),
        calendar=False,
        index_codes=index_codes,
    )


def test_an_index_the_panel_never_held_is_reported_by_both_readiness_faces(
    tmp_path: Path,
) -> None:
    """`index_weight`'s requirement names the index in `required_subjects`, so an index the
    partition does not carry is `subject_missing` and a blocked dataset -- the shape
    `index_weight_requirement` exists to produce, since a membership read for a missing index
    would otherwise be an empty one.

    Both values are asserted, not just the failing one. `all_ready=False` alone would also be
    produced by a face that dropped the index code and blocked everything; `all_ready=True` for
    the index the panel *does* hold is what shows the parameter arrived and was answered.
    """
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    absent = index_weight_query((ABSENT_INDEX_CODE,))

    readiness = sdk.panel_readiness(**without(sdk_arguments(absent), "sessions"))
    response = client.get("/api/v1/panel/readiness", params=without(absent, "session"))

    assert response.status_code == 200
    assert response.json() == readiness_payload(readiness)
    assert response.json()["all_ready"] is False
    assert response.json()["blocked_datasets"] == [INDEX_WEIGHT_DATASET]
    entry = response.json()["datasets"][0]
    assert entry["state"] == "blocked"
    assert entry["checks_waived"] == ["required_dates", "max_staleness"]
    assert entry["issues"] == [
        {
            "code": "subject_missing",
            "dataset": INDEX_WEIGHT_DATASET,
            "detail": f"1 required subject(s) are absent from {INDEX_WEIGHT_DATASET}",
            "year": None,
            "missing_dates": [],
            "missing_items": [ABSENT_INDEX_CODE],
        }
    ]

    # The index the panel does hold, through both faces, on the same store.
    held = index_weight_query((INDEX_CODE,))
    assert (
        client.get("/api/v1/panel/readiness", params=without(held, "session")).json()["all_ready"]
        is True
    )
    assert sdk.panel_readiness(**without(sdk_arguments(held), "sessions"))[0].is_ready is True
    # Naming none is a third answer and not the same as either: with no subject required,
    # `required_subjects` joins the waived list, so the dataset is `ready` and less was asked.
    silent = index_weight_query(())
    unasked = client.get("/api/v1/panel/readiness", params=without(silent, "session")).json()
    assert unasked["all_ready"] is True
    assert unasked["datasets"][0]["checks_waived"] == [
        "required_dates",
        "required_subjects",
        "max_staleness",
    ]


def test_an_index_code_reaches_the_gate_through_both_faces(tmp_path: Path) -> None:
    """The same parameter one endpoint over, where the difference it makes is the status code.

    Naming the index the panel holds clears; naming none is refused, because with
    `required_subjects` waived the gate has nothing that corroborated the index at all. So a
    face that dropped `index_codes` turns a `200` into a `409` -- and this is the one test that
    would catch it, since every other clearance in this file is driven with `index_codes=()` on
    both sides.
    """
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    held = index_weight_query((INDEX_CODE,))

    clearance = sdk.panel_clearance(**sdk_arguments(held))
    response = client.get("/api/v1/panel/gate", params=held)

    assert response.status_code == PANEL_HTTP_STATUS["answered"] == 200
    assert response.json() == clearance_payload(clearance)
    assert clearance.is_blocked is False
    assert response.json()["is_blocked"] is False
    assert response.json()["blocks"] == []
    assert [entry["dataset"] for entry in response.json()["cleared"]] == [INDEX_WEIGHT_DATASET]
    assert response.json()["unverified_checks"] == [
        {"dataset": INDEX_WEIGHT_DATASET, "checks": ["required_dates", "max_staleness"]}
    ]

    silent = index_weight_query(())
    unnamed_sdk = sdk.panel_clearance(**sdk_arguments(silent))
    unnamed = client.get("/api/v1/panel/gate", params=silent)

    assert unnamed.status_code == PANEL_HTTP_STATUS["blocked"] == 409
    assert unnamed.json() == clearance_payload(unnamed_sdk)
    assert [block["code"] for block in unnamed.json()["blocks"]] == ["check_unavailable"]


def test_the_sdk_judges_against_the_exchange_it_was_given_and_not_a_built_in_one(
    tmp_path: Path,
) -> None:
    """`exchange` is mandatory on the SDK because "a request judged against SSE's calendar and
    one judged against SZSE's are different questions" -- and until this issue's review, the
    only test of that asserted a missing argument raises `TypeError`. A method that accepted
    the argument and then judged against a hard-coded `SZSE` satisfied it, and satisfied every
    equivalence in this file too, because both faces are handed the same `EXCHANGE`.

    The fixture stores SZSE's calendar and no other, so `exchange="SSE"` is a value with an
    observable consequence: all three methods refuse. Driven through all three because they
    reach `panel_view.panel_request` by three separate call sites.
    """
    seed_panel(tmp_path)
    sdk, client = faces(tmp_path)
    stated = sdk_arguments(query(exchange="SSE"))

    for method, arguments in (
        (sdk.panel_health, stated),
        (sdk.panel_clearance, stated),
        (sdk.panel_readiness, without(stated, "sessions")),
    ):
        with pytest.raises(PanelUnreadableError, match=r"^the SSE calendar could not be read"):
            method(**arguments)  # type: ignore[operator]

    # The stored exchange still answers on the same store, so the refusal is about the name the
    # caller gave and not about a panel nothing could read.
    assert sdk.panel_health(**sdk_arguments(query())).is_clean is True
    assert sdk.panel_clearance(**sdk_arguments(query())).is_blocked is False
    # And HTTP puts the same refusal in its own envelope, naming the same exchange.
    refusal = client.get("/api/v1/panel/gate", params=query(exchange="SSE"))
    assert refusal.status_code == PANEL_HTTP_STATUS["panel_unreadable"]
    assert refusal.json()["detail"]["message"].startswith("the SSE calendar could not be read")


def test_the_exchange_is_inert_when_the_caller_states_this_run_has_no_calendar(
    tmp_path: Path,
) -> None:
    """The other half of the same parameter, stated rather than left to be found out.

    With `calendar=false` nothing downstream reads `exchange`, so two well-formed names produce
    byte-identical responses and a misspelling cannot be detected -- there is no stored
    calendar to compare it against, which is precisely what the caller asserted. Pinned as an
    equality so the inertness is a property of this face rather than a surprise, and paired
    with the `calendar=true` case above, where the same misspelling is a `409`.

    What *is* refused on both settings is a name no store could hold: `build_trading_calendar`
    rejects an empty or whitespace-padded exchange, so a request naming one is unsatisfiable
    whatever else it says.
    """
    seed_panel(tmp_path)
    _, client = faces(tmp_path)
    stated = query(sessions=(), calendar=False)

    real = client.get("/api/v1/panel/health", params=stated)
    misspelled = client.get("/api/v1/panel/health", params=stated | {"exchange": "NOT_AN_EXCHANGE"})

    assert real.status_code == misspelled.status_code == 200
    assert real.content == misspelled.content
    # ... and with the calendar switched on, the same misspelling is refused by name.
    named = client.get("/api/v1/panel/health", params=stated | {"calendar": True})
    assert named.status_code == 200
    assert (
        client.get(
            "/api/v1/panel/health",
            params=stated | {"calendar": True, "exchange": "NOT_AN_EXCHANGE"},
        ).status_code
        == PANEL_HTTP_STATUS["panel_unreadable"]
    )
    for unusable in ("", " ", "SZSE "):
        refused = client.get("/api/v1/panel/health", params=stated | {"exchange": unusable})
        assert refused.status_code == PANEL_HTTP_STATUS["bad_request"], unusable
        assert refused.json()["detail"]["reason"] == "bad_request"


def test_a_session_sent_to_the_readiness_endpoint_is_dropped_rather_than_honoured(
    tmp_path: Path,
) -> None:
    """`/panel/readiness` does not declare `session`, and an undeclared query parameter is
    discarded before the endpoint body runs -- so a caller who copies a `/panel/health` query
    onto this path is answered a narrower question than the one they typed, with nothing in the
    response saying so.

    That is the endpoint's real behaviour and it is now stated in its docstring and in
    `docs/api/http.md`. Asserted as a byte equality here so the statement stays true: if a
    later issue makes this face refuse the parameter, or honour it, this test fails and says
    which document to update.

    The equality is *doubly* held, and only one half is this endpoint's: passing the session
    through `_panel_query` anyway changes nothing either, because `panel_view.dataset_readiness`
    deliberately never reads `DependencyRequest.sessions`. That makes "make the readiness
    endpoint forward a session" an equivalent mutant, which is what it was found to be; the
    mutant this test does kill is "make the readiness endpoint refuse a session", which is the
    change the documentation above would stop describing.

    The second half is not vacuous in either direction: the same session sent to `/panel/health`
    changes which cross-checks ran, so these really are two different questions.
    """
    seed_panel(tmp_path)
    _, client = faces(tmp_path)
    stated = without(query(), "session")

    without_session = client.get("/api/v1/panel/readiness", params=stated)
    with_session = client.get(
        "/api/v1/panel/readiness", params=stated | {"session": [FIXTURE_SESSION.isoformat()]}
    )

    assert without_session.status_code == with_session.status_code == 200
    assert without_session.content == with_session.content
    # The same session sent to the face that does take it changes what ran, which is why the
    # drop matters: these are two different questions, not two spellings of one.
    assert {
        check["name"]
        for check in client.get("/api/v1/panel/health", params=query()).json()["cross_checks"]
        if check["ran"]
    } > {
        check["name"]
        for check in client.get("/api/v1/panel/health", params=query(sessions=())).json()[
            "cross_checks"
        ]
        if check["ran"]
    }


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
        # The two cells this issue's review found missing, and they are the two least
        # guessable in the table: `/panel/health` and `/panel/readiness` answer `200` about
        # every sick panel there is, and non-2xx about a *request* -- so the only non-2xx
        # either of them can produce is one that says nothing about the panel's health. A
        # reader who had seen only the rows above would have concluded these two faces are
        # always `200`.
        ("health", "a calendar that is not stored"): client.get(
            "/api/v1/panel/health", params=query(exchange="SSE")
        ).status_code,
        ("readiness", "a calendar that is not stored"): client.get(
            "/api/v1/panel/readiness", params=without(query(exchange="SSE"), "session")
        ).status_code,
        ("health", "an exchange no store could hold"): client.get(
            "/api/v1/panel/health", params=query(exchange="")
        ).status_code,
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
        ("health", "a calendar that is not stored"): 409,
        ("readiness", "a calendar that is not stored"): 409,
        ("health", "an exchange no store could hold"): 422,
    }


def test_the_two_bodies_a_409_can_carry_are_told_apart_by_one_named_field(
    tmp_path: Path,
) -> None:
    """`blocked` and `panel_unreadable` share `409` on purpose -- both are "the panel's state
    stands in the way" -- but they do **not** share a body, and this issue's review found the
    fork undocumented and unasserted. A client that switched on the status code alone and read
    `json()["blocks"]` works on the first and raises `KeyError` on the second.

    So the discriminator is named and pinned: a refusal is `{"detail": {"reason", "message"}}`
    and a verdict is the flat clearance payload. They share no key at all, which is the
    strongest form of the property -- there is no field a client could read that means one
    thing on one body and another on the other.
    """
    seed_panel(tmp_path)
    _, client = faces(tmp_path)

    blocked = client.get("/api/v1/panel/gate", params=query(sessions=()))
    unreadable = client.get("/api/v1/panel/gate", params=query(exchange="SSE"))

    assert blocked.status_code == unreadable.status_code == PANEL_HTTP_STATUS["blocked"] == 409
    assert set(blocked.json()) & set(unreadable.json()) == set()
    assert "detail" not in blocked.json()
    assert blocked.json()["is_blocked"] is True
    assert set(unreadable.json()) == {"detail"}
    assert unreadable.json()["detail"]["reason"] == "panel_unreadable"
    assert set(unreadable.json()["detail"]) == {"reason", "message"}
    # A `422` from this plane wears the same shape with the other reason on it, so one branch
    # reads both refusals; and the reason names a row of the table that enveloped it.
    naive = client.get(
        "/api/v1/panel/gate", params=query() | {"as_of": AS_OF.replace(tzinfo=None).isoformat()}
    )
    assert naive.status_code == PANEL_HTTP_STATUS["bad_request"] == 422
    assert naive.json()["detail"]["reason"] == "bad_request"
    for response in (unreadable, naive):
        reason = response.json()["detail"]["reason"]
        assert PANEL_HTTP_STATUS[reason] == response.status_code

    # A third shape exists and is not this plane's: FastAPI answers `422` for a parameter it
    # could not bind at all, with `detail` a *list*. `isinstance(detail, dict)` separates them,
    # and a client that assumed an object would break on the commonest client error there is.
    unbound = client.get("/api/v1/panel/gate", params=without(query(), "dataset"))
    assert unbound.status_code == 422
    assert isinstance(unbound.json()["detail"], list)
    assert isinstance(naive.json()["detail"], dict)


def test_no_panel_refusal_body_says_where_this_service_keeps_its_store(tmp_path: Path) -> None:
    """A `409`/`422` `detail` crosses a process boundary, so it says what is wrong and not
    where this deployment put its files. `str(error)` still names the store -- the SDK and the
    CLI are inside the process that owns it -- and `disclosable` is what the app sends.

    The hard case is the second one below: the *cause* interpolates a path of its own.
    `trade_cal` registered in the catalog with its Parquet file deleted raises a
    `PanelStorageError` naming that file, and the message is carried into the response because
    "which of `partition_missing`/`partition_file_missing` stood in the way" is the actionable
    half of the refusal. `_without_store_path` is what makes carrying it safe, and it has to
    handle the resolved spelling too: on macOS `tmp_path` is under `/var/folders/...` and
    resolves to `/private/var/folders/...`.
    """
    store = seed_panel(tmp_path)
    _, client = faces(tmp_path)
    locations = {str(store.root), str(store.root.resolve()), str(tmp_path)}

    refusals = [
        client.get("/api/v1/panel/gate", params=query(exchange="SSE")),
        client.get("/api/v1/panel/health", params=query(exchange="")),
        client.get(
            "/api/v1/panel/health",
            params=query() | {"as_of": AS_OF.replace(tzinfo=None).isoformat()},
        ),
        client.get("/api/v1/panel/gate", params=query(datasets=("not_a_dataset",), sessions=())),
    ]
    next((store.root / TRADING_CALENDAR_DATASET / str(YEAR)).glob("*.parquet")).unlink()
    refusals.append(client.get("/api/v1/panel/gate", params=query()))

    assert [response.status_code for response in refusals] == [409, 422, 422, 422, 409]
    for response in refusals:
        body = response.text
        for location in locations:
            assert location not in body, body
        assert isinstance(response.json()["detail"], dict)
    # The last one really did carry its cause rather than being emptied to achieve this.
    carried = refusals[-1].json()["detail"]["message"]
    assert "partition_file_missing" in carried
    assert "data.parquet" in carried
    # ... and the in-process message, which is not a response body, still names the store.
    sdk = OpenAlphaSDK(runtime_dir=tmp_path)
    with pytest.raises(PanelUnreadableError, match=re.escape(str(store.root))):
        sdk.panel_health(**sdk_arguments(query()))


def test_a_catalog_that_is_not_a_database_is_the_endpoint_breaking_and_not_a_verdict(
    tmp_path: Path,
) -> None:
    """The `internal_error` row `PANEL_HTTP_STATUS` was missing.

    `cli.PanelExit` has `internal_error` and argues for why it must: without it a command that
    crashed exited 1 and was indistinguishable from a panel that failed its check. The HTTP
    table made no such row, so a reader concluded that every non-2xx a panel endpoint produces
    means something about the panel or about the request -- and a `500` means neither. Both
    channels already behave this way; only the table and the test were missing.

    A catalog file that is not a DuckDB database is the smallest fault no branch in this plane
    anticipates: `PanelStore` opens it lazily, so the failure lands inside the endpoint rather
    than at construction, and nothing catches `duckdb.IOException`.
    """
    store = seed_panel(tmp_path)
    (store.root / "catalog.duckdb").write_bytes(b"not a duckdb database" * 64)
    client = TestClient(create_app(runtime_dir=tmp_path), raise_server_exceptions=False)
    # `calendar=false`: with the calendar on, the same broken catalog is met by
    # `stored_calendar` first and becomes the `409` above, which is a different situation.
    parameters = query(sessions=(), calendar=False)

    observed = {
        "readiness": client.get(
            "/api/v1/panel/readiness", params=without(parameters, "session")
        ).status_code,
        "health": client.get("/api/v1/panel/health", params=parameters).status_code,
        "gate": client.get("/api/v1/panel/gate", params=parameters).status_code,
    }

    assert observed == dict.fromkeys(observed, PANEL_HTTP_STATUS["internal_error"])
    assert PANEL_HTTP_STATUS["internal_error"] == 500
    # And it is a class of its own: not the code a refused gate wears, and not the code a
    # request that cannot be put wears.
    assert PANEL_HTTP_STATUS["internal_error"] not in {
        PANEL_HTTP_STATUS["blocked"],
        PANEL_HTTP_STATUS["bad_request"],
        PANEL_HTTP_STATUS["answered"],
    }


def test_no_status_code_in_the_table_means_what_panel_doctors_exit_one_means(
    tmp_path: Path,
) -> None:
    """The asymmetry between the two tables, asserted rather than described.

    `PANEL_HTTP_STATUS` says `cli.PanelExit` "is the sibling of this and the reasoning is
    shared". That holds for the gate -- `data-check` exits 1 where `/panel/gate` answers `409`
    -- and it does **not** hold for the doctor: `panel doctor` exits 1 on a panel that is not
    `is_clean`, and `/panel/health` answers `200` for that same panel, as does
    `/panel/readiness` for a blocked dataset. So an alert rule that reads the status code of
    `/panel/health` never fires, which is the easy mistake to make when the app also serves
    `GET /health` as a liveness probe.

    Both documents now say so; this pins the fact they are describing, and names the two things
    that *are* the HTTP equivalent of that exit code.
    """
    store = seed_panel(tmp_path)
    next((store.root / DAILY_DATASET / str(YEAR)).glob("*.parquet")).unlink()
    _, client = faces(tmp_path)
    parameters = query()

    health = client.get("/api/v1/panel/health", params=parameters)
    ready = client.get("/api/v1/panel/readiness", params=without(parameters, "session"))
    gate = client.get("/api/v1/panel/gate", params=parameters)
    liveness = client.get("/health")

    # A panel `panel doctor` would exit 1 over, and two faces that answer 2xx about it.
    assert health.json()["is_clean"] is False
    assert ready.json()["all_ready"] is False
    assert health.status_code == ready.status_code == PANEL_HTTP_STATUS["answered"] == 200
    assert liveness.status_code == 200
    # The status code of the sick panel's report is the status code of a healthy panel's
    # report and of the service's own liveness probe. Nothing in it carries the verdict.
    assert (
        health.status_code
        == liveness.status_code
        == client.get(
            "/api/v1/panel/health",
            params=query() | {"as_of": AS_OF.isoformat()},
        ).status_code
    )
    # The two things that are the equivalent: the body's verdict, and the gate's refusal.
    assert health.json()["counts_by_severity"]["blocking"] > 0
    assert gate.status_code == PANEL_HTTP_STATUS["blocked"] == 409


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
    # `related_limitations` is the field that separates "this fetch went wrong" from "this
    # dataset cannot answer that question at all", and it is the reason `limitations` is a
    # sibling of `findings` on this payload rather than merged into it. A caller who sees
    # `ambiguous_filing` and no limitation concludes the filing is ambiguous by accident; the
    # two names below say it is ambiguous by construction, and that no re-fetch will fix it.
    # Asserted by name and by count, because an empty list is what a dropped field looks like.
    notices = {notice["code"]: notice for notice in response.json()["notices"]}
    assert set(notices) == {"duplicate_versions", "ambiguous_filing"}
    assert notices["ambiguous_filing"]["related_limitations"] == [
        "a_correction_carries_no_instant_of_its_own",
        "update_flag_does_not_say_which_version_is_current",
    ]
    assert notices["duplicate_versions"]["related_limitations"] == [
        "update_flag_does_not_say_which_version_is_current",
    ]
    assert notices["duplicate_versions"]["count"] == 9
    assert notices["ambiguous_filing"]["count"] == 1
    # ... and every limitation a notice points at is on the report it points into, so the
    # names are a reference rather than a second vocabulary.
    declared = {limitation["code"] for limitation in response.json()["report"]["limitations"]}
    assert declared >= {
        "a_correction_carries_no_instant_of_its_own",
        "update_flag_does_not_say_which_version_is_current",
    }


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
