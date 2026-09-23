"""`V2-P4-010`'s identity rewrite, against a database written at `run-manifest/v2`.

The second migration roadmap section 8's rule demands, and it is owed for a reason the first
one could not have anticipated. `V2-P4-001` bumped three contracts and re-keyed the two whose
*own* stored key is content-derived. This issue bumps one contract -- `run-manifest` -- whose
stored key is `run_id` and does not move at all, and re-keys `decisions`, `validation_results`
and `research_reports` anyway, because `V2-P4-025` put the manifest's content address into
`DecisionLedger.run_manifest_id` in between. A field added to `RunManifest` therefore moves
three identities belonging to contracts this issue does not touch; the arithmetic is measured
in `tests/unit/domain/test_contract_identity.py::
test_the_component_planes_moved_the_addresses_the_migration_has_to_rewrite`.

The fixture writes genuine `run-manifest/v2` JSON rather than going through today's stores,
for the reason `test_identity_rewrite.py` states about its own: today's stores can no longer
produce a v2 payload, and a fixture built from what they *can* produce would be testing the
migration against rows it never had to migrate. The v2 address is computed with
`stable_model_id` over the frozen v2 snapshot, which is what the v2 build itself computed --
there is one hash function in this repository and this is it, applied to an older model.
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
    DecisionLedger,
)
from openalpha_cn.domain.memory import MemoryEntry
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.run import (
    RUN_MANIFEST_UNADDRESSED_FIELDS,
    RUN_MANIFEST_VERSIONS,
    RunManifestV2,
)
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.validation import AttributionTerm, ValidationResult
from openalpha_cn.domain.versioning import IdentityRewriteRequiredError, read_versioned
from openalpha_cn.storage.batch import SQLiteBatchTaskStore
from openalpha_cn.storage.memory import SQLiteResearchMemory
from openalpha_cn.storage.migrations import (
    SPLIT_BATCH_TASK_ITEMS_VERSION,
    MigrationFailedError,
    StrandedManifestReferenceError,
    _audit_manifest_references_resolve,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.portfolio import SQLitePortfolioLedger
from openalpha_cn.storage.product import SQLiteReportStore
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64
RUN_ID: Final[str] = "run_pre_component_planes"


def _v2_manifest() -> RunManifestV2:
    """A manifest as `V2-P4-025` wrote them: agent ids in `model_versions`, paired with the
    constant this issue removes."""
    return RunManifestV2(
        run_id=RUN_ID,
        mode="replay",
        as_of=NOW,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        model_versions=(
            {"component": "market-agent", "version": "baseline/v1"},  # type: ignore[arg-type]
        ),
        prompt_versions=(),
        random_seed=7,
        started_at=NOW,
        finished_at=NOW,
        status="succeeded",
    )


def _v2_address(manifest: RunManifestV2) -> str:
    """The address the v2 build computed for this manifest, by the v2 rule.

    `RunManifestV2` is a frozen snapshot and carries no `run_manifest_id` computed field, so
    the address is derived here the way `RunManifest.run_manifest_id` derives it -- same
    function, same exclusion set. Restating the exclusion set as a literal would let this
    fixture drift into computing an address the v2 build never produced, which would make the
    migration's input fictional.
    """
    return stable_model_id(
        prefix="run", model=manifest, exclude=frozenset(RUN_MANIFEST_UNADDRESSED_FIELDS)
    )


def _v2_decision(address: str) -> DecisionLedger:
    return DecisionLedger(
        run_id=RUN_ID,
        run_manifest_id=address,
        created_at=NOW,
        agent_outputs=(
            AgentDecision(
                agent_id="market-agent",
                signal_id="sig_pre_planes",
                recommendation="support",
                rationale="Price and volume confirm the event.",
            ),
        ),
        routing_path=("market-agent", "risk-gate"),
        risk_decision="pass",
        final_action="watch",
        evidence_ids=("ev_pre_planes",),
        signal_ids=("sig_pre_planes",),
        code_commit="0123456789abcdef",
    )


def _v2_validation(decision_id: str) -> ValidationResult:
    return ValidationResult(
        signal_id="sig_pre_planes",
        decision_id=decision_id,
        observation_start=NOW,
        observation_end=NOW + timedelta(days=5),
        realized_return=0.10,
        benchmark_return=0.02,
        transaction_cost=0.005,
        attribution=(
            AttributionTerm(category="rule", name="decision-policy", contribution=0.025),
            AttributionTerm(category="agent", name="market-agent", contribution=0.05),
        ),
        unexplained_return=0.0,
        confidence=0.8,
    )


def _batch_task(decision_id: str) -> BatchResearchTask:
    return BatchResearchTask(
        batch_id="batch_pre_planes",
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
                    decision_id=decision_id, signal_id="sig_pre_planes", final_action="watch"
                ),
            ),
        ),
        status="succeeded",
        max_concurrency=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _build_pre_component_plane_database(path: Path) -> dict[str, Any]:
    """A database as a post-`V2-P4-001`, pre-`V2-P4-010` install left it: stamped at 7.

    Stamped rather than migrated up to 7, because the point of the fixture is a database whose
    *rows* are v2 while its `user_version` says every migration through the batch split has
    already run -- which is exactly what an installed build one commit before this one has.
    """
    SQLiteRunRepository(path)
    SQLiteResearchMemory(path)
    SQLiteReportStore(path)
    SQLiteBatchTaskStore(path)
    SQLiteRecoveryStore(path)
    SQLitePortfolioLedger(path)

    manifest = _v2_manifest()
    address = _v2_address(manifest)
    decision = _v2_decision(address)
    validation = _v2_validation(decision.decision_id)
    entry = MemoryEntry(
        run_id=RUN_ID,
        subject="000001.SZ",
        created_at=NOW,
        decision_id=decision.decision_id,
        signal_id="sig_pre_planes",
        summary="written before V2-P4-010",
    )
    report = ResearchReport(
        run_id=RUN_ID,
        subject="000001.SZ",
        created_at=NOW,
        title="pre-plane report",
        summary="written before V2-P4-010",
        decision_id=decision.decision_id,
        signal_id="sig_pre_planes",
        final_action="watch",
        evidence_ids=("ev_pre_planes",),
        risk_flags=(),
    )
    task = _batch_task(decision.decision_id)

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
            (
                decision.decision_id,
                RUN_ID,
                decision.model_dump_json(exclude_computed_fields=True),
            ),
        )
        connection.execute(
            """
            INSERT INTO validation_results (validation_id, decision_id, signal_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (
                validation.validation_id,
                decision.decision_id,
                "sig_pre_planes",
                validation.model_dump_json(exclude_computed_fields=True),
            ),
        )
        connection.execute(
            "INSERT INTO research_memory (decision_id, subject, payload) VALUES (?, ?, ?)",
            (decision.decision_id, "000001.SZ", entry.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO research_reports (report_id, subject, payload) VALUES (?, ?, ?)",
            (report.report_id, "000001.SZ", report.model_dump_json(exclude_computed_fields=True)),
        )
        connection.execute(
            "INSERT INTO batch_tasks (batch_id, status, payload) VALUES (?, ?, ?)",
            ("batch_pre_planes", "succeeded", task.model_dump_json(exclude_computed_fields=True)),
        )
        connection.execute(f"PRAGMA user_version = {SPLIT_BATCH_TASK_ITEMS_VERSION}")
    return {
        "manifest_address": address,
        "decision_id": decision.decision_id,
        "validation_id": validation.validation_id,
        "report_id": report.report_id,
    }


def _column(path: Path, query: str) -> list[Any]:
    with closing(sqlite3.connect(path)) as connection:
        return [row[0] for row in connection.execute(query).fetchall()]


def test_reading_an_unmigrated_v2_manifest_refuses_instead_of_upcasting_it(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The refusal `V2-P4-010` adds, and the reason the v1 hop keeps upgrading.

    `upgrade_run_manifest_v1`'s licence is that "no stored key depends on the result". A v2
    manifest has one that does -- the very decision row written beside it here -- so a
    read-time upcast would hand back a manifest whose address that decision no longer names,
    with no exception raised anywhere. Asserted through the repository rather than through
    `read_versioned` alone, because the repository is the path an unmigrated database would
    actually be read on.
    """
    path = tmp_path / "state.sqlite3"
    _build_pre_component_plane_database(path)

    with pytest.raises(IdentityRewriteRequiredError, match="run-manifest"):
        SQLiteRunRepository(path).get_run(RUN_ID)


def test_the_manifest_address_moves_and_every_reference_follows_in_one_transaction(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The whole migration, asserted against the identities the fixture was built with.

    Four keys and two references are checked and every one is compared to the *stored* value
    rather than to a recomputed one, because a migration that updated a payload and not its key
    -- or a key and not the payload -- is precisely what `_audit_identity_rewrite` exists to
    catch, and a test that recomputed both from the same source could not see it.
    """
    path = tmp_path / "state.sqlite3"
    before = _build_pre_component_plane_database(path)

    run_migrations(path, clock=migration_clock)

    manifest = SQLiteRunRepository(path).get_run(RUN_ID)
    assert manifest is not None
    assert manifest.schema_version == "run-manifest/v3"
    assert manifest.run_manifest_id != before["manifest_address"]

    stored_decisions = _column(path, "SELECT decision_id FROM decisions")
    assert stored_decisions != [before["decision_id"]]
    assert len(stored_decisions) == 1

    decision = read_versioned(
        DECISION_LEDGER_VERSIONS, _column(path, "SELECT payload FROM decisions")[0]
    )
    assert decision.run_manifest_id == manifest.run_manifest_id
    assert decision.decision_id == stored_decisions[0]

    assert _column(path, "SELECT decision_id FROM validation_results") == [decision.decision_id]
    assert _column(path, "SELECT decision_id FROM research_memory") == [decision.decision_id]
    assert _column(path, "SELECT validation_id FROM validation_results") != [
        before["validation_id"]
    ]
    assert _column(path, "SELECT report_id FROM research_reports") != [before["report_id"]]
    assert json.loads(_column(path, "SELECT payload FROM research_reports")[0])["decision_id"] == (
        decision.decision_id
    )


def test_the_rewrite_is_idempotent_and_a_second_run_changes_nothing(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """A migration recorded as applied never runs again -- but this one must also be a no-op
    if it did, because its input and its output are both `run-manifest/v3` payloads once it has
    run once. Asserted by re-applying it to an already-migrated database directly."""
    path = tmp_path / "state.sqlite3"
    _build_pre_component_plane_database(path)
    run_migrations(path, clock=migration_clock)

    snapshot = (
        _column(path, "SELECT payload FROM runs"),
        _column(path, "SELECT decision_id FROM decisions"),
        _column(path, "SELECT validation_id FROM validation_results"),
        _column(path, "SELECT report_id FROM research_reports"),
    )
    from openalpha_cn.storage.migrations import _rewrite_manifest_component_planes

    with closing(sqlite3.connect(path)) as connection, connection:
        _rewrite_manifest_component_planes(connection)

    assert (
        _column(path, "SELECT payload FROM runs"),
        _column(path, "SELECT decision_id FROM decisions"),
        _column(path, "SELECT validation_id FROM validation_results"),
        _column(path, "SELECT report_id FROM research_reports"),
    ) == snapshot


def test_a_decision_left_pointing_at_an_address_no_run_produces_refuses_the_whole_migration(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The audit `_audit_identity_rewrite` never had, exercised against the state it guards.

    A stale `run_manifest_id` is a well-formed string that satisfies `RUN_MANIFEST_ID_PATTERN`
    and resolves to nothing -- there is no foreign key -- so every query keeps returning rows
    and no exception is raised anywhere. The audit is driven directly here because the passes
    above it are supposed to make this state unreachable: a test that could only produce it by
    breaking one of those passes would be testing the pass, not the audit.
    """
    path = tmp_path / "state.sqlite3"
    _build_pre_component_plane_database(path)
    run_migrations(path, clock=migration_clock)

    stale = _v2_decision("run_" + "0" * 24)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DELETE FROM decisions")
        connection.execute(
            "INSERT INTO decisions (decision_id, run_id, payload) VALUES (?, ?, ?)",
            (stale.decision_id, RUN_ID, stale.model_dump_json(exclude_computed_fields=True)),
        )

    with (
        closing(sqlite3.connect(path)) as connection,
        pytest.raises(StrandedManifestReferenceError, match="no stored run produces"),
    ):
        _audit_manifest_references_resolve(connection)


def test_a_decision_whose_run_row_is_missing_rolls_the_migration_back(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The other stranding, and the one the migration cannot repair rather than merely detect.

    `run_manifest_id` is not derivable from a decision row -- it lives in `runs` -- so a
    decision whose run has been deleted cannot be re-pointed at all. Refusing inside the
    transaction leaves the database exactly as it was, which is asserted rather than assumed:
    the alternative failure mode is a half-re-keyed ledger, and `MigrationFailedError` naming a
    backup is the contract `run_migrations` offers for it.
    """
    path = tmp_path / "state.sqlite3"
    before = _build_pre_component_plane_database(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DELETE FROM runs")

    with pytest.raises(MigrationFailedError) as exc_info:
        run_migrations(path, clock=migration_clock)

    assert exc_info.value.version == 8
    assert _column(path, "SELECT decision_id FROM decisions") == [before["decision_id"]]
    assert read_status(path).current_version == SPLIT_BATCH_TASK_ITEMS_VERSION


def test_the_migrated_manifest_states_the_planes_it_gained_rather_than_inventing_them(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """What a migrated v2 row is allowed to claim about itself, which is nothing extra.

    The agent id that sat in `model_versions` is **left where it was found**. A migration that
    moved it into `agent_versions` would be guessing at a fact the stored row does not carry --
    whether that agent was deterministic, learned or LLM-backed -- and `"baseline/v1"` is
    exactly the kind of confident wrong answer this issue exists to remove. The empty planes
    are the honest reading of a row written before they existed; runs executed after the
    upgrade populate them from the agents' own declarations.
    """
    path = tmp_path / "state.sqlite3"
    _build_pre_component_plane_database(path)

    run_migrations(path, clock=migration_clock)

    manifest = read_versioned(RUN_MANIFEST_VERSIONS, _column(path, "SELECT payload FROM runs")[0])

    assert manifest.agent_versions == ()
    assert manifest.alpha_model_versions == ()
    assert [item.model_dump() for item in manifest.model_versions] == [
        {"component": "market-agent", "version": "baseline/v1"}
    ]
