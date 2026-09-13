"""Which industry a security belonged to on a day (`V2-P1-010`), and when that became sayable.

Tushare serves this as two endpoints. `index_classify` is the **tree** -- one row per industry
node, with `level`, `parent_code` and a `src` naming the taxonomy vintage. `index_member_all` is
the **membership** -- one row per `(security, assignment interval)`, carrying all three levels at
once plus an explicit `in_date`/`out_date`. A live probe on 2026-08-09 read both exhaustively and
every number in this module comes from it: **511 SW2021 tree nodes (31 L1 / 134 L2 / 346 L3),
359 SW2014 ones (28 / 104 / 227), and 7,893 membership rows over 5,889 securities.**

## The dimension this dataset cannot see, and the value that looks like it

`in_date` is the day an assignment took effect. It is **not** the day the assignment became
knowable, and treating it as one is this dataset's central hazard.

Every one of the 7,893 membership rows is labelled with a node of the **SW2021** tree -- verified
structurally: the 31 distinct `l1_code` values used are exactly SW2021's 31, and the SW2014-only
L1 `801020.SI` 采掘 appears on none of them. The corpus's earliest `in_date` is **1984-05-09**
(`000639.SZ`), six years before the Shanghai exchange opened, and 12 securities have a first
`in_date` before 1990-12-19. So the dataset states 1984 memberships in a classification published
in 2021, and carries no column that says so.

The vintage's birthday is derivable rather than assumed. Tushare's per-index `index_member`
endpoint -- which this module does **not** store, and which is cited only as the witness --
reports 535 constituent rows leaving on 2021-12-10 against 393 entering on 2021-12-13, and the
SW2014-only 采掘 loses 66 of its 97 members on exactly 2021-12-13. The same endpoint shows 912
rows entering on 2014-02-21, which is SW2014's own effective date. `index_member_all` shows
**neither**: exactly 1 of its 7,893 rows carries `in_date=20211213` and exactly 1 carries
`out_date=20211210`, and both belong to `301071.SZ` 力量钻石, an ordinary single-name move. The
revision is not merely backfilled here, it is **erased**.

**How far apart the two endpoints are is measured, under a rule stated so it can be re-run.**
Take the 7,571 SSE sessions from 1990-12-19 to 2021-12-10. `index_member_all` gives an answer for
**12,675,906** (name x session) pairs in that window. Ask Tushare's per-index `index_member` about
each of those pairs and count a pair only where it names **exactly one** L1 -- 2,561,228 pairs it
is silent on and 547,817 where it names two at once are unmeasurable, not agreement. Of the
**9,566,861** that remain, **1,038,252 disagree** -- 10.85% of the measured pairs, 8.19% of all
the answers this dataset gives in the window, over **1,039 distinct securities**. The largest
flows are 轻工制造 -> 纺织服饰 (37,591 name-sessions), 基础化工 -> 汽车 (27,783) and 电力设备 ->
机械设备 (21,693).

**That is a disagreement, and calling it a look-ahead measurement would overclaim.** The witness
is backfilled too: `index_member` gives 环保 -- an L1 SW2021 created -- 149 constituent rows of
which **100 begin before 2021-12-13**, the earliest on 1999-12-30, and the same is true of
石油石化 (47 of 54) and 美容护理 (28 of 34). Neither endpoint here can be asked what
classification was in force on a 2015 session. What is *structural* and needs no witness is the
first paragraph: the labels are SW2021's, and SW2021 did not exist.

So `IndustryAnswer` never hands back a bare industry code. It carries the taxonomy, the
taxonomy's effective date, and `is_backfilled` -- computed from the day asked about, not from the
dataset -- for the reason `IndexWeights` carries `as_published_on`: losing the caveat has to be an
act rather than an omission. `providers/tushare.py` makes the same statement on the clock, by
flooring every row's `available_time` at the taxonomy's effective date -- so a readiness check at
a pre-2021 `as_of` blocks with `not_yet_knowable` instead of answering. What that floor does
**not** do is hold a whole interval back until it closed; see `_industry_membership_panel_rows`
for the row split that separates the two ends, and
`KNOWN_INDUSTRY_LIMITATIONS.a_partial_year_read_cannot_see_an_interval_close` for what the split
costs.

## The interval is closed at both ends

`out_date` is the **last day the assignment held**, not the first day it did not. Measured:
1,955 of the 2,004 transitions have the successor's `in_date` on the very next SSE session after
`out_date`. Zero securities carry two assignments covering one day and every one of the 5,889 has
exactly one open assignment.

**The endpoints are nearly all sessions and the exceptions are named rather than generalised
away.** Every one of the 2,004 `out_date` values is an SSE session. 21 `in_date` values over 14
distinct days are not: 11 of those days precede 1990-12-19, where the calendar simply does not
reach, and three do not -- 2022-01-09 and 2022-07-17 are Sundays (3 and 4 Beijing-board rows) and
2026-06-19 is the 端午 holiday (`301583.SZ` and `001248.SZ`). Nothing here assumes an endpoint is
a session; `assignment_on` compares calendar days.

The other 49 transitions are real coverage holes, and they are refused rather than filled:
`000639.SZ` ST西王 is 社会服务 through 2002-08-29 and unclassified for **4,103 sessions** until
2019-07-24, `000716.SZ` 黑芝麻 for 3,428 and `600365.SH` ST通葡 for 3,299 -- sessions strictly
between the two dates, since the security is still classified on the `out_date` itself. Carrying
the older label across seventeen years would be an answer no row supports.

**Which 49 they are is not something a calendar-free rule can say.** `is_not_calendar_adjacent`
answers `True` for 442 of the 2,004; the 393 extra hand over on the next session across a weekend
or a public holiday. `IndustryReclassification.is_across_a_gap` is exact only when
`reclassifications(sessions=...)` is given the calendar, and says so.

## What the whole-market coverage actually is

Joined against `stock_basic`'s 5,878-row registry, on the day of the probe **5,538 of the 5,539
listed securities have a current assignment (99.98%)**. The one that does not is `920038.BJ`
森合高科, listed 2026-08-05 -- four days earlier. Coverage of a *historical* cross section is
materially worse, because the holes above are inside listed lives: 5,358 of 5,363 (99.91%) on
2024-06-28, 3,814 of 3,876 (98.40%) on 2020-06-30, 2,694 of 2,776 (**97.05%**) on 2015-06-30,
1,784 of 1,869 (95.45%) on 2010-06-30 and 913 of 973 (93.83%) on 2000-06-30. A neutralisation
that silently drops the unclassified residue drops 3% of the 2015 market.

## The tree is a report on the memberships, never a precondition

25 membership rows name `850412.SI` 特钢 as their `l3_code`, and that node is **not** among the
SW2021 tree's 346 -- `index_classify(index_code=850412.SI)` resolves it as an **SW2014** L3 whose
`parent_code` is 230100 and whose `is_pub` is null. So the membership rows mix vintages inside the
level columns, while their L1 and L2 stay SW2021's. In the other direction, 3 SW2021 L2 nodes and
9 L3 nodes are used by no membership row at all.

`IndustryTree.ancestry` therefore refuses a node it does not carry, and nothing in
`SecurityIndustryHistory` walks the tree: an assignment answers from its own three codes.
Requiring the join would discard 25 real rows over one upstream inconsistency, which is the
answer `constituent_listing_report` reached for the same shape in `domain/index_membership.py`.

## Layering

Pure `datetime`, `bisect`, `dataclasses` and `types`, plus two sibling `domain` modules
(`panel_batch` for the reserved subject-column name, `stock_universe` for the registry join) --
the same placement, and the same reason, as `domain/index_membership.py`.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME
from openalpha_cn.domain.stock_universe import StockUniverse, StockUniverseError

INDUSTRY_TREE_DATASET: Final[str] = "index_classify"
INDUSTRY_MEMBERSHIP_DATASET: Final[str] = "index_member_all"
"""The two panel datasets (and partition directories) this contract reads back.

