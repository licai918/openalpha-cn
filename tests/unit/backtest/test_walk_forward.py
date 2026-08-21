"""`V2-P4-013`: the walk-forward split, and the two rules that cut it.

The row is one line -- *"Walk-forward 切分 + purge/embargo ... 禁止随机切分"* -- and every test here
is about one of its three clauses. The corpus is `tests/walk_forward_fixtures.py`, whose labels
are all read off one close series per security, so an overlap between two windows is a fact
about the prices rather than an assertion about the fixture.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from walk_forward_fixtures import (
    ALIGNED_FROM_ADJACENT,
    ALIGNED_FROM_OVERLAPPING,
    EMBARGO_SESSIONS,
    FIRST_TEST_DAY_INDEX,
    FOLDS,
    HORIZON,
    MOMENTUM,
    SECURITIES,
    SESSION_CLOSE,
    TEST_DAYS_PER_FOLD,
    as_of_for,
    cross_section_for,
    declaration,
    labelled_sections,
    labels_for,
    panel,
    prediction_days,
    sessions,
    trading_calendar,
)

from openalpha_cn.backtest.alpha_model import SingleFeatureAlphaModel
from openalpha_cn.backtest.walk_forward import (
    KNOWN_WALK_FORWARD_LIMITATIONS,
    LabelledCrossSection,
    LabelledPanel,
    WalkForwardError,
    WalkForwardFold,
    labelled_panel,
    shared_sessions,
    walk_forward_folds,
)
from openalpha_cn.domain.alpha_model import TrainingExample, TrainingSet
from openalpha_cn.domain.labels import overlapping_windows


def _folds(
    *, aligned_from: int = ALIGNED_FROM_OVERLAPPING, embargo: int = 0
) -> tuple[WalkForwardFold, ...]:
    return walk_forward_folds(
        panel(aligned_from=aligned_from),
        calendar=trading_calendar(),
        folds=FOLDS,
        test_days_per_fold=TEST_DAYS_PER_FOLD,
        embargo_sessions=embargo,
    )


def _days(examples: tuple[TrainingExample, ...]) -> set[date]:
    return {item.label.window.prediction_day for item in examples}


def test_a_fold_trains_only_on_prediction_days_strictly_before_its_test_block() -> None:
    """The row's first clause. Every training example's decision day precedes every test one.

    Stated as a comparison of the two extremes rather than as a set difference, because a split
    that leaked one day would still satisfy "the sets are disjoint".
    """
    for fold in _folds():
        assert max(_days(fold.train_examples)) < min(fold.test_days)
        assert set(fold.test_days) & _days(fold.train_examples) == set()


def test_the_purge_removes_every_label_that_had_not_closed_by_the_first_prediction() -> None:
    """The purge, stated as the rule and measured as the exact set it produces.

    A `5d` window opened on prediction day `d` exits five sessions after its entry, so the
    example on session index `i` closes on session `i + 6`. Fold 0's first prediction is made on
    session 12 at 09:00, before that session's 15:00 close, so every example from index 6 onward
    carries a label that had not closed when the model was asked -- and every one from index 5
    back had.
    """
    fold = _folds()[0]
    axis = sessions()
    assert fold.first_test_day == axis[FIRST_TEST_DAY_INDEX]

    assert _days(fold.purged) == set(axis[6:12])
    assert _days(fold.train_examples) == set(axis[0:6])
    for example in fold.purged:
        window = example.label.window
        assert window.close_instant(window.exit_day) > fold.first_test_as_of
    for example in fold.train_examples:
        window = example.label.window
        assert window.close_instant(window.exit_day) <= fold.first_test_as_of


def test_no_surviving_training_label_reads_a_session_a_test_label_reads() -> None:
    """The overlap property, derived rather than separately implemented.

    `V2-P4-011` left `TrainingSet.overlaps` as the input a purge needs and the row calls the
    rule "purging for overlapping labels", so this is the sentence that has to come out true.
    It is asserted as a *consequence* of the instant rule above, and the counterfactual is
    asserted beside it so the assertion cannot pass on a corpus with nothing to share.
    """
    fold = _folds()
    for item in fold:
        assert shared_sessions(item.train_examples, item.test_examples) == ()
    unpurged = _folds()[0]
    assert shared_sessions(unpurged.candidates, unpurged.test_examples) != ()


def test_the_purge_removes_a_session_the_shared_session_rule_alone_would_have_kept() -> None:
    """The instant rule is strictly stronger than the shared-session one, by exactly one session.

    Session 6's window spans sessions 7..12 and fold 0's earliest test label spans 13..18, so the
    two share nothing at all -- a purge written as "drop the examples whose windows overlap a
    test window" keeps it. Its label still closes at 15:00 on session 12, hours after the 09:00
    instant fold 0's first prediction is made at, so a model holding it could not have existed
    when it was asked. That is why the purge is written on the close instant and the shared
    session is the property rather than the rule.
    """
    fold = _folds()[0]
    axis = sessions()
    kept_by_the_weaker_rule = tuple(
        example
        for example in fold.candidates
        if shared_sessions((example,), fold.test_examples) == ()
    )
    assert _days(kept_by_the_weaker_rule) == set(axis[0:7])
    assert _days(fold.purged) - _days(kept_by_the_weaker_rule) == set(axis[7:12])
    assert _days(fold.purged) & _days(kept_by_the_weaker_rule) == {axis[6]}


def test_the_training_cutoff_never_reaches_past_the_instant_the_fold_is_first_asked_at() -> None:
    """What the purge buys, in `V2-P4-011`'s own vocabulary.

    `PredictionBatch` refuses `as_of < artifact.training_cutoff`, per batch. The purge applies
    the same comparison once per **fold**, anchored at the earliest instant the fold predicts
    at, so every batch in the fold passes rather than only the late ones.
    """
    for fold in _folds():
        assert fold.training_set.training_cutoff <= fold.first_test_as_of
        for section in fold.test_sections:
            assert fold.training_set.training_cutoff <= section.as_of


def test_a_training_set_the_purge_did_not_cut_produces_a_batch_the_contract_refuses() -> None:
    """The purge is not advice: without it the fold's first prediction cannot be made at all.

    Every purged example, on its own, would push the artifact's training cutoff past the instant
    the batch stands at, which is the refusal `V2-P4-011` installed on `PredictionBatch`. So the
    counterfactual to "no purge" is not an inflated number, it is a fold that will not run.
    """
    fold = _folds()[0]
    unpurged = TrainingSet(
        feature_ids=fold.panel.feature_ids,
        examples=fold.candidates,
    )
    model = SingleFeatureAlphaModel(declaration=declaration())
    section = fold.test_sections[0]
    with pytest.raises(ValueError, match="the fit consumed an outcome"):
        model.fit(unpurged).predict(section.cross_section, predicted_at=section.as_of)
    assert model.fit(fold.training_set).predict(
        section.cross_section, predicted_at=section.as_of
    ).subjects == tuple(sorted(SECURITIES))


def test_the_embargo_removes_the_sessions_before_the_block_the_purge_never_reaches() -> None:
    """The second rule, and the reason it is not the first one with a wider number.

    An embargo of `E` sessions removes the training examples whose labels close in the `E`
    sessions ending at the fold's first prediction day and that the purge did not already take.
    The two sets are disjoint by construction, so no embargo width can substitute for the purge
    and no purge can reach what the embargo takes.
    """
    axis = sessions()
    fold = _folds(embargo=EMBARGO_SESSIONS)[0]
    assert _days(fold.embargoed) == set(axis[4:6])
    assert _days(fold.train_examples) == set(axis[0:4])
    assert set(fold.embargoed) & set(fold.purged) == set()
    assert shared_sessions(fold.embargoed, fold.test_examples) == ()


def test_no_embargo_width_removes_what_the_purge_removes() -> None:
    """The half of "one rule applied and not the other" that runs the widths.

    Every width from zero up to the whole session axis: the embargoed set never contains a
    purged example, so an implementation that dropped the purge and widened the embargo removes
    a different set at every width rather than the same one later.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    purged = set(_folds()[0].purged)
    assert purged
    for width in range(0, FIRST_TEST_DAY_INDEX + 1):
        fold = WalkForwardFold(
            panel=built,
            calendar=trading_calendar(),
            first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
            test_day_count=TEST_DAYS_PER_FOLD,
            embargo_sessions=width,
        )
        assert set(fold.embargoed) & purged == set(), width
        assert set(fold.purged) == purged, width


