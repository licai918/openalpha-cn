"""FastAPI application for OpenAlpha CN's versioned public HTTP surface."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

from openalpha_cn import __version__
from openalpha_cn.backtest.replay import ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.evidence.service import (
    EvidenceBuildRequest,
    EvidenceBuildResponse,
    build_evidence,
    parse_serialized_evidence,
)
from openalpha_cn.runtime.engine import ResearchEngine, ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.parquet import ParquetEvidenceStore
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


def create_app(*, runtime_dir: Path | None = None) -> FastAPI:
    """Create an isolated application instance for serving and tests."""
    root = runtime_dir or Path(os.getenv("OPENALPHA_RUNTIME_DIR", "./runtime"))
    root.mkdir(parents=True, exist_ok=True)
    evidence_store = ParquetEvidenceStore(root / "evidence")
    run_repository = SQLiteRunRepository(root / "state.sqlite3")
    memory = InMemoryResearchMemory()
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

    @application.post("/api/v1/backtests/replay")
    def replay(request: ReplayApiRequest) -> ReplayReport:
        """Execute a supplied frozen corpus through the shared research core."""
        runner = ReplayRunner(
            code_commit=request.code_commit,
            config_digest=request.config_digest,
            random_seed=request.random_seed,
        )
        return runner.run(corpus=request.corpus, state_path=root / "api-replay.sqlite3")

    return application


app = create_app()
