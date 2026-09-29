"""Rows the upstream itself published wrong, and the named rule each one was handled by
(`V2-P6-013`).

## Why this module exists

The panel's write-time guards refuse a year on the first contradiction, and they are right to:
a partial or mismatched fetch looks exactly like a contradiction, and storing one is the failure
they exist to prevent. The 2013..2026 backfill then found contradictions that are **not** fetch
faults -- they are in Tushare's own data, they reproduce on a targeted re-fetch, and refusing
them refuses a whole year of every other security's data along with them. Measured live on
2026-09-26:

- `000022.SZ` on 2013-11-14 has a `daily_basic` row (close 13.75) and no `daily` bar, and a
  per-security range request for 2013-11-11..18 omits the bar too. It traded: 2013-11-15's
  `pre_close` is 13.75, `adj_factor` and `stk_limit` have rows for the 14th, and `suspend_d` has
  none.
- `002357.SZ` on 2013-07-15 closed at 6.8 in `daily` and 6.62 in `daily_basic`. The bar is
  corroborated by its own neighbour (2013-07-16's `pre_close` is 6.8, and `pct_chg` 2.72 is
  6.8/6.62 - 1), while the valuation row repeats 2013-07-12's close **and** 2013-07-12's
  `total_mv` exactly -- a stale valuation row carried forward a session.
- 2020-10-23 has ten securities whose two closes differ by a cent or a few (`600079.SH` 31.06
  against 31.05, `603268.SH` 16.21 against 16.17, `600898.SH` 5.97 against 5.98, ...). In every
  case checked the next session's `pre_close` equals the `daily` close and `pct_chg` matches it,
  while the `daily_basic` close equals neither the bar nor the previous close. So the stale
  shape above is one case of a wider one -- a valuation that contradicts a bar its own next
  session corroborates -- and the rule is stated at that width, with the stale case recorded.
- 2020-09-18 has ninety `daily_basic` rows with no bar: halted A shares (`000029.SZ`, an untimed
  `S` in `suspend_d`) and B shares (`200011.SZ` and others, with no `suspend_d` row and no bar
  all week). Measured live on 2026-09-28 (`V2-P6-017`), every one of them has a **null close**
  and every other field null but `volume_ratio` -- a placeholder, not a valuation, which the
  decoder refused whole (`invalid_response`) and stopped the 2020 backfill on. They are
  dropped with no halt requirement, and the kind says which evidence there is:
  `valuation_placeholder_on_halt` where `suspend_d` has the security halted all session,
  `valuation_placeholder_without_bar` where nothing explains the missing bar.
- `920476.BJ`, `920564.BJ`, `920425.BJ` and `920556.BJ` have `daily` rows on 2014-01-24 with a
  null `pre_close` and `pct_chg`. They are trading on another venue before these securities
  listed -- the stored registry's `list_date`s are 2022-10-14, 2022-06-17, 2023-01-30 and
  2023-03-17 -- back-mapped onto today's Beijing codes. A row before its security's listing is
  outside the listed A-share universe the panel models, null or not (`bar_before_listing`).
- `000509.SZ` on 2014-01-09 has a `stk_limit` row with `up_limit=0.0, down_limit=0.0`, is
  halted all session in `suspend_d`, and has no `daily` or `daily_basic` row. Zero/zero is how
  the upstream publishes "no band" for a halted security on that history.

## What a rule here is, and what it is not

Each `DefectKind` is a **named** rule with a precondition narrow enough that a fetch fault
cannot satisfy it by accident, and each one only ever **drops** the upstream's wrong row -- a
bar only under `bar_before_listing`, which is decided by the stored registry and not by the
row. Nothing here edits a bar or a valuation, fills a gap from another dataset or another
provider, or invents a value. A disagreement no rule names is still refused, by the same guard
as before.

## A row the upstream withdrew (`V2-P6-016`)

The rules above drop a row the upstream *serves* wrong. The live check of 2026-09-28 found the
other direction: `stk_limit` stopped serving three rows it had served for 2026-08-28 (funds
`158008.SZ`, `159096.SZ` and `561730.SH`, each with no other row in the stored year), and the
incremental build that fetched that session again was refused by the subject guard -- as a full
rebuild of the stored year was. `withdrawn_after_publication` is that case, and its precondition
is the three-part one `withdrawn_subjects` states: a stored row on a session the build fetched
again, absent from the upstream's answer, and absent again from a second whole-session answer that
is otherwise the first one. Nothing is dropped by the rule -- the upstream no longer serves the
row, so a rebuilt partition cannot hold it -- the rule only *records* that, with the stored row's
own `event_time`, `available_time` and `ingested_time` and its confirmation instant as the
record's `revision_time`, so the record is not knowable before the build that confirmed it.

## A `fina_indicator` version the upstream superseded (`V2-P6-018`)

`fina_indicator` carries no revision label: a correction re-publishes the same
`(ts_code, report_period)` under a later `ann_date` and stops serving the old version (measured
2026-09-28, `000909.SZ`'s 2026-03-31 report from 2026-04-25 to 2026-09-28).
`superseded_after_publication` records the stored old version, per `superseded_versions`, dated as
a withdrawal is: its own clocks, the confirmation instant as `revision_time`. One record per
`(ts_code, ann_date)` -- the key this record has -- with every superseded report kept whole in
`superseded_fina_indicator`.

Every dropped row is recorded in the `upstream_defects` panel dataset
(`panel_ingest.UPSTREAM_DEFECTS_DATASET`), one partition per year,
with the values that disagreed and the dropped row's own four clocks, so a reader can see
every row the source got wrong and when it was fetched.

Pure rules and a row decoder only: `panel_ingest` owns the batches, the re-fetch and the
partition, and this module imports no numerical or storage library (hard rule 2).
"""

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date
from math import isfinite
from types import MappingProxyType
from typing import Final, Literal, get_args

