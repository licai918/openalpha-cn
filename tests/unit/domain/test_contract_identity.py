"""The content addresses `V2-P4-001`/`V2-P4-025` move, pinned in both directions and audited.

The roadmap's warning for this issue was that the repository had **no golden ID assertion
anywhere**, so a contract change could move an identity -- or fail to -- and nothing would
notice either way. Three shapes are used here, and they catch three different failures:

- **Golden pins.** Exact ID strings for one fixed fixture per contract. These catch *drift*:
  an unrelated later change that moves an address nobody meant to move. Nothing else can, and
  a two-direction table cannot, because a table compares two ids to each other rather than to
  history.
- **Two directions, per field.** Every field that must move an address is varied alone and
  measured; every field that must *not* is varied alone and measured. This is the shape
  `V2-P3-002`/`014`/`015` each used for factor identity.
- **A meta-audit over `model_fields`.** The two tables above are hand-written, and a
  hand-written table stops covering what it claims the moment somebody adds a field --
  `V2-P3-002` measured that happening. So the tables are read back against the model's own
  declared fields, and field *n+1* fails until it is either measured or named with a reason.

One hazard is specific to identity tests and this module is arranged against it: an
assertion that two ids differ proves nothing about *which* field caused it if the fixture
gives two fields the same value. Every variation below is distinct from the base fixture and
from every other variation, and `test_no_two_field_variations_produce_the_same_address`
asserts the whole set of produced addresses is pairwise distinct rather than merely
different from the base.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

import openalpha_cn
from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN, stable_model_id
from openalpha_cn.domain.decision import AgentDecision, DecisionLedger, DecisionLedgerV1
from openalpha_cn.domain.run import (
    RUN_MANIFEST_UNADDRESSED_FIELDS,
    RUN_MANIFEST_VERSIONS,
    AgentVersion,
    AlphaModelRef,
    ArtifactDigest,
    CheckpointRecord,
    RunManifest,
    RunManifestV1,
    RunManifestV2,
    VersionRef,
)
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.domain.validation import (
    AttributionTerm,
    AttributionTermV1,
    ValidationResult,
    ValidationResultV1,
)
from openalpha_cn.domain.versioning import IdentityRewriteRequiredError, read_versioned

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64
OTHER_DIGEST: Final[str] = "b" * 64
ADDRESS: Final[str] = "run_" + "0" * 24
ARTIFACT: Final[str] = "mdl_" + "0" * 24
OTHER_ARTIFACT: Final[str] = "mdl_" + "1" * 24


# --- the signal frame, which deliberately did NOT move ---------------------------------

GOLDEN_SIGNAL_IDS: Final[dict[str, str]] = {
    "5d": "sig_56c99d03db9841eb6da3fa18",
    "10d": "sig_ce51fb2fc77c9953f8560797",
}
"""`signal_id` for the fixture below, measured on commit `d703905` -- *before* `V2-P4-001`.

