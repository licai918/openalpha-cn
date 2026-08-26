"""FastAPI application for OpenAlpha CN's versioned public HTTP surface."""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
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
from openalpha_cn.backtest.portfolio_policy import (
    PortfolioConstruction,
    PortfolioConstructionError,
    PortfolioConstructionPolicy,
    candidates_from_shortlist_answer,
    construct_portfolio,
    construction_view,
)
from openalpha_cn.backtest.replay import ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator
from openalpha_cn.config import load_config
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.factor import FactorError, FactorNote
from openalpha_cn.domain.risk_flag import UndeclaredRiskFlagError
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
from openalpha_cn.job_contracts import job_not_registered, job_run_view, scheduled_job_view
from openalpha_cn.logging_setup import configure_logging
from openalpha_cn.model_view import (
    ModelViewError,
    daily_request,
    daily_view,
    declared_hyperparameters,
    evaluate_model,
    evaluation_view,
    feature_columns,
    held_prediction,
    held_prediction_view,
    held_predictions,
    model_evaluation_request,
    prediction_index_view,
    run_daily,
)
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
from openalpha_cn.product.export import ReportExport
from openalpha_cn.product.export import export_report as build_report_export
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
    DEFAULT_BATCH_PAGE_SIZE,
    MAX_BATCH_ITEMS,
    MAX_BATCH_PAGE_SIZE,
    MAX_BATCH_WORKERS,
    BatchProgressEvent,
    BatchResearchService,
    BatchResearchTask,
    BatchTaskPage,
)
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.runtime.provenance import compute_config_digest, resolve_code_commit
from openalpha_cn.shortlist_view import (
    ShortlistViewError,
    held_shortlist,
    run_shortlist,
    shortlist_components,
    shortlist_evidence,
    shortlist_request,
    shortlist_view,
)
from openalpha_cn.storage.factor_experiments import ExperimentStoreError
from openalpha_cn.storage.predictions import PredictionStoreError
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


class PortfolioConstructionApiRequest(BaseModel):
    """One held shortlist, one declared policy, and the book being moved from (`V2-P5-013`).

    **`policy` is `PortfolioConstructionPolicy` itself rather than a set of loose numbers this
    route re-assembles**, which is the whole reason this face could be added as wiring. The SDK
    takes that exact model as its `policy` argument and the CLI builds it out of its flags, so
    the tier-weight validator a caller meets here is the one the other two faces meet -- in
    pydantic's own words, once, rather than restated at a third boundary. A route that took
    `tier_weights` and five `limits` fields flat would be a second declaration of the same
    contract, and `V2-P5-013` exists because faces that restate a contract come to disagree
    about it.

    `previous` is weights the **caller** states. It reaches no ledger, exactly as on the other
    two faces: see `KNOWN_CONSTRUCTION_LIMITATIONS
    .the_previous_book_is_declared_by_the_caller_and_never_read_from_a_ledger`. Empty by default,
    which is the first construction over a book that does not exist yet -- and *not* an implicit
    "read what I hold", which would make a turnover number depend on a store this request never
    named.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    shortlist_id: str
    policy: PortfolioConstructionPolicy
    previous: dict[str, Decimal] = Field(default_factory=dict)


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
    """Serialized verified research results plus screening criteria.

    `research` states the same ceiling `BatchSubmitRequest.requests` states, and `V2-P4-043` is
    why it states one at all: before this it declared none, so the only thing bounding a screen
    was `OPENALPHA_MAX_REQUEST_BYTES`, and a caller one name too far met a `413` about bytes
    with no number to aim at. One ceiling rather than a second constant, because a screen is the
    answer-side of a batch and a service whose two whole-market routes disagreed about how big
    the market may be would be `V2-P4-043` again in a different field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    research: tuple[dict[str, Any], ...] = Field(max_length=MAX_BATCH_ITEMS)
    criteria: ScreeningCriteria = ScreeningCriteria()


class ReportApiRequest(BaseModel):
    """One serialized research result to turn into an immutable report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research: dict[str, Any]


CORS_ALLOWED_ORIGINS: Final[tuple[str, ...]] = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
"""The only two origins a browser may talk to this service from.

Both are the local Vite dev server. This is the part of the CORS configuration that actually
decides anything, and `V2-P5-011` deliberately did not touch it -- see `CORS_ALLOWED_METHODS`.
"""

CORS_ALLOWED_METHODS: Final[tuple[str, ...]] = (
    "DELETE",
    "GET",
    "HEAD",
    "PATCH",
    "POST",
    "PUT",
)
"""Every HTTP method a browser may preflight against an allowed origin (`V2-P5-011`).

This was `["GET", "POST"]`, written by hand, and the roadmap row states the cost as a v2 risk: a
`PUT`/`DELETE`/`PATCH` route added later would be refused at the browser before FastAPI saw it.

**Measured on `c847295`, the hand-written list had already fallen behind the route table.** A
preflight naming `HEAD` answered `400 Disallowed CORS method` while the application declared four
`HEAD` routes -- FastAPI adds one to every `GET`. Nothing broke, because `HEAD` is a
CORS-safelisted method and no browser preflights it, but the divergence is the point: two
statements of "what this service serves" with nothing keeping them in step.

**Why a fixed list rather than deriving it from `application.routes`.** CORS is not
authorization. It tells a *browser* which cross-origin requests it may let a page make; it stops
nothing else, and this service authenticates nobody (audit `F101`). Advertising a method no route
serves costs a `405` instead of a browser-level refusal, which is the better failure -- the caller
sees the service's own answer. What must not drift is the other direction, a served method that
is *not* advertised, and that is pinned by
`tests/integration/test_cors_method_surface.py::test_every_method_the_route_table_declares_survives_a_preflight`,
which reads the methods off the running application.

**`OPTIONS` is absent deliberately, and not for the reason first written here.** An earlier
version of this docstring said Starlette "appends `OPTIONS` to what it advertises"; measured on
`starlette 1.3.1`, **it does not** -- `Access-Control-Allow-Methods` is exactly the list it is
given. `OPTIONS` is omitted because a browser never needs it advertised: the preflight *is* the
`OPTIONS` request, and what Starlette checks against this list is the value of
`Access-Control-Request-Method`, never `OPTIONS` itself.

That measurement also falsifies the second claim that stood here, that this tuple is
observationally identical to `["*"]`. It is not: `"*"` expands to Starlette's `ALL_METHODS`,
which *is* these six plus `OPTIONS`, so the two advertise different strings --
`tests/integration/test_cors_method_surface.py::
test_the_documented_method_list_is_the_one_a_preflight_is_told` tells them apart, and found this
error by failing on the document written from the same false belief.
"""

HSTS_MAX_AGE_SECONDS: Final[int] = 31_536_000
"""One year, the value HSTS preload requires and the shortest one worth sending (`V2-P5-012`).

`preload` itself is **not** in the header. Submitting an origin to the browser preload list is a
commitment the operator makes about a domain, and it is close to irreversible; a library that
made it on their behalf would be making it for a domain it has never seen. `includeSubDomains` is
sent because the header is inert unless the deployment already terminates TLS, and a deployment
that does is a deployment that owns its subdomains.

