"""The walk-forward split, its purge and its embargo (`V2-P4-013`).

The row is one line -- *"Walk-forward 切分 + purge/embargo | 禁止随机切分"* -- and Implementation
Decision 12 is what it is short for: *"时间相关 Alpha 主张不接受随机训练/测试切分。重叠标签需要
purging 与 embargo。"* Stories S27 and S28 name the same two things. A random split on
overlapping-label panel data does not merely bias a backtest; it puts the answer in the training
set and returns a number nobody can disbelieve, which is why the prohibition is the row's whole
emphasis.

`V2-P4-012` named what it left here in its own docstring: *"`V2-P4-013` owns the walk-forward
split, purge and embargo, and owns turning these sections into a `TrainingSet`."* This module is
those four things and nothing else -- it fits no model and reports no metric.

## Where the boundary falls, derived from `OutcomeLabel`'s fields

An `OutcomeLabel` carries a `LabelWindow`, and three of that window's fields decide everything
below. `sessions` is the exact tuple of sessions the target reads a close on -- `window_return`
chains `session_returns` over adjacent pairs of it, using `sessions[0]`'s close as the base.
`exit_day` is the last of them. `close_instant(exit_day)` dates that last close as an aware
instant in the window's own zone. So a label's information is fully described by *which closes it
read* and *when the last of them printed*, and both are on the label.

**The purge is one comparison.** A candidate training example is removed when

    example.label.window.close_instant(example.label.window.exit_day) > first_test_as_of

where `first_test_as_of` is the instant the fold's earliest test cross section is dated at. In
words: **the training label had not closed yet at the moment the fold was first asked.** A model
holding it could not have existed then. There is no width to choose -- how far the cut reaches is
a function of the horizon, which the data carries.

**The obvious rule is weaker, and is a property here rather than a second implementation.** S28
says "purging for overlapping labels", which suggests dropping the candidates whose `sessions`
intersect a test label's. That rule is implied by the one above and never the reverse: a shared
session means the training exit is at or after the earliest test entry; that entry is the session
*after* the first test prediction day; and `first_test_as_of` is dated on that prediction day in
the window's own zone -- so a shared session always puts the close instant after it. Writing both
would be one check plus a place for the weaker one to fall behind, which is the ground
`V2-P4-011` deleted its own duplicated check on. `shared_sessions` exists, `leaked_sessions` uses
it, and "no surviving training label reads a session a test label reads" is asserted as an
outcome. What the stronger rule costs is one session, and it is measured rather than hidden:
`test_the_purge_removes_a_session_the_shared_session_rule_alone_would_have_kept`.

**Equality is admitted.** `PredictionBatch` says why -- *"training through last night's close and
predicting as of it is what a daily model does"* -- so the comparison is `>` and not `>=`. A
corpus dated in the morning cannot tell those two apart, because no 15:00 close ever equals an
09:00 instant; `test_a_label_that_closed_exactly_when_the_fold_was_asked_survives_the_purge`
dates its cross sections at the close so that it can.

**And the purge is cross-sectional, which is where `V2-P4-011`'s pointer had to be corrected.**
That issue left `TrainingSet.overlaps` as "the measurement `V2-P4-013` needs". It is not the
purge, and `overlapping_windows`' own docstring says why without meaning to: it groups by
security, because two securities' windows over one set of sessions are "not an overlap in any
sense a purge cares about". True of two samples' independence, false of a fold boundary. A
training label measured over sessions inside the test period is a realized *market* return inside
the test period whichever security it is about. The purge here never groups by security at all --
it compares one instant per example -- and
`test_training_set_overlaps_cannot_draw_the_boundary_this_purge_draws` measures the gap from both
sides.

## What the embargo removes, and why it is not the purge with a bigger number

The embargo removes the candidates the purge **declined** whose labels closed inside the last
`embargo_sessions` sessions ending at the fold's first prediction day. The two sets are therefore
disjoint at every width, which is the property that makes "one applied and not the other" a
question with an answer:

- No embargo width reaches what the purge removes, because the embargo only ever looks at what
  the purge left (`test_no_embargo_width_removes_what_the_purge_removes`, run over every width
  from zero to the whole axis).
- No purge reaches what the embargo removes, because those labels closed before the fold was
  asked and share no session with any test label
  (`test_nothing_the_embargo_removes_shares_a_session_with_any_test_label`).

They cut on the same axis and they are determined by different things, which is the whole of the
distinction. The purge's reach is the **horizon**, and the horizon is on the label. The embargo's
reach is the **feature footprint** -- a column's trailing lookback, a fundamental's publication
lag, a revision that re-dates a value the training example read -- and none of that is on a label
or reachable from this module. So the purge is measured and the embargo is declared, and
`embargo_sessions` has no default: `build_label_window`'s `zone` is the precedent, and a waiver
that is a default is an accident. Zero is a legal statement -- "no separation is required" -- and
removes nothing.

## The relationship to the floor `V2-P4-011` installed

`PredictionBatch` refuses `as_of < artifact.training_cutoff` and its own docstring calls that a
floor and not a purge, leaving the purge here. The purge turns out to be *that comparison*, moved
twice: anchored once per **fold** at the earliest instant it predicts at rather than once per
batch at its own, and **removing** the examples rather than refusing the answer. Both moves earn
their keep. The earliest anchor is what makes every batch in a fold pass instead of only the late
ones; removing rather than refusing is what leaves a fold that runs.

It also has a consequence worth stating plainly, because it sounds like a hole in the evidence.
Skipping the purge does not produce an inflated skill number -- it produces a fold whose **first
prediction the contract refuses**, and that follows from the rule rather than from a fixture:
every purged example is by definition one whose close instant is after `first_test_as_of`, so
including any of them puts the artifact's `training_cutoff` past the instant the first batch
stands at. The purge's demonstration is therefore a learned direction plus a refusal. The
embargo's, where both sides run, is a pair of skill numbers: `1.0` against `0.0`. Both are in
`tests/unit/backtest/test_walk_forward_leak.py`, on a corpus whose leak was planted.

## Why a random split has nowhere to be written down

`WalkForwardFold` carries a panel, a calendar, a first test day, a block length and an embargo
width. It carries **no field naming which rows train**. Membership is derived: every prediction
day strictly before the block, less what the two rules remove. So an unordered split, a fold
whose training rows come from after its test block, and a split sending one security's later rows
to train while another's earlier rows go to test are all *unrepresentable* rather than
discouraged -- a boundary is a date, and a date takes the whole cross section with it. The block
being contiguous is unrepresentable too, since `(first_test_day, test_day_count)` cannot express
a scattered set of days.

What is only **refused** is where a legal block sits: a first test day the panel never asked on,
a block running past the panel's end, and a block starting on the panel's first day are three
constructor refusals, and a caller may still place a legal block anywhere. `walk_forward_folds`
is what tiles blocks from the tail in order, and a hand-built fold is not held to that schedule.
`an_unordered_split_is_unrepresentable_and_a_badly_placed_block_is_only_refused` is that
distinction where a reader meets it.

## Where this module lives, which was decided before it was written

`pyproject.toml`'s own comment on the `backtest/` contracts says it: *"`V2-P4-013`'s walk-forward
split lives under `backtest/` and consumes what that module produces -- a `FeatureCrossSection`,
which is in `domain/`."* `backtest-no-numeric-stack-or-panel-plane` forbids
`openalpha_cn.feature_matrix` to this package, so the join takes `domain/` contracts and never
the producer, exactly as `shortlist_view.py` hands `ComponentCrossSection` across the same seam.
This module joined both per-module study contracts on arrival, which
`tests/unit/test_import_layering.py` makes mandatory, and everything here is stdlib arithmetic
over `domain/` types.

## The join, and what it can and cannot check

`labelled_panel` pairs each dated cross section with the labels built at that same instant, and
the join key is not the security -- it is the instant. A label is refused unless its
`prediction_day` is the day `cross_section.as_of` resolves to *in the window's own zone*, which is
`build_label_window`'s own first step reused rather than re-derived. That check earns its place on
a shape `V2-P4-012` actually produces: a `FeatureMatrixSection.as_of` is the **resolved** instant,
the newest stored build every declared column shares, so a matrix that fell back to an earlier
build than the caller asked for would otherwise be joined to labels dated to the day the caller
asked about.

What it cannot check is that the feature values were readable at that instant. `V2-P4-012` owns
that -- every column comes back through the three `read_visible_at` loaders -- and P2's red-team
gate is where a look-ahead in a factor build is caught. A panel built from contaminated features
splits cleanly.

Rows that cannot become examples leave through `LabelledPanel.excluded` carrying
`OutcomeLabel.refusal_summary`, rather than vanishing: `TrainingExample` refuses an unlabelled
window outright, so the choice was between refusing the whole section and disclosing the row, and
disclosing is the one that keeps a halted name visible. A prediction day on which *every* row
refuses is the other case and is refused, because a block length is counted in prediction days
and a silently missing day moves every boundary after it.

## What is deliberately left to a named issue

- **`V2-P4-014`** and **`V2-P4-015`** own the baselines and the evaluation. Nothing here scores a
  fold; `test_set` exists so that whichever of them arrives first does not have to re-derive it.
- **`V2-P4-016`** owns the artifact's content address, and with it where Implementation Decision
  11's *split policy* field goes. A fold's policy is three scalars -- the block's first day, its
  length, and the embargo width -- against a panel and a calendar, so addressing one is an
  addition rather than a redesign.
- **`V2-P4-017`** owns persistence. Nothing here is stored: a split is recomputed from a panel, a
  calendar and five numbers.
- **`V2-P4-022`** owns the corpus with a known signal-to-noise ratio and a known-null control.
  The fixture behind this module's tests has no noise model and exists to make one bit of a
  reference model flip; it is a leak fixture and not a benchmark.
- **A rolling training window** is absent rather than configurable, and
  `only_an_expanding_training_window_is_offered` says so. It is another way of choosing
  candidates and would leave both rules unchanged.
- **Implementation Decision 12's third clause** -- the final holdout left untouched during
  selection -- is not this split's, and
  `the_final_holdout_decision_12_asks_to_leave_untouched_is_not_this_split` says where it does
  belong.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from openalpha_cn.domain.alpha_model import (
    FeatureCrossSection,
    TrainingExample,
    TrainingSet,
)
from openalpha_cn.domain.labels import OutcomeLabel
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError

NO_LABEL: Final[str] = "no label was offered for this security at this instant"
NO_FEATURE_ROW: Final[str] = "no feature row was offered for this security at this instant"


class WalkForwardError(ValueError):
    """Raised for a malformed labelled panel, fold or schedule.

    A `ValueError` subclass to match `domain/alpha_model.py`'s `AlphaModelError` and
    `domain/labels.py`'s `LabelError`, so a caller catching `ValueError` around a contract
    boundary keeps catching this one. A fold that the two rules empty is one of these and not
    an empty answer: a split that returns nothing is the shape this plane exists to make
    unavailable.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class WalkForwardLimitation:
    """One named boundary on what this split can be trusted to say."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelledCrossSection:
    """One instant's feature rows and the outcome labels built at that same instant.

    What `V2-P4-012` produces on one side and `domain/labels.py::label_outcome` on the other,
    carried together because a walk-forward needs both and neither knows about the other. The
    labels are a whole tuple rather than a mapping: `labelled_panel` is what refuses a repeated
    security, and a mapping would have made that shape unrepresentable in the *wrong* way --
    silently, by keeping whichever arrived last.
    """

    cross_section: FeatureCrossSection
    labels: tuple[OutcomeLabel, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelExclusion:
    """One security this panel could not turn into a training example, and the reason.

    `ScoreCoverage.incomplete_components`' disclosure applied to a join: a row that leaves has
    to leave visibly. `reason` carries `OutcomeLabel.refusal_summary` verbatim for a refused
    window, so an operator reads the same sentence the labeller wrote.
    """

    ts_code: str
    prediction_day: date
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelSection:
    """One prediction day: the instant, the whole cross section, and the rows that labelled.

    The cross section is kept **whole** and the examples are the subset that labelled, because
    the two are asked different questions. A fit consumes the labelled rows; `predict` is asked
    about every security the market held, and a name with nothing to say for it abstains rather
    than vanishing -- which is `V2-P4-011`'s *scored or abstained, never absent* and
    `V2-P4-012`'s row-of-`None`, one layer up.
    """

    as_of: datetime
    prediction_day: date
    cross_section: FeatureCrossSection
    examples: tuple[TrainingExample, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelledPanel:
    """Every instant's labelled rows under one recipe, in time order -- what a fold is cut from.

    One feature list, one exchange, one zone and one horizon, all enforced by `labelled_panel`.
    Each is load-bearing for a different rule: the feature list because values travel
    positionally, the exchange because the embargo counts its sessions, the zone because the
    purge compares `close_instant`s written on it, and the horizon because a purge's reach is a
    function of it and one number cannot be right for two.
    """

    feature_ids: tuple[str, ...]
    exchange: str
    sections: tuple[PanelSection, ...]
    excluded: tuple[PanelExclusion, ...]

    @property
    def prediction_days(self) -> tuple[date, ...]:
        """The decision days this panel asks about, ascending."""
        return tuple(section.prediction_day for section in self.sections)

    @property
    def examples(self) -> tuple[TrainingExample, ...]:
        """Every labelled row, in time order."""
        return tuple(example for section in self.sections for example in section.examples)

    def section_on(self, day: date) -> PanelSection:
        """The section this panel holds for `day`, or `WalkForwardError`."""
        for section in self.sections:
            if section.prediction_day == day:
                return section
        raise WalkForwardError(
            f"{day.isoformat()} is not one of this panel's prediction days "
            f"({self.prediction_days[0].isoformat()}.."
            f"{self.prediction_days[-1].isoformat()})"
        )


def label_sessions(examples: Iterable[TrainingExample]) -> frozenset[date]:
    """Every session any of these labels reads a close on.

    `LabelWindow.sessions` and not a range between the endpoints: a window's sessions are what
    the calendar actually held, so a rebuilt range would re-derive a calendar this module does
    not own.
    """
    return frozenset(day for example in examples for day in example.label.window.sessions)


def shared_sessions(
    left: Iterable[TrainingExample], right: Iterable[TrainingExample]
) -> tuple[date, ...]:
    """Every session both sides read a close on, ascending.

    Public, and not used by the purge. It is how the *property* the purge produces is measured
    -- `leaked_sessions` is this over a fold's survivors and its test rows, and has to be empty
    -- and it is what lets a test state the weaker shared-session rule and show that the purge
    is strictly stronger. Two securities are not separated: a fold boundary does not care which
    name a session's close belongs to, which is exactly where `overlapping_windows` differs.
    """
    return tuple(sorted(label_sessions(left) & label_sessions(right)))


def _prediction_day_of(label: OutcomeLabel, *, as_of: datetime) -> date:
    return ensure_aware(as_of).astimezone(label.window.zone).date()


def labelled_panel(sections: Iterable[LabelledCrossSection]) -> LabelledPanel:
    """Join dated cross sections to the labels built at those same instants.

    The join key is the **instant**, not the security: a label is refused unless its
    `prediction_day` is the day this section's `as_of` resolves to in the window's own zone,
    which is `build_label_window`'s first step reused rather than re-derived. Inside a section
    the securities are matched by code, and both directions of a miss are disclosed rather than
    dropped.
    """
    ordered = tuple(sections)
    if not ordered:
        raise WalkForwardError(
            "a labelled panel carries no cross section; a walk-forward over no instant is an "
            "empty success, and there is no time axis to split"
        )
    feature_ids = ordered[0].cross_section.feature_ids
    built: list[PanelSection] = []
    excluded: list[PanelExclusion] = []
    exchanges: set[str] = set()
    zones: set[str] = set()
    horizons: set[str] = set()
    for entry in ordered:
        cross_section = entry.cross_section
        if cross_section.feature_ids != feature_ids:
            raise WalkForwardError(
                f"this panel carries two feature lists, {list(feature_ids)} and "
                f"{list(cross_section.feature_ids)}; feature values travel positionally, so a "
                "panel that changes its columns part-way through is two matrices fitted as one"
            )
        if not entry.labels:
            raise WalkForwardError(
                f"the cross section dated {cross_section.as_of.isoformat()} offers no label at "
                "all; a prediction day is joined to its outcomes through the zone its own "
                "windows were dated in, and a section carrying none says nothing about which "
                "day it is even about"
            )
        by_code: dict[str, OutcomeLabel] = {}
        for label in entry.labels:
            built_for = label.window.prediction_day
            asked_on = _prediction_day_of(label, as_of=cross_section.as_of)
            if built_for != asked_on:
                raise WalkForwardError(
                    f"{label.ts_code}'s label was built for "
                    f"{built_for.isoformat()} and is offered against a cross section dated "
                    f"{cross_section.as_of.isoformat()}, which is {asked_on.isoformat()} in the "
                    "window's own zone; a feature row joined to an outcome it did not precede "
                    "is a training example about a decision nobody took"
                )
            if label.ts_code in by_code:
                raise WalkForwardError(
                    f"{label.ts_code} carries two labels on {built_for.isoformat()}; one "
                    "decision day is one question, and which of the two a fit consumed would "
                    "not be recoverable"
                )
            by_code[label.ts_code] = label
            exchanges.add(label.window.exchange)
            zones.add(str(label.window.zone))
            horizons.add(label.window.horizon.text)
        prediction_day = _prediction_day_of(entry.labels[0], as_of=cross_section.as_of)
        examples: list[TrainingExample] = []
        for row in cross_section.rows:
            matched = by_code.pop(row.ts_code, None)
            if matched is None:
                excluded.append(
                    PanelExclusion(
                        ts_code=row.ts_code, prediction_day=prediction_day, reason=NO_LABEL
                    )
                )
                continue
            if not matched.is_labelled:
                excluded.append(
                    PanelExclusion(
                        ts_code=row.ts_code,
                        prediction_day=prediction_day,
                        reason=matched.refusal_summary,
                    )
                )
                continue
            examples.append(TrainingExample(label=matched, features=row.values))
        for ts_code in sorted(by_code):
            excluded.append(
                PanelExclusion(
                    ts_code=ts_code, prediction_day=prediction_day, reason=NO_FEATURE_ROW
                )
            )
        if not examples:
            raise WalkForwardError(
                f"the cross section dated {cross_section.as_of.isoformat()} produced no "
                f"labelled row out of {len(cross_section.rows)} offered; a prediction day with "
                "nothing to learn from is a hole in the time axis, and a walk-forward that "
                "silently skipped it would report a fold whose training span is not the one it "
                "names"
            )
        built.append(
            PanelSection(
                as_of=cross_section.as_of,
                prediction_day=prediction_day,
                cross_section=cross_section,
                examples=tuple(examples),
            )
        )
    if len(horizons) > 1:
        raise WalkForwardError(
            f"this panel mixes horizons {sorted(horizons)}; a five-session target and a "
            "ten-session target reach different distances past a fold boundary, so one purge "
            "cannot be right for both"
        )
    if len(exchanges) > 1:
        raise WalkForwardError(
            f"this panel mixes exchanges {sorted(exchanges)}; the embargo counts sessions, and "
            "two exchanges' sessions are two different axes"
        )
    if len(zones) > 1:
        raise WalkForwardError(
            f"this panel mixes zones {sorted(zones)}; the purge compares "
            "LabelWindow.close_instant against the instant the fold is first asked at, and two "
            "zones put those two 15:00 closes on two different clocks"
        )
    days = [section.prediction_day for section in built]
    if days != sorted(set(days)):
        raise WalkForwardError(
            f"this panel's prediction days {[item.isoformat() for item in days]} are not "
            "strictly increasing; a fold's block is counted in prediction days, so a repeated "
            "or reordered day is one day's market counted twice or a boundary that means "
            "nothing. Two cross sections of one day is a shape V2-P4-012 hands here on purpose "
            "(test_feature_matrix_reads.py's own note says so), and this is the answer to it"
        )
    return LabelledPanel(
        feature_ids=feature_ids,
        exchange=exchanges.pop(),
        sections=tuple(built),
        excluded=tuple(excluded),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class WalkForwardFold:
    """One fold: a panel, a calendar, where the block starts, how long it is, and a width.

    Five fields, and the absent sixth is the point. There is no field naming which rows train,
    so train membership is *derived* -- every prediction day strictly before the block, less
    what the purge and the embargo remove -- and an unordered split has nowhere to be written
    down. The block is `(first_test_day, test_day_count)` rather than a list of days for the
    same reason: a scattered test set is unrepresentable rather than refused.

    The calendar is a field and not a global because the embargo counts sessions on it, and
    `__post_init__` refuses one whose exchange is not the panel's -- two exchanges' sessions are
    two different axes.
    """

    panel: LabelledPanel
    calendar: TradingCalendar
    first_test_day: date
    test_day_count: int
    embargo_sessions: int

    def __post_init__(self) -> None:
        if self.calendar.exchange != self.panel.exchange:
            raise WalkForwardError(
                f"this fold is measured on the {self.calendar.exchange} calendar and its panel "
                f"was labelled on {self.panel.exchange}; the embargo counts sessions, so a "
                "calendar from another exchange counts the wrong ones"
            )
        if self.test_day_count < 1:
            raise WalkForwardError(
                f"a fold tests on {self.test_day_count} prediction day(s); a fold with no test "
                "day produces no out-of-sample observation at all"
            )
        if self.embargo_sessions < 0:
            raise WalkForwardError(
                f"a fold declares an embargo of {self.embargo_sessions} sessions; a negative "
                "embargo would widen the training set past the purge that already cut it"
            )
        days = self.panel.prediction_days
        if self.first_test_day not in days:
            raise WalkForwardError(
                f"{self.first_test_day.isoformat()} is not one of this panel's prediction days "
                f"({days[0].isoformat()}..{days[-1].isoformat()}); a fold's boundary has to be "
                "a day the panel actually asked a question on"
            )
        start = days.index(self.first_test_day)
        if start == 0:
            raise WalkForwardError(
                f"a fold tests from {self.first_test_day.isoformat()}, this panel's first "
                "prediction day, so there is no prediction day before it to train on"
            )
        if start + self.test_day_count > len(days):
            raise WalkForwardError(
                f"a fold tests on {self.test_day_count} day(s) from "
                f"{self.first_test_day.isoformat()}, which runs past this panel's last "
                f"prediction day ({days[-1].isoformat()})"
            )

    @property
    def test_days(self) -> tuple[date, ...]:
        """The contiguous block of prediction days this fold is evaluated on."""
        days = self.panel.prediction_days
        start = days.index(self.first_test_day)
        return days[start : start + self.test_day_count]

    @property
    def test_sections(self) -> tuple[PanelSection, ...]:
        """The whole cross sections this fold predicts on, in time order."""
        return tuple(self.panel.section_on(day) for day in self.test_days)

    @property
    def test_examples(self) -> tuple[TrainingExample, ...]:
        """Every labelled row inside the test block."""
        return tuple(example for section in self.test_sections for example in section.examples)

    @property
    def candidates(self) -> tuple[TrainingExample, ...]:
        """Every labelled row on a strictly earlier prediction day, before either rule cuts."""
        days = self.panel.prediction_days
        start = days.index(self.first_test_day)
        return tuple(
            example for section in self.panel.sections[:start] for example in section.examples
        )

    @property
    def first_test_as_of(self) -> datetime:
        """The instant this fold's first prediction is made at."""
        return self.panel.section_on(self.first_test_day).as_of

    @property
    def purged(self) -> tuple[TrainingExample, ...]:
        """Every candidate whose label had not closed at the instant the fold was first asked.

        The rule, and the whole of it. `>` and not `>=`, because `PredictionBatch` admits
        equality and a fold has no reason to be stricter than the contract it feeds. Nothing
        here intersects a session set: "no surviving label reads a session a test label reads"
        follows from this comparison and is asserted as a consequence, not implemented twice.
        """
        deadline = self.first_test_as_of
        return tuple(
            example
            for example in self.candidates
            if example.label.window.close_instant(example.label.window.exit_day) > deadline
        )

    @property
    def embargoed(self) -> tuple[TrainingExample, ...]:
        """Every candidate the purge left whose label closed inside the embargo's sessions.

        Only what the purge declined, which is what keeps the two sets disjoint at every width
        and is why no embargo can stand in for a purge. Zero removes nothing and is a statement
        rather than a default -- the floor is `calendar.shift(anchor, 0)`, and returning early
        is what keeps a fold predicted at the session close from losing a label that closed on
        that same close, which the contract admits.
        """
        if self.embargo_sessions == 0:
            return ()
        floor = self._embargo_floor()
        purged = set(self.purged)
        return tuple(
            example
            for example in self.candidates
            if example not in purged and example.label.window.exit_day >= floor
        )

    @property
    def train_examples(self) -> tuple[TrainingExample, ...]:
        """What the fit is given: the candidates neither rule removed."""
        removed = set(self.purged) | set(self.embargoed)
        return tuple(example for example in self.candidates if example not in removed)

    @property
    def training_set(self) -> TrainingSet:
        """The fold's training set, or `WalkForwardError` naming which rule emptied it.

        `TrainingSet`'s own refusal for an empty set says a fit over nothing has an undefined
        cutoff, which is true and says nothing about *why* this fold is empty. The message here
        carries the arithmetic instead -- how many the purge took, how many the embargo took at
        which width, out of how many candidates -- because on a short panel the answer is
        usually "widen the panel or narrow the embargo" and neither is guessable from the other
        sentence.
        """
        examples = self.train_examples
        if not examples:
            raise WalkForwardError(
                f"the fold testing from {self.first_test_day.isoformat()} has no training "
                f"example left: the purge removed {len(self.purged)} and the "
                f"{self.embargo_sessions}-session embargo removed {len(self.embargoed)} of "
                f"{len(self.candidates)} candidate(s). A fold with no fit is refused here "
                "rather than answered with a model nobody trained"
            )
        return self._training_set_of(examples)

    @property
    def test_set(self) -> TrainingSet:
        """The fold's labelled test rows, for whichever evaluation consumes them.

        A `TrainingSet` of rows nobody trains on, and the name is `V2-P4-011`'s rather than a
        mistake: that contract built exactly one carrier for labelled rows aligned to a feature
        list, and declaring a second identical one here so that the word matched would be two
        types with one shape. What an evaluation needs from it -- `target` per row, and the
        alignment guarantees -- is the same either side of the boundary. Predicting is done off
        `test_sections`, which carry the whole cross section rather than only the labelled part.
        """
        return self._training_set_of(self.test_examples)

    @property
    def leaked_sessions(self) -> tuple[date, ...]:
        """Every session both the surviving training labels and the test labels read.

        Has to be empty, and is a measurement rather than a check: the purge is a comparison of
        instants and never touches a session set, so this is the independent reading of whether
        it did what S28 asks. `test_no_surviving_training_label_reads_a_session_a_test_label_reads`
        asserts the empty result beside a non-empty one over the unpurged candidates, so it
        cannot pass on a corpus with nothing to share.
        """
        return shared_sessions(self.train_examples, self.test_examples)

    def _training_set_of(self, examples: tuple[TrainingExample, ...]) -> TrainingSet:
        return TrainingSet(feature_ids=self.panel.feature_ids, examples=examples)

    def _embargo_floor(self) -> date:
        """The earliest session a surviving label may close on, counted back from the block.

        Anchored on the last session at or before the fold's first prediction day, reached as
        `previous_trading_day` of the earliest test entry -- which is always a session, while a
        prediction day need not be one. So `embargo_sessions` is the number of sessions of
        separation between a surviving label's close and the day the fold is first asked on.
        """
        entry = min(example.label.window.entry_day for example in self.test_examples)
        try:
            anchor = self.calendar.previous_trading_day(entry)
            return self.calendar.shift(anchor, -self.embargo_sessions)
        except TradingCalendarError as error:
            raise WalkForwardError(
                f"the fold testing from {self.first_test_day.isoformat()} declares a "
                f"{self.embargo_sessions}-session embargo, which reaches outside the "
                f"{self.calendar.exchange} calendar: {error}"
            ) from error