def test_an_embargo_of_zero_sessions_removes_nothing_and_has_to_be_said() -> None:
    """Zero is a statement -- "no separation is required" -- and never a default.

    `build_label_window`'s `zone` is the precedent: a waiver that is a default is an accident.
    """
    assert _folds(embargo=0)[0].embargoed == ()
    with pytest.raises(TypeError):
        walk_forward_folds(  # type: ignore[call-arg]
            panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            folds=FOLDS,
            test_days_per_fold=TEST_DAYS_PER_FOLD,
        )


def test_a_fold_carries_no_field_naming_which_rows_train() -> None:
    """禁止随机切分, made structural rather than discouraged -- for what this type can carry.

    A fold is a panel, a calendar, a first test day, a block length and an embargo width. There
    is no field for train membership and none for test membership, so the only split this type
    can express is "every earlier prediction day, less what the two rules remove". A shuffled
    partition has nowhere to be written down -- and neither has a split that puts one security's
    later rows in train and another's earlier rows in test, because the boundary is a date and
    a date takes the whole cross section with it.

    **This assertion is about the declaration and not about what a caller can assemble**, which
    is exactly what `V2-P4-090` measured: it was the whole support for "an unordered split is
    unrepresentable", and a panel whose sections were out of order defeated that claim without
    touching a single field of this type.
    `test_a_scattered_section_tuple_is_refused_by_the_panel_and_not_only_by_its_factory` is the
    other half, and it is a refusal rather than an absence.
    """
    assert {field.name for field in dataclasses.fields(WalkForwardFold)} == {
        "panel",
        "calendar",
        "first_test_day",
        "test_day_count",
        "embargo_sessions",
    }


# --------------------------------------------------------------------------------------
# What the type refuses, rather than what the factory happened to check (`V2-P4-090`)
# --------------------------------------------------------------------------------------


