"""`domain/index_membership.py`: composition, weights, staleness, and the sum tolerance.

Every number in this file is from a live probe of Tushare's `index_weight` endpoint on
2026-08-09 covering **633 publications** of `000300.SH` (2005-04..2026-07), `000905.SH`
(2007-01..2026-07) and `000852.SH` (2014-10..2026-07) -- 336,298 constituent rows. Nothing here
touches the network; the measurements are inlined.

The two facts this module exists to keep apart are also the two the tests are organised around:

- **Composition** is what the index publisher said the members are. It survives a forward fill
  better than the weights do and *not* perfectly, in two unrelated ways with two tests:
  `test_a_forward_filled_composition_can_name_a_terminated_security` pins the 38 terminations,
  and `test_a_scheduled_review_takes_effect_before_the_publication_that_reports_it` pins the
  much larger one -- the publisher's twice-yearly review is in force for ten sessions before
  `index_weight` reports it, 49,900 measured (name x session) answers over the whole history.
- **Weights** are a month-end snapshot that starts drifting the next session, and there is no
  `IndexMembership.weight_of(code, day)` signature that would let a caller carry the number away
  without the date. `weights_on(day).weight_of(code)` reaches a bare float in one line and is
  meant to; what is refused is the callable a caller could store and read back later as a fact
  about the day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from openalpha_cn.domain.index_membership import (
    CSI300_INDEX_CODE,
    CSI500_INDEX_CODE,
    CSI1000_INDEX_CODE,
    INDEX_WEIGHT_DATASET,
    INDEX_WEIGHT_INDEX_CODES,
    INDEX_WEIGHT_PANEL_COLUMNS,
    KNOWN_INDEX_MEMBERSHIP_LIMITATIONS,
    MAX_PUBLISHED_WEIGHT_DECIMALS,
    TOTAL_PUBLISHED_WEIGHT,
    ConstituentWeight,
    IndexMembershipError,
    IndexMembershipHorizonError,
    IndexPublication,
    build_index_membership,
    build_index_publication,
    constituent_listing_report,
    index_memberships_from_panel_rows,
    published_weight_decimals,
    published_weight_tolerance,
)
from openalpha_cn.domain.stock_universe import SecurityLifecycle, build_stock_universe

# --------------------------------------------------------------------------------------
# Measured corpus statistics, inlined.
# --------------------------------------------------------------------------------------

MEASURED_PUBLICATION_TOTALS: tuple[tuple[str, str, int, int, float], ...] = (
    # (index, publication date, constituent count, published decimals, sum of weights)
    # The fifteen widest deviations from 100 in the whole 633-publication corpus, plus the
    # three 2024-06-28 cross sections the roadmap brief quoted. The wide ones all sit in the
    # 2011..2015 era, when the endpoint published two decimals instead of three.
    ("000852.SH", "20141031", 1000, 2, 100.21),
    ("000905.SH", "20140829", 500, 2, 100.19),
    ("000905.SH", "20120731", 500, 2, 99.83),
    ("000905.SH", "20120531", 500, 2, 100.14),
    ("000905.SH", "20150529", 500, 2, 100.12),
    ("000300.SH", "20141128", 300, 2, 100.11),
    ("000905.SH", "20121130", 500, 2, 99.89),
    ("000300.SH", "20130426", 300, 2, 99.9),
    ("000852.SH", "20150130", 1000, 2, 100.09),
    ("000905.SH", "20141128", 500, 2, 100.09),
    ("000300.SH", "20130531", 300, 2, 100.09),
    ("000852.SH", "20150227", 1000, 2, 99.91),
    ("000300.SH", "20140130", 300, 2, 100.08),
    ("000905.SH", "20130830", 500, 2, 100.08),
    ("000300.SH", "20130228", 300, 2, 100.08),
    ("000300.SH", "20240628", 300, 3, 100.002),
    ("000905.SH", "20240628", 500, 3, 99.997),
    ("000852.SH", "20240628", 1000, 3, 100.002),
    # The publication that fits its own bound most tightly of all 633; see
    # `MEASURED_TIGHTEST_RATIO`.
    ("000905.SH", "20090123", 500, 3, 99.977),
)

MEASURED_TIGHTEST_RATIO = 0.092
"""The largest measured `deviation / tolerance` over all 633 publications.