Not recomputed from this build and pasted back, which would assert nothing: the two values
were produced by checking the pre-change tree out into a scratch directory and running it, so
they are the historical answer rather than today's. That is what makes
`test_narrowing_the_signal_horizon_moved_no_stored_signal_id` a measurement.
"""


def _signal(**overrides: Any) -> SignalFrame:
    fields: dict[str, Any] = {
        "subject": "000001.SZ",
        "as_of": NOW,
        "direction": "bullish",
        "strength": 0.4,
        "confidence": 0.6,
        "horizon": "5d",
        "evidence_ids": ("ev_golden",),
    }
    return SignalFrame(**{**fields, **overrides})


@pytest.mark.parametrize("horizon", sorted(GOLDEN_SIGNAL_IDS))
def test_narrowing_the_signal_horizon_moved_no_stored_signal_id(horizon: str) -> None:
    """Roadmap section 8's rule, applied rather than re-litigated.

    `V2-P4-001` narrows `SignalFrame.horizon` from four units to one and does **not** bump
    `signal-frame`'s `schema_version`, because a narrowing leaves every still-legal value
    serialising to the bytes it always did. Section 8 measured exactly that for `V2-P1-017`;
    this pins it for the second narrowing, against IDs taken from the pre-change tree.

    The stakes are not merely tidiness. The aggregate `SignalFrame` a run produces is never
    persisted -- only its ID is, inside `decisions.signal_ids` -- so a `signal-frame` bump
    would move an identity no migration could recompute from the database. Keeping these two
    values still is what makes `V2-P4-001`'s rewrite migration complete rather than
    approximately complete.
    """
    assert _signal(horizon=horizon).signal_id == GOLDEN_SIGNAL_IDS[horizon]


def test_the_aggregate_signal_frame_is_referenced_but_never_stored() -> None:
    """The measurement the paragraph above rests on, rather than a claim about the codebase.

    `ResearchEngine._persist_idempotently` writes a manifest and a decision and nothing else,
    and `DecisionLedger` carries `signal_ids` -- strings -- rather than frames. So the set of
    stored fields that could hold a whole signal is empty, and any future `signal-frame` bump
    has to answer for that before it can be migrated.
    """
    stored_by_the_ledger = set(DecisionLedger.model_fields)

    assert "signal_ids" in stored_by_the_ledger
    assert not any(
        field.annotation is SignalFrame for field in DecisionLedger.model_fields.values()
    )
    assert not any(field.annotation is SignalFrame for field in RunManifest.model_fields.values())


def test_the_default_excludes_nothing_so_no_existing_identity_moved() -> None:
    """`stable_model_id` gained an `exclude` parameter; every prior caller passes nothing.

    Asserted three ways because "the default is empty" is the sort of claim that is true of
    the source and false of the behaviour: the two call forms are compared directly, the
    result is compared to the model's own computed field, and the whole thing is anchored to
    a pre-change golden above. Fourteen call sites depend on this, twenty-one `factor_id`s
    among them.
    """
    signal = _signal()

    assert stable_model_id(prefix="sig", model=signal) == signal.signal_id
    assert stable_model_id(prefix="sig", model=signal, exclude=frozenset()) == signal.signal_id
    assert signal.signal_id == GOLDEN_SIGNAL_IDS["5d"]


# --- the run manifest, which gained an address (V2-P4-025) -----------------------------


def _manifest(**overrides: Any) -> RunManifest:
    fields: dict[str, Any] = {
        "run_id": "run_golden",
        "mode": RunMode.live,
        "as_of": NOW,
        "code_commit": "0123456789abcdef",
        "config_digest": DIGEST,
        "provider_payload_digests": (ArtifactDigest(name="tushare.daily", sha256=DIGEST),),
        "agent_versions": (AgentVersion(agent_id="market-agent", kind="deterministic"),),
        "model_versions": (VersionRef(component="baseline", version="1.0.0"),),
        "prompt_versions": (),
        "alpha_model_versions": (AlphaModelRef(name="lgbm-baseline", artifact_id=ARTIFACT),),
        "random_seed": 7,
        "environment": (VersionRef(component="python", version="3.11.14"),),
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=1),
        "status": "succeeded",
        "checkpoints": (),
    }
    return RunManifest(**{**fields, **overrides})


GOLDEN_RUN_MANIFEST_ID: Final[str] = "run_b046b7e50079ee325dee4929"
"""`_manifest().run_manifest_id` at `run-manifest/v3`."""

SUPERSEDED_RUN_MANIFEST_ID: Final[str] = "run_bce5768e42bac31236638c6d"
"""The same fixture's address at `run-manifest/v2`, measured on `d234e4b`.

Kept rather than deleted because "this contract change moved every stored run address" is the
premise `storage/migrations.py`'s version 8 rests on, and a premise nothing re-measures is a
premise that quietly stops being true. See
`test_the_component_planes_moved_the_addresses_the_migration_has_to_rewrite`.
"""

_ADDRESSED_MANIFEST_VARIATIONS: Final[tuple[tuple[str, Any], ...]] = (
    ("run_id", "run_other"),
    ("mode", RunMode.paper),
    ("as_of", NOW + timedelta(days=1)),
    ("code_commit", "fedcba9876543210"),
    ("config_digest", OTHER_DIGEST),
    ("provider_payload_digests", (ArtifactDigest(name="tushare.daily", sha256=OTHER_DIGEST),)),
    ("agent_versions", (AgentVersion(agent_id="market-agent", kind="llm_backed"),)),
    ("model_versions", (VersionRef(component="baseline", version="2.0.0"),)),
    ("prompt_versions", (VersionRef(component="committee", version="1.0.0"),)),
    ("alpha_model_versions", (AlphaModelRef(name="lgbm-baseline", artifact_id=OTHER_ARTIFACT),)),
    ("random_seed", 99999),
)
"""`V2-P4-010`'s three additions are varied by their *discriminating* field, not by any field.