def _scattered(built: LabelledPanel, *, index: int) -> LabelledPanel:
    """`built` with one early section moved to the end of the tuple, and nothing else changed."""
    sections = built.sections
    return dataclasses.replace(
        built, sections=sections[:index] + sections[index + 1 :] + (sections[index],)
    )


def test_a_scattered_section_tuple_is_refused_by_the_panel_and_not_only_by_its_factory() -> None:
    """`V2-P4-090`: the invariant that makes a fold's membership derivable, on the type.

    `labelled_panel` has always refused a section tuple whose prediction days do not strictly
    increase. That refusal was the *factory's*, and the acceptance measured what a frozen
    dataclass with no `__post_init__` is worth: `dataclasses.replace(panel, sections=...)` --
    the idiom this repository's own tests use everywhere -- moved `2026-01-08` to the end of a
    twenty-day panel and handed the result to the **shipped** `walk_forward_folds`, which
    accepted it and returned two folds. The second tested on `['2026-01-28', '2026-01-08']` and
    its `leaked_sessions` reported six sessions read by both the surviving training labels and
    the test labels -- reported, that is, by the module's own independent measurement of the
    property the purge exists to produce.

    So the check moved to where `WalkForwardFold` already puts its own, and the factory now has
    no copy of it -- both entry points are driven here, because the factory *delegating* rather
    than duplicating is half of the fix. `V2-P4-011`'s ground for deleting its own duplicated
    check is the same one: two copies is one check plus a place for the weaker one to fall
    behind.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    days = prediction_days()
    ordered = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:3])

    with pytest.raises(WalkForwardError, match="not strictly increasing"):
        _scattered(built, index=5)
    with pytest.raises(WalkForwardError, match="not strictly increasing"):
        labelled_panel([ordered[1], ordered[0], ordered[2]])


def test_the_shipped_schedule_can_no_longer_be_handed_the_split_that_leaked() -> None:
    """The probe, end to end: the tuple that produced six leaked sessions never reaches a fold.

    Asserted through `walk_forward_folds` rather than through the constructor alone, because the
    finding was not "a dataclass admits a bad value" -- it was that the shipped scheduler took
    one and returned folds. The panel is assembled first and then scattered, so the refusal has
    to come from the reassembly and cannot be the factory's.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    assert built.prediction_days[5] == date(2026, 1, 8)

    with pytest.raises(WalkForwardError, match="not strictly increasing"):
        walk_forward_folds(
            _scattered(built, index=5),
            calendar=trading_calendar(),
            folds=FOLDS,
            test_days_per_fold=2,
            embargo_sessions=0,
        )


def test_a_section_whose_instant_does_not_date_its_own_prediction_day_is_refused() -> None:
    """The second bypass, found while closing the first, and it reaches a leak the same way.

    Ordering alone is not what makes the purge safe. The purge's whole argument is that
    `first_test_as_of` is dated **on the fold's first prediction day, in the window's own zone**
    -- that is what turns "a training label shares a session with a test label" into "its close
    instant is after the first `as_of`". A section carrying a `prediction_day` its own instant
    does not resolve to breaks that premise while leaving the day order untouched.

    Both copies of the instant are moved here, so the refusal is this check and not the
    cheaper one above it.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    section = built.sections[FIRST_TEST_DAY_INDEX]
    moved = section.as_of + timedelta(days=365)

    with pytest.raises(WalkForwardError, match="in its labels' own zone"):
        dataclasses.replace(
            section,
            as_of=moved,
            cross_section=dataclasses.replace(section.cross_section, as_of=moved),
        )


def test_a_section_carrying_an_instant_its_own_cross_section_disagrees_with_is_refused() -> None:
    """One prediction day is one instant, and the section states it twice.

    This is the probe the second bypass was measured with: moving fold 0's first section's own
    `as_of` forward by a year and nothing else left the prediction days ascending, was accepted
    by the shipped `walk_forward_folds`, and produced a fold whose purge removed **0 of 48**
    candidates while `leaked_sessions` reported five.

    `evaluate_fold` also dates every batch at `section.as_of` while `score_point` refuses a
    batch whose `as_of` is not the section's, so the two copies disagreeing is a second fault
    on the same line.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    section = built.sections[FIRST_TEST_DAY_INDEX]

    with pytest.raises(WalkForwardError, match="its cross section is dated"):
        dataclasses.replace(section, as_of=section.as_of + timedelta(days=365))


def test_a_section_with_no_labelled_row_is_refused_however_it_was_assembled() -> None:
    """`a_prediction_day_that_labels_nothing_is_refused_rather_than_skipped`, on the type.

    The factory refused this and the type did not, so `dataclasses.replace(section, examples=())`
    produced a prediction day a fold counts in its block and can learn nothing from.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)

    with pytest.raises(WalkForwardError, match="produced no labelled row"):
        dataclasses.replace(built.sections[0], examples=())


def test_a_panel_with_no_section_is_refused_where_a_fold_would_have_read_past_the_end() -> None:
    """An empty panel reached `WalkForwardFold.__post_init__`, which indexes `days[0]`.

    Measured: `dataclasses.replace(panel, sections=())` was accepted, and the fold built on it
    raised a bare `IndexError: tuple index out of range` -- not a `WalkForwardError`, so a
    caller catching this module's own error caught nothing.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)

    with pytest.raises(WalkForwardError, match="carries no cross section"):
        dataclasses.replace(built, sections=())