from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRICE_DATE_COLUMN,
    RecordedReturnPath,
    ReturnPath,
    corroborated_return_path,
)
from openalpha_cn.domain.financial_statements import FINANCIAL_INDICATOR_DATASET
from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET

DefectKind = Literal[
    "valuation_without_bar",
    "valuation_placeholder_on_halt",
    "valuation_placeholder_without_bar",
    "valuation_contradicts_corroborated_bar",
    "valuation_contradicts_unconfirmed_bar",
    "limit_placeholder_on_halt",
    "bar_before_listing",
    "withdrawn_after_publication",
    "superseded_after_publication",
    "pre_close_corroborated_over_adj_factor",
    "adj_factor_corroborated_over_pre_close",
    "pre_close_contradicts_adj_factor",
]
"""The named rules. See `close_disagreement_kind`, `valuation_placeholder_kind`,
`limit_placeholder_kind`, `withdrawn_subjects`, `superseded_versions` and `return_path_kind`."""

DEFECT_KINDS: Final[frozenset[str]] = frozenset(get_args(DefectKind))

DEFECT_SOURCE_DATASETS: Final[frozenset[str]] = frozenset(
    {
        DAILY_DATASET,
        DAILY_BASIC_DATASET,
        ADJ_FACTOR_DATASET,
        PRICE_LIMIT_DATASET,
        SUSPENSION_DATASET,
        FINANCIAL_INDICATOR_DATASET,
    }
)
"""The datasets a record may name. `daily` and `adj_factor` only under `bar_before_listing` or
`withdrawn_after_publication`, `suspend_d` only under the latter, and `fina_indicator` only under
`superseded_after_publication` (`V2-P6-018`): no rule drops a bar or a halt the upstream still
serves, and none drops a report whose current version is not stored in its place.

The three `RETURN_PATH_KINDS` (`V2-P6-020`) name `stk_limit`: nothing is dropped under them, the
record is the `stk_limit` target's judgement of a `daily`/`adj_factor` disagreement, and its
band is the evidence. `write_upstream_defects` hands each source to exactly one build target, so
naming `daily` or `adj_factor` would put the record in the hands of a target that cannot see the
band and would erase it on its next build."""

