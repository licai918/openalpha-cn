"""FastAPI application for OpenAlpha CN's versioned public HTTP surface."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openalpha_cn import __version__
from openalpha_cn.agents.base import AgentResult
from openalpha_cn.agents.committee import DeliberationCommittee, DeliberationOutcome
from openalpha_cn.backtest.event_study import EventStudy, EventStudyReport, EventStudyRequest
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.factor_experiment import FactorExperimentError, open_experiment
from openalpha_cn.backtest.factor_ic import ICMethod
from openalpha_cn.backtest.multi_day import (
    PortfolioBacktestReport,
    PortfolioBacktestRunner,
    PortfolioBacktestStep,
)
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
    PortfolioTransition,
)
from openalpha_cn.backtest.replay import ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator
from openalpha_cn.config import load_config
from openalpha_cn.domain.factor import FactorError, FactorNote
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.validation import ValidationResult
from openalpha_cn.evidence.service import (
    EvidenceBuildRequest,
    EvidenceBuildResponse,
    build_evidence,
    parse_serialized_evidence,
)
from openalpha_cn.factor_view import (
    FactorViewError,
    experiment_view,
    factor_catalog,
    factor_entry,
    factor_request,
    run_factor_experiment,
)
from openalpha_cn.logging_setup import configure_logging
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import PanelDoctorError, panel_health_report
from openalpha_cn.panel_gate import DependencyRequest, PanelGateError, require_datasets
from openalpha_cn.panel_view import (
    PanelViewError,
    clearance_payload,
    dataset_readiness,
    health_report_payload,
    panel_request,
    panel_store,
    readiness_payload,
)
from openalpha_cn.product.research import (
    ResearchReport,
    ResearchReportFactory,
    ResearchScreener,
    ScreeningCriteria,
    ScreeningResult,
    WatchlistEntry,
)
from openalpha_cn.providers.base import utc_now
from openalpha_cn.runtime.batch import (
    MAX_BATCH_ITEMS,
    MAX_BATCH_WORKERS,
    BatchProgressEvent,
    BatchResearchService,
    BatchResearchTask,
)
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.runtime.provenance import compute_config_digest, resolve_code_commit
from openalpha_cn.shortlist_view import (
    ShortlistViewError,
    run_shortlist,
    shortlist_components,
    shortlist_evidence,
    shortlist_request,
    shortlist_view,
)
from openalpha_cn.storage.factor_experiments import ExperimentStoreError
from openalpha_cn.storage.recovery import RunRecoveryState


def _resolved_code_commit(explicit: str | None) -> str:
    """Return `explicit` verbatim when given; otherwise resolve a real commit.

    Mirrors `cli.py`'s `_resolved_code_commit`: a browser (or any other HTTP caller)
    cannot know the server's own git commit, so this only ever fills a gap the caller
    left open -- it never touches git when a value was genuinely supplied, which is
    what keeps this endpoint byte-for-byte identical to `OpenAlphaSDK.run_research`
    for the same explicit input (`test_rest_sdk_clock_parity.py`).
    """
    return explicit if explicit is not None else resolve_code_commit()


def _resolved_config_digest(explicit: str | None) -> str:
    """Return `explicit` verbatim when given; otherwise digest the effective config.

    Mirrors `cli.py`'s `_resolved_config_digest`, including calling `load_config()`
    fresh rather than reusing `create_app()`'s closed-over `config`: this is a
    request-time resolution (the same field can be omitted by any caller at any time),
    not a startup-time one.
    """
    return explicit if explicit is not None else compute_config_digest(load_config())


def _fill_missing_provenance(data: Any) -> Any:
    """`model_validator(mode="before")` body shared by `ResearchApiRequest` and
    `ReplayApiRequest`: resolve `code_commit`/`config_digest` server-side when a caller
    omits them (missing key or explicit JSON `null`), and pass an explicitly supplied
    value straight through untouched -- including an invalid one, so
    `ResearchRunRequest`/`ReplayApiRequest`'s own field validation (`min_length`,
    `pattern`) reports it accurately instead of this hook silently discarding it.

    This is the fix for the critical finding on task 17: `web/src/api/client.ts` used
    to POST the fabricated literals `code_commit: "web-development"` and
    `config_digest: "0".repeat(64)` on every request because these fields had no
    server-side fallback -- a browser genuinely cannot know the server's own commit or
    effective config, so only the server can honestly fill them in.
    """
    if not isinstance(data, dict):
        return data
    filled = dict(data)
    filled["code_commit"] = _resolved_code_commit(filled.get("code_commit"))
    filled["config_digest"] = _resolved_config_digest(filled.get("config_digest"))
    return filled


class ReplayApiRequest(BaseModel):
    """Inputs required to execute a frozen corpus through the replay API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus: ReplayCorpus
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    random_seed: int

    @model_validator(mode="before")
    @classmethod
    def resolve_missing_provenance(cls, data: Any) -> Any:
        return _fill_missing_provenance(data)


class ResearchApiRequest(ResearchRunRequest):
    """Research request that safely accepts serialized evidence output."""

    @model_validator(mode="before")
    @classmethod
    def resolve_missing_provenance(cls, data: Any) -> Any:
        return _fill_missing_provenance(data)

    @field_validator("evidence", mode="before")
    @classmethod
    def verify_serialized_evidence(cls, value: Any) -> Any:
        try:
            return parse_serialized_evidence(value)
        except ValueError:
            return value


class OutcomeApiRequest(BaseModel):
    """A serialized research result plus its later observed outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research: dict[str, Any]
    observation: OutcomeObservation


class PortfolioApiRequest(BaseModel):
    """One stateless portfolio transition request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PortfolioState
    order: PortfolioOrder
    market: MarketBar
    limits: PortfolioLimits = PortfolioLimits()


class BatchSubmitRequest(BaseModel):
    """A bounded set of immutable research requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1, max_length=128)
    requests: tuple[ResearchApiRequest, ...] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)
    max_concurrency: int = Field(default=4, ge=1, le=MAX_BATCH_WORKERS)


class PortfolioBacktestRequest(BaseModel):
    """Initial state and ordered daily transitions for a portfolio backtest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial: PortfolioState
    steps: tuple[PortfolioBacktestStep, ...] = Field(min_length=1)
    limits: PortfolioLimits = PortfolioLimits()


class DeliberationApiRequest(BaseModel):
    """Aggregate signal plus agent cases for optional committee review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: SignalFrame
    agent_results: tuple[AgentResult, ...] = ()


class ScreeningApiRequest(BaseModel):
    """Serialized verified research results plus screening criteria."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research: tuple[dict[str, Any], ...]
    criteria: ScreeningCriteria = ScreeningCriteria()


