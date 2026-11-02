"""`backtest/decile_readout.py` (`V2-P7-004`): one cross section's deciles, tails and rank IC, and
one dated series' dispersion, held to hand-computed numbers on cross sections generated here."""

from __future__ import annotations

import math
import random
import statistics
from datetime import date, timedelta

import pytest

from openalpha_cn.backtest.decile_readout import (
    DECILES,
    DecileReadoutError,
    read_cross_section,
    summarize_series,
)

CODES = tuple(f"6000{n:02d}.SH" for n in range(20))
"""Twenty names; `CODES[p]` scores `p`, so worst first is code order."""
SCORED = tuple((code, float(position)) for position, code in enumerate(CODES))


def tail_labels(scale: float = 1.0) -> dict[str, float]:
    """A loser tail: positions 0-1 lose 3%, positions 2-3 lose 1%, the other sixteen gain 1%."""

    def label(position: int) -> float:
        if position < 2:
            return -0.03 * scale
        if position < 4:
            return -0.01 * scale
        return 0.01 * scale

    return {code: label(position) for position, code in enumerate(CODES)}


def test_a_loser_tail_is_read_as_hand_computed() -> None:
    """EW = (2(-0.03) + 2(-0.01) + 16(0.01)) / 20 = 0.004. Decile 0 is positions 0-1, decile 1
    positions 2-3. The labels tie in three groups (average ranks 1.5, 3.5, 12.5); over centred
    ranks the covariance sum is 324, the score ranks' sum of squares 665 and the labels' 324, so
    the rank IC is sqrt(324 / 665). Dropping the worst 2 (k=1) leaves 0.14 / 18."""
    read = read_cross_section(SCORED, tail_labels(), loser_decile_count=2, tails=(1, 2, 3))

    assert (read.scope, read.labelled, read.unlabelled) == (20, 20, 0)
    assert read.scope_by_decile == (2,) * 10
    assert read.labelled_by_decile == (2,) * 10
    assert read.equal_weight == pytest.approx(0.004, abs=1e-15)
    assert read.decile_means[0] == pytest.approx(-0.03, abs=1e-15)
    assert read.decile_means[1] == pytest.approx(-0.01, abs=1e-15)
    assert all(mean == pytest.approx(0.01, abs=1e-15) for mean in read.decile_means[2:])
    assert read.decile_excess[0] == pytest.approx(-0.034, abs=1e-15)
    assert read.decile_excess[1] == pytest.approx(-0.014, abs=1e-15)
    assert all(excess == pytest.approx(0.006, abs=1e-15) for excess in read.decile_excess[2:])
    assert [k for k, _ in read.tail_excess] == [1, 2, 3]
    assert read.tail_excess[0][1] == pytest.approx(0.14 / 18 - 0.004, abs=1e-15)
    assert read.tail_excess[1][1] == pytest.approx(0.006, abs=1e-15)
    assert read.tail_excess[2][1] == pytest.approx(0.006, abs=1e-15)
    assert read.survivors_minus_ew == pytest.approx(0.006, abs=1e-15)
    assert read.ew_minus_worst_decile == pytest.approx(0.034, abs=1e-15)
    assert read.top_minus_bottom == pytest.approx(0.04, abs=1e-15)
    assert read.rank_ic == pytest.approx(math.sqrt(324 / 665), abs=1e-12)
    assert read.rank_ic_absence is None


def test_reversing_the_scores_reverses_the_sign_of_every_screen_metric() -> None:
    """Every metric is oriented so that greater than zero supports the screen."""
    forward = read_cross_section(SCORED, tail_labels(), loser_decile_count=2)
    backward = read_cross_section(
        tuple((code, -score) for code, score in SCORED), tail_labels(), loser_decile_count=2
    )

    for name in ("rank_ic", "survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"):
        assert getattr(forward, name) > 0, name
        assert getattr(backward, name) < 0, name


