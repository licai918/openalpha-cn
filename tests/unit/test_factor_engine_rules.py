"""The factor engine's rules that need no store (`V2-P3-002`).

Four groups, each closing a hole this repository has already fallen into once.

**The two tables must name the same factors.** `panel build`'s `PANEL_BUILD_TARGETS` gained
keys whose branches did not exist, and the command answered exit 0 with an empty partition list
-- a declared capability that reports success and does nothing. `FACTOR_DEFINITIONS` and
`FACTOR_EVALUATORS` are the same shape of pair, and `_refuse_table_drift` is the same shape of
audit, so it is exercised in both directions rather than trusted because it is called at import.

**The evaluator's own arithmetic, including the branch that never fires in production.**
`undefined_value` is one of five declared coverage codes, and a code that no branch can emit is
a table entry with nothing behind it. `_reversal_1d`'s zero-denominator guard is unreachable
through this repository's own writers (`daily_bars_from_panel_rows` refuses a non-positive
close), so it is driven directly here -- which is the only honest way to have both the guard and
the claim that it works.

**The reporting vocabularies must agree with the contract's.** `FACTOR_COVERAGE_ORDER` is a
second copy of `FACTOR_COVERAGE_CODES` that exists for a stable census key order, and two copies
of a closed set drift.

**Every shipped contract carries its prose.** The three factor identity contracts each had a
mandatory `summary` field until `V2-P3-014`'s prerequisite, which met that obligation and also
put prose inside three content addresses. The field is gone; `FactorNote` and
`test_every_shipped_contract_carries_its_prose` are what replaced it, and the obligation is
required of the *shipped* registries only -- `validate_notes` argues why an arbitrary one is
exempt.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest

from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    MAX_FACTOR_KEY_LENGTH,
    FactorBuildManifest,
    FactorDefinition,
    FactorField,
    FactorInputProvenance,
    FactorInputRef,
    FactorRegistry,
    set_digest,
)
from openalpha_cn.domain.panel_batch import MAX_IDENTIFIER_LENGTH, validate_panel_dataset
from openalpha_cn.panel_doctor import DATASET_CADENCE
from openalpha_cn.panel_factors import (
    FACTOR_CENSUS_COLUMNS,
    FACTOR_COVERAGE_ORDER,
    FACTOR_DEFINITIONS,
    FACTOR_EVALUATORS,
    FACTOR_MANIFEST_DATA_COLUMNS,
    FACTOR_MANIFEST_DATASET_PREFIX,
    FACTOR_MANIFEST_PANEL_COLUMNS,
    FACTOR_OBSERVATION_DATA_COLUMNS,
    FACTOR_OBSERVATION_DATASET_PREFIX,
    FACTOR_OBSERVATION_PANEL_COLUMNS,
    FACTOR_TRANSFORMS,
    REVERSAL_1D,
    FactorEngineError,
    FactorPanel,
    FactorWindow,
    _manifest_cells,
    _manifest_from_rows,
    _observation_from_row,
    _refuse_table_drift,
    _reversal_1d,
    factor_manifest_batch,
    factor_manifest_dataset,
    factor_observation_dataset,
)
from openalpha_cn.panel_neutralization import FACTOR_NEUTRALIZATIONS

AS_OF = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
OBSERVATIONS = factor_observation_dataset(REVERSAL_1D)
MANIFESTS = factor_manifest_dataset(REVERSAL_1D)
SOURCE_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "openalpha_cn"


def _window(*closes: float) -> FactorWindow:
    sessions = tuple(date(2026, 1, 5 + index) for index in range(len(closes)))
    return FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=sessions,
        periods=(),
        values=MappingProxyType({("daily", "close"): closes}),
    )


# --- the two tables ------------------------------------------------------------------------------


def test_the_shipped_registry_and_evaluator_table_name_exactly_the_same_factors() -> None:
    """The two tables agree, and the registry is exactly what this build has shipped so far.

    Written out rather than counted, so that a factor arriving from `V2-P3-009`..`013` has to be
    named here by whoever adds it. `reversal_1d` is the engine's verification factor and the four
    that follow are `V2-P3-012`'s momentum-and-reversal family; the family's own declarations are
    audited in `tests/unit/test_factor_momentum_reversal_rules.py`.
    """
    assert set(FACTOR_DEFINITIONS.qualified_keys) == set(FACTOR_EVALUATORS)
    assert FACTOR_DEFINITIONS.qualified_keys == (
        "reversal_1d/v1",
        "momentum_20_sessions/v1",
        "momentum_60_sessions/v1",
        "momentum_120_sessions/v1",
        "reversal_5_sessions/v1",
    )


def test_a_definition_with_no_evaluator_is_refused_rather_than_answered_emptily() -> None:
    """The `panel build` failure, in its factor-layer form: a declared factor a caller can ask
    for and nothing can compute would otherwise produce a full panel of observations all saying
    nothing was computable, which is an empty success with a coverage column on it."""
    orphan = FactorDefinition(
        key="declared_only",
        version=1,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=2,
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
    )

    with pytest.raises(FactorEngineError, match="declared with no evaluator"):
        _refuse_table_drift(FactorRegistry((orphan,)), FACTOR_EVALUATORS)


def test_an_evaluator_with_no_definition_is_refused_too() -> None:
    """The other direction, which fails differently: a formula with no declared identity,
    lookback or required fields is one nothing can hash, gate or read the sign of."""
    with pytest.raises(FactorEngineError, match="implemented with no definition"):
        _refuse_table_drift(
            FACTOR_DEFINITIONS,
            MappingProxyType({**FACTOR_EVALUATORS, "ghost/v1": _reversal_1d}),
        )


def test_the_drift_audit_passes_only_on_agreement() -> None:
    """The sentinel: if `_refuse_table_drift` raised unconditionally, both tests above would
    pass while proving nothing about agreement."""
    assert _refuse_table_drift(FACTOR_DEFINITIONS, FACTOR_EVALUATORS) is None


# --- the shipped definition ----------------------------------------------------------------------


def test_the_verification_factor_declares_the_properties_the_engine_reads() -> None:
    """It is the engine's own probe rather than a `V2-P3-012` deliverable, and each property is
    chosen for what it makes reachable: one required column so `input_missing` has exactly one
    way to happen, a lookback of 2 so `insufficient_history` is reachable at all (a 1-session
    window is satisfied by any security with one row), a denominator so `undefined_value` is a
    branch rather than a declaration, and a `max_window_sessions` equal to the lookback so the
    window-span refusal is reachable against the fixture panel's one halt.

    The equality is the point rather than a spare setting: a factor declared as one session's
    close-to-close simple return has no reading under which the two sessions may straddle a halt,
    and `V2-P3-012`'s 120-session momentum is where a wider tolerance belongs.

    **The absent report-period reach is a declared property too.** `FactorDefinition` requires
    each axis to be declared exactly when `required_fields` puts the factor on it, so
    `lookback_periods is None` here is the contract's statement that a close-to-close return
    reads no filing -- not an unset field. The prose is asserted on the registry's note rather
    than on the definition, because that is where it now lives and where a typo in it moves
    nothing.
    """
    assert REVERSAL_1D.qualified_key == "reversal_1d/v1"
    assert REVERSAL_1D.family == "momentum_reversal"
    assert REVERSAL_1D.direction == "lower_is_better"
    assert REVERSAL_1D.required_fields == (FactorField(dataset="daily", column="close"),)
    assert REVERSAL_1D.lookback_sessions == 2
    assert REVERSAL_1D.max_window_sessions == 2
    assert REVERSAL_1D.lookback_periods is None
    assert REVERSAL_1D.max_window_periods is None
    assert REVERSAL_1D.datasets == ("daily",)
    assert REVERSAL_1D.session_datasets == ("daily",)
    assert REVERSAL_1D.period_datasets == ()
    note = FACTOR_DEFINITIONS.note_for(REVERSAL_1D.qualified_key)
    assert note is not None and "verification factor" in note


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ((10.0, 11.0), 0.1),
        ((10.0, 9.0), -0.1),
        ((10.0, 10.0), 0.0),
        ((11.5, 12.0), 12.0 / 11.5 - 1.0),
    ],
)
def test_the_verification_factor_computes_the_session_return(
    closes: tuple[float, float], expected: float
) -> None:
    """The magnitude, not just the sign: a formula asserted only for sign would pass for
    `close[-1] - close[-2]`, which is a different factor with the same sign everywhere."""
    result = _reversal_1d(_window(*closes))

    assert result is not None
    assert result == pytest.approx(expected)


def test_the_verification_factor_reads_only_the_last_two_sessions_of_a_longer_window() -> None:
    """The engine hands an evaluator exactly `lookback_sessions` sessions, but an evaluator
    that indexed from the front would still pass every two-element case above."""
    assert _reversal_1d(_window(1.0, 2.0, 10.0, 11.0)) == pytest.approx(0.1)


def test_a_zero_prior_close_is_undefined_rather_than_a_crash() -> None:
    """The branch behind `undefined_value`. Unreachable through this repository's own writers --
    `DAILY_PRICE_COLUMNS` records no null and no non-positive close across 58,055 bars spanning
    2001 to 2026, and `daily_bars_from_panel_rows` refuses one -- so it is driven directly. A
    guard whose only evidence was a docstring would be the declaration this test exists to
    replace."""
    assert _reversal_1d(_window(0.0, 11.0)) is None


def test_an_evaluator_reaching_for_an_undeclared_column_is_refused_by_the_window() -> None:
    """A `KeyError` here would read as "the engine is broken"; what it means is that the
    definition's `required_fields` does not cover what the formula reads, which is the field
    the whole coverage check is built on."""
    window = FactorWindow(
        subject="000001.SZ",
        as_of=AS_OF,
        sessions=(date(2026, 1, 8), date(2026, 1, 9)),
        periods=(),
        values=MappingProxyType({("daily", "vol"): (1.0, 2.0)}),
    )

    with pytest.raises(FactorEngineError, match=r"did not declare daily\.close"):
        _reversal_1d(window)


# --- the vocabularies ----------------------------------------------------------------------------


def test_the_census_order_is_every_declared_coverage_code_and_no_other() -> None:
    """Two copies of a closed set drift; `panel_fixtures.STATEMENT_DATASETS` carries the same
    kind of pin against the domain's own tuple for the same reason."""
    assert set(FACTOR_COVERAGE_ORDER) == FACTOR_COVERAGE_CODES
    assert len(FACTOR_COVERAGE_ORDER) == len(FACTOR_COVERAGE_CODES)
    assert FACTOR_COVERAGE_ORDER[0] == "computed"