`agent_versions` varies the `kind` while holding `agent_id` fixed, and
`alpha_model_versions` varies the `artifact_id` while holding `name` fixed. Varying the id or
the name instead would pass against a `RunManifest` that hashed only the half a human typed --
which is exactly the state `model_versions` was in before this issue, where every entry paired
a real `agent_id` with the constant `"baseline/v1"` and the constant reached nothing.
"""

_UNADDRESSED_MANIFEST_VARIATIONS: Final[tuple[tuple[str, Any], ...]] = (
    ("environment", (VersionRef(component="python", version="3.12.9"),)),
    ("started_at", NOW - timedelta(hours=3)),
    ("finished_at", NOW + timedelta(hours=9)),
    ("status", "interrupted"),
    (
        "checkpoints",
        (CheckpointRecord(name="risk-gate", recorded_at=NOW, state_digest=DIGEST),),
    ),
)


def test_the_run_manifest_address_is_stable_for_a_fixed_declaration() -> None:
    assert _manifest().run_manifest_id == GOLDEN_RUN_MANIFEST_ID
    assert _manifest().run_manifest_id == _manifest().run_manifest_id


@pytest.mark.parametrize(("field", "value"), _ADDRESSED_MANIFEST_VARIATIONS)
def test_every_addressed_run_manifest_field_moves_the_run_level_id(
    field: str, value: object
) -> None:
    """Direction one: a declared input that changed must change the address."""
    assert _manifest(**{field: value}).run_manifest_id != GOLDEN_RUN_MANIFEST_ID


@pytest.mark.parametrize(("field", "value"), _UNADDRESSED_MANIFEST_VARIATIONS)
def test_no_unaddressed_run_manifest_field_moves_the_run_level_id(
    field: str, value: object
) -> None:
    """Direction two, and the direction this repository has got wrong before.

    `V2-P3-002` shipped a `manifest_id` that moved on a byte-identical re-fetch because
    `fetched_at` was inside a digest it hashed, and a stored build may not be dropped -- so the
    recomputed build could never be written. Every field varied here is a wall clock, a
    lifecycle state, or an observed host fact, and the started/finished pair is varied in
    *both* temporal directions so a comparison that happened to be one-sided would show.
    """
    assert _manifest(**{field: value}).run_manifest_id == GOLDEN_RUN_MANIFEST_ID


def test_a_rerun_at_a_different_wall_clock_reproduces_the_same_address() -> None:
    """The `V2-P3-014` arrangement, stated as one assertion rather than five.

    `FactorBuildManifest` keeps `built_at` out of its payload so that recomputing the same
    build from the same inputs reproduces its identity; the same property has to hold here or
    "did this run already happen" is unanswerable. Asserted as an absence from the hashed
    payload too, with the key set compared **exactly**, so the absence cannot be satisfied by
    a clock having quietly moved somewhere else inside it.
    """
    later = _manifest(
        started_at=NOW + timedelta(days=30),
        finished_at=NOW + timedelta(days=30, seconds=4),
    )

    assert later.run_manifest_id == GOLDEN_RUN_MANIFEST_ID

    hashed = _manifest().model_dump(
        mode="json",
        exclude_computed_fields=True,
        exclude=set(RUN_MANIFEST_UNADDRESSED_FIELDS),
    )
    assert set(hashed) == {
        "schema_version",
        "run_id",
        "mode",
        "as_of",
        "code_commit",
        "config_digest",
        "provider_payload_digests",
        "agent_versions",
        "model_versions",
        "prompt_versions",
        "alpha_model_versions",
        "random_seed",
    }


def test_the_component_planes_moved_the_addresses_the_migration_has_to_rewrite() -> None:
    """`V2-P4-010`'s cost, measured, because the migration it pays for is justified by it.

    Both directions of the same fact. The manifest address moved because `RunManifest` gained
    two fields; the *decision* identity moved even though `decision-ledger` was not bumped,
    because `run_manifest_id` is one of its fields. The second is the one worth pinning: it is
    the reason `storage/migrations.py`'s version 8 has to re-key `decisions` and cascade through
    `validation_results`, `research_reports`, `research_memory` and `batch_tasks`, and reading
    the diff of this issue would not tell you it was necessary.

    Asserted against values taken from `d234e4b` rather than recomputed from this build, so
    they are the historical answer rather than today's restated. `validation_id` is included
    for the same reason `decision_id` is: `ValidationResult` was not bumped either.
    """
    assert GOLDEN_RUN_MANIFEST_ID != SUPERSEDED_RUN_MANIFEST_ID
    assert GOLDEN_DECISION_ID != SUPERSEDED_DECISION_ID
    assert GOLDEN_VALIDATION_ID != SUPERSEDED_VALIDATION_ID
    assert _manifest().run_manifest_id == GOLDEN_RUN_MANIFEST_ID
    assert _decision().decision_id == GOLDEN_DECISION_ID
    assert _validation().validation_id == GOLDEN_VALIDATION_ID


def test_a_v2_manifest_refuses_to_advance_itself_on_read() -> None:
    """The measurement behind `refuse_run_manifest_v2_upgrade`, not a restatement of it.

    `upgrade_run_manifest_v1` is allowed precisely because no stored row referenced a manifest
    address at v1 -- `DecisionLedgerV1` has no `run_manifest_id`, asserted here rather than
    recalled -- and that stopped being true at `V2-P4-025`. So the v1 hop still upgrades on
    read and the v2 hop refuses, and the two are asserted side by side because the difference
    between them is the whole argument.
    """
    dumped = _manifest().model_dump(mode="python", exclude_computed_fields=True)
    v2_fields = {
        key: value
        for key, value in dumped.items()
        if key not in {"schema_version", "agent_versions", "alpha_model_versions"}
    }
    stored_v2 = RunManifestV2.model_validate(v2_fields)

    assert "run_manifest_id" not in DecisionLedgerV1.model_fields
    assert "run_manifest_id" in DecisionLedger.model_fields

    with pytest.raises(IdentityRewriteRequiredError, match="run-manifest"):
        read_versioned(RUN_MANIFEST_VERSIONS, stored_v2.model_dump_json())

    v1_fields = {
        key: value for key, value in v2_fields.items() if key not in {"schema_version"}
    } | {"mode": "live"}
    stored_v1 = RunManifestV1.model_validate(v1_fields)
    upgraded = read_versioned(RUN_MANIFEST_VERSIONS, stored_v1.model_dump_json())

    assert upgraded.schema_version == "run-manifest/v3"


def test_every_run_manifest_field_is_addressed_or_excluded_by_name() -> None:
    """The meta-audit: a field added to `RunManifest` fails until it is decided about.

    `schema_version` is the single exemption and it is a one-member `Literal` -- there is no
    other value to vary it to -- so it is covered by
    `test_the_schema_version_bump_is_what_moved_the_two_re_keyed_identities` asserting it is
    inside the hashed payload instead. `RunManifest` gained an address at `V2-P4-025` and
    `V2-P4-002` is already scheduled to add a column beside it; this is what stops the next
    field from silently joining the addressed set or silently avoiding it.
    """
    addressed = {name for name, _ in _ADDRESSED_MANIFEST_VARIATIONS}
    unaddressed = {name for name, _ in _UNADDRESSED_MANIFEST_VARIATIONS}

    assert addressed & unaddressed == set()
    assert addressed | unaddressed == set(RunManifest.model_fields) - {"schema_version"}
    assert unaddressed == set(RUN_MANIFEST_UNADDRESSED_FIELDS)


def test_every_exclusion_states_a_reason_rather_than_being_a_bare_name() -> None:
    """An exclusion with no reason is indistinguishable from an oversight.

    The length floor is crude and deliberate: it is not trying to grade the prose, only to
    stop `"": ""` and `"clock"` from satisfying a mapping whose entire purpose is to record
    *why* a field was kept out of an identity.
    """
    for field, reason in RUN_MANIFEST_UNADDRESSED_FIELDS.items():
        assert field in RunManifest.model_fields
        assert len(reason) > 60, field


def test_no_two_field_variations_produce_the_same_address() -> None:
    """Guards the hazard this repository has hit more than ten times in P3.

    An assertion that "changing X moves the ID" is satisfied by a fixture in which X and Y
    happen to produce the same new ID, and then the suite cannot tell which field did the
    work. Comparing the whole set for pairwise distinctness is what closes that.
    """
    addresses = [GOLDEN_RUN_MANIFEST_ID] + [
        _manifest(**{field: value}).run_manifest_id
        for field, value in _ADDRESSED_MANIFEST_VARIATIONS
    ]

    assert len(set(addresses)) == len(addresses)


def test_changing_config_digest_or_random_seed_alone_moves_the_run_level_id() -> None:
    """Roadmap section 9's experiment, at the contract level, with the answers inverted.

    Section 9 drove a real `run_cycle` and found both of these fields failing to reach any
    content-addressed identity, because neither is a field of a model `stable_model_id` was
    applied to. `V2-P4-025`'s acceptance is this assertion; the end-to-end half, through a
    real `run_cycle`, is
    `tests/integration/test_run_identity.py::test_changing_config_digest_alone_moves_the_run_level_id_and_the_decision_id`.
    """
    assert _manifest(config_digest=OTHER_DIGEST).run_manifest_id != GOLDEN_RUN_MANIFEST_ID
    assert _manifest(random_seed=99999).run_manifest_id != GOLDEN_RUN_MANIFEST_ID
    assert (
        _manifest(config_digest=OTHER_DIGEST).run_manifest_id
        != _manifest(random_seed=99999).run_manifest_id
    )


# --- the decision ledger, which gained a reference to that address ---------------------


def _decision(**overrides: Any) -> DecisionLedger:
    fields: dict[str, Any] = {
        "run_id": "run_golden",
        "run_manifest_id": GOLDEN_RUN_MANIFEST_ID,
        "created_at": NOW,
        "agent_outputs": (
            AgentDecision(
                agent_id="market-agent",
                signal_id="sig_golden",
                recommendation="support",
                rationale="Price and volume confirm the event.",
            ),
        ),
        "routing_path": ("market-agent", "risk-gate"),
        "risk_decision": "pass",
        "final_action": "watch",
        "evidence_ids": ("ev_golden",),
        "signal_ids": ("sig_golden",),
        "code_commit": "0123456789abcdef",
        "model_versions": (VersionRef(component="baseline", version="1.0.0"),),
        "prompt_versions": (),
    }
    return DecisionLedger(**{**fields, **overrides})


GOLDEN_DECISION_ID: Final[str] = "dec_26ea2f0a3b85c327029c76ff"
"""`_decision().decision_id` at `decision-ledger/v2`, against a `run-manifest/v3` address."""

SUPERSEDED_DECISION_ID: Final[str] = "dec_6d621fd9a25506cec565420f"
"""The same fixture's identity on `d234e4b`, when the manifest it names was at v2.