SOURCE_DATASET_COLUMN: Final[str] = "source_dataset"
DEFECT_KIND_COLUMN: Final[str] = "defect_kind"
BAR_CLOSE_COLUMN: Final[str] = "bar_close"
VALUATION_CLOSE_COLUMN: Final[str] = "valuation_close"
PREVIOUS_BAR_CLOSE_COLUMN: Final[str] = "previous_bar_close"
DEFECT_UP_LIMIT_COLUMN: Final[str] = "up_limit"
DEFECT_DOWN_LIMIT_COLUMN: Final[str] = "down_limit"
REPEATS_PREVIOUS_CLOSE_COLUMN: Final[str] = "valuation_repeats_previous_close"
LIST_DATE_COLUMN: Final[str] = "list_date"

UPSTREAM_DEFECT_DATA_COLUMNS: Final[tuple[str, ...]] = (
    PRICE_DATE_COLUMN,
    SOURCE_DATASET_COLUMN,
    DEFECT_KIND_COLUMN,
    BAR_CLOSE_COLUMN,
    VALUATION_CLOSE_COLUMN,
    PREVIOUS_BAR_CLOSE_COLUMN,
    DEFECT_UP_LIMIT_COLUMN,
    DEFECT_DOWN_LIMIT_COLUMN,
    REPEATS_PREVIOUS_CLOSE_COLUMN,
    LIST_DATE_COLUMN,
)
"""The stored columns after `subject`, in order.

The first three say *which* row was dropped and *by which rule*; the five numbers are the values
that disagreed, each `None` where the kind has nothing to say about it, and the last column keeps
the two shapes of a contradicted valuation apart:

- `valuation_without_bar`: `valuation_close` (there is no bar, so `bar_close` is `None`).
- `valuation_placeholder_on_halt` and `valuation_placeholder_without_bar`: nothing but the key
  -- there is no bar and the dropped row has no close, so every value column is `None`. Whether
  `suspend_d` explains the missing bar is carried by the kind rather than by a new column, which
  would make every stored partition of this dataset and the new build unreadable to each other
  (hard rule 3).
- `valuation_contradicts_corroborated_bar`: `bar_close`, `valuation_close`, `previous_bar_close`
  (the security's previous stored bar close, `None` on its first bar of the year), and
  `valuation_repeats_previous_close` -- `True` for the stale shape (`002357.SZ` on 2013-07-15),
  `False` for a valuation that equals neither close (2020-10-23), `None` when there is no
  previous bar to compare with.
- `valuation_contradicts_unconfirmed_bar`: the same four, for a mismatch on the build's last
  session, where no next stored session exists to corroborate the bar.
- `limit_placeholder_on_halt`: `up_limit` and `down_limit`, both `0.0`.
- `bar_before_listing`: `list_date` (ISO), from the stored registry, and the dropped row's own
  close (`bar_close` for `daily`, `valuation_close` for `daily_basic`) or band (`stk_limit`);
  an `adj_factor` row records only the date.
- `withdrawn_after_publication`: the withdrawn stored row's own close or band, in the same
  columns `bar_before_listing` uses; an `adj_factor` or `suspend_d` row records only the date.
- `pre_close_corroborated_over_adj_factor`, `adj_factor_corroborated_over_pre_close` and
  `pre_close_contradicts_adj_factor` (`V2-P6-020`): `bar_close` is the session's close and
  `previous_bar_close` the previous stored bar's close -- the two rows the disagreement was
  judged on, which a reader matches before following the record -- and `up_limit`/`down_limit`
  the band that decided it (`None` when none was published). The disputed `pre_close` and the
  two factors are the stored `daily` and `adj_factor` rows the record keys; the kind carries the
  decision rather than a new column, for `valuation_placeholder_*`'s reason (hard rule 3).
"""

UPSTREAM_DEFECT_NUMBER_COLUMNS: Final[tuple[str, ...]] = UPSTREAM_DEFECT_DATA_COLUMNS[3:8]

UPSTREAM_DEFECT_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *UPSTREAM_DEFECT_DATA_COLUMNS,
)
"""What a reader asks the store for, and the positional contract of the rows back."""


