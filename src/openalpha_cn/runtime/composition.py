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

Field types mirror the storage-Protocol layer Task 9 (V2-P0B-003) built: six of sixteen
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from openalpha_cn.agents.base import FeaturePlane, ResearchAgent
from openalpha_cn.backtest.validation import ValidationStore
from openalpha_cn.evidence.service import EvidenceStore
from openalpha_cn.models.deepseek import VendorEndpoint
from openalpha_cn.models.openai_compatible import (
    JsonTransport,
    ModelConfigurationError,
    OpenAICompatibleProvider,
    UrllibJsonTransport,
)
from openalpha_cn.models.recording import RecordingJsonTransport
from openalpha_cn.product.research import ReportStore, WatchlistStore
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import ResearchMemory
from openalpha_cn.runtime.recovery import RecoveryStore
from openalpha_cn.runtime.repository import RunRepository
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.factor_experiments import FileExperimentStore
from openalpha_cn.storage.jobs import SQLiteJobStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import MigrationRunResult, run_migrations
from openalpha_cn.storage.model_exchanges import FileModelExchangeStore
from openalpha_cn.storage.models import SQLiteModelUsageStore
from openalpha_cn.storage.paper_state import FilePaperStateStore
from openalpha_cn.storage.parquet import ParquetEvidenceStore
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger, open_paper_ledger
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.storage.product import SQLiteReportStore, SQLiteWatchlistStore
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.shortlists import FileShortlistStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository
from openalpha_cn.storage.validation import SQLiteValidationStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageContainer:
    """All sixteen storage components assembled for one shared `runtime_dir`.

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
    job_store: SQLiteJobStore
    """`V2-P5-010`'s durable trading-day schedules and their per-session runs.

    **The thirteenth store, and it was shipped without this wiring rather than with it.**
    `V2-P5-010` said so in its own row -- no CLI command, no REST route, not in `build_storage`,
    "出货产品中没有任何东西调用它们" -- and left the caller to a later row. `V2-P5-013` is that row.

    Concrete rather than Protocol-typed, and for `batch_store`'s reason exactly: `openalpha jobs
    list` and `GET /api/v1/jobs` call `list_jobs()`/`runs()`, while `TradingDayScheduler` (the
    only *service*-layer consumer) calls neither. A Protocol wide enough for both would hand the
    scheduler a listing it has no business asking for.

    It shares `state.sqlite3` with the other nine and creates its two tables with `CREATE TABLE
    IF NOT EXISTS` rather than through a migration -- see `storage/jobs.py`'s module docstring
    for the measurement behind that, which is that migrations 3 through 8 never run on a fresh
    database at all, so a ninth adding these tables would never run either.
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
    model_usage_store: SQLiteModelUsageStore
    """`V2-P0B-011`'s durable per-request LLM provider usage and cost accounting.

    Not the "model" in this file's opening "panel, factor, model, ranking, portfolio" --
    that third item is the AlphaModel fit/predict plane `prediction_store` fills (see `model
    daily-run` a few paragraphs up); this store is orthogonal to that plane, an LLM-call
    governance concern (`models.base.ModelProvider`, `agents/model.py`) rather than a stage
    of the quant research pipeline. Two different things this package calls "model."

    **The fourteenth store, and it shipped with none of this wiring at all.** `V2-P0B-011`
    moved the class here, out of `models/governance.py` and into `storage/models.py`, so that
    `openalpha_cn.models` would stop importing `sqlite3` directly (ADR-0001's
    `models-no-infra-imports` contract) -- but never added it to this container. That is a
    different gap from `job_store`'s: `job_store` names its own two-step history above (shipped
    unwired by `V2-P5-010`, wired by `V2-P5-013`, both in that field's own docstring); no
    equivalent second step was ever recorded for this one. It surfaced twice, independently:
    once when the foreign-key-enforcement registry
    (`tests/integration/storage/test_foreign_key_enforcement.py`) turned out to cover eight of
    what are now ten
    `state.sqlite3` stores it discovers by name, `SQLiteModelUsageStore` being one of the two
    missing entries -- registered there before this field existed, so it has been checked for
    `PRAGMA foreign_keys` all along, construction site or not -- and again here, where
    `grep -rn "SQLiteModelUsageStore(" src/` found no construction site anywhere in this
    package until this field, the `model_usage_store_at` opener, and `llm_provider` beside it
    (`V2-P7-012`).

    Concrete rather than Protocol-typed, but not for `batch_store`'s or `job_store`'s reason --
    neither has a wider surface here to narrow against. `models.governance.ModelUsageStore`,
    the Protocol `models/openai_compatible.py`'s `usage_store` parameter is already typed
    against, declares exactly this class's full public surface (`append`/`list`) -- there is
    nothing wider a Protocol here would need to hide. It stays concrete because, measured
    rather than assumed, nothing inside `openalpha_cn` calls through this field to narrow
    against in the first place: `OpenAICompatibleProvider` -- the one class that would ever
    call `.append()` on it -- was constructed only in tests until `llm_provider` above became
    its one `src/` construction site (`V2-P7-012`), and `llm_provider` passes this store as the
    `usage_store` parameter rather than reaching through the container. The caller this field
    also serves is external: a deployer who builds their own LLM-backed agent through
    `models/openai_compatible.py` and wants the same `runtime_dir`-scoped `state.sqlite3`
    every other store here already shares, rather than hand-rolling a separate path the way
    this file's own opening paragraph says `sdk.py`/`api/app.py` used to, by hand, before
    `build_storage` existed.

    No CLI command, REST route, or SDK method reads or writes through this field today, and
    that is stated rather than left to be discovered: in `job_store`'s own vocabulary, wiring
    the store into this container and giving it a "face" are two different rows, and only the
    first has been asked for so far.
    """
    paper_ledger: SQLitePortfolioLedger
    """`V2-P7-017`'s ledger of the paper book's own, at `runtime_dir / "paper" / "ledger.sqlite3"`.

    **A second instance of `portfolio_ledger`'s class, at a second file, and never the same
    table.** `portfolio_ledger` is where `sdk.py`'s `run_portfolio_backtest` and the order routes
    of `api/app.py` append, and its `portfolio_transitions` table has no column naming a book: a
    paper transition written there would be listed among backtest ones, and an attempt id a
    backtest happened to reuse would be refused as a conflict of the other's payload. T5 (section
    6) measured that and asked for this instance. The file is the paper book's alone, so it shares
    none of `state.sqlite3`'s rows -- and none of its migrations either: `run_migrations` opens
    `state.sqlite3` only, so `SQLitePortfolioLedger`'s `CREATE TABLE IF NOT EXISTS` gives this
    file the table as first created and nothing a migration added since (migration 4's
    `portfolio_transitions_subject_idx` was already one such). `storage.portfolio.
    open_paper_ledger` repeats those additions (`PAPER_LEDGER_MIGRATED_SCHEMA`), and
    `tests/unit/backtest/test_paper_state.py::
    test_the_paper_ledger_file_has_the_schema_the_migrations_give_the_backtest_ledger` compares
    the two files' `portfolio_transitions` after `build_storage`, so a future migration of that
    table that does not reach this file goes red.

    Concrete for `portfolio_ledger`'s reason: whoever reads the paper book's record back calls
    `list()`, which `PortfolioLedger` deliberately does not declare. Nothing appends to it yet --
    the daily settlement (`V2-P7-009`) is its writer and has not landed.
    """
    paper_state_store: FilePaperStateStore
    """`V2-P7-017`'s daily paper-book snapshots, under `runtime_dir / "paper" / "snapshots"`.

    One write-once document per settled session; `backtest/paper_state.py` renders, reads and
    verifies them, and this store keeps the bytes. Concrete for `experiment_store`'s reason: the
    Protocol its consumer declares (`backtest.paper_state.PaperStateDocumentStore`) lives above
    `openalpha_cn.storage`. Nothing writes to it yet, for `paper_ledger`'s reason.
    """
    migration_result: MigrationRunResult


EVIDENCE_DIRECTORY: Final[str] = "evidence"
"""Where a runtime directory's point-in-time evidence partitions live (`V2-P7-011`).

The one spelling of the literal that `build_storage` used to write by hand; see
`evidence_store_at`.
"""

MODEL_EXCHANGE_DIRECTORY: Final[str] = "model_exchanges"
"""Where a runtime directory's content-addressed model exchanges live (`V2-P7-012`).

The one spelling of the literal; see `model_exchange_store_at`. No `StorageContainer` field
names this store: it is not a stage of a run but the recorder around the one network call, so
`llm_provider` opens it at the point of use and there is nothing for `build_storage` to assemble.
"""

PAPER_SNAPSHOT_DIRECTORY: Final[tuple[str, str]] = ("paper", "snapshots")
"""Where a runtime directory's paper-book snapshots live (`V2-P7-017`)."""

PREDICTION_DIRECTORY: Final[str] = "predictions"
"""Where a runtime directory's registered predictions live (`V2-P4-017`)."""

SHORTLIST_DIRECTORY: Final[str] = "shortlists"
"""Where a runtime directory's stored shortlist answers live (`V2-P4-062`).

The one spelling of the literal five `cli.py` call sites used to write by hand; see
`shortlist_store_at`.
"""

EXPERIMENT_DIRECTORY: Final[str] = "experiments"
"""Where a runtime directory's sealed factor experiment documents live (`V2-P3-015`).

The one spelling of the literal `cli.py`'s `factor experiment` command used to write by hand; see
`experiment_store_at`.
"""

STATE_DATABASE: Final[str] = "state.sqlite3"
"""The SQLite file ten of the sixteen stores share at a runtime directory's root.

Named once for `run_repository_at` and `model_usage_store_at`, the two of those ten that a
caller opens without a `build_storage`; the other eight stay inline in `build_storage`, which is
already their one source.
"""


def evidence_store_at(runtime_dir: Path) -> ParquetEvidenceStore:
    """The point-in-time evidence store under `runtime_dir`, opened alone: no migration, and the
    directory is created by `ParquetEvidenceStore.__init__` exactly as it is inside
    `build_storage`.

    For `openalpha evidence limit-events`, which appends one session's derived events without
    assembling sixteen stores, and for any later reader with the same need. `build_storage`
    opens `runtime_dir / "evidence"` through this function, so there is one source for it (hard
    rule 5)."""
    return ParquetEvidenceStore(runtime_dir / EVIDENCE_DIRECTORY)


def model_exchange_store_at(runtime_dir: Path) -> FileModelExchangeStore:
    """The model-exchange documents under `runtime_dir`, opened alone: no migration, no directory
    created (`FileModelExchangeStore`'s reads are total on a missing directory; `put` creates it).

    For `llm_provider`, which wraps an endpoint's transport in the record/replay decorator. This
    store is deliberately not a `StorageContainer` field: it lives around a network call rather
    than inside a run, so the container stays at sixteen and `llm_provider` is its one opener in
    this composition root (hard rule 5)."""
    return FileModelExchangeStore(runtime_dir / MODEL_EXCHANGE_DIRECTORY)


def model_usage_store_at(runtime_dir: Path) -> SQLiteModelUsageStore:
    """The per-request LLM usage ledger at `runtime_dir / "state.sqlite3"`, opened alone: no
    migration (the caller that must run migrations is `build_storage`), and the store's own
    `CREATE TABLE IF NOT EXISTS` is its only schema work, exactly as inside `build_storage`.

    For `llm_provider`, which records usage when a request is actually made; `build_storage`
    opens the same path through this function, so there is one source for it (hard rule 5)."""
    return SQLiteModelUsageStore(runtime_dir / STATE_DATABASE)


def llm_provider(
    endpoint: VendorEndpoint,
    *,
    runtime_dir: Path,
    clock: Callable[[], datetime],
    replay: bool,
    transport: JsonTransport | None = None,
) -> OpenAICompatibleProvider:
    """The one assembly of a vendor endpoint's provider (`V2-P7-012`).

    Every model the research path can call goes through here, so three facts are true in one
    place rather than at each call site:

    - **The exchange is recorded.** The provider's transport is a `RecordingJsonTransport` over
      `model_exchange_store_at(runtime_dir)`; in `replay` it has no inner transport at all, so an
      unrecorded request raises `ModelExchangeNotStoredError` instead of touching a network.
    - **The credential is a name.** `api_key_env` is passed in record mode and `None` in replay,
      so a replayed run needs no key; the value is read from the process environment by the
      provider and never stored (`models/recording.py` excludes headers from the request digest).
    - **An unverified endpoint cannot spend a real request.** When `verified_on` is `None` and
      the caller supplied no transport, the shipped placeholder would otherwise call the
      vendor's endpoint with an invented model id. That is a `ModelConfigurationError` naming the
      endpoint and its documentation URL; an injected transport (tests) and replay are allowed,
      because neither reaches the vendor.

    Usage is attached in record mode only: the row is billed when the request was made, and a
    replay must not append a second one -- `SQLiteModelUsageStore.append` is idempotent only for
    a byte-identical payload, and a replayed `occurred_at` is a different wall clock.
    """
    if not replay and transport is None and endpoint.verified_on is None:
        raise ModelConfigurationError(
            f"{endpoint.name} ({endpoint.provider_id}/{endpoint.model}) has not been verified: "
            "its model id, base URL, structured-output mode and prices must be filled from "
            f"{endpoint.documentation_url} and `verified_on` set to the date of that reading "
            "before a live request is spent. Replay and an injected transport are allowed while "
            "it is unverified; this refusal is the difference between configured and verified"
        )
    inner: JsonTransport | None = None if replay else (transport or UrllibJsonTransport())
    recording = RecordingJsonTransport(
        store=model_exchange_store_at(runtime_dir),
        inner=inner,
        clock=clock,
    )
    return OpenAICompatibleProvider(
        provider_id=endpoint.provider_id,
        model=endpoint.model,
        base_url=endpoint.base_url,
        api_key_env=None if replay else endpoint.api_key_env,
        structured_output_mode=endpoint.structured_output_mode,
        temperature=endpoint.temperature,
        max_tokens=endpoint.max_output_tokens,
        require_usage=True,
        transport=recording,
        usage_store=None if replay else model_usage_store_at(runtime_dir),
        input_price_per_million=endpoint.input_price_per_million,
        output_price_per_million=endpoint.output_price_per_million,
        clock=clock,
    )


def paper_state_store_at(runtime_dir: Path) -> FilePaperStateStore:
    """The paper book's snapshot store under `runtime_dir`, opened alone: no migration, no
    directory created (`FilePaperStateStore`'s reads are total on a missing directory). For a
    reader that must not run `build_storage` -- `--show-book`. `build_storage` opens the same
    path through this function, so there is one source for it (hard rule 5)."""
    return FilePaperStateStore(runtime_dir.joinpath(*PAPER_SNAPSHOT_DIRECTORY))


def prediction_store_at(runtime_dir: Path, *, clock: Callable[[], datetime]) -> FilePredictionStore:
    """The prediction store under `runtime_dir`, opened alone: no migration, no directory
    created. For a reader of another registration's runtime directory, which it must not write
    (`V2-P7-023`: a predecessor's records). `build_storage` opens the same path through this
    function."""
    return FilePredictionStore(runtime_dir / PREDICTION_DIRECTORY, clock=clock)


def shortlist_store_at(runtime_dir: Path) -> FileShortlistStore:
    """`V2-P4-062`'s shortlist answers under `runtime_dir`, opened alone: no migration, no
    directory created (`FileShortlistStore`'s reads are total on a missing directory).

    For `openalpha shortlist run`, which writes one answer, and for `shortlist get`/`list`/
    `compare`/`portfolio construct`, which resolve an address a caller supplied without assembling
    sixteen stores. `build_storage` opens `runtime_dir / "shortlists"` through this function, so
    there is one source for it (hard rule 5)."""
    return FileShortlistStore(runtime_dir / SHORTLIST_DIRECTORY)


def experiment_store_at(runtime_dir: Path) -> FileExperimentStore:
    """`V2-P3-015`'s sealed experiment documents under `runtime_dir`, opened alone: no migration,
    no directory created (`FileExperimentStore`'s reads are total on a missing directory).

    For `openalpha factor experiment`, which writes one sealed document through the same path
    `build_storage` opens. `build_storage` opens `runtime_dir / "experiments"` through this
    function, so there is one source for it (hard rule 5)."""
    return FileExperimentStore(runtime_dir / EXPERIMENT_DIRECTORY)


def run_repository_at(runtime_dir: Path) -> SQLiteRunRepository:
    """The `runs`/`decisions` repository under `runtime_dir`, opened alone: no migration, and the
    store's own `_initialize` is the only schema work (`SQLiteRunRepository.__init__` creates its
    parent directory and its tables, exactly as it does inside `build_storage`).

    For `openalpha shortlist run` and `openalpha model daily-run`, which need the store one
    command writes through without the other fifteen. `build_storage` opens
    `runtime_dir / "state.sqlite3"` through this function, so there is one source for it (hard
    rule 5)."""
    return SQLiteRunRepository(runtime_dir / STATE_DATABASE)


def research_engine(
    *,
    repository: RunRepository,
    memory: ResearchMemory,
    recovery_store: RecoveryStore,
    clock: Callable[[], datetime],
    agents: Sequence[ResearchAgent] | None = None,
    features: FeaturePlane | None = None,
) -> ResearchEngine:
    """The one assembly of the shared-path research engine (`V2-P7-013`).

    `sdk.py` and `api/app.py` each built `ResearchEngine` by hand -- the SDK once, the REST face
    twice -- with the same four required wires. The two optional wires are the reason this
    function defaults them to `None` rather than filling them: the SDK threads `self.agents` and
    `self.features` through, and the REST face deliberately composes neither, so a shared opener
    that supplied a default roster or plane would silently change what
    `POST /api/v1/research/run` runs. Passing exactly the six keywords means the default is
    `ResearchEngine.__init__`'s decision and not a second copy of it here: an absent roster is
    `baseline_agents()` and an absent plane is `None`.
    """
    return ResearchEngine(
        repository=repository,
        memory=memory,
        clock=clock,
        recovery_store=recovery_store,
        agents=agents,
        features=features,
    )


def build_storage(*, runtime_dir: Path, clock: Callable[[], datetime]) -> StorageContainer:
    """Run pending schema migrations, then assemble every storage component once.

    All sixteen stores share one `runtime_dir`: ten at its root-level `state.sqlite3`
    (matching the pre-existing per-store convention), plus the Parquet evidence store
    under `runtime_dir / "evidence"`, the sealed experiment documents under
    `runtime_dir / "experiments"`, `V2-P4-062`'s shortlist answers under
    `runtime_dir / "shortlists"`, `V2-P4-017`'s registered predictions under
    `runtime_dir / "predictions"`, and `V2-P7-017`'s paper book -- its own ledger at
    `runtime_dir / "paper" / "ledger.sqlite3"` and its snapshots under
    `runtime_dir / "paper" / "snapshots"`. Interrupted-batch recovery runs here, using the
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
    evidence_store: EvidenceStore = evidence_store_at(runtime_dir)
    repository = run_repository_at(runtime_dir)
    memory: ResearchMemory = SQLiteResearchMemory(runtime_dir / "state.sqlite3")
    recovery_store: RecoveryStore = SQLiteRecoveryStore(runtime_dir / "state.sqlite3")
    batch_store = SQLiteBatchTaskStore(runtime_dir / "state.sqlite3")
    portfolio_ledger = SQLitePortfolioLedger(runtime_dir / "state.sqlite3")
    watchlist_store: WatchlistStore = SQLiteWatchlistStore(runtime_dir / "state.sqlite3")
    report_store: ReportStore = SQLiteReportStore(runtime_dir / "state.sqlite3")
    validation_store: ValidationStore = SQLiteValidationStore(runtime_dir / "state.sqlite3")
    experiment_store = experiment_store_at(runtime_dir)
    shortlist_store = shortlist_store_at(runtime_dir)
    job_store = SQLiteJobStore(runtime_dir / "state.sqlite3")
    model_usage_store = model_usage_store_at(runtime_dir)
    prediction_store = prediction_store_at(runtime_dir, clock=clock)
    paper_ledger = open_paper_ledger(runtime_dir / "paper" / "ledger.sqlite3")
    paper_state_store = paper_state_store_at(runtime_dir)
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
        job_store=job_store,
        model_usage_store=model_usage_store,
        paper_ledger=paper_ledger,
        paper_state_store=paper_state_store,
        migration_result=migration_result,
    )