`decision-ledger` is **not** bumped by `V2-P4-010` and this value moved anyway, which is the
whole reason the issue owes a migration: `run_manifest_id` is a field of `DecisionLedger`, so a
manifest gaining a field re-keys every stored decision without the decision contract changing
at all. `V2-P4-025`'s docstring predicted exactly this -- "including inputs `RunManifest` gains
later" -- and these two constants are that sentence measured.
"""


def test_the_decision_identity_is_stable_and_moves_with_the_manifest_it_names() -> None:
    """PRD section 1.3 B6, which roadmap section 9 recorded as still true after P0.B.

    "Different configurations produce the same decision ID" is false from here on, and it is
    false through one field rather than through copies of `config_digest` and `random_seed`:
    the ledger names the manifest's address, so every declared run input reaches `decision_id`
    at once -- including inputs `RunManifest` gains later.
    """
    assert _decision().decision_id == GOLDEN_DECISION_ID
    assert _decision(run_manifest_id="run_" + "1" * 24).decision_id != GOLDEN_DECISION_ID


def test_the_decision_identity_refuses_a_run_manifest_id_that_is_not_one() -> None:
    """A content address that is only conventionally a content address is not one.

    Without the pattern, `run_manifest_id="unknown"` would validate and produce a perfectly
    stable `decision_id` that answered for nothing -- the exact failure mode section 9
    describes, reintroduced one level up.
    """
    with pytest.raises(ValueError, match="String should match pattern"):
        _decision(run_manifest_id="unknown")


# --- the validation result, which gained a category and an explicit residual -----------


def _validation(**overrides: Any) -> ValidationResult:
    fields: dict[str, Any] = {
        "signal_id": "sig_golden",
        "decision_id": GOLDEN_DECISION_ID,
        "observation_start": NOW,
        "observation_end": NOW + timedelta(days=5),
        "realized_return": 0.10,
        "benchmark_return": 0.02,
        "transaction_cost": 0.005,
        "attribution": (
            AttributionTerm(category="rule", name="decision-policy", contribution=0.025),
            AttributionTerm(category="factor", name="momentum", contribution=0.03),
            AttributionTerm(category="agent", name="market-agent", contribution=0.02),
        ),
        "unexplained_return": 0.0,
        "confidence": 0.8,
        "data_quality_notes": (),
    }
    return ValidationResult(**{**fields, **overrides})


GOLDEN_VALIDATION_ID: Final[str] = "val_67f3514eb2107b79bebab445"
"""`_validation().validation_id` at `validation-result/v2`, against the decision above."""

SUPERSEDED_VALIDATION_ID: Final[str] = "val_f898bce11540c6fb3b08459c"
"""The same fixture's identity on `d234e4b`. The third link of the same chain.