class UpstreamDefectError(ValueError):
    """Raised for a malformed stored defect row."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UpstreamDefect:
    """One row the upstream published wrong, which dataset it was in, and the rule that
    dropped it."""

    ts_code: str
    trade_date: date
    source_dataset: str
    kind: DefectKind
    bar_close: float | None = None
    valuation_close: float | None = None
    previous_bar_close: float | None = None
    up_limit: float | None = None
    down_limit: float | None = None
    valuation_repeats_previous_close: bool | None = None
    list_date: date | None = None

    def values(self) -> tuple[float | None, ...]:
        """The five numbers in `UPSTREAM_DEFECT_NUMBER_COLUMNS` order."""
        return (
            self.bar_close,
            self.valuation_close,
            self.previous_bar_close,
            self.up_limit,
            self.down_limit,
        )


def close_disagreement_kind(
    *,
    bar_close: float | None,
    next_bar_pre_close: float | None,
    is_last_session: bool,
) -> DefectKind | None:
    """The rule that explains one `daily`/`daily_basic` close disagreement, or `None`.

    Called only after a re-fetch has reproduced the disagreement exactly; a re-fetch that differs
    is a partial fetch and never reaches this function.

    - **`valuation_without_bar`**: there is no bar and there is a valuation. The re-fetch has
      already confirmed both halves, so the valuation row is the one to drop -- a bar is never
      invented to match it.
    - **`valuation_contradicts_corroborated_bar`**: there is a bar and the security's next stored
      bar's `pre_close` equals its close -- a witness other than the bar itself -- so the
      valuation's different close is the wrong one.
    - **`valuation_contradicts_unconfirmed_bar`**: there is a bar, it has no next stored bar, and
      this is the **last session the build requested**. Nothing can corroborate it yet, and it
      is not claimed corroborated: the valuation is dropped because the bar is what the price
      path trusts, the defect is recorded as unconfirmed, and the next build that holds the
      following session judges it again under the rule above. The bar's own `pct_chg` is **not**
      a witness -- every storable bar agrees with itself.

    Anything else returns `None` and the caller refuses: a bar whose next session disagrees with
    it, and a bar with no next stored bar before the build's last session, are shapes a fetch
    fault can produce.
    """
    if bar_close is None:
        return "valuation_without_bar"
    if next_bar_pre_close is not None:
        return "valuation_contradicts_corroborated_bar" if next_bar_pre_close == bar_close else None
    return "valuation_contradicts_unconfirmed_bar" if is_last_session else None


def valuation_placeholder_kind(*, has_bar: bool, halted: bool) -> DefectKind | None:
    """The rule for a `daily_basic` row with a null close (`V2-P6-017`), or `None` to refuse it.

    Called only after a re-fetch has published the same placeholder again; one that comes back
    with a real close is a partial fetch and never reaches this function. `halted` is the year's
    `suspend_d` answer for the session -- an untimed `S`, `SuspensionDay.is_halted` -- and the
    caller refuses rather than guess when there is no corpus to ask.

    The upstream publishes a row with every value null but `volume_ratio` for a security that did
    not trade -- 90 of 2020-09-18's 4,160 rows -- and the row carries no valuation to store, so
    with no `daily` bar on the session it is dropped either way. The two kinds keep the evidence
    apart, as `limit_placeholder_on_halt` does for a band:

    - **`valuation_placeholder_on_halt`**: `suspend_d` has the security halted all session, so
      the missing bar is explained (`000029.SZ`).
    - **`valuation_placeholder_without_bar`**: nothing in `suspend_d` explains it (`200011.SZ`
      and the other B shares, with no halt row and no bar all week).

    Beside a bar it returns `None`, halted or not, and the caller refuses: the bar says the
    security traded and the placeholder says nothing about the session it traded in. That shape
    has not been observed, and no rule is invented for it.
    """
    if has_bar:
        return None
    return "valuation_placeholder_on_halt" if halted else "valuation_placeholder_without_bar"


def repeats_previous_close(
    *, valuation_close: float, previous_bar_close: float | None
) -> bool | None:
    """Whether a contradicted valuation is the stale shape: it equals the previous bar close.

    `None` when there is no previous bar in the year to compare with. Recorded beside the defect
    so the stale shape (`002357.SZ`, 2013-07-15) and the off-by-a-cent shape (2020-10-23) stay
    distinguishable in the record; neither is required by the rule.
    """
    if previous_bar_close is None:
        return None
    return valuation_close == previous_bar_close


def limit_placeholder_kind(
    *, up_limit: float, down_limit: float, halted: bool
) -> DefectKind | None:
    """The rule for a `stk_limit` row with a zero upper limit, or `None` to refuse it.

    **`limit_placeholder_on_halt`**: both limits are exactly `0.0` **and** the year's `suspend_d`
    corpus has the security halted for the whole session (`SuspensionDay.is_halted`: an untimed
    `S`). That is the upstream's no-band placeholder, and the row is dropped rather than stored,
    because a band of zero/zero read as a band refuses every order on both sides.

    A zero/zero row on a session the security is not halted, and a row with a zero upper limit
    beside a non-zero lower one, return `None` and are refused by name. A zero *lower* limit
    beside a positive upper one is not a candidate at all -- it is the Beijing board's published
    "no lower bound" and is stored as it always was.
    """
    if up_limit == 0.0 and down_limit == 0.0 and halted:
        return "limit_placeholder_on_halt"
    return None


def withdrawn_subjects(
    *, stored: Set[str], first: Set[str], second: Set[str]
) -> frozenset[str] | None:
    """The stored securities of one `(dataset, session)` the upstream has withdrawn, or `None`.

    **`withdrawn_after_publication`** (`V2-P6-016`). `stored` are the securities the stored
    partition holds on a session this build fetched again, `first` the securities the build's
    fetch of that session served, and `second` those a second whole-session fetch served -- asked
    only because `stored` holds a security `first` lacks, so it is one extra request per affected
    `(dataset, session)` and none otherwise, `V2-P6-013`'s "a refetch must reproduce it" applied
    to an absence.

    A security counts as withdrawn only when both answers lack it **and the two answers are the
    same answer**: `second == first`. A second answer that differs from the first in any security
    -- serving the missing one again, or anything else -- is what a partial fetch looks like, so
    this returns `None` and the caller refuses, naming both answers, rather than choosing one.
    An empty set is "nothing stored is missing"; the caller does not ask for a second fetch then.
    """
    if set(second) != set(first):
        return None
    return frozenset(set(stored) - set(first))


def superseded_versions(
    *,
    lost: Set[tuple[str, str, date]],
    served: Set[tuple[str, str, date]],
) -> tuple[frozenset[tuple[str, str, date]], frozenset[tuple[str, str, date]]]:
    """Split the stored `fina_indicator` versions a build no longer holds into superseded and
    unexplained: `(superseded, nowhere)` (`V2-P6-018`).

    **`superseded_after_publication`.** `fina_indicator` carries no revision label, so the
    upstream corrects a report by re-publishing the same `(ts_code, report_period)` under a later
    `ann_date` and no longer serving the old version -- measured on 2026-09-28, `000909.SZ`'s
    2026-03-31 report moved from 2026-04-25 to 2026-09-28. So a stored version
    `(ts_code, report_period, ann_date)` that is in `lost` -- not served where it is stored -- is
    superseded exactly when `served` -- every version this build writes, in any announcement year
    -- holds the same `(ts_code, report_period)` under a **later** `ann_date`. That holds at the
    version, not the key: another version of the same report still stored beside it does not stop
    the lost one being superseded. A lost version with no later version served is `nowhere`, and
    the caller refuses it, as a shrink always was.
    """
    latest: dict[tuple[str, str], date] = {}
    for subject, period, announced in served:
        key = (subject, period)
        if key not in latest or announced > latest[key]:
            latest[key] = announced
    superseded = frozenset(
        version
        for version in lost
        if (version[0], version[1]) in latest and latest[(version[0], version[1])] > version[2]
    )
    return superseded, frozenset(lost) - superseded


RETURN_PATH_KINDS: Final[Mapping[str, ReturnPath | None]] = MappingProxyType(
    {
        "pre_close_corroborated_over_adj_factor": "published",
        "adj_factor_corroborated_over_pre_close": "adjusted",
        "pre_close_contradicts_adj_factor": None,
    }
)
"""The three `V2-P6-020` kinds and the session path each one decides: the published
`close / pre_close` path, the factor path, or neither -- the session's return is unknowable."""


