"""Local-first Python SDK for OpenAlpha CN's complete research flow."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openalpha_cn import __version__
from openalpha_cn.agents.base import AgentResult, ResearchAgent
from openalpha_cn.agents.committee import DeliberationCommittee, DeliberationOutcome
from openalpha_cn.backtest.event_study import EventStudy, EventStudyReport, EventStudyRequest
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.factor_experiment import FactorExperimentRecord, open_experiment
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
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.factor import FactorNote
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.validation import ValidationResult
from openalpha_cn.evidence.service import build_provider_evidence
from openalpha_cn.factor_view import (
    ExperimentWrite,
    FactorBuildReport,
    build_factor_panels,
    build_view,
    experiment_view,
    factor_build_request,
    factor_catalog,
    factor_entry,
    factor_request,
    run_factor_experiment,
)
from openalpha_cn.model_view import (
    DailyRunResult,
    ModelEvaluation,
    daily_request,
    daily_view,
    evaluation_view,
    feature_columns,
    held_prediction,
    model_evaluation_request,
)
from openalpha_cn.model_view import evaluate_model as evaluate_model_run
from openalpha_cn.model_view import run_daily as run_daily_model_run
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
from openalpha_cn.shortlist_view import (
    ShortlistEvidence,
    ShortlistRunResult,
    held_shortlist,
    shortlist_components,
    shortlist_request,
    shortlist_view,
)
from openalpha_cn.shortlist_view import run_shortlist as run_shortlist_run
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
        self.experiment_store = storage.experiment_store
        self.shortlist_store = storage.shortlist_store
        self.prediction_store = storage.prediction_store

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

    # --- the factor plane (V2-P3-015) -----------------------------------------------------------
    #
    # Three methods paired one-for-one with `POST /api/v1/factors/run`,
    # `GET /api/v1/factors/experiments` and `GET /api/v1/factors/experiments/{id}`, and asserted
    # against them in `tests/integration/test_factor_interfaces.py`.
    #
    # **Not one of the nineteen parameters below has a default, and that is the whole design of
    # this signature.** Task 39's measured failure was an SDK that hardcoded `exchange` while the
    # equivalence test fed the same literal to both faces: 1,815 tests stayed green and what was
    # proved was that two paths agreed, not that either of them carried the caller's value to the
    # judgement. Every parameter here is forwarded verbatim to `factor_view.factor_request`, which
    # is the same call `POST /api/v1/factors/run` and `openalpha factor run` make, and
    # `tests/integration/test_factor_interfaces.py::
    # test_every_declared_run_parameter_reaches_the_answer` varies each one alone and requires
    # the answer to move.

    def run_factor_experiment(
        self,
        *,
        factor: str,
        transform: str,
        neutralization: str,
        start: date,
        end: date,
        as_of: datetime,
        exchange: str,
        horizon: str,
        ic_method: ICMethod,
        min_securities: int,
        min_as_ofs: int,
        group_count: int,
        min_securities_per_group: int,
        position_capital: Decimal,
        min_periods: int,
        participation_cap: Decimal,
        min_rebalances: int,
        redundancy_threshold: float,
        retention_floor: float,
        code_commit: str,
        note: FactorNote | None = None,
    ) -> tuple[FactorExperimentRecord, ExperimentWrite]:
        """Run one factor experiment over a closed range of prediction days, and seal it.

        Hands back the `FactorExperimentRecord` rather than a rendering of it -- that is what an
        in-process API is for. `FactorExperimentArtifact.attribution(...)` raises for a cell the
        declared grid does not contain and `tier_report(...)` raises for a tier the artifact does
        not carry, which is a guarantee JSON cannot make; the HTTP face gets `experiment_view`'s
        envelope instead, and `tests/integration/test_factor_interfaces.py::
        test_the_three_faces_seal_one_experiment_from_one_request` asserts the three are one
        document.

        The second element is what the document store did -- `created` or `unchanged`. A second
        identical run is a no-op rather than a duplicate, and a second *different* answer under
        one `experiment_id` is refused; both are `refuse_a_restated_experiment`'s rule enforced at
        the boundary that actually holds artifacts.
        """
        record, write = run_factor_experiment(
            panel_store(self.runtime_dir),
            factor_request(
                factor=factor,
                transform=transform,
                neutralization=neutralization,
                start=start,
                end=end,
                as_of=as_of,
                exchange=exchange,
                horizon=horizon,
                ic_method=ic_method,
                min_securities=min_securities,
                min_as_ofs=min_as_ofs,
                group_count=group_count,
                min_securities_per_group=min_securities_per_group,
                position_capital=position_capital,
                min_periods=min_periods,
                participation_cap=participation_cap,
                min_rebalances=min_rebalances,
                redundancy_threshold=redundancy_threshold,
                retention_floor=retention_floor,
                code_commit=code_commit,
            ),
            built_at=self.clock(),
            experiments=self.experiment_store,
            note=note,
        )
        return record, write

    def get_factor_experiment(self, experiment_id: str) -> FactorExperimentRecord | None:
        """Reopen one stored experiment, or `None` when nothing is held under that key.

        Through `open_experiment`, so a document whose content no longer hashes to its own seal
        does not come back as a record that merely differs -- it raises. That is the boundary
        `V2-P3-014` built the seal for, and it is why this method returns a record rather than the
        payload: a caller handed bytes would have to remember to check.
        """
        payload = self.experiment_store.get(experiment_id)
        return None if payload is None else open_experiment(payload)

    def list_factor_experiments(self) -> tuple[str, ...]:
        """Every held `experiment_id`, ascending."""
        return self.experiment_store.list_ids()

    def factor_experiment_view(
        self, record: FactorExperimentRecord, *, write: ExperimentWrite
    ) -> dict[str, object]:
        """The record as the HTTP face renders it, for a caller that wants the same bytes."""
        return experiment_view(record, write=write)

    def factor_catalog(self) -> dict[str, object]:
        """Every factor, transform and neutralisation this build declares, with their prose.

        `openalpha factor list --json` and `GET /api/v1/factors` are the same call, so the three
        faces cannot come to describe three builds. Takes no `runtime_dir` and reads no store: a
        declaration is a property of the build rather than of an installation.

        The **whole** note travels on every entry -- 705 to 4,830 characters each -- because it is
        what a caller came for. `return_vol_60`'s says in full that it occupies `V2-P3-013`'s
        residual-volatility slot, is deliberately not named for a residual, and that neither
        residual is computable in this build; nineteen disclosures of that kind existed in the
        source and reached no face until `V2-P3-019`.
        """
        return factor_catalog()

    def describe_factor(
        self,
        *,
        factor: str | None = None,
        transform: str | None = None,
        neutralization: str | None = None,
    ) -> dict[str, object]:
        """One declaration and its note, named by exactly one of the three handles.

        The twin of `openalpha factor describe` and of `GET /api/v1/factors?factor=...`. Raises
        `FactorRequestError` for none, for more than one, and for a handle no registry declares --
        the refusal names the declared handles rather than their content addresses.
        """
        return factor_entry(factor=factor, transform=transform, neutralization=neutralization)

    def build_factor_panels(
        self,
        *,
        factor: str,
        tier: str,
        as_ofs: Sequence[datetime],
        years: Sequence[int],
        exchange: str,
        max_staleness_days: int | None,
        waive_max_staleness: bool,
        transform: str = "",
        neutralization: str = "",
        subjects: Sequence[str] = (),
        supersedes_raw: Sequence[str] = (),
        supersedes_processed: Sequence[str] = (),
        supersedes_neutralized: Sequence[str] = (),
        code_commit: str,
    ) -> FactorBuildReport:
        """Compute this factor's stored tiers at the named instants and write them into the panel.

        The in-process twin of `openalpha factor build`, resolving through the same
        `factor_view.factor_build_request` and running through the same
        `factor_view.build_factor_panels`, so the two faces cannot come to build two panels from
        one declaration. `tests/integration/test_factor_build.py::
        test_the_two_build_faces_store_one_panel_from_one_request` drives both against one store
        and requires byte-identical `manifest_id`s.

        **There is deliberately no HTTP twin.** `openalpha panel build` has none either, and the
        reason is the same one, sharpened: this writes panel partitions, a partition is replaced
        whole, and the service ships with no authentication of its own ("local-first and has no
        public multi-tenant authentication"). A `POST` that replaced a stored partition would hand
        that to whoever could reach the port.
        `tests/integration/test_factor_build.py::test_no_http_route_builds_a_factor_partition`
        pins the absence, so it stays a decision rather than an oversight.

        Every parameter has the meaning `openalpha factor build --help` gives it; the four with
        defaults are the four the command also defaults, and `max_staleness_days` /
        `waive_max_staleness` are exclusive and one is required -- see `factor_build_request`.
        """
        return build_factor_panels(
            panel_store(self.runtime_dir),
            factor_build_request(
                factor=factor,
                tier=tier,
                transform=transform,
                neutralization=neutralization,
                as_ofs=as_ofs,
                years=years,
                exchange=exchange,
                max_staleness_days=max_staleness_days,
                waive_max_staleness=waive_max_staleness,
                subjects=subjects,
                supersedes_raw=supersedes_raw,
                supersedes_processed=supersedes_processed,
                supersedes_neutralized=supersedes_neutralized,
                code_commit=code_commit,
            ),
            built_at=self.clock(),
        )

    def factor_build_view(self, report: FactorBuildReport) -> dict[str, object]:
        """One build report as `openalpha factor build --json` renders it."""
        return build_view(report)

    def run_shortlist(
        self,
        *,
        components: Sequence[Mapping[str, object]],
        tier: str,
        shortlist_size: int,
        position_capital: Decimal | str,
        as_of: datetime,
        years: Sequence[int],
        exchange: str,
        horizon: str,
        minimum_tradable_ratio: float,
        minimum_researched_ratio: float,
        maximum_ranking_age_days: int,
        code_commit: str,
        config_digest: str,
        transform: str | None = None,
        neutralization: str | None = None,
        evidence: Mapping[str, ShortlistEvidence] | None = None,
    ) -> ShortlistRunResult:
        """Cut a shortlist out of the stored panel, join the evidence plane, and gate it.

        `V2-P4-033`'s in-process face, resolving through the same `shortlist_view.
        shortlist_request` and running through the same `shortlist_view.run_shortlist` as
        `openalpha shortlist run` and `POST /api/v1/shortlists/run`, so the three cannot come to
        cut three lists from one declaration.

        **Hands back the `ShortlistRunResult` rather than a rendering of it**, which is what an
        in-process API is for and is the strongest form the blocked/empty distinction takes
        anywhere in this repository: `result.clearance` is a `ShortlistClearance`, and
        `bool(clearance)`, `len(clearance)` and iterating it all **raise** -- including when the
        list cleared. A caller cannot write `if not clearance:` and quietly treat a refusal as an
        empty list, which is a guarantee JSON cannot make. `shortlist_view(result)` is the HTTP
        face's bytes for a caller that wants those instead.

        `evidence` is the evidence plane's answers about the shortlisted names, keyed by subject,
        and is empty by default -- see `the_evidence_plane_is_supplied_rather_than_run_by_this
        _module` for why this face does not run `run_cycle` itself. With none supplied,
        `researched_ratio` is `0.0` and any `minimum_researched_ratio` above zero refuses the
        list, which is the ordinary first answer: the shortlist says which names are worth
        spending an evidence run on, and the gate refuses to publish them as conclusions until
        those runs have happened.
        """
        return run_shortlist_run(
            panel_store(self.runtime_dir),
            shortlist_request(
                components=shortlist_components(components),
                tier=tier,
                shortlist_size=shortlist_size,
                position_capital=Decimal(str(position_capital)),
                as_of=as_of,
                years=years,
                exchange=exchange,
                horizon=horizon,
                minimum_tradable_ratio=minimum_tradable_ratio,
                minimum_researched_ratio=minimum_researched_ratio,
                maximum_ranking_age_days=maximum_ranking_age_days,
                code_commit=code_commit,
                config_digest=config_digest,
                transform=transform,
                neutralization=neutralization,
                evidence=evidence,
            ),
            built_at=self.clock(),
            runs=self.repository,
            shortlists=self.shortlist_store,
        )

    def shortlist_view(self, result: ShortlistRunResult) -> dict[str, object]:
        """One shortlist run as `openalpha shortlist run --json` and HTTP render it."""
        return shortlist_view(result)

    def held_shortlist(self, shortlist_id: str) -> dict[str, object]:
        """One stored shortlist answer, by the `shortlist_id` its own body carried.

        `V2-P4-062`'s in-process read, through the same `shortlist_view.held_shortlist` as
        `openalpha shortlist get` and `GET /api/v1/shortlists/{shortlist_id}`. Raises
        `ShortlistNotHeldError` when nothing is held rather than answering `None`, which is the
        opposite of `get_factor_experiment` one plane over and is that method's own distinction
        applied: an experiment is looked up by a key a caller composed from a declaration and
        may legitimately not exist yet, while a `shortlist_id` is an address that was **printed
        on an answer**, so nothing held under one is a fact about this runtime directory and not
        about the question.
        """
        return held_shortlist(self.shortlist_store, shortlist_id)

    def list_shortlists(self) -> tuple[str, ...]:
        """Every held `shortlist_id`, ascending."""
        return self.shortlist_store.list_ids()

    def evaluate_model(
        self,
        *,
        features: Sequence[Mapping[str, object]],
        name: str,
        family: str,
        horizon: str,
        seed: int,
        start: date,
        end: date,
        as_of: datetime,
        years: Sequence[int],
        exchange: str,
        folds: int,
        test_days_per_fold: int,
        embargo_sessions: int,
        minimum_scored_ratio: float,
        code_commit: str,
        config_digest: str,
        feature_version: str | None = None,
        hyperparameters: Sequence[tuple[str, bool | int | float | str]] = (),
    ) -> ModelEvaluation:
        """Fit one declaration once per walk-forward fold and report what it ordered.

        `V2-P4-021`'s in-process face, resolving through the same
        `model_view.model_evaluation_request` and running through the same
        `model_view.evaluate_model` as `openalpha model evaluate` and
        `POST /api/v1/models/evaluate`, so the three cannot come to fit three models from one
        declaration.

        **Hands back the `ModelEvaluation` rather than a rendering of it**, which is what an
        in-process API is for: `result.folds` is a tuple of `FoldEvaluation`, and each one refuses
        at construction to carry a `mean_rank_ic` its own coverage says it does not have. A caller
        reading `fold.mean_rank_ic is None` beside `fold.coverage` can tell "not measured" from
        "measured at nothing", which is the distinction JSON preserves only because
        `evaluation_view` is careful to. `self.evaluation_view(result)` is the HTTP face's bytes
        for a caller that wants those instead.

        It stores nothing. See
        `an_evaluation_registers_nothing_because_every_record_it_could_write_would_be_unwitnessed`.
        """
        return evaluate_model_run(
            panel_store(self.runtime_dir),
            model_evaluation_request(
                columns=feature_columns(features),
                name=name,
                family=family,
                horizon=horizon,
                seed=seed,
                start=start,
                end=end,
                as_of=as_of,
                years=years,
                exchange=exchange,
                folds=folds,
                test_days_per_fold=test_days_per_fold,
                embargo_sessions=embargo_sessions,
                minimum_scored_ratio=minimum_scored_ratio,
                code_commit=code_commit,
                config_digest=config_digest,
                feature_version=feature_version,
                hyperparameters=hyperparameters,
            ),
        )

    def evaluation_view(self, result: ModelEvaluation) -> dict[str, object]:
        """One evaluation as `openalpha model evaluate --json` and HTTP render it."""
        return evaluation_view(result)

    def run_daily_model(
        self,
        *,
        features: Sequence[Mapping[str, object]],
        name: str,
        family: str,
        horizon: str,
        seed: int,
        start: date,
        end: date,
        predict_at: datetime,
        as_of: datetime,
        years: Sequence[int],
        exchange: str,
        minimum_scored_ratio: float,
        code_commit: str,
        config_digest: str,
        feature_version: str | None = None,
        hyperparameters: Sequence[tuple[str, bool | int | float | str]] = (),
    ) -> DailyRunResult:
        """Fit on what has already closed, score `predict_at`, and register the answer.

        Story S32's in-process face. `predicted_at` and `started_at` both come from `self.clock`
        and neither is a parameter, which is deliberate and is the same decision the other two
        faces take: `FilePredictionStore` is constructed by `build_storage` with this same clock,
        so the instant the batch claims and the instant the store witnessed are readings of one
        clock this method's caller does not reach. A caller who wants to drive the three standings
        constructs the SDK with the clock it wants, which is what a test does.

        **A refused run still registered its prediction**, and `result.record` carries it either
        way -- Story S32's requirement is unconditional and the floor is about whether the answer
        may be acted on.
        """
        now = self.clock()
        return run_daily_model_run(
            panel_store(self.runtime_dir),
            daily_request(
                columns=feature_columns(features),
                name=name,
                family=family,
                horizon=horizon,
                seed=seed,
                start=start,
                end=end,
                predict_at=predict_at,
                as_of=as_of,
                years=years,
                exchange=exchange,
                minimum_scored_ratio=minimum_scored_ratio,
                code_commit=code_commit,
                config_digest=config_digest,
                feature_version=feature_version,
                hyperparameters=hyperparameters,
            ),
            predictions=self.prediction_store,
            runs=self.repository,
            predicted_at=now,
            started_at=now,
        )

    def daily_view(self, result: DailyRunResult) -> dict[str, object]:
        """One daily run as `openalpha model daily-run --json` and HTTP render it."""
        return daily_view(result)

    def held_prediction(self, record_id: str) -> PredictionRecord:
        """One registered prediction, by the `record_id` its own run reported.

        Hands back the `PredictionRecord` rather than a rendering, so a caller reads `standing`
        off a `computed_field` that is re-derived from the two instants every time -- a provenance
        a producer stamps is a provenance a producer chooses, and this is the form in which that
        stays visible. `model_view.prediction_view(record)` is the rendering, and it is the one
        that carries what the standing does *not* prove.

        Raises `ModelNotHeldError` when nothing is held rather than answering `None`,
        `held_shortlist`'s distinction: a `record_id` is an address that was printed on an answer.
        """
        return held_prediction(self.prediction_store, record_id)

    def list_predictions(self) -> tuple[str, ...]:
        """Every registered `record_id`, ascending."""
        return self.prediction_store.list_ids()

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