`validation-result` is not bumped by `V2-P4-010` either, and moved anyway, because
`ValidationResult.decision_id` names a decision that a manifest field addition re-keyed. Three
contracts, one of them changed, three identities moved -- which is the shape of the cascade
`storage/migrations.py` has to reproduce on disk, and the reason it cannot stop at `runs`.
"""


def test_the_validation_identity_is_stable_for_a_fixed_result() -> None:
    assert _validation().validation_id == GOLDEN_VALIDATION_ID


def test_moving_a_contribution_into_the_explicit_residual_moves_the_identity() -> None:
    """The residual is a real field, not a presentation detail, and the address says so.

    The two results compared here reconcile to the *same* `net_active_return` -- they differ
    only in whether the last 0.02 is claimed by an agent or admitted as unexplained. That is
    exactly the substitution `backtest/validation.py`'s last-term-absorbs trick makes
    invisible today (`V2-P5-005`), and a fixture with `unexplained_return=0.0` on both sides
    would not have been able to tell the two apart at all.
    """
    with_residual = _validation(
        attribution=(
            AttributionTerm(category="rule", name="decision-policy", contribution=0.025),
            AttributionTerm(category="factor", name="momentum", contribution=0.03),
        ),
        unexplained_return=0.02,
    )

    assert with_residual.net_active_return == _validation().net_active_return
    assert with_residual.validation_id != GOLDEN_VALIDATION_ID


def test_the_model_category_is_a_distinct_answer_from_the_agent_category() -> None:
    """`model` is not a spelling of `agent`; a report that conflated them would be wrong.

    Same name, same contribution, same everything else -- only the category differs -- so this
    cannot pass by accident through some other field having moved.
    """
    as_agent = _validation(
        attribution=(
            AttributionTerm(category="rule", name="decision-policy", contribution=0.025),
            AttributionTerm(category="factor", name="momentum", contribution=0.03),
            AttributionTerm(category="agent", name="lgbm-baseline", contribution=0.02),
        )
    )
    as_model = _validation(
        attribution=(
            AttributionTerm(category="rule", name="decision-policy", contribution=0.025),
            AttributionTerm(category="factor", name="momentum", contribution=0.03),
            AttributionTerm(category="model", name="lgbm-baseline", contribution=0.02),
        )
    )

    assert as_agent.validation_id != as_model.validation_id


def test_an_unreconciled_attribution_is_still_refused_now_that_a_residual_exists() -> None:
    """Adding a free variable to a sum must not turn the check into a formality.

    The residual makes the constraint *satisfiable* for any term set, which is the point --
    but only when the caller states the leftover. A caller who states the wrong one, or none,
    still fails.
    """
    with pytest.raises(ValueError, match="attribution does not reconcile"):
        _validation(unexplained_return=0.01)

    with pytest.raises(ValueError, match="attribution does not reconcile"):
        _validation(
            attribution=(
                AttributionTerm(category="model", name="lgbm-baseline", contribution=0.01),
            )
        )


# --- section 8's table, re-measured on the contracts this issue bumped -----------------


def test_the_schema_version_bump_is_what_moved_the_two_re_keyed_identities() -> None:
    """Roadmap section 8's measurement, redone for `V2-P4-001`'s own bumps.

    Section 8 established that `schema_version` is a real field rather than a
    `computed_field`, and is therefore hashed -- which is why a version bump moves a
    content-addressed identity, and why `storage/migrations.py` owes a rewrite rather than a
    read-time upcast. Measured here by hashing each frozen v1 snapshot with the same prefix
    its v2 uses: same data, different version, different address.

    It also covers `schema_version` for `test_every_run_manifest_field_is_addressed_or_
    excluded_by_name`'s single exemption -- a one-member `Literal` cannot be varied, so its
    reaching the identity is asserted as membership of the hashed payload.
    """
    assert "schema_version" in _manifest().model_dump(
        mode="json",
        exclude_computed_fields=True,
        exclude=set(RUN_MANIFEST_UNADDRESSED_FIELDS),
    )

    v1_decision = DecisionLedgerV1.model_validate(
        {
            key: value
            for key, value in _decision()
            .model_dump(mode="python", exclude_computed_fields=True)
            .items()
            if key not in {"schema_version", "run_manifest_id"}
        }
    )
    assert stable_model_id(prefix="dec", model=v1_decision) != GOLDEN_DECISION_ID

    v1_validation = ValidationResultV1.model_validate(
        {
            key: value
            for key, value in _validation()
            .model_dump(mode="python", exclude_computed_fields=True)
            .items()
            if key not in {"schema_version", "unexplained_return"}
        }
    )
    assert stable_model_id(prefix="val", model=v1_validation) != GOLDEN_VALIDATION_ID
    assert isinstance(v1_validation.attribution[0], AttributionTermV1)


def test_the_frozen_v1_snapshots_still_refuse_what_v1_refused() -> None:
    """A snapshot that accepts more than its version did repairs data while re-versioning it.

    `model` is `V2-P4-001`'s addition to the attribution categories; a v1 payload could never
    have contained one, so `AttributionTermV1` must not accept it -- otherwise the migration
    would silently bless a row no v1 build could have written.
    """
    with pytest.raises(ValueError, match="model"):
        AttributionTermV1(category="model", name="lgbm-baseline", contribution=0.02)

    with pytest.raises(ValueError, match="unexplained_return"):
        ValidationResultV1.model_validate(
            {
                **_validation().model_dump(mode="python", exclude_computed_fields=True),
                "schema_version": "validation-result/v1",
            }
        )

    with pytest.raises(ValueError, match="run_manifest_id"):
        RunManifestV1.model_validate(
            {
                **_manifest().model_dump(mode="python", exclude_computed_fields=True),
                "run_manifest_id": ADDRESS,
            }
        )


# --- the one canonicalisation, audited rather than claimed (V2-P4-037) -----------------
#
# `domain/_identity.py` says a second canonicalisation "would have put two canonicalisations in
# play where a difference between them would be invisible until two IDs disagreed". Until
# `V2-P4-037` **nothing enforced it**, and the cost was measured twice on `146698c`:
#
#   - Rewriting `ShortlistGateManifest.gate_manifest_id` as its own `json.dumps(..., sort_keys=
#     False, separators=(", ", ": "))` plus `sha256[:24]` moved that declaration's address from
#     `sgt_6c3ec68a648da428cffaa992` to `sgt_3248f195a1022718a0a1b2a2`.
#   - Minting a **new** address beside the existing one -- a second computed field on the same
#     model, `sgs_<24 hex>`, with the same bespoke spelling -- left ruff, `uv run mypy`
#     (140 files), `lint-imports` (8 kept, 0 broken) and `tests/unit` (2813 passed) all green.
#
# The first probe went red by accident and the accident is worth recording, because it is not a
# guard: `test_manifest_component_provenance.py::live_prefixes` counts `stable_model_id` and
# `cross_section_digest` **call sites**, so *replacing* one drops the census from 27 to 26.
# `test_a_quantitative_model_reference_must_be_something_the_one_hash_function_produced` then
# fails on arithmetic, saying nothing about canonicalisation -- and the second probe, which adds
# a mint without removing a call, moves that census not at all. A count of who calls the one
# function cannot answer who else is minting.
#
# So the audit below is over the **mint**: every place under `src/` that truncates a digest to
# a content address's width, keyed by the function it sits in and checked in both directions.
# It is the shape `tests/unit/product/test_governed_screening.py::
# test_no_shipped_risk_flag_is_written_in_executable_code_under_product` uses one plane over.
#
# What it cannot see is stated rather than left to be found: it reads the literal `24` written
# at the slice, so a mint spelled `hexdigest()[:_WIDTH]` against a module constant would pass.
# Every one of the eight sites writes the literal, a copy of one of them will too, and widening
# to "every `sha256` call in `src/`" would mix content addresses in with the seven plain
# 64-hex checksums (`payload_digest`, `config_digest`, `content_hash`, ...) that are a different
# question. This guards drift, not an adversary.

SOURCE_ROOT: Final[Path] = Path(openalpha_cn.__file__).resolve().parent

_DIGITS = re.fullmatch(r".*\{(\d+)\}\$", CONTENT_ADDRESS_PATTERN)
assert _DIGITS is not None, CONTENT_ADDRESS_PATTERN
CONTENT_ADDRESS_DIGITS: Final[int] = int(_DIGITS.group(1))

CANONICAL_JSON_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"ensure_ascii=False", "separators=(',', ':')", "allow_nan=False"}
)

OPTIONAL_JSON_KEYWORDS: Final[frozenset[str]] = frozenset({"sort_keys=True"})

DECLARED_CONTENT_ADDRESS_MINTS: Final[dict[str, str]] = {
    "domain/_identity.py::stable_model_id": (
        "the one canonicalisation: every identity derived from a pydantic model is this one"
    ),
    "domain/factor.py::set_digest": (
        "`set_`: an unordered set of subject codes, which is not a model and has no fields"
    ),
    "domain/factor.py::cross_section_digest": (
        "`obs`/`prc`/`nrs`/`xs`: one cross section of (subject, coverage, value) triples"
    ),
    "domain/factor_neutralization.py::characteristic_digest": (
        "`chr_`: one industry/market-cap cross section, reachable from two planes that may "
        "not import each other"
    ),
    "backtest/candidate_ranking.py::ranking_content_digest": (
        "`rkc_`: the researched candidates a ranking answered with, beside the declaration "
        "address `ranking_manifest_id` that `stable_model_id` mints"
    ),
    "shortlist_view.py::stable_answer_digest": (
        "`sla_`: one rendered shortlist answer, a mapping this face assembles and never a model"
    ),
    "domain/evidence.py::EvidenceSnapshot.evidence_id": (
        "`ev_`: provenance and content joined with `|`, so there is no JSON to canonicalise"
    ),
    "storage/parquet.py::ParquetEvidenceStore.append": (
        "not an address at all: `part-<24 hex>.parquet` is a file name, and no prefix "
        "`CONTENT_ADDRESS_PATTERN` admits can be spelled with the `-` it uses"
    ),
}

"""Every function under `src/` that may mint a content address, and what each one addresses.

