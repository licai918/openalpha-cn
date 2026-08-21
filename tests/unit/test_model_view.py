"""The model faces' own rules: the tables, the boundaries, and the layer they sit on.

`tests/integration/test_model_interfaces.py` holds every behaviour a user can reach, from a
`CliRunner`, a `TestClient` or an `OpenAlphaSDK`, because that is the defect `V2-P4-021` exists to
avoid repeating. What is left for this file is the half a face cannot show: that its envelope
tables are **complete** rather than merely correct today, that the one rule it applies which
`backtest/` does not already own separates the cases it claims to, and that the contract keeping
this module out of `backtest/` responds.
"""

from __future__ import annotations

import ast
import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, get_args

import pytest
import walk_forward_fixtures
from import_linter_containment import contained_lint_imports

from openalpha_cn import cli
from openalpha_cn.api.app import MODEL_HTTP_STATUS
from openalpha_cn.backtest.alpha_baseline import BASELINE_FAMILY, BaselineScorePoint, FoldEvaluation
from openalpha_cn.backtest.alpha_tree import TREE_FAMILY
from openalpha_cn.backtest.walk_forward import LabelledPanel
from openalpha_cn.cli import MODEL_EXIT, PANEL_BUILD_TARGETS
from openalpha_cn.domain.alpha_model import AlphaModelDeclaration, TrainingSet, artifact_for
from openalpha_cn.domain.horizon import HorizonError, parse_horizon
from openalpha_cn.domain.labels import MINIMUM_LABEL_ZONE_OFFSET, LabelError
from openalpha_cn.domain.prediction_record import PredictionStanding
from openalpha_cn.domain.trading_calendar import TradingCalendarError
from openalpha_cn.feature_matrix import FeatureColumn
from openalpha_cn.model_view import (
    _OUTCOME_WINDOW_FAULTS,
    KNOWN_MODEL_VIEW_LIMITATIONS,
    MODEL_DATE_ZONE,
    MODEL_FAMILIES,
    MODEL_PANEL_DATASETS,
    MODEL_VIEW_LIMITATION_CODES,
    PREDICTION_STANDING_MEANINGS,
    EvaluationRequest,
    ModelEvaluation,
    ModelNotHeldError,
    ModelPanelUnreadableError,
    ModelRequestError,
    ModelRunBlockedError,
    ModelRunRequest,
    ModelViewError,
    feature_columns,
    model_evaluation_request,
    trainable_at,
)
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS
from openalpha_cn.panel_view import panel_store

ROOT: Final[Path] = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def labelled_panel_fixture() -> LabelledPanel:
    """`V2-P4-013`'s own corpus, joined -- twenty prediction days of four securities.

    The corpus this repository already has for walk-forward work rather than a second one built
    here, which is the point: `trainable_at` compares a `close_instant` the trading calendar
    computed against a supplied deadline, and a hand-assembled panel would let this test agree
    with itself about what a window closes at.
    """
    return walk_forward_fixtures.panel(aligned_from=walk_forward_fixtures.ALIGNED_FROM_OVERLAPPING)


PROBE: Final[Path] = ROOT / "src" / "openalpha_cn" / "backtest" / "_model_face_probe.py"


# --- the registry, bound to this file the way every other one is -------------------------------


def test_the_declared_limitations_are_exactly_these_nine() -> None:
    """`KNOWN_MODEL_VIEW_LIMITATIONS`' codes, as a set literal compared for equality.

    `tests/unit/test_known_limitation_registries.py`'s binding: every declared code has to appear
    as a string literal in executable test code, and equality rather than membership because a
    membership assertion is additive -- it can see a code that was renamed and never one that was
    removed.
    """
    assert {
        "a_resolved_feature_version_is_not_a_declared_one",
        "an_evaluation_registers_nothing_because_every_record_it_could_write_would_be_unwitnessed",
        "an_evaluation_writes_no_run_manifest_because_it_took_no_decision",
        "the_daily_fit_purges_and_does_not_embargo",
        "a_prediction_day_is_the_instants_own_zone_date_and_not_its_pricing_session",
        "the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_in_time_fit",
        "the_scored_ratio_floor_is_a_coverage_bar_and_never_a_quality_one",
        "no_hyperparameter_is_selected_by_anything_on_this_face",
        "a_neutralized_feature_column_is_refused_by_this_face",
    } == MODEL_VIEW_LIMITATION_CODES
    assert len(KNOWN_MODEL_VIEW_LIMITATIONS) == len(MODEL_VIEW_LIMITATION_CODES)


