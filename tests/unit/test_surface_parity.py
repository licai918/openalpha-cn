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
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Final

import typer

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


def _routes() -> set[str]:
    with tempfile.TemporaryDirectory() as directory:
        application = create_app(runtime_dir=Path(directory) / "runtime")
        found = set()
        for route in application.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if path is None or methods is None or path in FRAMEWORK_ROUTES:
                continue
            found.update(
                f"{method} {path}" for method in methods if method not in {"HEAD", "OPTIONS"}
            )
    return found


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
    """
    measured = {
        "routes": len(_routes()),
        "sdk_methods": len(_sdk_methods()),
        "cli_commands": len(_cli_commands()),
        "without_sdk": len(WITHOUT_SDK_REASONS),
        "rest_only": sum(1 for value in PARITY.values() if value == (None, None)),
    }

    assert measured == {
        "routes": 47,
        "sdk_methods": 54,
        "cli_commands": 32,
        "without_sdk": 11,
        "rest_only": 9,
    }