Eight, and the roadmap row for this issue named five of them -- `stable_model_id` plus a
"genuine non-model content digests" list of `set_digest`, `rkc_`, `chr_` and `ev_`. Read off
the tree instead, `cross_section_digest`, `stable_answer_digest` and `ParquetEvidenceStore
.append` are there too, which is the same lesson `V2-P4-016` took from the hand-written prefix
list this module's sibling replaced: a list of who hashes, written from memory, is a list that
is already wrong.

Keyed by `<module>::<class.function>` rather than by module, so a **second** bespoke digest
added inside a file that already holds a legitimate one is red. `domain/factor.py` is why that
matters: it holds two, and a file-level allowlist would have admitted a third.

The seven prefixes these mint (`set`, `obs`/`prc`/`nrs`/`xs`, `chr`, `rkc`, `sla`, `ev`, and
whichever the caller hands `stable_model_id`) are a different census from the one in
`test_manifest_component_provenance.py::live_prefixes`, which reads **prefix arguments** off
call sites -- 27 of them carrying 24 distinct prefixes, none containing an underscore, as of
this issue. That one answers "is `mdl` taken"; this one answers "who is allowed to mint at
all", and neither implies the other.
"""

JOINED_STRING_MINTS: Final[frozenset[str]] = frozenset(
    {
        "domain/evidence.py::EvidenceSnapshot.evidence_id",
        "storage/parquet.py::ParquetEvidenceStore.append",
    }
)
"""The two mints that canonicalise without JSON, declared so the keyword audit cannot skip.