Declared here rather than in `providers/tushare.py` for the reason `INDEX_WEIGHT_DATASET` is:
`panel_ingest` reads the rows back and is pinned to importing `domain` and `panel` only.
"""

SW2014_TAXONOMY: Final[str] = "SW2014"
SW2021_TAXONOMY: Final[str] = "SW2021"

INDUSTRY_TAXONOMY_EFFECTIVE_FROM: Final[Mapping[str, date]] = MappingProxyType(
    {SW2014_TAXONOMY: date(2014, 2, 21), SW2021_TAXONOMY: date(2021, 12, 13)}
)
"""When each measured vintage came into force -- the birthday `is_backfilled` is read against.

Both dates are derived from Tushare's per-index `index_member` endpoint rather than looked up:
912 constituent rows enter on 2014-02-21, and 535 leave on 2021-12-10 against 393 entering on
2021-12-13 with the SW2014-only L1 `801020.SI` 采掘 losing 66 of its 97 members on that exact
day. A third `src` value would need its own measurement, and `_require_dated_taxonomy` refuses
one that has none rather than defaulting it -- an undated vintage makes `is_backfilled`
unanswerable, and answering `False` would be the silent version of this module's whole subject.
"""

INDUSTRY_MEMBERSHIP_TAXONOMY: Final[str] = SW2021_TAXONOMY
"""The one vintage `index_member_all` speaks, measured rather than configured.

The endpoint takes no `src` -- `src='SW2021'` and `src='SW2014'` both return **zero** rows -- and
every one of its 7,893 rows uses one of SW2021's 31 L1 codes, with the SW2014-only `801020.SI`
采掘 on none of them. So the membership corpus has exactly one taxonomy by construction, which is
why the vintage is a parameter of the reader rather than a stored column.
"""

INDUSTRY_LEVELS: Final[tuple[str, ...]] = ("L1", "L2", "L3")
INDUSTRY_ROOT_PARENT: Final[str] = "0"
"""What an L1 node's `parent_code` is. Measured: all 31 SW2021 L1 rows carry exactly `'0'`."""

SW2021_L1_COUNT: Final[int] = 31
SW2014_L1_COUNT: Final[int] = 28
"""How many level-one industries each vintage has, measured against the live endpoint.

`index_classify(src='SW2021')` returns 511 rows -- 31 L1, 134 L2, 346 L3 -- and
`src='SW2014'` returns 359 -- 28 / 104 / 227. The bare request (and `src=''`, and any `level`
without a `src`) answers **SW2014**, which is why `providers/tushare.py` refuses a request that
does not name one: the endpoint's default vintage is not the one `index_member_all` labels its
rows with.
"""

INDUSTRY_FROM_COLUMN: Final[str] = "industry_from"
"""What the response calls `in_date`, renamed on the way into the panel.

Renamed for `INDEX_PUBLICATION_DATE_COLUMN`'s reason, and then some: `in_date` reads as "the
date this became true", and the one thing this module exists to say is that it is not the date
this became knowable.
"""
INDUSTRY_THROUGH_COLUMN: Final[str] = "industry_through"
"""What the response calls `out_date`. Inclusive -- the last day the assignment held."""
INDUSTRY_L1_COLUMN: Final[str] = "l1_code"
INDUSTRY_L2_COLUMN: Final[str] = "l2_code"
INDUSTRY_L3_COLUMN: Final[str] = "l3_code"

INDUSTRY_MEMBERSHIP_DATA_COLUMNS: Final[tuple[str, ...]] = (
    INDUSTRY_FROM_COLUMN,
    INDUSTRY_THROUGH_COLUMN,
    INDUSTRY_L1_COLUMN,
    INDUSTRY_L2_COLUMN,
    INDUSTRY_L3_COLUMN,
)
"""The columns a provider projects, in order. `subject` is added by the batch itself.

Neither `l1_name` nor `name` is here. The industry names belong to the *tree* and would be a
second, drifting copy of it -- and the security name is `domain/name_history.py`'s answer from a
dataset that has an announcement clock, which is exactly why `stock_basic`'s `name` is not
projected either. `is_new` is not here because it is derivable: all 5,889 `Y` rows have an empty
`out_date` and all 2,004 `N` rows have one, with no exceptions, so it is fetched as an
independent witness and cross-checked rather than stored -- the treatment `namechange` gives
`end_date`.
"""

INDUSTRY_MEMBERSHIP_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *INDUSTRY_MEMBERSHIP_DATA_COLUMNS,
)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back.

The subject is the **security**, not the industry: a read groups by security to answer "what was
this one's industry on day D", the same shape `namechange` stores. `panel_ingest`'s subject guard
then protects against a partial re-fetch dropping securities from a year, which is the failure a
per-`l1_code` backfill loop can produce.

**No clock column is here, and `V2-P4-034` deliberately did not add one.** That defect's fix needs
every visible row's `event_time` to reconcile a filtered read against its partition's date census,
and the reader takes it by *prepending* the clock to this tuple at its own call site and stripping
it off the rows again -- `panel_ingest._read_visible_membership_rows`, the idiom
`panel_factors.load_factor_observations` already uses on the same method. Widening this tuple was
the other option and was rejected on what it moves: `industry_histories_from_panel_rows` checks a
row's width against it and then unpacks positionally, so every consumer of the row shape shifts to
give one reader a column that is a property of *that read* rather than of what the domain decodes.
"""

INDUSTRY_CODE_COLUMN: Final[str] = "industry_code"
INDUSTRY_NAME_COLUMN: Final[str] = "industry_name"
INDUSTRY_LEVEL_COLUMN: Final[str] = "level"
INDUSTRY_PARENT_COLUMN: Final[str] = "parent_code"
INDUSTRY_PUBLISHED_COLUMN: Final[str] = "is_published"
"""What the response calls `is_pub`. Nullable, and the null is real: all 227 SW2014 L3 rows
carry `None` while SW2021's carry `'1'` or `'0'` (10 of 134 L2 and 87 of 346 L3 are `'0'`)."""
INDUSTRY_TAXONOMY_COLUMN: Final[str] = "taxonomy"
INDUSTRY_TAXONOMY_DATE_COLUMN: Final[str] = "taxonomy_date"
"""The vintage's effective date, synthesised by the provider -- `index_classify` has no date
column of any kind, and a tree row's one honest instant is the day its taxonomy came into
force."""

INDUSTRY_TREE_DATA_COLUMNS: Final[tuple[str, ...]] = (
    INDUSTRY_CODE_COLUMN,
    INDUSTRY_NAME_COLUMN,
    INDUSTRY_LEVEL_COLUMN,
    INDUSTRY_PARENT_COLUMN,
    INDUSTRY_PUBLISHED_COLUMN,
    INDUSTRY_TAXONOMY_COLUMN,
    INDUSTRY_TAXONOMY_DATE_COLUMN,
)
INDUSTRY_TREE_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *INDUSTRY_TREE_DATA_COLUMNS,
)
"""The subject is the node's `index_code` (`801010.SI`), which is what a membership row's
`l1_code`/`l2_code`/`l3_code` join against. `industry_code` (`110000`) is the *other* identifier
the same row carries, and it is what `parent_code` points at -- so both are stored, because
dropping either breaks one of the two joins."""


