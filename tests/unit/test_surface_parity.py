"""The three-way difference between REST, the SDK and the CLI, as an equality (`V2-P5-013`).

`V2-P5-013`'s row said `OutcomeValidator` was **completely absent from the SDK**, that seven
capabilities were REST-only, and that the CLI covered 4 of 20 capability domains. Measured on
`2746663`, the first was false -- `OpenAlphaSDK.validate_outcome`,
`.list_validations_by_decision` and `.list_validations_by_signal` all exist -- and the third was
false by a factor of six. `V2-P5-001` had meanwhile shipped a CLI command and an SDK method with
no route, and `V2-P5-010` had shipped a whole subsystem with no face at all.

**Every one of those is a fact that a paragraph could not keep.** Prose about a gap is written
once and read later, and nothing goes red when the gap closes or when a new one opens. So the
gap lives here instead, as three declared sets checked against the three live surfaces:

- `PARITY` names, for every shipping REST route, the SDK method and CLI command that reach the
  same capability, or `None` with the reason (in `WITHOUT_SDK_REASONS`) that it does not.
- `SDK_ONLY` and `CLI_ONLY` name the capabilities that exist on one face and no route, each with
  its reason.

A route added without a row here is red and names the route. A row naming an SDK method or CLI
command that does not exist is red and names it. A gap that closes without its row changing is
red. That is the difference between this file and the sentence it replaces.

**This measures reachability and not equivalence.** That two faces name the same capability does
not mean they render it alike; the equality assertions for that live at each capability's own
integration test (`test_portfolio_construction_interfaces.py` holds three renderings of one
construction byte-equal, `test_scheduled_job_faces.py` holds two listings of one schedule equal).
What this file makes impossible is the *silent* kind of drift -- a face that simply is not there.
"""

from __future__ import annotations

import inspect
import os
import re
import tempfile
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Final
from unittest import mock

import pytest
import typer
from fastapi import FastAPI
from starlette.routing import Mount

from openalpha_cn import cli as cli_module
from openalpha_cn.api.app import create_app
from openalpha_cn.sdk import OpenAlphaSDK

