"""A stored factor cross section held against the build manifest that addresses it (`V2-P3-019`).

## The measurement this file exists for

The product acceptance of `P3` edited one Parquet file behind the store -- sixteen values, sign
flipped -- deleted `runtime/experiments` so no previously stored answer could contradict the new
one, and ran the real `openalpha factor run`. It got:

| | honest store | tampered store |
|---|---|---|
| `mean_ic` (raw) | `+1.0` | `-1.0` |
| `mean_spread` | `+0.00812` | `-0.00794` |
| `experiment_id` | one string | **the same string** |
| exit code | 0 | 0 |
| refusal | -- | **none** |

Three facts made that possible and each is closed by a different piece of this change:

1. **A build manifest addressed everything about a build except its answers.** `subject_digest`,
   `universe_digest`, `input_partition_hash` and the five `census_*` counts were byte-identical
   across the two stores. `FactorBuildManifest.observation_digest` is the field that was missing,
   and `panel_factors._refuse_rows_that_are_not_the_answers_their_manifest_addresses` is the read
   that uses it.
2. **The one guard that could have fired was stateful.** The document store refuses "two
   `content_digest`s under one `experiment_id`", so it only ever fires on a machine that already
   ran the honest version -- which is why deleting `runtime/experiments` was enough, and why a
   fresh machine or a first run was never protected at all. A seal on the panel is stateless.
3. **`panel doctor` could not reach the plane.** It refused `--dataset factor_obs_reversal_1d_v1`
   by name, listing the fifteen fetched datasets. See `tests/unit/test_panel_doctor_rules.py::
   test_a_derived_partition_gets_a_bound_of_none_on_the_record_rather_than_a_refusal` and, for
   what the report now says about a tampered one, `test_a_tampered_factor_partition_is_a_
   blocking_finding_on_the_report` at the foot of this file.

## The tampers, and what each one is for

Each is applied to a real Parquet partition on disk, through DuckDB, with the catalog left
alone -- which is the whole point: somebody who edits a file behind the store does not update the
catalog either.

| tamper | what it models | what stops it |
|---|---|---|
| every value's sign flipped | a corrupted or "corrected" column | the seal |
| one cell rewritten | a stray script writing to the wrong path | the seal |
| one row's coverage code restated | a hand-edited "fix" for a hole | the seal |
| one row replaced by a copy of another | a partial sync, count preserved | the seal |
| one row deleted | a truncated sync | `partition_row_count_mismatch` |
| the file truncated | an interrupted copy | `partition_file_unreadable` |

The last two are asserted against **their own** codes rather than against the seal. A test that
accepted any refusal would pass on a build where the seal never ran -- the storage plane's
row-count check would be doing all the work and nobody would know -- and the fourth row exists
for the same reason from the other side: it keeps `count(*)` identical, so no cheaper check can
claim the credit for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import duckdb
import pytest
from panel_fixtures import YEAR, GeneratedPanel, generate_panel, write_generated_panel

from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.factor import (
    FactorDefinition,
    FactorField,
    cross_section_digest,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import (
    FACTOR_SEAL_CHECK,
    PanelHealthReport,
    panel_health_report,
)
from openalpha_cn.panel_factors import (
    REVERSAL_1D,
    FactorEngineError,
    compute_factor,
    factor_manifest_dataset,
    factor_observation_dataset,
    load_factor_observations,
    write_factor_panels,
)
from openalpha_cn.panel_gate import DependencyRequest, require_datasets
from openalpha_cn.panel_ingest import daily_basic_requirement, daily_requirement

COMMIT: Final[str] = "a1b2c3d"
AS_OF: Final[datetime] = datetime(2026, 1, 16, 4, 0, tzinfo=UTC)
BUILT_AT: Final[datetime] = datetime(2026, 1, 16, 5, 0, tzinfo=UTC)
INPUT_STALENESS_BOUND: Final[timedelta] = timedelta(days=5)
"""One trading week. The engine refuses a waived bound, so the requirement states one;
`tests/integration/panel/test_factor_engine.py` argues the number in full."""

REVERSAL = REVERSAL_1D
"""The shipped factor and its shipped evaluator, so the partition this file tampers with is the
one an operator would actually have on disk -- `factor_obs_reversal_1d_v1`, which is the file the
product acceptance edited."""

OBSERVATIONS: Final[str] = factor_observation_dataset(REVERSAL)
MANIFESTS: Final[str] = factor_manifest_dataset(REVERSAL)


@pytest.fixture
def panel() -> GeneratedPanel:
    return generate_panel(shapes=("daily.close_moves_between_sessions",))


@pytest.fixture
def store(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    built = PanelStore(tmp_path / "panel")
    write_generated_panel(built, panel)
    computed = compute_factor(
        built,
        REVERSAL,
        as_of=AS_OF,
        subjects=panel.securities,
        universe=frozenset(panel.securities),
        requirements={
            DAILY_DATASET: daily_requirement(
                panel.calendar(),
                years=(YEAR,),
                as_of=AS_OF,
                max_staleness=INPUT_STALENESS_BOUND,
            )
        },
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )
    write_factor_panels(built, [computed])
    return built


def _partition(store: PanelStore, dataset: str) -> Path:
    return store.root / dataset / str(YEAR) / "data.parquet"


def _rewrite(path: Path, projection: str) -> None:
    """Replace a stored partition with `projection` over its own rows, catalog untouched.

    The template is given `{rows}` -- the partition's rows numbered from zero in `rn`, which every
    projection drops -- and `{src}` for the cases that need a scalar subquery over the original.
    So each tamper below is one clause rather than a re-derivation of twelve columns.
    """
    numbered = f"SELECT *, row_number() OVER () - 1 AS rn FROM read_parquet('{path.as_posix()}')"
    scratch = path.with_suffix(".tampered.parquet")
    connection = duckdb.connect()
    try:
        connection.execute(
            f"COPY ({projection.format(rows=numbered, src=path.as_posix())}) "
            f"TO '{scratch.as_posix()}' (FORMAT PARQUET)"
        )
    finally:
        connection.close()
    scratch.replace(path)


def _row_count(path: Path) -> int:
    connection = duckdb.connect()
    try:
        answer = connection.execute(
            f"SELECT count(*) FROM read_parquet('{path.as_posix()}')"
        ).fetchone()
    finally:
        connection.close()
    assert answer is not None
    return int(answer[0])


def _read(store: PanelStore) -> tuple[object, ...]:
    return load_factor_observations(store, REVERSAL, years=(YEAR,), as_of=AS_OF)


# --- the honest store, so every refusal below is measured against something -----------------------


def test_the_honest_store_reads_back_and_its_values_are_the_ones_that_were_computed(
    store: PanelStore, panel: GeneratedPanel
) -> None:
    """The baseline the rest of this file is measured against.

    Without it a seal that refused *everything* would make every test here green, which is
    `V2-P1-013`'s "assert blocking, not an empty success" with the sign reversed. The values are
    asserted as numbers rather than as "not None" for the reason `test_factor_engine.py`'s own
    docstring gives: a proof that only checks existence hangs the claim on a free parameter.
    """
    observations = _read(store)
    computed = [item for item in observations if item.coverage == "computed"]  # type: ignore[attr-defined]

    assert len(observations) == len(panel.securities)
    assert computed
    assert all(item.value is not None and item.value != 0.0 for item in computed)  # type: ignore[attr-defined]


# --- the four tampers nothing used to catch -------------------------------------------------------


_EVERY_VALUE_FLIPPED: Final[str] = "SELECT * EXCLUDE (rn) REPLACE (-value AS value) FROM ({rows})"
_ONE_CELL_REWRITTEN: Final[str] = (
    "SELECT * EXCLUDE (rn) REPLACE (CASE WHEN rn = 0 THEN 99.0 ELSE value END AS value) "
    "FROM ({rows})"
)
_ONE_COVERAGE_CODE_RESTATED: Final[str] = (
    "SELECT * EXCLUDE (rn) REPLACE ("
    "CASE WHEN rn = 0 THEN 'input_missing' ELSE coverage END AS coverage, "
    "CASE WHEN rn = 0 THEN NULL ELSE value END AS value) FROM ({rows})"
)
_ONE_ROW_REPLACED_BY_A_COPY: Final[str] = (
    "SELECT * EXCLUDE (rn) FROM ({rows}) WHERE rn <> 0 "
    "UNION ALL SELECT * EXCLUDE (rn) REPLACE ("
    "(SELECT min(subject) FROM read_parquet('{src}')) AS subject) FROM ({rows}) WHERE rn = 1"
)


@pytest.mark.parametrize(
    ("name", "projection"),
    [
        ("every_value_flipped", _EVERY_VALUE_FLIPPED),
        ("one_cell_rewritten", _ONE_CELL_REWRITTEN),
        ("one_coverage_code_restated", _ONE_COVERAGE_CODE_RESTATED),
        ("one_row_replaced_by_a_copy", _ONE_ROW_REPLACED_BY_A_COPY),
    ],
)
def test_a_row_level_edit_behind_the_store_is_refused_by_the_build_it_claims_to_be(
    store: PanelStore, name: str, projection: str
) -> None:
    """The Critical, one tamper at a time.

    `match=` is narrow enough to say *which* rule refused: "addresses the cross section" is only
    ever produced by the digest comparison. A bare `pytest.raises(FactorEngineError)` would also
    pass if the read had been refused by the row-count check, by the coverage decoder or by a
    typo in the dataset name -- and every tamper here preserves the row count precisely so that no
    cheaper check can claim the credit.

    `one_coverage_code_restated` is the case a digest over values alone would miss half of: it
    moves a `computed` row to `input_missing`, and because exactly the `computed` code carries a
    value the edit has to move the value too. The strictly harder version -- one valueless code
    restated as another -- is measured on the primitive in
    `tests/unit/domain/test_factor.py::test_the_address_moves_for_every_edit_a_row_level_tamper_can_make`,
    where a cross section can be built by hand.
    """
    before = _row_count(_partition(store, OBSERVATIONS))
    _rewrite(_partition(store, OBSERVATIONS), projection)

    assert _row_count(_partition(store, OBSERVATIONS)) == before, name
    with pytest.raises(FactorEngineError, match="addresses the cross section"):
        _read(store)


# --- the two the storage plane already caught, asserted against their own codes -------------------


def test_a_deleted_row_is_refused_by_the_row_count_check_and_not_by_the_seal(
    store: PanelStore,
) -> None:
    """A tamper the layer below already stops, pinned to *that* layer's code.

    `panel/store.py` reads a partition's row count out of the Parquet footer on every readiness
    assessment and compares it against the coverage record, so deleting a row is refused before
    the seal is reached. Asserting that here rather than accepting any refusal is what keeps this
    file honest about which check does what -- and it is why the fourth tamper above had to be
    built to preserve the count.
    """
    _rewrite(_partition(store, OBSERVATIONS), "SELECT * EXCLUDE (rn) FROM ({rows}) WHERE rn <> 0")

    with pytest.raises(FactorEngineError, match="partition_row_count_mismatch"):
        _read(store)


def test_a_truncated_file_is_refused_as_an_unreadable_partition(store: PanelStore) -> None:
    """The same argument for the coarsest damage there is: half a Parquet file is not a
    partition, and `partition_file_unreadable` is the code that says so."""
    partition = _partition(store, OBSERVATIONS)
    raw = partition.read_bytes()
    partition.write_bytes(raw[: len(raw) // 2])

    with pytest.raises(FactorEngineError, match="partition_file_unreadable"):
        _read(store)


# --- answers with no build behind them ------------------------------------------------------------


def test_answers_whose_build_no_manifest_claims_are_refused(store: PanelStore) -> None:
    """Rows filed under a `manifest_id` the manifest partition does not hold.

    The state `V2-P3-002`'s manifest exists to make impossible, reached from the other side: the
    numbers are there, they are internally consistent, and nothing stored says what parameters
    produced them. A distinct refusal from a broken digest because the remedy is different -- a
    broken digest means the rows moved, this means the account of them is gone.
    """
    _rewrite(
        _partition(store, OBSERVATIONS),
        "SELECT * EXCLUDE (rn) REPLACE ('fmn_nobody' AS manifest_id) FROM ({rows})",
    )

    with pytest.raises(FactorEngineError, match="no visible manifest claims"):
        _read(store)


# --- what rewriting the stored digest costs the tamperer ------------------------------------------


def test_editing_the_stored_digest_to_match_the_tampered_rows_breaks_the_manifest_instead(
    store: PanelStore,
) -> None:
    """The reason `observation_digest` is a *hashed* field rather than a recorded one.

    A digest that sat outside `manifest_id` -- `FactorInputProvenance`'s "recorded but not
    addressed" arrangement -- would be a column rewritten in the same pass as the values it
    describes, leaving both partitions agreeing with themselves and every identity unmoved: the
    same hole, one table over. Inside `manifest_id` it cannot be, because
    `panel_factors._manifest_from_rows` re-derives the address from the stored row and refuses a
    disagreement. So the digest column is defended by the mechanism it defends.

    Measured rather than argued. The values are flipped **and** the digest column is rewritten to
    the address those flipped values actually hash to -- the best a tamperer holding both files
    can do -- and the read is still refused, under a different message, which is what says the
    second line of defence rather than the first is doing the work.
    """
    _rewrite(_partition(store, OBSERVATIONS), _EVERY_VALUE_FLIPPED)
    _rewrite(
        _partition(store, MANIFESTS),
        "SELECT * EXCLUDE (rn) REPLACE "
        f"('{_digest_of_stored_rows(store)}' AS observation_digest) FROM ({{rows}})",
    )

    with pytest.raises(FactorEngineError, match="reassembles to"):
        _read(store)


def _digest_of_stored_rows(store: PanelStore) -> str:
    """The address the tampered partition's rows hash to, computed the way the plane computes it.

    Through `cross_section_digest` rather than by re-deriving the canonical form here: a test that
    spelled the canonicalisation a second time would be asserting that two spellings agree, which
    is not the property under test.
    """
    partition = _partition(store, OBSERVATIONS)
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"SELECT subject, coverage, value FROM read_parquet('{partition.as_posix()}')"
        ).fetchall()
    finally:
        connection.close()
    return cross_section_digest(
        ((str(subject), str(coverage), value) for subject, coverage, value in rows), prefix="obs"
    )


# --- the same fact, seen by the health report and the dependency gate -----------------------------


def _report(store: PanelStore) -> PanelHealthReport:
    return panel_health_report(
        store, as_of=AS_OF, datasets=(OBSERVATIONS,), years=(YEAR,), calendar=None
    )


def test_an_honest_factor_partition_is_clean_and_carries_the_derived_cadence(
    store: PanelStore,
) -> None:
    """What `panel doctor --dataset factor_obs_reversal_1d_v1` says now, and what it used to.

    It used to say `'factor_obs_reversal_1d_v1' has no declared publication cadence` and exit 3,
    listing the fifteen fetched datasets -- a refusal on the *name*, so nothing past it ever ran
    and the whole plane was out of reach of the gate. Now the report reaches it: the bound is
    still `None`, because nothing publishes into a derived partition, and the difference is that
    the absence is stated on the record and the seal check runs.
    """
    report = _report(store)
    (health,) = report.datasets
    (seal,) = [check for check in report.cross_checks if check.name == FACTOR_SEAL_CHECK]

    assert report.is_clean
    assert health.freshness.cadence == "derived"
    assert health.freshness.max_staleness is None
    assert seal.ran and seal.finding_count == 0
    assert seal.datasets == (OBSERVATIONS,)


def test_a_tampered_factor_partition_is_a_blocking_finding_on_the_report(
    store: PanelStore,
) -> None:
    """The report's half of the answer, on the tamper the product acceptance actually made.

    Blocking rather than a warning, and naming the build rather than a count: a caller reading
    this has to know which `as_of` to rebuild. The finding also names the pair of datasets, since
    the evidence is in two partitions -- which is why this is a cross-check rather than a
    dimension of `dataset_health`.
    """
    _rewrite(_partition(store, OBSERVATIONS), _EVERY_VALUE_FLIPPED)
    report = _report(store)
    findings = [item for item in report.findings if item.code == "factor_seal_broken"]

    assert not report.is_clean
    assert len(findings) == 1
    assert findings[0].severity == "blocking"
    assert findings[0].datasets == (OBSERVATIONS, MANIFESTS)
    assert findings[0].count == 1
    assert findings[0].items and findings[0].items[0].startswith("fmn_")


def test_the_gate_refuses_a_tampered_factor_partition(store: PanelStore) -> None:
    """`data-check`'s deliverable is its exit code, so the block matters more than the finding.

    A cleared read of a tampered partition is the one failure this whole change is about: it
    succeeds and returns different numbers. The gate is asserted to be *blocked* and to name the
    seal code, rather than merely to be non-empty -- `DependencyClearance` refuses `bool()` and
    `len()` for exactly that reason.
    """
    _rewrite(_partition(store, OBSERVATIONS), _EVERY_VALUE_FLIPPED)
    clearance = require_datasets(
        store,
        DependencyRequest(
            datasets=(OBSERVATIONS,),
            as_of=AS_OF,
            years=(YEAR,),
            sessions=(),
            calendar=None,
        ),
    )

    assert clearance.is_blocked
    assert "factor_seal_broken" in {block.code for block in clearance.blocks}
    assert clearance.cleared_or_none is None


def test_answers_with_no_manifest_are_a_blocking_finding_too(store: PanelStore) -> None:
    """The report's version of the orphan direction, which the loader reaches first.

    `load_factor_observations` refuses an orphaned build before it can look at anything else, so
    the "a manifest describes a build whose answers are gone" half of that rule is unreachable
    through the loader on a single-build store. The report has no such ordering: it collects both
    sides and reports the symmetric difference, so this one finding covers a build with rows and
    no manifest **and** a manifest with no rows, which is what the message says.
    """
    _rewrite(
        _partition(store, OBSERVATIONS),
        "SELECT * EXCLUDE (rn) REPLACE ('fmn_nobody' AS manifest_id) FROM ({rows})",
    )
    report = _report(store)
    findings = [item for item in report.findings if item.code == "factor_build_unaddressed"]

    assert not report.is_clean
    assert len(findings) == 1
    assert findings[0].severity == "blocking"
    assert set(findings[0].items) == {"fmn_nobody"} | {
        build for build in findings[0].items if build.startswith("fmn_") and build != "fmn_nobody"
    }
    assert findings[0].count == 2


def test_the_manifest_half_of_a_pair_is_reportable_on_its_own_and_runs_no_seal_check(
    store: PanelStore,
) -> None:
    """`panel doctor --dataset factor_manifest_reversal_1d_v1` used to be refused by name too.

    It is now a derived partition like its sibling and gets the same readiness verdict, and it
    deliberately runs **no** seal check: the pair is checked from the answers' side, and a report
    that ran it from both ends would file one broken seal twice under two dataset names.
    """
    report = panel_health_report(
        store, as_of=AS_OF, datasets=(MANIFESTS,), years=(YEAR,), calendar=None
    )
    (health,) = report.datasets
    (seal,) = [check for check in report.cross_checks if check.name == FACTOR_SEAL_CHECK]

    assert report.is_clean
    assert health.freshness.cadence == "derived"
    assert seal.datasets == ()
    assert seal.finding_count == 0


def test_a_missing_manifest_partition_is_reported_as_a_check_that_could_not_run(
    store: PanelStore,
) -> None:
    """ "I could not look" must not read as "I looked and it was fine".

    The answers are present and ready, and the partition that addresses them is gone -- so the
    seal cannot be evaluated at all. That is `check_unavailable` rather than a clean report or a
    fabricated `factor_seal_broken`, which is `_rebuild_check`'s own distinction between a verdict
    about the rows and the report saying it did not get to run.
    """
    _partition(store, MANIFESTS).unlink()
    report = panel_health_report(
        store, as_of=AS_OF, datasets=(OBSERVATIONS,), years=(YEAR,), calendar=None
    )
    (seal,) = [check for check in report.cross_checks if check.name == FACTOR_SEAL_CHECK]

    assert not report.is_clean
    assert not seal.ran
    assert {item.code for item in report.findings} == {"check_unavailable"}


TWO_INPUT_PROBE: Final[FactorDefinition] = FactorDefinition(
    key="seal_two_input_probe",
    version=1,
    family="quality",
    direction="higher_is_better",
    required_fields=(
        FactorField(dataset=DAILY_DATASET, column="close"),
        FactorField(dataset=DAILY_BASIC_DATASET, column="total_mv"),
    ),
    lookback_sessions=2,
    max_window_sessions=3,
    lookback_periods=None,
    max_window_periods=None,
)
"""A probe that reads **two** datasets, so its build stores two manifest rows.

