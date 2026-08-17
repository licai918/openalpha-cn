"""The preprocessing transform against a real panel (`V2-P3-003`).

The acceptance is D8: *"原始因子值与预处理分离 ... 报告可比较 raw / processed / neutralized 表现而
不覆盖源观测"*, and each half of it is asserted here against partitions on disk rather than
against in-memory objects -- because the round trip is where a column that was never written, or
one that decodes into the wrong field, becomes visible.

## The four claims this file exists to hold

1. **The processed values are separate from the raw ones and both are readable at once.** Two
   partitions, two record types, one `as_of`; the raw values are asserted **byte-identical**
   before and after a transform is written over the same year, and both are read back in one
   test.
2. **A processed row names the exact raw row it came from.**
   `(source_manifest_id, subject, as_of)` is performed as a join against
   `load_factor_observations` rather than described, and the value it lands on is the one the
   transform's arithmetic used.
3. **The cross section is point-in-time because it is the source panel's.** The processed values
   at a mid-year `as_of` are derived by hand from the closes the availability filter left, so a
   transform that ever consulted a later session would produce different numbers; and the
   subjects of the output are exactly the subjects of the input.
4. **What determines the answers is either in the identity or exempted by name**, audited off
   `apply_factor_transform`'s own signature -- `test_every_determinant_of_this_build_is_either_
   in_the_identity_or_exempted_by_name`'s instrument, because a parametrized equivalence test
   varies what a model declares and cannot show that the model declares everything.

## The frame

The generated panel (eight securities, `daily.close_moves_between_sessions`) for everything that
needs a real point-in-time read, and a hand-written 120-security probe partition for the shipped
`CROSS_SECTION_STANDARD`, whose `min_cross_section` is 100 -- so that the *shipped* configuration
is exercised end to end rather than only probe specs invented by tests. Eight names is below that
floor by design and the fixture-panel tests use a probe spec with a floor of 1.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import YEAR, GeneratedPanel, generate_panel, write_generated_panel

from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor import FactorDefinition, FactorField
from openalpha_cn.domain.factor_transform import (
    PROCESSED_COVERAGE_ORDER,
    FactorTransformSpec,
    MissingValuePolicy,
    ProcessedFactorObservation,
    WinsorizationPolicy,
)
from openalpha_cn.domain.panel_batch import (
    SUBJECT_COLUMN_NAME,
    ColumnarPanelBatch,
    PanelColumn,
    TimelineColumns,
)
from openalpha_cn.panel.catalog import KNOWN_STORAGE_LIMITATIONS, ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    REVERSAL_1D,
    TRANSFORM_MANIFEST_DATA_COLUMNS,
    FactorEngineError,
    FactorPanel,
    ProcessedFactorPanel,
    _median,
    _population_stdev,
    apply_factor_transform,
    compute_factor,
    factor_observation_dataset,
    factor_transform_manifest_dataset,
    load_factor_manifests,
    load_factor_observations,
    load_factor_transform_manifests,
    load_processed_factor_observations,
    processed_factor_dataset,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement, write_panel_batch

MID_WINDOW: Final[datetime] = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on the sixth session: sessions 1-5 knowable, 6-10 not."""

EARLY_WINDOW: Final[datetime] = datetime(2026, 1, 9, 4, 0, tzinfo=UTC)

BUILT_AT: Final[datetime] = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"
INPUT_STALENESS_BOUND: Final[timedelta] = timedelta(days=5)
SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions",)
HALTED: Final[str] = "601318.SH"

OBSERVATIONS: Final[str] = factor_observation_dataset(REVERSAL_1D)
PROCESSED: Final[str] = processed_factor_dataset(REVERSAL_1D)
TRANSFORM_MANIFESTS: Final[str] = factor_transform_manifest_dataset(REVERSAL_1D)


def _spec(**overrides: Any) -> FactorTransformSpec:
    """A probe transform whose floor is 1, so the eight-name fixture panel can be processed."""
    settings: dict[str, Any] = {
        "key": "probe_zscore",
        "version": 1,
        "winsorization": WinsorizationPolicy(method="none"),
        "standardization": "zscore",
        "missing_values": MissingValuePolicy(
            not_in_universe="exclude",
            insufficient_history="exclude",
            ambiguous_filing="exclude",
            input_missing="exclude",
            undefined_value="exclude",
        ),
        "min_cross_section": 1,
        **overrides,
    }
    return FactorTransformSpec(**settings)


RANK_SPEC: Final[FactorTransformSpec] = _spec(key="probe_rank", standardization="rank")


@pytest.fixture
def panel() -> GeneratedPanel:
    return generate_panel(shapes=SHAPES)


@pytest.fixture
def store(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, panel)
    return built


def _close(panel: GeneratedPanel, code: str, day: date) -> float:
    """The fixture's own close arithmetic, restated so expected values are derived, not copied."""
    return 10.0 + panel.securities.index(code) + 0.5 * panel.sessions.index(day)


def _compute(
    store: PanelStore, panel: GeneratedPanel, *, as_of: datetime = MID_WINDOW, **overrides: Any
) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": as_of,
        "subjects": panel.securities,
        "universe": frozenset(panel.securities),
        "requirements": {
            DAILY_DATASET: daily_requirement(
                panel.calendar(),
                years=(YEAR,),
                as_of=as_of,
                max_staleness=INPUT_STALENESS_BOUND,
            )
        },
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(store, REVERSAL_1D, **settings)  # type: ignore[arg-type]


def _apply(source: FactorPanel, spec: FactorTransformSpec | None = None) -> ProcessedFactorPanel:
    return apply_factor_transform(
        source, _spec() if spec is None else spec, code_commit=COMMIT, built_at=BUILT_AT
    )


# --- the values, and the fact that they are not the raw ones ----------------------------------


