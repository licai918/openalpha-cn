"""Local-first Python SDK for OpenAlpha CN's complete research flow."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from openalpha_cn import __version__
from openalpha_cn.backtest.replay import ReplayCorpus, ReplayReport, ReplayRunner
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.evidence.service import build_file_evidence
from openalpha_cn.providers.base import ProviderMetadata, utc_now
from openalpha_cn.runtime.engine import ResearchEngine, ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.parquet import ParquetEvidenceStore
from openalpha_cn.storage.recovery import RunRecoveryState, SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository


class OpenAlphaSDK:
    """Compose storage, evidence, research, and replay behind one Python API."""

    def __init__(
        self,
        *,
        runtime_dir: Path,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.evidence_store = ParquetEvidenceStore(runtime_dir / "evidence")
        self.repository = SQLiteRunRepository(runtime_dir / "state.sqlite3")
        self.memory = SQLiteResearchMemory(runtime_dir / "state.sqlite3")
        self.recovery_store = SQLiteRecoveryStore(runtime_dir / "state.sqlite3")

    def health(self) -> dict[str, str]:
        """Return SDK and package readiness."""
        return {"status": "ok", "version": __version__}

    def build_file_evidence(
        self,
        *,
        path: Path,
        as_of: datetime,
        metadata: ProviderMetadata,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Import a user-owned file, normalize evidence, and persist it."""
        response = build_file_evidence(
            path=path,
            as_of=as_of,
            metadata=metadata,
            clock=self.clock,
        )
        if response.items:
            self.evidence_store.append(response.items)
        return response.items

    def query_evidence(
        self,
        *,
        as_of: datetime,
        subject: str | None = None,
        kind: str | None = None,
    ) -> tuple[EvidenceSnapshot, ...]:
        """Query point-in-time-visible local evidence."""
        return self.evidence_store.query(as_of=as_of, subject=subject, kind=kind)

    def run_research(self, request: ResearchRunRequest) -> ResearchRunResult:
        """Execute and persist one shared-path research run."""
        engine = ResearchEngine(
            repository=self.repository,
            memory=self.memory,
            clock=self.clock,
        )
        return engine.run_cycle(request)

    def list_memory(self, *, subject: str) -> tuple[MemoryEntry, ...]:
        """Return decision-linked research memory that survives restarts."""
        return self.memory.list(subject=subject)

    def get_recovery(self, run_id: str) -> RunRecoveryState | None:
        """Inspect the durable node-level recovery state for one run."""
        return self.recovery_store.get(run_id)

    def replay(
        self,
        *,
        corpus: ReplayCorpus,
        code_commit: str,
        config_digest: str,
        random_seed: int,
    ) -> ReplayReport:
        """Run a frozen corpus and return deterministic validation metrics."""
        return ReplayRunner(
            code_commit=code_commit,
            config_digest=config_digest,
            random_seed=random_seed,
        ).run(corpus=corpus, state_path=self.runtime_dir / "sdk-replay.sqlite3")