# --- the two envelope tables, complete rather than merely correct -------------------------------


def _fault_reasons() -> set[str]:
    """Every `reason` a `ModelViewError` subclass declares, read off the class hierarchy.

    Off the hierarchy rather than off a hand-written list, because the whole point of the two
    tables below is that a fault added to `model_view.py` with no row raises `KeyError` at a
    channel boundary rather than being mis-enveloped -- and a test that listed the faults by hand
    would go stale in exactly the same way the tables would.
    """
    seen: set[str] = set()
    pending = [ModelViewError]
    while pending:
        current = pending.pop()
        seen.add(current.reason)
        pending.extend(current.__subclasses__())
    return seen - {ModelViewError.reason}


def test_every_fault_this_module_can_raise_has_a_row_in_both_channel_tables() -> None:
    """`MODEL_EXIT` and `MODEL_HTTP_STATUS` cover the same reasons, and cover every fault.

    Both directions. A fault with no row is a `KeyError` at the boundary -- which is the
    *designed* failure and is loud -- but a row for a fault that no longer exists is silent, and
    is how a table comes to promise a status code for something that cannot happen.
    """
    reasons = _fault_reasons()

    assert reasons == {"bad_request", "panel_unreadable", "blocked", "not_held"}
    assert reasons <= set(MODEL_EXIT)
    assert reasons <= set(MODEL_HTTP_STATUS)
    assert set(MODEL_EXIT) == set(MODEL_HTTP_STATUS)
    assert set(MODEL_EXIT) - reasons == {"answered", "refused", "internal_error"}


@pytest.mark.parametrize(
    ("error", "exit_code", "status"),
    [
        (ModelRequestError, 3, 422),
        (ModelPanelUnreadableError, 1, 409),
        (ModelRunBlockedError, 1, 409),
        (ModelNotHeldError, 1, 404),
    ],
)
def test_each_fault_wears_the_envelope_its_remedy_asks_for(
    error: type[ModelViewError], exit_code: int, status: int
) -> None:
    """The rows themselves, so a reordering of either table is a diff rather than a surprise.

    `bad_request` is 3/422 because no amount of building repairs the question; the other three
    are 1/409 or 1/404 because the remedy is a build, a wider range, or a run that has not
    happened yet. `SHORTLIST_EXIT`'s reasoning, and the same numbers, so a CI job that already
    switches on them does not learn a sixth meaning.
    """
    assert int(MODEL_EXIT[error.reason]) == exit_code
    assert MODEL_HTTP_STATUS[error.reason] == status


def test_a_refused_run_is_not_a_fault_and_wears_its_own_row() -> None:
    """`refused` is in both tables and is **not** a `reason` any exception declares.

    A run refused by its declared coverage floor is this pipeline answering, not failing: the
    measurement, both sides of the bar and -- on a daily run -- the address the prediction was
    registered under are all on the verdict. Raising would have made "this model answered about
    too little of the market" indistinguishable, at a face, from "the panel could not be read",
    which is the collapse `V2-P4-023` refused inside the library and `V2-P4-033` refused at a
    surface.
    """
    assert "refused" not in _fault_reasons()
    assert int(MODEL_EXIT["refused"]) == 1
    assert MODEL_HTTP_STATUS["refused"] == 409
    assert MODEL_HTTP_STATUS["refused"] != MODEL_HTTP_STATUS["answered"]


def test_every_standing_this_repository_can_compute_has_a_pair_of_sentences() -> None:
    """`PREDICTION_STANDING_MEANINGS` against `PredictionStanding`'s own `Literal`.

    A fourth standing added to `domain/prediction_record.py` raises `KeyError` in
    `prediction_view` rather than rendering with no explanation at all, which is what this
    equality is for. Both halves are required to be non-empty, because the second one -- what the
    standing does **not** prove -- is the one a face is most likely to lose.
    """
    assert set(PREDICTION_STANDING_MEANINGS) == set(get_args(PredictionStanding))
    for proves, does_not in PREDICTION_STANDING_MEANINGS.values():
        assert proves.strip() and does_not.strip()