def return_path_kind(
    *,
    published_pre_close: float,
    implied_pre_close: float,
    close: float,
    up_limit: float | None,
    down_limit: float | None,
) -> DefectKind:
    """The record for one session whose `daily.pre_close` and `adj_factor` disagree past
    `daily_prices.pre_close_tolerance` (`V2-P6-020`).

    Called only after a re-fetch has reproduced both statements. Which one the day's own price
    corroborates is `daily_prices.corroborated_return_path`'s rule -- the `stk_limit` band centred
    on it, with the session's close inside the band:

    - **`pre_close_corroborated_over_adj_factor`**: the published `pre_close` is the exchange's
      reference; the factor path is wrong for this session and the published return is the
      session's return. 17 of the research store's 24 residual disagreements, among them every
      one where the factor steps on a session whose `pre_close` equals the previous close
      (`000998.SZ` 11.267 -> 10.97 on 2020-01-02 and back, `000545.SZ`, `000011.SZ`,
      `603081.SH`).
    - **`adj_factor_corroborated_over_pre_close`**: the factor path's implied `pre_close` is the
      reference; the published one is wrong. Not observed in the store, and stated so that the
      rule is symmetric rather than a preference.
    - **`pre_close_contradicts_adj_factor`**: neither is corroborated -- no band, or a band the
      close lies outside. The seven halt-spanning disagreements of the store (`000010.SZ`
      2013-07-19, `000509.SZ`, `000670.SZ`, `600688.SH`, `600871.SH`, `600733.SH`, `600610.SH`
      2014-11-25) are all this: each resumed with a close outside the band centred on its own
      published `pre_close`. The session's return is unknowable.

    Always a kind, never `None`: a reproduced disagreement is recorded one way or another, and a
    reader refuses only the ones nobody recorded.
    """
    path = corroborated_return_path(
        published_pre_close=published_pre_close,
        implied_pre_close=implied_pre_close,
        close=close,
        up_limit=up_limit,
        down_limit=down_limit,
    )
    if path == "published":
        return "pre_close_corroborated_over_adj_factor"
    if path == "adjusted":
        return "adj_factor_corroborated_over_pre_close"
    return "pre_close_contradicts_adj_factor"