`000905.SH` on 2009-01-23: 500 names at three decimals summing to 99.977, a deviation of 0.023
against a tolerance of 0.25. The bound is therefore about eleven times looser than the worst
real publication needs, which is what an arithmetic worst case looks like next to independent
rounding errors -- and is exactly why it is not tightened to the measured number.
"""


def _weights(pairs: tuple[tuple[str, float], ...]) -> tuple[ConstituentWeight, ...]:
    return tuple(ConstituentWeight(con_code=code, weight=weight) for code, weight in pairs)


def _publication(
    day: date,
    pairs: tuple[tuple[str, float], ...],
    *,
    index_code: str = CSI300_INDEX_CODE,
) -> IndexPublication:
    """A publication built without validation, the way `CalendarDay` is built in its tests.

    `IndexPublication` is a plain carrier -- the rules live once, in `build_index_publication`
    -- so a structural test can use a three-name index without having to make its weights sum
    to 100 first.
    """
    return IndexPublication(index_code=index_code, published_on=day, weights=_weights(pairs))


NOVEMBER = _publication(date(2024, 11, 29), (("600000.SH", 40.0), ("600519.SH", 60.0)))
DECEMBER = _publication(date(2024, 12, 31), (("600519.SH", 55.0), ("600036.SH", 45.0)))


# --------------------------------------------------------------------------------------
# The 2026 June review, as published. Real codes and real weights.
# --------------------------------------------------------------------------------------

CSI300_2026_MAY_REMOVED: tuple[tuple[str, float], ...] = (
    # The 19 names in 000300.SH's 2026-05-29 publication that its 2026-06-30 one no longer
    # carries, at the weights the May publication gave them.
    ("000661.SZ", 0.09),
    ("000786.SZ", 0.088),
    ("000876.SZ", 0.067),
    ("000983.SZ", 0.072),
    ("002252.SZ", 0.106),
    ("002459.SZ", 0.068),
    ("002601.SZ", 0.089),
    ("300347.SZ", 0.076),
    ("300759.SZ", 0.097),
    ("300782.SZ", 0.14),
    ("300979.SZ", 0.022),
    ("600161.SH", 0.05),
    ("600377.SH", 0.024),
    ("601236.SH", 0.036),
    ("601298.SH", 0.024),
    ("601808.SH", 0.029),
    ("603195.SH", 0.042),
    ("688169.SH", 0.08),
    ("688187.SH", 0.06),
)

CSI300_2026_JUNE_ADDED: tuple[tuple[str, float], ...] = (
    # The 19 names 000300.SH's 2026-06-30 publication carries and its 2026-05-29 one did not.
    ("000657.SZ", 0.308),
    ("000988.SZ", 0.656),
    ("001280.SZ", 0.039),
    ("002202.SZ", 0.233),
    ("002353.SZ", 0.331),
    ("002532.SZ", 0.123),
    ("002558.SZ", 0.13),
    ("002602.SZ", 0.294),
    ("002837.SZ", 0.215),
    ("300450.SZ", 0.158),
    ("301165.SZ", 0.038),
    ("301308.SZ", 0.632),
    ("600118.SH", 0.172),
    ("600221.SH", 0.123),
    ("600549.SH", 0.335),
    ("601727.SH", 0.156),
    ("688072.SH", 0.582),
    ("688183.SH", 0.156),
    ("688521.SH", 0.552),
)

CSI300_2026_REVIEW_SESSIONS: tuple[date, ...] = tuple(
    date(2026, 6, day) for day in (15, 16, 17, 18, 22, 23, 24, 25, 26, 29)
)
"""Every open session between the 2026 June review taking effect and being published.

The review takes effect after the close of 2026-06-12, the second Friday, so 2026-06-15 is the
first session it governs; `index_weight` does not carry it until 2026-06-30. Read off
`index_daily(000300.SH)`: ten sessions, 2026-06-19 being the Dragon Boat holiday.
"""

CSI300_2026_MAY = _publication(date(2026, 5, 29), (("600519.SH", 3.108), *CSI300_2026_MAY_REMOVED))
CSI300_2026_JUNE = _publication(date(2026, 6, 30), (("600519.SH", 2.62), *CSI300_2026_JUNE_ADDED))
"""The two publications either side of the 2026 June review, reduced to what changed.

