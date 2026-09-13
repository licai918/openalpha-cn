"""Index composition and published weights (`V2-P1-009`) -- two answers, not one.

Tushare's `index_weight` serves one row per `(index, publication date, constituent)` with a
percentage `weight`. A live probe on 2026-08-09 read the whole published history of the three
indices this issue names -- `000300.SH` (沪深300, from 2005-04-29), `000905.SH` (中证500, from
2007-01-31) and `000852.SH` (中证1000, from 2014-10-31) -- **633 publications, 336,298
constituent rows** -- and every number in this module comes from it.

## The one thing this dataset is not: a daily series

The endpoint publishes **once per calendar month**, on that month's last open SSE session.
Measured exhaustively rather than sampled: 633 publications fall on 633 distinct months, no
month inside any index's life carries none, no month carries two, and all 633 dates are the last
open session of their month on the stored SSE calendar. `000300.SH` published on 12 dates in
2023 (2023-01-31 … 2023-12-29) and on 9 in 2005, its launch year.

So every question about a day that is not a publication day is answered by carrying the previous
publication forward, and **that is where this module has to be careful**.

## Why the answer is split in two

`domain/adjustment.py` stores a step function too, and the resemblance is the trap.
An adjustment factor genuinely *does not change* between two ex-rights days: reading
2024-03-15's factor off the 2024-02-08 observation is exact. An index weight is the opposite.
It is a month-end snapshot of a capitalisation share, and it starts moving with prices the very
next session. Nothing in this dataset says by how much.

The two facts a caller wants therefore have different reliability, and they are returned as
different types so they cannot be confused:

- **`constituents_on(day) -> IndexComposition`** -- who the members are. This survives a forward
  fill far better than the weights do and it is **not** exact, in two unrelated ways that are
  easy to conflate. `composition_is_also_forward_filled` counts 38 terminations landing inside
  a fill window. `scheduled_review_takes_effect_before_it_is_published` is the larger one by
  three orders of magnitude: the publisher's twice-yearly review takes effect *two weeks before*
  the publication that reports it, which is **49,900** measured (name x session) answers naming
  a security that had already been removed -- and the same 49,900 omitting one that had already
  been added.
- **`weights_on(day) -> IndexWeights`** -- how much each member is worth. A forward fill here is
  an **approximation whose error grows with the day of the month**, and this module makes no
  claim about its size.

Both answers therefore carry `as_published_on`, `days_since_publication` and
`undated_rebalance` -- the last being the names that changed at the *next* publication, which
this dataset cannot place anywhere inside the gap.

There is deliberately no `IndexMembership.weight_of(con_code, day) -> float`. That is not a
claim that a bare float is unreachable: `weights_on(day).weight_of(code)` is one line and is
meant to be. What is refused is the **signature** -- a callable taking a day and returning a
number, because that is the one a caller stores in a variable, passes across a module boundary
and reads six months later as "the weight on 2024-12-15". Routing through `IndexWeights` means
the publication date and the age are in hand at the instant the number is taken, so losing them
is an act rather than an omission.

## The sum tolerance is derived, not calibrated

The weights are a rounded percentage column that sums to *about* 100 and never exactly:
100.002, 99.997 and 100.002 for the three indices on 2024-06-28. Across the whole corpus the
deviation has a median of 0.005, a 99th percentile of 0.11 and a maximum of **0.21**
(`000852.SH`, 2014-10-31). A tolerance drawn from one recent session -- 0.003, say -- would
refuse more than half of the published history.

So the bound is arithmetic instead. `n` cells rounded to `d` decimals can move a total by at
most `n * 0.5 * 10**-d`, which is `published_weight_tolerance(count=n, decimals=d)`, and a
correctly rounded publication cannot breach it for any `n` or `d`. Applied to all 633
publications it refuses none, and the tightest fit is `000905.SH` on 2009-01-23 at 0.092 of its
bound. The precision is read from the publication rather than assumed, because the endpoint
changed it twice: three decimals in 2005..2010 and 2016..2026, two in 2013..2014, and 2011, 2012
and 2015 carrying publications of both kinds.

**That last part is not something the corpus proves is necessary, and saying otherwise would be
the overclaim this repository keeps having to correct.** Every one of the 633 publications --
including all of the two-decimal era -- happens to sit inside the *three*-decimal bound as well,
so a hard-coded `decimals=3` would admit exactly the same set. What the per-publication rule
buys is the case the corpus contains evidence *for* rather than evidence *of*: this endpoint has
already published more coarsely once, and a 300-name index published at two decimals may
legitimately miss by up to 1.5, which the fixed rule would refuse at 0.15.

The check is loose by construction (independent rounding errors grow as `sqrt(n)`, the bound as
`n`), and it is not tightened to the measured ratio because that would be exactly the
narrow-sample calibration the derived bound exists to avoid. It is still not vacuous: the
failure it is there for is a response the 7,000-row cap split in the middle, and a hundred names
of a three-hundred-name index sum to about 33 against a tolerance of 0.05.

## Layering

Pure `datetime`, `bisect`, `dataclasses` and `decimal`, plus two sibling `domain` modules
(`panel_batch` for the reserved subject-column name, `stock_universe` for the registry join). No
provider, no store, no clock -- the same placement, and the same reason, as
`domain/trading_calendar.py`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME
from openalpha_cn.domain.stock_universe import ListingStatus, StockUniverse, StockUniverseError

INDEX_WEIGHT_DATASET: Final[str] = "index_weight"
"""The panel dataset (and partition directory) index compositions are stored under.

