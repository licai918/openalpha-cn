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

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.decision import AgentDecision, DecisionLedger, DecisionLedgerV1
from openalpha_cn.domain.run import (
    RUN_MANIFEST_UNADDRESSED_FIELDS,
    ArtifactDigest,
    CheckpointRecord,
    RunManifest,
    RunManifestV1,
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

NOW: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
DIGEST: Final[str] = "a" * 64
OTHER_DIGEST: Final[str] = "b" * 64
ADDRESS: Final[str] = "run_" + "0" * 24


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
        "model_versions": (VersionRef(component="baseline", version="1.0.0"),),
        "prompt_versions": (),
        "random_seed": 7,
        "environment": (VersionRef(component="python", version="3.11.14"),),
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=1),
        "status": "succeeded",
        "checkpoints": (),
    }
    return RunManifest(**{**fields, **overrides})


GOLDEN_RUN_MANIFEST_ID: Final[str] = "run_bce5768e42bac31236638c6d"
"""`_manifest().run_manifest_id`. New at `V2-P4-025`, so there is no earlier value to compare."""

_ADDRESSED_MANIFEST_VARIATIONS: Final[tuple[tuple[str, Any], ...]] = (
    ("run_id", "run_other"),
    ("mode", RunMode.paper),
    ("as_of", NOW + timedelta(days=1)),
    ("code_commit", "fedcba9876543210"),
    ("config_digest", OTHER_DIGEST),
    ("provider_payload_digests", (ArtifactDigest(name="tushare.daily", sha256=OTHER_DIGEST),)),
    ("model_versions", (VersionRef(component="baseline", version="2.0.0"),)),
    ("prompt_versions", (VersionRef(component="committee", version="1.0.0"),)),
    ("random_seed", 99999),
)

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
        "model_versions",
        "prompt_versions",
        "random_seed",
    }


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


GOLDEN_DECISION_ID: Final[str] = "dec_6d621fd9a25506cec565420f"
"""`_decision().decision_id` at `decision-ledger/v2`."""


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


GOLDEN_VALIDATION_ID: Final[str] = "val_f898bce11540c6fb3b08459c"
"""`_validation().validation_id` at `validation-result/v2`."""


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