Real constituent codes at their real published weights, with 600519.SH 贵州茅台 as an unchanged
anchor. The full cross sections are 300 rows each and the sum rule is tested on its own above;
what these two are here to pin is the *difference*, which is the whole of it.
"""


# --------------------------------------------------------------------------------------
# The weight-sum tolerance
# --------------------------------------------------------------------------------------


def test_the_sum_tolerance_admits_every_publication_measured_over_twenty_one_years() -> None:
    """The bound is derived from the publication's own precision, not calibrated on a sample.

    `n` cells each rounded to `d` decimals can move the total by at most `n * 0.5 * 10**-d`, so
    a correctly rounded publication cannot breach it whatever `n` and `d` are. That is what
    makes it safe to apply to a 1,000-name index published at two decimals and a 300-name one
    published at three, which a single constant cannot be: the brief for this task quoted
    100.002 / 99.997 / 100.002 from one 2024 session, and a 0.003 tolerance drawn from those
    would refuse **more than half** the corpus -- the median deviation is 0.005 and the worst is
    0.21.
    """
    ratios: list[float] = []
    for index_code, day, count, decimals, total in MEASURED_PUBLICATION_TOTALS:
        tolerance = published_weight_tolerance(count=count, decimals=decimals)
        deviation = abs(total - TOTAL_PUBLISHED_WEIGHT)
        assert deviation <= tolerance, (
            f"{index_code} {day} sums to {total} across {count} names published at "
            f"{decimals} decimals, outside the derived tolerance {tolerance}"
        )
        ratios.append(deviation / tolerance)
    assert max(ratios) == pytest.approx(MEASURED_TIGHTEST_RATIO, abs=5e-4)


def test_the_sum_tolerance_is_not_slack_enough_to_hide_a_truncated_publication() -> None:
    """A bound that admits everything is not a check.

    The failure this witness exists for is a response the 7,000-row cap split in the middle:
    `index_weight(000300.SH, 20100101..20231231)` came back with 7,000 rows whose oldest
    publication, 2022-01-28, carried **100** of its 300 names. A hundred names of a
    three-hundred-name index sum to about a third of 100, and the tolerance for 100 cells at
    three decimals is 0.05.
    """
    tolerance = published_weight_tolerance(count=100, decimals=3)
    assert tolerance == pytest.approx(0.05)
    assert abs(33.4 - TOTAL_PUBLISHED_WEIGHT) > tolerance


def _flat_publication(count: int, weight: float) -> tuple[ConstituentWeight, ...]:
    """`count` constituents of equal `weight`. The shape is what matters here, not the names."""
    return _weights(tuple((f"{600000 + index}.SH", weight) for index in range(count)))


def test_one_constituent_going_missing_from_a_three_hundred_name_index_is_caught() -> None:
    """The realistic truncation is one dropped row, not two hundred, and the bound catches it.

    300 names at 0.333 each sum to 99.9, inside the 0.15 tolerance three decimals imply. Drop
    one and the sum falls to 99.567, a deviation of 0.433 -- more than the derived bound and far
    less than any round constant a reader might be tempted to substitute for it. That is what
    stops the tolerance being widened into a formality.
    """
    day = date(2024, 6, 28)
    whole = build_index_publication(
        index_code=CSI300_INDEX_CODE,
        published_on=day,
        weights=_flat_publication(300, 0.333),
    )
    assert whole.constituent_count == 300

    with pytest.raises(IndexMembershipError, match="sum to 100"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=day,
            weights=_flat_publication(299, 0.333),
        )


def test_a_coarser_publication_gets_the_wider_bound_its_own_precision_earns() -> None:
    """Why the precision is read per publication rather than fixed at three decimals.

    On the 633 publications measured this makes no difference -- every one of them, including
    the 2011..2015 two-decimal era, happens to sit inside the *three*-decimal bound as well, so
    the corpus alone cannot tell the two rules apart. What tells them apart is a return to
    coarser publishing, which this endpoint has already done once: 300 names published at two
    decimals may legitimately miss by up to 1.5, and a fixed three-decimal rule would refuse
    them at 0.15.
    """
    day = date(2013, 4, 26)
    coarse = build_index_publication(
        index_code=CSI300_INDEX_CODE,
        published_on=day,
        weights=_flat_publication(300, 0.33),
    )
    assert coarse.published_total == pytest.approx(99.0)
    assert published_weight_tolerance(count=300, decimals=2) == pytest.approx(1.5)
    assert published_weight_tolerance(count=300, decimals=3) == pytest.approx(0.15)


def test_published_precision_is_read_from_the_publication_rather_than_assumed() -> None:
    """The endpoint changed precision twice, so a hard-coded decimal count would be wrong.

    Three decimals in 2005..2010 and 2016..2026, two in 2013..2014, and 2011, 2012 and 2015 each
    carrying publications of both kinds -- re-aggregated for the task-32 review, which found the
    earlier wording had 2012 on the wrong side of the boundary; the two-decimal era is narrower
    than it said and the straddling one wider. Reading it per publication is the safe direction:
    `max` over the cells
    can only *under*-report the precision (every cell might end in a zero), and fewer decimals
    means a wider tolerance, never a narrower one.
    """
    assert published_weight_decimals(_weights((("a", 1.5), ("b", 98.5)))) == 1
    assert published_weight_decimals(_weights((("a", 1.5), ("b", 98.507)))) == 3
    assert published_weight_decimals(_weights((("a", 50.0), ("b", 50.0)))) == 0


def test_published_precision_is_capped_so_a_float_artefact_cannot_tighten_the_bound() -> None:
    """A seventeen-decimal cell would derive a tolerance of ~0 and refuse the whole corpus.

    No publication in 633 carries more than three decimals, so anything beyond that is an
    upstream change or a float artefact rather than real precision. Capping keeps the tolerance
    at its tightest *measured* value instead of letting one cell drive it to nothing -- and a
    genuine move to five decimals would only make the real deviations smaller, so the capped
    bound stays sound.
    """
    assert MAX_PUBLISHED_WEIGHT_DECIMALS == 3
    assert published_weight_decimals(_weights((("a", 0.3000000000000004), ("b", 1.0)))) == 3


def test_a_publication_whose_weights_do_not_add_up_is_refused_by_name() -> None:
    with pytest.raises(IndexMembershipError, match="sum to 100"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=date(2024, 6, 28),
            weights=_weights((("600000.SH", 30.0), ("600519.SH", 30.0))),
        )


def test_a_publication_that_adds_up_is_accepted_and_keeps_its_published_total() -> None:
    """100.002 is stored as published rather than normalised to 100.

    Renormalising would destroy the only statement the endpoint makes about its own rounding,
    and every downstream consumer that divides by the total would get a different answer from
    one that uses the published number.
    """
    publication = build_index_publication(
        index_code=CSI300_INDEX_CODE,
        published_on=date(2024, 6, 28),
        weights=_weights(
            (
                ("600519.SH", 5.19),
                ("300750.SZ", 2.676),
                ("601318.SH", 1.0),
                ("600036.SH", 1.0),
                ("600000.SH", 90.136),
            )
        ),
    )
    assert publication.published_total == pytest.approx(100.002)
    assert publication.constituent_count == 5
    assert publication.constituents[0] == "300750.SZ"


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), 100.5])
def test_a_weight_that_cannot_be_a_share_of_an_index_is_refused(bad: float) -> None:
    """Zero, negative, non-finite and over-100 weights are all refused.

    Measured: 336,298 constituent rows carry a weight between 0.007 and 7.745 and not one zero.
    A zero would silently drop a name out of every capitalisation-weighted calculation while
    leaving it in the membership, which is the worst of both answers.

    The pattern names the range rule rather than just "weight", because the sum rule's message
    also contains that word: with `match="weight"` a widened upper bound let 100.5 through
    `_require_weight` and be refused a step later by the total instead, with the test passing
    either way.
    """
    with pytest.raises(IndexMembershipError, match=r"must be a float in \(0, 100"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=date(2024, 6, 28),
            weights=_weights((("600519.SH", bad), ("600000.SH", 100.0))),
        )


def test_a_publication_with_no_constituents_is_refused() -> None:
    """An empty index answers "nothing is in it" to every question, which is exactly what a
    fetch that came back with no rows also looks like."""
    with pytest.raises(IndexMembershipError, match="has no constituents"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE, published_on=date(2024, 6, 28), weights=()
        )


def test_a_constituent_listed_twice_in_one_publication_is_refused() -> None:
    with pytest.raises(IndexMembershipError, match="appears more than once"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=date(2024, 6, 28),
            weights=_weights((("600519.SH", 50.0), ("600519.SH", 50.0))),
        )


# --------------------------------------------------------------------------------------
# Composition and weights are two answers, not one
# --------------------------------------------------------------------------------------


def test_a_mid_month_question_is_answered_from_the_previous_publication_and_says_so() -> None:
    """The staleness is not optional context -- it is part of both answers.

    `weights_on` has no sibling that returns a bare `float` for a day, and that is deliberate:
    the number a caller wants is a month-end snapshot, and a signature that handed it back
    naked would make "the weight on 2024-12-15" read like a fact about 2024-12-15.
    """
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))

    weights = membership.weights_on(date(2024, 12, 16))
    assert weights.as_published_on == date(2024, 11, 29)
    assert weights.days_since_publication == 17
    assert weights.is_as_published is False
    assert weights.weight_of("600519.SH") == pytest.approx(60.0)

    composition = membership.constituents_on(date(2024, 12, 16))
    assert composition.as_published_on == date(2024, 11, 29)
    assert composition.days_since_publication == 17
    assert composition.is_as_published is False
    assert composition.members == ("600000.SH", "600519.SH")


def test_a_scheduled_review_takes_effect_before_the_publication_that_reports_it() -> None:
    """The composition answer's largest failure, and it is silent rather than refused.

    The publisher's June and December reviews take effect after the close of that month's
    second Friday; `index_weight` does not carry the new list until the month's last session.
    In 2026 that is the 2026-06-12 close against the 2026-06-30 publication, and the two
    publications differ by 19 names in `000300.SH` (50 in `000905.SH`, 100 in `000852.SH`), so
    each of the ten sessions in between is answered with 19 securities the publisher had already
    removed and without 19 it had already added.

    Nothing raises. `IndexMembershipHorizonError` is not reached -- the day is inside the read
    -- and `days_since_publication` reports 17 days for 2026-06-15 without ever suggesting that
    part of what it is 17 days old *about* was replaced on day 14. Over every June and December
    review from 2013-12 to 2026-06 that is 49,900 (name x session) answers naming a removed
    security and 49,900 omitting an added one, against the 38 terminations
    `composition_is_also_forward_filled` counts.

    What the module can say from its own rows is `undated_rebalance`: these names changed
    somewhere inside this gap. It needs neither the publisher's schedule nor a trading calendar,
    which is why it is the claim this module makes.
    """
    membership = build_index_membership(CSI300_INDEX_CODE, (CSI300_2026_MAY, CSI300_2026_JUNE))
    removed = tuple(code for code, _ in CSI300_2026_MAY_REMOVED)
    added = tuple(code for code, _ in CSI300_2026_JUNE_ADDED)
    assert len(removed) == 19
    assert len(added) == 19
    assert len(CSI300_2026_REVIEW_SESSIONS) == 10

    for session in CSI300_2026_REVIEW_SESSIONS:
        composition = membership.constituents_on(session)
        assert composition.as_published_on == date(2026, 5, 29)
        assert [code for code in removed if composition.includes(code)] == list(removed)
        assert [code for code in added if composition.includes(code)] == []

        rebalance = composition.undated_rebalance
        assert rebalance is not None
        assert rebalance.previous_publication == date(2026, 5, 29)
        assert rebalance.publication == date(2026, 6, 30)
        assert rebalance.removed == removed
        assert rebalance.added == added
        assert rebalance.is_one_for_one is True
        assert len(rebalance.changed) == 38

    assert membership.constituents_on(date(2026, 6, 15)).days_since_publication == 17


def test_a_publication_day_has_nothing_carried_forward_for_a_review_to_have_broken() -> None:
    """`undated_rebalance` is about the fill, so the day the fill is empty it is `None`.

    2026-05-29 is exactly right on 2026-05-29 -- the June review had not happened -- and the
    field says so rather than naming a change that was still two weeks away. The same holds at
    the far end for a reason worth separating: 2026-06-30 is the last publication in this read,
    so there is no following one to diff against, and any day past it is refused by the horizon
    rather than answered without a rebalance.
    """
    membership = build_index_membership(CSI300_INDEX_CODE, (CSI300_2026_MAY, CSI300_2026_JUNE))

    assert membership.constituents_on(date(2026, 5, 29)).undated_rebalance is None
    assert membership.constituents_on(date(2026, 6, 30)).undated_rebalance is None
    assert membership.undated_rebalance_on(date(2026, 6, 16)) is not None


def test_the_weights_answer_carries_the_same_warning_and_hands_it_to_the_composition() -> None:
    """A name the publisher had already removed is not merely present here, it is sized.

    `600161.SH` sits in the carried-forward snapshot at its May weight through every session of
    the window, so a capitalisation-weighted sum over 2026-06-22 spends that weight on a
    security that had left the index a week earlier. `composition()` has to carry the field or
    the cheaper type would be the less honest one.
    """
    membership = build_index_membership(CSI300_INDEX_CODE, (CSI300_2026_MAY, CSI300_2026_JUNE))

    weights = membership.weights_on(date(2026, 6, 22))
    assert weights.weight_of("600161.SH") == pytest.approx(0.05)
    assert weights.undated_rebalance is not None
    assert weights.undated_rebalance.removed[:1] == ("000661.SZ",)
    assert weights.composition().undated_rebalance == weights.undated_rebalance


def test_a_gap_in_which_nothing_changed_leaves_no_undated_rebalance() -> None:
    """464 of the 630 measured transitions changed no name, so a field that was set on every
    mid-month day would be noise rather than a signal a gate could branch on."""
    unchanged = _publication(date(2024, 12, 31), (("600000.SH", 40.0), ("600519.SH", 60.0)))
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, unchanged))

    assert membership.constituents_on(date(2024, 12, 16)).undated_rebalance is None
    assert membership.rebalances() == ()


def test_the_review_window_is_a_named_limitation_and_carries_its_measurement() -> None:
    """The window has to be in the module's own disclosures, not only in a test.

    `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS` is what a caller reads to find out what this dataset
    cannot answer, and until this issue's review it named eight boundaries with the effective
    date in none of them.
    """
    detail = {entry.code: entry.detail for entry in KNOWN_INDEX_MEMBERSHIP_LIMITATIONS}
    review = detail["scheduled_review_takes_effect_before_it_is_published"]

    assert "second " in review and "Friday" in review
    for measurement in ("19", "50", "100", "10", "49,900", "2026-06-30", "2013-12"):
        assert measurement in review, measurement
    assert "undated_rebalance" in review
    assert (
        "scheduled_review_takes_effect_before_it_is_published"
        in (detail["composition_is_also_forward_filled"])
    )


def test_asking_on_a_publication_day_is_the_only_answer_that_is_not_stale() -> None:
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))

    weights = membership.weights_on(date(2024, 12, 31))
    assert weights.is_as_published is True
    assert weights.days_since_publication == 0
    assert weights.as_published_on == date(2024, 12, 31)
    assert membership.constituents_on(date(2024, 12, 31)).is_as_published is True


def test_the_composition_answer_carries_no_weight_at_all() -> None:
    """The separation is structural, not a naming convention.

    A caller that only needs membership never holds a weight, so it cannot accidentally read a
    month-old number as a current one; and a caller that needs weights holds an object whose
    own fields name the day the numbers came from.
    """
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))
    composition = membership.constituents_on(date(2024, 12, 16))

    assert not hasattr(composition, "weights")
    assert not hasattr(composition, "weight_of")
    assert membership.weights_on(date(2024, 12, 16)).composition() == composition


def test_a_weight_asked_for_a_name_that_is_not_a_constituent_is_refused() -> None:
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))
    weights = membership.weights_on(date(2024, 12, 31))

    assert weights.includes("600036.SH") is True
    assert weights.includes("600000.SH") is False
    with pytest.raises(IndexMembershipError, match="not a constituent"):
        weights.weight_of("600000.SH")


def test_a_percentage_weight_offers_its_fraction_not_a_hundredfold_error() -> None:
    """`weight` is published as a percentage: 5.19 means 5.19%, and the column sums to ~100."""
    assert TOTAL_PUBLISHED_WEIGHT == 100.0
    assert ConstituentWeight(con_code="600519.SH", weight=5.19).fraction == pytest.approx(0.0519)


# --------------------------------------------------------------------------------------
# Horizons
# --------------------------------------------------------------------------------------


def test_a_day_after_the_last_publication_is_refused_rather_than_forward_filled() -> None:
    """`AdjustmentHistory`'s upper horizon, and for a sharper reason.

    A factor moves only on corporate actions; an index's membership moved on **166 of the 630**
    publication-to-publication transitions measured, so carrying the last publication forward
    past the end of the read is an assertion about a window that demonstrably changes more often
    than not.
    """
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))
    with pytest.raises(IndexMembershipHorizonError, match="after"):
        membership.weights_on(date(2025, 1, 2))
    with pytest.raises(IndexMembershipHorizonError, match="after"):
        membership.constituents_on(date(2025, 1, 2))


def test_a_day_before_the_first_publication_is_refused_rather_than_back_filled() -> None:
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))
    assert membership.covered_from == date(2024, 11, 29)
    assert membership.covered_through == date(2024, 12, 31)
    with pytest.raises(IndexMembershipHorizonError, match="before"):
        membership.weights_on(date(2024, 11, 28))


# --------------------------------------------------------------------------------------
# Assembly rules
# --------------------------------------------------------------------------------------


def test_a_missing_month_inside_the_read_is_refused_rather_than_forward_filled_over() -> None:
    """This is what pays for `index_weight_requirement`'s waived `required_dates`.

    The endpoint publishes exactly once per calendar month -- 633 publications across 633
    distinct months, each on that month's last open SSE session, with no month carrying two and
    no month inside any index's life carrying none. A partition that lost a month would
    otherwise answer every day of it from a two-month-old snapshot, silently.
    """
    january = _publication(date(2025, 1, 27), (("600519.SH", 100.0),))
    with pytest.raises(IndexMembershipError, match="no publication for 2024-12"):
        build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, january))


def test_two_publications_in_one_month_are_refused_as_two_unreconciled_sources() -> None:
    duplicate = _publication(date(2024, 11, 15), (("600519.SH", 100.0),))
    with pytest.raises(IndexMembershipError, match="two publications in 2024-11"):
        build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, duplicate))


def test_a_publication_belonging_to_another_index_is_refused() -> None:
    other = _publication(date(2024, 12, 31), (("600519.SH", 100.0),), index_code=CSI500_INDEX_CODE)
    with pytest.raises(IndexMembershipError, match="carries"):
        build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, other))


def test_an_empty_membership_is_refused_rather_than_answering_nothing_to_everything() -> None:
    with pytest.raises(IndexMembershipError, match="at least one publication"):
        build_index_membership(CSI300_INDEX_CODE, ())


def test_publications_may_arrive_in_any_order() -> None:
    membership = build_index_membership(CSI300_INDEX_CODE, (DECEMBER, NOVEMBER))
    assert membership.publication_dates == (date(2024, 11, 29), date(2024, 12, 31))


# --------------------------------------------------------------------------------------
# Rebalances
# --------------------------------------------------------------------------------------


def test_a_rebalance_names_who_joined_and_who_left() -> None:
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))
    (rebalance,) = membership.rebalances()

    assert rebalance.previous_publication == date(2024, 11, 29)
    assert rebalance.publication == date(2024, 12, 31)
    assert rebalance.added == ("600036.SH",)
    assert rebalance.removed == ("600000.SH",)
    assert rebalance.is_one_for_one is True


def test_a_transition_that_changed_nothing_is_not_a_rebalance() -> None:
    """Most transitions are not rebalances, so reporting all of them would bury the ones that
    are: 464 of the 630 measured transitions left the membership untouched."""
    unchanged = _publication(date(2024, 12, 31), (("600000.SH", 41.0), ("600519.SH", 59.0)))
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, unchanged))
    assert membership.rebalances() == ()


def test_a_removal_with_no_replacement_is_reported_as_such() -> None:
    """Measured once in 633 publications, and it is why the constituent count is data rather
    than a constant -- see `test_the_constituent_count_is_not_asserted_to_match_the_index_name`.
    """
    december = _publication(date(2024, 12, 31), (("600519.SH", 100.0),))
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, december))
    (rebalance,) = membership.rebalances()

    assert rebalance.added == ()
    assert rebalance.removed == ("600000.SH",)
    assert rebalance.is_one_for_one is False


def test_the_constituent_count_is_not_asserted_to_match_the_index_name() -> None:
    """`000300.SH` published **298** constituents on 2009-12-31.

    600001.SH (邯郸钢铁) and 600357.SH (承德钒钛) were both terminated on 2009-12-29 and the
    December publication dropped them without a replacement; the January 2010 review restored
    the count to 300. It is the only off-nominal publication in the whole corpus, and it is why
    nothing here checks the count against the index's name -- a rule that did would refuse a
    real partition on the one day it mattered.
    """
    publication = build_index_publication(
        index_code=CSI300_INDEX_CODE,
        published_on=date(2009, 12, 31),
        weights=_weights((("600519.SH", 40.0), ("600000.SH", 60.0))),
    )
    assert publication.constituent_count == 2


# --------------------------------------------------------------------------------------
# The stock-universe join
# --------------------------------------------------------------------------------------


def _registry_universe() -> object:
    return build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=(
            SecurityLifecycle(
                ts_code="600270.SH",
                exchange="SSE",
                listed_on=date(2000, 12, 28),
                delisted_on=date(2018, 12, 28),
            ),
            SecurityLifecycle(ts_code="600000.SH", exchange="SSE", listed_on=date(1999, 11, 10)),
        ),
    )


def test_a_published_constituent_can_be_delisted_on_its_own_publication_day() -> None:
    """The measured counterexample to "a constituent must have been listed that day".

    `600270.SH` 外运发展(退) is in `000905.SH`'s 2018-12-28 publication at weight **0.253**, and
    its registry `delist_date` is **2018-12-28** -- which `domain/stock_universe.py` treats as
    exclusive, so the security was not listed that day. One constituent-day out of the 336,298
    measured, and the join reports it instead of refusing the publication, because refusing
    would throw away a real month of a real index over one upstream inconsistency.
    """
    publication = _publication(
        date(2018, 12, 28),
        (("600270.SH", 0.253), ("600000.SH", 99.747)),
        index_code=CSI500_INDEX_CODE,
    )
    report = constituent_listing_report(publication, universe=_registry_universe())

    assert report.not_listed == ("600270.SH",)
    assert report.unknown_to_registry == ()
    assert report.is_clean is False


def test_a_published_constituent_can_be_absent_from_the_registry_entirely() -> None:
    """`990018.SH` sits in eighteen `000300.SH` publications from 2005-04-29 to 2006-09-29 at
    weights between 0.681 and 1.391, and is in neither the `L` nor the `D` half of
    `stock_basic`. It is reported separately from `not_listed` because the two facts are
    different: one is a security the registry says was gone, the other is a code the registry
    has never heard of."""
    publication = _publication(date(2005, 4, 29), (("990018.SH", 1.364), ("600000.SH", 98.636)))
    report = constituent_listing_report(publication, universe=_registry_universe())

    assert report.unknown_to_registry == ("990018.SH",)
    assert report.not_listed == ()


def test_a_publication_whose_constituents_were_all_listed_reports_clean() -> None:
    publication = _publication(date(2017, 12, 29), (("600000.SH", 100.0),))
    report = constituent_listing_report(publication, universe=_registry_universe())

    assert report.is_clean is True
    assert report.not_listed == ()
    assert report.unknown_to_registry == ()
    assert report.beyond_snapshot == ()


def test_a_publication_past_the_registry_snapshot_is_reported_rather_than_answered() -> None:
    """`StockUniverse` refuses to answer membership past its own snapshot, and this join must
    not turn that refusal into a quiet "not listed"."""
    publication = _publication(date(2026, 12, 31), (("600000.SH", 100.0),))
    report = constituent_listing_report(publication, universe=_registry_universe())

    assert report.beyond_snapshot == ("600000.SH",)
    assert report.not_listed == ()
    assert report.is_clean is False


def test_a_forward_filled_composition_can_name_a_terminated_security() -> None:
    """Composition survives a forward fill better than weights do, and not perfectly.

    Across the corpus, **38** constituent terminations fall strictly inside a forward-fill
    window -- the security is in publication P, is delisted before the next publication, and a
    question asked in between therefore names it. `600001.SH` is the clearest: it is in
    `000300.SH`'s 2009-11-30 publication, its `delist_date` is 2009-12-29, and the next
    publication is 2009-12-31, so 2009-12-29 and 2009-12-30 both answer with a security that
    had already gone. `600837.SH` (2025-03-04, inside 2025-02-28..2025-03-31) and `000982.SZ`
    (2024-08-12, inside 2024-07-31..2024-08-30) are the same shape.

    So `constituents_on` reports the publication date and the age for the composition too, and
    this module does not claim that a forward-filled membership is a fact about the day.
    """
    universe = build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=(
            SecurityLifecycle(
                ts_code="600001.SH",
                exchange="SSE",
                listed_on=date(1998, 1, 22),
                delisted_on=date(2009, 12, 29),
            ),
            SecurityLifecycle(ts_code="600000.SH", exchange="SSE", listed_on=date(1999, 11, 10)),
        ),
    )
    november = _publication(date(2009, 11, 30), (("600001.SH", 0.2), ("600000.SH", 99.8)))
    december = _publication(date(2009, 12, 31), (("600000.SH", 100.0),))
    membership = build_index_membership(CSI300_INDEX_CODE, (november, december))

    composition = membership.constituents_on(date(2009, 12, 30))
    assert "600001.SH" in composition.members
    assert composition.as_published_on == date(2009, 11, 30)
    assert composition.days_since_publication == 30
    assert universe.is_listed("600001.SH", date(2009, 12, 30)) is False


# --------------------------------------------------------------------------------------
# Stored rows
# --------------------------------------------------------------------------------------


def test_stored_rows_rebuild_one_membership_per_index() -> None:
    rows = (
        (CSI300_INDEX_CODE, "2024-11-29", "600000.SH", 40.0),
        (CSI300_INDEX_CODE, "2024-11-29", "600519.SH", 60.0),
        (CSI500_INDEX_CODE, "2024-11-29", "300750.SZ", 100.0),
    )
    memberships = index_memberships_from_panel_rows(rows)

    assert sorted(memberships) == [CSI300_INDEX_CODE, CSI500_INDEX_CODE]
    assert memberships[CSI300_INDEX_CODE].covered_from == date(2024, 11, 29)
    assert memberships[CSI500_INDEX_CODE].weights_on(date(2024, 11, 29)).weight_of(
        "300750.SZ"
    ) == pytest.approx(100.0)


def test_stored_rows_go_through_the_sum_rule() -> None:
    """A partition read is the last place a truncated publication could still be caught."""
    rows = (
        (CSI300_INDEX_CODE, "2024-11-29", "600000.SH", 40.0),
        (CSI300_INDEX_CODE, "2024-11-29", "600519.SH", 20.0),
    )
    with pytest.raises(IndexMembershipError, match="sum to 100"):
        index_memberships_from_panel_rows(rows)


def test_a_stored_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(IndexMembershipError, match="expected 4"):
        index_memberships_from_panel_rows(((CSI300_INDEX_CODE, "2024-11-29", "600000.SH"),))


def test_a_stored_weight_that_is_not_a_float_is_refused() -> None:
    """The message has to name the row, not just the value: a partition read is thousands of
    rows and `build_index_publication`'s own type refusal names only the constituent."""
    with pytest.raises(IndexMembershipError, match=r"row 0: weight must be a float"):
        index_memberships_from_panel_rows(
            ((CSI300_INDEX_CODE, "2024-11-29", "600000.SH", "100.0"),)
        )