Sent unconditionally rather than only over TLS: a user agent must ignore this header when the
transport is not secure (RFC 6797 s7.2), so on `http://127.0.0.1:8000` it is a no-op, while
behind the TLS-terminating reverse proxy `docs/deployment/production.zh-CN.md` requires it is the
only place the header can come from.
"""


def _hardening_headers() -> tuple[tuple[bytes, bytes], ...]:
    """The response headers every answer this service gives must carry.

    A function rather than a literal so `HSTS_MAX_AGE_SECONDS` is read from one place.
    """
    return (
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
        (b"cross-origin-embedder-policy", b"require-corp"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (
            b"strict-transport-security",
            f"max-age={HSTS_MAX_AGE_SECONDS}; includeSubDomains".encode("ascii"),
        ),
    )


async def _no_body_receive() -> Message:
    """A `receive` for a response that is sent instead of, not after, reading the request.

    Returns a disconnect rather than the real `receive`, so a refusal issued because the body was
    too large can never itself pull another chunk of that body off the wire.
    """
    return {"type": "http.disconnect"}


class SecurityHeadersMiddleware:
    """Apply browser hardening headers and meter the request body as it arrives.

    ## The body meter (`V2-P5-012`)

    This used to read `Content-Length` and nothing else, which audit `F100` records and which
    **a measurement on `c847295` confirms is a complete bypass**: against a deliberately tiny
    1,024-byte ceiling, a chunked `POST /api/v1/research/batches` of **36,000,030 bytes** was
    answered `422 json_invalid` -- a *parser* error, reached only because the whole body had
    already been read. `tracemalloc` measured a **108,346,472-byte** peak for that one request,
    three times the body, because Starlette accumulates the chunks in a list and then joins them.

    So there are now two gates, and they answer different questions:

    - **Declared.** A `Content-Length` above the ceiling is refused before the application is
      called at all. Unchanged, and still the cheapest refusal available -- nothing is read.
    - **Streamed.** Every `http.request` chunk the application asks for is counted, and the
      moment the running total crosses the ceiling the stream is cut: the middleware stops
      calling `receive`, so the transport stops being asked for more, and the body the
      application holds is bounded by the ceiling plus the one chunk that crossed it.

    Cutting the stream means the application sees a body that ends early, and for a JSON route
    that is a `422`. That answer would be a lie about *why* the request failed, so the refusal is
    swapped at `http.response.start`: whatever the application decided, the truth is that the
    body exceeded the ceiling, and `413` is what the caller is told. The swap is skipped if the
    response had already started before the overrun was seen, since a second response start is
    not something this middleware may send -- no route in this application streams a response
    before reading its body, but the guard costs one comparison.

    **What is deliberately still not metered**: a body sent to a route that never reads it (a
    `404`, or a `GET` carrying a body). Nothing accumulates in that case -- the application never
    asks for a chunk, so the transport's own flow control holds the connection -- and the harm
    this row exists to close is memory, not arrival.

    ## The headers

    `V2-P5-012` adds the three audit `F102` names -- `Strict-Transport-Security`,
    `Cross-Origin-Embedder-Policy`, `Cross-Origin-Resource-Policy` -- and fixes the same
    finding's second half: the headers were **appended** to whatever the response already
    carried, so a route setting `x-frame-options: SAMEORIGIN` produced
    `x-frame-options: SAMEORIGIN, DENY` (measured on `c847295`, two raw header lines). They are
    now replaced by name, which is what makes them a service-wide policy rather than a
    suggestion any route can add to.
    """

    _HEADERS = _hardening_headers()
    _MANAGED_NAMES = frozenset(name for name, _ in _HEADERS)

    def __init__(self, app: ASGIApp, *, max_request_bytes: int) -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes

    def _hardened(self, existing: Sequence[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        """`existing` with every header this service owns replaced rather than added to.

        Replacement is `V2-P4`/`F102`'s second half: appending let a route contribute a second
        value for a policy header, and a browser reading `x-frame-options: SAMEORIGIN, DENY`
        has been handed a policy this service never chose.
        """
        kept = [
            (name, value) for name, value in existing if name.lower() not in self._MANAGED_NAMES
        ]
        return [*kept, *self._HEADERS]

    async def _refuse(self, scope: Scope, send: Send, response: JSONResponse) -> None:
        """Send `response` in place of the application's, still fully hardened."""

        async def hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": self._hardened(message.get("headers", ()))}
            await send(message)

        await response(scope, _no_body_receive, hardened)

    def _too_large(self, *, declared: int | None, measured: int | None) -> JSONResponse:
        """The one `413` this middleware issues, from either gate.

        `reason` and `limit_bytes` are identical across the two, because `docs/api/http.md`
        makes `detail.reason` the key a client switches on and a caller who exceeded the ceiling
        exceeded the same ceiling either way. `declared_bytes` and `measured_bytes` are both
        always present and exactly one is non-null, which is what tells the two gates apart:
        `declared_bytes` means "you said so and nothing was read"; `measured_bytes` means "you
        declared nothing, and this is where reading stopped" -- a floor on the body, never its
        size, because the rest was never asked for.
        """
        if declared is not None:
            message = (
                f"the request declared {declared} bytes against a configured ceiling of "
                f"{self.max_request_bytes}. Raise OPENALPHA_MAX_REQUEST_BYTES on the server, "
                "or send fewer items per request."
            )
        else:
            message = (
                f"the request body reached {measured} bytes without declaring a Content-Length, "
                f"against a configured ceiling of {self.max_request_bytes}. Reading stopped "
                "there, so that number is a floor and not the body's size. Raise "
                "OPENALPHA_MAX_REQUEST_BYTES on the server, or send fewer items per request."
            )
        return JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "reason": "request_too_large",
                    "message": message,
                    "declared_bytes": declared,
                    "measured_bytes": measured,
                    "limit_bytes": self.max_request_bytes,
                }
            },
        )

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
                await self._refuse(
                    scope,
                    send,
                    JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid Content-Length header."},
                    ),
                )
                return
            if content_length > self.max_request_bytes:
                await self._refuse(
                    scope, send, self._too_large(declared=content_length, measured=None)
                )
                return

        received = 0
        overrun = False
        started = False
        swallowing = False

        async def metered_receive() -> Message:
            nonlocal received, overrun
            message = await receive()
            if message["type"] != "http.request":
                return message
            received += len(message.get("body", b""))
            if received > self.max_request_bytes:
                overrun = True
                # Cut the stream here. `receive` is not called again, so the transport is never
                # asked for the rest, and the application holds at most one chunk past the
                # ceiling instead of the whole body.
                return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def hardened_send(message: Message) -> None:
            nonlocal started, swallowing
            if message["type"] == "http.response.start":
                if overrun and not started:
                    swallowing = True
                    await self._refuse(
                        scope, send, self._too_large(declared=None, measured=received)
                    )
                    return
                started = True
                message = {**message, "headers": self._hardened(message.get("headers", ()))}
            elif swallowing:
                return
            await send(message)

        await self.app(scope, metered_receive, hardened_send)


class ResearchIntegrityError(ValueError):
    """One research result whose content-derived identifier does not describe its content.

    `V2-P4-041`. `_parse_research_result` has always distinguished the three -- `signal_id`,
    `decision_id`, `run_manifest_id` -- and the routes flattened all three into
    `"Research result failed integrity validation."`, so a caller holding 5,545 results learned
    neither which record nor which of the three addresses had moved.

    A `ValueError` subclass so the existing `except (KeyError, TypeError, ValueError)` at each
    call site still catches it; what the routes now do is *ask* it which fault it is instead of
    discarding that. `claimed` and `derived` are both carried because the difference between
    them is the only actionable thing this route can offer: an edited record and an edited
    identifier need different fixes, and only the two values side by side tell them apart.
    """

    def __init__(
        self, *, reason: str, field: str, claimed: object, derived: str, subject: str
    ) -> None:
        super().__init__(f"research {field} does not match its content")
        self.reason = reason
        self.field = field
        self.claimed = claimed
        self.derived = derived
        self.subject = subject


MAX_ECHOED_INPUT_BYTES: Final[int] = 512
"""How much of a rejected value a validation refusal may quote back.

`V2-P4-040`'s shape, arriving inside `V2-P4-043`'s own fix. Pydantic's error objects carry
`input` -- the value that was refused -- and FastAPI serialises it verbatim, so a `too_long` on a
whole-market collection answered with the whole collection. Measured on `daaabf5`:
`POST /api/v1/screen` with 10,001 records (14,771,528 bytes in) refused in **13,821,594 bytes**,
and `POST /api/v1/research/batches` with 10,001 requests in **9,261,138 bytes** -- the same
defect on a second route, which is why the elision is installed once for the app rather than
per route.

**The sharper case is not a ceiling at all.** A misspelled top-level key produces two errors
(`missing` and `extra_forbidden`) and each echoes the entire body, so at *200* records the
refusal measured 553,037 bytes against a 295,450-byte request -- 1.87x, and growing with the
number of faults rather than with the ceiling.

512 bytes rather than zero, deliberately. FastAPI's list shape is documented in
`docs/api/http.md` as the answer to an ordinary parameter fault, and there the echo is the useful
half: `input: "not-a-number"` beside `loc` is what tells a caller which value to fix. What is
removed is the echo that is a copy of the request, and the replacement names what it replaced --
kind and size -- so "you sent a list of ten thousand" is still distinguishable from "you sent a
string".
"""

MAX_VALIDATION_ERRORS: Final[int] = 20
"""How many validation faults one refusal lists before it starts counting instead.

Eliding each `input` bounds an entry; only truncating the list bounds the body.
`BatchSubmitRequest.requests` validates every item, so a thousand malformed requests is a
thousand error objects and the list alone reaches megabytes with every echo already gone.

The count of what was not listed is carried as a final entry rather than dropped, because a
truncated list that did not say so would tell a caller they had fixed everything when they had
fixed twenty things. That entry carries a `loc` like every other, which is what keeps
`docs/api/http.md`'s documented discriminator -- `isinstance(detail, dict)` is false, every entry
has a `loc` -- true of it as well.
"""

_DECLARED_CEILING_TYPES: Final[frozenset[str]] = frozenset({"too_long", "too_short"})
"""The pydantic error types that mean "this service declared a bound and you crossed it".

These are the only validation faults that are a refusal **this service decided** rather than a
mismatch between what arrived and what a type says. `V2-P4-043` added the ceiling precisely so a
caller would meet a number to aim at, and `docs/api/http.md` makes `detail.reason` the key a
client switches on for the refusals this service decides -- which `V2-P4-041` built for the same
route in the same commit. So these take the object shape and everything else keeps FastAPI's
list; see `_validation_refusal`.
"""