`evidence_id` joins five strings with `|` and `ParquetEvidenceStore.append` joins sorted
evidence ids the same way, so there is no `json.dumps` in either function and no keyword
spelling to compare. Declared as an exact set rather than tested for where a `json.dumps`
happens to exist, because "check the spelling where there is one" is satisfied by a new mint
that canonicalises some third way -- the direction `V2-P4-092` warns about, where an audit is
made wide by being made unfalsifiable.
"""


def _qualified_owners(tree: ast.Module) -> dict[int, str]:
    """`id(node)` -> the dotted class/function path it sits inside; `""` at module level."""
    owners: dict[int, str] = {}

    def walk(parent: ast.AST, path: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(parent):
            owners[id(child)] = ".".join(path)
            walk(
                child,
                (*path, child.name)
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                else path,
            )

    walk(tree, ())
    return owners


def _canonicalisations(tree: ast.Module, owners: dict[int, str]) -> dict[str, frozenset[str]]:
    """Every `json.dumps(...)` keyword spelling in one file, keyed by its enclosing function."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "dumps":
            continue
        found.setdefault(owners[id(node)], set()).update(
            f"{word.arg}={ast.unparse(word.value)}" for word in node.keywords
        )
    return {owner: frozenset(spelling) for owner, spelling in found.items()}