class ReportApiRequest(BaseModel):
    """One serialized research result to turn into an immutable report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research: dict[str, Any]


class SecurityHeadersMiddleware:
    """Apply browser hardening headers and reject declared oversized bodies."""

    _HEADERS = (
        (
            b"content-security-policy",
            (
                b"default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
                b"form-action 'self'; img-src 'self' data:; "
                b"script-src 'self'; style-src 'self'; connect-src 'self'"
            ),
        ),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    )

    def __init__(self, app: ASGIApp, *, max_request_bytes: int) -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
                await response(scope, receive, send)
                return
            if content_length > self.max_request_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body exceeds configured limit."},
                )
                await response(scope, receive, send)
                return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", ()), *self._HEADERS]
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def _parse_research_result(payload: dict[str, Any]) -> ResearchRunResult:
    """Rebuild a strict result while verifying its content-derived identifiers.

    Every computed identifier the response carried has to be stripped before validation and
    re-derived afterwards, because each contract is `extra="forbid"` and would otherwise
    reject its own serialized form. `V2-P4-025` adds a third such identifier --
    `RunManifest.run_manifest_id` -- and it is verified rather than merely dropped, for the
    same reason `signal_id` and `decision_id` are: a caller that could hand back an
    unverified manifest address could hand back one that does not describe the manifest
    beside it, which is the whole thing the address is for.
    """
    clean = {**payload}
    claimed_signal_id = clean.get("signal", {}).get("signal_id")
    claimed_decision_id = clean.get("decision", {}).get("decision_id")
    claimed_manifest_id = clean.get("manifest", {}).get("run_manifest_id")

    signal = {**clean["signal"]}
    signal.pop("signal_id", None)
    clean["signal"] = signal

    decision = {**clean["decision"]}
    decision.pop("decision_id", None)
    clean["decision"] = decision

    manifest = {**clean["manifest"]}
    manifest.pop("run_manifest_id", None)
    clean["manifest"] = manifest

    agent_results = []
    for item in clean.get("agent_results", []):
        agent = {**item}
        agent_signal = {**agent["signal"]}
        agent_signal.pop("signal_id", None)
        agent["signal"] = agent_signal
        agent_results.append(agent)
    clean["agent_results"] = agent_results

    result = ResearchRunResult.model_validate(clean)
    if claimed_signal_id != result.signal.signal_id:
        raise ValueError("research signal_id does not match its content")
    if claimed_decision_id != result.decision.decision_id:
        raise ValueError("research decision_id does not match its content")
    if claimed_manifest_id != result.manifest.run_manifest_id:
        raise ValueError("research run_manifest_id does not match its content")
    return result


PANEL_HTTP_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "answered": 200,
        "blocked": 409,
        "panel_unreadable": 409,
        "bad_request": 422,
        "internal_error": 500,
    }
)
"""What the three `GET /api/v1/panel/*` endpoints do about each situation, as one table.

`cli.py`'s `PanelExit` is the sibling of this and the reasoning is shared: a caller has
different remedies available and only the envelope to pick between them, so collapsing them
into "2xx and non-2xx" makes a refused read indistinguishable from a mistyped request.

**The two tables are siblings, not twins, and exactly one row does not correspond.** `panel
doctor` exits `PanelExit.unhealthy` (1) when the report is not `is_clean`; `GET
/api/v1/panel/health` answers `200` for that same panel, and so does `/panel/readiness` for a
blocked dataset. There is no HTTP status code anywhere in this table that means what `panel
doctor`'s exit 1 means. A monitor built by reading a status code alone will therefore never
fire on a sick panel -- and the endpoint being called `/panel/health` in an app that also
serves `GET /health`, a real liveness probe, makes that the easy mistake to make rather than
an exotic one. The HTTP equivalents of `panel doctor`'s exit 1 are, in the body,
`is_clean == false` / `all_ready == false` / a non-zero `counts_by_severity`, or, as a status
code, `409` from `/api/v1/panel/gate` -- which is a *different question* (see the
`answered`/`blocked` rows below) and will also refuse some healthy panels.

- **`answered` (200)** -- this endpoint answered. For `/panel/health` and `/panel/readiness`
  that is *all* it says: those two are reports, and a report that found a `blocking` finding
  has succeeded at its job. The verdict is in the body, first-class and unmissable
  (`is_clean`, `counts_by_severity` total over all three severities, `all_ready`, `state`),
  because there is no permission in a report for a status code to grant. For `/panel/gate`,
  `200` says something stronger -- "you may read" -- which is why it is not what a refusal
  wears.
- **`blocked` (409)** -- the gate ran and refused. A conflict with the current state of the
  panel, which is exactly what RFC 9110 reserves `409` for, and the body still carries every
  block, notice and unverified check: a caller told `409` and nothing else cannot act on it.
  An endpoint that ran the gate, was refused and still answered `200` would be no gate at all,
  which is the "empty success" `V2-P1-013` exists to make unavailable, reappearing one layer
  up.
- **`panel_unreadable` (409)** -- the same class of fact one step earlier: the exchange
  calendar this request asked to be judged against is not in the store, so no verdict exists
  to report. `409` rather than `404`, because nothing about the *endpoint* is missing.
