"""Local-first Python SDK for OpenAlpha CN's complete research flow."""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path

from openalpha_cn import __version__
from openalpha_cn.agents.base import AgentResult, ResearchAgent
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
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.validation import ValidationResult
from openalpha_cn.evidence.service import build_provider_evidence
from openalpha_cn.panel.catalog import DatasetReadiness
from openalpha_cn.panel_doctor import PanelHealthReport, panel_health_report
from openalpha_cn.panel_gate import DependencyClearance, require_datasets
from openalpha_cn.panel_view import dataset_readiness, panel_request, panel_store
from openalpha_cn.product.research import (
    ResearchReport,
    ResearchReportFactory,
    ResearchScreener,
    ScreeningCriteria,
    ScreeningResult,
    WatchlistEntry,
)
from openalpha_cn.providers.base import ProviderMetadata, utc_now
from openalpha_cn.providers.file import FileProvider
from openalpha_cn.runtime.batch import BatchResearchService, BatchResearchTask
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.runtime.contracts import ResearchRunRequest, ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import MemoryEntry
from openalpha_cn.storage.parquet import read_parquet_records
from openalpha_cn.storage.recovery import RunRecoveryState


class OpenAlphaSDK:
    """Compose storage, evidence, research, and replay behind one Python API."""

    def __init__(
        self,
        *,
        runtime_dir: Path,
        clock: Callable[[], datetime] = utc_now,
        agents: Sequence[ResearchAgent] | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.clock = clock
        self.agents = None if agents is None else tuple(agents)
        storage = build_storage(runtime_dir=runtime_dir, clock=clock)
        self.evidence_store = storage.evidence_store
        self.repository = storage.repository
        self.memory = storage.memory
        self.recovery_store = storage.recovery_store
        self.batch_store = storage.batch_store
        self.portfolio_ledger = storage.portfolio_ledger
        self.watchlist_store = storage.watchlist_store
        self.report_store = storage.report_store
        self.validation_store = storage.validation_store

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
        provider = FileProvider(
            path=path,
            metadata=metadata,
            clock=self.clock,
            parquet_reader=read_parquet_records,
        )
        response = build_provider_evidence(provider=provider, dataset="events", as_of=as_of)
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
            recovery_store=self.recovery_store,
            agents=self.agents,
        )
        return engine.run_cycle(request)

    def list_memory(self, *, subject: str) -> tuple[MemoryEntry, ...]:
        """Return decision-linked research memory that survives restarts."""
        return self.memory.list(subject=subject)

    def get_recovery(self, run_id: str) -> RunRecoveryState | None:
        """Inspect the durable node-level recovery state for one run."""
        return self.recovery_store.get(run_id)

    def run_batch(
        self,
        *,
        batch_id: str,
        requests: Sequence[ResearchRunRequest],
        max_concurrency: int = 4,
    ) -> BatchResearchTask:
        """Submit and synchronously complete one durable bounded batch."""
        service = BatchResearchService(
            store=self.batch_store,
            runner=self.run_research,
            clock=self.clock,
        )
        service.submit(
            batch_id=batch_id,
            requests=requests,
            max_concurrency=max_concurrency,
        )
        return service.run(batch_id)

    def deliberate(
        self,
        *,
        signal: SignalFrame,
        agent_results: tuple[AgentResult, ...],
    ) -> DeliberationOutcome:
        """Run the optional bull/bear and risk committee with ablation output."""
        return DeliberationCommittee().review(signal=signal, results=agent_results)

    def run_event_study(self, request: EventStudyRequest) -> EventStudyReport:
        """Compute event CAR and deterministic significance statistics."""
        return EventStudy().analyze(request)

    def screen(
        self,
        *,
        results: tuple[ResearchRunResult, ...],
        criteria: ScreeningCriteria,
    ) -> ScreeningResult:
        """Filter and rank structured research results."""
        return ResearchScreener().screen(results=results, criteria=criteria)

    def put_watchlist(self, entry: WatchlistEntry) -> None:
        """Create or intentionally update one local watchlist entry."""
        self.watchlist_store.put(entry)

    def list_watchlist(self) -> tuple[WatchlistEntry, ...]:
        """List the local observation pool."""
        return self.watchlist_store.list()

    def create_report(self, result: ResearchRunResult) -> ResearchReport:
        """Generate and persist one evidence-linked report."""
        report = ResearchReportFactory().build(result)
        self.report_store.append(report)
        return report

    def list_reports(self, *, subject: str | None = None) -> tuple[ResearchReport, ...]:
        """List generated reports, optionally by subject."""
        return self.report_store.list(subject=subject)

    def validate_outcome(
        self,
        *,
        research: ResearchRunResult,
        observation: OutcomeObservation,
    ) -> ValidationResult:
        """Validate an observed outcome, persist it, and return the reconciled result.

        The SDK's own outcome-validation entry point (V2-P0B-010): before this, only
        `POST /api/v1/backtests/validate` and the web UI could reach `OutcomeValidator`
        (`backtest/validation.py`) -- `sdk.py` never imported `backtest.validation` at
        all, so a programmatic caller had no way to validate an outcome without going
        through HTTP, contradicting this module's own "complete research flow" docstring
        (audit finding F29). Mirrors `create_report`'s shape: compute, then persist via
        `self.validation_store`, so a result computed through the SDK is durable the same
        way a result computed through REST is.
        """
        result = OutcomeValidator().validate(research=research, observation=observation)
        self.validation_store.append(result)
        return result

    def list_validations_by_decision(self, decision_id: str) -> tuple[ValidationResult, ...]:
        """List validation results for one decision, in append order."""
        return self.validation_store.list_by_decision(decision_id)

    def list_validations_by_signal(self, signal_id: str) -> tuple[ValidationResult, ...]:
        """List validation results for one signal, in append order."""
        return self.validation_store.list_by_signal(signal_id)

    # --- the panel plane (V2-P1-016) ----------------------------------------------------------
    #
    # Three methods paired one-for-one with `GET /api/v1/panel/readiness`, `/health` and
    # `/gate`, and asserted against them in `tests/integration/test_panel_interfaces.py`. Each
    # resolves its parameters through `panel_view.panel_request`, so the two faces cannot come
    # to ask two different questions of one store.
    #
    # They hand back the objects rather than a rendering of them -- that is what an in-process
    # API is for. `PanelHealthReport.findings_with_code`, `DependencyClearance.blocks_for`,
    # `.unverified` and `.cleared_for` all raise for a code or a dataset the request never
    # named, which is a guarantee JSON cannot carry.
    #
    # `exchange` and `with_calendar` have no defaults, matching `DependencyRequest`'s own rule:
    # every field that decides how hard the panel is examined is mandatory, because the most
    # permissive request must not also be the easiest one to build.

    def panel_readiness(
        self,
        *,
        datasets: Sequence[str],
        years: Sequence[int],
        as_of: datetime,
        exchange: str,
        with_calendar: bool,
        index_codes: Sequence[str] = (),
    ) -> tuple[DatasetReadiness, ...]:
        """Each named dataset's own readiness verdict, in request order.

        No session and no cross-dataset check: this is one dataset's catalog records against
        the requirement its own reader puts. `DatasetReadiness.checks_waived` is the field to
        read beside an empty `issues`, because the empty tuple there is the *stronger* claim.
        """
        store = panel_store(self.runtime_dir)
        return dataset_readiness(
            store,
            panel_request(
                store,
                datasets=datasets,
                years=years,
                sessions=(),
                index_codes=index_codes,
                as_of=as_of,
                exchange=exchange,
                with_calendar=with_calendar,
            ),
        )

    def panel_health(
        self,
        *,
        datasets: Sequence[str],
        years: Sequence[int],
        sessions: Sequence[date],
        as_of: datetime,
        exchange: str,
        with_calendar: bool,
        index_codes: Sequence[str] = (),
    ) -> PanelHealthReport:
        """What is wrong with the stored panel at `as_of`, as a structured report.

        Answers "is this panel sick", which is a different question from `panel_clearance`'s
        "may this request read it" -- the two may disagree about one panel and both be right,
        because the gate has a refusal (`unverified_daily_coverage`) that is not a fault of the
        panel at all.

        `sessions` names the sessions the day-level cross-checks run on and is not inferred:
        "check every session" is a whole-corpus scan and "check the last one" is a guess about
        what the caller cares about.
        """
        store = panel_store(self.runtime_dir)
        request = panel_request(
            store,
            datasets=datasets,
            years=years,
            sessions=sessions,
            index_codes=index_codes,
            as_of=as_of,
            exchange=exchange,
            with_calendar=with_calendar,
        )
        return panel_health_report(
            store,
            as_of=request.as_of,
            datasets=request.datasets,
            years=request.years,
            calendar=request.calendar,
            index_codes=request.index_codes,
            cross_section_days=request.sessions,
        )

    def panel_clearance(
        self,
        *,
        datasets: Sequence[str],
        years: Sequence[int],
        sessions: Sequence[date],
        as_of: datetime,
        exchange: str,
        with_calendar: bool,
        index_codes: Sequence[str] = (),
    ) -> DependencyClearance:
        """Whether this request may read the stored panel, and everything the answer rests on.

        The returned `DependencyClearance` is a verdict, not a collection: `bool()`, `len()`
        and iteration all raise on it **even when it cleared**, which is deliberate -- an
        accessor that answered on a healthy panel and raised on a sick one would pass every
        test written against the first and fail only in production. Ask `is_blocked`, read
        `cleared` (which raises when blocked), or name the merged shape `cleared_or_none`.

        `cleared` hands back `ClearedDataset` records rather than bare names, because the width
        of the permission is part of it: the years the year-scoped checks covered, the sessions
        a cross-check actually opened, and the caveats still open outside them.
        """
        store = panel_store(self.runtime_dir)
        return require_datasets(
            store,
            panel_request(
                store,
                datasets=datasets,
                years=years,
                sessions=sessions,
                index_codes=index_codes,
                as_of=as_of,
                exchange=exchange,
                with_calendar=with_calendar,
            ),
        )

    def execute_portfolio_order(
        self,
        *,
        state: PortfolioState,
        order: PortfolioOrder,
        market: MarketBar,
        limits: PortfolioLimits | None = None,
    ) -> PortfolioTransition:
        """Apply one order through the deterministic A-share portfolio core."""
        transition = PortfolioSimulator(limits=limits).execute_order(
            state=state,
            order=order,
            market=market,
        )
        self.portfolio_ledger.append(transition)
        return transition

    def list_portfolio_transitions(
        self,
        *,
        subject: str | None = None,
    ) -> tuple[PortfolioTransition, ...]:
        """List immutable portfolio order/execution records."""
        return self.portfolio_ledger.list(subject=subject)

    def run_portfolio_backtest(
        self,
        *,
        initial: PortfolioState,
        steps: tuple[PortfolioBacktestStep, ...],
        limits: PortfolioLimits | None = None,
    ) -> PortfolioBacktestReport:
        """Run and persist a multi-day portfolio backtest."""
        return PortfolioBacktestRunner(
            limits=limits,
            ledger=self.portfolio_ledger,
        ).run(initial=initial, steps=steps)

    def replay(
        self,
        *,
        corpus: ReplayCorpus,
        code_commit: str,
        config_digest: str,
        random_seed: int,
    ) -> ReplayReport:
        """Run a frozen corpus and return deterministic validation metrics.

        `ReplayRunner.run()` keeps its own migrated `sdk-replay.sqlite3` for run/recovery
        state (see its docstring for why), but persists validation results into
        `self.validation_store` -- the same store `validate_outcome()` above uses -- so a
        result produced by replay is retrievable through `list_validations_by_decision`/
        `list_validations_by_signal` exactly like one produced by `validate_outcome()`
        (P0.B acceptance review, Finding 1).
        """
        return ReplayRunner(
            code_commit=code_commit,
            config_digest=config_digest,
            random_seed=random_seed,
        ).run(
            corpus=corpus,
            state_path=self.runtime_dir / "sdk-replay.sqlite3",
            validation_store=self.validation_store,
            clock=self.clock,
        )