def content_address_mints() -> dict[str, frozenset[str] | None]:
    """Every place under `src/` that truncates a digest to a content address's width."""
    mints: dict[str, frozenset[str] | None] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owners = _qualified_owners(tree)
        canonicalisations = _canonicalisations(tree, owners)
        module = path.relative_to(SOURCE_ROOT).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
                continue
            upper = node.slice.upper
            if not (isinstance(upper, ast.Constant) and upper.value == CONTENT_ADDRESS_DIGITS):
                continue
            owner = owners[id(node)]
            mints[f"{module}::{owner}"] = canonicalisations.get(owner)
    return mints


def test_the_width_the_audit_looks_for_is_the_one_the_pattern_declares() -> None:
    """The extractor's own test: the audit hunts the width `CONTENT_ADDRESS_PATTERN` requires.

    Read out of the pattern rather than written as a second `24`, so a repository that widened
    its addresses would move the audit with them instead of leaving it hunting a width nothing
    produces any more -- which is an audit that passes by finding nothing, the failure shape
    every AST census in this repository is arranged against.
    """
    assert CONTENT_ADDRESS_DIGITS == 24
    assert re.fullmatch(CONTENT_ADDRESS_PATTERN, "mdl_" + "0" * CONTENT_ADDRESS_DIGITS)
    assert not re.fullmatch(CONTENT_ADDRESS_PATTERN, "mdl_" + "0" * (CONTENT_ADDRESS_DIGITS - 1))


def test_every_content_address_in_the_source_tree_is_minted_where_this_module_says() -> None:
    """`V2-P4-037`: the audit `_identity.py`'s "one canonicalisation" sentence never had.

    Equality rather than containment, so this is red in both directions: a mint that appears
    (the measured probe -- a second computed field spelling its own `sgs_<24 hex>` -- is caught
    here rather than by the prefix census, which it does not move at all), and a declared mint
    that is deleted or renamed without this table being told.
    """
    assert set(content_address_mints()) == set(DECLARED_CONTENT_ADDRESS_MINTS)
    assert set(DECLARED_CONTENT_ADDRESS_MINTS) >= JOINED_STRING_MINTS
    assert all(DECLARED_CONTENT_ADDRESS_MINTS.values()), (
        "a mint on the allowlist with no stated reason is a name somebody added to make a "
        "failure go away, which is the allowlist failure mode this shape has to survive"
    )


def test_every_declared_mint_spells_canonical_the_one_way() -> None:
    """The other half of the sentence: one canonicalisation, not merely one set of minters.

    Six of the eight canonicalise with `json.dumps`, and all six must spell it the way
    `stable_model_id` does -- `ensure_ascii=False`, `separators=(",", ":")`, `allow_nan=False`.
    That is the exact difference the measured probe introduced (`sort_keys=False` and
    `separators=(", ", ": ")`), and it is the difference their docstrings each promise not to
    have while nothing checked.

    `sort_keys=True` is optional rather than required, and the reason is a real one rather than
    a convenience: four of the six hash a **list** -- sorted subject codes, sorted rows,
    sorted candidate tuples -- where `sort_keys` cannot change a byte, and requiring it would
    mean editing `backtest/candidate_ranking.py` to satisfy an audit rather than to fix a
    defect. The two whose payload is a mapping (`stable_model_id`, `stable_answer_digest`) both
    pass it. `sort_keys=False` is refused by the second assertion, which is what keeps
    "optional" from meaning "unchecked".
    """
    mints = content_address_mints()

    assert {site for site, spelling in mints.items() if spelling is None} == JOINED_STRING_MINTS

    canonicalising = {site: words for site, words in mints.items() if words is not None}
    assert {
        site: sorted(CANONICAL_JSON_KEYWORDS - words) for site, words in canonicalising.items()
    } == {site: [] for site in canonicalising}
    assert {
        site: sorted(words - CANONICAL_JSON_KEYWORDS - OPTIONAL_JSON_KEYWORDS)
        for site, words in canonicalising.items()
    } == {site: [] for site in canonicalising}