def _elided(value: object) -> object:
    """`value` as it may appear in a refusal: itself when small, its measurement when not."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - json refuses very little via `default`
        return f"<{type(value).__name__} elided>"
    if len(encoded.encode("utf-8")) <= MAX_ECHOED_INPUT_BYTES:
        return value
    measure = len(value) if isinstance(value, list | dict | str | tuple) else len(encoded)
    return f"<{type(value).__name__} of {measure} elided>"


def _declared_ceiling_detail(error: Mapping[str, Any]) -> dict[str, Any]:
    """One `too_long`/`too_short` fault as this service's own `{"reason", "message"}` refusal.

    `field` is the last **string** element of `loc`, which is the field the ceiling is declared
    on: `loc` for a body-level collection is `("body", "research")`, and an index would be an
    `int`. `limit` and `received` are read off pydantic's `ctx` and the input's own length rather
    than restated from `MAX_BATCH_ITEMS`, so a second ceiling declared anywhere in this module
    reports its own number instead of this one's.
    """
    location = [part for part in error.get("loc", ()) if isinstance(part, str) and part != "body"]
    field = location[-1] if location else "body"
    context = error.get("ctx") or {}
    limit = context.get("max_length", context.get("min_length"))
    received = len(error["input"]) if isinstance(error.get("input"), list | tuple | dict) else None
    return {
        "reason": "declared_ceiling_exceeded",
        "message": (
            f"{field} carries {received} items and this service declares a ceiling of {limit}. "
            f"{error.get('msg', '')} The ceiling is a count rather than a size, so shortening "
            "the records does not help; send at most the declared number, in more than one "
            "request if the market is larger than that. This refusal deliberately does not quote "
            "the body back."
        ),
        "field": field,
        "loc": list(error.get("loc", ())),
        "limit": limit,
        "received": received,
    }


def _validation_refusal(error: RequestValidationError) -> JSONResponse:
    """Every `422` this service's request validation issues, bounded and shaped.

    Two shapes and the split is by what the refusal is *about*, which is the split
    `docs/api/http.md` already documents rather than a fourth one invented here:

    - a **ceiling this service declares** is one of this service's own semantic refusals, so it
      takes the `{"reason", "message", ...}` object `_research_refusal` uses on the same route;
    - everything else keeps FastAPI's list, because `V2-P4-051` measured what it costs a client
      when two `422` bodies cannot be told apart, and `loc` with a small echo is the useful
      answer to "you sent a string where a float goes".

    The object shape is taken only when **every** fault is a ceiling fault, and that condition is
    measured rather than assumed. `BatchSubmitRequest.requests` declares `min_length=1`, and a
    body whose items all fail their own validation produces a `too_short` *alongside* the item
    faults -- pydantic counts what survived, which is nothing -- so a rule of "the first ceiling
    fault wins" answers "you sent too few" to a caller who sent a thousand malformed requests.
    Measured on five malformed items: `Counter({'missing': 25, 'too_short': 1})`. When a ceiling
    fault is derivative like that, the item faults are the answer and the list is their shape.

    Where the object shape does apply, the first ceiling fault wins rather than all of them --
    `screen`'s rule for malformed records: a caller who sent one collection too long wants one
    sentence, and enumerating every fault in a whole-market body is this row's own defect as a
    wall of text instead of as a copy.
    """
    faults = list(error.errors())
    ceilings = [fault for fault in faults if fault.get("type") in _DECLARED_CEILING_TYPES]
    if ceilings and len(ceilings) == len(faults):
        return JSONResponse(
            status_code=422, content={"detail": _declared_ceiling_detail(ceilings[0])}
        )
    listed = [
        {**fault, "input": _elided(fault.get("input")), "loc": list(fault.get("loc", ()))}
        for fault in faults[:MAX_VALIDATION_ERRORS]
    ]
    dropped = len(faults) - len(listed)
    if dropped:
        listed.append(
            {
                "loc": [],
                "type": "errors_elided",
                "msg": f"{dropped} further validation error(s) were not listed",
                "input": None,
            }
        )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(listed)})


def _research_refusal(error: Exception, *, index: int | None) -> HTTPException:
    """Turn a rejected research result into this service's `{"reason", "message"}` refusal.

    The shape is `_panel_detail`'s, which `docs/api/http.md` already documents as the thing a
    client switches on (`detail.reason`); this route joining it is the point of the row rather
    than an incidental tidy-up. `index` is `None` on `POST /api/v1/reports`, which parses one
    result rather than a list, and is carried as an explicit `null` so a client reads the same
    keys from both routes.
    """
    path = "research" if index is None else f"research[{index}]"
    if isinstance(error, ResearchIntegrityError):
        detail: dict[str, Any] = {
            "reason": error.reason,
            "message": (
                f"{path} (subject {error.subject}) carries {error.field.rsplit('.', 1)[-1]} "
                f"{error.claimed!r} but its own content derives {error.derived!r}. All three "
                "identifiers on a research result are content-derived, so send the record back "
                "exactly as this service handed it out, or omit the identifier and let it be "
                "re-derived."
            ),
            "index": index,
            "subject": error.subject,
            "field": f"{path}.{error.field}",
            "claimed": error.claimed,
            "derived": error.derived,
        }
        return HTTPException(status_code=422, detail=detail)
    return HTTPException(
        status_code=422,
        detail={
            "reason": "malformed_research_result",
            "message": (
                f"{path} is not a well-formed research result and was refused before any "
                f"identifier could be checked: {error}"
            ),
            "index": index,
            "subject": None,
            "field": path,
            "claimed": None,
            "derived": None,
        },
    )


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
    subject = result.signal.subject
    if claimed_signal_id != result.signal.signal_id:
        raise ResearchIntegrityError(
            reason="signal_id_mismatch",
            field="signal.signal_id",
            claimed=claimed_signal_id,
            derived=result.signal.signal_id,
            subject=subject,
        )
    if claimed_decision_id != result.decision.decision_id:
        raise ResearchIntegrityError(
            reason="decision_id_mismatch",
            field="decision.decision_id",
            claimed=claimed_decision_id,
            derived=result.decision.decision_id,
            subject=subject,
        )
    if claimed_manifest_id != result.manifest.run_manifest_id:
        raise ResearchIntegrityError(
            reason="run_manifest_id_mismatch",
            field="manifest.run_manifest_id",
            claimed=claimed_manifest_id,
            derived=result.manifest.run_manifest_id,
            subject=subject,
        )
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
        "not_held": 404,
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
  before the `as_of`, the declared components disagree about which instant they share, a declared
  component's stored cross section admits no value at all, or a supplied signal names a security
  the funnel did not shortlist. A refusal body.
- **`panel_unreadable` (409)** -- `ShortlistPanelUnreadableError`: a partition this screen needs is
  missing, damaged, stale or holds rows that were not knowable at the stated `as_of`.
- **`not_held` (404)** -- `ShortlistNotHeldError` on `GET /api/v1/shortlists/{shortlist_id}`:
  the address is well formed and this runtime directory holds no answer under it, or holds one
  that no longer hashes to it. `404` and not `409`, because there is no conflict with anything --
  the resource does not exist here. A *malformed* address is `bad_request` below and never this,
  so "that is not an address" and "nothing is filed under that address" stay two answers rather
  than one 404 covering both.
- **`bad_request` (422)** -- `ShortlistRequestError`: a factor no registry declares, a weight that
  is not positive, a processed-tier screen with no transform, a `neutralization` on a tier that has
  none, a `position_capital` at or above `shortlist_view.POSITION_CAPITAL_CEILING`, a naive
  `as_of`, or a retrieval address that is not `stable_answer_digest`'s own output.
- **`internal_error` (500)** -- the endpoint itself broke. **Nothing in this module raises it, and
  nothing that does reach it wears this table's body**: an exception no branch here anticipates is
  caught by Starlette, not by `_shortlist_refusal`, and the caller gets `text/plain` `Internal
  Server Error` with no `reason` at all. The row records a code that is already spoken for,
  exactly as `cli.CLICK_USAGE_EXIT_CODE` does.

  This is not hypothetical and the docstring used to say only the first half. `V2-P4-045` measured
  `position_capital=1e26` arriving exactly that way: `decimal.InvalidOperation` is an
  `ArithmeticError`, so it passed every `except ShortlistViewError` on all three faces.
  `shortlist_request` now refuses that value by name, which is where a *caller-supplied* number
  has to be stopped -- a `500` here means a defect in this repository, and the remedy is a bug
  report rather than a different request.

**`409` therefore carries two body schemas here, and `detail` is the discriminator** --
`PANEL_HTTP_STATUS`' own arrangement, unchanged: a verdict body has `is_blocked` and no `detail`
key, and a refusal body is `{"detail": {"reason": ..., "message": ...}}`. A client that read
`json()["blocks"]` on every `409` would raise `KeyError` on the second, so it switches on
`"detail" in body` first.

**`422` also carries two body schemas, and there `"detail" in body` is not enough** -- `V2-P4-051`.
This module's refusal is that same `{"reason", "message"}` **object**, while a body FastAPI itself
rejected (an unparseable `as_of`, a misspelled field, a non-numeric `position_capital`, a wrong
`Content-Type`, malformed JSON) comes back as `{"detail": [...]}` -- a **list** of field errors.
Both have the key, so a client has to branch on `isinstance(detail, dict)`; `docs/api/http.md`
says so and `tests/integration/test_shortlist_interfaces.py` holds both shapes to it. The two are
deliberately not merged: the list names the offending field, which a flattened message would lose.
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


MODEL_HTTP_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "answered": 200,
        "refused": 409,
        "blocked": 409,
        "panel_unreadable": 409,
        "not_held": 404,
        "bad_request": 422,
        "internal_error": 500,
    }
)
"""What the model routes do about each situation, as one table.

`SHORTLIST_HTTP_STATUS`' sibling, row for row, and the reasoning is shared: a caller has
different remedies available and only the envelope to pick between them.

- **`answered` (200)** -- the schedule ran, or the prediction was registered, and the declared
  coverage floor admitted it.
- **`refused` (409)** -- the floor refused it. *Not* a fault: the measurement, both sides of the
  comparison and (for a daily run) the `record_id` the prediction was registered under are all on
  the body, which is a **verdict** rather than a `detail` object. A conflict with the current
  state of the panel and this model's coverage of it, which is what RFC 9110 reserves `409` for.
