"""FastAPI application for OpenAlpha CN's versioned public HTTP surface."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
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
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.validation import ValidationResult
from openalpha_cn.evidence.service import (
    EvidenceBuildRequest,
    EvidenceBuildResponse,
    build_evidence,
    parse_serialized_evidence,
)
from openalpha_cn.logging_setup import configure_logging
from openalpha_cn.product.research import (
    ResearchReport,
    ResearchReportFactory,
    ResearchScreener,
    ScreeningCriteria,
    ScreeningResult,
    WatchlistEntry,
)
from openalpha_cn.providers.base import utc_now
from openalpha_cn.runtime.batch import BatchProgressEvent, BatchResearchService, BatchResearchTask
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.runtime.provenance import compute_config_digest, resolve_code_commit
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
    requests: tuple[ResearchApiRequest, ...] = Field(min_length=1, max_length=1000)
    max_concurrency: int = Field(default=4, ge=1, le=32)


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
    """Rebuild a strict result while verifying its content-derived identifiers."""
    clean = {**payload}
    claimed_signal_id = clean.get("signal", {}).get("signal_id")
    claimed_decision_id = clean.get("decision", {}).get("decision_id")

    signal = {**clean["signal"]}
    signal.pop("signal_id", None)
    clean["signal"] = signal

    decision = {**clean["decision"]}
    decision.pop("decision_id", None)
    clean["decision"] = decision

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
    return result


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