def test_the_stored_observation_columns_are_the_six_facts_the_acceptance_names() -> None:
    """`V2-P3-002` asks each observation to record subject, as-of, value, coverage marker, input
    reference and build manifest. The subject and the as-of are the batch's own columns; the
    other four are stored ones, and this pins that none of them was dropped."""
    assert FACTOR_OBSERVATION_PANEL_COLUMNS[0] == "subject"
    assert set(FACTOR_OBSERVATION_DATA_COLUMNS) >= {
        "value",
        "coverage",
        "manifest_id",
        "input_row_count",
        "input_session_first",
        "input_session_last",
        "factor_id",
    }


def _stored_row(**overrides: object) -> tuple[object, ...]:
    """One well-formed `factor_obs_*` row, in the stored column order."""
    cells: dict[str, object] = {
        "event_time": AS_OF,
        "subject": "000001.SZ",
        "factor_id": "fct_probe",
        "factor_key": "reversal_1d",
        "factor_version": 1,
        "value": 0.25,
        "coverage": "computed",
        "manifest_id": "fmn_probe",
        "input_row_count": 2,
        "input_session_first": "2026-01-08",
        "input_session_last": "2026-01-09",
        "input_period_first": None,
        "input_period_last": None,
        **overrides,
    }
    return tuple(cells.values())