def walk_forward_folds(
    panel: LabelledPanel,
    *,
    calendar: TradingCalendar,
    folds: int,
    test_days_per_fold: int,
    embargo_sessions: int,
) -> tuple[WalkForwardFold, ...]:
    """Cut the tail of `panel` into `folds` contiguous test blocks, in time order.

    The blocks tile the **end** of the panel so that every fold has a training span behind it
    and the earliest fold has the shortest one, which is what an expanding walk-forward is. Each
    fold is validated before it is returned -- a schedule hands back folds that run or it hands
    back nothing, rather than deferring the emptiness to whichever caller first asked for a
    training set, which on a long schedule is after the first `k` folds have been fitted.

    Neither the negative-embargo check nor any of the block-placement checks is repeated here:
    `WalkForwardFold.__post_init__` runs on every fold this builds.
    """
    if folds < 1 or test_days_per_fold < 1:
        raise WalkForwardError(
            f"a walk-forward schedule of {folds} fold(s) of {test_days_per_fold} test day(s) "
            "needs at least one of each; a schedule of none is an empty success"
        )
    days = panel.prediction_days
    tested = folds * test_days_per_fold
    if tested >= len(days):
        raise WalkForwardError(
            f"{folds} fold(s) of {test_days_per_fold} test day(s) is {tested} of this panel's "
            f"{len(days)} prediction day(s) and leaves no prediction day to train the first "
            "fold on"
        )
    built = tuple(
        WalkForwardFold(
            panel=panel,
            calendar=calendar,
            first_test_day=days[len(days) - tested + index * test_days_per_fold],
            test_day_count=test_days_per_fold,
            embargo_sessions=embargo_sessions,
        )
        for index in range(folds)
    )
    for fold in built:
        _ = fold.training_set
    return built