class IndustryClassificationError(ValueError):
    """Raised for any malformed industry tree or history, or malformed query against one.

    A `ValueError` subclass to match `domain/index_membership.py`'s `IndexMembershipError`.
    """


class IndustryHorizonError(IndustryClassificationError):
    """Raised when a day's answer lies outside what the read actually covers.

    Separate from its parent for `IndexMembershipHorizonError`'s reason: the question was well
    formed and the data simply does not reach that day -- either before the security's first
    assignment or inside one of the 49 measured coverage holes. `V2-P1-013`'s gate blocks on it;
    a malformed query is a bug.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryLimitation:
    """One named boundary on what a stored industry classification can be trusted to answer."""

    code: str
    detail: str


KNOWN_INDUSTRY_LIMITATIONS: Final[tuple[IndustryLimitation, ...]] = (
    IndustryLimitation(
        code="every_pre_2021_answer_is_a_backfill",
        detail=(
            "index_member_all expresses the entire history in the SW2021 taxonomy, which came "
            "into force 2021-12-13. All 31 distinct l1_code values it uses are SW2021's, the "
            "SW2014-only L1 801020.SI 采掘 appears on none of its 7,893 rows, and its earliest "
            "in_date is 1984-05-09 -- six years before the Shanghai exchange opened, with 12 "
            "securities starting before 1990-12-19. How far that is from the other endpoint is "
            "measured under a stated rule, over the 7,571 SSE sessions from 1990-12-19 to "
            "2021-12-10: index_member_all answers 12,675,906 (name x session) pairs in that "
            "window; Tushare's per-index index_member names exactly one L1 for 9,566,861 of them "
            "(it is silent on 2,561,228 and names two at once on 547,817, and neither of those "
            "is counted either way); of the 9,566,861 comparable pairs, 1,038,252 disagree -- "
            "10.85% of them, 8.19% of all the answers, over 1,039 distinct securities, the "
            "largest flows being 轻工制造 -> 纺织服饰 (37,591), 基础化工 -> 汽车 (27,783) and "
            "电力设备 -> 机械设备 (21,693). That is a disagreement between two endpoints and not "
            "a measurement against the classification in force: index_member is backfilled too, "
            "giving the SW2021-created L1 环保 149 rows of which 100 start before 2021-12-13 "
            "(earliest 1999-12-30), 石油石化 47 of 54 and 美容护理 28 of 34. The structural "
            "claim needs no witness. IndustryAnswer.is_backfilled reports it per answer and "
            "providers/tushare.py floors availability at the vintage's effective date so a "
            "pre-2021 readiness check blocks rather than answering."
        ),
    ),
    IndustryLimitation(
        code="the_taxonomy_revision_itself_is_erased",
        detail=(
            "The dataset does not merely relabel the past, it removes the event. Exactly 1 of "
            "its 7,893 rows carries in_date=20211213 and exactly 1 carries out_date=20211210, "
            "and both are 301071.SZ 力量钻石, an ordinary single-name move -- against 393 and "
            "535 respectively in index_member. So no reader can detect the 2021 revision from "
            "these rows, and the largest transitions visible here are the annual constituent "
            "reviews instead (613 assignments start 2021-07-30, 255 on 2022-07-29, 124 on "
            "2017-06-29). INDUSTRY_TAXONOMY_EFFECTIVE_FROM is therefore a measured constant "
            "rather than something this dataset could supply."
        ),
    ),
    IndustryLimitation(
        code="the_first_in_date_is_not_a_classification_event",
        detail=(
            "For 4,325 of the 5,875 securities the registry also carries, the first in_date "
            "precedes the security's own stock_basic list_date -- 600844.SH is classified from "
            "1993-10-08 and listed 1994-03-11 -- and 12 precede any exchange existing at all. "
            "1,307 coincide with the list_date exactly and 243 fall after it, the extreme being "
            "600841.SH 动力新科, listed 1994-03-11 with no assignment until 2022-07-29, 6,903 "
            "sessions later. So the first in_date is neither a listing date nor an event this "
            "dataset explains, and it must not be read as one."
        ),
    ),
    IndustryLimitation(
        code="a_security_can_be_unclassified_inside_its_listed_life",
        detail=(
            "49 of the corpus's 2,004 transitions leave a gap rather than handing straight over: "
            "000639.SZ ST西王 is 社会服务 through 2002-08-29 and unclassified for 4,103 sessions "
            "until 2019-07-24, 000716.SZ 黑芝麻 for 3,428 and 600365.SH ST通葡 for 3,299 -- "
            "sessions strictly between the two dates, the out_date itself still being classified. "
            "Which transitions those 49 are needs a calendar: is_not_calendar_adjacent flags 442 "
            "of the 2,004 and the 393 extra merely cross a weekend or a holiday. "
            "SecurityIndustryHistory refuses such a day rather than carrying the older label "
            "forward. Whole-market coverage of the listed universe is 5,538 of 5,539 (99.98%) on "
            "2026-08-07, the one exception being 920038.BJ 森合高科 listed four days earlier -- "
            "but only 2,694 of 2,776 (97.05%) on 2015-06-30, 1,784 of 1,869 (95.45%) on "
            "2010-06-30 and 913 of 973 (93.83%) on 2000-06-30. industry_coverage_report names "
            "the residue for a given day rather than leaving it to be inferred."
        ),
    ),
    IndustryLimitation(
        code="a_membership_can_name_a_node_the_tree_does_not_carry",
        detail=(
            "25 membership rows give l3_code=850412.SI 特钢, which is absent from the SW2021 "
            "tree's 346 L3 nodes; index_classify(index_code=850412.SI) resolves it as an SW2014 "
            "L3 with parent_code 230100 and a null is_pub, while those same rows' l1_code and "
            "l2_code are SW2021's. In the other direction 3 SW2021 L2 nodes and 9 L3 nodes are "
            "named by no membership row. IndustryTree.ancestry refuses an unknown node and "
            "nothing in SecurityIndustryHistory consults the tree, so those 25 rows still answer "
            "from their own three codes -- discarding them over one upstream inconsistency would "
            "be the worse answer, which is what constituent_listing_report concluded for the "
            "same shape."
        ),
    ),
    IndustryLimitation(
        code="the_default_response_hides_the_history",
        detail=(
            "A bare index_member_all request returns only the currently-effective assignments: "
            "both of its offset pages (3,000 then 2,889 rows) are is_new='Y' with an empty "
            "out_date, and is_new='' and is_new='Y,N' behave identically. The 2,004 superseded "
            "rows exist and are reachable only by asking for is_new='N' explicitly. A fetch that "
            "omits the parameter therefore stores a current snapshot that looks exactly like a "
            "complete history -- 5,889 rows, one per security, no truncation flag -- which is "
            "why providers/tushare.py makes the flag a required part of the request rather than "
            "a default."
        ),
    ),
    IndustryLimitation(
        code="no_announcement_and_no_revision_history",
        detail=(
            "Neither endpoint carries an announcement column, so within a vintage's own era the "
            "availability of an assignment is dated at in_date's midnight -- the same bound, and "
            "the same residue, as stock_basic's lifecycle rows: the annual review is published "
            "before it takes effect and how far before is not derivable here. Neither carries a "
            "revision instant either, so revision_time equals available_time and a partition's "
            "revised_row_count of 0 reads as 'unmeasured', not 'none'."
        ),
    ),
    IndustryLimitation(
        code="a_partial_year_read_cannot_see_an_interval_close",
        detail=(
            "One assignment is stored as two rows -- an opening row dated at in_date and, where "
            "the interval closed, a closing row dated at out_date -- so that neither carries a "
            "fact from an instant the other did not have. The two land in different partitions "
            "whenever the interval crosses a year, which is the whole point (it is what lets an "
            "as_of in 2022 read a 1991 assignment at all) and is also the cost: a read that "
            "names the opening year and not the closing one reassembles an interval that never "
            "ends, and answers with an industry the security had already left. That direction is "
            "fail-open, unlike everything else here, so it is closed by a rule rather than a "
            "note: load_industry_histories compares the years it was asked for against the "
            "years the store actually holds and sets SecurityIndustryHistory.answerable_through "
            "to the year before the first stored partition it skipped, after which "
            "assignment_on and is_classified_on refuse rather than answer. A read that "
            "covers every stored year gets no bound, correctly. What no rule here can see "
            "is a year that was never ingested at all: 1985 and 1987 have no partition "
            "because nothing happened in them, which is indistinguishable from a year whose "
            "fetch was skipped."
        ),
    ),
    IndustryLimitation(
        code="no_cross_section_before_the_taxonomy_is_readable_at_all",
        detail=(
            "The floor under every row's available_time is 2021-12-13, so the earliest as_of at "
            "which any of this can be read is 2021-12-13 -- not a filter that thins a 2015 cross "
            "section, a refusal of the whole read. Measured on the stored corpus: at as_of "
            "2015-06-30 every partition blocks with not_yet_knowable. That is the correct answer "
            "and it is also a hard limit on what V2-P3-004 can neutralise against; a backtest "
            "that wants an industry for a 2015 session needs a source that published one in "
            "2015, which this is not. What the row split buys is the SW2021 era itself: before "
            "it, one closed interval held its own opening year past every earlier as_of, and "
            "fed the real 7,893-row corpus 29 of the 38 requestable years blocked at as_of "
            "2023-06-30 with the 9 that read holding 118 securities between them."
        ),
    ),
    IndustryLimitation(
        code="silent_truncation_at_the_response_cap",
        detail=(
            "index_member_all serves at most 3,000 rows per response, the lowest cap of any "
            "endpoint wired here, and drops rows past it with has_more=True. limit only narrows "
            "-- limit=100 returns 100, limit=3001/5000/10000 all return 3,000 -- so 3,000 is the "
            "server's own ceiling. Slicing by l1_code keeps every request far under it (the "
            "largest is 801890.SI 机械设备 at 625 current plus 229 superseded), and the "
            "superseded half is the one to watch: it grew from 439 rows at the end of 2015 to "
            "801 at the end of 2020 to 2,004 today, 587 of them added by the 2021 revision "
            "alone. index_classify's cap is unmeasurable from outside, exactly as stock_basic's "
            "is: the 511-row SW2021 tree returns has_more=False, limit=100000 also returns 511 "
            "with has_more=False, and limit=511 returns 511 with has_more=True."
        ),
    ),
)
"""Named boundaries on what a stored industry classification answers, each measured on real data.