def test_a_stored_row_of_the_wrong_width_is_refused_rather_than_unpacked_positionally() -> None:
    """A partition written by a build with a different column list would otherwise decode into
    plausible values in the wrong fields -- a `manifest_id` read as a `factor_id`, a session
    date read as a coverage code. The width check is what makes that a refusal."""
    with pytest.raises(FactorEngineError, match="expected 13"):
        _observation_from_row(("too", "few"), dataset=OBSERVATIONS)


def test_a_stored_coverage_code_this_build_does_not_declare_is_refused() -> None:
    """The forward-compatibility direction, and the same judgement
    `PANEL_BATCH_SCHEMA_VERSIONS_READABLE` makes: a value this build cannot interpret is
    refused rather than guessed at, because a verdict computed from a field whose meaning is
    unknown is worse than no verdict."""
    row = _stored_row(value=None, coverage="probably_fine", input_row_count=0)

    with pytest.raises(FactorEngineError, match="which this build does not declare"):
        _observation_from_row(row, dataset=OBSERVATIONS)


@pytest.mark.parametrize("stored", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_a_stored_value_that_is_not_a_finite_number_is_refused(stored: object) -> None:
    """`_coverage_code`'s symmetric case, which was missing.

    That function defends the `coverage` column against a build that knows a sixth code; nothing
    defended the `value` column against a number this build's own rules say cannot be in it.
    `float(str(cell))` parses `'nan'` and `'inf'` without complaint, so a stored row carrying
    either decoded into a `computed` observation and reached `FactorPanel.values()` -- the input
    to a rank correlation. Driven from both a text cell and a float cell, because the decoder
    stringifies before parsing and a guard placed on only one of the two would look like it
    worked.
    """
    with pytest.raises(FactorEngineError, match="not a finite number"):
        _observation_from_row(_stored_row(value=stored), dataset=OBSERVATIONS)


def test_a_stored_row_whose_event_clock_is_not_an_instant_is_refused() -> None:
    row = _stored_row(event_time="2026-01-12", value=None, coverage="not_in_universe")

    with pytest.raises(FactorEngineError, match="not a datetime"):
        _observation_from_row(row, dataset=OBSERVATIONS)


def test_a_well_formed_stored_row_decodes_into_every_field_it_came_from() -> None:
    """The positive half, without which the four refusals above would be satisfied by a
    decoder that rejected everything."""
    observation = _observation_from_row(_stored_row(), dataset=OBSERVATIONS)

    assert observation.subject == "000001.SZ"
    assert observation.as_of == AS_OF
    assert observation.value == pytest.approx(0.25)
    assert observation.coverage == "computed"
    assert observation.factor_id == "fct_probe"
    assert observation.manifest_id == "fmn_probe"
    assert observation.input_row_count == 2
    assert observation.input_session_first == date(2026, 1, 8)
    assert observation.input_session_last == date(2026, 1, 9)


def test_the_manifest_columns_carry_both_partition_hashes_and_both_row_counts() -> None:
    """The input reference's own acceptance: which partitions, proved two ways, and how much of
    each one was visible against how much was withheld.

    `input_batch_digest` is stored and is **not** a field of the hashed manifest, which is the
    whole arrangement: recorded, and out of a content address that has to survive a re-fetch.
    """
    assert {
        "input_dataset",
        "input_year",
        "input_batch_digest",
        "input_partition_hash",
        "input_visible_rows",
        "input_withheld_rows",
    } <= set(FACTOR_MANIFEST_DATA_COLUMNS)
    assert "code_commit" in FACTOR_MANIFEST_DATA_COLUMNS


def test_the_manifest_columns_carry_what_makes_two_builds_distinguishable() -> None:
    """The two sets and the two bounds, stored rather than only hashed.

    A reader holding an observation partition can otherwise say nothing about *which* cross
    section produced a number: `subject_count` was there and two builds over disjoint two-name
    cross sections shared it, and shared a `manifest_id`. `direction` is here for the sharper
    version of the same problem -- a stored `value` column cannot be read at all without knowing
    which end of the cross section the factor calls good.
    """
    assert {
        "subject_digest",
        "universe_digest",
        "direction",
        "lookback_sessions",
        "max_window_sessions",
    } <= set(FACTOR_MANIFEST_DATA_COLUMNS)


def test_the_stored_census_is_one_column_per_declared_coverage_code() -> None:
    """`coverage_census()` speaks only to a caller that asks, and no face asks yet -- so a build
    that answered nobody was indistinguishable in storage from one that scored the whole market.

    Derived from `FACTOR_COVERAGE_ORDER` rather than listed, so a sixth coverage code arrives
    with a column; asserted in both directions so a column that stopped being derived from the
    vocabulary is caught rather than a set that merely overlaps it.
    """
    assert tuple(f"census_{code}" for code in FACTOR_COVERAGE_ORDER) == FACTOR_CENSUS_COLUMNS
    assert set(FACTOR_CENSUS_COLUMNS) <= set(FACTOR_MANIFEST_DATA_COLUMNS)
    assert len(FACTOR_CENSUS_COLUMNS) == len(FACTOR_COVERAGE_CODES)


# --- the stored manifest, decoded ---------------------------------------------------------------


def _manifest_cells_from(**overrides: object) -> dict[str, object]:
    """One well-formed `factor_manifest_*` row, in the stored column order."""
    cells: dict[str, object] = {
        "subject": "fmn_probe",
        "factor_id": "fct_probe",
        "factor_key": "reversal_1d",
        "factor_version": 1,
        "as_of_time": AS_OF,
        "date_timezone": "Asia/Shanghai",
        "code_commit": "a1b2c3d",
        "direction": "lower_is_better",
        "lookback_sessions": 2,
        "max_window_sessions": 2,
        "lookback_periods": None,
        "max_window_periods": None,
        "subject_count": 2,
        "subject_digest": set_digest(("000001.SZ", "000002.SZ")),
        "universe_count": 2,
        "universe_digest": set_digest(("000001.SZ", "000002.SZ")),
        **dict.fromkeys(FACTOR_CENSUS_COLUMNS, 0),
        "input_dataset": "daily",
        "input_year": 2026,
        "input_batch_digest": "sha256:aa",
        "input_partition_hash": "bb",
        "input_visible_rows": 4,
        "input_withheld_rows": 0,
        **overrides,
    }
    assert tuple(cells) == FACTOR_MANIFEST_PANEL_COLUMNS
    return cells


def test_a_stored_manifest_row_of_the_wrong_width_is_refused() -> None:
    """`_observation_from_row`'s argument one dataset over: a partition written by a build with a
    different column list would decode into plausible values in the wrong fields."""
    with pytest.raises(FactorEngineError, match="expected 27"):
        _manifest_cells(("too", "few"), dataset=MANIFESTS)


def test_a_stored_manifest_row_whose_as_of_is_not_an_instant_is_refused() -> None:
    with pytest.raises(FactorEngineError, match="not a datetime"):
        _manifest_from_rows(
            [_manifest_cells_from(as_of_time="2026-01-12")],
            dataset=MANIFESTS,
            manifest_id="fmn_probe",
        )


def test_a_stored_direction_this_build_does_not_declare_is_refused() -> None:
    """`_coverage_code`'s judgement applied to the other stored vocabulary: a value this build
    cannot interpret is refused rather than cast, because `direction` is what a reader needs to
    read the *sign* of the values the build produced."""
    with pytest.raises(FactorEngineError, match="which this build does not declare"):
        _manifest_from_rows(
            [_manifest_cells_from(direction="sideways")],
            dataset=MANIFESTS,
            manifest_id="fmn_probe",
        )


def test_a_stored_build_that_does_not_reassemble_to_its_own_identity_is_refused() -> None:
    """The only thing that makes `load_factor_manifests`' output trustworthy.

    Every field it reads is one the identity was computed from, so a decoder that dropped or
    mistyped one would hand back a build nobody ever ran -- under the `manifest_id` the caller
    then uses to name it in `supersedes`. The stored subject *is* the identity, so recomputing it
    and comparing costs one hash and settles it.
    """
    with pytest.raises(FactorEngineError, match="reassembles to"):
        _manifest_from_rows(
            [_manifest_cells_from()],
            dataset=MANIFESTS,
            manifest_id="fmn_not_this_one",
        )


def test_a_panel_whose_provenance_does_not_cover_its_inputs_cannot_be_written() -> None:
    """`FactorPanel.input_provenance` and `manifest.inputs` are produced together by
    `compute_factor`, and a hand-assembled panel where they disagree would otherwise store an
    `input_batch_digest` naming the wrong partition -- which is worse than the missing one it
    would be standing in for, because the column's whole purpose is provenance."""
    manifest = FactorBuildManifest(
        factor_id=REVERSAL_1D.factor_id,
        factor_key=REVERSAL_1D.key,
        factor_version=REVERSAL_1D.version,
        as_of=AS_OF,
        date_timezone="Asia/Shanghai",
        code_commit="a1b2c3d",
        direction=REVERSAL_1D.direction,
        lookback_sessions=REVERSAL_1D.lookback_sessions,
        max_window_sessions=REVERSAL_1D.max_window_sessions,
        lookback_periods=None,
        max_window_periods=None,
        subject_count=1,
        subject_digest=set_digest(("000001.SZ",)),
        universe_count=1,
        universe_digest=set_digest(("000001.SZ",)),
        inputs=(
            FactorInputRef(
                dataset="daily",
                year=2026,
                partition_content_hash="bb",
                visible_row_count=2,
                withheld_row_count=0,
            ),
        ),
    )
    mismatched = FactorPanel(
        definition=REVERSAL_1D,
        manifest=manifest,
        observations=(),
        built_at=AS_OF,
        input_provenance=(
            FactorInputProvenance(dataset="daily", year=2025, batch_digest="sha256:aa"),
        ),
    )

    with pytest.raises(FactorEngineError, match=r"does not carry a batch digest"):
        factor_manifest_batch(mismatched)


# --- the dataset names ---------------------------------------------------------------------------


def test_each_factor_is_filed_under_its_own_two_datasets() -> None:
    """The partition axis this plane offers, and the reason `V2-P3-009`..`013`'s seventeen
    factors do not have to be alive at once to write a year; see `panel_factors`' docstring."""
    assert factor_observation_dataset(REVERSAL_1D) == "factor_obs_reversal_1d_v1"
    assert factor_manifest_dataset(REVERSAL_1D) == "factor_manifest_reversal_1d_v1"
    assert factor_observation_dataset(REVERSAL_1D) != factor_manifest_dataset(REVERSAL_1D)


def test_the_longest_legal_factor_key_still_names_a_legal_panel_dataset() -> None:
    """`MAX_FACTOR_KEY_LENGTH` is sized against the dataset-name budget, and the arithmetic tying
    them together is the kind that goes stale in a comment.

    Built as the actual worst case -- the longest key the contract admits, the highest version it
    admits, the longer of the two prefixes -- and handed to the panel plane's own validator
    rather than compared against a number restated here. Widening `MAX_FACTOR_KEY_LENGTH` or
    lengthening either prefix fails here rather than at the first write.
    """
    longest = FactorDefinition(
        key="k" * MAX_FACTOR_KEY_LENGTH,
        version=999,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=2,
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
    )

    for name in (factor_observation_dataset(longest), factor_manifest_dataset(longest)):
        validate_panel_dataset(name)
        assert len(name) <= MAX_IDENTIFIER_LENGTH

    widest = max(FACTOR_OBSERVATION_DATASET_PREFIX, FACTOR_MANIFEST_DATASET_PREFIX, key=len)
    assert len(widest) + MAX_FACTOR_KEY_LENGTH + len("_v999") <= MAX_IDENTIFIER_LENGTH


def test_the_factor_planes_datasets_are_derived_and_therefore_have_no_cadence() -> None:
    """`DATASET_CADENCE` says how often an *upstream* publishes, and a derived dataset has no
    upstream -- so `panel doctor` and `panel_gate` refuse to be asked about one.

    That was prose and nothing held it. It matters more now than when it was written about a
    fixed pair: the factor plane's datasets are one pair *per factor*, so `V2-P3-009`..`013`
    bring seventeen more, and a family that grows is the kind whose next member arrives
    unnoticed. Read off the live registry rather than a list, so it covers factors that do not
    exist yet. `V2-P3-014`/`015` own the factor-side health report this defers to.
    """
    derived = {
        name
        for definition in FACTOR_DEFINITIONS.definitions
        for name in (factor_observation_dataset(definition), factor_manifest_dataset(definition))
    }

    assert derived
    assert not (derived & set(DATASET_CADENCE))


# --- the prose the three shipped registries carry -------------------------------------------------


SHIPPED_REGISTRIES: Final[tuple[tuple[str, tuple[str, ...], object], ...]] = (
    ("factor", FACTOR_DEFINITIONS.qualified_keys, FACTOR_DEFINITIONS),
    ("transform", FACTOR_TRANSFORMS.qualified_keys, FACTOR_TRANSFORMS),
    ("neutralisation", FACTOR_NEUTRALIZATIONS.qualified_keys, FACTOR_NEUTRALIZATIONS),
)
"""The three registries a build actually ships, each with the handles it declares.

Written out because the test below is parametrised on it and a failure has to name which of the
three lost its prose. That it is *these three* and not two or four is not left to the writing:
`test_the_shipped_registry_table_is_every_registry_this_build_declares` reads the source tree for
module-level registry constants and holds this tuple against what it finds.
"""

REGISTRY_ANNOTATIONS: Final[frozenset[str]] = frozenset(
    {"FactorRegistry", "FactorTransformRegistry", "FactorNeutralizationRegistry"}
)
"""The registry types a shipped constant can be annotated with, as the source spells them."""


def _module_level_registry_names(path: Path) -> Iterator[str]:
    """Every `NAME: Final[<a registry type>] = ...` this module declares, by AST rather than
    import: a registry that is declared and never referenced still ships."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        annotation = ast.unparse(node.annotation)
        if any(f"[{name}]" in annotation for name in REGISTRY_ANNOTATIONS):
            yield node.target.id


def test_the_shipped_registry_table_is_every_registry_this_build_declares() -> None:
    """The direction a hand-written table cannot cover: a **fourth** registry.

    `test_every_shipped_contract_carries_its_prose` is parametrised on `SHIPPED_REGISTRIES`, so a
    fourth identity contract shipped without prose would not fail it -- it would simply not be
    asked. That is the same shape as `KNOWN_*`'s: ten registries arrived under a per-module
    binding and none of them acquired one, until
    `test_the_registry_table_is_every_known_registry_in_the_source_tree` read the tree instead.
    This is that check for this table.

    The narrow half of the gap is already closed and is worth saying so the two are not confused:
    adding a *definition* to one of these three registries and forgetting its note fails at
    import, because each registry validates its own notes -- which is the road `V2-P3-009`..`013`
    take. What was open is the registry nobody has written yet.
    """
    found = {
        name
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for name in _module_level_registry_names(path)
    }

    assert found == {"FACTOR_DEFINITIONS", "FACTOR_TRANSFORMS", "FACTOR_NEUTRALIZATIONS"}
    assert len(SHIPPED_REGISTRIES) == len(found)


@pytest.mark.parametrize(("role", "handles", "registry"), SHIPPED_REGISTRIES)
def test_every_shipped_contract_carries_its_prose(
    role: str, handles: tuple[str, ...], registry: Any
) -> None:
    """`validate_notes` refuses a note about nothing and declines to require the converse; this
    is the converse, required where it is a real obligation.

    An arbitrary registry has no such duty -- a test's two probe specs have nothing to say about
    themselves, and forcing a sentence would produce the placeholder prose `V2-P0B-009` deleted.
    A *shipped* contract is different: it is the configuration this build runs, its settings are
    each a stated judgement, and the prose is where the judgement is stated. Before this change
    the obligation was met by a mandatory `summary` field that also poisoned the content address;
    the field is gone and this is what replaced it.

    Asserted per registry rather than over a merged set, so a failure names which of the three
    lost its prose, and on `handles` rather than on the notes, so a note for a contract that no
    longer exists cannot make the count come out right.
    """
    assert handles
    for handle in handles:
        note = registry.note_for(handle)
        assert note is not None, f"the shipped {role} {handle} carries no note"
        assert note.strip() == note
        assert len(note) > 100, f"the {role} note for {handle} says almost nothing"