FRAMEWORK_ROUTES: Final[frozenset[str]] = frozenset(
    {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
)
"""Paths FastAPI mounts itself. They are documentation of this API, not capabilities of it."""


PARITY: Final[MappingProxyType[str, tuple[str | None, str | None]]] = MappingProxyType(
    {
        # --- research core ---------------------------------------------------------------
        "POST /api/v1/research/run": ("run_research", "research run"),
        "POST /api/v1/research/deliberate": ("deliberate", None),
        "POST /api/v1/evidence/build": ("build_file_evidence", "evidence build"),
        "GET /api/v1/evidence": ("query_evidence", None),
        "GET /api/v1/memory/{subject}": ("list_memory", None),
        "GET /api/v1/runs/{run_id}/recovery": ("get_recovery", None),
        "GET /health": ("health", None),
        # --- batch -----------------------------------------------------------------------
        "POST /api/v1/research/batches": ("run_batch", None),
        "GET /api/v1/research/batches": (None, None),
        "GET /api/v1/research/batches/{batch_id}": (None, None),
        "GET /api/v1/research/batches/{batch_id}/events": (None, None),
        "POST /api/v1/research/batches/{batch_id}/cancel": (None, None),
        "POST /api/v1/research/batches/{batch_id}/retry": (None, None),
        # --- panel -----------------------------------------------------------------------
        "GET /api/v1/panel/health": ("panel_health", "panel doctor"),
        "GET /api/v1/panel/gate": ("panel_clearance", "data-check"),
        "GET /api/v1/panel/readiness": ("panel_readiness", None),
        # --- factors ---------------------------------------------------------------------
        "GET /api/v1/factors": ("factor_catalog", "factor list"),
        "POST /api/v1/factors/run": ("run_factor_experiment", "factor run"),
        "GET /api/v1/factors/experiments": ("list_factor_experiments", None),
        "GET /api/v1/factors/experiments/{experiment_id}": ("get_factor_experiment", None),
        # --- models ----------------------------------------------------------------------
        "POST /api/v1/models/evaluate": ("evaluate_model", "model evaluate"),
        "POST /api/v1/models/daily-run": ("run_daily_model", "model daily-run"),
        "GET /api/v1/predictions": ("list_predictions", "model predictions"),
        "GET /api/v1/predictions/{record_id}": ("held_prediction", "model prediction"),
        # --- shortlists ------------------------------------------------------------------
        "POST /api/v1/shortlists/run": ("run_shortlist", "shortlist run"),
        "GET /api/v1/shortlists": ("list_shortlists", "shortlist list"),
        "GET /api/v1/shortlists/{shortlist_id}": ("held_shortlist", "shortlist get"),
        # --- portfolio -------------------------------------------------------------------
        "POST /api/v1/portfolio/construct": ("construct_portfolio", "portfolio construct"),
        "POST /api/v1/portfolio/execute": ("execute_portfolio_order", None),
        "GET /api/v1/portfolio/ledger": ("list_portfolio_transitions", None),
        "POST /api/v1/backtests/portfolio": ("run_portfolio_backtest", None),
        # --- studies and validation ------------------------------------------------------
        "POST /api/v1/backtests/event-study": ("run_event_study", None),
        "POST /api/v1/backtests/replay": ("replay", "replay run"),
        "POST /api/v1/backtests/validate": ("validate_outcome", None),
        "GET /api/v1/backtests/validations/by-decision/{decision_id}": (
            "list_validations_by_decision",
            None,
        ),
        "GET /api/v1/backtests/validations/by-signal/{signal_id}": (
            "list_validations_by_signal",
            None,
        ),
        # --- product ---------------------------------------------------------------------
        "POST /api/v1/screen": ("screen", None),
        "GET /api/v1/watchlist": ("list_watchlist", None),
        "POST /api/v1/watchlist": ("put_watchlist", None),
        "POST /api/v1/watchlist/{subject}/remove": (None, None),
        "GET /api/v1/reports": ("list_reports", None),
        "POST /api/v1/reports": ("create_report", None),
        "GET /api/v1/reports/{report_id}": (None, None),
        # `V2-P5-022`. The one report route that landed on all three faces at once, and
        # deliberately: an export is the artifact a user hands to somebody else, so a face that
        # could produce it without the licence gate would be the whole defect.
        "GET /api/v1/reports/{report_id}/export": ("export_report", "report export"),
        "GET /api/v1/market/events": (None, None),
        "GET /api/v1/themes": (None, None),
        # --- scheduling (`V2-P5-013`) ----------------------------------------------------
        "GET /api/v1/jobs": (None, "jobs list"),
        "GET /api/v1/jobs/{job_id}": (None, "jobs list"),
    }
)
"""Every shipping route, and what reaches the same capability on the other two faces.

`None` is a **measured** gap and not an aspiration; `WITHOUT_SDK_REASONS` below says why each one
is still open. The route string is `"<METHOD> <path>"` exactly as FastAPI holds it, so a path
renamed in `api/app.py` arrives here as an unmapped route rather than as a silently orphaned row.
"""


WITHOUT_SDK_REASONS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "GET /api/v1/research/batches": "batch reads (F30)",
        "GET /api/v1/research/batches/{batch_id}": "batch reads (F30)",
        "GET /api/v1/research/batches/{batch_id}/events": "batch reads (F30)",
        "POST /api/v1/research/batches/{batch_id}/cancel": "batch control (F30)",
        "POST /api/v1/research/batches/{batch_id}/retry": "batch control (F30)",
        "POST /api/v1/watchlist/{subject}/remove": "watchlist removal (F30)",
        "GET /api/v1/reports/{report_id}": "one report by id (F30)",
        "GET /api/v1/market/events": "the filter is inlined in `api/app.py` (F30)",
        "GET /api/v1/themes": "the theme list is inlined in `api/app.py` (F30)",
        "GET /api/v1/jobs": "reachable from `openalpha jobs list`; an SDK twin is open work",
        "GET /api/v1/jobs/{job_id}": "reachable from `openalpha jobs list`; an SDK twin is "
        "open work",
    }
)
"""Every route with no SDK method, and why it has none.

Nine of the eleven reach **no** other face at all, which is audit `F30`'s list -- and `F30` says
"7 项" where the list it enumerates is nine. The other two are `V2-P5-013`'s own new job routes,
which have a CLI face and no SDK one.

Recorded rather than closed, and the distinction between the two kinds is a measurement:
`market/events` and `themes` compute their answers **inside the route function**, so there is no
library-level callable for an SDK method to delegate to, and adding one is a refactor of
`api/app.py` rather than the wiring `V2-P5-013` closed for `portfolio/construct`. The five batch
rows, `watchlist remove` and `reports/{report_id}` are wiring and are genuinely open work.

The count is pinned below so that closing one goes red and says which.
"""