Declared here rather than in `providers/tushare.py` for the reason `TRADING_CALENDAR_DATASET`
is: `panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

INDEX_PUBLICATION_DATE_COLUMN: Final[str] = "publication_date"
"""What the response calls `trade_date`, renamed on the way into the panel.

`adj_factor` renames its `trade_date` to `factor_date` for the same reason: four datasets
already store a column called `trade_date` and every one of them means "the session this row
describes". This one does not -- it means "the session whose close the publisher computed this
snapshot from", and the rows answer questions about the *following* month.
"""

INDEX_CONSTITUENT_COLUMN: Final[str] = "con_code"
INDEX_WEIGHT_COLUMN: Final[str] = "weight"

INDEX_WEIGHT_DATA_COLUMNS: Final[tuple[str, ...]] = (
    INDEX_PUBLICATION_DATE_COLUMN,
    INDEX_CONSTITUENT_COLUMN,
    INDEX_WEIGHT_COLUMN,
)
"""The columns a provider projects, in order. `subject` is added by the batch itself."""

INDEX_WEIGHT_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *INDEX_WEIGHT_DATA_COLUMNS,
)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back.

The subject column is the **index**, not the constituent, and that choice is load-bearing rather
than cosmetic. `PanelStore`'s partition key is `(dataset, year)` with no index dimension and
`write_partition` replaces a partition whole, so three indices sharing one year have to be
distinguishable by a stored column or the second write destroys the first -- exactly the failure
`V2-P1-004` hit with `exchange` and answered with `panel_ingest._refuse_to_drop_stored_subjects`.
That guard compares stored subjects against a batch's, so it only works if the subject is the
thing that would go missing.

Reading the constituent out of the subject column would *also* trip that guard today, and only
by that coincidence. It would **not** start misfiring on ordinary rebalances -- the guard
compares a whole *partition's* subject set against a whole *batch's*, and a batch for a year
carries every name that year published, so rewriting 2023 for all three indices drops zero
subjects under either choice. What it would stop catching is the case the guard exists for:
`000906.SH` 中证800 is `000300.SH` plus `000905.SH`, so a batch for it covers both of their
constituent sets, and writing it over their partition would drop two indices while the guard
saw nothing go missing. The index is also what `index_weight_requirement` names in
`required_subjects` and what `load_index_membership` filters on, neither of which the
constituent could serve.
"""

CSI300_INDEX_CODE: Final[str] = "000300.SH"
CSI500_INDEX_CODE: Final[str] = "000905.SH"
CSI1000_INDEX_CODE: Final[str] = "000852.SH"

INDEX_WEIGHT_INDEX_CODES: Final[tuple[str, ...]] = (
    CSI300_INDEX_CODE,
    CSI500_INDEX_CODE,
    CSI1000_INDEX_CODE,
)
"""The three indices `V2-P1-009` measures. Not a limit on what the descriptor can fetch.

`providers/tushare.py`'s `index_weight` descriptor takes whatever `index_code` a caller asks
for. What is bounded by this tuple is the *evidence*: the response cap, the monthly cadence, the
sum tolerance and the constituent-count exceptions below were all measured on these three, and
another index inherits the code without inheriting the measurement.
"""

TOTAL_PUBLISHED_WEIGHT: Final[float] = 100.0
"""What a publication's weights add up to. The column is a **percentage**: 5.19 means 5.19%."""

MAX_PUBLISHED_WEIGHT_DECIMALS: Final[int] = 3
"""The most decimals any measured publication carries, and the cap on the inferred precision.

Inferring the precision per publication is the safe direction on its own -- taking the maximum
over the cells can only ever *under*-report it, and fewer decimals means a wider tolerance. The
cap guards the other end: one cell arriving as `0.3000000000000004` would derive a tolerance of
roughly zero and refuse the entire corpus, so anything past three decimals is treated as three.
A genuine upstream move to five decimals would only shrink the real deviations, so the capped
bound stays sound rather than becoming optimistic.
"""


class IndexMembershipError(ValueError):
    """Raised for any malformed index publication or malformed query against one.

    A `ValueError` subclass to match `domain/trading_calendar.py`'s `TradingCalendarError` and
    `domain/adjustment.py`'s `AdjustmentError`.
    """


class IndexMembershipHorizonError(IndexMembershipError):
    """Raised when a question's answer would lie outside the publications actually read.

    Separate from its parent because it is not a caller mistake: the question was well formed
    and the read simply did not cover that day. `V2-P1-013`'s gate blocks on it; a malformed
    query is a bug.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstituentWeight:
    """One constituent's published share of one index, as a percentage.

    A plain carrier with no validation of its own, following `FactorObservation`'s precedent: a
    nominal type is not a boundary, so the rules live once, in `build_index_publication`.
    """

    con_code: str
    weight: float

    @property
    def fraction(self) -> float:
        """The weight as a fraction of the index, i.e. `weight / 100`.

        Offered because the published unit is a percentage and the hundredfold error it invites
        is silent: a portfolio built on `weight` rather than `fraction` is 100x levered and every
        relative number in it still looks right.
        """
        return self.weight / TOTAL_PUBLISHED_WEIGHT


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexLimitation:
    """One named boundary on what a stored index composition can be trusted to answer."""

    code: str
    detail: str


KNOWN_INDEX_MEMBERSHIP_LIMITATIONS: Final[tuple[IndexLimitation, ...]] = (
    IndexLimitation(
        code="weights_drift_between_publications",
        detail=(
            "A weight is a month-end snapshot of a capitalisation share, and it moves with "
            "prices from the next session onward. The endpoint publishes once a month -- 633 "
            "publications on 633 distinct months across 2005-04..2026-07 -- so a question "
            "asked mid-month is answered from a snapshot up to 30 calendar days (about 22 "
            "sessions) old, and this dataset carries nothing that says how far it has moved "
            "since. This is the sharpest difference from domain/adjustment.py, whose step "
            "function is exact between change points: a factor really is constant between two "
            "ex-rights days, an index weight really is not constant between two publications. "
            "IndexWeights therefore carries as_published_on and days_since_publication, and "
            "there is no accessor that returns a bare float for a day."
        ),
    ),
    IndexLimitation(
        code="composition_is_also_forward_filled",
        detail=(
            "Membership survives a forward fill much better than the weights do, and not "
            "perfectly. Across the corpus 38 constituent terminations fall strictly inside a "
            "forward-fill window: the security is in publication P, its registry delist_date "
            "is after P and at or before the next publication, and every day in between "
            "answers with a security that had already gone. 600001.SH 邯郸钢铁 is in "
            "000300.SH's 2009-11-30 publication, was terminated 2009-12-29, and the next "
            "publication is 2009-12-31 -- so 2009-12-29 and 2009-12-30 both name it. "
            "600837.SH 海通证券 (terminated 2025-03-04, between the 2025-02-28 and 2025-03-31 "
            "publications) and 000982.SZ 中银绒业 (2024-08-12, between 2024-07-31 and "
            "2024-08-30) are the same shape. Membership also changes far more often than the "
            "twice-a-year review suggests: 166 of the 630 measured publication-to-publication "
            "transitions changed it, the scheduled June and December reviews replacing 50 "
            "names in 000905.SH and 100 in 000852.SH, and single-name substitutions landing in "
            "every other month of the year. Those scheduled replacements are a second and much "
            "larger problem in their own right -- they take effect before the publication that "
            "reports them -- and they are counted separately under "
            "scheduled_review_takes_effect_before_it_is_published; the 38 here are "
            "terminations only."
        ),
    ),
    IndexLimitation(
        code="scheduled_review_takes_effect_before_it_is_published",
        detail=(
            "The twice-yearly review takes effect BEFORE the publication that reports it, and "
            "the effect is not 38 rows, it is 49,900. The publisher's methodology puts the "
            "June and December reviews into force after the close of that month's second "
            "Friday; index_weight does not carry the new list until the month's last session, "
            "so for the sessions in between constituents_on answers from a composition the "
            "publisher had already replaced. It is not a refusal and not a wide error bar -- "
            "no horizon is raised, nothing is flagged, and days_since_publication reports only "
            "how old the snapshot is, never that the publisher changed it in the meantime. "
            "Measured on the corpus rather than argued: 000300.SH's 2026-05-29 and 2026-06-30 "
            "publications differ by 19 names, 000905.SH's by 50 and 000852.SH's by 100, and "
            "2026-06-15..2026-06-29 -- the sessions after the 2026-06-12 second Friday and "
            "before the 2026-06-30 publication -- is 10 of them, so every 000300.SH question "
            "in that window names 19 securities that had gone and omits 19 that had arrived. "
            "Over all 76 June and December reviews from 2013-12 to 2026-06 (9 to 14 sessions "
            "each, 7 to 100 names each) that is 6,358 + 15,250 + 28,292 = 49,900 (name x "
            "session) answers naming a removed security, and 49,900 more omitting an added "
            "one. A lower bound: the reviews fell in January and July before 2013 and those "
            "are not in the count. Only the effective-date rule is external to this dataset; "
            "the response has no effective-date column and never will. What the module can "
            "state from its own rows is which names are unsettled, and "
            "IndexComposition.undated_rebalance states it without needing the rule or a "
            "calendar."
        ),
    ),
    IndexLimitation(
        code="a_publication_can_carry_fewer_names_than_the_index_is_called",
        detail=(
            "000300.SH published 298 constituents on 2009-12-31, not 300. 600001.SH 邯郸钢铁 "
            "and 600357.SH 承德钒钛 were both terminated on 2009-12-29 and the month-end "
            "publication dropped them with no replacement; the 2010-01-29 review restored the "
            "count. That is the only off-nominal publication among the 633 measured (000905.SH "
            "is 500 on all 235 of its own, 000852.SH is 1000 on all 142), and it is why "
            "nothing in this module checks a constituent count against the index's name."
        ),
    ),
    IndexLimitation(
        code="published_weights_do_not_sum_to_exactly_one_hundred",
        detail=(
            "The published column sums to about 100 and never exactly: 100.002 / 99.997 / "
            "100.002 for the three indices on 2024-06-28. Over the 633 publications the "
            "absolute deviation has a median of 0.005, a 90th percentile of 0.03, a 99th of "
            "0.11 and a maximum of 0.21 (000852.SH, 2014-10-31, 1,000 names published at two "
            "decimals). The weights are stored as published rather than renormalised, so a "
            "consumer that needs them to sum to one must divide by the publication's own "
            "published_total rather than by 100."
        ),
    ),
    IndexLimitation(
        code="a_constituent_can_be_missing_from_the_security_registry",
        detail=(
            "990018.SH appears in eighteen 000300.SH publications from 2005-04-29 to "
            "2006-09-29, at weights from 0.681 to 1.391, and is in neither the L nor the D "
            "half of stock_basic's 5,878-row registry -- so domain/stock_universe.py refuses "
            "it as an unknown code rather than answering about it. It is not a corrupt code. "
            "namechange(990018.SH) resolves it to 上港集箱 / G上港, the entity 上港集团 "
            "absorbed by share exchange in 2006, and stock_basic dates the surviving listing "
            "600018.SH 上港集团 to 2006-10-26; the publications show the handover directly, "
            "with 990018.SH in 000300.SH on 2006-09-29, out on 2006-10-31 and 600018.SH "
            "arriving in that same transition. Tushare renumbers a pre-merger entity out of "
            "the survivor's code and stock_basic keeps only the survivor, which is the "
            "mechanism V2-P1-007 met in namechange. Separately, 600270.SH "
            "外运发展 is in 000905.SH's 2018-12-28 publication at weight 0.253 while its "
            "registry delist_date is 2018-12-28, which that module treats as exclusive: the "
            "index published a constituent the registry says was already gone. Those are the "
            "only two constituent identities out of 336,298 constituent-rows that the registry "
            "cannot confirm, and constituent_listing_report reports both rather than refusing "
            "the publication -- discarding a real month of a real index over one upstream "
            "inconsistency would be the worse answer."
        ),
    ),
    IndexLimitation(
        code="publication_lag_is_not_in_the_response",
        detail=(
            "The response carries no announcement column, so availability is dated at the "
            "publication session's close (15:00/16:30 Asia/Shanghai, "
            "ClockStrategy.daily_close). That is the earliest instant the snapshot can exist, "
            "because it is computed from that session's close -- but if the publisher in fact "
            "releases the file the following morning, this rule is a look-ahead of up to one "
            "session on 633 publications and nothing in this endpoint can say which. The "
            "direction is stated rather than argued away: unlike stk_limit, whose daily_close "
            "dating is one session late and therefore conservative, this one is potentially "
            "one session early."
        ),
    ),
    IndexLimitation(
        code="silent_truncation_at_the_response_cap",
        detail=(
            "The endpoint serves at most 7,000 rows per response and drops the oldest, and no "
            "`limit` raises that: limit=5000 returns exactly 5,000 and limit=20000 returns "
            "7,000, so the parameter can only narrow the answer and 7,000 is the server's own "
            "ceiling. Truncation "
            "can split a publication rather than only dropping whole ones: "
            "index_weight(000300.SH, 20100101..20231231) returns 7,000 rows over 24 dates "
            "whose oldest, 2022-01-28, carries 100 of its 300 names. "
            "providers/tushare.py::_check_response_completeness refuses such a response, and "
            "the sum tolerance here would refuse the split publication even if it did not."
        ),
    ),
    IndexLimitation(
        code="no_revision_history",
        detail=(
            "The endpoint serves one snapshot per request and carries no revision instant, so "
            "a weight corrected upstream is indistinguishable from one that was always that "
            "value. revision_time equals available_time, which invents nothing; read a "
            "partition's revised_row_count of 0 as 'unmeasured', not 'none'. The precision "
            "eras -- three decimals in 2005..2010 and 2016..2026, two in 2013..2014, and "
            "2011, 2012 and 2015 each carrying publications of both kinds -- are themselves "
            "evidence that the published numbers get restated in form."
        ),
    ),
)
"""Named boundaries on what a stored index composition answers, each measured on real data.

**Not an enumeration of every way the published weights could be wrong.** These are the ones a
live probe of the endpoint could demonstrate on 2026-08-09, over the three indices named in
`INDEX_WEIGHT_INDEX_CODES` and the window 2005-04-29..2026-07-31.
"""


def published_weight_decimals(weights: Sequence[ConstituentWeight]) -> int:
    """How many decimals this publication was published to, capped at the measured maximum.

    The maximum over the cells, because that is the only estimator that cannot *over*-report
    (a cell may happen to end in a zero; none can show more precision than was published), and
    over-reporting is the dangerous direction -- it tightens the tolerance below what correctly
    rounded data can satisfy. See `MAX_PUBLISHED_WEIGHT_DECIMALS` for the other end.
    """
    observed = 0
    for entry in weights:
        text = format(Decimal(repr(entry.weight)).normalize(), "f")
        _, _, fraction = text.partition(".")
        observed = max(observed, len(fraction))
    return min(observed, MAX_PUBLISHED_WEIGHT_DECIMALS)


def published_weight_tolerance(*, count: int, decimals: int) -> float:
    """The most a correctly rounded weight column of `count` cells can miss 100 by.

    `count * 0.5 * 10**-decimals`: each cell is at most half a unit in the last place away from
    its unrounded value, and the errors can in principle all point the same way. See this
    module's docstring for why an arithmetic worst case is preferred to a calibrated one, and
    for the measurement showing it refuses none of 633 real publications.
    """
    if count < 1:
        raise IndexMembershipError(
            f"a weight-sum tolerance needs at least one constituent, got count={count}"
        )
    if decimals < 0:
        raise IndexMembershipError(
            f"a weight-sum tolerance needs a non-negative decimal count, got {decimals}"
        )
    return count * 0.5 * 10.0**-decimals


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexPublication:
    """One index's constituents and weights as published on one session.

    Build it with `build_index_publication`; this constructor is not a boundary and validates
    nothing. `weights` is ascending by `con_code`.
    """

    index_code: str
    published_on: date
    weights: tuple[ConstituentWeight, ...]

    @property
    def constituents(self) -> tuple[str, ...]:
        """Every constituent code, ascending."""
        return tuple(entry.con_code for entry in self.weights)

    @property
    def constituent_count(self) -> int:
        return len(self.weights)

    @property
    def published_total(self) -> float:
        """The weights as published, added up. Not 100 -- see this module's docstring."""
        return sum(entry.weight for entry in self.weights)


def build_index_publication(
    *, index_code: str, published_on: date, weights: Iterable[ConstituentWeight]
) -> IndexPublication:
    """Assemble an `IndexPublication` from constituent weights, in any order.

    Refuses, rather than repairs, five things: a malformed index code, a malformed date, an
    empty constituent set, a weight that cannot be a share of an index, a duplicated
    constituent, and a total outside the derived tolerance.

    A duplicated `con_code` is refused even when the two weights agree, unlike
    `build_adjustment_history`'s byte-identical collapse: a repeated constituent would be
    counted twice by every weighted sum downstream, and one publication naming a security twice
    is two sources that were never reconciled rather than a redundant row.
    """
    _require_text(index_code, "index_code")
    _require_plain_date(published_on, "published_on")
    seen: dict[str, ConstituentWeight] = {}
    for entry in weights:
        _require_text(entry.con_code, "con_code")
        _require_weight(entry.weight, entry.con_code)
        if entry.con_code in seen:
            raise IndexMembershipError(
                f"{entry.con_code} appears more than once in {index_code}'s "
                f"{published_on.isoformat()} publication; a constituent has one weight, and "
                "two rows for it would be counted twice by every weighted sum"
            )
        seen[entry.con_code] = entry
    if not seen:
        raise IndexMembershipError(
            f"{index_code}'s {published_on.isoformat()} publication has no constituents; an "
            "empty index answers 'nothing is in it' to every question, which is "
            "indistinguishable from a failed read"
        )
    ordered = tuple(seen[key] for key in sorted(seen))
    total = sum(entry.weight for entry in ordered)
    tolerance = published_weight_tolerance(
        count=len(ordered), decimals=published_weight_decimals(ordered)
    )
    if abs(total - TOTAL_PUBLISHED_WEIGHT) > tolerance:
        raise IndexMembershipError(
            f"{index_code}'s {published_on.isoformat()} publication carries {len(ordered)} "
            f"constituents whose weights sum to {total!r}, which does not sum to 100 within "
            f"{tolerance!r} -- the most a correctly rounded column of that many cells can miss "
            "by. A short sum is what a response truncated in the middle of a publication looks "
            "like"
        )
    return IndexPublication(index_code=index_code, published_on=published_on, weights=ordered)


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexRebalance:
    """What changed between two consecutive publications of one index."""

    index_code: str
    previous_publication: date
    publication: date
    added: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def is_one_for_one(self) -> bool:
        """Whether every name that left was replaced.

        Almost always true, and the exception is the point: `000300.SH`'s 2009-12-31 publication
        removed two names and added none, which is how that publication came to carry 298
        constituents.
        """
        return len(self.added) == len(self.removed)

    @property
    def changed(self) -> tuple[str, ...]:
        """Every name on either side of the transition, ascending. Added and removed are
        disjoint by construction, so this is their union and its length is their sum."""
        return tuple(sorted((*self.added, *self.removed)))


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexComposition:
    """Who an index's members were on one day, and which publication says so.

    Carries no weight of any kind. A caller that only needs membership therefore never holds a
    number it could mistake for a current one -- see this module's docstring.

    ## `undated_rebalance`, and why a day count was not enough

    `days_since_publication` says how old the snapshot is. It does not say that the publisher
    replaced part of it in the meantime, and on the two months a year where that matters most
    the publisher reliably has: the June and December reviews take effect after the close of
    that month's second Friday and are not published until the month's last session, so 9 to 14
    sessions every half-year are answered from a composition that had already been superseded
    -- 49,900 measured (name x session) answers naming a security that had gone, and as many
    again omitting one that had arrived. See
    `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS.scheduled_review_takes_effect_before_it_is_published`.

    `undated_rebalance` is the transition out of `as_published_on`, i.e. what the *next*
    publication in the read reports as having changed. It is `None` on a publication day (there
    is nothing carried forward to be wrong about) and on a transition that changed nothing.

    It is deliberately built from this module's own rows and nothing else. The effective-date
    rule is the publisher's, the response has no effective-date column, and deriving the second
    Friday's next session would need a `TradingCalendar` this module does not take -- so the
    claim made here is the weaker one that is entirely this dataset's: *these names changed
    somewhere inside this gap and the gap is all the resolution there is.* That is why it also
    covers the ordinary single-name substitutions, which are on no schedule at all, and why it
    keeps working across the pre-2013 era when the reviews fell in January and July instead.

    Consequently it over-reports rather than under-reports: on 2026-06-01 it already names the
    19 securities that would not change until 2026-06-15. A dependency gate (`V2-P1-013`) that
    refuses on it refuses a superset of the wrong answers, which is the direction to be wrong in.
    """

    index_code: str
    asked_for: date
    as_published_on: date
    members: tuple[str, ...]
    undated_rebalance: IndexRebalance | None

    @property
    def days_since_publication(self) -> int:
        """Calendar days between the publication and the day asked about; 0 on a publication."""
        return (self.asked_for - self.as_published_on).days

    @property
    def is_as_published(self) -> bool:
        """Whether the day asked about is itself the publication day."""
        return self.asked_for == self.as_published_on

    def includes(self, con_code: str) -> bool:
        """Whether `con_code` is in this composition."""
        return con_code in self.members


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexWeights:
    """An index's published weights, as they stood on the last publication before a given day.

    The staleness fields are not decoration. Unlike a composition, which the publisher changes
    on a schedule, a weight starts drifting the session after it is published, so
    `days_since_publication` is the size of the window over which this answer is an
    approximation. See `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS.weights_drift_between_publications`.

    `undated_rebalance` carries the same fact as on `IndexComposition`, and it bites harder
    here: a name the publisher had already removed still carries its full published weight in
    this snapshot, so it is not merely present in a membership, it is sized.
    """

    index_code: str
    asked_for: date
    as_published_on: date
    weights: tuple[ConstituentWeight, ...]
    undated_rebalance: IndexRebalance | None

    @property
    def days_since_publication(self) -> int:
        return (self.asked_for - self.as_published_on).days

    @property
    def is_as_published(self) -> bool:
        return self.asked_for == self.as_published_on

    @property
    def published_total(self) -> float:
        """The weights as published, added up; the divisor for turning them into fractions."""
        return sum(entry.weight for entry in self.weights)

    def includes(self, con_code: str) -> bool:
        return any(entry.con_code == con_code for entry in self.weights)

    def weight_of(self, con_code: str) -> float:
        """This constituent's published weight, as a percentage.

        Refuses an unknown code rather than answering `0.0`: "not a constituent" and "a
        constituent with no weight" are different facts, and a zero would flow into a weighted
        sum as though the second were true.
        """
        for entry in self.weights:
            if entry.con_code == con_code:
                return entry.weight
        raise IndexMembershipError(
            f"{con_code!r} is not a constituent of {self.index_code} as published on "
            f"{self.as_published_on.isoformat()}; a weight of 0 would be indistinguishable "
            "from a constituent the publisher gave no weight to"
        )

    def composition(self) -> IndexComposition:
        """The same answer with the weights dropped -- the free direction of the split.

        `undated_rebalance` travels with it. Dropping it here would make the cheaper type the
        less honest one, which is the opposite of what the split is for.
        """
        return IndexComposition(
            index_code=self.index_code,
            asked_for=self.asked_for,
            as_published_on=self.as_published_on,
            members=tuple(entry.con_code for entry in self.weights),
            undated_rebalance=self.undated_rebalance,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConstituentListingReport:
    """What a security registry says about one publication's constituents (`V2-P1-005` join).

    Three separate answers rather than one count, because they are three different facts and
    only the second is an inconsistency between two datasets that both claim to know:

    - `unknown_to_registry`: `stock_basic` has never carried this code at all.
    - `not_listed`: the registry carries it and says it was not listed that day.
    - `beyond_snapshot`: the publication post-dates the registry snapshot, so the registry has
      no opinion. `StockUniverse` refuses to answer past its own snapshot and this join must not
      turn that refusal into a quiet "not listed".
    """

    index_code: str
    published_on: date
    unknown_to_registry: tuple[str, ...]
    not_listed: tuple[str, ...]
    beyond_snapshot: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        """Whether the registry confirmed every constituent was listed on the publication day."""
        return not (self.unknown_to_registry or self.not_listed or self.beyond_snapshot)


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexMembership:
    """One index's published history over a bounded window of publications.

    Build it with `build_index_membership`; this constructor is not a boundary and validates
    nothing. `publications` is ascending by `published_on` and holds at least one entry.
    """

    index_code: str
    publications: tuple[IndexPublication, ...]

    @property
    def covered_from(self) -> date:
        """The first day this membership can answer for -- its earliest publication."""
        return self.publications[0].published_on

    @property
    def covered_through(self) -> date:
        """The last day this membership can answer for; see `publication_in_effect_on`."""
        return self.publications[-1].published_on

    @property
    def publication_dates(self) -> tuple[date, ...]:
        return tuple(entry.published_on for entry in self.publications)

    def publication_in_effect_on(self, day: date) -> IndexPublication:
        """The publication a question about `day` is answered from.

        Refuses both ends of the window rather than extrapolating. The lower bound is the same
        rule `AdjustmentHistory.factor_on` applies; the upper one is stricter in spirit, because
        an index's membership changed on 166 of the 630 measured transitions, so carrying the
        last publication past the end of the read would assert that nothing happened in a window
        that usually has something in it.
        """
        _require_plain_date(day, "day")
        if day < self.covered_from:
            raise IndexMembershipHorizonError(
                f"{day.isoformat()} is before {self.index_code}'s first publication in this "
                f"read, {self.covered_from.isoformat()}; the composition in effect that day was "
                "published earlier and this read never saw it"
            )
        if day > self.covered_through:
            raise IndexMembershipHorizonError(
                f"{day.isoformat()} is after {self.index_code}'s last publication in this read, "
                f"{self.covered_through.isoformat()}; carrying it forward would assert that no "
                "rebalance happened in a window this read never covered, and 166 of 630 "
                "measured transitions changed the membership"
            )
        position = bisect_right(self.publication_dates, day)
        return self.publications[position - 1]

    def constituents_on(self, day: date) -> IndexComposition:
        """Who the members were on `day`, with the publication that says so."""
        publication = self.publication_in_effect_on(day)
        return IndexComposition(
            index_code=self.index_code,
            asked_for=day,
            as_published_on=publication.published_on,
            members=publication.constituents,
            undated_rebalance=self.undated_rebalance_on(day),
        )

    def undated_rebalance_on(self, day: date) -> IndexRebalance | None:
        """What changed between the publication answering for `day` and the one after it.

        `None` when `day` is itself a publication day -- nothing is being carried forward, so
        there is nothing for a change to have invalidated -- and `None` when the following
        publication changed no names. Otherwise the following publication is always in the read:
        a `day` past `covered_through` was already refused by `publication_in_effect_on`, and
        every other day has a publication after it by construction.

        The names it reports changed *somewhere* between the two publications. This dataset
        cannot say where, which is exactly the point; see `IndexComposition`.
        """
        publication = self.publication_in_effect_on(day)
        if day == publication.published_on:
            return None
        following = self.publications[bisect_right(self.publication_dates, day)]
        return _rebalance_between(self.index_code, publication, following)

    def weights_on(self, day: date) -> IndexWeights:
        """The published weights in effect on `day`, with the publication that says so.

        Not "the weights on `day`". Between publications this is a month-end snapshot carried
        forward, and the drift is unmeasurable from this dataset -- which is why the result
        carries `as_published_on` and `days_since_publication` and there is no accessor that
        hands back a bare number for a day.
        """
        publication = self.publication_in_effect_on(day)
        return IndexWeights(
            index_code=self.index_code,
            asked_for=day,
            as_published_on=publication.published_on,
            weights=publication.weights,
            undated_rebalance=self.undated_rebalance_on(day),
        )

    def rebalances(self) -> tuple[IndexRebalance, ...]:
        """Every transition that changed the membership, ascending. Unchanged ones are omitted.

        464 of the 630 measured transitions changed nothing, so returning all of them would bury
        the ones that did.
        """
        changes = (
            _rebalance_between(
                self.index_code, self.publications[index - 1], self.publications[index]
            )
            for index in range(1, len(self.publications))
        )
        return tuple(change for change in changes if change is not None)


def _rebalance_between(
    index_code: str, previous: IndexPublication, current: IndexPublication
) -> IndexRebalance | None:
    """The membership difference between two consecutive publications, or `None` if there is
    none. Shared so that `rebalances()` and `undated_rebalance_on()` cannot drift apart."""
    before = set(previous.constituents)
    after = set(current.constituents)
    added = tuple(sorted(after - before))
    removed = tuple(sorted(before - after))
    if not added and not removed:
        return None
    return IndexRebalance(
        index_code=index_code,
        previous_publication=previous.published_on,
        publication=current.published_on,
        added=added,
        removed=removed,
    )


def build_index_membership(
    index_code: str, publications: Iterable[IndexPublication]
) -> IndexMembership:
    """Assemble an `IndexMembership` from publications, in any order.

    Refuses, rather than repairs, four things: a malformed index code, an empty history, a
    publication belonging to another index, and a month that carries two publications or none.

    ## The month rule is what pays for a waived date requirement

    `panel_ingest.index_weight_requirement` waives `required_dates`, because stating the
    publication days would mean deriving each month's last open session from a calendar this
    contract does not hold. What replaces it is stronger and needs no calendar: the endpoint
    publishes exactly once per calendar month (633 publications on 633 distinct months,
    2005-04..2026-07), so a gap in the month sequence is a lost publication and a doubled month
    is two sources.

    Unlike `domain/adjustment.py`'s equivalent, this census survives into the read: publications
    *are* the stored rows, so the rule runs on every load rather than only at write time, and it
    sees a hole that straddles two partitions. A partition that is short at either end is caught
    by the horizon instead -- `covered_from` and `covered_through` move with it, and the days it
    cannot answer are refused by name.
    """
    _require_text(index_code, "index_code")
    by_day: dict[date, IndexPublication] = {}
    for entry in publications:
        _require_plain_date(entry.published_on, "published_on")
        if entry.index_code != index_code:
            raise IndexMembershipError(
                f"a membership for {index_code} carries {entry.index_code}; one membership "
                "covers one index, and mixing two would answer one index's question with "
                "another index's constituents"
            )
        existing = by_day.get(entry.published_on)
        if existing is not None:
            raise IndexMembershipError(
                f"{index_code} has two publications dated {entry.published_on.isoformat()}; a "
                "publication is one snapshot, so two are two sources that were never reconciled"
            )
        by_day[entry.published_on] = entry
    if not by_day:
        raise IndexMembershipError(
            f"an index membership needs at least one publication; {index_code} has none, and an "
            "empty history refuses every question, which is indistinguishable from a failed read"
        )
    ordered = tuple(by_day[key] for key in sorted(by_day))
    _require_one_publication_per_month(index_code, ordered)
    return IndexMembership(index_code=index_code, publications=ordered)


def _require_one_publication_per_month(
    index_code: str, publications: Sequence[IndexPublication]
) -> None:
    """Refuse a doubled month, and a month with no publication inside the covered window."""
    months = [(entry.published_on.year, entry.published_on.month) for entry in publications]
    for index in range(1, len(months)):
        year, month = months[index - 1]
        if months[index] == (year, month):
            raise IndexMembershipError(
                f"{index_code} has two publications in {year:04d}-{month:02d} "
                f"({publications[index - 1].published_on.isoformat()} and "
                f"{publications[index].published_on.isoformat()}); the endpoint publishes once "
                "per calendar month on that month's last open session, so this is two sources "
                "that were never reconciled"
            )
        expected = (year + 1, 1) if month == 12 else (year, month + 1)
        if months[index] != expected:
            raise IndexMembershipError(
                f"{index_code} has no publication for {expected[0]:04d}-{expected[1]:02d}, "
                f"between {publications[index - 1].published_on.isoformat()} and "
                f"{publications[index].published_on.isoformat()}; the endpoint publishes once "
                "per calendar month, so a missing month would be answered for its whole length "
                "from a snapshot two months old"
            )


def index_memberships_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> Mapping[str, IndexMembership]:
    """Rebuild one membership per index from rows shaped like `INDEX_WEIGHT_PANEL_COLUMNS`.

    The counterpart of the provider's projection. What this function owns is the shape of a
    *stored row* -- its width, the ISO text its date column is stored as, and the fact that the
    weight comes back as a real `float` -- which `build_index_publication` never sees.

    A read-only mapping rather than a `dict`, because a whole-partition read is shared and a
    caller mutating one entry would be editing what other callers hold.
    """
    grouped: dict[tuple[str, date], list[ConstituentWeight]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(INDEX_WEIGHT_PANEL_COLUMNS):
            raise IndexMembershipError(
                f"row {index} has {len(row)} values, expected "
                f"{len(INDEX_WEIGHT_PANEL_COLUMNS)} ({', '.join(INDEX_WEIGHT_PANEL_COLUMNS)})"
            )
        subject, day_text, con_code, weight = row
        index_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        published_on = _parse_iso_date(day_text, index, INDEX_PUBLICATION_DATE_COLUMN)
        grouped.setdefault((index_code, published_on), []).append(
            ConstituentWeight(
                con_code=_require_stored_text(con_code, index, INDEX_CONSTITUENT_COLUMN),
                weight=_stored_weight(weight, index),
            )
        )
    by_index: dict[str, list[IndexPublication]] = {}
    for (index_code, published_on), weights in grouped.items():
        by_index.setdefault(index_code, []).append(
            build_index_publication(
                index_code=index_code, published_on=published_on, weights=weights
            )
        )
    return MappingProxyType(
        {
            index_code: build_index_membership(index_code, group)
            for index_code, group in by_index.items()
        }
    )


def constituent_listing_report(
    publication: IndexPublication, *, universe: StockUniverse
) -> ConstituentListingReport:
    """Ask a `StockUniverse` whether each constituent was listed on the publication day.

    The `V2-P1-009`-to-`V2-P1-005` join, and it **reports** rather than refuses, because the
    counterexamples are real: two constituent identities out of 336,298 constituent-rows cannot
    be confirmed by the registry, and a rule that refused the publication would discard a real
    month of a real index over one of them. See
    `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS.a_constituent_can_be_missing_from_the_security_registry`
    for both by name.

    An unknown code is `StockUniverseError` from `universe.status_on`, which is caught here and
    routed to `unknown_to_registry` -- an absent code is a fact about the registry, not a
    malformed query.
    """
    unknown: list[str] = []
    not_listed: list[str] = []
    beyond: list[str] = []
    for con_code in publication.constituents:
        try:
            status = universe.status_on(con_code, publication.published_on)
        except StockUniverseError:
            unknown.append(con_code)
            continue
        if status is ListingStatus.beyond_snapshot:
            beyond.append(con_code)
        elif status is not ListingStatus.listed:
            not_listed.append(con_code)
    return ConstituentListingReport(
        index_code=publication.index_code,
        published_on=publication.published_on,
        unknown_to_registry=tuple(unknown),
        not_listed=tuple(not_listed),
        beyond_snapshot=tuple(beyond),
    )


def _require_text(value: str, role: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise IndexMembershipError(
            f"{role} must be a non-empty string without surrounding whitespace; got {value!r}"
        )


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise IndexMembershipError(
            f"row {index}: {column} must be a non-empty string without surrounding whitespace, "
            f"got {type(value).__name__} {value!r}"
        )
    return value


def _require_weight(value: float, con_code: str) -> None:
    """Reject anything that cannot be a constituent's share of an index.

    `type(...) is float`, not `isinstance`, for `domain/adjustment.py::_require_price`'s reason:
    what only the exact check refuses is a `float` *subclass* such as `numpy.float64`, whose
    arithmetic is not `float`'s own.

    Zero is refused along with the negatives. Measured: 336,298 constituent rows carry a weight
    between 0.007 and 7.745 and not one zero, so a zero here is a fault -- and it is the
    dangerous kind, because it leaves a security in the membership while removing it from every
    weighted sum. The upper bound is `TOTAL_PUBLISHED_WEIGHT`: no constituent can be worth more
    than the whole index.
    """
    if (
        type(value) is not float
        or not isfinite(value)
        or value <= 0.0
        or value > TOTAL_PUBLISHED_WEIGHT
    ):
        raise IndexMembershipError(
            f"{con_code}'s weight must be a float in (0, {TOTAL_PUBLISHED_WEIGHT}]; got "
            f"{type(value).__name__} {value!r}"
        )


def _stored_weight(value: object, index: int) -> float:
    if type(value) is not float:
        raise IndexMembershipError(
            f"row {index}: {INDEX_WEIGHT_COLUMN} must be a float, got "
            f"{type(value).__name__} {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise IndexMembershipError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise IndexMembershipError(
            f"row {index}: {column} is not an ISO date: {value!r}"
        ) from error


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`; see `stock_universe._require_plain_date`."""
    if type(value) is not date:
        raise IndexMembershipError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