def test_a_stored_publication_date_that_is_not_iso_text_is_refused() -> None:
    """`2024/11/29` rather than `20241129`: the latter *is* accepted by `date.fromisoformat`
    since 3.11, so using it here would have been a test that passes for the wrong reason."""
    with pytest.raises(IndexMembershipError, match="not an ISO date"):
        index_memberships_from_panel_rows(((CSI300_INDEX_CODE, "2024/11/29", "600000.SH", 100.0),))


# --------------------------------------------------------------------------------------
# Declared shape and named boundaries
# --------------------------------------------------------------------------------------


def test_the_panel_column_contract_puts_the_index_in_the_subject_column() -> None:
    """The index is the subject, which is what keeps three indices in one year's partition.

    `PanelStore`'s key is `(dataset, year)` with no index dimension, so a partition written for
    one index would replace one written for another. `panel_ingest.write_index_weights` refuses
    that -- see `tests/integration/panel/test_index_weight_ingest.py` -- and it can only do so
    because the index is what the subject column holds.
    """
    assert INDEX_WEIGHT_PANEL_COLUMNS == ("subject", "publication_date", "con_code", "weight")
    assert INDEX_WEIGHT_DATASET == "index_weight"
    assert INDEX_WEIGHT_INDEX_CODES == (
        CSI300_INDEX_CODE,
        CSI500_INDEX_CODE,
        CSI1000_INDEX_CODE,
    )