- **`blocked` (409)** -- `ModelRunBlockedError`: no declared column has a stored cross section in
  the range, two prediction days resolve to one session's market, a cross section produced no
  labelled row, the purge left a fold with nothing to train on. A refusal body.
- **`panel_unreadable` (409)** -- `ModelPanelUnreadableError`: a partition this run needs is
  missing, damaged, stale, or contradicts another about one session.
- **`not_held` (404)** -- `ModelNotHeldError` on `GET /api/v1/predictions/{record_id}`: the
  address is well formed and this installation holds no prediction under it, or holds one that no
  longer hashes to it. A *malformed* address is `bad_request` and never this, so "that is not an
  address" and "nothing is filed under that address" stay two answers.
- **`bad_request` (422)** -- `ModelRequestError`: a factor no registry declares, a family no
  implementation answers to, a horizon that is not countable in sessions, a reading `as_of`
  before the range it reads, a `predict_at` inside the training range, a declared
  `feature_version` that is not the recipe these columns address.
- **`internal_error` (500)** -- the endpoint itself broke. Nothing in this module raises it, and
  nothing that does reach it wears this table's body: an exception no branch anticipates is caught
  by Starlette and the caller gets `text/plain` with no `reason` at all. The row records a code
  that is already spoken for, exactly as `SHORTLIST_HTTP_STATUS`' own does.

  **This sentence was false the day it was written, and `V2-P4-088` is how.** It arrived in
  `f81b0f5` -- `V2-P4-021`'s own commit, and the same commit that put `predictions.put` outside
  `run_daily`'s only `try`. The store seals a batch against a deadline it derives from the
  calendar, so a prediction day in the last `horizon.sessions + 1` sessions of a year-keyed
  calendar reached `/models/daily-run` as `CalendarHorizonError` and left it as `500 text/plain`.
  It is true again, and the way it is kept true is not this paragraph:
  `model_view._OUTCOME_WINDOW_FAULTS` names the two refusals building an outcome window raises,
  both places that build one catch it, and `tests/integration/test_year_end_daily_run.py` drives
  a whole year of 2026 at this route.

**`409` carries two body schemas here and `detail` is the discriminator**, unchanged from the
shortlist plane: a verdict body has `is_blocked` and no `detail` key, and a refusal body is
`{"detail": {"reason": ..., "message": ...}}`. **`422` also carries two**, and there
`"detail" in body` is not enough (`V2-P4-051`): this module's refusal is that same
`{"reason", "message"}` **object**, while a body FastAPI itself rejected comes back as
`{"detail": [...]}` -- a **list** of field errors. A client branches on `isinstance(detail, dict)`.
"""


def _model_refusal(error: ModelViewError) -> HTTPException:
    """One `model_view` fault, enveloped by the row of `MODEL_HTTP_STATUS` it names.

    Looked up by `error.reason` rather than switched on by exception type, `_shortlist_refusal`'s
    rule and its reason: a fault added to `model_view.py` with no row here raises `KeyError` at
    this boundary -- a `500` that says the table is incomplete -- instead of being quietly
    enveloped as whichever branch an `isinstance` chain happened to end on.
    """
    return HTTPException(
        status_code=MODEL_HTTP_STATUS[error.reason],
        detail=_panel_detail(error.reason, error.disclosable),
    )


class ModelFeatureApiRequest(BaseModel):
    """One column of the declared feature matrix: a factor, a tier and the specs narrowing it.

    A model rather than a free-form object so that a body naming `factors` or `tiers` is refused
    by FastAPI's own `422` with the offending key, rather than reaching `feature_columns` and
    being reported as a missing one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: str
    tier: str
    transform: str | None = None
    neutralization: str | None = None


class ModelHyperparameterApiRequest(BaseModel):
    """One declared hyperparameter, as a name and a flat scalar.

    A list of `{name, value}` objects rather than a JSON object, because
    `AlphaModelDeclaration.hyperparameters` is an ordered tuple of pairs whose *order* reaches the
    artifact's address -- and a JSON object cannot express "this list is sorted" in a way a
    contract can refuse. The face sorts, exactly as the CLI does, so the two spellings of one
    declaration produce one address.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    value: bool | int | float | str


class ModelRunApiRequest(BaseModel):
    """Every parameter both model routes share, and only the last five have a default.

    `model_view`'s resolvers refuse a default for each of the rest and this model does not
    reinstate one: a browser that omitted `minimum_scored_ratio` would otherwise get a bar nobody
    chose on the one question these routes are gated by.

    `code_commit`, `config_digest` and `feature_version` are `None` -- *unset* -- rather than
    `""`, which is `V2-P4-046`'s measured requirement and not a style: with an empty-string
    default there is no value the parser can hand back that means "the caller typed an empty one",
    so `code_commit or None` resolves it server-side and the same literal is refused on one face
    and accepted on another. Omitting still resolves; declaring empty is refused everywhere.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    features: tuple[ModelFeatureApiRequest, ...]
    name: str
    family: str
    horizon: str
    seed: int
    start: date
    end: date
    as_of: datetime
    years: tuple[int, ...]
    exchange: str
    minimum_scored_ratio: float
    code_commit: str | None = None
    config_digest: str | None = None
    shelf_life_days: int | None = None
    """`V2-P4-018`'s span, and `None` here means *declared none* rather than *unset*.

    The one optional field on this model whose absence is not resolved server-side into something
    else. `code_commit` and the two beside it are `None`-as-unset and get filled in; this stays
    `None` all the way to `AlphaModelArtifact.is_stale_at`, and the answer body renders
    `shelf_life_days: null` so a reader sees that no expiry was asked for rather than guessing
    which one was assumed.
    """
    feature_version: str | None = None
    hyperparameters: tuple[ModelHyperparameterApiRequest, ...] = ()

    @property
    def declared_features(self) -> list[dict[str, object]]:
        """The feature mappings `model_view.feature_columns` takes."""
        return [feature.model_dump() for feature in self.features]

    @property
    def declared_hyperparameters(self) -> tuple[tuple[str, bool | int | float | str], ...]:
        """The declared hyperparameters in the order a declaration has them.

        Through `model_view.declared_hyperparameters`, which is the same call
        `cli._model_hyperparameters` makes. It used to be this face's own `sorted(...)` over whole
        `(name, value)` pairs described as "`cli._model_hyperparameters`' rule" -- and it was not
        that rule: `V2-P4-091` measured the two disagreeing on a name declared twice with values
        of two types, where sorting the pair compares the values and raises `TypeError` on the way
        to a `500`. The comment claiming they agreed is what kept it invisible, so the agreement
        is a shared call now rather than a sentence.
        """
        return declared_hyperparameters((item.name, item.value) for item in self.hyperparameters)


class ModelEvaluateApiRequest(ModelRunApiRequest):
    """A walk-forward schedule laid over the shared parameters. None of the three has a default."""

    folds: int
    test_days_per_fold: int
    embargo_sessions: int


class ModelDailyRunApiRequest(ModelRunApiRequest):
    """One prediction instant laid over the shared parameters.

    `predict_at` is the instant the prediction is **about** and never the instant it is produced
    at: the latter is this service's own clock, which is what the prediction store compares its
    reading against and is therefore the one input a caller must not be able to choose.
    """

    predict_at: datetime


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


def _undeclared_risk_flag_refusal(
    error: UndeclaredRiskFlagError,
    *,
    evidence: Sequence[EvidenceSnapshot],
) -> HTTPException:
    """One undeclared `quality_flags` string, enveloped as the `422` pydantic would have written.

    ## Why this route needs a hand-written field error at all (`V2-P4-101`)

    `risk_flags` reaches this application by two roads. On `POST /api/v1/research/deliberate` it
    is a *field of the request body*, so pydantic validates it against `RiskFlag` and produces
    its own field error -- `loc == ["body", "signal", "risk_flags", 0]`, the offending `input`,
    and a `msg` listing all ten declared flags. On `POST /api/v1/research/run` it arrives inside
    `EvidenceSnapshot.payload`, which is a `JsonValue` with **no schema** by design, so pydantic
    has nothing to check it against and the string reaches `agents/baseline.py::_quality_flags`
    intact. That is where `V2-P4-030` closed the vocabulary, and until this function existed the
    refusal escaped the route uncaught: Starlette answered `500 text/plain Internal Server
    Error`, which tells a producer nothing and tells an operator, falsely, that this repository
    has a defect.

    ## Why it copies pydantic's shape rather than inventing a body

    Because a client should not have to learn that one field has two refusal dialects depending
    on which route it entered by. The body is the `{"detail": [ ... ]}` **list** of field errors
    -- the second of the two `422` schemas this module's docstring records, and deliberately not
    the `{"reason", "message"}` object a panel refusal carries, which a client tells apart with
    `isinstance(detail, dict)`. `msg` is `UndeclaredRiskFlagError.expected` under pydantic's own
    `"Input should be ..."` preamble, in declaration order, so the two bodies are equal string
    for string; `tests/integration/test_undeclared_risk_flag_surfaces.py::
    test_the_two_faces_of_one_vocabulary_refuse_the_same_string_the_same_way` asserts exactly
    that and would go red on a second dialect.

    ## The `loc`, which is the half a producer cannot reconstruct

    `_quality_flags` re-raises with the offending snapshot's `evidence_id` and the flag's
    position, and this maps the former back to its index in the request's own `evidence` array.
    `evidence_id` rather than an index is what crosses the agent boundary because an agent sees
    only the items of its own family -- `MarketAgent` keeps `market_event` and drops the rest --
    so a positional index taken there would name the wrong item on any request mixing families.
    The lookup is by identity here, where the unfiltered array is in scope.

    If the id is not found the `loc` stops at `["body", "evidence"]` rather than guessing a
    position. That branch is unreachable through this route (the engine is handed exactly this
    array) and is written as a widening rather than a `raise` because a wrong address is worse
    than a general one: it would send a producer to edit an item that is correct.
    """
    location: list[str | int] = ["body", "evidence"]
    index = next(
        (
            position
            for position, item in enumerate(evidence)
            if item.evidence_id == error.evidence_id
        ),
        None,
    )
    if index is not None:
        location.extend([index, "payload", "quality_flags"])
        if error.flag_index is not None:
            location.append(error.flag_index)
    return HTTPException(
        status_code=422,
        detail=[
            {
                "type": "enum",
                "loc": location,
                "msg": f"Input should be {error.expected}",
                "input": error.value,
                "ctx": {"expected": error.expected},
            }
        ],
    )