- **`bad_request` (422)** -- the request cannot be put at all: a dataset with no declared
  publication cadence (`PanelDoctorError`), a request naming no dataset (`PanelGateError`,
  `PanelRequestError`), a naive `as_of`, an exchange name no store could hold. Distinct from
  `409` for `PanelExit`'s reason -- no amount of re-fetching fixes it -- and `422` because
  that is already this app's code for a well-formed request it cannot accept (`POST
  /api/v1/screen`, `/reports`, `/backtests/validate`).
- **`internal_error` (500)** -- **the endpoint itself broke.** Nothing was judged: a fault no
  branch here anticipated, such as a catalog file that is not a DuckDB database at all.
  Written down for the reason `PanelExit.internal_error` is: without it a reader of this table
  would conclude that every non-2xx a panel endpoint can produce means something about the
  panel or about the request, and treat a `500` as one of the two. It is not raised anywhere
  in this module -- Starlette's own handler produces it -- so the row records a code that is
  already spoken for, exactly as `cli.CLICK_USAGE_EXIT_CODE` does one channel over.

## `409` carries two body schemas, and they are told apart by one key

`blocked` and `panel_unreadable` share a status code deliberately (both are "the panel's state
stands in the way") but **not** a body, and a client that switched on the code alone and read
`json()["blocks"]` would work on the first and raise `KeyError` on the second:

- a **verdict** body is the flat `panel_view.clearance_payload` -- `is_blocked`, `blocks`,
  `cleared`, `report`, ... -- and never has a `detail` key;
- a **refusal** body is `{"detail": {"reason": ..., "message": ...}}`, where `reason` is the
  row of this table it was enveloped by (`panel_unreadable`, `bad_request`). The two bodies
  share no key at all, and `detail.reason` is the discriminator to switch on.

A third shape exists and is not this module's: FastAPI's own parameter validation answers
`422` with `detail` as a *list* of error objects, for a missing `dataset` or an unparseable
`year`. `isinstance(body["detail"], dict)` separates that from a panel refusal.
`docs/api/http.md` states all three.

`message` is the fault's `disclosable` text and not `str(error)`: the store's filesystem
location is configuration of this process, and a refusal that echoed it would answer a
question about the deployment to anyone who could reach the port.

**A `notice` never reaches the non-2xx half, and the argument is measurement.** `V2-P1-011`
drove a real 53-security corpus end to end: `ambiguous_filing` fires on 8.15% of `income`'s
filings, 1.29% of `balancesheet`'s, 15.80% of `cashflow`'s and 13.70% of `fina_indicator`'s.
A face that refused those would refuse every honest financial panel, be worked around in the
first client that met it, and protect nothing. The notices ride on the clearance instead, so
a cleared caller still sees them.
"""


FACTOR_HTTP_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "answered": 200,
        "blocked": 409,
        "panel_unreadable": 409,
        "bad_request": 422,
        "conflict": 409,
        "not_found": 404,
        "internal_error": 500,
    }
)
"""What `POST /api/v1/factors/run` and the two `GET /api/v1/factors/experiments*` routes do about
each situation, as one table.

`PANEL_HTTP_STATUS`'s sibling and `cli.FactorExit`'s, and the reasoning is shared: a caller has
different remedies available and only the envelope to pick between them, so collapsing them into
"2xx and non-2xx" makes a refused run indistinguishable from a mistyped request.

**The row that carries this issue is `blocked`, and it must not be a 2xx.** A run whose three
stored tiers were not built at the same instants produces **no artifact**. An endpoint that
answered `200` with an empty body, or with an experiment whose neutralised row measured nothing,
would be the empty success `V2-P1-013` exists to make unavailable, one plane up -- and the
neutralised row is the one `V2-P3-014`'s acceptance criterion is decided on, so a client that
proceeded on it would report "the factor survives neutralisation" about a tier that was never
built. `409` rather than `404` or `422`: nothing about the endpoint is missing and nothing about
the request is malformed, it is a conflict with the current state of the panel, which is what RFC
9110 reserves `409` for.

- **`answered` (200)** -- the experiment was assembled and sealed. The body carries the whole
  sealed document; see `factor_view.experiment_view`.
- **`blocked` (409)** -- `FactorRunBlockedError`. The range holds no stored cross section, or a
  tier is missing at instants the raw tier has, or a tier's studies refused the cross section
  they were handed. Every one of them names what is missing, because a caller told `409` and
  nothing else cannot act on it.
- **`panel_unreadable` (409)** -- `FactorPanelUnreadableError`: a partition this run needs is
  missing, damaged, stale or holds rows that were not knowable at the stated `as_of`. The same
  class of fact one step earlier, which is why `/api/v1/panel/*` shares this arrangement.
- **`bad_request` (422)** -- `FactorRequestError`: a factor no registry declares, a range that
  runs backwards, an `--as-of` before `--end`, a floor outside `(0, 1]`. Distinct from `409` for
  `PanelExit`'s reason -- no amount of building fixes it -- and `422` because that is already this
  app's code for a well-formed request it cannot accept.
- **`conflict` (409)** -- the document store refused: a second, different answer under a held
  `experiment_id`, which `refuse_a_restated_experiment` says is a build that did not reproduce.
  It shares `409` with the two rows above and is told apart by `detail.reason`, exactly as
  `PANEL_HTTP_STATUS`' two `409`s are.
- **`not_found` (404)** -- `GET /api/v1/factors/experiments/{experiment_id}` and nothing is held
  under that key. The only `404` on this face: a *run* never answers it, because a run that found
  nothing to read is a statement about the panel rather than about a missing resource.
- **`internal_error` (500)** -- the endpoint itself broke. Not raised anywhere in this module;
  the row records a code that is already spoken for, exactly as `cli.CLICK_USAGE_EXIT_CODE` does.

**A refusal body is `{"detail": {"reason": ..., "message": ...}}`**, `_panel_detail`'s shape
unchanged, and `message` is the fault's `disclosable` text rather than `str(error)`: the store's
filesystem location is configuration of this process, and a refusal that echoed it would answer a
question about the deployment to whoever could reach the port.
"""


def _factor_refusal(error: FactorViewError) -> HTTPException:
    """One `FactorViewError`, enveloped by the row of `FACTOR_HTTP_STATUS` it names.

    Looked up by `error.reason` rather than switched on by exception type, `_panel_refusal`'s rule
    and its reason: a fault added to `factor_view.py` with no row here raises `KeyError` at this
    boundary -- a `500` that says the table is incomplete -- instead of being quietly enveloped as
    whichever branch an `isinstance` chain happened to end on.
    `tests/unit/test_factor_view_layering.py::
    test_every_factor_view_fault_has_a_row_in_both_channel_tables` asserts every subclass's
    `reason` is a key of this table and of `cli.FACTOR_EXIT`, so that `KeyError` is unreachable in
    practice.
    """
    return HTTPException(
        status_code=FACTOR_HTTP_STATUS[error.reason],
        detail=_panel_detail(error.reason, error.disclosable),
    )


class FactorRunApiRequest(BaseModel):
    """Every declared parameter of one factor run, and not one of them has a default.

    `factor_view.factor_request` refuses a default for each of these and this model does not
    reinstate one: a browser that omitted `min_securities` would otherwise get a floor nobody
    chose, and the four upstream specs each argue that a decision moving the answers is a decision
    somebody records making. `note` is the single exception and it is prose -- out of every
    content address, exactly as `FactorExperimentRecord.note` is.

    The two `Decimal` fields arrive as JSON strings or numbers and pydantic coerces them; they are
    `Decimal` rather than `float` because `QuantilePortfolioSpec.position_capital` and
    `TradeabilitySpec.participation_cap` are, and money that round-trips through a binary float is
    money that does not add up.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: str
    transform: str
    neutralization: str
    start: date
    end: date
    as_of: datetime
    exchange: str
    horizon: str
    ic_method: ICMethod
    min_securities: int
    min_as_ofs: int
    group_count: int
    min_securities_per_group: int
    position_capital: Decimal
    min_periods: int
    participation_cap: Decimal
    min_rebalances: int
    redundancy_threshold: float
    retention_floor: float
    code_commit: str | None = None
    """The commit the studies ran at. `None` -- or omitted -- resolves server-side.

    `_resolved_code_commit`'s argument unchanged and for its measured reason: a browser cannot
    know the server's own git commit, so a face without this fallback gets
    `code_commit: "web-development"` invented by the client, which is the critical finding task 17
    closed. An explicitly supplied value passes through untouched, including an invalid one, so
    `factor_request`'s own `min_length` reports it accurately instead of this hook discarding it.
    """
    note: str | None = None
    """Prose about this experiment, carried on the record and out of every digest."""


SHORTLIST_HTTP_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "answered": 200,
        "refused": 409,
        "blocked": 409,
        "panel_unreadable": 409,
        "bad_request": 422,
        "internal_error": 500,
    }
)
"""What `POST /api/v1/shortlists/run` does about each situation, as one table.

`PANEL_HTTP_STATUS`' and `FACTOR_HTTP_STATUS`' sibling, and the reasoning is shared: a caller has
different remedies available and only the envelope to pick between them.

**The row this issue exists for is `refused`, and it is the only row here with no `factor_view`
counterpart.** It is *not* a fault -- nothing went wrong, the gate ran and said no -- and it
carries the **verdict** body rather than a `detail` object, exactly as `GET /api/v1/panel/gate`'s
`blocked` row does one plane over. `V2-P4-023`'s gate tells "this list was refused" from "this
list is legitimately empty" inside the library by making `bool()` raise on a `ShortlistClearance`;
at this boundary there is only JSON, so the distinction is re-made in two keys:

- a **refused** list answers `409` with `"is_blocked": true` and `"admitted": null`, and every
  bar it missed under `blocks` with both sides of the comparison;
- an **admitted** list answers `200` with `"is_blocked": false` and `"admitted": [...]`, and that
  array may be **empty** -- a shortlist every name of which came back unresearched, under a
  `minimum_researched_ratio` this caller declared it could live with.

`null` and `[]` are therefore two different answers on this route. The product acceptance measured
them collapsed into one (`{"items":[],"excluded":[],"reviewed":0}` for both), which is the defect
`V2-P4-033` was filed for.

- **`answered` (200)** -- the gate ran and admitted the list. `shortlist_view`'s envelope.
- **`refused` (409)** -- the gate ran and refused it. A conflict with the current state of the
  panel and of this run's evidence, which is what RFC 9110 reserves `409` for. Not an error, and
  the body is a verdict rather than a `detail`.
- **`blocked` (409)** -- `ShortlistRunBlockedError`: no component has a stored cross section at or
  before the `as_of`, or the declared components disagree about which instant they share, or a
  supplied signal names a security the funnel did not shortlist. A refusal body.
- **`panel_unreadable` (409)** -- `ShortlistPanelUnreadableError`: a partition this screen needs is
  missing, damaged, stale or holds rows that were not knowable at the stated `as_of`.
- **`bad_request` (422)** -- `ShortlistRequestError`: a factor no registry declares, a weight that
  is not positive, a processed-tier screen with no transform, a naive `as_of`.
- **`internal_error` (500)** -- the endpoint itself broke. Not raised anywhere in this module; the
  row records a code that is already spoken for, exactly as `cli.CLICK_USAGE_EXIT_CODE` does.

**`409` therefore carries two body schemas here, and `detail` is the discriminator** --
`PANEL_HTTP_STATUS`' own arrangement, unchanged: a verdict body has `is_blocked` and no `detail`
key, and a refusal body is `{"detail": {"reason": ..., "message": ...}}`. A client that read
`json()["blocks"]` on every `409` would raise `KeyError` on the second, so it switches on
`"detail" in body` first.
"""


def _shortlist_refusal(error: ShortlistViewError) -> HTTPException:
    """One `shortlist_view` fault, enveloped by the row of `SHORTLIST_HTTP_STATUS` it names.

    Looked up by `error.reason` rather than switched on by exception type, `_factor_refusal`'s rule
    and its reason: a fault added to `shortlist_view.py` with no row here raises `KeyError` at this
    boundary -- a `500` that says the table is incomplete -- instead of being quietly enveloped as
    whichever branch an `isinstance` chain happened to end on.
    """
    return HTTPException(
        status_code=SHORTLIST_HTTP_STATUS[error.reason],
        detail=_panel_detail(error.reason, error.disclosable),
    )


class ShortlistComponentApiRequest(BaseModel):
    """One factor's contribution to the declared composite: the handle, and a weight.

    A model rather than a free-form object so that a body naming `weights` or `factor_id` is
    refused by FastAPI's own `422` with the offending key, rather than reaching
    `shortlist_components` and being reported as a missing one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: str
    weight: float


class ShortlistRunApiRequest(BaseModel):
    """Every declared parameter of one shortlist run, and only two of them have a default.

    `shortlist_view.shortlist_request` refuses a default for each of the rest and this model does
    not reinstate one: a browser that omitted `minimum_researched_ratio` would otherwise get a bar
    nobody chose on the one question this route exists to answer.

    The two with defaults are the two that are genuinely absent rather than unstated: `transform`
    and `neutralization` are `None` for a raw-tier screen (which reads the factor's own stored
    values and applies nothing), and `evidence` is empty for the ordinary first run, where the
    shortlist says which names are worth researching and nothing has been researched yet.

    `position_capital` arrives as a JSON string or number and pydantic coerces it; it is `Decimal`
    rather than `float` because `ShortlistSpec.position_capital` is -- it decides
    `below_board_minimum`, and money that round-trips through a binary float is money that does not
    add up.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    components: tuple[ShortlistComponentApiRequest, ...]
    tier: str
    shortlist_size: int
    position_capital: Decimal
    as_of: datetime
    years: tuple[int, ...]
    exchange: str
    horizon: str
    minimum_tradable_ratio: float
    minimum_researched_ratio: float
    maximum_ranking_age_days: int
    code_commit: str | None = None
    """The commit this screen ran at. `None` -- or omitted -- resolves server-side.

    `FactorRunApiRequest.code_commit`'s argument unchanged and for its measured reason: a browser
    cannot know the server's own git commit, so a face without this fallback gets one invented by
    the client. An explicitly supplied value passes through untouched, including an invalid one,
    so `shortlist_request`'s own length check reports it accurately.
    """
    config_digest: str | None = None
    """The configuration this screen ran under. `None` resolves server-side.

    `code_commit`'s rule and its reason, one field over.
    """
    transform: str | None = None
    neutralization: str | None = None
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """The evidence plane's answers about the shortlisted names, keyed by subject.

    `dict[str, Any]` rather than a typed `SignalFrame`, which is `OutcomeApiRequest.research`'s own
    shape and its measured reason: `SignalFrame` is `extra="forbid"` with a computed `signal_id`,
    so a typed field here would **reject the serialized form this service itself hands out**.
    `shortlist_view.shortlist_evidence` strips that identifier and verifies it against the frame's
    own content, which is `_parse_research_result`'s rule applied to the one contract this route
    receives.

    Empty by default, which is the ordinary first run: the shortlist says which names are worth an
    evidence run, and nothing has been researched yet.
    """


def _panel_detail(reason: str, message: str) -> dict[str, str]:
    """The body every panel refusal carries, whatever its status code.

    An object rather than a bare string, because `409` carries two incompatible schemas and
    `reason` is what tells them apart -- see `PANEL_HTTP_STATUS`. A verdict body never has a
    `detail` key, so the presence of this object *is* "no verdict was reached".
    """
    return {"reason": reason, "message": message}


def _panel_bad_request(error: Exception) -> HTTPException:
    """A request the panel plane cannot put, as this app's own code for that.

    For the two the panel plane raises without a `reason` of their own -- `PanelDoctorError`
    and `PanelGateError`, which predate this face and are not `PanelViewError`s. Their
    messages name a dataset or an override, never a path.
    """
    return HTTPException(
        status_code=PANEL_HTTP_STATUS["bad_request"],
        detail=_panel_detail("bad_request", str(error)),
    )


def _panel_refusal(error: PanelViewError) -> HTTPException:
    """One `PanelViewError`, enveloped by the row of `PANEL_HTTP_STATUS` it names.

    Looked up by `error.reason` rather than switched on by exception type: a fault added to
    `panel_view.py` with no row here raises `KeyError` at this boundary -- a `500` that says
    the table is incomplete -- instead of being quietly enveloped as whichever branch an
    `isinstance` chain happened to end on. `tests/unit/test_panel_view.py` asserts every
    subclass's `reason` is a key of the table, so that `KeyError` is unreachable in practice.
    """
    return HTTPException(
        status_code=PANEL_HTTP_STATUS[error.reason],
        detail=_panel_detail(error.reason, error.disclosable),
    )


def _panel_query(
    runtime_dir: Path,
    *,
    dataset: Sequence[str],
    year: Sequence[int],
    session: Sequence[date],
    index_code: Sequence[str],
    as_of: datetime,
    exchange: str,
    calendar: bool,
) -> tuple[PanelStore, DependencyRequest]:
    """Resolve one panel request, mapping the two pre-verdict faults onto the table above.

    Shared by all three endpoints so they cannot come to ask three different questions of one
    store -- which is what makes `test_panel_interfaces.py`'s "the health endpoint and the gate
    disagree and both are right" a statement about the two *verdicts* rather than about two
    requests that differed.
    """
    store = panel_store(runtime_dir)
    try:
        request = panel_request(
            store,
            datasets=dataset,
            years=year,
            sessions=session,
            index_codes=index_code,
            as_of=as_of,
            exchange=exchange,
            with_calendar=calendar,
        )
    except PanelViewError as error:
        raise _panel_refusal(error) from error
    return store, request


_PANEL_DATASET_QUERY = Query(
    description=(
        "A dataset to assess, repeatable. Nothing is inferred: this is the caller's own "
        "statement of what should be there."
    )
)
_PANEL_YEAR_QUERY = Query(
    description=(
        "A partition year to assess, repeatable. Deliberately the caller's assertion of what "
        "should be present rather than a reading of what is -- passing the stored years would "
        "make partition_missing unreachable by construction."
    )
)
_PANEL_SESSION_QUERY = Query(
    description=(
        "An ISO-8601 session the day-level cross-checks run on, repeatable. Not inferred: "
        "'check every session' is a whole-corpus scan and 'check the last one' is a guess."
    )
)
_PANEL_INDEX_QUERY = Query(description="An index code index_weight is assessed against.")


def create_app(
    *,
    runtime_dir: Path | None = None,
    web_dir: Path | None = None,
    max_request_bytes: int | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    """Create an isolated application instance for serving and tests.

    Reads `OPENALPHA_RUNTIME_DIR`/`OPENALPHA_WEB_DIR`/`OPENALPHA_MAX_REQUEST_BYTES`
    from the real process environment via `openalpha_cn.config.load_config()` --
    never from a `.env` file directly (see that module's docstring). This keeps
    `app = create_app()` below, which runs at import time, filesystem-free for
    `.env`: importing this module can never read a developer's real `.env`,
    regardless of the process's current working directory. A caller-supplied
    keyword argument always wins over the environment, exactly as before.

    Raises `ConfigError` -- naming the specific `OPENALPHA_*` variable, never a
    bare traceback -- if an `OPENALPHA_*` environment variable fails validation
    (e.g. a non-numeric `OPENALPHA_MAX_REQUEST_BYTES`). This is the fix for the
    import-time crash `int(os.getenv("OPENALPHA_MAX_REQUEST_BYTES", ...))` used
    to cause: `app = create_app()` below executes at module import time, so an
    unguarded conversion error there used to surface as a bare Python traceback
    at process startup.

    Also calls `logging_setup.configure_logging(config.log_level)` -- this function
    is, along with `cli.py::main()`, one of this package's two logging entry points
    (V2-P0B-007). Safe to call every time this function runs (idempotent per
    process, see `configure_logging`'s own docstring), including the module-scope
    `app = create_app()` call below.

    `clock` mirrors `sdk.py`'s `OpenAlphaSDK.__init__` (`clock: Callable[[], datetime]
    = utc_now`, `sdk.py:52`): it defaults to the same `providers/base.py#utc_now` and
    threads through to `build_storage` and every `ResearchEngine`/`BatchResearchService`
    this function builds, exactly the way the SDK already threads `self.clock`. Before
    this parameter existed, each of those four call sites built its own
    `lambda: datetime.now(UTC)`, so REST and the SDK -- given the same input -- minted
    different `decision_id`s (a content-addressed field fed by `DecisionLedger.
    created_at`, `domain/decision.py`) purely because they ran at different wall-clock
    instants. Not passing `clock` reproduces that exact prior default behavior (V2-P0B-008).
    """
    config = load_config()
    configure_logging(config.log_level)
    root = runtime_dir if runtime_dir is not None else config.runtime_dir
    request_limit = max_request_bytes if max_request_bytes is not None else config.max_request_bytes
    if request_limit < 1:
        raise ValueError("max_request_bytes must be positive")
    storage = build_storage(runtime_dir=root, clock=clock)
    evidence_store = storage.evidence_store
    run_repository = storage.repository
    memory = storage.memory
    recovery_store = storage.recovery_store
    batch_store = storage.batch_store
    portfolio_ledger = storage.portfolio_ledger
    watchlist_store = storage.watchlist_store
    report_store = storage.report_store
    validation_store = storage.validation_store
    experiment_store = storage.experiment_store

    def run_one(request: ResearchRunRequest) -> ResearchRunResult:
        return ResearchEngine(
            repository=run_repository,
            memory=memory,
            clock=clock,
            recovery_store=recovery_store,
        ).run_cycle(request)

    batch_service = BatchResearchService(
        store=batch_store,
        runner=run_one,
        clock=clock,
    )
    application = FastAPI(
        title="OpenAlpha CN API",
        version=__version__,
        description="Evidence-traceable, point-in-time A-share research.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.add_middleware(
        SecurityHeadersMiddleware,
        max_request_bytes=request_limit,
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        """Return a dependency-free liveness result."""
        return {"status": "ok", "version": __version__}

    @application.post("/api/v1/evidence/build")
    def evidence_build(request: EvidenceBuildRequest) -> EvidenceBuildResponse:
        """Normalize a provider batch into versioned evidence snapshots."""
        response = build_evidence(request)
        if response.items:
            evidence_store.append(response.items)
        return response

    @application.get("/api/v1/evidence")
    def evidence_query(
        as_of: datetime,
        subject: str | None = None,
        kind: str | None = None,
    ) -> EvidenceBuildResponse:
        """Query evidence that was visible at the requested clock."""
        return EvidenceBuildResponse(
            items=evidence_store.query(as_of=as_of, subject=subject, kind=kind)
        )

    @application.get("/api/v1/market/events")
    def market_events(
        as_of: datetime,
        subject: str | None = None,
    ) -> EvidenceBuildResponse:
        """Return normalized board-event evidence."""
        items = evidence_store.query(as_of=as_of, subject=subject)
        return EvidenceBuildResponse(
            items=tuple(
                item
                for item in items
                if item.kind in {"limit_up", "broken_board", "consecutive_board"}
            )
        )

    @application.get("/api/v1/themes")
    def themes(
        as_of: datetime,
        subject: str | None = None,
    ) -> EvidenceBuildResponse:
        """Return normalized theme, catalyst, and disclosure evidence."""
        items = evidence_store.query(as_of=as_of, subject=subject)
        return EvidenceBuildResponse(
            items=tuple(item for item in items if item.kind in {"theme", "catalyst", "disclosure"})
        )

    @application.post("/api/v1/research/run")
    def research_run(request: ResearchApiRequest) -> ResearchRunResult:
        """Execute the shared live/replay/backtest research cycle."""
        engine = ResearchEngine(
            repository=run_repository,
            memory=memory,
            clock=clock,
            recovery_store=recovery_store,
        )
        return engine.run_cycle(request)

    @application.post("/api/v1/research/deliberate")
    def research_deliberate(request: DeliberationApiRequest) -> DeliberationOutcome:
        """Run an explicit, ablatable bull/bear and risk committee."""
        return DeliberationCommittee().review(
            signal=request.signal,
            results=request.agent_results,
        )

    @application.post("/api/v1/screen")
    def screen(request: ScreeningApiRequest) -> ScreeningResult:
        """Rank verified research results by explicit screening criteria."""
        try:
            results = tuple(_parse_research_result(item) for item in request.research)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="Research result failed integrity validation.",
            ) from error
        return ResearchScreener().screen(results=results, criteria=request.criteria)

    @application.post("/api/v1/watchlist")
    def watchlist_put(entry: WatchlistEntry) -> WatchlistEntry:
        """Create or intentionally update one watchlist entry."""
        watchlist_store.put(entry)
        return entry

    @application.get("/api/v1/watchlist")
    def watchlist_list() -> tuple[WatchlistEntry, ...]:
        """List the durable local observation pool."""
        return watchlist_store.list()

    @application.post("/api/v1/watchlist/{subject}/remove")
    def watchlist_remove(subject: str) -> dict[str, bool]:
        """Remove one watchlist entry."""
        return {"removed": watchlist_store.remove(subject)}

    @application.post("/api/v1/reports")
    def report_create(request: ReportApiRequest) -> ResearchReport:
        """Generate and append one evidence-linked research report."""
        try:
            result = _parse_research_result(request.research)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="Research result failed integrity validation.",
            ) from error
        report = ResearchReportFactory().build(result)
        report_store.append(report)
        return report

    @application.get("/api/v1/reports")
    def report_list(subject: str | None = None) -> tuple[ResearchReport, ...]:
        """List immutable generated reports."""
        return report_store.list(subject=subject)

    @application.get("/api/v1/reports/{report_id}")
    def report_get(report_id: str) -> ResearchReport:
        """Load one immutable report by content-derived ID."""
        report = report_store.get(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report was not found.")
        return report

    @application.post("/api/v1/research/batches", status_code=202)
    def batch_submit(
        request: BatchSubmitRequest,
        background_tasks: BackgroundTasks,
    ) -> BatchResearchTask:
        """Queue a bounded research batch and start it after the response."""
        try:
            task = batch_service.submit(
                batch_id=request.batch_id,
                requests=request.requests,
                max_concurrency=request.max_concurrency,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        # FastAPI background task contract:
        # https://fastapi.tiangolo.com/tutorial/background-tasks/
        background_tasks.add_task(batch_service.run, request.batch_id)
        return task

    @application.get("/api/v1/research/batches")
    def batch_list() -> tuple[BatchResearchTask, ...]:
        """List durable research batches."""
        return batch_store.list()

    @application.get("/api/v1/research/batches/{batch_id}")
    def batch_get(batch_id: str) -> BatchResearchTask:
        """Return the latest batch state."""
        task = batch_store.get(batch_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Batch was not found.")
        return task

    @application.get("/api/v1/research/batches/{batch_id}/events")
    def batch_events(batch_id: str) -> tuple[BatchProgressEvent, ...]:
        """Return append-only progress events for polling clients."""
        if batch_store.get(batch_id) is None:
            raise HTTPException(status_code=404, detail="Batch was not found.")
        return batch_store.list_events(batch_id)

    @application.post("/api/v1/research/batches/{batch_id}/cancel")
    def batch_cancel(batch_id: str) -> BatchResearchTask:
        """Request cooperative cancellation of pending batch work."""
        try:
            return batch_service.cancel(batch_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Batch was not found.") from error

    @application.post("/api/v1/research/batches/{batch_id}/retry", status_code=202)
    def batch_retry(batch_id: str, background_tasks: BackgroundTasks) -> BatchResearchTask:
        """Retry failed items using their existing run recovery state."""
        task = batch_store.get(batch_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Batch was not found.")
        background_tasks.add_task(batch_service.run, batch_id)
        return task

    @application.get("/api/v1/memory/{subject}")
    def memory_query(subject: str) -> tuple[MemoryEntry, ...]:
        """Return durable decision-linked memory for one subject."""
        return memory.list(subject=subject)

    @application.get("/api/v1/runs/{run_id}/recovery")
    def recovery_query(run_id: str) -> RunRecoveryState:
        """Return node-level progress used to resume an interrupted run."""
        state = recovery_store.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Recovery state was not found.")
        return state

    @application.post("/api/v1/backtests/replay")
    def replay(request: ReplayApiRequest) -> ReplayReport:
        """Execute a supplied frozen corpus through the shared research core.

        `ReplayRunner.run()` keeps its own migrated `api-replay.sqlite3` for run/recovery
        state, but persists validation results into `validation_store` -- the same store
        `validate_outcome` above uses -- so a result produced by replay is retrievable
        through `GET /api/v1/backtests/validations/by-decision/{id}` and `by-signal/{id}`
        exactly like one produced by `POST /api/v1/backtests/validate` (P0.B acceptance
        review, Finding 1).
        """
        runner = ReplayRunner(
            code_commit=request.code_commit,
            config_digest=request.config_digest,
            random_seed=request.random_seed,
        )
        return runner.run(
            corpus=request.corpus,
            state_path=root / "api-replay.sqlite3",
            validation_store=validation_store,
            clock=clock,
        )

    @application.post("/api/v1/portfolio/execute")
    def portfolio_execute(request: PortfolioApiRequest) -> PortfolioTransition:
        """Apply A-share execution, T+1, costs, and exposure limits."""
        transition = PortfolioSimulator(limits=request.limits).execute_order(
            state=request.state,
            order=request.order,
            market=request.market,
        )
        portfolio_ledger.append(transition)
        return transition

    @application.get("/api/v1/portfolio/ledger")
    def portfolio_ledger_query(
        subject: str | None = None,
    ) -> tuple[PortfolioTransition, ...]:
        """List immutable order/execution transitions."""
        return portfolio_ledger.list(subject=subject)

    @application.post("/api/v1/backtests/portfolio")
    def portfolio_backtest(request: PortfolioBacktestRequest) -> PortfolioBacktestReport:
        """Run a multi-day A-share portfolio report and persist transitions."""
        return PortfolioBacktestRunner(
            limits=request.limits,
            ledger=portfolio_ledger,
        ).run(initial=request.initial, steps=request.steps)

    @application.post("/api/v1/backtests/event-study")
    def event_study(request: EventStudyRequest) -> EventStudyReport:
        """Compute CAR, t-statistic, and deterministic bootstrap confidence."""
        return EventStudy().analyze(request)

    @application.post("/api/v1/backtests/validate")
    def validate_outcome(request: OutcomeApiRequest) -> ValidationResult:
        """Validate an observed outcome, persist it, and reconcile its attribution.

        Persistence (V2-P0B-010) is the fix for this endpoint's prior behavior: it
        computed a `ValidationResult` and returned it without ever storing it anywhere,
        so a past decision's outcome could never be looked back up -- see
        `storage/validation.py`'s module docstring. `validation_store.append` is
        idempotent by `validation_id` (content-derived), so replaying the identical
        request -- e.g. a client retry after a dropped response -- is a safe no-op, not a
        duplicate row.
        """
        try:
            research = _parse_research_result(request.research)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="Research result failed integrity validation.",
            ) from error
        result = OutcomeValidator().validate(
            research=research,
            observation=request.observation,
        )
        validation_store.append(result)
        return result

    @application.get("/api/v1/backtests/validations/by-decision/{decision_id}")
    def validations_by_decision(decision_id: str) -> tuple[ValidationResult, ...]:
        """List persisted validation results for one decision, in append order."""
        return validation_store.list_by_decision(decision_id)

    @application.get("/api/v1/backtests/validations/by-signal/{signal_id}")
    def validations_by_signal(signal_id: str) -> tuple[ValidationResult, ...]:
        """List persisted validation results for one signal, in append order."""
        return validation_store.list_by_signal(signal_id)

    @application.get("/api/v1/panel/readiness")
    def panel_readiness(
        dataset: Annotated[list[str], _PANEL_DATASET_QUERY],
        year: Annotated[list[int], _PANEL_YEAR_QUERY],
        as_of: datetime,
        exchange: str,
        calendar: bool,
        index_code: Annotated[list[str] | None, _PANEL_INDEX_QUERY] = None,
    ) -> JSONResponse:
        """Report each named dataset's own readiness verdict at a stated `as_of`.

        The narrowest of the three panel faces: one dataset's catalog records against the
        requirement its own reader puts, with no cross-dataset check and no session -- which is
        why this endpoint takes no `session`. `state` is `ready` or `blocked` per dataset and
        `all_ready` is the whole-request answer; `checks_waived` says which questions were never
        put, because an empty `issues` list means nothing without it.

        **A `session` sent here is discarded, not honoured and not refused.** It is not a
        declared parameter of this endpoint, and an undeclared query parameter is dropped by
        FastAPI before this function runs; there is nothing here that could see it in order to
        object. So a caller who copies a `/panel/health` query onto this path gets an answer to
        a narrower question than the one they typed, with nothing in the response saying so.
        `tests/integration/test_panel_interfaces.py::
        test_a_session_sent_to_the_readiness_endpoint_is_dropped_rather_than_honoured` pins
        that the answer is byte-identical with and without it, so the drop is a stated property
        of this face rather than a discovery. A caller who needs the session-scoped verdict is
        asking `/panel/health`'s question and should send it there.

        Always `200` when the request could be put at all: this is a report, and a report that
        found a blocked dataset has succeeded. Permission to read is `/api/v1/panel/gate`'s
        answer, and only that endpoint's `200` claims it.
        """
        store, request = _panel_query(
            root,
            dataset=dataset,
            year=year,
            session=(),
            index_code=index_code or (),
            as_of=as_of,
            exchange=exchange,
            calendar=calendar,
        )
        try:
            entries = dataset_readiness(store, request)
        except PanelDoctorError as error:
            raise _panel_bad_request(error) from error
        return JSONResponse(
            status_code=PANEL_HTTP_STATUS["answered"],
            content=readiness_payload(entries),
        )

    @application.get("/api/v1/panel/health")
    def panel_health(
        dataset: Annotated[list[str], _PANEL_DATASET_QUERY],
        year: Annotated[list[int], _PANEL_YEAR_QUERY],
        as_of: datetime,
        exchange: str,
        calendar: bool,
        session: Annotated[list[date] | None, _PANEL_SESSION_QUERY] = None,
        index_code: Annotated[list[str] | None, _PANEL_INDEX_QUERY] = None,
    ) -> JSONResponse:
        """Report what is wrong with the stored panel at a stated `as_of`.

        The HTTP twin of `openalpha panel doctor`, and distinct from `GET /health`, which is
        this service's dependency-free liveness probe. Per-dataset readiness and freshness, the
        cross-dataset checks with a record of which of them actually ran, and the datasets' own
        structural limitations kept separate from this fetch's defects.

        Always `200` when the request could be put: `is_clean` and a `counts_by_severity` total
        over all three severities are in the body. This endpoint grants nothing, so its status
        code claims nothing -- see `PANEL_HTTP_STATUS`.
        """
        store, request = _panel_query(
            root,
            dataset=dataset,
            year=year,
            session=session or (),
            index_code=index_code or (),
            as_of=as_of,
            exchange=exchange,
            calendar=calendar,
        )
        try:
            report = panel_health_report(
                store,
                as_of=request.as_of,
                datasets=request.datasets,
                years=request.years,
                calendar=request.calendar,
                index_codes=request.index_codes,
                cross_section_days=request.sessions,
            )
        except PanelDoctorError as error:
            raise _panel_bad_request(error) from error
        return JSONResponse(
            status_code=PANEL_HTTP_STATUS["answered"],
            content=health_report_payload(report),
        )

    @application.get("/api/v1/panel/gate")
    def panel_gate(
        dataset: Annotated[list[str], _PANEL_DATASET_QUERY],
        year: Annotated[list[int], _PANEL_YEAR_QUERY],
        as_of: datetime,
        exchange: str,
        calendar: bool,
        session: Annotated[list[date] | None, _PANEL_SESSION_QUERY] = None,
        index_code: Annotated[list[str] | None, _PANEL_INDEX_QUERY] = None,
    ) -> JSONResponse:
        """Run the fail-closed dependency gate and answer `409` when it refuses.

        The status code is the deliverable. A refused request that still answered `200` would
        be the empty success `V2-P1-013` exists to make unavailable, one layer up: a client
        that checks the status and then reads `cleared` would proceed on a panel the gate would
        not clear.

        The refusal has a body. Every block carries its code, category, severity, both sides of
        a cross-dataset finding and its detail; the notices and the unverified checks ride
        along; and the whole health report the verdict rests on is nested under `report`.

        A clearance is a verdict rather than a collection, and this endpoint treats it as one:
        it asks `is_blocked` and the serialiser reads `cleared_or_none`, never `bool()`,
        `len()` or iteration, all three of which raise here **even when the request cleared**.
        """
        store, request = _panel_query(
            root,
            dataset=dataset,
            year=year,
            session=session or (),
            index_code=index_code or (),
            as_of=as_of,
            exchange=exchange,
            calendar=calendar,
        )
        try:
            clearance = require_datasets(store, request)
        except (PanelGateError, PanelDoctorError) as error:
            raise _panel_bad_request(error) from error
        return JSONResponse(
            status_code=(
                PANEL_HTTP_STATUS["blocked"]
                if clearance.is_blocked
                else PANEL_HTTP_STATUS["answered"]
            ),
            content=clearance_payload(clearance),
        )

    @application.get("/api/v1/factors")
    def factor_declarations(
        factor: str | None = None,
        transform: str | None = None,
        neutralization: str | None = None,
    ) -> JSONResponse:
        """Every factor, transform and neutralisation this build declares -- or one of them.

        The HTTP twin of `openalpha factor list` / `openalpha factor describe` and of
        `OpenAlphaSDK.factor_catalog()` / `.describe_factor()`. With no query parameter it serves
        `factor_view.factor_catalog()` whole; with exactly one it serves that one declaration and
        its prose through `factor_view.factor_entry`.

        **A query parameter rather than a path segment**, and the choice is forced rather than
        stylistic: a handle is `key/vN` and contains a `/`, so `GET /api/v1/factors/{handle}` would
        either need a `:path` converter -- which would shadow `/api/v1/factors/run` and
        `/api/v1/factors/experiments` for every client that mistyped one -- or an escaping rule for
        the one character the identity is defined by. `factor_entry`'s own refusal is what keeps
        "none" and "more than one" from being resolved by precedence.

        Reads no store, so it answers before `openalpha panel build` has ever run. That is the
        point: it is the route a caller uses to find out what to build.
        """
        try:
            body: dict[str, object] = (
                factor_catalog()
                if factor is None and transform is None and neutralization is None
                else factor_entry(factor=factor, transform=transform, neutralization=neutralization)
            )
        except FactorViewError as error:
            raise _factor_refusal(error) from error
        return JSONResponse(status_code=FACTOR_HTTP_STATUS["answered"], content=body)

    @application.post("/api/v1/factors/run")
    def factor_run(request: FactorRunApiRequest) -> JSONResponse:
        """Run one factor experiment over a closed range of prediction days, and seal it.

        The HTTP twin of `openalpha factor run` and of `OpenAlphaSDK.run_factor_experiment`. All
        three resolve through `factor_view.factor_request` and run through
        `factor_view.run_factor_experiment`, so they cannot come to ask three different questions
        of one store -- which is what makes
        `tests/integration/test_factor_interfaces.py::
        test_the_three_faces_seal_one_experiment_from_one_request` a statement about one answer
        rather than about three requests that happened to agree.

        **A refused run answers `409`, never `200` with an empty body.** See `FACTOR_HTTP_STATUS`:
        the neutralised row is the one the acceptance criterion is decided on, so a face that
        reported a three-tier experiment whose third row was never built would be worse than one
        that reported nothing.

        The body is `factor_view.experiment_view`'s envelope: the two content addresses, what the
        store did, and the sealed document itself. Nothing about the deployment travels in it --
        no path, no runtime directory, no credential -- and
        `test_no_factor_response_or_log_line_carries_a_token` drives that with a real token in the
        environment.
        """
        try:
            resolved = factor_request(
                factor=request.factor,
                transform=request.transform,
                neutralization=request.neutralization,
                start=request.start,
                end=request.end,
                as_of=request.as_of,
                exchange=request.exchange,
                horizon=request.horizon,
                ic_method=request.ic_method,
                min_securities=request.min_securities,
                min_as_ofs=request.min_as_ofs,
                group_count=request.group_count,
                min_securities_per_group=request.min_securities_per_group,
                position_capital=request.position_capital,
                min_periods=request.min_periods,
                participation_cap=request.participation_cap,
                min_rebalances=request.min_rebalances,
                redundancy_threshold=request.redundancy_threshold,
                retention_floor=request.retention_floor,
                code_commit=_resolved_code_commit(request.code_commit),
            )
            record, write = run_factor_experiment(
                panel_store(root),
                resolved,
                built_at=clock(),
                experiments=experiment_store,
                note=(
                    None
                    if request.note is None
                    else FactorNote(subject=resolved.definition.qualified_key, summary=request.note)
                ),
            )
        except FactorViewError as error:
            raise _factor_refusal(error) from error
        except (ExperimentStoreError, FactorError) as error:
            raise HTTPException(
                status_code=FACTOR_HTTP_STATUS["conflict"],
                detail=_panel_detail("conflict", str(error)),
            ) from error
        return JSONResponse(
            status_code=FACTOR_HTTP_STATUS["answered"],
            content=experiment_view(record, write=write),
        )

    @application.post("/api/v1/shortlists/run")
    def shortlist_run(request: ShortlistRunApiRequest) -> JSONResponse:
        """Cut a shortlist out of the stored panel, join the evidence plane, and gate it.

        `V2-P4-033`'s HTTP face, and the twin of `openalpha shortlist run` and of
        `OpenAlphaSDK.run_shortlist`. All three resolve through `shortlist_view.shortlist_request`
        and run through `shortlist_view.run_shortlist`, so they cannot come to cut three lists
        from one declaration.

        **A refused list answers `409` with `"admitted": null`, never `200` with `[]`.** That is
        the whole deliverable: `GET /api/v1/panel/gate`'s arrangement one plane over, where the
        status code is what says the gate refused and the body still carries every block, the
        measurement each was read against, and the funnel's own coverage code. A caller told `409`
        and nothing else cannot act on it, and a caller told `200` with an empty array cannot tell
        a refusal from a market that offered nothing. See `SHORTLIST_HTTP_STATUS`.

        A clearance is a verdict rather than a collection, and this endpoint treats it as one: it
        asks `is_blocked` and the serialiser reads `admitted_or_none`, never `bool()`, `len()` or
        iteration -- all three of which raise **even when the list cleared**.
        """
        try:
            resolved = shortlist_request(
                components=shortlist_components(
                    [component.model_dump() for component in request.components]
                ),
                tier=request.tier,
                shortlist_size=request.shortlist_size,
                position_capital=request.position_capital,
                as_of=request.as_of,
                years=request.years,
                exchange=request.exchange,
                horizon=request.horizon,
                minimum_tradable_ratio=request.minimum_tradable_ratio,
                minimum_researched_ratio=request.minimum_researched_ratio,
                maximum_ranking_age_days=request.maximum_ranking_age_days,
                code_commit=_resolved_code_commit(request.code_commit),
                config_digest=_resolved_config_digest(request.config_digest),
                transform=request.transform,
                neutralization=request.neutralization,
                evidence=shortlist_evidence(request.evidence),
            )
            result = run_shortlist(panel_store(root), resolved, built_at=clock())
        except ShortlistViewError as error:
            raise _shortlist_refusal(error) from error
        return JSONResponse(
            status_code=(
                SHORTLIST_HTTP_STATUS["refused"]
                if result.is_blocked
                else SHORTLIST_HTTP_STATUS["answered"]
            ),
            content=shortlist_view(result),
        )

    @application.get("/api/v1/factors/experiments")
    def factor_experiment_list() -> dict[str, list[str]]:
        """Every sealed experiment this installation holds, by content address, ascending."""
        return {"experiment_ids": list(experiment_store.list_ids())}

    @application.get("/api/v1/factors/experiments/{experiment_id}")
    def factor_experiment_get(experiment_id: str) -> JSONResponse:
        """One sealed experiment, reopened and re-sealed on the way out.

        Through `open_experiment`, so a stored document whose content no longer hashes to its own
        seal does not come back as a record that merely differs -- it is refused. The envelope
        reports `unchanged`, because reading held an artifact rather than writing one, and the
        document is the one on disk.
        """
        try:
            payload = experiment_store.get(experiment_id)
        except ExperimentStoreError as error:
            raise HTTPException(
                status_code=FACTOR_HTTP_STATUS["bad_request"],
                detail=_panel_detail("bad_request", str(error)),
            ) from error
        if payload is None:
            raise HTTPException(
                status_code=FACTOR_HTTP_STATUS["not_found"],
                detail=_panel_detail("not_found", "No experiment is held under that id."),
            )
        try:
            record = open_experiment(payload)
        except FactorExperimentError as error:
            raise HTTPException(
                status_code=FACTOR_HTTP_STATUS["conflict"],
                detail=_panel_detail("conflict", str(error)),
            ) from error
        return JSONResponse(
            status_code=FACTOR_HTTP_STATUS["answered"],
            content=experiment_view(record, write="unchanged"),
        )

    configured_web_dir = web_dir if web_dir is not None else config.web_dir
    if configured_web_dir is not None:
        index = configured_web_dir / "index.html"
        if not index.is_file():
            raise ValueError(f"web_dir does not contain index.html: {configured_web_dir}")
        application.mount(
            "/",
            StaticFiles(directory=configured_web_dir, html=True),
            name="web",
        )

    return application


app = create_app()