def test_a_panel_whose_sections_disagree_about_the_recipe_is_refused_by_the_type() -> None:
    """Feature values travel positionally, and `_training_set_of` reads the *panel's* list.

    So a panel whose declared `feature_ids` is not what its sections carry hands every fold a
    `TrainingSet` whose column names are one matrix's and whose values are another's.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)

    with pytest.raises(WalkForwardError, match="two feature lists"):
        dataclasses.replace(built, feature_ids=(MOMENTUM,))


def test_a_panel_whose_exchange_is_not_its_labels_is_refused_by_the_type() -> None:
    """The embargo counts sessions on the calendar `WalkForwardFold` checks against this field.

    That check compares the calendar to `panel.exchange`; if the field and the labels disagree,
    the fold passes its own guard and counts another exchange's sessions.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)

    with pytest.raises(WalkForwardError, match="mixes exchanges"):
        dataclasses.replace(built, exchange="SSE")


def test_a_section_holding_a_row_built_for_another_day_is_refused() -> None:
    """The section reads one window to date itself, so every row in it has to be that day's.

    `LabelledPanel` takes each section's zone, exchange and horizon off its first example, and
    `PanelSection` resolves its own instant against that same one. A row filed under another
    prediction day would ride through both and be purged, embargoed and tested against a
    boundary its window does not stand behind.
    """
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    today, tomorrow = built.sections[0], built.sections[1]

    with pytest.raises(WalkForwardError, match="sits in the section this panel calls"):
        dataclasses.replace(today, examples=(today.examples[0], tomorrow.examples[1]))


def test_a_section_whose_rows_do_not_agree_on_the_three_things_the_purge_reads_is_refused() -> None:
    """What makes reading one window per section sound rather than convenient.

    A ten-session window sitting inside a five-session day is dated to the same prediction day,
    so the check above cannot see it -- and the panel's own horizon, exchange and zone are read
    off whichever example happens to be first.
    """
    days = prediction_days()
    built = panel(aligned_from=ALIGNED_FROM_OVERLAPPING)
    section = built.sections[0]
    longer = labels_for(days[0], aligned_from=ALIGNED_FROM_OVERLAPPING, horizon="10d")
    assert longer[1].window.prediction_day == section.prediction_day

    with pytest.raises(WalkForwardError, match="one prediction day is one question"):
        dataclasses.replace(
            section,
            examples=(
                section.examples[0],
                TrainingExample(label=longer[1], features=section.examples[1].features),
            ),
        )


def test_a_sections_prediction_day_is_its_instants_date_in_the_labels_own_zone() -> None:
    """Not the date the instant happens to be *written* in, which is `V2-P4-012`'s real shape.

    A `FeatureMatrixSection.as_of` is an instant, and the corpus everywhere else in this file
    writes its instants in Shanghai -- where an instant's own `.date()` and its Shanghai date
    are the same day and a fixture cannot tell one rule from the other. A mutant replacing
    `_prediction_day_of` with `as_of.date()` survived the whole unit suite on that account.

    Between 00:00 and 08:00 Shanghai the two disagree, so this section is dated at 07:00 in the
    market's own zone and handed over expressed in UTC, where it reads as the previous day.
    """
    day = prediction_days()[0]
    early = time(7, 0)
    instant = as_of_for(day, at=early).astimezone(UTC)
    assert instant.date() != day

    built = labelled_panel(
        [
            LabelledCrossSection(
                cross_section=dataclasses.replace(cross_section_for(day, at=early), as_of=instant),
                labels=labels_for(day, aligned_from=ALIGNED_FROM_OVERLAPPING, at=early),
            )
        ]
    )

    assert built.sections[0].prediction_day == day
    assert built.sections[0].as_of == instant


def test_folds_are_time_ordered_and_share_no_test_day() -> None:
    """A schedule, not a shuffle: the blocks tile the tail of the panel in order."""
    folds = _folds()
    assert len(folds) == FOLDS
    assert [item.first_test_day for item in folds] == sorted(item.first_test_day for item in folds)
    seen: set[date] = set()
    for item in folds:
        assert set(item.test_days) & seen == set()
        seen |= set(item.test_days)
    assert seen == set(prediction_days()[-FOLDS * TEST_DAYS_PER_FOLD :])


def test_a_first_test_day_the_panel_never_asked_on_is_refused() -> None:
    """A fold's boundary has to be one of the panel's own decision days."""
    with pytest.raises(WalkForwardError, match="is not one of this panel's prediction days"):
        WalkForwardFold(
            panel=panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            first_test_day=prediction_days()[0] - timedelta(days=1),
            test_day_count=1,
            embargo_sessions=0,
        )


def test_a_fold_whose_block_starts_on_the_panels_first_day_has_nothing_to_train_on() -> None:
    """Refused where it happens rather than answered with an empty training set."""
    with pytest.raises(WalkForwardError, match="no prediction day before it"):
        WalkForwardFold(
            panel=panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            first_test_day=prediction_days()[0],
            test_day_count=1,
            embargo_sessions=0,
        )