def test_the_forward_sentence_says_the_two_things_v2_p4_017_refuses_to_leave_out() -> None:
    """The unverifiable half, held to the words `domain/prediction_record.py` chose.

    That module states plainly that `predicted_at` is whatever the caller passed to `predict` and
    that *"none of this defends against whoever owns the disk"*. A rendering that dropped either
    would turn a single-user bookkeeping fact into what reads like an attestation, which is the
    one way this surface could undo the contract underneath it.
    """
    _proves, does_not = PREDICTION_STANDING_MEANINGS["forward"]

    assert "predicted_at is whatever the caller passed to predict" in does_not
    assert "nothing here defends against whoever owns the disk" in does_not
    assert "a timestamp somebody else controls" in does_not


# --- the prerequisites table -------------------------------------------------------------------


def test_every_dataset_this_face_reads_names_a_panel_build_target_that_exists() -> None:
    """`MODEL_PANEL_DATASETS`' values against `cli.PANEL_BUILD_TARGETS`.

    `V2-P4-078`'s bar: the remedy has to name a command that runs. `--dataset daily` is refused by
    name -- `write_daily_panel` takes the bars, the valuations and the halts together -- so a
    remedy spelling `daily` would send a caller to a command that does not exist, which is worse
    than naming none.
    """
    assert set(MODEL_PANEL_DATASETS.values()) <= set(PANEL_BUILD_TARGETS)
    for dataset, target in MODEL_PANEL_DATASETS.items():
        assert dataset in PANEL_BUILD_TARGETS[target], (
            f"{target} does not write {dataset}; the remedy would name the wrong command"
        )


def test_the_prerequisites_are_the_shortlists_plus_the_adjustment_series_and_less_the_renames() -> (
    None
):
    """This face's six panel datasets against `shortlist run`'s six, and the two that differ.

    Stated as a comparison rather than as a list, because the difference is the whole finding:
    a label is a **return between two sessions**, so `label_outcome` requires an
    `AdjustmentHistory` and `window_return` refuses a series that does not reach the window --
    while nothing here builds a `MarketBar`, so no name history is read and `is_st` is never
    asked. A user who has been running `shortlist run` has the wrong five of the six.
    """
    from openalpha_cn.shortlist_view import SHORTLIST_PANEL_DATASETS

    assert set(MODEL_PANEL_DATASETS) - set(SHORTLIST_PANEL_DATASETS) == {"adj_factor"}
    assert set(SHORTLIST_PANEL_DATASETS) - set(MODEL_PANEL_DATASETS) == {"namechange"}


# --- the two families ---------------------------------------------------------------------------


def test_the_family_table_names_exactly_the_two_implementations_this_build_ships() -> None:
    """`MODEL_FAMILIES` keyed by each implementation's own declared constant.

    Keyed by the constants rather than by string literals, so a family renamed in `backtest/`
    moves this table with it instead of leaving a key no declaration can match -- and each
    implementation refuses a declaration whose `family` is not its own, so a mis-keyed row would
    be a `bad_request` for a legal declaration.
    """
    assert set(MODEL_FAMILIES) == {BASELINE_FAMILY, TREE_FAMILY}
    assert BASELINE_FAMILY == "cross_sectional_rank"
    assert TREE_FAMILY == "boosted_rank_trees"


# --- the one rule this module applies that `backtest/` does not already own ----------------------


def test_trainable_at_keeps_a_label_that_closed_on_the_very_instant_it_predicts_at(
    labelled_panel_fixture: LabelledPanel,
) -> None:
    """`<=` and not `<`, which is `PredictionBatch`'s own floor read from the other side.

    That contract admits `as_of == training_cutoff` -- *"training through last night's close and
    predicting as of it is what a daily model does"* -- so a fit that dropped a label closing on
    the very instant it predicts at would be stricter than the contract it feeds, for a reason
    neither could state.
    """
    panel = labelled_panel_fixture
    closes = sorted(
        example.label.window.close_instant(example.label.window.exit_day)
        for example in panel.examples
    )

    assert trainable_at(panel, deadline=closes[-1]) == panel.examples
    kept = trainable_at(panel, deadline=closes[-1].replace(microsecond=0) - _ONE_SECOND)
    assert len(kept) < len(panel.examples)


_ONE_SECOND = datetime(2000, 1, 1, 0, 0, 1, tzinfo=UTC) - datetime(2000, 1, 1, tzinfo=UTC)