def _normalised_segment(segment: str) -> str:
    """One path segment, reduced to the form the two owner sets are compared in.

    Case-folded, and stripped of trailing dots and spaces. `V2-P5-030`: the owner sets were
    compared as raw text, so the segments that belong to the API and to the build were claimed
    under exactly one spelling each. Measured through raw ASGI, against the same
    `create_app(web_dir=None)` baseline that answers `404 application/json` to every one of
    them:

        GET /API/v1/nope        -> 200 text/html
        GET /api./v1/nope       -> 200 text/html
        GET /api /v1/nope       -> 200 text/html
        GET /Assets/missing.js  -> 200 text/html

    That is the sentence `SinglePageFallbackFiles` says it exists to prevent, arriving through
    the door nobody checked: a caller that branches on `response.ok` reads a page as a payload,
    and a case typo is a client-side typo like any other.

    `/Assets/` is the one that matters most and the one least likely to be noticed, because it
    is not the same defect on both machines. macOS is case-insensitive, so `/Assets/index.js`
    resolves to the real file and is never seen; on the Linux the `Dockerfile` ships, the
    lookup fails and the shell is served as `text/html` to a `<script>` tag -- the MIME-type
    error the build-directory owner exists to prevent, reproducible only in production.

    **This is deliberately broader than HTTP**, where paths are case-sensitive and
    `/API/v1/nope` genuinely is a different resource. The asymmetry is what justifies it: a
    client area whose first segment collides with an owner under this rule is caught, loudly
    and immediately, by `test_every_client_area_is_an_address_the_production_server_serves`,
    which asks the server for every segment in `web/src/routes.ts` and requires the shell. A
    reserved namespace silently answering as a page is caught by nobody, and is read by a
    caller rather than by a test.

    Trailing dots and spaces go for the same reason and not a different one: they are what a
    permissive filesystem and a hand-edited config both drop, so `api.` and `api ` are ways of
    writing `api` that no client meant as a page. A segment that is *only* dots and spaces
    normalises to the empty string and is not a location at all, which is the same answer
    `.` and `..` already got.
    """
    return segment.rstrip(". ").casefold()