def recorded_return_paths(
    defects: Iterable[UpstreamDefect],
) -> dict[tuple[str, date], RecordedReturnPath]:
    """The `V2-P6-020` decisions among `defects`, keyed by `(ts_code, session)`, as the
    `daily_prices.RecordedReturnPath`s a reader hands `session_returns`.

    Every other kind is left out. A return-path record without the two closes it was judged on
    cannot be matched against the rows a reader holds, and two records for one session are two
    answers to one question; both are refused rather than read.
    """
    decided: dict[tuple[str, date], RecordedReturnPath] = {}
    for defect in defects:
        if defect.kind not in RETURN_PATH_KINDS:
            continue
        key = (defect.ts_code, defect.trade_date)
        if defect.bar_close is None or defect.previous_bar_close is None:
            raise UpstreamDefectError(
                f"{defect.ts_code} on {defect.trade_date.isoformat()}: a {defect.kind} record "
                f"needs {BAR_CLOSE_COLUMN} and {PREVIOUS_BAR_CLOSE_COLUMN}, the two closes it was "
                "judged on, and this one lacks one; a reader could not tell whether it is a "
                "decision about the rows it holds"
            )
        if key in decided:
            raise UpstreamDefectError(
                f"{defect.ts_code} on {defect.trade_date.isoformat()} carries two return-path "
                "records; one session has one decision"
            )
        decided[key] = RecordedReturnPath(
            ts_code=defect.ts_code,
            day=defect.trade_date,
            close=defect.bar_close,
            previous_close=defect.previous_bar_close,
            path=RETURN_PATH_KINDS[defect.kind],
        )
    return decided