SDK_ONLY: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "build_factor_panels": "`openalpha factor build`'s in-process twin; no route (F30 mirror)",
        "compare_shortlists": "`openalpha shortlist compare`'s twin; no route",
        "construct_portfolio_from_ranking": "takes an in-process `ShortlistRunResult`, which no "
        "HTTP body can carry -- a run's own ranking, not a stored answer",
        "construction_candidates": "a narrowing helper for a caller assembling its own policy",
        "construction_view": "a renderer, not a capability",
        "daily_view": "a renderer, not a capability",
        "describe_factor": "`openalpha factor describe`'s twin; no route",
        "evaluation_view": "a renderer, not a capability",
        "factor_build_view": "a renderer, not a capability",
        "factor_experiment_view": "a renderer, not a capability",
        "held_predictions": "the batch behind `held_prediction`; the route serves one record",
        "outcome_statistics": "`V2-P5-008`'s gross/net table; `openalpha outcome statistics`'s "
        "twin, no route yet",
        "outcome_statistics_view": "a renderer, not a capability",
        "segmented_outcomes": "`V2-P5-009`'s segmented table over stored validations; "
        "its whole input is a declared SegmentationPlan and the CLI twin takes it as a "
        "file, no route yet",
        "segmented_report_view": "a renderer, not a capability",
        "turnover_variants": "`V2-P5-024`'s buffered book beside the unbuffered one; "
        "`openalpha portfolio turnover-variants`'s twin, and unlike "
        "`construct_portfolio` no route does it yet",
        "turnover_variant_view": "a renderer, not a capability",
        "shortlist_view": "a renderer, not a capability",
    }
)
"""SDK methods with no REST route, and why each one is not a gap in the same sense.

Three kinds, and the distinction is what stops this set becoming a to-do list nobody reads:
renderers (a `*_view` is how a face turns an answer into bytes, and an HTTP route *is* that
face), in-process-only signatures (`construct_portfolio_from_ranking` takes an object no request
body can carry), and genuine CLI/SDK twins whose route has not been written.
"""


CLI_ONLY: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "doctor": "probes provider credentials on this machine; deliberately not remote",
        "factor build": "writes factor partitions; `build_factor_panels` is the SDK twin",
        "factor describe": "`describe_factor` is the SDK twin; no route",
        "jobs due": "needs a stored calendar and answers about this machine's schedules",
        "jobs register": "declares a schedule; a write this unauthenticated API must not take",
        "jobs run": "takes a lease and does work on the machine holding the runtime directory",
        "migrate prune-backups": "deletes local backup files; not a remote capability",
        "migrate run": "operates on `state.sqlite3` before any app is built",
        "migrate status": "operates on `state.sqlite3` before any app is built",
        "panel build": "reaches a paid provider; not exposed unauthenticated",
        "serve": "starts the server, so it cannot be a route on it",
        "shortlist compare": "`compare_shortlists` is the SDK twin; no route",
        "validation segmented": "`V2-P5-009`'s segmented table -- industry, size, "
        "liquidity and regime buckets tested in one family; the SDK twin exists and no "
        "route does yet",
        "portfolio turnover-variants": "`V2-P5-024`'s buffered arm beside the "
        "unbuffered one; the SDK twin exists and no route does yet",
        "validation statistics": "`V2-P5-008`'s gross-beside-net table over stored "
        "validations; the SDK twin exists and no route does yet",
        "version": "reports this build; `GET /health` carries the version too",
    }
)
"""CLI commands with no REST route, and why.

Four of them **must not** have one while this API has no authentication at all (audit `F101`'s
second sentence, unclosed): `doctor` reads credentials, `panel build` spends a paid quota, and
`jobs register`/`jobs run` write a schedule and take a lease. `serve` and the three `migrate`
commands are structurally impossible as routes -- they act on the process or on the database
before the process exists.
"""