def test_a_fold_the_two_rules_empty_names_the_rule_that_emptied_it() -> None:
    """A training set of nothing is refused with the arithmetic that produced it.

    An embargo wide enough to reach past the panel's first prediction day leaves a fold with no
    training example at all, and "a training set carries no example" is the wrong message: it
    says nothing about which of the two cuts took the last row.
    """
    fold = WalkForwardFold(
        panel=panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
        calendar=trading_calendar(),
        first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAYS_PER_FOLD,
        embargo_sessions=FIRST_TEST_DAY_INDEX,
    )
    with pytest.raises(WalkForwardError, match=r"purge removed \d+ and the 12-session embargo"):
        _ = fold.training_set


def test_a_schedule_that_leaves_no_training_day_for_its_first_fold_is_refused() -> None:
    """`folds * test_days_per_fold` has to leave a panel behind it."""
    with pytest.raises(WalkForwardError, match="leaves no prediction day"):
        walk_forward_folds(
            panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            folds=5,
            test_days_per_fold=4,
            embargo_sessions=0,
        )


def test_a_schedule_of_no_folds_is_refused_rather_than_answered_with_an_empty_tuple() -> None:
    """An empty success is the shape this plane exists to make unavailable."""
    for folds, per_fold in ((0, 4), (2, 0)):
        with pytest.raises(WalkForwardError, match="at least one"):
            walk_forward_folds(
                panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
                calendar=trading_calendar(),
                folds=folds,
                test_days_per_fold=per_fold,
                embargo_sessions=0,
            )


def test_a_negative_embargo_reaches_the_caller_through_the_schedule() -> None:
    """The schedule carries no copy of this check, and does not need one.

    A mutation sweep measured that: `walk_forward_folds` builds folds, so the refusal one frame
    in is the one every path meets. The second copy was deleted rather than asserted twice --
    `V2-P4-011` and `V2-P4-012` each closed a survivor the same way -- and this test is what
    keeps the surviving copy reachable from the face a caller actually uses.
    """
    with pytest.raises(WalkForwardError, match="embargo of -1 sessions"):
        walk_forward_folds(
            panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            folds=FOLDS,
            test_days_per_fold=TEST_DAYS_PER_FOLD,
            embargo_sessions=-1,
        )


def test_a_calendar_from_another_exchange_is_refused() -> None:
    """The embargo counts sessions, so the calendar has to be the one the windows were built on."""
    other = trading_calendar(exchange="SSE")
    with pytest.raises(WalkForwardError, match="SSE calendar"):
        walk_forward_folds(
            panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=other,
            folds=FOLDS,
            test_days_per_fold=TEST_DAYS_PER_FOLD,
            embargo_sessions=0,
        )


def test_the_panel_refuses_a_label_built_at_another_instant() -> None:
    """The join is by the instant the window was dated from, which is the only join there is.

    `build_label_window` derives a prediction day by reading the `as_of` in the window's own
    zone. A label whose prediction day is not that day is a label about a different decision,
    and pairing it with these features would date a feature row against an outcome it never
    preceded -- which is exactly what happens when a matrix section resolves to an earlier
    build than the caller asked for.
    """
    days = prediction_days()
    with pytest.raises(WalkForwardError, match="was built for"):
        labelled_panel(
            [
                LabelledCrossSection(
                    cross_section=cross_section_for(days[0]),
                    labels=labels_for(days[1], aligned_from=ALIGNED_FROM_OVERLAPPING),
                )
            ]
        )


def test_the_panel_discloses_the_rows_it_could_not_label() -> None:
    """A refused label is excluded and counted, never dropped and never read as zero.

    `V2-P4-011` refuses an unlabelled window at `TrainingExample`, so the choice here is between
    refusing the whole section and disclosing the row. Disclosing is the one that keeps a halted
    name visible: `ScoreCoverage.incomplete_components`' argument, one plane up.
    """
    days = prediction_days()
    labels = labels_for(days[0], aligned_from=ALIGNED_FROM_OVERLAPPING)
    kept = labels[1:]
    built = labelled_panel(
        [LabelledCrossSection(cross_section=cross_section_for(days[0]), labels=kept)]
    )
    assert {item.ts_code for item in built.excluded} == {SECURITIES[0]}
    assert built.excluded[0].prediction_day == days[0]
    assert "no label" in built.excluded[0].reason
    assert {item.ts_code for item in built.sections[0].examples} == set(SECURITIES[1:])


def test_the_panel_refuses_instants_that_do_not_increase() -> None:
    """A panel is time-ordered by construction, `FeatureMatrixRequest.as_ofs`' own rule."""
    days = prediction_days()
    sections = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:3])
    with pytest.raises(WalkForwardError, match="not strictly increasing"):
        labelled_panel([sections[1], sections[0], sections[2]])


def test_the_panel_refuses_two_feature_lists() -> None:
    """Feature values travel positionally, so one panel is one recipe."""
    days = prediction_days()
    first = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1])[0]
    second = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[1:2])[0]
    narrowed = dataclasses.replace(
        second,
        cross_section=dataclasses.replace(
            second.cross_section,
            feature_ids=(MOMENTUM,),
            rows=tuple(
                dataclasses.replace(row, values=row.values[:1]) for row in second.cross_section.rows
            ),
        ),
    )
    with pytest.raises(WalkForwardError, match="two feature lists"):
        labelled_panel([first, narrowed])


