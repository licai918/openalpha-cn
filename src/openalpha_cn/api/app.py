"""FastAPI application for OpenAlpha CN's versioned public HTTP surface."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openalpha_cn import __version__
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioOrder,
    PortfolioSimulator,
    PortfolioState,
    PortfolioTransition,
)
from openalpha_cn.backtest.replay import ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator
from openalpha_cn.domain.validation import ValidationResult
from openalpha_cn.evidence.service import (
    EvidenceBuildRequest,
    EvidenceBuildResponse,
    build_evidence,
    parse_serialized_evidence,
)
from openalpha_cn.runtime.engine import ResearchEngine, ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.parquet import ParquetEvidenceStore
from openalpha_cn.storage.recovery import RunRecoveryState, SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository


class ReplayApiRequest(BaseModel):
    """Inputs required to execute a frozen corpus through the replay API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus: ReplayCorpus
    code_commit: str = Field(min_length=7, max_length=64)
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    random_seed: int


class ResearchApiRequest(ResearchRunRequest):
    """Research request that safely accepts serialized evidence output."""

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
) -> FastAPI:
    """Create an isolated application instance for serving and tests."""
    root = runtime_dir or Path(os.getenv("OPENALPHA_RUNTIME_DIR", "./runtime"))
    request_limit = (
        int(os.getenv("OPENALPHA_MAX_REQUEST_BYTES", str(8 * 1024 * 1024)))
        if max_request_bytes is None
        else max_request_bytes
    )
    if request_limit < 1:
        raise ValueError("max_request_bytes must be positive")
    root.mkdir(parents=True, exist_ok=True)
    evidence_store = ParquetEvidenceStore(root / "evidence")
    run_repository = SQLiteRunRepository(root / "state.sqlite3")
    memory = SQLiteResearchMemory(root / "state.sqlite3")
    recovery_store = SQLiteRecoveryStore(root / "state.sqlite3")
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
            clock=lambda: datetime.now(UTC),
        )
        return engine.run_cycle(request)

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
        """Execute a supplied frozen corpus through the shared research core."""
        runner = ReplayRunner(
            code_commit=request.code_commit,
            config_digest=request.config_digest,
            random_seed=request.random_seed,
        )
        return runner.run(corpus=request.corpus, state_path=root / "api-replay.sqlite3")

    @application.post("/api/v1/portfolio/execute")
    def portfolio_execute(request: PortfolioApiRequest) -> PortfolioTransition:
        """Apply A-share execution, T+1, costs, and exposure limits."""
        return PortfolioSimulator(limits=request.limits).execute_order(
            state=request.state,
            order=request.order,
            market=request.market,
        )

    @application.post("/api/v1/backtests/validate")
    def validate_outcome(request: OutcomeApiRequest) -> ValidationResult:
        """Validate an observed outcome and reconcile rule/factor/agent attribution."""
        try:
            research = _parse_research_result(request.research)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="Research result failed integrity validation.",
            ) from error
        return OutcomeValidator().validate(
            research=research,
            observation=request.observation,
        )

    configured_web_dir = web_dir
    if configured_web_dir is None and os.getenv("OPENALPHA_WEB_DIR"):
        configured_web_dir = Path(os.environ["OPENALPHA_WEB_DIR"])
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