@contextmanager
def _application() -> Iterator[FastAPI]:
    """A real application with storage in a temp directory and **no** build mounted.

    `OPENALPHA_WEB_DIR` is **cleared**, and passing `web_dir=None` is not a substitute for it --
    measured, and the first version of this helper got exactly that wrong. `create_app` reads the
    variable through `load_config()` whenever `web_dir` is `None`, because `None` there means
    "not specified" rather than "no build"; so with the variable exported the application arrives
    *mounted* either way. Now that `_surface_of` keys `Mount`, that is one extra `MOUNT /` in the
    table on a developer's machine and none in CI -- the worst shape a count can have.
    `test_an_exported_web_dir_does_not_change_the_surface_this_file_counts` holds it down, and it
    is the test that falsified the `web_dir=None` version.
    """
    with mock.patch.dict(os.environ), tempfile.TemporaryDirectory() as directory:
        os.environ.pop("OPENALPHA_WEB_DIR", None)
        yield create_app(runtime_dir=Path(directory) / "runtime")


def _route_entries() -> dict[str, object]:
    """Every route this application answers, keyed the way `PARITY` writes them.

    **Every route object, not only the ones with `methods`.** Until `V2-P5-035` this skipped
    anything whose `methods` was `None`, which is `WebSocketRoute` and `Mount` -- so adding an
    `@application.websocket(...)` left the parity table green and the count unmoved (measured:
    `5 passed`). A websocket is a capability of this API by any reading, and a mount is a whole
    sub-application; neither is a thing the three-way difference may be blind to. They are keyed
    `WEBSOCKET <path>` and `MOUNT <path>`, which are not HTTP methods and cannot collide with
    one.
    """
    with _application() as application:
        return _surface_of(application)


def _surface_of(application: FastAPI) -> dict[str, object]:
    """The keying itself, over any application, so a test can drive it on one it built.

    Separate from `_route_entries` because the shipped application carries no `WebSocketRoute`
    and no `Mount`, so the branch that keys them is unreachable through it -- measured: deleting
    that branch left the whole file green while `_route_entries` was the only caller. A test
    that re-implemented the keying to prove it works would be a second statement of the same
    rule, which is what this file exists to stop.
    """
    found: dict[str, object] = {}
    for route in application.routes:
        path = getattr(route, "path", None)
        if path is None or path in FRAMEWORK_ROUTES:
            continue
        methods = getattr(route, "methods", None)
        if methods is None:
            kind = "MOUNT" if isinstance(route, Mount) else "WEBSOCKET"
            # `application.mount("/", ...)` is stored by starlette with `path == ""`, so the
            # root mount would key as `MOUNT ` with a trailing space and read as a typo. It is
            # the one mount this application actually has, so it is the one worth spelling.
            found[f"{kind} {path or '/'}"] = route
            continue
        for method in methods:
            if method not in {"HEAD", "OPTIONS"}:
                found[f"{method} {path}"] = route
    return found


def _routes() -> set[str]:
    return set(_route_entries())


DOMAIN_TYPE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

TYPE_SCAFFOLDING: Final[frozenset[str]] = frozenset(
    {
        "tuple",
        "list",
        "dict",
        "set",
        "frozenset",
        "Sequence",
        "Mapping",
        "Iterable",
        "None",
        "NoneType",
        "Optional",
        "Union",
        "Any",
        "str",
        "int",
        "float",
        "bool",
        "Annotated",
        "Final",
        "Literal",
        "typing",
        "openalpha_cn",
        "object",
        "JSONResponse",
        "Response",
        "starlette",
        "responses",
        "class",
        "fastapi",
    }
)
"""Names that carry no information about *which* capability an answer belongs to.

`JSONResponse` and `object` are in here for the same reason `dict` is: a route annotated with
one of them has declined to say what it returns, so there is nothing to hold its SDK twin
against. Those rows are counted rather than checked -- see
`test_a_route_and_its_sdk_twin_answer_with_the_same_domain_type`.
"""