def test_a_panel_of_no_section_is_refused() -> None:
    with pytest.raises(WalkForwardError, match="carries no cross section"):
        labelled_panel([])


def test_the_panel_hands_the_fold_the_cross_sections_predict_takes() -> None:
    """`V2-P4-012` hands this issue dated cross sections; a fold hands them back unchanged.

    The rows a fold predicts on are the whole cross section, labelled or not -- a security the
    panel could not label is still a security the model is asked about, and abstaining is what
    `V2-P4-011` made that visible with.
    """
    fold = _folds()[0]
    assert tuple(section.prediction_day for section in fold.test_sections) == fold.test_days
    for section in fold.test_sections:
        assert section.cross_section is fold.panel.section_on(section.prediction_day).cross_section
        assert section.cross_section.subjects == tuple(SECURITIES)


def test_training_set_overlaps_cannot_draw_the_boundary_this_purge_draws() -> None:
    """`V2-P4-011` named `TrainingSet.overlaps` as this issue's input; it is not the purge.

    `overlapping_windows` groups by security, on the stated ground that two securities' windows
    spanning the same sessions "is not an overlap in any sense a purge cares about". That is
    true of two samples' independence and false of a fold boundary: a training label measured
    over sessions inside the test period is a realized market return inside the test period,
    whichever security it is about. Measured two ways here -- the pairs it reports are mostly
    about two training examples, which no boundary cares about, and a panel whose securities do
    not repeat across the boundary reports **nothing** while the boundary still leaks.
    """
    fold = _folds()[0]
    reported = overlapping_windows(
        TrainingSet(feature_ids=fold.panel.feature_ids, examples=fold.candidates).samples
    )
    test_days = set(fold.test_days)
    assert reported
    assert not any(
        overlap.earlier.prediction_day in test_days or overlap.later.prediction_day in test_days
        for overlap in reported
    )

    split = len(SECURITIES) // 2
    days = prediction_days()
    disjoint = labelled_panel(
        [
            dataclasses.replace(
                section,
                cross_section=dataclasses.replace(
                    section.cross_section,
                    rows=tuple(
                        row
                        for row in section.cross_section.rows
                        if (row.ts_code in SECURITIES[:split])
                        == (section.cross_section.as_of.date() < days[FIRST_TEST_DAY_INDEX])
                    ),
                ),
                labels=tuple(
                    label
                    for label in section.labels
                    if (label.ts_code in SECURITIES[:split])
                    == (label.window.prediction_day < days[FIRST_TEST_DAY_INDEX])
                ),
            )
            for section in labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING)
        ]
    )
    boundary = WalkForwardFold(
        panel=disjoint,
        calendar=trading_calendar(),
        first_test_day=days[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAYS_PER_FOLD,
        embargo_sessions=0,
    )
    across = TrainingSet(
        feature_ids=disjoint.feature_ids,
        examples=boundary.candidates + boundary.test_examples,
    )
    crossing = [
        overlap
        for overlap in overlapping_windows(across.samples)
        if overlap.earlier.prediction_day < boundary.first_test_day <= overlap.later.prediction_day
    ]
    assert crossing == []
    assert shared_sessions(boundary.candidates, boundary.test_examples) != ()
    assert boundary.purged


def test_the_panel_and_the_horizon_the_folds_carry_are_the_corpus_they_were_cut_from() -> None:
    """A sanity floor: the fixture really is one horizon over one exchange."""
    built = panel(aligned_from=ALIGNED_FROM_ADJACENT)
    assert built.exchange == "SZSE"
    assert {item.label.window.horizon.text for item in built.examples} == {HORIZON}
    assert built.prediction_days == prediction_days()


def test_the_limitation_registry_is_exactly_the_boundaries_this_split_declares() -> None:
    """Equality rather than membership: a removed entry has to fail something too.

    `KNOWN_ADJUSTMENT_LIMITATIONS` has had this form since `V2-P1-005`, and
    `tests/unit/test_known_limitation_registries.py` is what makes every code below have to
    appear in executable test code somewhere rather than only in prose.
    """
    assert {item.code for item in KNOWN_WALK_FORWARD_LIMITATIONS} == {
        "the_purge_is_the_prediction_batch_floor_moved_from_a_refusal_to_a_removal",
        "the_shared_session_rule_is_a_property_here_and_not_a_second_implementation",
        "the_embargo_width_is_declared_because_the_footprint_it_covers_is_not_on_the_label",
        "training_set_overlaps_is_grouped_by_security_and_a_fold_boundary_is_not",
        "train_membership_is_unrepresentable_and_the_order_behind_it_is_only_refused",
        "only_an_expanding_training_window_is_offered",
        "nothing_here_evaluates_a_fold_and_this_corpus_is_not_a_benchmark",
        "the_join_is_by_instant_and_cannot_check_that_a_feature_row_is_point_in_time",
        "a_prediction_day_that_labels_nothing_is_refused_rather_than_skipped",
        "the_final_holdout_decision_12_asks_to_leave_untouched_is_not_this_split",
        "the_finest_walk_forward_this_repository_can_currently_cut_is_annual",
    }


def test_the_training_window_expands_and_never_slides() -> None:
    """`only_an_expanding_training_window_is_offered`, driven rather than asserted about.

    Every fold's candidate set is a prefix of the panel, so a later fold's training span
    contains an earlier one's and never leaves the front. A rolling schedule would break the
    containment, which is what makes this the shape worth asserting.
    """
    folds = _folds()
    assert len(folds) > 1
    for earlier, later in pairwise(folds):
        assert set(earlier.candidates) < set(later.candidates)
        assert min(_days(later.candidates)) == min(_days(earlier.candidates))


def test_a_prediction_day_that_labels_nothing_is_refused_rather_than_skipped() -> None:
    """A hole in the time axis moves every boundary after it, so it is refused where it appears.

    Two shapes, and they need two messages: a section that offers no label carries no window
    and therefore no zone to read its own instant in, while a section whose labels all refuse
    knows exactly which day it is about and has nothing to teach.
    """
    days = prediction_days()
    section = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1])[0]
    with pytest.raises(WalkForwardError, match="offers no label at all"):
        labelled_panel([dataclasses.replace(section, labels=())])

    refused = labels_for(days[0], aligned_from=ALIGNED_FROM_OVERLAPPING, snapshot_on=sessions()[0])
    assert not any(label.is_labelled for label in refused)
    with pytest.raises(WalkForwardError, match="produced no labelled row"):
        labelled_panel([dataclasses.replace(section, labels=refused)])