**Not an enumeration of every way the classification could be wrong.** These are the ones a live
probe of both endpoints could demonstrate on 2026-08-09, over the SW2021 and SW2014 vintages and
the window 1984-05-09..2026-07-31.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryNode:
    """One node of one taxonomy vintage.

    A plain carrier with no validation of its own, following `CalendarDay`'s precedent: a nominal
    type is not a boundary, so the rules live once, in `build_industry_tree`.

    `index_code` (`801010.SI`) is what a membership row joins against; `industry_code`
    (`110000`) is what `parent_code` points at. They are two identifiers for one node and both
    are needed, because dropping either breaks one of the two joins.
    """

    index_code: str
    industry_code: str
    industry_name: str
    level: str
    parent_code: str
    taxonomy: str
    is_published: bool | None
    """Whether an index is published for this node; `None` where the vintage does not say."""


@dataclass(frozen=True, slots=True)
class IndustryTree:
    """One vintage's nodes, with the parent chain resolved.

    Build it with `build_industry_tree`; this constructor is not a boundary and validates
    nothing. `nodes` is ascending by `index_code`.
    """

    taxonomy: str
    effective_from: date
    nodes: tuple[IndustryNode, ...]

    @property
    def level_one_count(self) -> int:
        """How many L1 industries this vintage has; 31 for SW2021 and 28 for SW2014."""
        return len(self.nodes_at("L1"))

    def nodes_at(self, level: str) -> tuple[IndustryNode, ...]:
        """Every node at one level, ascending by `index_code`."""
        return tuple(node for node in self.nodes if node.level == level)

    def node(self, index_code: str) -> IndustryNode:
        """The node with this `index_code`, or `IndustryClassificationError`.

        An absent node is refused rather than answered, because "this vintage does not have this
        industry" and "this industry has no members" are different facts -- and the first one is
        real here: 25 membership rows name an L3 node the SW2021 tree does not carry.
        """
        for node in self.nodes:
            if node.index_code == index_code:
                return node
        raise IndustryClassificationError(
            f"{index_code} is not in the {self.taxonomy} tree; an absent node is not an industry "
            "with no members, and this vintage's own membership rows name at least one node it "
            "does not carry"
        )

    def ancestry(self, index_code: str) -> tuple[IndustryNode, ...]:
        """The node and its ancestors, leaf first, up to the L1 industry.

        Never returns a partial chain: `build_industry_tree` has already refused a tree in which
        a parent is missing, so every node this can reach walks all the way to a root.
        """
        node = self.node(index_code)
        chain = [node]
        while chain[-1].parent_code != INDUSTRY_ROOT_PARENT:
            chain.append(self._by_industry_code(chain[-1].parent_code))
        return tuple(chain)

    def _by_industry_code(self, industry_code: str) -> IndustryNode:
        for node in self.nodes:
            if node.industry_code == industry_code:
                return node
        raise IndustryClassificationError(
            f"the {self.taxonomy} tree has no node with industry_code {industry_code!r}"
        )