def test_an_unlabelled_name_keeps_its_decile_and_leaves_every_number() -> None:
    """The deciles are cut over the whole scope before any outcome; one of the worst and one of
    the best lose their label and are counted, never read as zero."""
    labels = tail_labels()
    del labels[CODES[0]], labels[CODES[19]]

    read = read_cross_section(SCORED, labels, loser_decile_count=2)

    assert (read.scope, read.labelled, read.unlabelled) == (20, 18, 2)
    assert read.scope_by_decile == (2,) * 10
    assert read.labelled_by_decile == (1, *(2,) * 8, 1)
    assert read.equal_weight == pytest.approx((-0.03 - 0.02 + 15 * 0.01) / 18, abs=1e-15)
    assert read.decile_means[0] == pytest.approx(-0.03, abs=1e-15)
    assert read.decile_means[9] == pytest.approx(0.01, abs=1e-15)
    equal_weight = 0.10 / 18
    assert read.tail_excess[0][1] == pytest.approx(0.13 / 17 - equal_weight, abs=1e-15)
    assert read.tail_excess[1][1] == pytest.approx(0.01 - equal_weight, abs=1e-15)
    assert read.tail_excess[2][1] == pytest.approx(0.01 - equal_weight, abs=1e-15)
    assert read.survivors_minus_ew == pytest.approx(0.01 - equal_weight, abs=1e-15)


def test_a_tail_is_cut_over_the_whole_scope_not_among_the_names_that_earned_a_label() -> None:
    """Position 10 loses its label and is a survivor. EW = (2(-0.03) + 2(-0.01) + 15(0.01)) / 19 =
    0.07 / 19. Dropping the worst 20 * k // 10 names of the *scope* leaves, for k=1, positions 2-19
    less 10 (17 names: -0.02 + 0.15) and for k=2, positions 4-19 less 10 (15 names of 0.01). A cut
    among the 19 labelled names would drop 19 * 1 // 10 = 1 name for k=1 and 19 * 2 // 10 = 3 for
    k=2, and keep a name the report's survivors have already shed."""
    labels = tail_labels()
    del labels[CODES[10]]
    equal_weight = 0.07 / 19

    read = read_cross_section(SCORED, labels, loser_decile_count=2, tails=(1, 2, 3))

    assert (read.scope, read.labelled, read.unlabelled) == (20, 19, 1)
    assert read.labelled_by_decile == (2, 2, 2, 2, 2, 1, 2, 2, 2, 2)
    assert read.equal_weight == pytest.approx(equal_weight, abs=1e-15)
    assert read.tail_excess[0][1] == pytest.approx(0.13 / 17 - equal_weight, abs=1e-15)
    assert read.tail_excess[1][1] == pytest.approx(0.01 - equal_weight, abs=1e-15)
    assert read.tail_excess[2][1] == pytest.approx(0.01 - equal_weight, abs=1e-15)
    assert read.survivors_minus_ew == pytest.approx(0.01 - equal_weight, abs=1e-15)


def test_the_survivors_are_the_tail_the_book_drops_not_the_first_tail_asked_for() -> None:
    """A book that drops one decile has the k=1 tail as its survivors, whatever else is asked."""
    labels = tail_labels()
    del labels[CODES[10]]

    read = read_cross_section(SCORED, labels, loser_decile_count=1, tails=(2,))

    assert read.survivors_minus_ew == pytest.approx(0.13 / 17 - 0.07 / 19, abs=1e-15)
    assert read.tail_excess == ((2, pytest.approx(0.01 - 0.07 / 19, abs=1e-15)),)


def test_the_widest_tail_and_a_book_that_drops_no_loser_can_be_read() -> None:
    """k=9 drops 18 of 20 and keeps the two best (0.01 each, against an equal weight of 0.004); a
    book that drops nobody keeps every labelled name, so its survivors are the equal weight."""
    widest = read_cross_section(SCORED, tail_labels(), loser_decile_count=9, tails=(9,))
    nobody = read_cross_section(SCORED, tail_labels(), loser_decile_count=0)

    assert widest.tail_excess == ((9, pytest.approx(0.006, abs=1e-15)),)
    assert widest.survivors_minus_ew == pytest.approx(0.006, abs=1e-15)
    assert nobody.survivors_minus_ew == 0.0


def test_the_cut_is_by_score_then_code_and_does_not_depend_on_input_order() -> None:
    """600001 and 600009 tie at -1.0 across the first decile boundary (positions 1 and 2 of
    20): the code breaks the tie, so 600001 is in decile 0 and 600009 is not."""
    others = tuple((f"6001{n:02d}.SH", 1.0 + n) for n in range(17))
    scored = (("600009.SH", -1.0), ("600001.SH", -1.0), ("600005.SH", -3.0), *others)
    labels = {code: 0.0 for code, _ in scored} | {"600001.SH": -0.05, "600009.SH": 0.05}

    read = read_cross_section(scored, labels, loser_decile_count=2)
    shuffled = list(scored)
    random.Random(4).shuffle(shuffled)

    assert read.decile_means[0] == pytest.approx(-0.025, abs=1e-15)
    assert read_cross_section(tuple(shuffled), labels, loser_decile_count=2) == read