The shipped `reversal_1d` reads one, so one build is one row and a manifest partition cannot hold
two accounts of it. That state is reachable only for a multi-input build -- which every shipped
statement factor is -- so the guard against it needs a fixture that has one."""

TWO_INPUT_MANIFESTS: Final[str] = factor_manifest_dataset(TWO_INPUT_PROBE)


@pytest.fixture
def two_input_store(tmp_path: Path, panel: GeneratedPanel) -> PanelStore:
    built = PanelStore(tmp_path / "two-input")
    write_generated_panel(built, panel)
    computed = compute_factor(
        built,
        TWO_INPUT_PROBE,
        as_of=AS_OF,
        subjects=panel.securities,
        universe=frozenset(panel.securities),
        requirements={
            DAILY_DATASET: daily_requirement(
                panel.calendar(),
                years=(YEAR,),
                as_of=AS_OF,
                max_staleness=INPUT_STALENESS_BOUND,
            ),
            DAILY_BASIC_DATASET: daily_basic_requirement(
                panel.calendar(),
                years=(YEAR,),
                as_of=AS_OF,
                max_staleness=INPUT_STALENESS_BOUND,
            ),
        },
        code_commit=COMMIT,
        built_at=BUILT_AT,
        evaluators={TWO_INPUT_PROBE.qualified_key: lambda window: 1.0},
    )
    write_factor_panels(built, [computed])
    return built


def test_a_build_filed_under_two_addresses_is_refused_rather_than_resolved(
    two_input_store: PanelStore,
) -> None:
    """One build has one address, and picking one of two would be the report deciding silently.

    The raw tier stores one manifest row per `(build, input partition)`, so a two-dataset build
    has two rows carrying one digest by construction. Rewriting one of them is a manifest
    partition edited behind the store, and taking the first would decide -- without saying so --
    which of two accounts of a build the answers are held to. So the check declines to look, which
    arrives as `check_unavailable`: "I could not look" rather than a verdict either way.
    """
    dataset = factor_observation_dataset(TWO_INPUT_PROBE)
    _rewrite(
        _partition(two_input_store, TWO_INPUT_MANIFESTS),
        "SELECT * EXCLUDE (rn) REPLACE ("
        "CASE WHEN rn = 0 THEN 'obs_elsewhere' ELSE observation_digest END AS observation_digest"
        ") FROM ({rows})",
    )
    report = panel_health_report(
        two_input_store, as_of=AS_OF, datasets=(dataset,), years=(YEAR,), calendar=None
    )
    (seal,) = [check for check in report.cross_checks if check.name == FACTOR_SEAL_CHECK]

    assert not report.is_clean
    assert not seal.ran
    assert seal.skipped_reason is not None
    assert "more than one observation_digest" in seal.skipped_reason