def build_industry_tree(*, taxonomy: str, nodes: Iterable[IndustryNode]) -> IndustryTree:
    """Assemble an `IndustryTree` from nodes, in any order.

    Refuses, rather than repairs, seven things: a taxonomy with no measured effective date, a
    malformed identifier, an unknown `level`, a node belonging to another vintage, a duplicated
    `index_code` or `industry_code`, an L1 whose `parent_code` is not the root, and a non-L1
    whose parent is absent from this tree.

    The parent rule is the load-bearing one: a broken chain is exactly what a partial read of the
    tree partition looks like, and `ancestry` would otherwise answer a leaf's L1 by walking into
    a `KeyError` several layers away from the row that caused it.
    """
    _require_text(taxonomy, "taxonomy")
    effective_from = _require_dated_taxonomy(taxonomy)
    by_index: dict[str, IndustryNode] = {}
    by_industry: dict[str, IndustryNode] = {}
    for node in nodes:
        _require_text(node.index_code, "index_code")
        _require_text(node.industry_code, "industry_code")
        _require_text(node.industry_name, "industry_name")
        _require_text(node.parent_code, "parent_code")
        if node.level not in INDUSTRY_LEVELS:
            raise IndustryClassificationError(
                f"{node.index_code}'s level must be one of {list(INDUSTRY_LEVELS)}; got "
                f"{node.level!r}"
            )
        if node.taxonomy != taxonomy:
            raise IndustryClassificationError(
                f"a {taxonomy} tree carries a {node.taxonomy} node ({node.index_code}); the "
                "endpoint's own default vintage is SW2014 while every index_member_all row is "
                "SW2021, so a mixed tree is the mistake a bare request makes"
            )
        if node.index_code in by_index:
            raise IndustryClassificationError(
                f"{node.index_code} appears more than once in the {taxonomy} tree; a node has "
                "one definition, and two rows for it are two sources that were never reconciled"
            )
        if node.industry_code in by_industry:
            raise IndustryClassificationError(
                f"industry_code {node.industry_code} is claimed by both "
                f"{by_industry[node.industry_code].index_code} and {node.index_code} in the "
                f"{taxonomy} tree; parent_code points at industry_code, so a collision makes the "
                "parent chain ambiguous"
            )
        if node.level == "L1" and node.parent_code != INDUSTRY_ROOT_PARENT:
            raise IndustryClassificationError(
                f"L1 node {node.index_code} names parent {node.parent_code!r}; every one of the "
                f"{SW2021_L1_COUNT} measured SW2021 L1 rows carries {INDUSTRY_ROOT_PARENT!r}, "
                "and a rooted L1 would make ancestry() walk past the top of the tree"
            )
        by_index[node.index_code] = node
        by_industry[node.industry_code] = node
    if not by_index:
        raise IndustryClassificationError(
            f"an industry tree needs at least one node; the {taxonomy} tree has none, and an "
            "empty tree refuses every question, which is indistinguishable from a failed read"
        )
    for node in by_index.values():
        if node.parent_code != INDUSTRY_ROOT_PARENT and node.parent_code not in by_industry:
            raise IndustryClassificationError(
                f"{node.index_code} names parent {node.parent_code}, which is not in the "
                f"{taxonomy} tree; a broken chain is what a partial read of the tree partition "
                "looks like, and ancestry() would answer the wrong L1 or none at all"
            )
    return IndustryTree(taxonomy, effective_from, tuple(by_index[key] for key in sorted(by_index)))


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryAssignment:
    """One security's membership of one industry over one closed interval.

    A plain carrier with no validation of its own; the rules live in
    `build_security_industry_history`.

    `effective_through` is **inclusive** -- the last day this assignment held -- and `None` means
    it still holds. See this module's docstring for the measurement.
    """

    ts_code: str
    l1_code: str
    l2_code: str
    l3_code: str
    effective_from: date
    effective_through: date | None = None

    @property
    def is_current(self) -> bool:
        """Whether this assignment is the one still in force."""
        return self.effective_through is None

    def covers(self, day: date) -> bool:
        """Whether this assignment was in force on `day`, both endpoints inclusive."""
        if day < self.effective_from:
            return False
        return self.effective_through is None or day <= self.effective_through


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryAnswer:
    """One security's industry on one day, with the vintage that says so.

    There is deliberately no `SecurityIndustryHistory.l1_on(day) -> str`. That is not a claim
    that a bare code is unreachable -- `industry_on(day).l1_code` is one line and is meant to be.
    What is refused is the **signature**, for the reason `IndexMembership` refuses
    `weight_of(code, day)`: a callable taking a day and returning a code is the one a caller
    stores in a variable and reads six months later as "the industry on 2015-06-30", by which
    point the fact that SW2021 did not exist in 2015 has been lost.
    """

    ts_code: str
    asked_for: date
    assignment: IndustryAssignment
    taxonomy: str
    taxonomy_effective_from: date

    @property
    def l1_code(self) -> str:
        return self.assignment.l1_code

    @property
    def l2_code(self) -> str:
        return self.assignment.l2_code

    @property
    def l3_code(self) -> str:
        return self.assignment.l3_code

    @property
    def is_backfilled(self) -> bool:
        """Whether the day asked about predates the taxonomy that labels the answer.

        The boundary is inclusive at the taxonomy's own effective date: 2021-12-13 is the first
        day SW2021 existed, so an answer for it is not a backfill.
        """
        return self.asked_for < self.taxonomy_effective_from

    @property
    def backfilled_by_days(self) -> int:
        """How many calendar days before its taxonomy this answer reaches; `0` when it does not."""
        if not self.is_backfilled:
            return 0
        return (self.taxonomy_effective_from - self.asked_for).days


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryReclassification:
    """What changed when one assignment handed over to the next."""

    ts_code: str
    effective_from: date
    previous: IndustryAssignment
    current: IndustryAssignment
    unclassified_sessions: int | None = None
    """Sessions on which the security carried no industry, or `None` when no calendar was given.

    `SecurityIndustryHistory.reclassifications(sessions=...)` fills it by counting the sessions
    strictly between the predecessor's `effective_through` and the successor's `effective_from`.
    `None` is not zero and must not be read as one: it means the caller passed no calendar, which
    is the only state in which `is_across_a_gap` falls back to a calendar-day guess.
    """

    @property
    def changed_levels(self) -> tuple[str, ...]:
        """Which of L1/L2/L3 moved, coarsest first.

        A move inside one L1 and a move between two are different facts to a caller neutralising
        on L1, and a bare "it changed" cannot tell them apart.
        """
        changed = []
        if self.previous.l1_code != self.current.l1_code:
            changed.append("L1")
        if self.previous.l2_code != self.current.l2_code:
            changed.append("L2")
        if self.previous.l3_code != self.current.l3_code:
            changed.append("L3")
        return tuple(changed)

    @property
    def is_not_calendar_adjacent(self) -> bool:
        """Whether the two dates are not consecutive calendar days. **A superset of the gaps.**

        Named for what it measures rather than for what a reader wants, because on the real
        corpus the two are an order of magnitude apart. Of the 2,004 transitions this answers
        `True` for **442**, while only **49** leave the security genuinely unclassified: the
        other **393** hand over on the next *session* across a weekend or a holiday, and their
        calendar deltas are exactly the market's -- 237 of three days (Friday to Monday), 48 of
        ten (a National Day or Spring Festival week), 47 of four, 32 of five, 14 of two.
        `('600354.SH', 2007-06-29, 2007-07-02)` and `('300268.SZ', 2011-09-30, 2011-10-10)` are
        two of them; neither has a single unclassified session in it.

        That the two counts were once claimed to be the same number was a coincidence of size,
        not a measurement. This property is the honest calendar-free statement and it is only
        ever a **bound**: a `False` proves there was no gap, a `True` proves nothing.
        """
        through = self.previous.effective_through
        if through is None:
            return False
        return (self.current.effective_from - through).days > 1

    @property
    def is_across_a_gap(self) -> bool:
        """Whether the security was unclassified between the two assignments.

        Exact when `reclassifications(sessions=...)` was given a calendar: `True` for exactly the
        **49** of the corpus's 2,004 transitions that leave at least one session with no
        assignment covering it, and `False` for the 1,955 whose successor starts on the very next
        session -- including the 393 that cross a weekend or a holiday to get there.

        **Without a calendar this falls back to `is_not_calendar_adjacent`, which is a superset**
        (442 of 2,004) and says so. The fallback is a report rather than a gate: the rule that
        actually refuses to answer for an unclassified day is `assignment_on`, which tests
        coverage directly and needs no calendar to be exact.
        """
        if self.unclassified_sessions is not None:
            return self.unclassified_sessions > 0
        return self.is_not_calendar_adjacent


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityIndustryHistory:
    """One security's assignments, ascending by `effective_from`.

    Build it with `build_security_industry_history`; this constructor is not a boundary and
    validates nothing.
    """

    ts_code: str
    assignments: tuple[IndustryAssignment, ...]
    taxonomy: str
    taxonomy_effective_from: date
    answerable_through: int | None = None
    """The last calendar year this history can answer for, or `None` for "no bound established".

    Load-bearing for a reason specific to how the rows are stored: `providers/tushare.py` files
    an assignment's opening and its close as two rows in two partitions, so a read that stopped
    before the closing year reassembles an interval that never ends and would answer with an
    industry the security had already left. `load_industry_histories` sets this to the year
    before the first stored partition it did **not** read, and `assignment_on` refuses a later
    day rather than answering from a stale open interval -- see
    `KNOWN_INDUSTRY_LIMITATIONS.a_partial_year_read_cannot_see_an_interval_close`.

    `None` when the read covered every stored year, and when the history was built directly from
    `IndustryAssignment` values rather than from partitions -- the same "there is no window to
    check" that `StockUniverse.years_read` being empty means.
    """

    @property
    def covered_from(self) -> date:
        """The first day this history can answer for."""
        return self.assignments[0].effective_from

    @property
    def covered_through(self) -> date | None:
        """The last day it can answer for; `None` when the final assignment is still open."""
        return self.assignments[-1].effective_through

    def assignment_on(self, day: date) -> IndustryAssignment:
        """The assignment in force on `day`, or `IndustryHorizonError`.

        Refuses three days rather than answering: one before the first assignment, one after a
        closed final assignment, and one inside a gap. The gap case is the one that matters --
        carrying the previous label across `000639.SZ`'s 4,103 unclassified sessions would be an
        answer no row supports and would be indistinguishable from a security that never left.

        A fourth day is refused when the history was read back from partitions: one after the
        last year that read covered. An assignment's close is stored as its own row in its own
        year, so an unread later year cannot be told apart from an interval that is still open.
        """
        _require_plain_date(day, "day")
        self._require_year_was_read(day)
        if day < self.covered_from:
            raise IndustryHorizonError(
                f"{day.isoformat()} is before {self.ts_code}'s first assignment, which takes "
                f"effect {self.covered_from.isoformat()}; an unclassified day is unknown rather "
                "than equal to the earliest industry on file"
            )
        position = bisect_right([entry.effective_from for entry in self.assignments], day)
        candidate = self.assignments[position - 1]
        if candidate.covers(day):
            return candidate
        through = candidate.effective_through
        # `covers` was False on the assignment with the greatest start at or before `day`, so
        # `effective_through` cannot be None here -- an open assignment covers every later day.
        assert through is not None
        if candidate is self.assignments[-1]:
            raise IndustryHorizonError(
                f"{day.isoformat()} is after {self.ts_code}'s last assignment, which ended "
                f"{through.isoformat()}; this read never saw an assignment covering that day, "
                "and carrying the last one forward would assert the security never left"
            )
        raise IndustryHorizonError(
            f"{day.isoformat()} falls in a gap between {through.isoformat()} and "
            f"{self.assignments[position].effective_from.isoformat()} in {self.ts_code}'s "
            "history; the security carried no industry then, and filling the gap forward would "
            "be an answer no row supports"
        )

    def industry_on(self, day: date) -> IndustryAnswer:
        """The industry in force on `day`, with the vintage and the backfill distance."""
        return IndustryAnswer(
            ts_code=self.ts_code,
            asked_for=day,
            assignment=self.assignment_on(day),
            taxonomy=self.taxonomy,
            taxonomy_effective_from=self.taxonomy_effective_from,
        )

    def is_classified_on(self, day: date) -> bool:
        """Whether any assignment covers `day`. The question `assignment_on` refuses to answer.

        Offered because "no industry that day" is a legitimate thing for a caller assembling a
        cross section to ask, and forcing it through an exception would make the ordinary
        3% residue of a 2015 cross section look like an error.

        It still raises for a day past the last year read, and that is not the same hazard: an
        unclassified day is a fact this read saw, while a day whose partition was never opened is
        one it did not -- and there the `False` and the `True` are both guesses.
        """
        _require_plain_date(day, "day")
        self._require_year_was_read(day)
        return any(entry.covers(day) for entry in self.assignments)

    def _require_year_was_read(self, day: date) -> None:
        """Refuse a day past the last year this read can speak for."""
        if self.answerable_through is not None and day.year > self.answerable_through:
            raise IndustryHorizonError(
                f"{day.isoformat()} is after {self.answerable_through}, the last membership year "
                f"{self.ts_code}'s history can answer for; a stored year before it was left "
                "unread, and an assignment's close is stored as its own row in its own year, so "
                "an interval that ended there is indistinguishable here from one still open"
            )

    def reclassifications(
        self, *, sessions: Sequence[date] = ()
    ) -> tuple[IndustryReclassification, ...]:
        """Every hand-over between consecutive assignments, ascending.

        1,645 of the corpus's 5,889 securities have at least one and the most any has is five
        (`000007.SZ`, six assignments).

        `sessions` is the exchange calendar, ascending, and it is what makes
        `IndustryReclassification.is_across_a_gap` exact rather than a calendar-day superset --
        442 transitions look like gaps by the calendar and only 49 are. It is a parameter rather
        than state on this class because a calendar belongs to `domain/trading_calendar.py` and
        this contract answers coverage questions without one; passing it buys precision on a
        *report*, never on a refusal.
        """
        ordered = tuple(sessions)
        return tuple(
            IndustryReclassification(
                ts_code=self.ts_code,
                effective_from=self.assignments[index].effective_from,
                previous=self.assignments[index - 1],
                current=self.assignments[index],
                unclassified_sessions=_unclassified_sessions(
                    ordered, self.assignments[index - 1], self.assignments[index]
                ),
            )
            for index in range(1, len(self.assignments))
        )