def test_a_tail_drops_n_k_tenths_rounded_down() -> None:
    """Nineteen names: k=1 drops 19 // 10 = 1 name (position 0), not round(1.9) = 2."""
    scored = tuple((f"6000{n:02d}.SH", float(n)) for n in range(19))
    labels = {code: 0.0 for code, _ in scored} | {"600000.SH": -1.0, "600001.SH": 1.0}

    read = read_cross_section(scored, labels, loser_decile_count=1, tails=(1,))

    assert read.equal_weight == pytest.approx(0.0, abs=1e-15)
    assert read.tail_excess == ((1, pytest.approx(1 / 18, abs=1e-15)),)


@pytest.mark.parametrize(
    ("scored", "labels", "code"),
    [
        (SCORED[:1], {CODES[0]: 0.01}, "fewer_than_two_labelled"),
        (
            tuple((code, 1.0) for code in CODES[:3]),
            {code: float(index) for index, code in enumerate(CODES[:3])},
            "scores_tied",
        ),
        (SCORED[:3], {code: 0.01 for code in CODES[:3]}, "labels_tied"),
        (
            tuple((code, 1.0) for code in CODES[:3]),
            {code: 0.01 for code in CODES[:3]},
            "scores_tied",
        ),
    ],
    ids=["one_name", "scores_tied", "labels_tied", "both_tied_says_scores_first"],
)
def test_a_degenerate_rank_ic_is_absent_with_a_code(
    scored: tuple[tuple[str, float], ...], labels: dict[str, float], code: str
) -> None:
    read = read_cross_section(scored, labels, loser_decile_count=2)

    assert (read.rank_ic, read.rank_ic_absence) == (None, code)


@pytest.mark.parametrize(
    ("scored", "labels", "options", "said"),
    [
        ((("600000.SH", 1.0), ("600000.SH", 2.0)), {}, {}, "more than once"),
        ((("600000.SH", math.nan),), {}, {}, "finite"),
        (SCORED[:2], {"000001.SZ": 0.01}, {}, "not scored"),
        ((), {}, {}, "empty"),
        (SCORED[:2], {CODES[0]: math.nan}, {}, "finite"),
        (SCORED, {}, {"loser_decile_count": 10}, "loser_decile_count"),
        (SCORED, {}, {"loser_decile_count": -1}, "loser_decile_count"),
        (SCORED, {}, {"tails": (0,)}, "tail"),
        (SCORED, {}, {"tails": (10,)}, "tail"),
        (SCORED, {}, {"tails": (1, 1)}, "tail"),
    ],
)
def test_a_cross_section_that_cannot_be_read_is_refused_by_name(
    scored: tuple[tuple[str, float], ...],
    labels: dict[str, float],
    options: dict[str, object],
    said: str,
) -> None:
    arguments: dict[str, object] = {"loser_decile_count": 2, **options}
    with pytest.raises(DecileReadoutError, match=said):
        read_cross_section(scored, labels, **arguments)  # type: ignore[arg-type]


def test_a_cyclic_no_signal_panel_shows_no_structure_on_average() -> None:
    """Ten names scored 0..9 over ten sessions; on session t the name of rank r earns
    ((r + t) mod 10) / 100. Each decile holds one name and earns 4.5% on average; the rank IC of
    session t is (33 - 2 t (10 - t)) / 33, and the ten sum to zero: nothing to find."""
    codes = CODES[:10]
    scored = tuple((code, float(rank)) for rank, code in enumerate(codes))
    reads = [
        read_cross_section(
            scored,
            {code: ((rank + t) % 10) / 100 for rank, code in enumerate(codes)},
            loser_decile_count=2,
        )
        for t in range(10)
    ]

    assert [read.rank_ic for read in reads] == pytest.approx(
        [(33 - 2 * t * (10 - t)) / 33 for t in range(10)], abs=1e-12
    )
    for decile in range(DECILES):
        mean = statistics.fmean(read.decile_excess[decile] for read in reads)  # type: ignore[misc]
        assert mean == pytest.approx(0.0, abs=1e-15), decile
    for name in ("survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"):
        assert statistics.fmean(getattr(read, name) for read in reads) == pytest.approx(
            0.0, abs=1e-15
        ), name
    series = summarize_series(
        [(date(2021, 1, 4 + t), read.rank_ic) for t, read in enumerate(reads)]  # type: ignore[misc]
    )
    assert series.mean == pytest.approx(0.0, abs=1e-12)
    assert series.sd == pytest.approx(math.sqrt(2442 / 1089 / 9), rel=1e-12)