class SinglePageFallbackFiles(StaticFiles):
    """Serve the built web app so that every address the client router has is an address.

    `V2-P5-027`. `StaticFiles(html=True)` falls back to `index.html` only for *directory*
    requests, so an unmatched path is a `404`. Measured on `12532e3` with a `TestClient` over
    the real `create_app` and a real `pnpm build`: `/` was `200`, and `/data-health`,
    `/shortlists`, `/shortlists/sl_abc`, `/factor-lab`, `/factor-lab/fxp_abc` and `/portfolio`
    were all `404`. Every bookmarkable URL pages ① through ④ added worked under `vite dev`,
    which has its own history fallback, and 404'd under `openalpha serve`. The e2e suite could
    not see it, because the thing answering in development was the dev server.

    ## The rule

    A `GET`/`HEAD` this build cannot answer is answered with `index.html` at `200`, **unless
    its first path segment belongs to somebody else**. Two owners exist and neither is written
    down -- both are derived, so a route family or a build directory added later is covered
    without anybody remembering this class. Ownership is decided on `_normalised_segment`
    rather than on raw text, because the sets were compared with `==` until `V2-P5-030` and
    `/API/v1/nope`, `/api./v1/nope` and `/Assets/missing.js` were therefore all pages:

    - `reserved_roots`, the first path segments of the live route table (`api`, `health`,
      `docs`, `redoc`, `openapi.json` today), passed in by `create_app` after every route is
      registered. **This is the load-bearing half.** Without it an unknown `/api/v1/...` --
      a client-side typo, a stale caller, a renamed route -- comes back as an HTML `200`, and
      a caller that branches on `response.ok` reads a page as a payload. The refusal has to
      stay in the API's own vocabulary, which is JSON.
    - the directories inside the build itself (`assets` today). A subresource that is missing
      is a corrupted deploy; answering it with `text/html` turns a clean `404` into a
      MIME-type error several layers from the cause.

    ## What a method may say, and what it may not

    A non-`GET`/`HEAD` request to a client address is a `405`, because the page is there and
    the verb is wrong. A non-`GET`/`HEAD` request to anything else is a `404` unless the build
    really holds it -- `StaticFiles` checks the method before the lookup and would otherwise
    answer `405` for every unmatched path in every namespace, which asserts existence about
    paths that do not exist. See `get_response`.

    ## Unknown non-API paths get the shell, and that is a decision

    `/no-such-page` is served `index.html` at `200`, and `AppRouter`'s `NotFoundPage` renders
    the `role="alert"` that names the address. The alternative -- refusing at the server --
    requires this file to hold a second copy of the client's route table, which is exactly the
    defect `V2-P5-011` measured on `CORS_ALLOWED_METHODS`: two statements of one fact, nothing
    keeping them equal, and the copy already behind the original. The cost of the choice taken
    is that a mistyped address is `200` rather than `404`; the cost of the other one is that a
    *working* page 404s the day someone adds a route to `web/src/routes.ts` and does not think
    to edit Python. `tests/unit/test_spa_addressability.py` holds the two sides in
    correspondence so that the collision case -- a client area landing on a reserved segment --
    goes red naming the segment.

    ## What was measured about `vite dev` rather than copied from it

    `vite 8.1.5`, probed directly: it has **no** extension rule (`/no-such.page` and
    `/stocks/000001.SZ` are both `200` shell) and it serves the shell for `/assets/missing.js`
    too; its protection of `/api` comes from `vite.config.ts`'s hand-written proxy list, not
    from the fallback -- a list that is already missing `/docs` and `/redoc`. So development is
    followed on addressability and deliberately not followed on those two points, each of which
    would ship a defect. Nothing here keys on `Accept` either, which `vite` does: a rule that
    answers `curl` differently from a browser makes an incident unreproducible from a terminal.

    Being a `StaticFiles` subclass rather than a catch-all route is deliberate: the fallback is
    *how the build is served*, not a capability of this API, so it stays out of the route table
    that `tests/unit/test_surface_parity.py` counts.
    """

    def __init__(self, *, directory: Path, reserved_roots: frozenset[str]) -> None:
        super().__init__(directory=directory, html=True)
        self._reserved_roots = frozenset(_normalised_segment(root) for root in reserved_roots)
        self._build_roots = frozenset(
            _normalised_segment(child.name) for child in directory.iterdir() if child.is_dir()
        )

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Delegate; turn a `404` for a *location* into the shell and a `405` for nothing into
        a `404`.

        Two corrections to `StaticFiles`, and they are about opposite halves of the same
        sentence -- what exists, and what may be done to it.

        **A location gets the shell.** `404` and not every `HTTPException`: `POST /portfolio`
        answered with a page would be a worse lie than the `405`, because the page really is
        there and the verb really is wrong. That guard has a surviving mutant and it is
        reported rather than hidden: deleting `error.status_code == 404` from this branch
        leaves every test green, because re-entering with `index.html` re-raises the identical
        `405` -- `starlette` re-checks the method before the lookup. The two versions are
        indistinguishable through the HTTP surface today and no honest test separates them. It
        is kept as the fail-closed shape for a status this library does not raise yet.

        **A `405` for something that is not there is a `404`.** `StaticFiles` checks the method
        *before* the lookup, so every non-`GET`/`HEAD` request to every unmatched path got
        `405` -- including the API's own namespace. Measured through raw ASGI against a
        `create_app(web_dir=None)` baseline:

            POST /api/v1/nope   405 application/json   <- with the mount
            POST /api/v1/nope   404 application/json   <- without it

        Mounting the build changed what a misspelled, renamed or retired API path says about
        itself, and `405` says the resource exists. A caller that reads `404` as "gone" reads
        `405` as "still there, wrong verb" and retries a path that will never answer. The
        class docstring only ever reasoned about `POST /portfolio`, which is the case where
        `405` is right, and `tests/unit/test_spa_addressability.py` pinned only that one.

        Whether the thing exists is asked by **replaying the request as a `GET`** rather than
        by a second copy of the lookup rules: a directory with no `index.html` and a file that
        is really there answer differently, and only `starlette` knows which is which here.
        """
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if self._is_a_client_location(path):
                if error.status_code == 404:
                    return await super().get_response("index.html", scope)
                raise
            if error.status_code == 405 and not await self._a_get_would_have_found_it(path, scope):
                raise StarletteHTTPException(status_code=404) from error
            raise

    async def _a_get_would_have_found_it(self, path: str, scope: Scope) -> bool:
        """Whether this build holds anything at `path`, asked in `starlette`'s own words.

        Called only to decide whether a `405` is a true sentence, and only for paths that are
        not client locations. The response is built and dropped; `FileResponse` does not read
        the file to be constructed, so the cost is the `stat` that the refused request would
        have paid anyway.
        """
        try:
            await super().get_response(path, {**scope, "method": "GET"})
        except StarletteHTTPException as error:
            return error.status_code != 404
        return True

    def _is_a_client_location(self, path: str) -> bool:
        """Whether this mount-relative path is a place the client router could be showing.

        `path` has already been normalised by `StaticFiles.get_path`, so `.` (the root) and
        `..` (an escape attempt) are the two non-segment values it can start with; neither is
        a location, and neither should be dressed up as one.

        The comparison is against `_normalised_segment`, not the raw text -- see that
        function for what was measured and why the asymmetry justifies it.
        """
        raw = path.split("/", 1)[0]
        if raw in {"", ".", ".."}:
            return False
        root = _normalised_segment(raw)
        if not root:
            return False
        return root not in self._reserved_roots and root not in self._build_roots


def _reserved_root_segments(application: FastAPI) -> frozenset[str]:
    """The first path segment of every route the application already answers.

    Derived from the live table rather than written down, so `SinglePageFallbackFiles` cannot
    fall behind a route added next week -- the failure mode this repository has already
    measured twice, on `CORS_ALLOWED_METHODS` (`V2-P5-011`) and on the citation tables
    `tests/unit/test_source_cited_tests.py` was written for.

    Call it *after* every route is registered and before the mount is added; `create_app` does.

    `getattr` rather than `route.path`: `application.routes` is typed `list[BaseRoute]`, and
    `BaseRoute` declares no `path` -- only `Route`, `WebSocketRoute` and `Mount` carry one. A
    `cast` here would be a claim about a list this function does not own, so the attribute is
    asked for and the answers that are not strings are skipped.
    """
    segments: set[str] = set()
    for route in application.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str) and path.startswith("/"):
            segments.add(path.split("/")[1])
    segments.discard("")
    return frozenset(segments)


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
    shortlist_store = storage.shortlist_store
    memory = storage.memory
    recovery_store = storage.recovery_store
    batch_store = storage.batch_store
    portfolio_ledger = storage.portfolio_ledger
    watchlist_store = storage.watchlist_store
    report_store = storage.report_store
    validation_store = storage.validation_store
    experiment_store = storage.experiment_store
    prediction_store = storage.prediction_store
    job_store = storage.job_store

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
    # Added first, so it is the *inner* of the two: Starlette wraps `user_middleware[0]`
    # outermost and `add_middleware` inserts at position 0. The order matters and it is not
    # the one this file had. `SecurityHeadersMiddleware` short-circuits an oversized body, and
    # while it was outermost that `413` never passed through the CORS layer -- so a browser
    # asking cross-origin got an opaque network failure instead of the refusal this service
    # went to some trouble to word (`V2-P4-043`). With CORS outermost, every refusal below it
    # carries `Access-Control-Allow-Origin` and a browser can read it.
    #
    # The one thing CORS-outermost gives up is the hardening headers on a *preflight*
    # response, which Starlette answers itself without calling inward. A preflight carries no
    # body and renders nothing, so there is nothing for a content policy to protect; every
    # response that a browser actually surfaces still comes back through
    # `SecurityHeadersMiddleware`.
    application.add_middleware(
        SecurityHeadersMiddleware,
        max_request_bytes=request_limit,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(CORS_ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=list(CORS_ALLOWED_METHODS),
        allow_headers=["Content-Type"],
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
        """Execute the shared live/replay/backtest research cycle.

        The one catch is by type and by exactly one type (`V2-P4-101`). `except ValueError` here
        would be wide enough to report an unrelated arithmetic or parsing defect in this
        repository as the caller's spelling mistake -- the over-broad catch `V2-P4-045` booked
        against the shortlist face -- so anything that is not an undeclared risk flag still
        reaches Starlette and is still a `500`, which for a genuine internal defect is the
        honest answer.
        """
        engine = ResearchEngine(
            repository=run_repository,
            memory=memory,
            clock=clock,
            recovery_store=recovery_store,
        )
        try:
            return engine.run_cycle(request)
        except UndeclaredRiskFlagError as error:
            raise _undeclared_risk_flag_refusal(error, evidence=request.evidence) from error

    @application.post("/api/v1/research/deliberate")
    def research_deliberate(request: DeliberationApiRequest) -> DeliberationOutcome:
        """Run an explicit, ablatable bull/bear and risk committee."""
        return DeliberationCommittee().review(
            signal=request.signal,
            results=request.agent_results,
        )

    @application.post("/api/v1/screen")
    def screen(request: ScreeningApiRequest) -> ScreeningResult:
        """Rank verified research results by explicit screening criteria.

        Parsed one record at a time rather than inside a generator expression, because the
        index of the offending record is half of what `V2-P4-041` is about and a comprehension
        discards it. The refusal is still on the first fault: a caller who edited one record
        wants to be told which, and enumerating every fault in a 5,545-record body would
        reproduce this row's defect as a wall of text instead of a single sentence.
        """
        results = []
        for index, item in enumerate(request.research):
            try:
                results.append(_parse_research_result(item))
            except (KeyError, TypeError, ValueError) as error:
                raise _research_refusal(error, index=index) from error
        return ResearchScreener().screen(results=tuple(results), criteria=request.criteria)

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
            raise _research_refusal(error, index=None) from error
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

    @application.get("/api/v1/reports/{report_id}/export")
    def report_export(report_id: str) -> ReportExport:
        """Assemble one report's shareable form, with restricted payloads withheld.

        `V2-P5-022`, PRD Implementation Decision 27: 不导出 Tushare 原始 payload. The rule and
        every word about it live in `product/export.py`; this route resolves the report's
        evidence at the report's own clock and hands the result to it, exactly as
        `OpenAlphaSDK.export_report` and `openalpha report export` do.

        A separate address from `GET /api/v1/reports/{report_id}` rather than a `?evidence=1`
        flag on it, because they are two different artifacts: one is the report, and one is a
        thing a user may hand to somebody else. A query parameter would let the safe answer and
        the shareable one be confused for each other in a caller's log.
        """
        report = report_store.get(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report was not found.")
        evidence = evidence_store.query(as_of=report.created_at, subject=report.subject)
        return build_report_export(report=report, evidence=evidence)

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
    def batch_list(
        limit: int = Query(default=DEFAULT_BATCH_PAGE_SIZE, ge=1, le=MAX_BATCH_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> BatchTaskPage:
        """List durable research batches as summaries, one page at a time.

        `V2-P4-040`. This route was `return batch_store.list()`, which inlined every item of
        every batch: twenty whole-market batches measured `items: 115,355, bytes: 36,857,096`
        (36.9 MB) in 2.35s, and three batches already exceeded the 8 MiB body this same service
        refuses on the way *in*. `V2-P4-019` raised `MAX_BATCH_ITEMS` tenfold and this listing
        did not follow, so a listing became a bulk export.

        A summary carries no field that grows with a batch's item count -- the items themselves
        are one route away at `GET /api/v1/research/batches/{batch_id}`, which is unchanged --
        so `MAX_BATCH_PAGE_SIZE` bounds this response in bytes and not merely in rows.

        **This changes the response shape, and deliberately.** It was `[BatchResearchTask, ...]`
        and is now `{"batches": [...], "total": n, "limit": n, "offset": n}`. Nothing in this
        repository consumed it -- no test, no `sdk.py` method, no page under `web/` -- and the
        shape it had could not answer the question it exists for at the scale the same release
        made reachable. No stored contract moved, so AGENTS.md's rule 3 migration is not in play;
        `CHANGELOG.md` records it for callers outside this tree.
        """
        return BatchTaskPage(
            batches=batch_store.list_summaries(limit=limit, offset=offset),
            total=batch_store.count_batches(),
            limit=limit,
            offset=offset,
        )

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

    @application.get("/api/v1/jobs")
    def job_list() -> dict[str, list[dict[str, object]]]:
        """Every trading-day schedule this installation holds, by name (`V2-P5-013`).

        `V2-P5-010` shipped the scheduling primitive with no route at all and said so in its own
        row. This is the read half of the face, and it is **only** the read half, deliberately:
        this service has no authentication of any kind (audit `F101`'s second sentence, still
        open), so declaring a schedule and taking a lease stay on the machine that holds the
        runtime directory, where `openalpha jobs register` and `openalpha jobs run` are. What an
        unauthenticated reader can have is the answer to *is the daily job running*, which is
        the operational question and carries no authority.

        Rendered through `job_contracts.scheduled_job_view`, the same function `openalpha jobs
        list --json` calls, so the two cannot come to describe one schedule two ways --
        `tests/integration/test_scheduled_job_faces.py` asserts the bodies equal rather than
        alike.
        """
        return {"jobs": [scheduled_job_view(job) for job in job_store.list_jobs()]}

    @application.get("/api/v1/jobs/{job_id}")
    def job_get(job_id: str) -> dict[str, object]:
        """One schedule and every per-session attempt it has recorded, ascending by session.

        The runs are on this route and not on the listing above for `GET /api/v1/shortlists`'
        reason: a job accumulates one row per trading session, so roughly 250 a year, and a
        listing that carried them all would grow without bound while answering a question
        ("which schedules exist") that never needed them.

        A name no schedule is registered under is `404` with `_panel_detail`'s `{reason,
        message}` object -- the shortlist plane's own `not_held` row -- and the message is
        byte-identical to the one `openalpha jobs due` prints, because both faces call
        `_job_not_registered`. A bare `"Not Found"` string here would be indistinguishable from
        the router's answer for a path that does not exist.
        """
        job = job_store.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=SHORTLIST_HTTP_STATUS["not_held"],
                detail=_panel_detail("not_held", job_not_registered(job_id)),
            )
        return {
            "job": scheduled_job_view(job),
            "runs": [job_run_view(run) for run in job_store.runs(job_id)],
        }

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
        """Apply A-share execution, T+1, costs, and exposure limits.

        **The `except` is the whole of `V2-P5-013`'s fix on this route, and it is not
        decorative.** `SQLitePortfolioLedger.append` raises a bare `ValueError` when an
        `order_id` is reused with different content, and nothing caught it -- so resubmitting an
        order under an id the ledger already holds answered `500` `text/plain` `Internal Server
        Error`. That says "this repository has a defect" for a request the caller fixes by
        changing one field.

        **Narrow by type and by placement.** Only the *ledger* write is wrapped, not the
        simulation: `PortfolioSimulator` **returns** its disagreements with the market -- a
        suspended bar, a limit-locked price, a subject mismatch -- as `accepted: false`
        transitions with a `reason`, and those are correct `200` answers. Widening this to cover
        the simulation would report a fact about the market as a bad request, which is
        `V2-P4-045`'s over-broad catch in a second place.

        `detail` is a plain string here rather than the `{reason, message}` object the panel and
        shortlist planes use, and that is deliberate: those planes have a fault-reason table
        (`SHORTLIST_HTTP_STATUS`) whose rows the object discriminates between, and this route has
        exactly one refusal. Inventing a one-row table to carry it would be a shape a client has
        to branch on for no information.
        """
        transition = PortfolioSimulator(limits=request.limits).execute_order(
            state=request.state,
            order=request.order,
            market=request.market,
        )
        try:
            portfolio_ledger.append(transition)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return transition

    @application.get("/api/v1/portfolio/ledger")
    def portfolio_ledger_query(
        subject: str | None = None,
    ) -> tuple[PortfolioTransition, ...]:
        """List immutable order/execution transitions."""
        return portfolio_ledger.list(subject=subject)

    @application.post("/api/v1/portfolio/construct")
    def portfolio_construct(request: PortfolioConstructionApiRequest) -> JSONResponse:
        """Weight one admitted shortlist under a declared heuristic policy (`V2-P5-013`).

        `V2-P5-001` shipped `openalpha portfolio construct` and `OpenAlphaSDK
        .construct_portfolio` and left this face out; `V2-P5-013`'s measurement is what found
        it. It is `/api/v1/portfolio/construct` and not `/portfolios/`: `portfolio/execute` and
        `portfolio/ledger` already spell this noun singular, and a second spelling of one noun on
        one API is the drift this row exists to close rather than to add to.

        **Three lines of body, and that is the point.** The read is `held_shortlist`, the policy
        is `construct_portfolio`, and the rendering is `construction_view` -- the same three the
        CLI and the SDK call, so the three faces cannot come to weight one list three ways.
        `tests/integration/test_portfolio_construction_interfaces.py` asserts this route's `200`
        body **byte-equal** to `openalpha portfolio construct --json`, and both refusals below
        equal to the sentence the CLI prints on stderr.

        ## What each refusal is

        - **`404`** -- `ShortlistNotHeldError`: a well-formed address this installation holds no
          answer under. Through `_shortlist_refusal`, so it is the same row of
          `SHORTLIST_HTTP_STATUS` that `GET /api/v1/shortlists/{shortlist_id}` answers with.
        - **`422` with a `{reason, message}` object** -- a request with no answer:
          `ShortlistRequestError` for a malformed address, `PortfolioConstructionError` for a
          shortlist the gate refused (`admitted` is null), for an admitted list holding no names,
          and for a declared `max_industry_weight` over candidates that carry no industry -- the
          refusal `V2-P5-001` chose over an unenforceable cap, which arrives here unchanged
          because it is raised in the shared policy rather than at any one face.
        - **`422` with a list of field errors** -- a body pydantic itself rejected: a tier vector
          that does not sum to one, a weight that is not positive, a cap outside `(0, 1]`. Two
          shapes on one status code is this module's standing arrangement and `isinstance(detail,
          dict)` is the discriminator; see `SHORTLIST_HTTP_STATUS`.

        `ShortlistStoreError` is deliberately **not** caught. It is a fault in the store rather
        than in the request, and letting it reach Starlette is the same choice
        `shortlist_get` above makes one route over.
        """
        try:
            construction: PortfolioConstruction = construct_portfolio(
                candidates=candidates_from_shortlist_answer(
                    held_shortlist(shortlist_store, request.shortlist_id)
                ),
                policy=request.policy,
                previous=request.previous,
            )
        except ShortlistViewError as error:
            raise _shortlist_refusal(error) from error
        except PortfolioConstructionError as error:
            raise HTTPException(
                status_code=SHORTLIST_HTTP_STATUS["bad_request"],
                detail=_panel_detail("bad_request", str(error)),
            ) from error
        return JSONResponse(status_code=200, content=construction_view(construction))

    @application.post("/api/v1/backtests/portfolio")
    def portfolio_backtest(request: PortfolioBacktestRequest) -> PortfolioBacktestReport:
        """Run a multi-day A-share portfolio report and persist transitions.

        `portfolio_execute`'s `except` and its reason, on the route that reaches the same ledger
        through a series rather than through one order -- so one fault cannot answer two ways
        depending on which door the caller came through. `V2-P5-013`; both are asserted equal in
        `tests/integration/test_portfolio_route_refusals.py`.

        **Measured before it was believed.** This arrived reported as a regression that
        `V2-P5-003` introduced with a strictly-ascending-session check. Driven on `2746663`,
        before any of that row exists, the `500` is already here: `PortfolioBacktestRunner.run`
        appends every transition to the ledger, so resubmitting a backtest whose orders keep
        their ids reaches the same bare `ValueError`. That row adds a third road to a fault that
        already had one. Its check lands in this same `except` when it merges.

        Wrapping the whole `run` rather than the append alone, and that is the one difference
        from the route above: the runner owns the loop, so there is no seam between "simulate"
        and "record" to put a narrower guard at. It costs nothing here because the runner's
        *own* refusals are the same kind of fault -- a step series that cannot be put --
        while every disagreement with the market is still a returned rejection inside the report.
        """
        try:
            return PortfolioBacktestRunner(
                limits=request.limits,
                ledger=portfolio_ledger,
            ).run(initial=request.initial, steps=request.steps)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
            raise _research_refusal(error, index=None) from error
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
        limitation_detail: bool = True,
    ) -> JSONResponse:
        """Report what is wrong with the stored panel at a stated `as_of`.

        The HTTP twin of `openalpha panel doctor`, and distinct from `GET /health`, which is
        this service's dependency-free liveness probe. Per-dataset readiness and freshness, the
        cross-dataset checks with a record of which of them actually ran, and the datasets' own
        structural limitations kept separate from this fetch's defects.

        Always `200` when the request could be put: `is_clean` and a `counts_by_severity` total
        over all three severities are in the body. This endpoint grants nothing, so its status
        code claims nothing -- see `PANEL_HTTP_STATUS`.

        `limitation_detail=false` is `openalpha panel doctor --no-limitation-detail`, and it is
        here rather than on the CLI alone because the two faces answering differently about the
        same store is the drift this repository keeps measuring. `V2-P4-110`: the paragraphs were
        84.8% of a one-dataset report and do not depend on the panel.
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
            content=health_report_payload(report, limitation_detail=limitation_detail),
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
            result = run_shortlist(
                panel_store(root),
                resolved,
                built_at=clock(),
                runs=run_repository,
                shortlists=shortlist_store,
            )
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

    @application.get("/api/v1/shortlists")
    def shortlist_list() -> dict[str, list[str]]:
        """Every shortlist answer this installation holds, by content address, ascending.

        `GET /api/v1/factors/experiments`' twin, and its shape: a listing of keys rather than of
        bodies, because a shortlist answer is kilobytes and the caller almost always wants one.
        """
        return {"shortlist_ids": list(shortlist_store.list_ids())}

    @application.get("/api/v1/shortlists/{shortlist_id}")
    def shortlist_get(shortlist_id: str) -> JSONResponse:
        """One stored shortlist answer, by the `shortlist_id` its own body carried.

        `V2-P4-062`'s missing route. The three content addresses on a run's answer addressed
        nothing: `runtime/` held no shortlist artifact, this API had no `GET`, and a caller who
        wanted to compare today's list with yesterday's had to have saved `--json` themselves.

        **The body is the stored answer and not a re-run**, which is the whole point: it is what
        was published, byte for byte, and `shortlist_view.open_shortlist` re-derives the address
        from the content before handing it back, so a document edited on disk is a `404` rather
        than a shortlist somebody reads names off.

        Registered **after** `POST /api/v1/shortlists/run` and beside `GET /api/v1/shortlists`,
        which do not collide with it: the run route is a different method, and the listing route
        is a different path. FastAPI matches in declaration order, so `/api/v1/shortlists` cannot
        be swallowed by `{shortlist_id}` -- and a request for a literal `run` would be a `422`
        naming the address shape rather than a mis-routed run.
        """
        try:
            answer = held_shortlist(shortlist_store, shortlist_id)
        except ShortlistViewError as error:
            raise _shortlist_refusal(error) from error
        return JSONResponse(status_code=SHORTLIST_HTTP_STATUS["answered"], content=answer)

    @application.post("/api/v1/models/evaluate")
    def model_evaluate(request: ModelEvaluateApiRequest) -> JSONResponse:
        """Fit one declaration once per walk-forward fold and report what it ordered.

        `V2-P4-021`'s HTTP face, and the twin of `openalpha model evaluate` and of
        `OpenAlphaSDK.evaluate_model`. All three resolve through
        `model_view.model_evaluation_request` and run through `model_view.evaluate_model`, so
        they cannot come to fit three models from one declaration.

        **A refused evaluation answers `409` with `"admitted": null`, never `200` with `[]`.**
        That is `V2-P4-033`'s deliverable one plane over: the status code is what says the floor
        refused, and the body still carries every fold, the artifact each was fitted to, and both
        sides of the comparison it missed. See `MODEL_HTTP_STATUS`.
        """
        try:
            resolved = model_evaluation_request(
                columns=feature_columns(request.declared_features),
                name=request.name,
                family=request.family,
                horizon=request.horizon,
                seed=request.seed,
                start=request.start,
                end=request.end,
                as_of=request.as_of,
                years=request.years,
                exchange=request.exchange,
                folds=request.folds,
                test_days_per_fold=request.test_days_per_fold,
                embargo_sessions=request.embargo_sessions,
                minimum_scored_ratio=request.minimum_scored_ratio,
                shelf_life_days=request.shelf_life_days,
                code_commit=_resolved_code_commit(request.code_commit),
                config_digest=_resolved_config_digest(request.config_digest),
                feature_version=request.feature_version,
                hyperparameters=request.declared_hyperparameters,
            )
            result = evaluate_model(panel_store(root), resolved)
        except ModelViewError as error:
            raise _model_refusal(error) from error
        return JSONResponse(
            status_code=(
                MODEL_HTTP_STATUS["refused"] if result.is_blocked else MODEL_HTTP_STATUS["answered"]
            ),
            content=evaluation_view(result),
        )

    @application.post("/api/v1/models/daily-run")
    def model_daily_run(request: ModelDailyRunApiRequest) -> JSONResponse:
        """Fit on what has already closed, score the declared instant, and register the answer.

        The route Story S32 is about. The batch is produced with **this service's** clock as
        `predicted_at`, and `FilePredictionStore` stamps `recorded_at` with the same clock it was
        constructed with -- a caller cannot supply either, which is the entire mechanism behind
        `PredictionRecord.standing`.

        **A refused run still registered its prediction**, and the `record_id` is on the `409`
        body: the floor decides whether the answer may be acted on, and Story S32's requirement
        that a prediction be persisted before its outcome is known is unconditional.
        """
        try:
            resolved = daily_request(
                columns=feature_columns(request.declared_features),
                name=request.name,
                family=request.family,
                horizon=request.horizon,
                seed=request.seed,
                start=request.start,
                end=request.end,
                predict_at=request.predict_at,
                as_of=request.as_of,
                years=request.years,
                exchange=request.exchange,
                minimum_scored_ratio=request.minimum_scored_ratio,
                shelf_life_days=request.shelf_life_days,
                code_commit=_resolved_code_commit(request.code_commit),
                config_digest=_resolved_config_digest(request.config_digest),
                feature_version=request.feature_version,
                hyperparameters=request.declared_hyperparameters,
            )
            now = clock()
            result = run_daily(
                panel_store(root),
                resolved,
                predictions=prediction_store,
                runs=run_repository,
                predicted_at=now,
                started_at=now,
            )
        except ModelViewError as error:
            raise _model_refusal(error) from error
        except PredictionStoreError as error:
            raise HTTPException(
                status_code=MODEL_HTTP_STATUS["blocked"],
                detail=_panel_detail("blocked", str(error)),
            ) from error
        return JSONResponse(
            status_code=(
                MODEL_HTTP_STATUS["refused"] if result.is_blocked else MODEL_HTTP_STATUS["answered"]
            ),
            content=daily_view(result),
        )

    @application.get("/api/v1/predictions")
    def prediction_list() -> dict[str, object]:
        """Every registered prediction this installation holds, oldest custody first.

        `GET /api/v1/shortlists`' twin and its shape: a listing of keys rather than of bodies,
        because a prediction batch at market width is hundreds of kilobytes and a caller almost
        always wants one of them.

        **The order is the custody stamp and no longer the content digest** (`V2-P4-098`). A
        digest sort is uncorrelated with time, and this listing is read to answer which of two
        predictions was committed to first -- measured on five records, the one created third
        sorted first. `record_ids` keeps its name and its place and carries the new order;
        `predictions` is the same list with each row saying what it is, both standing sentences
        included, so a caller chooses which body to fetch rather than fetching all of them.
        """
        return prediction_index_view(held_predictions(prediction_store))

    @application.get("/api/v1/predictions/{record_id}")
    def prediction_get(record_id: str) -> JSONResponse:
        """One registered prediction, by the `record_id` its own run reported.

        **The body is what was registered and not a re-run.** `FilePredictionStore.get`
        re-derives the address from the content before handing it over, so a document edited on
        disk is a refusal rather than scores somebody trades on.

        Registered **after** `POST /api/v1/models/daily-run` and beside `GET /api/v1/predictions`,
        which do not collide with it: the run route is a different method on a different path, and
        the listing route is a different path. FastAPI matches in declaration order.

        The body carries the whole fitted artifact under `model` and `KNOWN_MODEL_VIEW_
        LIMITATIONS` under `limitations` -- `held_prediction_view`, shared with the command line,
        because this and `openalpha model prediction` are the two faces a stored prediction is
        read through a year later and neither has the run's own answer to hand.
        """
        try:
            record = held_prediction(prediction_store, record_id)
        except ModelViewError as error:
            raise _model_refusal(error) from error
        except PredictionStoreError as error:
            raise HTTPException(
                status_code=MODEL_HTTP_STATUS["not_held"],
                detail=_panel_detail("not_held", str(error)),
            ) from error
        return JSONResponse(
            status_code=MODEL_HTTP_STATUS["answered"], content=held_prediction_view(record)
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

    @application.exception_handler(RequestValidationError)
    async def validation_refusal(request: Request, error: RequestValidationError) -> JSONResponse:
        """Bound and shape every request-validation `422` this application issues.

        Registered on the application rather than written into each route, because the defect it
        answers is a property of the *transport*: any route with a declared collection ceiling
        refuses by echoing the collection, and two of them do today. A per-route fix would have
        left `POST /api/v1/research/batches` at 8.83 MiB while `POST /api/v1/screen` was fixed,
        which is `V2-P4-067(b)`'s shape one wave later. See `_validation_refusal`.
        """
        return _validation_refusal(error)

    configured_web_dir = web_dir if web_dir is not None else config.web_dir
    if configured_web_dir is not None:
        index = configured_web_dir / "index.html"
        if not index.is_file():
            raise ValueError(f"web_dir does not contain index.html: {configured_web_dir}")
        application.mount(
            "/",
            SinglePageFallbackFiles(
                directory=configured_web_dir,
                reserved_roots=_reserved_root_segments(application),
            ),
            name="web",
        )

    return application


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Build the module-scope `app` on first attribute access rather than at import.

    PEP 562. `uvicorn openalpha_cn.api.app:app` -- which is what `openalpha serve` passes and
    what the `Dockerfile` runs -- imports this module and then reads the attribute, and
    `from openalpha_cn.api.app import app` is the same two steps; both still get an application.
    What no longer gets one is `import openalpha_cn.api.app`, which is what a linter, a type
    checker, a documentation build or an editor's auto-import does, and which had no business
    creating a database.

    **`V2-P4-111`, and `create_app`'s own docstring is what says this line was wrong.** It makes
    a point of `app = create_app()` being "filesystem-free for `.env`: importing this module can
    never read a developer's real `.env`, regardless of the process's current working
    directory". It was not filesystem-free for `runtime/`: `create_app` calls `build_storage`,
    which runs migrations and takes a SQLite backup, so a bare import applied migrations and
    wrote a ~139 KB file into whatever `OPENALPHA_RUNTIME_DIR` pointed at -- the repository
    itself, by default. Measured: one import moved the file count in the user's own
    `runtime/backups/` from 126 to 127.

    A module-level `__getattr__` rather than a `lru_cache`d factory function, because the name
    `openalpha_cn.api.app:app` is a published ASGI entry point: it appears in the `Dockerfile`,
    in `cli.serve`, and in whatever deployment a user has written. Changing what that string
    means is a breaking change; changing *when* it is evaluated is not.

    The instance is cached, so repeated access returns one application rather than one per
    reference -- `app` was a singleton before and the two stores it owns are still opened once.
    """
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    global _app
    if _app is None:
        _app = create_app()
    return _app