def test_trainable_at_removes_every_example_whose_outcome_had_not_printed(
    labelled_panel_fixture: LabelledPanel,
) -> None:
    """A deadline before everything keeps nothing, and the empty answer is what `run_daily`
    refuses by name rather than fitting on.

    The two directions together are what stop this passing on a function that returned its
    argument, or on one that returned nothing.
    """
    panel = labelled_panel_fixture
    closes = sorted(
        example.label.window.close_instant(example.label.window.exit_day)
        for example in panel.examples
    )

    assert trainable_at(panel, deadline=closes[0] - _ONE_SECOND) == ()
    assert len(trainable_at(panel, deadline=closes[-1])) == len(panel.examples)


# --- request resolution -------------------------------------------------------------------------


def test_a_column_declared_with_a_key_no_column_has_is_refused_with_the_keys_that_exist() -> None:
    """A body naming `factors` gets a sentence, not a `TypeError` from a splat."""
    with pytest.raises(ModelRequestError, match="which no column has"):
        feature_columns([{"factors": "reversal_1d/v1", "tier": "raw"}])


def test_declaring_no_column_at_all_is_refused_rather_than_fitted_on_nothing() -> None:
    with pytest.raises(ModelRequestError, match="declares no feature"):
        feature_columns([])


def test_a_raw_column_carrying_a_transform_is_refused_by_the_contract_that_owns_the_rule() -> None:
    """`FeatureColumn.__post_init__`'s refusal, re-raised in this face's own hierarchy.

    The rule is `feature_matrix`'s and is not restated here: a raw column is the factor's own
    stored values and no derived spec narrows that read, so a declared transform would be
    recorded in the id and ignored by the loader. What this face adds is the *type* -- a caller
    of `model evaluate` catches one hierarchy whatever part of the request was wrong.
    """
    with pytest.raises(ModelRequestError, match="carries a transform"):
        feature_columns(
            [
                {
                    "factor": "reversal_1d/v1",
                    "tier": "raw",
                    "transform": "cross_section_standard/v1",
                }
            ]
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("false", False), ("3", 3), ("0.5", 0.5), ("auto", "auto")],
)
def test_a_command_line_hyperparameter_reads_back_as_the_type_a_json_body_would_carry(
    raw: str, expected: bool | int | float | str
) -> None:
    """Bool before int before float before string, and the order is what round-trips.

    `AlphaModelDeclaration.hyperparameters` reaches the artifact's content address, so
    `--hyperparameter x=true` parsing to the **string** `"true"` while `{"x": true}` carries the
    boolean would give one declaration two addresses -- `V2-P4-046`'s equivalence broken on the
    one field where both spellings validate. `3` read as a float would reach the address as `3.0`
    for the same reason.
    """
    parsed = cli._model_scalar(raw)

    assert parsed == expected
    assert type(parsed) is type(expected)


# --- the layer, and the contract that keeps it there --------------------------------------------


def test_the_backtest_contract_names_this_module_and_the_entry_responds() -> None:
    """`backtest-no-numeric-stack-or-panel-plane` forbids `openalpha_cn.model_view`, measurably.

    Adding a name to a contract's forbidden list proves nothing on its own: the list could name a
    module nothing reaches and stay green forever. `V2-P4-033` made the entry for
    `shortlist_view` and measured that it fires; this does the same for the entry this issue
    added, by putting a probe under `backtest/` that imports this module and watching the
    contract break.

    The probe is a **new** file rather than an edit to a real one because this contract's source
    is the whole package, so a new module is covered on arrival -- which is the property the P3
    acceptance created it for.

    **Both calls go through `contained_lint_imports`, and `V2-P4-089` is why that sentence is
    here.** This file used to spell `from importlinter.cli import lint_imports as _lint_imports`
    -- the raw CLI, wearing the exact name of the containment wrapper one directory over -- and
    put back `logging.getLogger("importlinter").disabled` afterwards, which is the one logger the
    linter's own `dictConfig` names and therefore the one it never disables. It read as contained
    and was not: every other logger in the process stayed disabled, and six `caplog` acceptances
    in `tests/integration` failed on `assert 0 == 1` whenever this file was collected first.
    """
    assert not PROBE.exists(), "probe file must not already exist"
    PROBE.write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "from openalpha_cn.model_view import evaluate_model\n\n"
        '__all__ = ["evaluate_model"]\n',
        encoding="utf-8",
    )
    try:
        broken = contained_lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("backtest-no-numeric-stack-or-panel-plane",),
        )
    finally:
        PROBE.unlink()

    assert broken == 1, (
        "a backtest module importing openalpha_cn.model_view must break the contract; if this "
        "passes, the forbidden entry this issue added is decorative"
    )

    kept = contained_lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("backtest-no-numeric-stack-or-panel-plane",),
    )
    assert kept == 0