def _domain_types(annotation: object) -> frozenset[str]:
    """The domain type names inside an annotation, with the scaffolding removed."""
    if annotation is None:
        return frozenset()
    text = annotation if isinstance(annotation, str) else str(annotation)
    return (
        frozenset(
            name.split(".")[-1] for name in DOMAIN_TYPE.findall(text.replace("openalpha_cn.", ""))
        )
        - TYPE_SCAFFOLDING
    )


def _answer_types(route: object) -> frozenset[str]:
    model = getattr(route, "response_model", None)
    if model is None:
        model = typing.get_type_hints(route.endpoint).get("return")  # type: ignore[attr-defined]
    return _domain_types(model)


def _sdk_answer_types(method: str) -> frozenset[str]:
    return _domain_types(typing.get_type_hints(getattr(OpenAlphaSDK, method)).get("return"))


def _comparable_pairs() -> list[tuple[str, frozenset[str], frozenset[str]]]:
    """Every parity row where **both** faces say what they answer with.

    A row where either side resolves to nothing after `TYPE_SCAFFOLDING` has no claim to check,
    so it is left out here and counted by
    `test_the_rows_this_type_check_cannot_constrain_are_counted_rather_than_skipped` instead --
    the two together are exactly the paired rows, which is what stops "not comparable" from
    becoming a quiet exemption.
    """
    entries = _route_entries()
    pairs: list[tuple[str, frozenset[str], frozenset[str]]] = []
    for route, (method, _) in PARITY.items():
        if method is None or route not in entries:
            continue
        served = _answer_types(entries[route])
        answered = _sdk_answer_types(method)
        if served and answered:
            pairs.append((route, served, answered))
    return pairs


def _sdk_methods() -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(OpenAlphaSDK)
        if not name.startswith("_") and (inspect.isfunction(member) or inspect.ismethod(member))
    }


def _cli_commands(application: typer.Typer | None = None, prefix: str = "") -> set[str]:
    application = cli_module.app if application is None else application
    found = {
        (prefix + (command.name or command.callback.__name__.replace("_", "-"))).strip()
        for command in application.registered_commands
        if command.callback is not None or command.name is not None
    }
    for group in application.registered_groups:
        if group.typer_instance is None:  # pragma: no cover - every group here has one
            continue
        name = group.name or group.typer_instance.info.name or ""
        found |= _cli_commands(group.typer_instance, f"{prefix}{name} ")
    return found


def test_every_shipping_route_has_a_row_in_the_parity_table() -> None:
    """A route added on one face and nowhere else is red here, naming the route.

    This is the assertion the row's prose could not make. `V2-P5-001` shipped
    `openalpha portfolio construct` and `OpenAlphaSDK.construct_portfolio` with no route and
    nothing noticed for four rows; `V2-P5-010` shipped a whole scheduling subsystem with no face
    and recorded the fact in a paragraph. Both directions are covered, because the equality below
    is an equality and not a subset.
    """
    assert _routes() == set(PARITY), (
        "the route table and the parity table disagree; add a row (with its gap reason) for a "
        "new route, or delete the row for a route that no longer exists"
    )


def test_every_named_sdk_method_and_cli_command_actually_exists() -> None:
    """A parity row is evidence only if both halves resolve.

    Without this the table degrades into the prose it replaced: a row naming
    `OpenAlphaSDK.validate_outcomes` (plural, and non-existent) would assert coverage that is not
    there, which is exactly the failure mode of the sentence in `V2-P5-013`'s own row.
    """
    sdk = _sdk_methods()
    commands = _cli_commands()
    named_sdk = {method for method, _ in PARITY.values() if method is not None}
    named_cli = {command for _, command in PARITY.values() if command is not None}

    assert named_sdk <= sdk, (
        f"parity names SDK methods that do not exist: {sorted(named_sdk - sdk)}"
    )
    assert named_cli <= commands, (
        f"parity names CLI commands that do not exist: {sorted(named_cli - commands)}"
    )


