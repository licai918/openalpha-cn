"""Single composition root wiring all storage components (V2-P0B-002).

`sdk.py` and `api/app.py` used to assemble the same eight SQLite/Parquet stores twice,
by hand, at the same `runtime_dir`-relative paths -- and drifted: `api/app.py` recovered
interrupted batches with a hardcoded `datetime.now(UTC)` clock while `sdk.py` used its
injected `self.clock` (see `sdk.py:74` / `api/app.py:266` before this change). `build_storage`
is now the only place either module constructs a store; both call it and hold the result.

v2 adds five more storage layers (panel, factor, model, ranking, portfolio). Without a
composition root, each one would need wiring twice, by hand, forever.

`V2-P4-021` wires the twelfth, `FilePredictionStore`, which `V2-P4-017` shipped and
deliberately left out: nothing could fill it until a face above the model and storage planes
existed, and `model daily-run` is that face. See `StorageContainer.prediction_store`.

Field types mirror the storage-Protocol layer Task 9 (V2-P0B-003) built: six of twelve
fields are typed against the narrowest Protocol their consumers need (`ResearchMemory`,
`RecoveryStore`, `EvidenceStore`, `WatchlistStore`, `ReportStore`,
`ValidationStore`) -- because `sdk.py`/`api/app.py` only ever call the methods those
Protocols declare on these six, routing them through this container does not widen
what a consumer can do. `repository` was a seventh until `V2-P4-049` and is now concrete
for `batch_store`'s reason; see its own field docstring.
`ValidationStore` (`backtest/validation.py`, V2-P0B-010) follows
the same narrowing as `WatchlistStore`/`ReportStore`: `sdk.py`/`api/app.py` call
`append`/`list_by_decision`/`list_by_signal` on it directly (there is no engine-layer
consumer the way `RunRepository`/`RecoveryStore` have `ResearchEngine`), so that Protocol
declares exactly those three and nothing else `SQLiteValidationStore` happens to expose.
`batch_store` and `portfolio_ledger` stay concrete (`SQLiteBatchTaskStore` /
`SQLitePortfolioLedger`): `api/app.py`'s `batch_list`/`batch_events` routes call
`.list()`/`.list_events()`, and both `sdk.py`'s `list_portfolio_transitions` and
`api/app.py`'s `portfolio_ledger_query` call `.list()` on the ledger -- none of which are
in `BatchTaskStore`/`PortfolioLedger`, by design (see those Protocols' docstrings in
`runtime/batch.py` and `backtest/multi_day.py`: they declare exactly the methods
`BatchResearchService`/`PortfolioBacktestRunner` call, not `sdk.py`/`api/app.py`'s wider
direct usage). Widening either Protocol to cover `list`/`list_events`/`recover_interrupted`
would hand every *service-layer* consumer (which never needs them) access to methods it
has no business calling -- the same decorative-abstraction problem Task 9 just fixed in
the other direction. Keeping these two fields concrete, honestly, is the documented
alternative Task 9's reviewer flagged; nothing here uses `cast` or `# type: ignore`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openalpha_cn.backtest.validation import ValidationStore
from openalpha_cn.evidence.service import EvidenceStore
from openalpha_cn.product.research import ReportStore, WatchlistStore
from openalpha_cn.runtime.memory import ResearchMemory
from openalpha_cn.runtime.recovery import RecoveryStore
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.factor_experiments import FileExperimentStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import MigrationRunResult, run_migrations
from openalpha_cn.storage.parquet import ParquetEvidenceStore
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.storage.product import SQLiteReportStore, SQLiteWatchlistStore
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.shortlists import FileShortlistStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository
from openalpha_cn.storage.validation import SQLiteValidationStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageContainer:
    """All twelve storage components assembled for one shared `runtime_dir`.

    `migration_result` is the outcome of the `run_migrations()` call this function makes
    before constructing any store below -- exposed so a caller that needs to report on
    it honestly (the `openalpha migrate run` CLI command, in particular: see
    `cli.py::migrate_run`) doesn't have to run migrations a second time just to get the
    `from_version`/`to_version`/`applied`/`backup_path` this construction already computed.
    """

    evidence_store: EvidenceStore
    repository: SQLiteRunRepository
    """The `runs` and `decisions` store, concrete rather than `RunRepository`-typed.

    It joined `batch_store` and `portfolio_ledger` for their reason and by their route
    (`V2-P4-049`): `shortlist_view.stored_run_manifest_ids` resolves a supplied
    `run_manifest_id` against what this deployment holds -- and, since `V2-P4-075`, against
    which of those runs *finished* -- and the only way to answer either is
    `list_runs()`, which `RunRepository` does not declare and must not -- that Protocol names
    exactly the four methods `ResearchEngine` calls, and widening it would hand every
    service-layer consumer a listing it has no business asking for. `ResearchEngine` still takes
    this field as a `RunRepository`, because a concrete class satisfies its own Protocol; what
    changed is only what the two faces above may call on it.
    """
    memory: ResearchMemory
    recovery_store: RecoveryStore
    batch_store: SQLiteBatchTaskStore
    portfolio_ledger: SQLitePortfolioLedger
    watchlist_store: WatchlistStore
    report_store: ReportStore
    validation_store: ValidationStore
    experiment_store: FileExperimentStore
    """`V2-P3-015`'s sealed factor experiment documents, under `runtime_dir / "experiments"`.

    Concrete rather than Protocol-typed, and for `batch_store`'s reason rather than by omission:
    the Protocol its consumer declares (`factor_view.ExperimentDocumentStore`) lives *above*
    `openalpha_cn.storage`, so typing this field against it would give `openalpha_cn.runtime` an
    import edge into `openalpha_cn.factor_view` -- and through it into `openalpha_cn.backtest`'s
    five factor leaves -- for a field whose only job here is to be handed to a face that already
    imports both. The structural match is what makes the injection work and
    `tests/unit/test_factor_view_layering.py` is what pins that it still holds.
    """
    prediction_store: FilePredictionStore
    """`V2-P4-017`'s registered predictions, under `runtime_dir / "predictions"`.

    **The twelfth store, and it was deliberately not wired until something could fill it.**
    `V2-P4-017` shipped `FilePredictionStore` and left it out of this container by name: two
    `lint-imports` contracts stand between a `PredictionBatch` producer and this package, one per
    direction -- `backtest-studies-touch-no-store` bars the outbound edge and
    `storage-no-upward-deps` the inbound one -- so nothing could hand it a batch until a face
    above both planes existed, and *"a twelfth store nothing can fill is a field, not a wiring"*.
    `V2-P4-021`'s `model daily-run` is that face.

    Concrete rather than Protocol-typed, and for the **opposite** reason to
    `experiment_store`'s. That field is concrete because its consumer's Protocol lives above
    `openalpha_cn.storage`; this one is concrete because it is the *only* store in this container
    that is not opaque to its own documents -- it deserializes a `PredictionRecord` so that `get`
    can re-derive the address and refuse a document edited on disk, which is `V2-P4-073`'s
    read-side lesson. `model_view.ModelPredictionStore` is the narrow Protocol its consumer
    declares, and this class satisfies it structurally.

    **It is the one store here that takes the `clock`.** Every other store either ignores time or
    is handed an instant per call; `FilePredictionStore` reads a clock the caller does not own,
    because that is the entire mechanism behind `PredictionRecord.standing` -- a caller who
    backdates `predicted_at` reaches `unwitnessed` and cannot reach `forward`. Passing
    `build_storage`'s own `clock` here is what makes a test's fixed clock and a deployment's real
    one the same seam.
    """
    shortlist_store: FileShortlistStore
    """`V2-P4-062`'s stored shortlist answers, under `runtime_dir / "shortlists"`.

    Concrete for `experiment_store`'s reason exactly: the Protocol its consumer declares
    (`shortlist_view.ShortlistDocumentStore`) lives *above* `openalpha_cn.storage`, so typing this
    field against it would give `openalpha_cn.runtime` an import edge into
    `openalpha_cn.shortlist_view` -- and through it into `openalpha_cn.backtest`'s funnel, ranking
    and gate leaves -- for a field whose only job here is to be handed to a face that already
    imports both.
    """
    migration_result: MigrationRunResult


def build_storage(*, runtime_dir: Path, clock: Callable[[], datetime]) -> StorageContainer:
    """Run pending schema migrations, then assemble every storage component once.

    All twelve stores share one `runtime_dir`: eight at its root-level `state.sqlite3`
    (matching the pre-existing per-store convention), plus the Parquet evidence store
    under `runtime_dir / "evidence"`, the sealed experiment documents under
    `runtime_dir / "experiments"`, `V2-P4-062`'s shortlist answers under
    `runtime_dir / "shortlists"` and `V2-P4-017`'s registered predictions under
    `runtime_dir / "predictions"`. Interrupted-batch recovery runs here, using the
    caller-supplied `clock`, instead of being duplicated (and, in `api/app.py`'s case,
    hardcoded) at each call site.

    `run_migrations` runs first, before any store is constructed (V2-P0B-004): this is
    the one and only mount point for `state.sqlite3`'s migration engine, so every caller
    (`sdk.py`, `api/app.py`, and the `openalpha migrate` CLI commands going through a
    full SDK) gets migrations applied automatically, using the same caller-supplied
    `clock` `recover_interrupted` already uses below. `validation_store` is constructed
    after this call, the same as every other `state.sqlite3` store, but its table's
    existence does not depend on that ordering the way `_demo_add_runs_archived_at` (an
    `ALTER TABLE` on `runs`) does: `create_validation_results` (V2-P0B-010) is ordered
    *before* the demo migration precisely so it always applies within this same first
    `run_migrations()` call, on a fresh install, before any store below is constructed --
    see that migration's docstring in `storage/migrations.py` for why this ordering is
    load-bearing, not incidental.
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    migration_result = run_migrations(runtime_dir / "state.sqlite3", clock=clock)
    evidence_store: EvidenceStore = ParquetEvidenceStore(runtime_dir / "evidence")
    repository = SQLiteRunRepository(runtime_dir / "state.sqlite3")
    memory: ResearchMemory = SQLiteResearchMemory(runtime_dir / "state.sqlite3")
    recovery_store: RecoveryStore = SQLiteRecoveryStore(runtime_dir / "state.sqlite3")
    batch_store = SQLiteBatchTaskStore(runtime_dir / "state.sqlite3")
    portfolio_ledger = SQLitePortfolioLedger(runtime_dir / "state.sqlite3")
    watchlist_store: WatchlistStore = SQLiteWatchlistStore(runtime_dir / "state.sqlite3")
    report_store: ReportStore = SQLiteReportStore(runtime_dir / "state.sqlite3")
    validation_store: ValidationStore = SQLiteValidationStore(runtime_dir / "state.sqlite3")
    experiment_store = FileExperimentStore(runtime_dir / "experiments")
    shortlist_store = FileShortlistStore(runtime_dir / "shortlists")
    prediction_store = FilePredictionStore(runtime_dir / "predictions", clock=clock)
    batch_store.recover_interrupted(now=clock())
    logger.info(
        "storage_initialized",
        extra={
            "runtime_dir": str(runtime_dir),
            "schema_version": migration_result.to_version,
        },
    )
    return StorageContainer(
        evidence_store=evidence_store,
        repository=repository,
        memory=memory,
        recovery_store=recovery_store,
        batch_store=batch_store,
        portfolio_ledger=portfolio_ledger,
        watchlist_store=watchlist_store,
        report_store=report_store,
        validation_store=validation_store,
        experiment_store=experiment_store,
        shortlist_store=shortlist_store,
        prediction_store=prediction_store,
        migration_result=migration_result,
    )