def test_one_refused_row_is_disclosed_where_a_whole_refused_day_is_denied() -> None:
    """The two halves of the same decision, side by side.

    An individual refused window is the ordinary shape of a label set, so it leaves the panel
    through `excluded` carrying `OutcomeLabel.refusal_summary` -- the row stays visible instead
    of vanishing. A day on which every row refuses is the shape above, and refuses.
    """
    days = prediction_days()
    healthy = labels_for(days[0], aligned_from=ALIGNED_FROM_OVERLAPPING)
    refused = labels_for(days[0], aligned_from=ALIGNED_FROM_OVERLAPPING, snapshot_on=sessions()[0])
    built = labelled_panel(
        [
            LabelledCrossSection(
                cross_section=cross_section_for(days[0]),
                labels=(refused[0], *healthy[1:]),
            )
        ]
    )
    assert [item.ts_code for item in built.excluded] == [SECURITIES[0]]
    assert "beyond_registry_snapshot" in built.excluded[0].reason
    assert {item.ts_code for item in built.sections[0].examples} == set(SECURITIES[1:])


def test_a_label_that_closed_exactly_when_the_fold_was_asked_survives_the_purge() -> None:
    """Equality is admitted, which is `V2-P4-011`'s rule and not a looseness inherited from it.

    "Training through last night's close and predicting as of it is what a daily production
    model does" is what `PredictionBatch` says about its own floor, and a purge that read `>=`
    would throw that fold's last honest day away. The 09:00 corpus every other test here uses
    **cannot see the difference**: no 15:00 close ever equals a 09:00 instant, so both readings
    remove the same set. This corpus dates its cross sections at the close instead, which puts a
    training label's exit exactly on the fold's first prediction instant.
    """
    at_the_close = panel(aligned_from=ALIGNED_FROM_OVERLAPPING, at=SESSION_CLOSE)
    fold = WalkForwardFold(
        panel=at_the_close,
        calendar=trading_calendar(),
        first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
        test_day_count=TEST_DAYS_PER_FOLD,
        embargo_sessions=0,
    )
    axis = sessions()
    on_the_instant = [
        example
        for example in fold.candidates
        if example.label.window.close_instant(example.label.window.exit_day)
        == fold.first_test_as_of
    ]
    assert {item.label.window.prediction_day for item in on_the_instant} == {axis[6]}
    assert set(on_the_instant) <= set(fold.train_examples)
    assert _days(fold.purged) == set(axis[7:12])
    assert fold.embargoed == ()
    assert fold.training_set.training_cutoff == fold.first_test_as_of


def test_the_panel_refuses_two_cross_sections_of_one_prediction_day() -> None:
    """`V2-P4-012` hands this shape here on purpose, and this is the answer to it.

    `test_feature_matrix_reads.py`'s own note says a readiness check "answers happily and hands
    `V2-P4-013` two observations of 2026-01-15's market to split". A fold's block is counted in
    prediction days, so two sections of one day make every boundary after it mean a different
    span than it names.
    """
    days = prediction_days()
    morning = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1])[0]
    afternoon = labelled_sections(
        aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1], at=SESSION_CLOSE
    )[0]
    assert morning.cross_section.as_of != afternoon.cross_section.as_of
    with pytest.raises(WalkForwardError, match="not strictly increasing"):
        labelled_panel([morning, afternoon])