def test_this_module_reaches_no_concrete_store() -> None:
    """`model_view` imports nothing from `openalpha_cn.storage`, and the Protocols are why.

    Nothing forbids it -- a face may reach anything it renders -- so this is a design property
    rather than a contract, and it is worth pinning: `ModelPredictionStore` and
    `ResearchRunWriter` exist so that `runtime/composition.py` stays the only place in the
    repository that decides where predictions and runs live. An import here would make this the
    second, and the two would drift the way `sdk.py` and `api/app.py` did before `V2-P0B-002`.
    """
    source = (ROOT / "src" / "openalpha_cn" / "model_view.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    reached = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(module.startswith("openalpha_cn.storage") for module in reached), sorted(
        module for module in reached if module.startswith("openalpha_cn.storage")
    )


def test_the_panel_store_helper_is_the_one_every_face_already_uses() -> None:
    """Re-exported rather than restated, `shortlist_view`'s rule.

    Three faces disagreeing about where a runtime directory's panel lives would make every
    equivalence between them a coincidence.
    """
    from openalpha_cn import model_view

    assert model_view.panel_store is panel_store


def _evaluation_request() -> EvaluationRequest:
    """One resolved request, for the two aggregation tests that need a shape rather than a run."""
    return model_evaluation_request(
        columns=(FeatureColumn(definition=FACTOR_DEFINITIONS.get("reversal_1d/v1"), tier="raw"),),
        name="aggregation",
        family=BASELINE_FAMILY,
        horizon="5d",
        seed=1,
        start=date(2026, 1, 6),
        end=date(2026, 1, 9),
        as_of=datetime(2026, 1, 20, 4, 0, tzinfo=UTC),
        years=(2026,),
        exchange="SZSE",
        folds=1,
        test_days_per_fold=1,
        embargo_sessions=0,
        minimum_scored_ratio=0.0,
        code_commit="abcdef1234567",
        config_digest="e" * 64,
    )


def _fold_evaluation(*, offered: int, scored: int, paired: int) -> FoldEvaluation:
    """One fold whose three counts differ, which no corpus in this repository produces yet."""
    panel = walk_forward_fixtures.panel(aligned_from=walk_forward_fixtures.ALIGNED_FROM_OVERLAPPING)
    artifact = artifact_for(
        declaration=walk_forward_fixtures.declaration(),
        training_set=TrainingSet(
            feature_ids=panel.feature_ids, examples=panel.sections[0].examples
        ),
    )
    point = BaselineScorePoint(
        as_of=datetime(2026, 1, 9, 9, 0, tzinfo=UTC),
        predicted_at=datetime(2026, 1, 9, 9, 0, tzinfo=UTC),
        prediction_day=date(2026, 1, 9),
        offered_count=offered,
        scored_count=scored,
        paired_count=paired,
        coverage="insufficient_sample",
        rank_ic=None,
    )
    return FoldEvaluation(
        artifact=artifact,
        first_test_day=date(2026, 1, 9),
        points=(point,),
        coverage="insufficient_as_ofs",
        measured_count=0,
        mean_rank_ic=None,
        stdev_rank_ic=None,
        rank_icir=None,
        scored_ratio=scored / offered,
    )


# --- the aggregation the first mutation round found nothing separating --------------------------


def test_the_coverage_numerator_is_what_the_model_answered_and_not_what_carried_a_label(
    labelled_panel_fixture: LabelledPanel,
) -> None:
    """`scored_count` sums `point.scored_count`, never `point.paired_count`.

    A mutant swapping the two survived every surface test, and the fixture is why: on that corpus
    the one security the model abstains on is also the one the labeller refuses, so `scored` and
    `paired` coincide on every test day. They are two different disclosures about two different
    planes -- the model declining a row, and the panel having no outcome for it -- and the
    coverage floor divides by the offered market to bound the **first**. Reading it off `paired`
    would let a thin label corpus relieve a bar that exists to catch an abstaining model.

    Driven on a constructed `FoldEvaluation` rather than on a panel, because what is under test is
    the aggregation and the corpus that separates the two counts is `V2-P4-022`'s to build.
    """
    fold = _fold_evaluation(offered=10, scored=8, paired=4)
    evaluation = ModelEvaluation(
        request=_evaluation_request(),
        prediction_days=(date(2026, 1, 9),),
        excluded=(),
        folds=(fold,),
    )

    assert evaluation.offered_count == 10
    assert evaluation.scored_count == 8
    assert evaluation.scored_ratio == 0.8