def _unclassified_sessions(
    sessions: tuple[date, ...], previous: IndustryAssignment, current: IndustryAssignment
) -> int | None:
    """Sessions strictly between two assignments, or `None` when no calendar was supplied.

    `None` rather than `0` for the missing calendar, and the type is the point: `0` would be
    "measured, and there was no gap", which is the one thing an absent calendar cannot say.
    """
    if not sessions:
        return None
    through = previous.effective_through
    if through is None:
        return 0
    return max(0, bisect_left(sessions, current.effective_from) - bisect_right(sessions, through))


def build_security_industry_history(
    ts_code: str,
    assignments: Iterable[IndustryAssignment],
    *,
    taxonomy: str,
    answerable_through: int | None = None,
) -> SecurityIndustryHistory:
    """Assemble a `SecurityIndustryHistory` from assignments, in any order.

    Refuses, rather than repairs, seven things: a taxonomy with no measured effective date, a
    malformed `ts_code`, an empty history, an assignment belonging to another security, a
    malformed or backwards interval, two assignments starting on one day, two assignments with no
    end date, and two assignments covering one day.

    The overlap and single-open rules are measured rather than assumed: across the 5,889
    securities in the corpus, zero carry two assignments covering one day and every one has
    exactly one open assignment. So either shape here is two sources that were never reconciled,
    and the wrong one would be picked silently by `bisect`.
    """
    _require_text(ts_code, "ts_code")
    effective_from = _require_dated_taxonomy(taxonomy)
    by_start: dict[date, IndustryAssignment] = {}
    for entry in assignments:
        if entry.ts_code != ts_code:
            raise IndustryClassificationError(
                f"an industry history for {ts_code} carries {entry.ts_code}; one history covers "
                "one security, and mixing two would answer the wrong one's industry"
            )
        _require_text(entry.l1_code, "l1_code")
        _require_text(entry.l2_code, "l2_code")
        _require_text(entry.l3_code, "l3_code")
        _require_plain_date(entry.effective_from, "effective_from")
        if entry.effective_through is not None:
            _require_plain_date(entry.effective_through, "effective_through")
            if entry.effective_through < entry.effective_from:
                raise IndustryClassificationError(
                    f"{ts_code}'s assignment ends {entry.effective_through.isoformat()}, before "
                    f"it starts on {entry.effective_from.isoformat()}; industry_through is the "
                    "last day the assignment held, so the two being reversed describes an "
                    "interval with no days in it"
                )
        if entry.effective_from in by_start:
            raise IndustryClassificationError(
                f"{ts_code} has two assignments starting "
                f"{entry.effective_from.isoformat()}; a security has one industry at a time, so "
                "this is two sources that were never reconciled"
            )
        by_start[entry.effective_from] = entry
    if not by_start:
        raise IndustryClassificationError(
            f"an industry history needs at least one assignment; {ts_code} has none, and an "
            "empty history refuses every question, which is indistinguishable from a failed read"
        )
    ordered = tuple(by_start[key] for key in sorted(by_start))
    open_ended = [entry for entry in ordered if entry.effective_through is None]
    if len(open_ended) > 1:
        raise IndustryClassificationError(
            f"{ts_code} has two assignments with no end date "
            f"({open_ended[0].effective_from.isoformat()} and "
            f"{open_ended[1].effective_from.isoformat()}); every one of the 5,889 securities "
            "measured has exactly one, and two would both answer every later day"
        )
    for previous, current in pairwise(ordered):
        through = previous.effective_through
        if through is None or current.effective_from <= through:
            raise IndustryClassificationError(
                f"{ts_code}'s assignment starting {previous.effective_from.isoformat()} overlaps "
                f"the one starting {current.effective_from.isoformat()}; no security in the "
                "corpus is in two industries on one day, so an overlap is two sources and "
                "bisect would silently pick one of them"
            )
    return SecurityIndustryHistory(
        ts_code=ts_code,
        assignments=ordered,
        taxonomy=taxonomy,
        taxonomy_effective_from=effective_from,
        answerable_through=answerable_through,
    )