def test_the_panel_refuses_two_horizons() -> None:
    """One purge cannot be right for two horizons, and each fold alone would look consistent.

    `TrainingSet` already refuses a *set* that mixes them. It cannot refuse a **panel** that
    mixes them, because a panel whose early days are `5d` and whose late days are `10d` produces
    a fold whose training set is wholly `5d` and whose test set is wholly `10d` -- two valid
    training sets, and a purge whose reach is wrong for one of them.
    """
    days = prediction_days()
    short = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1])[0]
    long = LabelledCrossSection(
        cross_section=cross_section_for(days[1]),
        labels=labels_for(days[1], aligned_from=ALIGNED_FROM_OVERLAPPING, horizon="10d"),
    )
    with pytest.raises(WalkForwardError, match="mixes horizons"):
        labelled_panel([short, long])


def test_the_panel_refuses_two_exchanges_and_two_zones() -> None:
    """Two session axes, and two clocks, are the two inputs both rules are measured on."""
    days = prediction_days()
    home = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1])[0]
    away = LabelledCrossSection(
        cross_section=cross_section_for(days[1]),
        labels=labels_for(days[1], aligned_from=ALIGNED_FROM_OVERLAPPING, exchange="SSE"),
    )
    with pytest.raises(WalkForwardError, match="mixes exchanges"):
        labelled_panel([home, away])

    elsewhere = LabelledCrossSection(
        cross_section=cross_section_for(days[1]),
        labels=labels_for(
            days[1], aligned_from=ALIGNED_FROM_OVERLAPPING, zone=ZoneInfo("Asia/Tokyo")
        ),
    )
    with pytest.raises(WalkForwardError, match="mixes zones"):
        labelled_panel([home, elsewhere])


def test_the_panel_refuses_two_labels_for_one_security_on_one_day() -> None:
    """Not repairable downstream: only one of the two would ever become an example.

    `TrainingSet` refuses one security twice on one prediction day, but it would never see the
    second one -- a join keyed by security keeps whichever label arrived last, and which of the
    two a fit consumed would not be recoverable from anything the panel carries.
    """
    days = prediction_days()
    labels = labels_for(days[0], aligned_from=ALIGNED_FROM_OVERLAPPING)
    with pytest.raises(WalkForwardError, match="carries two labels"):
        labelled_panel(
            [
                LabelledCrossSection(
                    cross_section=cross_section_for(days[0]),
                    labels=(*labels, labels[0]),
                )
            ]
        )


def test_a_label_naming_a_security_the_cross_section_never_offered_is_disclosed() -> None:
    """The other direction of the join, and it leaves through the same door."""
    days = prediction_days()
    section = labelled_sections(aligned_from=ALIGNED_FROM_OVERLAPPING, days=days[:1])[0]
    narrowed = dataclasses.replace(
        section,
        cross_section=dataclasses.replace(
            section.cross_section,
            rows=section.cross_section.rows[1:],
        ),
    )
    built = labelled_panel([narrowed])
    assert [item.ts_code for item in built.excluded] == [SECURITIES[0]]
    assert "no feature row" in built.excluded[0].reason


def test_a_fold_whose_block_runs_past_the_panel_is_refused_rather_than_shortened() -> None:
    """Slicing would answer with a shorter block: a fold testing on fewer days than it names."""
    with pytest.raises(WalkForwardError, match="runs past this panel's last prediction day"):
        WalkForwardFold(
            panel=panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            first_test_day=prediction_days()[-2],
            test_day_count=4,
            embargo_sessions=0,
        )


def test_a_fold_refuses_an_empty_block_and_a_negative_embargo() -> None:
    """Both are refused on the fold, which is the one type every path goes through.

    `walk_forward_folds` builds folds, so a refusal stated here covers the schedule too --
    which is why the schedule carries no second copy of either check.
    """
    for count, width, message in ((0, 0, "tests on 0 prediction day"), (1, -1, "embargo of -1")):
        with pytest.raises(WalkForwardError, match=message):
            WalkForwardFold(
                panel=panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
                calendar=trading_calendar(),
                first_test_day=prediction_days()[FIRST_TEST_DAY_INDEX],
                test_day_count=count,
                embargo_sessions=width,
            )


def test_the_schedule_refuses_a_fold_the_two_rules_would_empty() -> None:
    """A schedule hands back folds that run, or it hands back nothing.

    Without this the emptiness surfaces later, at whichever caller first touched
    `training_set` -- which on a long schedule is after the first `k` folds have already been
    fitted.
    """
    with pytest.raises(WalkForwardError, match="has no training example left"):
        walk_forward_folds(
            panel(aligned_from=ALIGNED_FROM_OVERLAPPING),
            calendar=trading_calendar(),
            folds=FOLDS,
            test_days_per_fold=TEST_DAYS_PER_FOLD,
            embargo_sessions=FIRST_TEST_DAY_INDEX,
        )


def test_the_test_set_is_the_blocks_labelled_rows_and_the_panel_holds_every_section() -> None:
    """The accessors `V2-P4-014` reads a fold through, pinned apart from the training one."""
    fold = _folds()[0]
    assert set(fold.test_set.examples) == set(fold.test_examples)
    assert set(fold.test_set.examples) & set(fold.training_set.examples) == set()
    assert _days(fold.test_set.examples) == set(fold.test_days)

    built = fold.panel
    assert len(built.examples) == len(SECURITIES) * len(built.sections)
    assert _days(built.examples) == set(built.prediction_days)