def test_the_processed_values_at_a_mid_year_as_of_are_the_ones_the_visible_window_implies(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The point-in-time claim, as numbers rather than as a structural argument.

    Every expected z-score is derived from the closes the *availability filter* left at
    `MID_WINDOW` -- sessions 1-5 of ten -- through the fixture's own close arithmetic, and then
    standardized by hand. A transform that consulted a later session, or one whose participants
    were not exactly the raw panel's `computed` observations, would produce a different number for
    every security and fail here rather than pass with a plausible one.

    The structural half is `test_the_transform_takes_no_store_and_therefore_no_second_visibility_
    rule`; this is the measurement that would catch a change to it.
    """
    raw = _compute(store, panel)
    expected_raw = {}
    for code in panel.securities:
        window = (
            (date(2026, 1, 7), date(2026, 1, 8))
            if code == HALTED
            else (
                date(2026, 1, 8),
                date(2026, 1, 9),
            )
        )
        expected_raw[code] = _close(panel, code, window[1]) / _close(panel, code, window[0]) - 1.0
    location = sum(expected_raw.values()) / len(expected_raw)
    scale = _population_stdev(tuple(expected_raw.values()))

    result = _apply(raw)

    assert raw.values() == pytest.approx(expected_raw)
    assert result.statistics.location == pytest.approx(location)
    assert result.statistics.scale == pytest.approx(scale)
    for code, value in expected_raw.items():
        assert result.values()[code] == pytest.approx((value - location) / scale), code


def test_a_transform_introduces_no_security_the_source_panel_did_not_have(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The cross section a cross-sectional statistic sees is exactly the one `compute_factor`
    produced at that `as_of` -- no wider, no narrower, and in the same order.

    Asserted against a *narrowed universe*, so the panel carries a `not_in_universe` row: the
    output still has a row for it, marked `source_not_computed`, and never a value. A transform
    that had derived its own cross section from anywhere would either lose that name or gain one.
    """
    excluded = panel.securities[0]
    raw = _compute(store, panel, universe=frozenset(panel.securities) - {excluded})

    result = _apply(raw)
    by_subject = {item.subject: item for item in result.observations}

    assert tuple(item.subject for item in result.observations) == tuple(
        item.subject for item in raw.observations
    )
    assert by_subject[excluded].coverage == "source_not_computed"
    assert by_subject[excluded].source_coverage == "not_in_universe"
    assert excluded not in result.values()
    assert result.statistics.participant_count == len(panel.securities) - 1


def test_two_as_ofs_of_one_factor_standardize_against_their_own_cross_sections(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A cross section is per `as_of`, and the whole risk this layer adds is that it might not be.

    The two builds see different windows, so their raw values differ; their processed values must
    differ too, and each one's must sum to zero over its own participants -- which is the
    property that fails first if a transform ever pooled two `as_of`s.
    """
    early = _apply(_compute(store, panel, as_of=EARLY_WINDOW))
    middle = _apply(_compute(store, panel))

    assert early.values() != middle.values()
    assert sum(early.values().values()) == pytest.approx(0.0)
    assert sum(middle.values().values()) == pytest.approx(0.0)
    assert early.manifest.transform_manifest_id != middle.manifest.transform_manifest_id


def test_the_transform_takes_no_store_and_therefore_no_second_visibility_rule() -> None:
    """The structural half of the point-in-time claim, audited off the live signature.

    `compute_factor` reads through `PanelStore.read_visible_at` under a declared freshness bound
    pooled over `requirement.years`, and `V2-P3-002`'s remediation spent a round making that
    hold. A cross-sectional transform is where it could be reopened -- one security's processed
    value depends on the others -- so this function is written to be unable to: no store, no
    `as_of`, no universe, no requirement. Nothing it can read.

    Asserted on the parameter names *and* their annotations, because a `store` smuggled in under
    another name would pass a name-only check.
    """
    signature = inspect.signature(apply_factor_transform)

    assert set(signature.parameters) == {"panel", "spec", "code_commit", "built_at"}
    annotations = {name: str(item.annotation) for name, item in signature.parameters.items()}
    assert not any("PanelStore" in text for text in annotations.values())
    assert not any("Requirement" in text for text in annotations.values())
    assert "FactorPanel" in annotations["panel"]


# --- separation from the raw values -----------------------------------------------------------


def test_the_raw_and_processed_planes_are_two_partitions_that_read_back_together(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """D8's "与原值分离", end to end: written, read back, and both readable at one `as_of`.

    The raw values are asserted **byte-identical** across the processed write, which is the half
    a type argument cannot make: the two datasets could still have been the same directory.
    """
    raw = _compute(store, panel)
    write_factor_panels(store, [raw])
    before = {
        item.subject: item.value
        for item in load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    }

    processed = _apply(raw)
    write_processed_factor_panels(store, [processed])

    after = {
        item.subject: item.value
        for item in load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    }
    stored = load_processed_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_WINDOW
    )

    assert after == before
    assert PROCESSED != OBSERVATIONS
    assert {item.subject: item.value for item in stored} == pytest.approx(dict(processed.values()))
    assert {item.coverage for item in stored} == {"processed"}
    assert {item.source_coverage for item in stored} == {"computed"}


def test_a_processed_row_names_the_exact_raw_row_it_came_from(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The join, performed rather than described.

    `(source_manifest_id, subject, as_of)` is the key of a row in `factor_obs_reversal_1d_v1`,
    and this resolves every processed row through it and checks that the raw value it lands on is
    the one the transform's own arithmetic consumed: `raw = value * scale + location`.

    That last equation is what makes the pointer *load-bearing* rather than decorative -- a
    processed row pointing at the wrong build would still resolve, and would land on a number
    that does not reproduce.
    """
    raw = _compute(store, panel)
    processed = _apply(raw)
    write_factor_panels(store, [raw])
    write_processed_factor_panels(store, [processed])

    stored_raw = {
        (item.manifest_id, item.subject, item.as_of): item
        for item in load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    }
    stored_processed = load_processed_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_WINDOW
    )
    statistics = processed.statistics

    assert statistics.location is not None
    assert statistics.scale is not None
    assert len(stored_processed) == len(panel.securities)
    for row in stored_processed:
        source = stored_raw[(row.source_manifest_id, row.subject, row.as_of)]
        assert row.value is not None
        assert source.value is not None
        assert source.coverage == row.source_coverage
        assert source.value == pytest.approx(row.value * statistics.scale + statistics.location)


def test_two_transforms_of_one_factor_share_a_partition_and_read_back_apart(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The consequence of the dataset-name budget, exercised rather than only documented.

    One processed partition holds every transform of the factor, so both have to be written in
    one call and a read has to name which one it wants. Both halves are asserted, and the z-score
    and the rank are asserted to be *different numbers* over the same raw values -- otherwise a
    read that returned the wrong transform's rows would pass.
    """
    raw = _compute(store, panel)
    zscored = _apply(raw)
    ranked = _apply(raw, RANK_SPEC)
    write_processed_factor_panels(store, [zscored, ranked])

    first = load_processed_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_WINDOW
    )
    second = load_processed_factor_observations(
        store, REVERSAL_1D, RANK_SPEC, years=(YEAR,), as_of=MID_WINDOW
    )
    manifests = load_factor_transform_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert {item.transform_id for item in first} == {_spec().transform_id}
    assert {item.transform_id for item in second} == {RANK_SPEC.transform_id}
    assert len(first) == len(second) == len(panel.securities)
    assert {item.subject: item.value for item in first} != {
        item.subject: item.value for item in second
    }
    assert {item.transform_id for item in manifests} == {
        _spec().transform_id,
        RANK_SPEC.transform_id,
    }


def test_a_stored_transform_manifest_reassembles_to_the_identity_it_was_written_under(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`load_factor_transform_manifests`' whole trustworthiness claim, against a real partition."""
    processed = _apply(_compute(store, panel))
    write_processed_factor_panels(store, [processed])

    (stored,) = load_factor_transform_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert stored == processed.manifest
    assert stored.transform_manifest_id == processed.manifest.transform_manifest_id
    assert stored.source_manifest_id == processed.manifest.source_manifest_id


def test_a_rebuild_from_an_unchanged_source_reproduces_its_identity_and_writes_as_a_no_op(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The direction that makes `transform_manifest_id` a recovery path rather than only a drift
    detector: `write_processed_factor_panels` refuses to drop a stored build, so an identity that
    moved for no reason would be a rebuild that can never be written.

    The wall clock is moved between the two applications, because that is the field whose
    presence in the address would break exactly this.
    """
    raw = _compute(store, panel)
    first = _apply(raw)
    write_processed_factor_panels(store, [first])

    rebuilt = apply_factor_transform(
        raw, _spec(), code_commit=COMMIT, built_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    )
    write_processed_factor_panels(store, [rebuilt])

    (stored,) = load_factor_transform_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    assert rebuilt.manifest.transform_manifest_id == first.manifest.transform_manifest_id
    assert stored.transform_manifest_id == first.manifest.transform_manifest_id


def test_a_write_that_would_drop_a_stored_transform_build_is_refused_and_supersedes_names_one(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A partition is replaced whole, so a second transform written on its own destroys the first.

    The refusal is the enforcement of "one partition holds every transform of one factor" -- and
    `supersedes` is the only way past it, refusing a name no partition holds so a typo cannot
    turn the guard off for the write it arrived with.
    """
    raw = _compute(store, panel)
    zscored = _apply(raw)
    ranked = _apply(raw, RANK_SPEC)
    write_processed_factor_panels(store, [zscored])

    with pytest.raises(FactorEngineError, match="it would drop"):
        write_processed_factor_panels(store, [ranked])
    with pytest.raises(FactorEngineError, match="which no partition this write touches holds"):
        write_processed_factor_panels(store, [ranked], supersedes=("ftm_not_a_build",))

    write_processed_factor_panels(
        store, [ranked], supersedes=(zscored.manifest.transform_manifest_id,)
    )
    manifests = load_factor_transform_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    assert {item.transform_id for item in manifests} == {RANK_SPEC.transform_id}


def test_a_superseded_raw_build_leaves_its_processed_rows_pointing_at_nothing(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The disclosed residue of the provenance pointer, reproduced rather than asserted in prose.

    `KNOWN_STORAGE_LIMITATIONS.a_derived_partition_may_outlive_the_build_its_rows_point_at` says
    this plane has no cross-dataset referential integrity, and `V2-P3-003` is the first issue
    whose rows depend on one. This is the measurement that entry quotes: a raw build is written,
    transformed and stored, and then `write_factor_panels` is called for a second `as_of` with
    `supersedes` naming the first build. The write succeeds and every processed row is left
    naming a build the store no longer holds.

    Written as a test rather than left in the registry's prose for the reason the P2 review
    measured: a limitation whose code appears only in docstrings is one the suite has no opinion
    about, and renaming it leaves everything green. It also fails in the *useful* direction --
    if somebody later closes the hole, the last assertion goes red and the disclosure has to come
    out with the fix.
    """
    raw = _compute(store, panel)
    write_factor_panels(store, [raw])
    write_processed_factor_panels(store, [_apply(raw)])

    other = _compute(store, panel, as_of=EARLY_WINDOW)
    write_factor_panels(store, [other], supersedes=(raw.manifest.manifest_id,))

    stored_builds = {
        item.manifest_id
        for item in load_factor_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    }
    raw_keys = {
        (item.manifest_id, item.subject, item.as_of)
        for item in load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)
    }
    stored_processed = load_processed_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_WINDOW
    )
    unresolved = [
        item
        for item in stored_processed
        if (item.source_manifest_id, item.subject, item.as_of) not in raw_keys
    ]

    assert stored_builds == {other.manifest.manifest_id}
    assert raw.manifest.manifest_id not in stored_builds
    assert {item.source_manifest_id for item in stored_processed} == {raw.manifest.manifest_id}
    assert len(unresolved) == len(stored_processed) == len(panel.securities)
    assert "a_derived_partition_may_outlive_the_build_its_rows_point_at" in {
        item.code for item in KNOWN_STORAGE_LIMITATIONS
    }


def test_a_processed_partition_that_was_never_written_is_a_refusal_and_not_an_empty_read(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """Both loaders refuse a blocked read rather than answering with nothing.

    The distinction `V2-P1-013`'s acceptance exists for -- "assert blocking, not an empty
    success" -- applied to the read side: a caller asking for a transform nobody has run yet
    would otherwise get an empty tuple, which is indistinguishable from a build in which every
    security was excluded.
    """
    with pytest.raises(FactorEngineError, match="cannot be read at"):
        load_processed_factor_observations(
            store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_WINDOW
        )
    with pytest.raises(FactorEngineError, match="cannot be read at"):
        load_factor_transform_manifests(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)


def test_two_applications_of_one_transform_at_one_as_of_cannot_be_written_together(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """A second answer to one cross-section question would store two rows for every
    `(subject, as_of, transform)` and leave a reader to choose between them."""
    raw = _compute(store, panel)

    with pytest.raises(FactorEngineError, match="more than one application"):
        write_processed_factor_panels(store, [_apply(raw), _apply(raw)])
    with pytest.raises(FactorEngineError, match="at least one panel"):
        write_processed_factor_panels(store, [])


# --- what determines the answers ------------------------------------------------------------------


_IDENTITY_EXEMPT_ARGUMENTS: Final[dict[str, str]] = {
    "built_at": (
        "the wall clock, deliberately out of the content address so that re-applying an "
        "unchanged transform to an unchanged source build reproduces its ID and can therefore "
        "be written past the drop guard; recorded as the partition's fetched_at instead"
    ),
}
"""Every `apply_factor_transform` argument that does **not** have to move
`transform_manifest_id`, with why.

One entry, and it is the one `FactorBuildManifest` already carries the same exemption for. The
list is short because this function was written to have nothing else to exempt: it takes no
store, no evaluator table and no requirement, so the three exemptions `compute_factor` needs do
not arise here. An exemption is a claim, so it is written out rather than left as a name in a
set.
"""

_DETERMINANT_CASES: Final[tuple[tuple[str, str], ...]] = (
    ("panel", "panel"),
    ("spec", "spec"),
    ("code_commit", "code_commit"),
)
"""Every `apply_factor_transform` argument that **must** move `transform_manifest_id`."""


@pytest.mark.parametrize(("argument", "_"), _DETERMINANT_CASES)
def test_every_determinant_of_this_transform_moves_the_manifest_id(
    store: PanelStore, panel: GeneratedPanel, argument: str, _: str
) -> None:
    """One argument at a time, against the baseline application.

    `panel` is varied by its *observations* rather than by its manifest, which is the sharper
    case and the reason `source_observation_digest` is a field: a build manifest identifies a
    computation's inputs, so a panel carrying the same `manifest_id` and different numbers is
    constructible, and an identity blind to that would be exempting `panel` on a promise.
    """
    raw = _compute(store, panel)
    baseline = _apply(raw)

    if argument == "panel":
        varied = _apply(_compute(store, panel, as_of=EARLY_WINDOW))
    elif argument == "spec":
        varied = _apply(raw, RANK_SPEC)
    else:
        varied = apply_factor_transform(raw, _spec(), code_commit="0000000", built_at=BUILT_AT)

    assert varied.manifest.transform_manifest_id != baseline.manifest.transform_manifest_id


def test_a_source_panel_whose_numbers_moved_moves_the_transform_identity(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The `panel` determinant at its sharpest: the *same* source build identity, different rows.

    Constructed by hand because `compute_factor` will not produce it, which is the point --
    `FactorPanel` is a public frozen dataclass, so this is reachable, and without
    `source_observation_digest` the two applications would share a `transform_manifest_id` while
    producing different numbers.
    """
    raw = _compute(store, panel)
    first, *rest = raw.observations
    tampered = dataclasses.replace(first, value=(first.value or 0.0) + 1.0)
    edited = FactorPanel(
        definition=raw.definition,
        manifest=raw.manifest,
        observations=(tampered, *rest),
        built_at=raw.built_at,
        input_provenance=raw.input_provenance,
    )

    baseline = _apply(raw)
    moved = _apply(edited)

    assert edited.manifest.manifest_id == raw.manifest.manifest_id
    assert moved.manifest.source_manifest_id == baseline.manifest.source_manifest_id
    assert moved.manifest.source_observation_digest != baseline.manifest.source_observation_digest
    assert moved.manifest.transform_manifest_id != baseline.manifest.transform_manifest_id


def test_every_determinant_of_this_transform_is_either_in_the_identity_or_exempted_by_name() -> (
    None
):
    """The audit, and the reason the two tables above cannot go stale.

    `test_every_determinant_of_this_build_is_either_in_the_identity_or_exempted_by_name`'s
    instrument, applied to the function this issue adds: the parameters are read off the live
    signature and partitioned, so a fifth argument fails here until somebody classifies it. A
    test that varied a hand-written list would pass just as happily while one arrived -- which is
    the drift `_refuse_table_drift` and `panel build`'s `_audit_written_partitions` were both
    written to close.
    """
    parameters = set(inspect.signature(apply_factor_transform).parameters)
    moved = {argument for argument, _ in _DETERMINANT_CASES}

    assert moved | set(_IDENTITY_EXEMPT_ARGUMENTS) == parameters
    assert not (moved & set(_IDENTITY_EXEMPT_ARGUMENTS))
    assert all(len(reason) > 40 for reason in _IDENTITY_EXEMPT_ARGUMENTS.values())


# --- the shipped transform, on a cross section wide enough for it -------------------------------


WIDE_DATASET: Final[str] = "probe_wide_cross_section"
WIDE_AS_OF: Final[datetime] = datetime(2026, 1, 8, 4, 0, tzinfo=UTC)
WIDE_SESSIONS: Final[tuple[date, ...]] = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
WIDE_COUNT: Final[int] = 120
"""Twenty above `CROSS_SECTION_STANDARD.min_cross_section`, so the shipped floor is cleared with
room rather than exactly -- a fixture sized to the boundary makes every other assertion in the
test depend on the boundary's arithmetic."""

WIDE_DEFINITION: Final[FactorDefinition] = FactorDefinition(
    key="probe_wide",
    version=1,
    family="value",
    direction="higher_is_better",
    required_fields=(FactorField(dataset=WIDE_DATASET, column="score"),),
    lookback_sessions=1,
    max_window_sessions=1,
    lookback_periods=None,
    max_window_periods=None,
)


HOLED_SUBJECT: Final[str] = "999998.SZ"
"""A 121st security in the partition with every session present and the last one's cell null.

The shape `daily_basic` actually has -- `panel_ingest.load_daily_valuations` measured 60 of
3,843 names carrying no valuation row on 2020-03-02 -- and the one that produces `input_missing`
rather than `insufficient_history`, which matters because the shipped transform's actions for the
two differ: `insufficient_history` is excluded and `input_missing` is filled. It is in the
partition and **not** in `_wide_subjects()`, so only the test that wants a hole asks about it.
"""


def _wide_subjects() -> tuple[str, ...]:
    return tuple(f"{index + 1:06d}.SZ" for index in range(WIDE_COUNT))


def _wide_score(index: int) -> float:
    """A cross section with one deliberate outlier, so a 1% winsorization has something to clip.

    Linear in the index except for the last name, which sits an order of magnitude out. Without
    it the shipped transform's `winsorized_high_count` would still be 2 -- the rule clips
    `ceil((n-1) q)` names whatever the shape -- but the *effect* on the deviation would be
    invisible, and this test's point is that the effect is real.
    """
    return 1000.0 if index == WIDE_COUNT - 1 else float(index)


@pytest.fixture
def wide_store(tmp_path: Path) -> PanelStore:
    store = PanelStore(tmp_path / "wide")
    subjects = (*_wide_subjects(), HOLED_SUBJECT)
    instants = tuple(
        datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC) for day in WIDE_SESSIONS
    )
    published = tuple(
        datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC) for day in WIDE_SESSIONS
    )
    scores: list[float | None] = []
    for index in range(WIDE_COUNT):
        scores.extend(_wide_score(index) for _ in WIDE_SESSIONS)
    scores.extend([0.0, 0.0, None])
    write_panel_batch(
        store,
        ColumnarPanelBatch(
            provider_id="synthetic",
            dataset=WIDE_DATASET,
            kind="probe",
            as_of=datetime(2026, 12, 31, 12, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 12, 31, 12, 0, tzinfo=UTC),
            status="success",
            subjects=tuple(name for name in subjects for _ in WIDE_SESSIONS),
            timeline=TimelineColumns(
                event_time=tuple(instants) * len(subjects),
                available_time=tuple(published) * len(subjects),
                ingested_time=tuple(published) * len(subjects),
                revision_time=tuple(published) * len(subjects),
            ),
            columns=(PanelColumn("score", "float", tuple(scores)),),
        ),
        year=YEAR,
    )
    return store


def _wide_requirement() -> ReadinessRequirement:
    return ReadinessRequirement(
        dataset=WIDE_DATASET,
        as_of=WIDE_AS_OF,
        years=(YEAR,),
        required_dates=None,
        required_subjects=None,
        required_fields=("subject", "score"),
        max_staleness=timedelta(days=5),
    )


def test_the_shipped_transform_standardizes_a_cross_section_wide_enough_for_its_own_floor(
    wide_store: PanelStore,
) -> None:
    """`CROSS_SECTION_STANDARD` end to end, at a size its declared floor admits.

    Three things are asserted as numbers rather than as shapes: the 1% winsorization clips two
    names at each end of a 120-name cross section (`ceil(119 * 0.01) = 2`), the deviation the
    z-score divides by is the *winsorized* one and is more than two and a half times smaller than
    the raw one, and the whole processed cross section sums to zero.

    **The ratio in that sentence was wrong and the number below is what it always was.** It read
    "more than eight times smaller" against pinned values of `raw_scale = 92.128623` and
    `scale = 34.600725`, whose ratio is 2.6626 -- the very ratio the assertion under it tests.
    (The "27 times" in `test_the_deviation_the_z_score_divides_by_is_the_winsorized_one` is
    correct: 39.0128 / 1.4142 = 27.6. This fixture's outlier is one name in 120 rather than one
    in six, so it moves the deviation by less.)
    """
    subjects = _wide_subjects()
    raw = compute_factor(
        wide_store,
        WIDE_DEFINITION,
        as_of=WIDE_AS_OF,
        subjects=subjects,
        universe=frozenset(subjects),
        requirements={WIDE_DATASET: _wide_requirement()},
        code_commit=COMMIT,
        built_at=BUILT_AT,
        evaluators={
            WIDE_DEFINITION.qualified_key: lambda window: window.series(WIDE_DATASET, "score")[-1]
        },
    )

    result = apply_factor_transform(
        raw, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )

    assert result.statistics.participant_count == WIDE_COUNT
    assert result.statistics.winsorized_low_count == 2
    assert result.statistics.winsorized_high_count == 2
    assert result.statistics.scale is not None
    raw_scale = _population_stdev(tuple(raw.values().values()))
    assert raw_scale == pytest.approx(92.128623, rel=1e-6)
    assert result.statistics.scale == pytest.approx(34.600725, rel=1e-6)
    assert raw_scale / result.statistics.scale > 2.6
    assert result.statistics.upper_bound == pytest.approx(117.81)
    assert sum(result.values().values()) == pytest.approx(0.0)
    assert dict(result.coverage_census())["processed"] == WIDE_COUNT


def test_the_shipped_transform_declines_a_cross_section_below_its_declared_floor(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The eight-name fixture panel is below `min_cross_section`, and the shipped spec says so
    rather than producing numbers whose winsorization was a function of how many names happened
    to be scored. That is the floor doing its job on a real panel, not a synthetic vector."""
    result = apply_factor_transform(
        _compute(store, panel), CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )

    assert dict(result.coverage_census())["insufficient_cross_section"] == len(panel.securities)
    assert result.values() == {}


def test_a_filled_hole_reaches_storage_marked_as_imputed_and_not_as_processed(
    wide_store: PanelStore,
) -> None:
    """The last hop of the separation claim: an imputed number is distinguishable from a measured
    one **on the stored partition**, not only in memory.

    Driven through the shipped transform, whose `input_missing` action is
    `fill_cross_sectional_median`, against a cross section where one name is outside the universe
    and therefore excluded rather than filled -- so both non-`computed` outcomes are on one
    partition and each carries the raw code that produced it.
    """
    subjects = _wide_subjects()
    excluded = subjects[0]
    raw = compute_factor(
        wide_store,
        WIDE_DEFINITION,
        as_of=WIDE_AS_OF,
        subjects=subjects,
        universe=frozenset(subjects) - {excluded},
        requirements={WIDE_DATASET: _wide_requirement()},
        code_commit=COMMIT,
        built_at=BUILT_AT,
        evaluators={
            WIDE_DEFINITION.qualified_key: lambda window: window.series(WIDE_DATASET, "score")[-1]
        },
    )
    result = apply_factor_transform(
        raw, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    write_processed_factor_panels(wide_store, [result])

    stored = load_processed_factor_observations(
        wide_store, WIDE_DEFINITION, CROSS_SECTION_STANDARD, years=(YEAR,), as_of=WIDE_AS_OF
    )
    by_subject: dict[str, ProcessedFactorObservation] = {item.subject: item for item in stored}

    assert by_subject[excluded].coverage == "source_not_computed"
    assert by_subject[excluded].source_coverage == "not_in_universe"
    assert by_subject[excluded].value is None
    assert {item.coverage for item in stored} == {"processed", "source_not_computed"}
    assert dict(result.coverage_census())["imputed"] == 0


def test_an_input_missing_hole_is_imputed_with_the_processed_median_and_stored_as_such(
    wide_store: PanelStore,
) -> None:
    """The fill branch on a real partition, with the imputed number asserted against the median
    of the processed cross section rather than against "not None".

    The hole is `HOLED_SUBJECT`, a name with every session present and the last one's cell null,
    which is what makes it `input_missing` rather than `insufficient_history` -- and the shipped
    transform's actions for those two differ, so a fixture that produced the wrong one would have
    exercised the exclusion branch under the fill branch's name.
    """
    subjects = (*_wide_subjects(), HOLED_SUBJECT)
    raw = compute_factor(
        wide_store,
        WIDE_DEFINITION,
        as_of=WIDE_AS_OF,
        subjects=subjects,
        universe=frozenset(subjects),
        requirements={WIDE_DATASET: _wide_requirement()},
        code_commit=COMMIT,
        built_at=BUILT_AT,
        evaluators={
            WIDE_DEFINITION.qualified_key: lambda window: window.series(WIDE_DATASET, "score")[-1]
        },
    )
    result = apply_factor_transform(
        raw, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    write_processed_factor_panels(wide_store, [result])

    stored = load_processed_factor_observations(
        wide_store, WIDE_DEFINITION, CROSS_SECTION_STANDARD, years=(YEAR,), as_of=WIDE_AS_OF
    )
    by_subject = {item.subject: item for item in stored}
    measured = sorted(result.measured_values().values())

    assert by_subject[HOLED_SUBJECT].coverage == "imputed"
    assert by_subject[HOLED_SUBJECT].source_coverage == "input_missing"
    assert by_subject[HOLED_SUBJECT].value == pytest.approx(_median(measured))
    assert result.statistics.imputed_count == 1
    assert result.statistics.participant_count == WIDE_COUNT
    assert HOLED_SUBJECT not in result.measured_values()
    assert HOLED_SUBJECT in result.values()


# --- what the stored manifest row actually says ---------------------------------------------------
#
# Everything above this line asserts on objects `apply_factor_transform` returned, or on the nine
# processed columns `load_processed_factor_observations` decodes. The twenty-three columns below
# are stored by `transform_manifest_batch` and reassembled by nothing -- `load_factor_transform_
# manifests` deliberately does not put them back on a `FactorTransformManifest` -- so until this
# section existed the suite asserted their *names* and never once read a value.
#
# Measured with a falsifier injected at the two batch builders, one column at a time, over the
# four files that cover this issue: with these four tests deselected, **all twenty-four**
# (the twenty-three plus `transform_manifest_id` on the processed rows) came back
# `250 passed` -- twenty-four falsified partitions, zero red tests. With them selected, all
# twenty-four go red, under each of three falsifications: `+1000` / `+1000.0` /
# `"zzz_falsified"`, the same written as a constant, and the constant written over the nulls
# too. That last one is why the four builds below are chosen the way they are -- see
# `MAD_RANK_PROBE`.


_UNREASSEMBLED_MANIFEST_COLUMNS: Final[tuple[str, ...]] = TRANSFORM_MANIFEST_DATA_COLUMNS[11:]
"""Every stored transform-manifest column that no read path rebuilds an object out of.

The head eleven are covered by `_transform_manifest_from_row`'s own identity self-check, which is
why they were the columns a falsification already failed on. These are the rest: the declared
policy, the census and the statistics -- exactly the columns
`TRANSFORM_MANIFEST_DATA_COLUMNS`, `PROCESSED_CENSUS_COLUMNS` and
`test_the_stored_statistics_columns_make_a_declared_winsorization_falsifiable` justify at length
and nothing read. Taken as a slice so that a twenty-fourth arriving with no expectation fails
`_assert_the_partition_says_what_the_transform_measured`'s first line rather than passing
silently.
"""


def _assert_the_partition_says_what_the_transform_measured(
    store: PanelStore,
    definition: FactorDefinition,
    result: ProcessedFactorPanel,
    expected: dict[str, object],
) -> None:
    """Read this build's manifest row off the partition and hold every column to a value.

    Three bindings:

    1. **Every one of the twenty-four** columns above is held to an expected value, by an
       equality on the key set rather than by a list somebody keeps in step -- a twenty-fifth
       arriving with no expectation fails on the first line. It was twenty-three until
       `V2-P3-018` gave the missing-value policy a fifth code and therefore a fifth stored
       action column.
    2. **The processed rows' own `transform_manifest_id`** must be this build's. It is the one
       stored column no read path checks: `_processed_observation_from_row` decodes
       it with `str()`, so a wrong one round-trips cleanly and points every row at a manifest the
       partition may not hold.
    3. **The census columns are recounted against the processed rows in the other partition.**
       This is what makes them falsifiable rather than merely asserted: a census column and the
       rows it counts are written by two different functions out of two different objects, so a
       build that stored one and not the other -- `PROCESSED_CENSUS_COLUMNS`' whole argument --
       shows up as a disagreement between two partitions rather than as a number nobody compared
       to anything.
    """
    assert len(_UNREASSEMBLED_MANIFEST_COLUMNS) == 24
    assert set(expected) == set(_UNREASSEMBLED_MANIFEST_COLUMNS)
    manifest_id = result.manifest.transform_manifest_id
    rows = store.query(
        factor_transform_manifest_dataset(definition),
        year=YEAR,
        columns=[SUBJECT_COLUMN_NAME, *_UNREASSEMBLED_MANIFEST_COLUMNS],
    )
    stored = {
        str(row[0]): dict(zip(_UNREASSEMBLED_MANIFEST_COLUMNS, row[1:], strict=True))
        for row in rows
    }
    assert manifest_id in stored, sorted(stored)
    for name, want in expected.items():
        found = stored[manifest_id][name]
        assert found == (pytest.approx(want) if isinstance(want, float) else want), name

    processed = store.query(
        processed_factor_dataset(definition),
        year=YEAR,
        columns=["transform_id", "transform_manifest_id", "coverage"],
    )
    mine = [row for row in processed if row[0] == result.spec.transform_id]
    assert mine
    assert {row[1] for row in mine} == {manifest_id}
    census = dict.fromkeys(PROCESSED_COVERAGE_ORDER, 0)
    for row in mine:
        census[str(row[2])] += 1
    assert census == {code: expected[f"census_{code}"] for code in PROCESSED_COVERAGE_ORDER}


def _wide_raw(
    store: PanelStore,
    subjects: tuple[str, ...],
    universe: frozenset[str],
    *,
    score: Any = None,
) -> FactorPanel:
    """`compute_factor` over the wide probe partition, with the evaluator left substitutable.

    The `score` hook exists for one case only -- a cross section where every name carries the
    same number, which is the only way to reach `degenerate_cross_section` on a partition whose
    scores are distinct by construction.
    """
    return compute_factor(
        store,
        WIDE_DEFINITION,
        as_of=WIDE_AS_OF,
        subjects=subjects,
        universe=universe,
        requirements={WIDE_DATASET: _wide_requirement()},
        code_commit=COMMIT,
        built_at=BUILT_AT,
        evaluators={
            WIDE_DEFINITION.qualified_key: score
            or (lambda window: window.series(WIDE_DATASET, "score")[-1])
        },
    )


MAD_RANK_PROBE: Final[FactorTransformSpec] = FactorTransformSpec(
    key="probe_mad_rank",
    version=2,
    winsorization=WinsorizationPolicy(method="mad", mad_scale=3.0),
    standardization="rank",
    missing_values=MissingValuePolicy(
        not_in_universe="refuse",
        insufficient_history="fill_neutral",
        ambiguous_filing="refuse",
        input_missing="exclude",
        undefined_value="fill_cross_sectional_median",
    ),
    min_cross_section=50,
)
"""A second shipped-shape transform that differs from `CROSS_SECTION_STANDARD` in **every**
stored policy column, and whose five missing-value columns use all four declarable actions.

Both properties are deliberate and are what makes the assertions below falsify a constant rather
than only a perturbation. A column every test expects `"exclude"` in is one a build could write
`"exclude"` into unconditionally; here `missing_not_in_universe` is `"refuse"`,
`missing_insufficient_history` is `"fill_neutral"`, `missing_ambiguous_filing` is `"refuse"`,
`missing_input_missing` is `"exclude"` and `missing_undefined_value` is
`"fill_cross_sectional_median"`, so no single constant satisfies this row and
`CROSS_SECTION_STANDARD`'s at once, and every one of the five differs from its counterpart there.
Same for `mad` against `quantile`, `rank` against `zscore`, `mad_scale=3.0` against two
quantiles, and `50` against `100`.

`V2-P3-018` added the fifth column and `MissingValueAction` has only four members, so exactly one
pair here must repeat -- it is `not_in_universe` and `ambiguous_filing`, at stored positions 0 and
2, chosen non-adjacent so a transposition of neighbouring columns is still visible.
"""


def test_the_stored_policy_census_and_statistics_are_the_ones_this_build_actually_used(
    wide_store: PanelStore,
) -> None:
    """The shipped transform, with one name outside the universe and one hole to fill.

    Every number here is reproducible by hand from `_wide_score`: 119 participants (`000001.SZ`
    is out of the universe), a 1% winsorization at `(119 - 1) * 0.01 = 1.18` and `* 0.99 =
    116.82`, so the bounds interpolate to 2.18 and 117.82 and clip two names at each end, and the
    winsorized mean and population deviation are 60.0 and 34.312284. An independent
    reimplementation of the quantile rule, the clip and the deviation agrees with all seven.
    """
    subjects = (*_wide_subjects(), HOLED_SUBJECT)
    excluded = subjects[0]
    raw = _wide_raw(wide_store, subjects, frozenset(subjects) - {excluded})
    result = apply_factor_transform(
        raw, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    write_processed_factor_panels(wide_store, [result])

    _assert_the_partition_says_what_the_transform_measured(
        wide_store,
        WIDE_DEFINITION,
        result,
        {
            "winsorization_method": "quantile",
            "winsorization_lower_quantile": 0.01,
            "winsorization_upper_quantile": 0.99,
            "winsorization_mad_scale": None,
            "standardization_method": "zscore",
            "min_cross_section": 100,
            "missing_not_in_universe": "exclude",
            "missing_insufficient_history": "exclude",
            "missing_ambiguous_filing": "exclude",
            "missing_input_missing": "fill_cross_sectional_median",
            "missing_undefined_value": "exclude",
            "census_processed": 119,
            "census_imputed": 1,
            "census_source_not_computed": 1,
            "census_insufficient_cross_section": 0,
            "census_degenerate_cross_section": 0,
            "participant_count": 119,
            "winsorized_low_count": 2,
            "winsorized_high_count": 2,
            "imputed_count": 1,
            "lower_bound": 2.18,
            "upper_bound": 117.82,
            "location": 60.0,
            "scale": 34.312284,
        },
    )


def test_the_stored_policy_columns_are_the_declared_policy_and_not_a_default(
    wide_store: PanelStore,
) -> None:
    """`MAD_RANK_PROBE` end to end, because a policy column is only falsifiable against a second
    policy.

    This is the row that makes `TRANSFORM_MANIFEST_DATA_COLUMNS`' claim -- the stored policy is
    "a projection of `transform_id`" -- mean something on a partition: a reader who cannot
    resolve the `transform_id` finds `mad`, `3.0`, `rank`, `50` and four different missing-value
    actions here, and finds `quantile`, `0.01`/`0.99`, `zscore`, `100` in the test above, so the
    column is carrying the declaration rather than a shape.

    The statistics are the mad rule's own: the median of `0..118, 1000` is 59.5, its median
    absolute deviation is 30.0, so the interval is `59.5 +/- 90.0` and clips exactly the one
    outlier at the top and nothing at the bottom. `rank` estimates no location and no scale, so
    both are stored null -- which is the pair that would be silently plausible if nobody read
    them.
    """
    subjects = (*_wide_subjects(), HOLED_SUBJECT)
    raw = _wide_raw(wide_store, subjects, frozenset(subjects))
    result = apply_factor_transform(raw, MAD_RANK_PROBE, code_commit=COMMIT, built_at=BUILT_AT)
    write_processed_factor_panels(wide_store, [result])

    _assert_the_partition_says_what_the_transform_measured(
        wide_store,
        WIDE_DEFINITION,
        result,
        {
            "winsorization_method": "mad",
            "winsorization_lower_quantile": None,
            "winsorization_upper_quantile": None,
            "winsorization_mad_scale": 3.0,
            "standardization_method": "rank",
            "min_cross_section": 50,
            "missing_not_in_universe": "refuse",
            "missing_insufficient_history": "fill_neutral",
            "missing_ambiguous_filing": "refuse",
            "missing_input_missing": "exclude",
            "missing_undefined_value": "fill_cross_sectional_median",
            "census_processed": WIDE_COUNT,
            "census_imputed": 0,
            "census_source_not_computed": 1,
            "census_insufficient_cross_section": 0,
            "census_degenerate_cross_section": 0,
            "participant_count": WIDE_COUNT,
            "winsorized_low_count": 0,
            "winsorized_high_count": 1,
            "imputed_count": 0,
            "lower_bound": -30.5,
            "upper_bound": 149.5,
            "location": None,
            "scale": None,
        },
    )


def test_a_degenerate_cross_section_stores_its_own_census_and_not_an_empty_one(
    wide_store: PanelStore,
) -> None:
    """`PROCESSED_CENSUS_COLUMNS`' stated reason, driven: "a transform whose cross section was
    too thin produces a partition full of nulls, which is byte-indistinguishable in storage from
    a transform that ran and imputed nothing -- unless the counts are stored".

    Here the cross section is not thin, it is flat: 120 names all carrying 7.0, so the
    winsorization clips nobody, the z-score finds a zero deviation and every row is
    `degenerate_cross_section` with no value. The stored `value` column is therefore identical to
    the one the *next* test's build writes, and only `census_degenerate_cross_section == 120`
    against `census_insufficient_cross_section == 120` tells the two apart.
    """
    subjects = _wide_subjects()
    raw = _wide_raw(wide_store, subjects, frozenset(subjects), score=lambda window: 7.0)
    result = apply_factor_transform(
        raw, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    write_processed_factor_panels(wide_store, [result])

    _assert_the_partition_says_what_the_transform_measured(
        wide_store,
        WIDE_DEFINITION,
        result,
        {
            "winsorization_method": "quantile",
            "winsorization_lower_quantile": 0.01,
            "winsorization_upper_quantile": 0.99,
            "winsorization_mad_scale": None,
            "standardization_method": "zscore",
            "min_cross_section": 100,
            "missing_not_in_universe": "exclude",
            "missing_insufficient_history": "exclude",
            "missing_ambiguous_filing": "exclude",
            "missing_input_missing": "fill_cross_sectional_median",
            "missing_undefined_value": "exclude",
            "census_processed": 0,
            "census_imputed": 0,
            "census_source_not_computed": 0,
            "census_insufficient_cross_section": 0,
            "census_degenerate_cross_section": WIDE_COUNT,
            "participant_count": WIDE_COUNT,
            "winsorized_low_count": 0,
            "winsorized_high_count": 0,
            "imputed_count": 0,
            "lower_bound": 7.0,
            "upper_bound": 7.0,
            "location": None,
            "scale": None,
        },
    )


def test_a_cross_section_below_the_floor_stores_the_count_that_says_so(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The other half of the pair, on the eight-name fixture panel and the shipped floor of 100.

    `participant_count == 8` beside `census_insufficient_cross_section == 8` is the whole
    disclosure: the build saw eight securities, declined to standardize them, and said which of
    the two whole-panel reasons it was. Every bound and both estimators are null, because there
    is no interval and nothing was estimated -- and a build that stored zeros there instead would
    read as a winsorization that clipped at 0.0.
    """
    raw = _compute(store, panel)
    result = apply_factor_transform(
        raw, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=BUILT_AT
    )
    write_processed_factor_panels(store, [result])

    _assert_the_partition_says_what_the_transform_measured(
        store,
        REVERSAL_1D,
        result,
        {
            "winsorization_method": "quantile",
            "winsorization_lower_quantile": 0.01,
            "winsorization_upper_quantile": 0.99,
            "winsorization_mad_scale": None,
            "standardization_method": "zscore",
            "min_cross_section": 100,
            "missing_not_in_universe": "exclude",
            "missing_insufficient_history": "exclude",
            "missing_ambiguous_filing": "exclude",
            "missing_input_missing": "fill_cross_sectional_median",
            "missing_undefined_value": "exclude",
            "census_processed": 0,
            "census_imputed": 0,
            "census_source_not_computed": 0,
            "census_insufficient_cross_section": len(panel.securities),
            "census_degenerate_cross_section": 0,
            "participant_count": len(panel.securities),
            "winsorized_low_count": 0,
            "winsorized_high_count": 0,
            "imputed_count": 0,
            "lower_bound": None,
            "upper_bound": None,
            "location": None,
            "scale": None,
        },
    )


# --- the guards on the way out --------------------------------------------------------------------


def test_a_write_of_a_panel_whose_spec_did_not_produce_it_is_refused_and_stores_nothing(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`ProcessedFactorPanel` is a public frozen dataclass, and the write boundary now says so.

    Measured before `_refuse_a_processed_panel_that_does_not_own_its_rows` existed, on a pair of
    probe specs differing in every stored policy column: the write was accepted and the stored
    manifest row read back as
    `('ftm_3dbe...', 'ftx_bea6...', 'probe_a', 'rank', 'quantile', 1)` -- the first transform's
    `transform_id` and `transform_key` beside the second's `standardization_method` and
    `winsorization_method`, over a partition of the first transform's z-scores. The identity
    self-check on the way back could not see it, because the ten head columns it reassembles were
    consistent with each other.

    The second assertion is the one that makes the refusal worth having: every guard runs before
    the first write, so a refused call leaves no partition at all.
    """
    raw = _compute(store, panel)
    write_factor_panels(store, [raw])
    result = _apply(raw)

    with pytest.raises(FactorEngineError, match="does not describe the transform and factor"):
        write_processed_factor_panels(store, [dataclasses.replace(result, spec=RANK_SPEC)])

    assert store.query(PROCESSED, year=YEAR, columns=[SUBJECT_COLUMN_NAME]) == []
    assert store.query(TRANSFORM_MANIFESTS, year=YEAR, columns=[SUBJECT_COLUMN_NAME]) == []


def test_two_panels_of_one_build_wearing_two_spec_labels_are_still_one_application(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """`_refuse_two_applications_of_one_transform_at_one_as_of` keyed on the *spec* and guarded
    rows filed under the *manifest*, so relabelling one panel's spec split the key.

    Measured before the fix: this write was accepted, the manifest partition came back holding
    two rows under one `transform_manifest_id`, and `load_processed_factor_observations` returned
    sixteen rows for an eight-name cross section -- two answers to one question, which is what
    that function's docstring says it exists to prevent.
    """
    raw = _compute(store, panel)
    write_factor_panels(store, [raw])
    result = _apply(raw)

    with pytest.raises(FactorEngineError, match="more than one application"):
        write_processed_factor_panels(store, [result, dataclasses.replace(result, spec=RANK_SPEC)])

    assert store.query(TRANSFORM_MANIFESTS, year=YEAR, columns=[SUBJECT_COLUMN_NAME]) == []


def test_a_processed_partition_can_be_written_when_the_raw_one_never_was(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The wider edge of the disclosed dangling-pointer limitation, which the entry first missed.

    `KNOWN_STORAGE_LIMITATIONS.a_derived_partition_may_outlive_the_build_its_rows_point_at` said
    `supersedes` is the only path to a processed row naming a build no partition holds, so the
    pointer "requires a deliberate act that already names the build it is destroying". It does
    not: **nothing requires the raw partition to have been written at all.** Write order is not
    enforced across datasets, because this plane has no cross-dataset integrity -- which is the
    entry's own first sentence, and the reason the narrower bound was wrong rather than merely
    incomplete.

    Asserted rather than left in prose for the reason the `supersedes` reproduction already is:
    a disclosure whose only reader is a docstring is one the suite has no opinion about. This
    one also fails in the useful direction -- close the hole and the second assertion goes red
    with the disclosure that has to come out beside it.
    """
    raw = _compute(store, panel)
    result = _apply(raw)

    write_processed_factor_panels(store, [result])
    stored = load_processed_factor_observations(
        store, REVERSAL_1D, _spec(), years=(YEAR,), as_of=MID_WINDOW
    )

    assert len(stored) == len(panel.securities)
    assert {item.source_manifest_id for item in stored} == {raw.manifest.manifest_id}
    with pytest.raises(FactorEngineError, match="cannot be read at"):
        load_factor_observations(store, REVERSAL_1D, years=(YEAR,), as_of=MID_WINDOW)

    disclosure = {item.code: item.detail for item in KNOWN_STORAGE_LIMITATIONS}
    assert (
        "nothing requires the raw partition to have been written at all"
        in (disclosure["a_derived_partition_may_outlive_the_build_its_rows_point_at"])
    )