def test_no_field_of_a_resolved_request_carries_a_default(
    labelled_panel_fixture: LabelledPanel,
) -> None:
    """Every parameter of a model run is a decision somebody recorded making.

    A mutant giving `minimum_scored_ratio` a `0.0` default survived, because all three faces
    require it and the dataclass default is unreachable from any of them today. It is still the
    wrong shape: the next face to be written would silently inherit a coverage bar nobody chose,
    which is exactly what `FactorRunRequest` refuses field by field one plane over. Asserted over
    the whole field set rather than that one field, so the guarantee covers the parameter added
    next as well.
    """
    defaulted = [
        field.name
        for field in dataclasses.fields(ModelRunRequest)
        if field.default is not dataclasses.MISSING
        or field.default_factory is not dataclasses.MISSING
    ]

    assert defaulted == [], (
        f"{defaulted} carries a default on ModelRunRequest; a run parameter with a default is a "
        "decision nobody recorded making, and every face already requires each of these"
    )


# --------------------------------------------------------------------------------------------
# V2-P4-088: the outcome-window guard, and the arm of it that cannot fire
# --------------------------------------------------------------------------------------------


def test_the_label_error_arm_of_the_outcome_window_guard_cannot_fire_and_is_kept_anyway() -> None:
    """A surviving mutant, answered the way `V2-P4-084` answered its own.

    Dropping `LabelError` from `model_view._OUTCOME_WINDOW_FAULTS` leaves the whole suite green,
    measured. `build_label_window` raises it for exactly one reason -- a `zone` west of
    `MINIMUM_LABEL_ZONE_OFFSET`, which would date an afternoon Shanghai signal on the previous day
    and enter on a session whose close had already been published -- and both call sites pass
    `MODEL_DATE_ZONE`, a module constant at `+08:00`. No face supplies a zone at all.

    The arm is kept rather than deleted, which is
    `tests/integration/test_unlabelled_corpus_faces.py`'s decision on the identical finding:
    removing a guard is the fail-open direction, and the only thing making this one dead is a
    constant one line could move. So the *reason* is pinned instead. The day `MODEL_DATE_ZONE`
    goes west, this test fails and says the arm is now live -- rather than a `500` saying it.

    The neighbouring refusal `build_label_window` propagates is **not** in the tuple and does not
    need to be. `ResearchHorizon.sessions` raises `HorizonError` for a calendar unit, but
    `AlphaModelDeclaration.horizon` carries `COUNTABLE_HORIZON_PATTERN`, so a run declaring `3m`
    is refused as a `bad_request` before anything asks a calendar to count it -- measured on the
    command line as `exit 3`, "String should match pattern '^[1-9][0-9]{0,2}[d]$'".
    """
    offset = MODEL_DATE_ZONE.utcoffset(datetime(2026, 12, 31, tzinfo=UTC))

    assert offset is not None and offset >= MINIMUM_LABEL_ZONE_OFFSET, (
        "MODEL_DATE_ZONE has moved west of the bound build_label_window refuses at, so the "
        "LabelError arm of _OUTCOME_WINDOW_FAULTS is now reachable and needs a face driving it"
    )
    assert LabelError in _OUTCOME_WINDOW_FAULTS
    assert TradingCalendarError in _OUTCOME_WINDOW_FAULTS

    assert HorizonError not in _OUTCOME_WINDOW_FAULTS
    months = parse_horizon("3m")
    with pytest.raises(HorizonError):
        assert months.sessions
    with pytest.raises(ValueError, match="horizon"):
        AlphaModelDeclaration(
            name="reversal-rank",
            family=BASELINE_FAMILY,
            horizon="3m",
            feature_version=f"feat_{'0' * 24}",
            seed=7,
            code_commit="abcdef1234567",
        )