def test_a_series_summary_is_hand_computed() -> None:
    """[0.1, 0.2, -0.1, 0.4]: mean 0.15, squared deviations sum 0.13, so sd = sqrt(0.13 / 3);
    lag-1 products -0.0025 - 0.0125 - 0.0625 = -0.0775 over 0.13."""
    summary = summarize_series(
        [
            (date(2020, 3, 2), 0.1),
            (date(2020, 4, 1), 0.2),
            (date(2021, 3, 1), -0.1),
            (date(2021, 4, 1), 0.4),
        ]
    )

    assert summary.n == 4
    assert summary.mean == pytest.approx(0.15, abs=1e-15)
    assert summary.sd == pytest.approx(math.sqrt(0.13 / 3), abs=1e-15)
    assert summary.se == pytest.approx(math.sqrt(0.13 / 3) / 2, abs=1e-15)
    assert summary.t == pytest.approx(0.15 / (math.sqrt(0.13 / 3) / 2), abs=1e-12)
    assert summary.share_positive == 0.75
    assert summary.lag1_autocorrelation == pytest.approx(-0.0775 / 0.13, abs=1e-12)
    assert summary.by_year == ((2020, 2, pytest.approx(0.15)), (2021, 2, pytest.approx(0.15)))
    assert summary.absence is None


def test_a_degenerate_series_says_what_it_lacks() -> None:
    assert summarize_series([]).absence == "no_values"
    one = summarize_series([(date(2021, 1, 4), 0.1)])
    assert (one.n, one.mean, one.sd, one.absence) == (1, 0.1, None, "fewer_than_two_values")
    flat = summarize_series([(date(2021, 1, 4), 0.1), (date(2021, 2, 1), 0.1)])
    assert (flat.sd, flat.t, flat.absence) == (0.0, None, "no_dispersion")
    with pytest.raises(DecileReadoutError, match="ascending"):
        summarize_series([(date(2021, 2, 1), 0.1), (date(2021, 1, 4), 0.2)])
    with pytest.raises(DecileReadoutError, match="finite"):
        summarize_series([(date(2021, 1, 4), math.inf)])


def dated(*values: float) -> list[tuple[date, float]]:
    """One value per consecutive day from 2021-01-04."""
    return [(date(2021, 1, 4) + timedelta(days=index), value) for index, value in enumerate(values)]


def test_a_zero_is_not_a_positive_value() -> None:
    """[0, 0.1, -0.1, 0.2]: two of the four are above zero."""
    summary = summarize_series(dated(0.0, 0.1, -0.1, 0.2))

    assert summary.share_positive == 0.5


def test_lag_one_needs_three_values() -> None:
    """Two values with dispersion have an sd, a t and no autocorrelation; [1, 2, 4] is the
    smallest series that has one: centred -4/3, -1/3, 5/3, so (4/9 - 5/9) / (42/9) = -1/42."""
    two = summarize_series(dated(0.1, 0.3))
    three = summarize_series(dated(1.0, 2.0, 4.0))

    assert two.sd == pytest.approx(math.sqrt(0.02), abs=1e-15)
    assert two.se == pytest.approx(0.1, abs=1e-15)
    assert two.t == pytest.approx(2.0, abs=1e-12)
    assert (two.lag1_autocorrelation, two.absence) == (None, None)
    assert three.lag1_autocorrelation == pytest.approx(-1 / 42, abs=1e-12)


def test_a_constant_series_of_three_has_no_dispersion_and_so_no_autocorrelation() -> None:
    """The float mean of [0.1] * 3 is not exactly 0.1, so centring leaves rounding noise whose
    ratio to itself would read as a perfect autocorrelation; the exact sd of 0 is what rules it
    out."""
    constant = summarize_series(dated(0.1, 0.1, 0.1))

    assert (constant.sd, constant.se, constant.t) == (0.0, 0.0, None)
    assert (constant.lag1_autocorrelation, constant.absence) == (None, "no_dispersion")
