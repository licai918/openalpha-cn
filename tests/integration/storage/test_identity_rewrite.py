"""`V2-P4-001`'s identity-rewrite migration, against a database written at the old versions.

Roadmap section 8 is the brief for this file. A contract version bump moves a
content-addressed identity; two of the three contracts `V2-P4-001` bumps have that identity as
a *stored key*; so a transparent read-time upcast would recompute the key and leave every
reference to it spelling the old value. The prescription is one explicit migration -- read the
old row, advance the version, recompute the ID, and update every row that references it in the
same transaction -- and these tests are what say it actually happened.

The database is built by writing genuine v1 JSON directly, not through today's stores, because
today's stores can no longer produce a v1 payload. That is the whole point: the fixture has to
be what a real pre-`V2-P4-001` install left behind, or the migration is being tested against
rows it did not have to migrate. (`test_migrations.py`'s `_build_v1_shaped_database` is about
the pre-migration-engine *table* layout and does write through the stores; the two fixtures
answer different questions.)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.batch_contracts import BatchResearchTask, BatchResultRef, BatchTaskItem
from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.decision import (
    DECISION_LEDGER_VERSIONS,
    AgentDecision,
    DecisionLedgerV1,
)
from openalpha_cn.domain.memory import MemoryEntry
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.run import RUN_MANIFEST_VERSIONS, RunManifestV1
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.validation import (
    VALIDATION_RESULT_VERSIONS,
    AttributionTermV1,
    ValidationResultV1,
)
from openalpha_cn.domain.versioning import IdentityRewriteRequiredError, read_versioned
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import (
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    MigrationFailedError,
    UnmigratableHorizonError,
    _rewrite_contract_identities,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64
RUN_ID: Final[str] = "run_pre_p4"


def _v1_manifest() -> RunManifestV1:
    return RunManifestV1(
        run_id=RUN_ID,
        mode="replay",
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
        started_at=NOW,
        finished_at=NOW,
        status="succeeded",
    )


def _v1_decision() -> DecisionLedgerV1:
    return DecisionLedgerV1(
        run_id=RUN_ID,
        created_at=NOW,
        agent_outputs=(
            AgentDecision(
                agent_id="market-agent",
                signal_id="sig_pre_p4",
                recommendation="support",
                rationale="Price and volume confirm the event.",
            ),
        ),
        routing_path=("market-agent", "risk-gate"),
        risk_decision="pass",
        final_action="watch",
        evidence_ids=("ev_pre_p4",),
        signal_ids=("sig_pre_p4",),
        code_commit="0123456789abcdef",
    )


def _v1_validation(decision_id: str) -> ValidationResultV1:
    return ValidationResultV1(
        signal_id="sig_pre_p4",
        decision_id=decision_id,
        observation_start=NOW,
        observation_end=NOW + timedelta(days=5),
        realized_return=0.10,
        benchmark_return=0.02,
        transaction_cost=0.005,
        attribution=(
            AttributionTermV1(category="rule", name="decision-policy", contribution=0.025),
            AttributionTermV1(category="agent", name="market-agent", contribution=0.05),
        ),
        confidence=0.8,
    )


def _batch_task(decision_id: str) -> BatchResearchTask:
    return BatchResearchTask(
        batch_id="batch_pre_p4",
        items=(
            BatchTaskItem(
                request=ResearchRunRequest(
                    run_id=RUN_ID,
                    mode="replay",
                    subject="000001.SZ",
                    as_of=NOW,
                    evidence=(),
                    code_commit="0123456789abcdef",
                    config_digest=DIGEST,
                    random_seed=7,
                ),
                status="succeeded",
                result=BatchResultRef(
                    decision_id=decision_id, signal_id="sig_pre_p4", final_action="watch"
                ),
            ),
        ),
        status="succeeded",
        max_concurrency=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _build_pre_p4_database(path: Path) -> dict[str, Any]:
    """Populate `path` with the tables today's stores own and the payloads yesterday's wrote.

    Every store is constructed so its `CREATE TABLE IF NOT EXISTS` runs -- including
    `SQLitePortfolioLedger`, which nothing here writes to, because `create_query_path_indexes`
    requires `portfolio_transitions` and would otherwise defer and take everything ordered
    after it, this migration included, with it. `validation_results` is created by hand for
    the mirror-image reason: it is owned by a *migration* rather than by a store, and creating
    it here is what makes the fixture a database whose rows predate `V2-P4-001` rather than
    one that predates `V2-P0B-010`.
    """
    SQLiteRunRepository(path)
    SQLiteResearchMemory(path)
    SQLiteReportStore(path)
    SQLiteBatchTaskStore(path)
    SQLiteRecoveryStore(path)
    SQLitePortfolioLedger(path)

    manifest = _v1_manifest()
    decision = _v1_decision()
    old_decision_id = stable_model_id(prefix="dec", model=decision)
    validation = _v1_validation(old_decision_id)
    old_validation_id = stable_model_id(prefix="val", model=validation)
    entry = MemoryEntry(
        run_id=RUN_ID,
        subject="000001.SZ",
        created_at=NOW,
        decision_id=old_decision_id,
        signal_id="sig_pre_p4",
        summary="written before V2-P4-001",
    )
    report = ResearchReport(
        run_id=RUN_ID,
        subject="000001.SZ",
        created_at=NOW,
        title="pre-P4 report",
        summary="written before V2-P4-001",
        decision_id=old_decision_id,
        signal_id="sig_pre_p4",
        final_action="watch",
        evidence_ids=("ev_pre_p4",),
        risk_flags=(),
    )
    task = _batch_task(old_decision_id)

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS validation_results (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_id TEXT UNIQUE NOT NULL,
                decision_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO runs (run_id, payload) VALUES (?, ?)",
            (RUN_ID, manifest.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO decisions (decision_id, run_id, payload) VALUES (?, ?, ?)",
            (old_decision_id, RUN_ID, decision.model_dump_json()),
        )
        connection.execute(
            """
            INSERT INTO validation_results (validation_id, decision_id, signal_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (old_validation_id, old_decision_id, "sig_pre_p4", validation.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO research_memory (decision_id, subject, payload) VALUES (?, ?, ?)",
            (old_decision_id, "000001.SZ", entry.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO research_reports (report_id, subject, payload) VALUES (?, ?, ?)",
            (report.report_id, "000001.SZ", report.model_dump_json(exclude_computed_fields=True)),
        )
        connection.execute(
            "INSERT INTO batch_tasks (batch_id, status, payload) VALUES (?, ?, ?)",
            ("batch_pre_p4", "succeeded", task.model_dump_json(exclude_computed_fields=True)),
        )
    return {
        "decision_id": old_decision_id,
        "validation_id": old_validation_id,
        "report_id": report.report_id,
    }


def _column(path: Path, query: str) -> list[Any]:
    with closing(sqlite3.connect(path)) as connection:
        return [row[0] for row in connection.execute(query).fetchall()]


def test_a_pre_p4_database_reads_back_at_the_current_version_after_migrating(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The gate P4 owes: records written at v1 are still readable after the bump.

    Readable *and* re-keyed. The manifest keeps its caller-supplied `run_id` and gains the
    content address the ledger needs; the ledger's own key moves because it gained a field.
    """
    path = tmp_path / "state.sqlite3"
    before = _build_pre_p4_database(path)

    run_migrations(path, clock=migration_clock)

    assert read_status(path).current_version == REWRITE_CONTRACT_IDENTITIES_VERSION
    repository = SQLiteRunRepository(path)
    manifest = repository.get_run(RUN_ID)
    assert manifest is not None
    assert manifest.schema_version == "run-manifest/v2"
    assert manifest.mode == "replay"
    assert manifest.config_digest == DIGEST

    decision = repository.get_decision_for_run(RUN_ID)
    assert decision is not None
    assert decision.schema_version == "decision-ledger/v2"
    assert decision.run_manifest_id == manifest.run_manifest_id
    assert decision.decision_id != before["decision_id"]
    assert decision.routing_path == ("market-agent", "risk-gate")
    assert decision.signal_ids == ("sig_pre_p4",)


def test_every_reference_to_the_moved_decision_follows_it_in_the_same_transaction(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The half section 8 says is silent when it goes wrong.

    Five places name a decision: the `decisions` primary key, `validation_results`' column and
    payload, `research_memory`'s `UNIQUE` column and payload, `research_reports`' payload (and
    therefore its own `report_id`, which hashes it), and `batch_tasks`' nested
    `BatchResultRef`. All five are asserted, and the old ID is asserted *absent* from each --
    "the new one is there" alone would pass on a table that gained a row instead of moving one.
    """
    path = tmp_path / "state.sqlite3"
    before = _build_pre_p4_database(path)

    run_migrations(path, clock=migration_clock)

    repository = SQLiteRunRepository(path)
    decision = repository.get_decision_for_run(RUN_ID)
    assert decision is not None
    moved = decision.decision_id

    assert _column(path, "SELECT decision_id FROM decisions") == [moved]
    assert _column(path, "SELECT decision_id FROM validation_results") == [moved]
    assert _column(path, "SELECT decision_id FROM research_memory") == [moved]

    validation = read_versioned(
        VALIDATION_RESULT_VERSIONS, _column(path, "SELECT payload FROM validation_results")[0]
    )
    assert validation.decision_id == moved
    assert validation.schema_version == "validation-result/v2"
    assert validation.unexplained_return == 0.0
    assert validation.validation_id != before["validation_id"]
    assert _column(path, "SELECT validation_id FROM validation_results") == [
        validation.validation_id
    ]

    entry = MemoryEntry.model_validate_json(_column(path, "SELECT payload FROM research_memory")[0])
    assert entry.decision_id == moved

    report = ResearchReport.model_validate_json(
        _column(path, "SELECT payload FROM research_reports")[0]
    )
    assert report.decision_id == moved
    assert _column(path, "SELECT report_id FROM research_reports") == [report.report_id]
    assert report.report_id != before["report_id"]

    task = BatchResearchTask.model_validate_json(
        _column(path, "SELECT payload FROM batch_tasks")[0]
    )
    assert task.items[0].result is not None
    assert task.items[0].result.decision_id == moved

    with closing(sqlite3.connect(path)) as connection:
        dump = "\n".join(line for line in connection.iterdump())
    assert before["decision_id"] not in dump
    assert before["validation_id"] not in dump


def test_the_rewrite_is_idempotent_and_a_second_run_changes_nothing(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Forward-only migrations run once, but the *function* must be safe to re-run.

    `PRAGMA user_version` already stops a second application, so this re-applies the pass
    deliberately, with the registry's own migration, and compares the whole dump. A rewrite
    that re-keyed already-current rows would produce a second, different identity for the same
    contents -- the failure mode a content address exists to make impossible.
    """
    path = tmp_path / "state.sqlite3"
    _build_pre_p4_database(path)
    run_migrations(path, clock=migration_clock)
    with closing(sqlite3.connect(path)) as connection:
        first = "\n".join(connection.iterdump())

    with closing(sqlite3.connect(path)) as connection, connection:
        _rewrite_contract_identities(connection)
    with closing(sqlite3.connect(path)) as connection:
        second = "\n".join(connection.iterdump())

    assert first == second


def test_reading_an_unmigrated_v1_row_refuses_instead_of_upcasting_it(
    tmp_path: Path,
) -> None:
    """Section 8's "不能靠读时透明 upcast", enforced rather than documented.

    The `runs` row upgrades transparently in the same breath, and that asymmetry is the
    argument: `run_id` is caller-supplied and does not move, so nothing can be stranded by
    upgrading a manifest on read, while a decision's key *is* its content address.
    """
    path = tmp_path / "state.sqlite3"
    before = _build_pre_p4_database(path)
    repository = SQLiteRunRepository(path)

    manifest = repository.get_run(RUN_ID)
    assert manifest is not None
    assert manifest.schema_version == "run-manifest/v2"

    with pytest.raises(IdentityRewriteRequiredError, match="openalpha migrate run") as decision_err:
        repository.get_decision(before["decision_id"])
    assert decision_err.value.contract == "decision-ledger"

    raw_validation = _column(path, "SELECT payload FROM validation_results")[0]
    with pytest.raises(IdentityRewriteRequiredError, match="content-derived identity") as val_err:
        read_versioned(VALIDATION_RESULT_VERSIONS, raw_validation)
    assert val_err.value.contract == "validation-result"


def test_the_registries_still_read_a_v1_payload_before_deciding_what_to_do_with_it(
    tmp_path: Path,
) -> None:
    """A malformed v1 row must fail as malformed, not as "needs migrating".

    `read_versioned` validates against the payload's own version's model before consulting the
    upgrade, so the two failures stay distinguishable -- which is what lets an operator tell
    "run the migration" apart from "this row is corrupt".
    """
    path = tmp_path / "state.sqlite3"
    _build_pre_p4_database(path)
    broken = json.dumps({"schema_version": "decision-ledger/v1", "run_id": RUN_ID})

    with pytest.raises(ValueError, match="Field required") as error:
        read_versioned(DECISION_LEDGER_VERSIONS, broken)
    assert not isinstance(error.value, IdentityRewriteRequiredError)


def test_a_stored_calendar_horizon_refuses_the_whole_rewrite_by_name(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The one thing `SignalFrame`'s narrowing strands, refused rather than converted.

    A `3m` horizon inside a stored recovery state is outside the contract from `V2-P4-001` on,
    and turning it into a session count needs the sessions-per-month constant this repository
    has never measured. Refusing names the run and states the two remedies, instead of
    surfacing later as a regex `ValidationError` from whichever store read it first.

    The rollback matters as much as the refusal: nothing may be half re-keyed after this.
    """
    path = tmp_path / "state.sqlite3"
    _build_pre_p4_database(path)
    stranded = {
        "schema_version": "run-recovery/v1",
        "run_id": "run_calendar_horizon",
        "request_digest": DIGEST,
        "graph_signature": DIGEST,
        "agent_ids": ["market-agent"],
        "completed_results": [
            {
                "agent_id": "market-agent",
                "signal": {
                    "schema_version": "signal-frame/v1",
                    "subject": "000001.SZ",
                    "as_of": NOW.isoformat(),
                    "direction": "bullish",
                    "strength": 0.4,
                    "confidence": 0.6,
                    "horizon": "3m",
                    "evidence_ids": ["ev_pre_p4"],
                    "confirmation_conditions": [],
                    "invalidation_conditions": [],
                    "risk_flags": [],
                    "abstention_reason": None,
                },
                "rationale": "ok",
            }
        ],
        "next_agent_index": 1,
        "attempt_count": 1,
        "status": "running",
        "started_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "error_type": None,
    }
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO run_recovery (run_id, request_digest, graph_signature, status, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("run_calendar_horizon", DIGEST, DIGEST, "running", json.dumps(stranded)),
        )

    with pytest.raises(MigrationFailedError) as error:
        run_migrations(path, clock=migration_clock)

    assert error.value.name == "rewrite_contract_identities"
    cause = error.value.__cause__
    assert isinstance(cause, UnmigratableHorizonError)
    assert "run_calendar_horizon" in str(cause)
    assert "3m" in str(cause)
    assert "trading days" in str(cause)

    assert read_status(path).current_version < REWRITE_CONTRACT_IDENTITIES_VERSION
    assert (
        read_versioned(
            RUN_MANIFEST_VERSIONS, _column(path, "SELECT payload FROM runs")[0]
        ).schema_version
        == "run-manifest/v2"
    )  # upgraded on read; the stored payload is untouched
    assert json.loads(_column(path, "SELECT payload FROM runs")[0])["schema_version"] == (
        "run-manifest/v1"
    )


def test_build_storage_migrates_a_pre_p4_runtime_directory_end_to_end(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The path a real operator takes: start the SDK or the API against an old runtime dir.

    `build_storage` is the migration engine's only mount point, so this is what actually
    happens to somebody's accumulated records -- and it has to happen without the caller doing
    anything, which is why it is asserted through `build_storage` rather than
    `run_migrations`.
    """
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    before = _build_pre_p4_database(runtime_dir / "state.sqlite3")

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    assert storage.migration_result.to_version == REWRITE_CONTRACT_IDENTITIES_VERSION
    decision = storage.repository.get_decision_for_run(RUN_ID)
    assert decision is not None
    assert decision.decision_id != before["decision_id"]
    assert storage.validation_store.list_by_decision(decision.decision_id) != ()
    assert storage.memory.list(subject="000001.SZ")[0].decision_id == decision.decision_id