def test_every_named_limitation_carries_a_measurement() -> None:
    """A boundary with no number behind it is a disclaimer, not a disclosure."""
    codes = {entry.code for entry in KNOWN_INDEX_MEMBERSHIP_LIMITATIONS}
    assert "weights_drift_between_publications" in codes
    assert "composition_is_also_forward_filled" in codes
    assert "a_publication_can_carry_fewer_names_than_the_index_is_called" in codes
    assert "published_weights_do_not_sum_to_exactly_one_hundred" in codes
    assert "a_constituent_can_be_missing_from_the_security_registry" in codes
    assert "publication_lag_is_not_in_the_response" in codes
    for entry in KNOWN_INDEX_MEMBERSHIP_LIMITATIONS:
        assert any(character.isdigit() for character in entry.detail), entry.code


# --------------------------------------------------------------------------------------
# The remaining guards
# --------------------------------------------------------------------------------------


def test_two_publications_dated_the_same_day_are_refused() -> None:
    """Distinct from the doubled-month rule: this is the same snapshot arriving twice, which is
    what merging two overlapping fetches of one month looks like."""
    twin = _publication(date(2024, 11, 29), (("600036.SH", 100.0),))
    with pytest.raises(IndexMembershipError, match="two publications dated 2024-11-29"):
        build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, twin))