def industry_histories_from_panel_rows(
    rows: Iterable[Sequence[object]], *, taxonomy: str, answerable_through: int | None = None
) -> Mapping[str, SecurityIndustryHistory]:
    """Rebuild one history per security from rows shaped like
    `INDUSTRY_MEMBERSHIP_PANEL_COLUMNS`.

    The counterpart of the provider's projection. What this function owns is the shape of a
    *stored row* -- its width, the ISO text its two date columns are stored as, and the fact that
    one assignment is stored as **two rows** -- none of which `build_security_industry_history`
    sees.

    ## Folding the split back together

    `providers/tushare.py` writes a closed assignment as an opening row (its `industry_through`
    empty, dated at `industry_from`) and a closing row (its `industry_through` populated, dated
    there), so that neither carries a fact from an instant the other did not have. A read that
    covers both years gets both, and they are the same assignment: same security, same start,
    same three codes. This folds them, preferring the closed one, because a close is later
    knowledge than the open it supersedes.

    Two rows that share a security and a start day and disagree about anything **else** are not a
    split -- they are two sources that were never reconciled -- and are refused here rather than
    collapsed, which is `build_security_industry_history`'s rule kept intact one layer up.

    `taxonomy` is a parameter rather than a stored column, and that is deliberate: the vintage is
    a property of the *request* (`index_member_all` takes no `src` -- passing one returns zero
    rows), so storing it per membership row would be storing the same constant 7,893 times and
    inviting a partition that carries two. It is stored on the *tree* rows, where it genuinely
    varies.

    `answerable_through` is the last year the read can speak for, and it is what lets
    `assignment_on` refuse a day whose closing rows may simply not have been read. `None`
    means no bound was established -- either the read covered every stored year or the rows
    did not come from partitions at all.

    A read-only mapping rather than a `dict`, because a whole-partition read is shared and a
    caller mutating one entry would be editing what other callers hold.
    """
    grouped: dict[str, dict[date, IndustryAssignment]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(INDUSTRY_MEMBERSHIP_PANEL_COLUMNS):
            raise IndustryClassificationError(
                f"row {index} has {len(row)} values, expected "
                f"{len(INDUSTRY_MEMBERSHIP_PANEL_COLUMNS)} "
                f"({', '.join(INDUSTRY_MEMBERSHIP_PANEL_COLUMNS)})"
            )
        subject, starts, ends, level_one, level_two, level_three = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        assignment = IndustryAssignment(
            ts_code=ts_code,
            l1_code=_require_stored_text(level_one, index, INDUSTRY_L1_COLUMN),
            l2_code=_require_stored_text(level_two, index, INDUSTRY_L2_COLUMN),
            l3_code=_require_stored_text(level_three, index, INDUSTRY_L3_COLUMN),
            effective_from=_parse_iso_date(starts, index, INDUSTRY_FROM_COLUMN),
            effective_through=(
                None if ends is None else _parse_iso_date(ends, index, INDUSTRY_THROUGH_COLUMN)
            ),
        )
        by_start = grouped.setdefault(ts_code, {})
        stored = by_start.get(assignment.effective_from)
        by_start[assignment.effective_from] = (
            assignment if stored is None else _fold_split_rows(stored, assignment, index)
        )
    return MappingProxyType(
        {
            ts_code: build_security_industry_history(
                ts_code,
                [by_start[start] for start in sorted(by_start)],
                taxonomy=taxonomy,
                answerable_through=answerable_through,
            )
            for ts_code, by_start in grouped.items()
        }
    )


def _fold_split_rows(
    stored: IndustryAssignment, arriving: IndustryAssignment, index: int
) -> IndustryAssignment:
    """Fold an assignment's opening row and its closing row back into one, or refuse.

    The two halves agree on everything but `effective_through`, and the populated one wins: the
    close is what the opening row could not yet say. Anything else two rows can disagree about on
    one start day is two sources.
    """
    if (stored.l1_code, stored.l2_code, stored.l3_code) != (
        arriving.l1_code,
        arriving.l2_code,
        arriving.l3_code,
    ):
        raise IndustryClassificationError(
            f"row {index}: {stored.ts_code} has two assignments starting "
            f"{stored.effective_from.isoformat()} with different industries "
            f"({stored.l1_code}/{stored.l2_code}/{stored.l3_code} and "
            f"{arriving.l1_code}/{arriving.l2_code}/{arriving.l3_code}); an assignment is stored "
            "as an opening row and a closing row that agree on everything but the end date, so "
            "this is two sources that were never reconciled"
        )
    if stored.effective_through is None:
        return arriving
    if arriving.effective_through is None or arriving.effective_through == stored.effective_through:
        return stored
    raise IndustryClassificationError(
        f"row {index}: {stored.ts_code}'s assignment starting "
        f"{stored.effective_from.isoformat()} is closed twice, on "
        f"{stored.effective_through.isoformat()} and "
        f"{arriving.effective_through.isoformat()}; one assignment ends once, and picking either "
        "date would answer a different set of days than the other"
    )


def industry_trees_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> Mapping[str, IndustryTree]:
    """Rebuild one tree per vintage from rows shaped like `INDUSTRY_TREE_PANEL_COLUMNS`.

    Keyed by taxonomy because one partition legitimately holds more than one: the two vintages'
    trees are two requests and there is no reason a caller could not store both.

    The stored `taxonomy_date` is checked against `INDUSTRY_TAXONOMY_EFFECTIVE_FROM` rather than
    read from it. It is a witness in the sense `namechange`'s `end_date` is one: if the two
    disagree, the partition was written under a different rule than the one reading it, and that
    is a finding rather than something to silently prefer one side of.
    """
    grouped: dict[str, list[IndustryNode]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(INDUSTRY_TREE_PANEL_COLUMNS):
            raise IndustryClassificationError(
                f"row {index} has {len(row)} values, expected "
                f"{len(INDUSTRY_TREE_PANEL_COLUMNS)} "
                f"({', '.join(INDUSTRY_TREE_PANEL_COLUMNS)})"
            )
        subject, code, name, level, parent, published, taxonomy, taxonomy_date = row
        vintage = _require_stored_text(taxonomy, index, INDUSTRY_TAXONOMY_COLUMN)
        stored = _parse_iso_date(taxonomy_date, index, INDUSTRY_TAXONOMY_DATE_COLUMN)
        expected = _require_dated_taxonomy(vintage)
        if stored != expected:
            raise IndustryClassificationError(
                f"row {index}: stored {INDUSTRY_TAXONOMY_DATE_COLUMN} {stored.isoformat()} "
                f"disagrees with {vintage}'s measured effective date {expected.isoformat()}; the "
                "stored column is a witness, so a disagreement means the partition was written "
                "under a different rule than the one reading it"
            )
        if published is not None and type(published) is not bool:
            raise IndustryClassificationError(
                f"row {index}: {INDUSTRY_PUBLISHED_COLUMN} must be a bool or None, got "
                f"{type(published).__name__} {published!r}"
            )
        grouped.setdefault(vintage, []).append(
            IndustryNode(
                index_code=_require_stored_text(subject, index, SUBJECT_COLUMN_NAME),
                industry_code=_require_stored_text(code, index, INDUSTRY_CODE_COLUMN),
                industry_name=_require_stored_text(name, index, INDUSTRY_NAME_COLUMN),
                level=_require_stored_text(level, index, INDUSTRY_LEVEL_COLUMN),
                parent_code=_require_stored_text(parent, index, INDUSTRY_PARENT_COLUMN),
                taxonomy=vintage,
                is_published=published,
            )
        )
    return MappingProxyType(
        {
            vintage: build_industry_tree(taxonomy=vintage, nodes=group)
            for vintage, group in grouped.items()
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryCoverageReport:
    """How much of one day's listed universe this classification can actually answer for.

    Two separate answers rather than one ratio, because they are two different facts and only the
    second is an inconsistency between two datasets that both claim to know:

    - `unclassified`: the registry says the security was listed and no assignment covers the day.
    - `unknown_to_registry`: this classification carries a code `stock_basic` has never had. 14
      of the corpus's 5,889 do, including the two withdrawn IPOs `000991.SZ` 通海高科 and
      `002525.SZ` 胜景山河 that `KNOWN_UNIVERSE_LIMITATIONS` already names, and `T00018.SH`,
      which is the registry's `T600018.SH` under a third spelling.

    There is deliberately **no** `beyond_snapshot` bucket, unlike `ConstituentListingReport`'s.
    That report iterates a publication's constituents and asks the registry about each, so it
    meets the snapshot horizon one code at a time. This one starts from `universe.listed_on(day)`,
    which refuses a day past the snapshot outright -- so "beyond the snapshot" is a refusal of the
    whole question rather than a verdict about some of its codes, and a field for it would never
    hold anything.
    """

    day: date
    classified: tuple[str, ...]
    unclassified: tuple[str, ...]
    unknown_to_registry: tuple[str, ...]

    @property
    def listed_count(self) -> int:
        """How many securities the registry reports as listed on the day."""
        return len(self.classified) + len(self.unclassified)

    @property
    def classified_ratio(self) -> float | None:
        """The share of the listed universe that has an industry, or `None` if nothing was listed.

        A ratio and not a verdict: a caller that needs a threshold states it, because what counts
        as enough depends on what the residue is being dropped from -- see
        `KNOWN_INDUSTRY_LIMITATIONS.a_security_can_be_unclassified_inside_its_listed_life` for
        the 93.83%..99.98% range this has actually taken.

        `None` rather than `0.0` or `1.0` for an empty universe, and the type is the point.
        Either number would be a lie a threshold check reads silently -- `0.0` fails a day on
        which nothing could have been missing, `1.0` passes a failed read -- while `None` makes
        `ratio >= 0.95` a type error the caller has to answer. This is `ListingStatus.__bool__`'s
        response to the same hazard: a value that has no truth is not given a plausible one.
        """
        if not self.listed_count:
            return None
        return len(self.classified) / self.listed_count

    @property
    def is_complete(self) -> bool:
        """Whether every listed security had an industry and no code was unknown.

        Vacuously `True` for a day on which nothing was listed. A caller that needs to tell that
        apart from a covered market reads `listed_count`, which is also what `classified_ratio`
        returns `None` for.
        """
        return not (self.unclassified or self.unknown_to_registry)


def industry_coverage_report(
    universe: StockUniverse,
    histories: Mapping[str, SecurityIndustryHistory],
    *,
    day: date,
) -> IndustryCoverageReport:
    """Ask a `StockUniverse` which of `day`'s listed securities this classification covers.

    The `V2-P1-010`-to-`V2-P1-005` join, and it **reports** rather than refuses, for
    `constituent_listing_report`'s reason: the residue is real and permanent (60 to 85 names on
    every historical cross section measured), so a rule that refused the day would refuse every
    day.

    A security in one of the 49 coverage holes counts as `unclassified` even though it appears in
    the corpus elsewhere, because a neutralisation on that day has no industry for it -- "the
    corpus knows this code" and "the corpus answers for this code today" are different facts and
    only the second one is usable.

    A day past the registry snapshot raises `UniverseHorizonError` from `universe.listed_on`
    rather than being reported: without a listed universe there is nothing to take coverage of,
    and answering it with the codes this corpus happens to carry would report a classification's
    own membership as though it were the market's.
    """
    _require_plain_date(day, "day")
    classified: list[str] = []
    unclassified: list[str] = []
    for ts_code in universe.listed_on(day):
        history = histories.get(ts_code)
        if history is not None and history.is_classified_on(day):
            classified.append(ts_code)
        else:
            unclassified.append(ts_code)
    unknown: list[str] = []
    for ts_code in sorted(histories):
        try:
            universe.security(ts_code)
        except StockUniverseError:
            unknown.append(ts_code)
    return IndustryCoverageReport(
        day=day,
        classified=tuple(sorted(classified)),
        unclassified=tuple(sorted(unclassified)),
        unknown_to_registry=tuple(unknown),
    )


def _require_dated_taxonomy(taxonomy: str) -> date:
    """The vintage's effective date, or a refusal naming it.

    A vintage with no measured birthday cannot answer `is_backfilled`, and defaulting it to
    "not backfilled" would be the silent version of the defect this module exists to name.
    """
    effective_from = INDUSTRY_TAXONOMY_EFFECTIVE_FROM.get(taxonomy)
    if effective_from is None:
        raise IndustryClassificationError(
            f"taxonomy {taxonomy!r} has no effective date in "
            f"{sorted(INDUSTRY_TAXONOMY_EFFECTIVE_FROM)}; an undated vintage cannot say whether "
            "an answer predates it, and treating it as always-current would hide exactly the "
            "look-ahead this contract exists to report"
        )
    return effective_from


def _require_text(value: str, role: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise IndustryClassificationError(
            f"{role} must be a non-empty string without surrounding whitespace; got {value!r}"
        )


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise IndustryClassificationError(
            f"row {index}: {column} must be a non-empty string without surrounding whitespace, "
            f"got {type(value).__name__} {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise IndustryClassificationError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise IndustryClassificationError(
            f"row {index}: {column} is not an ISO date: {value!r}"
        ) from error


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`; see `stock_universe._require_plain_date`."""
    if type(value) is not date:
        raise IndustryClassificationError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