def before_listing(*, trade_date: date, list_date: date | None) -> bool:
    """Whether a row dated `trade_date` precedes its security's listing (`bar_before_listing`).

    `list_date` comes from the stored `stock_basic` registry. A security the registry does not
    know (`None`) is **not** before its listing -- nothing says so -- and its rows go on to be
    stored or refused exactly as before. On or after `list_date` a row is inside the universe.
    """
    return list_date is not None and trade_date < list_date


def upstream_defects_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> tuple[UpstreamDefect, ...]:
    """Rebuild stored defect rows shaped like `UPSTREAM_DEFECT_PANEL_COLUMNS`, in row order."""
    defects: list[UpstreamDefect] = []
    for index, row in enumerate(rows):
        if len(row) != len(UPSTREAM_DEFECT_PANEL_COLUMNS):
            raise UpstreamDefectError(
                f"row {index} has {len(row)} values, expected "
                f"{len(UPSTREAM_DEFECT_PANEL_COLUMNS)} ({', '.join(UPSTREAM_DEFECT_PANEL_COLUMNS)})"
            )
        subject, day_text, source, kind, *numbers, repeats, listed_text = row
        if type(subject) is not str or not subject:
            raise UpstreamDefectError(f"row {index}: subject must be a non-empty string")
        if type(day_text) is not str:
            raise UpstreamDefectError(f"row {index}: {PRICE_DATE_COLUMN} must be an ISO date")
        try:
            trade_date = date.fromisoformat(day_text)
        except ValueError as error:
            raise UpstreamDefectError(
                f"row {index}: {PRICE_DATE_COLUMN} is not an ISO date: {day_text!r}"
            ) from error
        if source not in DEFECT_SOURCE_DATASETS:
            raise UpstreamDefectError(
                f"row {index}: {SOURCE_DATASET_COLUMN} {source!r} is not one of "
                f"{sorted(DEFECT_SOURCE_DATASETS)}"
            )
        if kind not in DEFECT_KINDS:
            raise UpstreamDefectError(
                f"row {index}: {DEFECT_KIND_COLUMN} {kind!r} is not one of {sorted(DEFECT_KINDS)}"
            )
        for name, value in zip(UPSTREAM_DEFECT_NUMBER_COLUMNS, numbers, strict=True):
            if value is not None and (type(value) is not float or not isfinite(value)):
                raise UpstreamDefectError(
                    f"row {index}: {name} must be a finite float or null, got "
                    f"{type(value).__name__} {value!r}"
                )
        if repeats is not None and type(repeats) is not bool:
            raise UpstreamDefectError(
                f"row {index}: {REPEATS_PREVIOUS_CLOSE_COLUMN} must be a boolean or null, got "
                f"{type(repeats).__name__} {repeats!r}"
            )
        listed: date | None = None
        if listed_text is not None:
            if type(listed_text) is not str:
                raise UpstreamDefectError(f"row {index}: {LIST_DATE_COLUMN} must be an ISO date")
            try:
                listed = date.fromisoformat(listed_text)
            except ValueError as error:
                raise UpstreamDefectError(
                    f"row {index}: {LIST_DATE_COLUMN} is not an ISO date: {listed_text!r}"
                ) from error
        bar_close, valuation_close, previous_bar_close, up_limit, down_limit = numbers
        defects.append(
            UpstreamDefect(
                ts_code=subject,
                trade_date=trade_date,
                source_dataset=str(source),
                kind=kind,  # type: ignore[arg-type]
                bar_close=bar_close,  # type: ignore[arg-type]
                valuation_close=valuation_close,  # type: ignore[arg-type]
                previous_bar_close=previous_bar_close,  # type: ignore[arg-type]
                up_limit=up_limit,  # type: ignore[arg-type]
                down_limit=down_limit,  # type: ignore[arg-type]
                valuation_repeats_previous_close=repeats,
                list_date=listed,
            )
        )
    return tuple(defects)