def test_a_composition_answers_membership_and_a_weights_answer_reports_its_own_total() -> None:
    membership = build_index_membership(CSI300_INDEX_CODE, (NOVEMBER, DECEMBER))

    composition = membership.constituents_on(date(2024, 12, 20))
    assert composition.includes("600519.SH") is True
    assert composition.includes("300750.SZ") is False
    assert membership.weights_on(date(2024, 12, 20)).published_total == pytest.approx(100.0)


@pytest.mark.parametrize(("count", "decimals"), [(0, 3), (-1, 3), (300, -1)])
def test_a_tolerance_for_an_impossible_publication_shape_is_refused(
    count: int, decimals: int
) -> None:
    """A zero-cell tolerance is 0, which would refuse every publication; a negative decimal
    count is a bound that grows without limit. Neither is a shape this endpoint can produce, so
    both are bugs in the caller rather than data."""
    with pytest.raises(IndexMembershipError, match="tolerance needs"):
        published_weight_tolerance(count=count, decimals=decimals)


def test_a_blank_index_code_is_refused() -> None:
    with pytest.raises(IndexMembershipError, match="index_code must be a non-empty string"):
        build_index_publication(
            index_code=" ", published_on=date(2024, 6, 28), weights=_weights((("a", 100.0),))
        )