def test_an_exported_web_dir_does_not_change_the_surface_this_file_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build on disk and `OPENALPHA_WEB_DIR` exported must not add a row to the table.

    Now that `_surface_of` keys `Mount`, an application built with a web directory carries
    `MOUNT /` -- correctly, and `tests/unit/test_spa_addressability.py` is where that is a
    subject. Here it would be an extra route nobody declared, appearing only for developers who
    export the variable and never in CI. The `os.environ.pop` in `_application` is what stops
    it, and this test is what proved it has to be there: with `web_dir=None` alone, which is
    what stood there first, the assertion below fails.
    """
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setenv("OPENALPHA_WEB_DIR", str(build))

    assert not any(key.startswith("MOUNT ") for key in _routes())

    with tempfile.TemporaryDirectory() as directory:
        mounted = create_app(runtime_dir=Path(directory) / "runtime", web_dir=build)
    assert "MOUNT /" in _surface_of(mounted), (
        "this test proves nothing unless a mounted application really does carry MOUNT /"
    )


def test_a_route_and_its_sdk_twin_answer_with_the_same_domain_type() -> None:
    """A row's two halves must be about one capability, not merely both exist.

    `V2-P5-035`. `test_every_named_sdk_method_and_cli_command_actually_exists` checks a
    **subset**: every name in the table resolves to something real. Measured, exchanging
    `POST /api/v1/screen`'s SDK method with `GET /api/v1/watchlist`'s left the whole file at
    `5 passed` -- both names still existed, the keys were untouched, the derived gap sets were
    unchanged and every count held. The table said which capability each route reaches, and
    nothing at all checked that it was telling the truth.

    Two things this deliberately is not. It is **not** a name rule: the two faces name things
    by opposite conventions (`report_list`/`list_reports`, `panel_gate`/`panel_clearance`), and
    measured, the route endpoint name equals the SDK method name in 6 of 48 rows. And it is not
    a call-graph check: `OpenAlphaSDK` is in-process, so a method reaches its capability
    directly rather than through the route, and there is no edge between them to follow.

    What is left, and what a swap actually breaks, is the **answer's type**. A route that
    declares a response model and an SDK method that declares a return annotation are both
    saying what the capability produces, and two faces of one capability produce the same
    thing. Rows where either side declares nothing that survives `TYPE_SCAFFOLDING` are
    unconstrained -- counted by the test below, never silently skipped.

    **The surviving swap is reported rather than hidden.** Two methods on one path that answer
    with the same domain type are indistinguishable here: measured, exchanging
    `GET /api/v1/reports`'s `list_reports` with `POST /api/v1/reports`'s `create_report` leaves
    this file at `9 passed`, because both answer about a report. Nothing in a type separates
    "list them" from "create one", and the only thing that would is an equivalence test at the
    capability's own integration module -- which is the boundary this file's own header already
    draws between reachability and equivalence. What is closed is the swap **across**
    capabilities, which is every swap that changes what the row is about.
    """
    mismatched = sorted(
        f"{route} answers {sorted(served)} but its declared twin answers {sorted(answered)}"
        for route, served, answered in _comparable_pairs()
        if not served & answered
    )

    assert mismatched == [], (
        "a parity row pairs a route with an SDK method that answers about something else; "
        "either the row is wrong or the two faces have drifted apart"
    )


def test_the_rows_this_type_check_cannot_constrain_are_counted_rather_than_skipped() -> None:
    """The residue of the check above, as a number, so it cannot grow quietly.

    A route annotated `-> JSONResponse` and an SDK method annotated `-> None` each decline to
    say what they produce, so their row has nothing to hold. Seventeen rows are in that state
    today. Left uncounted, the check above would weaken every time somebody wrote a route
    without a response model -- the shape of a floor, which `V2-P4-038` measured the worth of.

    The number going **down** is a row that started declaring its type, and is as red as one
    going up: re-measure and write the new figure, which is the point at which somebody reads
    this docstring.
    """
    entries = _route_entries()
    paired = [
        route for route, (method, _) in PARITY.items() if method is not None and route in entries
    ]

    assert len(paired) == 37
    assert len(paired) - len(_comparable_pairs()) == 17


def test_a_websocket_or_a_mount_is_a_surface_this_table_can_see() -> None:
    """`V2-P5-035`'s other half: `_routes()` skipped every route object with no `methods`.

    `WebSocketRoute` and `Mount` both have `methods is None`, so both fell out of the table
    before it was compared to anything. Measured: adding an `@application.websocket("/api/v1/
    stream")` to `create_app` left this file at `5 passed`, which is a whole capability arriving
    on the REST face with the parity audit silent.

    Driven on an application built here rather than by mutating `create_app`, so the extraction
    is what is tested and the shipped application is not disturbed. The shipped one is asserted
    to carry neither, which is the other direction: `MOUNT /` appears the moment a build is
    served, and `_application()` clears `OPENALPHA_WEB_DIR` so that it does not appear by
    accident on a developer's machine and vanish in CI.
    """
    probe = FastAPI()

    @probe.websocket("/api/v1/stream")
    async def stream(websocket: object) -> None:  # pragma: no cover - never connected to
        raise NotImplementedError

    probe.mount("/static", FastAPI())

    keys = set(_surface_of(probe))

    assert keys == {"WEBSOCKET /api/v1/stream", "MOUNT /static"}
    assert not any(key.startswith(("WEBSOCKET ", "MOUNT ")) for key in _routes())


def test_each_route_without_an_sdk_method_declares_why_it_has_none() -> None:
    """The gap set is derived from the table rather than restated beside it.

    Two sets that must agree, computed from opposite ends: the routes whose SDK half is `None`,
    and the routes `WITHOUT_SDK_REASONS` gives a reason for. A gap closed without its reason
    being deleted, or a reason written for a gap that no longer exists, is red.
    """
    unreached = {route for route, (method, _) in PARITY.items() if method is None}

    assert unreached == set(WITHOUT_SDK_REASONS)
    assert all(WITHOUT_SDK_REASONS.values()), (
        "a declared gap with an empty reason is not a decision"
    )


def test_the_sdk_and_cli_surfaces_beyond_the_route_table_are_declared() -> None:
    """The other two thirds of the three-way difference, held to the same standard.

    An SDK method or CLI command that reaches no route is not automatically a defect -- a
    renderer is not a capability, and `openalpha serve` cannot be a route on the server it
    starts. What is a defect is one nobody decided about, so each is named with its reason and
    the sets are equalities.
    """
    reached_sdk = {method for method, _ in PARITY.values() if method is not None}
    reached_cli = {command for _, command in PARITY.values() if command is not None}

    assert _sdk_methods() - reached_sdk == set(SDK_ONLY)
    assert _cli_commands() - reached_cli == set(CLI_ONLY)
    assert all(SDK_ONLY.values())
    assert all(CLI_ONLY.values())


def test_the_measured_surface_counts_are_the_ones_this_file_was_written_against() -> None:
    """`REGISTRY_ENTRY_COUNTS`' arrangement, applied to the three product faces.

    A number that moves goes red naming which face moved, so a change to any surface has to pass
    through this file rather than around it. Measured on `2746663` before `V2-P5-013`: 43 routes,
    48 SDK methods, 25 CLI commands, with 9 REST-only capabilities.

    `V2-P5-022` moved three of the five by one each -- `GET /api/v1/reports/{report_id}/export`,
    `OpenAlphaSDK.export_report`, `openalpha report export` -- and left `without_sdk` and
    `rest_only` alone, which is the point: the row shipped one capability on all three faces
    rather than one face and two entries in the gap table.
    """
    measured = {
        "routes": len(_routes()),
        "sdk_methods": len(_sdk_methods()),
        "cli_commands": len(_cli_commands()),
        "without_sdk": len(WITHOUT_SDK_REASONS),
        "rest_only": sum(1 for value in PARITY.values() if value == (None, None)),
    }

    assert measured == {
        "routes": 48,
        "sdk_methods": 55,
        "cli_commands": 33,
        "without_sdk": 11,
        "rest_only": 9,
    }