KNOWN_WALK_FORWARD_LIMITATIONS: Final[tuple[WalkForwardLimitation, ...]] = (
    WalkForwardLimitation(
        code="the_purge_is_the_prediction_batch_floor_moved_from_a_refusal_to_a_removal",
        detail=(
            "PredictionBatch refuses as_of < artifact.training_cutoff, per batch, and "
            "V2-P4-011 called that a floor and not a purge. The purge here is the same "
            "comparison after two changes of scope: anchored once at the instant the fold is "
            "first asked at rather than at each batch's own, and removing the examples rather "
            "than refusing the answer. Both changes matter. The earliest anchor is what makes "
            "every batch in the fold pass rather than only the late ones; removing rather than "
            "refusing is what leaves a fold that runs. The consequence worth stating is the "
            "one that sounds like a gap: skipping the purge does not produce an inflated "
            "number, it produces a fold whose first prediction the contract refuses. That "
            "follows from the rule and not from a fixture -- a purged example is by definition "
            "one whose close instant is after first_test_as_of, so including any of them puts "
            "training_cutoff past the instant the first batch stands at. "
            "test_the_purge_is_what_stops_the_fit_learning_the_test_blocks_direction measures "
            "both halves -- the direction the unpurged fit absorbs, and the refusal it then "
            "runs into."
        ),
    ),
    WalkForwardLimitation(
        code="the_shared_session_rule_is_a_property_here_and_not_a_second_implementation",
        detail=(
            "Story S28 asks for purging for overlapping labels, and the obvious rule is 'drop "
            "the training examples whose windows share a session with a test window'. That "
            "rule is strictly weaker than the one implemented, and the elimination is general "
            "rather than a fixture's accident: a shared session means the training exit is at "
            "or after the earliest test entry, that entry is the session after the first test "
            "prediction day, and the first test as_of is dated on that prediction day in the "
            "window's own zone -- so a shared session always implies a close instant after the "
            "first as_of, and never the reverse. Implementing both would be one check plus a "
            "place for the weaker one to fall behind, which is what V2-P4-011 deleted its "
            "duplicated check on. So no session set is intersected inside the purge; "
            "shared_sessions exists, and 'no surviving training label reads a session a test "
            "label reads' is asserted as a consequence. What that costs is visible: the purge "
            "removes a session the weaker rule would have kept, and "
            "test_the_purge_removes_a_session_the_shared_session_rule_alone_would_have_kept "
            "names it."
        ),
    ),
    WalkForwardLimitation(
        code="the_embargo_width_is_declared_because_the_footprint_it_covers_is_not_on_the_label",
        detail=(
            "The purge takes no width: OutcomeLabel.window.sessions is the exact set of "
            "sessions the target reads and LabelWindow.close_instant dates it, so how much to "
            "remove is measured rather than chosen. The embargo takes one, and the reason is "
            "that what it covers is not on the label at all -- a feature column's trailing "
            "lookback, a fundamental's publication lag, and a revision that re-dates a value "
            "the training example read. None of those is visible from a window and this module "
            "reads no factor definition, so the sessions of separation are stated by the "
            "caller and there is no default. Zero is a legal statement -- 'no separation is "
            "required' -- and removes nothing. It is not a knob that turns the rule off: the "
            "rule it would be turning off is the purge, and no embargo width can reach what "
            "the purge takes, because the embargo only ever considers candidates the purge "
            "declined."
        ),
    ),
    WalkForwardLimitation(
        code="training_set_overlaps_is_grouped_by_security_and_a_fold_boundary_is_not",
        detail=(
            "V2-P4-011 named TrainingSet.overlaps as the measurement this issue needs. It is "
            "not the purge, and the reason is in overlapping_windows' own docstring: it groups "
            "by security, on the ground that two securities' windows spanning one set of "
            "sessions 'is not an overlap in any sense a purge cares about'. That is true of "
            "two samples' independence and false of a fold boundary. A training label measured "
            "over sessions inside the test period is a realized market return inside the test "
            "period whichever security it is about, and cross-sectional returns share a market "
            "factor. Measured both directions in "
            "test_training_set_overlaps_cannot_draw_the_boundary_this_purge_draws: on the "
            "ordinary corpus every pair it reports sits wholly on one side of the boundary, "
            "and on a corpus whose securities do not repeat across the boundary it reports no "
            "crossing pair at all while the boundary still leaks. What it remains is a "
            "diagnostic for whether a label set overlaps, which is what its docstring claims "
            "for it."
        ),
    ),
    WalkForwardLimitation(
        code="an_unordered_split_is_unrepresentable_and_a_badly_placed_block_is_only_refused",
        detail=(
            "禁止随机切分 is structural for the part that can be. WalkForwardFold carries a "
            "panel, a calendar, a first test day, a block length and an embargo width, and no "
            "field naming which rows train -- so train membership is derived from the boundary "
            "and there is nowhere to write down a shuffled partition, a fold whose training "
            "rows come from after its test block, or a split sending one security's later rows "
            "to train and another's earlier rows to test. A boundary is a date, and a date "
            "takes the whole cross section with it. What is only refused rather than "
            "unrepresentable is where the block sits: a first test day the panel never asked "
            "on, a block running past the panel's end, and a block starting on the panel's "
            "first day are three constructor refusals, and a caller may still place a legal "
            "block anywhere. walk_forward_folds is what tiles the blocks from the tail in "
            "order; a hand-built fold is not held to that schedule."
        ),
    ),
    WalkForwardLimitation(
        code="only_an_expanding_training_window_is_offered",
        detail=(
            "A fold's candidates are every prediction day strictly before its block, so the "
            "training span grows with each fold and never slides. A rolling window -- train on "
            "the last N days only -- is a different study and a defensible one on a market "
            "whose regime moves. It is absent rather than configurable because nothing on this "
            "chain has asked for one and a second schedule needs its own answer to what N "
            "means when prediction days are sparse. Whichever issue first needs it owns it, "
            "and the fold type does not have to move when it arrives: a rolling variant is "
            "another way of choosing candidates, and the two rules that cut them are unchanged."
        ),
    ),
    WalkForwardLimitation(
        code="nothing_here_evaluates_a_fold_and_this_corpus_is_not_a_benchmark",
        detail=(
            "This module splits and stops. It fits no model, computes no metric and reports no "
            "number: V2-P4-014 and V2-P4-015 own the baselines and their evaluation, and "
            "Implementation Decision 11's split-policy field on a model artifact is "
            "V2-P4-016's to place. The skill numbers in "
            "tests/unit/backtest/test_walk_forward_leak.py exist to demonstrate that a rule "
            "removes a planted leak and are not a claim about alpha -- the corpus behind them "
            "has no noise model, and its signal is a step function chosen to flip one bit of a "
            "reference model. V2-P4-022 owns the dataset with a known signal-to-noise ratio "
            "and a known-null control, which is the one an evaluation may report against, and "
            "it depends on this issue rather than the other way round."
        ),
    ),
    WalkForwardLimitation(
        code="the_join_is_by_instant_and_cannot_check_that_a_feature_row_is_point_in_time",
        detail=(
            "labelled_panel refuses a label whose prediction day is not the one the cross "
            "section's as_of resolves to in the window's own zone. That is "
            "build_label_window's own first step reused, and it catches the case where a "
            "feature matrix resolved to an earlier build than the caller asked for while the "
            "label was dated to the day the caller asked about. It cannot check that the "
            "feature values themselves were readable at that instant. V2-P4-012 owns that -- "
            "every column comes back through the three read_visible_at loaders -- and the P2 "
            "gate is where a look-ahead in a factor build is red-teamed. So a panel assembled "
            "from contaminated features splits cleanly and produces a confident wrong answer, "
            "and the embargo is a backstop against that rather than a fix for it."
        ),
    ),
    WalkForwardLimitation(
        code="a_prediction_day_that_labels_nothing_is_refused_rather_than_skipped",
        detail=(
            "A cross section whose every row is unlabelled -- a market-wide halt, or a corpus "
            "read past its halt coverage -- raises rather than becoming a section with no "
            "examples. The alternative was to drop the day, and dropping is what makes a fold "
            "report a training span it does not have: a block length is counted in prediction "
            "days, so a silently missing day moves every boundary after it. The cost is real "
            "and is stated rather than hidden -- a caller whose corpus genuinely holds such a "
            "day has to drop it deliberately, and this module offers no switch for doing so. "
            "Individual unlabelled rows are the ordinary case and are disclosed through "
            "LabelledPanel.excluded instead, carrying OutcomeLabel.refusal_summary."
        ),
    ),
    WalkForwardLimitation(
        code="the_final_holdout_decision_12_asks_to_leave_untouched_is_not_this_split",
        detail=(
            "Implementation Decision 12 has three clauses. Walk-forward instead of a random "
            "split is here and purging and embargo for overlapping labels are here; the third "
            "-- the final holdout or forward paper-portfolio observation left untouched during "
            "selection -- is not. Every prediction day this panel holds is reachable by some "
            "fold, so nothing is held back by construction, and a caller who wants a final "
            "holdout gets one by not putting those days in the panel, which is a discipline "
            "rather than a gate. The gate belongs where the selection happens, and the "
            "forward-looking half is Story S32's and V2-P4-017's: a prediction persisted "
            "before its outcome is known is untouched because the outcome does not exist yet, "
            "which is a stronger guarantee than any split can give."
        ),
    ),
    WalkForwardLimitation(
        code="the_finest_walk_forward_this_repository_can_currently_cut_is_annual",
        detail=(
            "Nothing in this module has a granularity of its own -- it splits whatever "
            "prediction days it is given -- and the granularity available upstream is coarser "
            "than a reader would assume. The roadmap's V2-P3-004 review measured it: "
            "neutralized_observation_batch stamps every derived row's available_time at the "
            "build's as_of, and that as_of has to be at or after the year's daily_basic "
            "partition max_available_time, which is the year's last session. So a neutralized "
            "column read at any mid-year instant comes back empty rather than refused. "
            "V2-P4-026 moved that bottleneck by giving load_daily_valuations an explicit "
            "session-level gate and is a hard prerequisite of this issue for exactly this "
            "reason. A matrix built only from raw and processed tiers is unaffected. This "
            "entry exists so that a walk-forward over hundreds of instants is not read as "
            "available today merely because this module would split one."
        ),
    ),
)
"""Eleven named boundaries, in `KNOWN_ALPHA_MODEL_LIMITATIONS`' form.

Every `code` is required to appear as a string literal in executable test code by
`tests/unit/test_known_limitation_registries.py`, which is what keeps a rename from silently
orphaning every citation of it.
"""