def test_a_datetime_where_a_date_belongs_is_refused() -> None:
    """`datetime` subclasses `date` and compares against one without ever equalling it, so a
    publication dated with a `datetime` would sit one notch off every `bisect` boundary."""
    with pytest.raises(IndexMembershipError, match=r"must be a plain datetime\.date"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=datetime(2024, 6, 28, tzinfo=UTC),
            weights=_weights((("600519.SH", 100.0),)),
        )


def test_a_stored_constituent_that_is_not_text_is_refused() -> None:
    with pytest.raises(IndexMembershipError, match=r"row 0: con_code must be a non-empty string"):
        index_memberships_from_panel_rows(((CSI300_INDEX_CODE, "2024-11-29", 600000, 100.0),))


def test_a_stored_publication_date_that_is_not_text_at_all_is_refused() -> None:
    with pytest.raises(IndexMembershipError, match=r"row 0: publication_date must be an ISO date"):
        index_memberships_from_panel_rows(((CSI300_INDEX_CODE, 20241129, "600000.SH", 100.0),))


# --------------------------------------------------------------------------------------
# The exact type checks, and the subclasses that are the only thing they refuse
# --------------------------------------------------------------------------------------


class _ScaledFloat(float):
    """A `float` subclass whose arithmetic is not `float`'s own; `numpy.float64` is one."""

    def __mul__(self, other: object) -> float:  # pragma: no cover - never reached
        return 0.0


class _Code(str):
    """A `str` subclass whose comparison is not `str`'s own; `numpy.str_` is one."""

    def __eq__(self, other: object) -> bool:  # pragma: no cover - never reached
        return True

    def __hash__(self) -> int:  # pragma: no cover - never reached
        return 0


def test_a_float_subclass_weight_is_refused_because_that_is_what_the_exact_check_is_for() -> None:
    """`type(...) is float`, not `isinstance`, and this is the only case that tells them apart.

    `isinstance(True, float)` is already `False`, so `bool` does not distinguish the two
    spellings. A **subclass** does, and it is where the danger is: this weight is about to be
    multiplied into a price or a return, so the arithmetic that runs has to be `float`'s.
    `domain/adjustment.py::_require_price` refuses a `_ScaledFloat` for the same reason and
    `tests/unit/domain/test_adjustment.py` pins it; this is that test for this column.
    """
    with pytest.raises(IndexMembershipError, match=r"must be a float in \(0, 100"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=date(2024, 6, 28),
            weights=_weights((("600519.SH", _ScaledFloat(100.0)),)),
        )


def test_a_float_subclass_in_a_stored_row_is_refused_by_the_row_scoped_rule() -> None:
    """Separate from the rule above because the message is: a partition read is thousands of
    rows and `_stored_weight` is the only one that can name which."""
    with pytest.raises(IndexMembershipError, match=r"row 0: weight must be a float"):
        index_memberships_from_panel_rows(
            ((CSI300_INDEX_CODE, "2024-11-29", "600000.SH", _ScaledFloat(100.0)),)
        )


def test_a_string_subclass_code_is_refused_wherever_a_code_is_taken() -> None:
    """The same argument as the float one, for the columns that are keys rather than numbers.

    Every code here is used as a dict key, a `sorted()` key and a set member -- `build_index_
    publication` de-duplicates on it, `index_memberships_from_panel_rows` groups on it, and
    `rebalances()` takes set differences of it. A subclass that overrides `__eq__` or `__hash__`
    (`numpy.str_` is a `str` subclass) changes what "the same constituent" means in all three,
    so the exact type is checked rather than the interface.
    """
    with pytest.raises(IndexMembershipError, match="index_code must be a non-empty string"):
        build_index_publication(
            index_code=_Code(CSI300_INDEX_CODE),
            published_on=date(2024, 6, 28),
            weights=_weights((("600519.SH", 100.0),)),
        )
    with pytest.raises(IndexMembershipError, match="con_code must be a non-empty string"):
        build_index_publication(
            index_code=CSI300_INDEX_CODE,
            published_on=date(2024, 6, 28),
            weights=_weights(((_Code("600519.SH"), 100.0),)),
        )
    with pytest.raises(IndexMembershipError, match="index_code must be a non-empty string"):
        build_index_membership(_Code(CSI300_INDEX_CODE), (NOVEMBER,))


def test_a_string_subclass_in_a_stored_row_is_refused_by_the_row_scoped_rule() -> None:
    """`_require_stored_text` is the boundary a partition read crosses, and it names the row."""
    with pytest.raises(IndexMembershipError, match=r"row 0: subject must be a non-empty string"):
        index_memberships_from_panel_rows(
            ((_Code(CSI300_INDEX_CODE), "2024-11-29", "600000.SH", 100.0),)
        )
    with pytest.raises(IndexMembershipError, match=r"row 0: con_code must be a non-empty string"):
        index_memberships_from_panel_rows(
            ((CSI300_INDEX_CODE, "2024-11-29", _Code("600000.SH"), 100.0),)
        )
