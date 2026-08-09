"""What a security was called on a day, and whether it was under special treatment
(`V2-P1-005`).

## Two clocks, and why merging them is the whole risk

Tushare's `namechange` carries both `ann_date` (when a rename was announced) and `start_date`
(when it took effect), and they genuinely separate: `000001.SZ` was announced 平安银行 on
2012-01-20 and became it on 2012-08-02, seven months later, while its 1991 record has the two
on the same day. A live probe of the full corpus -- 14,166 rows assembled from 37
announcement-year windows -- found 6,150 rows where the two coincide and **zero** rows where
the announcement post-dates the effect.

A contract with one clock has to pick, and either pick is wrong somewhere. Keying the name on
`ann_date` renames the security seven months early, which is a plain look-ahead. Keying only
on `start_date` gets the name right but makes the announcement invisible, so a point-in-time
reader cannot tell "nothing is happening" from "a rename has been announced and has not landed
yet" -- and on 2012-01-20 that distinction was public information. So both survive:
`available_time` is the announcement, `event_time` is the effect, `name_on()` answers from the
effect, `pending_at()` answers from the announcement, and `announced_by()` is the
point-in-time filter that bounds what a reader at a given instant could have had.

Nothing here *requires* `announced_on <= effective_from`. The ordering is measured, not
depended on: a late announcement would simply mean the reader does not see the rename until it
is announced, which is what `announced_by()` already does.

## `end_date` is a witness, and is deliberately not stored

`namechange`'s `end_date` is `None` on the record currently in effect -- 5,882 of the corpus's
rows -- and that is a real fact, not a missing value. (An earlier draft of this line said
5,891. That number came from the single paginated pull this module elsewhere declares unsound:
its 14,166 raw rows contained 380 exact duplicates, and 5,891 counts some records twice.
Deduplicated it is 5,766 of 13,786; over the 37 announcement-year windows this module actually
uses -- 14,166 mutually distinct rows -- it is 5,882.) It is still not written to the panel,
because it is *derivable from the successor's `start_date`* and, unlike the successor, it is
not gated by any announcement: the record in effect today carries the start of a rename that
may not have been announced yet, so storing it would put tomorrow's news on today's row. The
column is kept as an independent witness instead and cross-checked in
`tests/unit/domain/test_name_history.py` -- the same treatment `trading_calendar.py` gives
`pretrade_date`, and for the same reason: a witness can disagree, and a disagreement is a
finding.

One disagreement is already recorded. For a **delisted** security the final record never
closes: `000003.SZ` was terminated on 2002-06-14 and its last `namechange` record is still
open-ended, so this contract answers `PT金田A` for 2026 while the registry calls the same
security `PT金田A(退)`. That is not reconciled here. This module knows nothing about
delisting; `domain/stock_universe.py` is the other half of the answer, and composing them is
the caller's job rather than a hidden coupling.

## The special-treatment grammar, and the two mistakes it exists to prevent

"Is it ST" is "what was it called that day", and the obvious `name.startswith(("ST", "*ST"))`
is wrong on real history in two independent ways.

The first is the share reform. 2005-2007 put a `G` (reform complete) or `S` (reform pending)
marker **in front of** the ST marker, producing `GST星源`, `SST华新`, `G*ST国农`, `S*ST兰宝`.
The naive rule marks 2,592 of the corpus's rows; adding the reform-aware strip takes it to
2,827, so **235 rows** carry a genuinely special-treated name that the naive rule reads as
ordinary. The naive rule has zero false positives, which is precisely why the gap survives
review.

The second is ChiNext, and it survived this module's own review. Before 2020 创业板 ran no ST
regime at all: 退市风险警示 was worn as a **bare `*` prefix** with nothing behind it. Exactly
two rows in the corpus are in that form, and they are the only rows out of 14,166 whose name
begins with `*` and is not `*ST`:

    ('300372.SZ', '*欣泰', start=20160712, end=20160822, ann=20160708, '高风险警示')
    ('300028.SZ', '*金亚', start=20180627, end=20180807, ann=20180626, '高风险警示')

each closed by its own `撤销高风险警示` row (`欣泰电气` 20160823, `金亚科技` 20180808). Both
securities were terminated afterwards (`欣泰退` 2017-08-28, `金亚退` 2020-08-03), so the two
windows this grammar used to answer `RiskWarning.none` for were the two windows a downstream
ST filter most needed to see. The test is therefore on the marker `*`, not on the literal
`*ST`, which takes the strict rule to 2,829 rows.

The reverse mistake is just as available, and the number that used to sit here described a
different rule than the sentence around it. `S深发展A` and `G上港` carry the same reform
markers with no ST behind them, so a rule that read a leading `S`/`G` as a warning marker in
its own right would mismark the **1,210** rows whose name starts with `S`/`G` and has no `ST`
behind it. The narrower error -- strip the letter unconditionally, *then* look for `ST` --
costs something else: it marks 1,708 rows with zero false positives but silently *loses*
`ST天龙`, which becomes `T天龙`. The rule avoids both by stripping the marker only when `ST`
or `*ST` follows it.

`change_reason` is not used to derive the state, and that is deliberate. It labels a
*transition*, and a transition can land on another marked state -- 16 rows carry `撤销PT` and
**8** of them produce a name that is still special-treated (`ST吉轻工`, `ST振新`, `ST琼华侨`,
`ST东海A`, `ST闽闽东`, `ST永久`, `ST凯地`, `ST红光`). It is kept as the independent witness
instead, and the witness is what found the ChiNext gap above. What that cross-check now says
is bounded rather than absolute: across **this** 14,166-row corpus and **its** fifteen distinct
reason values, the eight that impose special treatment -- `ST`, `*ST`, `PT`, `高风险警示`,
`从ST变为*ST`, `撤消*ST并实行ST`, `叠加ST`, `叠加*ST` -- land on zero names this grammar calls
ordinary. That is a statement about a corpus, re-checkable against a re-pull, not a property
of the grammar: a sixteenth reason value, or a warning form neither `ST` nor `*` encodes,
would read as ordinary here and the witness is what would say so.

Delisting-period naming is classified rather than swept into `none`. 153 rows carry
`change_reason="退市整理期"`; 92 of their names end in `退` and the other 61 begin with `退市`,
and **no row with any other reason** matches either shape. Returning `none` for `国华退` would
be a wrong answer a downstream would trust, not a gap.

## Layering

Pure `datetime`, `bisect` and `dataclasses`, plus `domain/panel_batch.py` for the reserved
subject-column name. No provider, no store, no clock -- the same placement, and the same
reason, as `domain/trading_calendar.py`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Final

from openalpha_cn.domain.panel_batch import SUBJECT_COLUMN_NAME

NAMECHANGE_DATASET: Final[str] = "namechange"
"""The panel dataset (and partition directory) the rename corpus is stored under."""

NAME_COLUMN: Final[str] = "name"
NAME_EFFECTIVE_COLUMN: Final[str] = "effective_date"
NAME_ANNOUNCEMENT_COLUMN: Final[str] = "announcement_date"
NAME_REASON_COLUMN: Final[str] = "change_reason"

NAME_HISTORY_DATA_COLUMNS: Final[tuple[str, ...]] = (
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_REASON_COLUMN,
)
"""The columns a provider projects, in order. `subject` is added by the batch itself.

`effective_date` and `announcement_date` duplicate the `event_time` and `available_time`
clock columns as plain ISO text, exactly as the calendar stores `cal_date` beside its own
`event_time`. Keeping both clocks visible *as data* is what makes "the two are not merged" a
readable property of a stored partition rather than a claim about this module.
"""

NAME_HISTORY_PANEL_COLUMNS: Final[tuple[str, ...]] = (
    SUBJECT_COLUMN_NAME,
    *NAME_HISTORY_DATA_COLUMNS,
)
"""What a reader asks `PanelStore.query` for, and the positional contract of the rows back."""

_REFORM_MARKERS: Final[tuple[str, ...]] = ("S", "G")
_RISK_WARNING_MARKER: Final[str] = "*"
"""The 退市风险警示 marker. `*ST` on the main board, a bare `*` on pre-2020 ChiNext."""
_DELISTING_SUFFIX: Final[str] = "退"
_DELISTING_PREFIX: Final[str] = "退市"


class NameHistoryError(ValueError):
    """Raised for any malformed name history or malformed query against one."""


class NameHistoryHorizonError(NameHistoryError):
    """Raised when a question's answer would lie before anything this history knows.

    Separate from its parent because it is not a caller mistake: the question was well formed
    and the corpus simply does not reach that far back, or the announcement filter removed
    every record. `V2-P1-013`'s gate blocks on it; a malformed query is a bug.
    """


class RiskWarning(Enum):
    """The exchange risk-warning state a name encodes. Deliberately not a `bool`.

    `__bool__` raises for every member, including `none`. Leaving `none` usable as a falsy
    value and the rest truthy would make `if warning:` read correctly for four members and
    silently merge `delisting_process` with `st` -- a landmine rather than a guardrail, and
    the same answer `CalendarDayStatus` and `ListingStatus` give.
    """

    none = "none"
    st = "st"
    star_st = "star_st"
    """退市风险警示 -- the delisting-risk warning, however the board spells it.

    Named after the main board's `*ST` marker, but it is the *state* that is the member, not
    the string: ChiNext wore the same warning as a bare `*` prefix before 2020 (`*欣泰`,
    `*金亚`) and lands here too. Adding a sixth member for that form would assert the two are
    different warnings, which they are not -- an ST filter wants them merged, and every
    downstream `is RiskWarning.star_st` would have had to grow a second arm to stay correct.
    """

    pt = "pt"
    delisting_process = "delisting_process"

    def __bool__(self) -> bool:
        raise NameHistoryError(
            f"{type(self).__name__}.{self.name} is a five-valued verdict and has no truth "
            "value; compare it against the member you mean (`is RiskWarning.none`) rather "
            "than collapsing four distinct warning states into one truthy branch"
        )


def risk_warning_of(name: str) -> RiskWarning:
    """The risk-warning state a security name encodes. See this module's docstring.

    Order matters and is measured: the delisting-period forms are tested first, and no row in
    the 14,166-row corpus carries both a delisting marker and an ST/`*`/PT prefix, so the
    precedence is unobservable in real data rather than a judgement about which wins.
    """
    if type(name) is not str or not name:
        raise NameHistoryError(f"a security name must be a non-empty string; got {name!r}")
    if name.endswith(_DELISTING_SUFFIX) or name.startswith(_DELISTING_PREFIX):
        return RiskWarning.delisting_process
    core = name
    # Strip a share-reform marker only when an ST marker follows it: `S深发展A` and `G上港`
    # carry the same prefix with an ordinary name behind it, and reading a bare leading
    # `S`/`G` as a marker would mismark 1,210 rows across the corpus.
    if core[:1] in _REFORM_MARKERS and (core[1:3] == "ST" or core[1:4] == "*ST"):
        core = core[1:]
    # The marker is `*`, not the literal `*ST`: pre-2020 ChiNext wore 退市风险警示 as a bare
    # `*` prefix (`*欣泰`, `*金亚`), and those are the corpus's only `*`-prefixed non-`*ST`
    # names. Testing `*ST` here answered `none` for both of them.
    if core.startswith(_RISK_WARNING_MARKER):
        return RiskWarning.star_st
    if core.startswith("ST"):
        return RiskWarning.st
    if core.startswith("PT"):
        return RiskWarning.pt
    return RiskWarning.none


@dataclass(frozen=True, slots=True, kw_only=True)
class NameRecord:
    """One name a security carried, with both of the clocks that bound it.

    A plain carrier with no validation of its own, following `CalendarDay`'s precedent: a
    nominal type is not a boundary, so the rules live once, in `build_name_history`.

    There is no `end_date`. The interval this record covers runs from `effective_from` to the
    next record's `effective_from`, and the last record's interval is open -- see this
    module's docstring for why the upstream column is a witness rather than a field.
    """

    ts_code: str
    name: str
    effective_from: date
    announced_on: date
    change_reason: str

    @property
    def risk_warning(self) -> RiskWarning:
        """The risk-warning state this record's name encodes."""
        return risk_warning_of(self.name)


@dataclass(frozen=True, slots=True, kw_only=True)
class NameHistory:
    """One security's names, ascending by effective date.

    Build it with `build_name_history`; this constructor is not a boundary and validates
    nothing.

    There is deliberately **no upper horizon**. The last record answers for every later day,
    including days after the security was delisted, because this dataset carries no
    termination and inventing one here would be a guess. `stock_universe.py` holds that fact.
    """

    ts_code: str
    records: tuple[NameRecord, ...]

    @property
    def first_effective_date(self) -> date:
        return self.records[0].effective_from

    def record_on(self, day: date) -> NameRecord:
        """The record in effect on `day`, or `NameHistoryHorizonError` before the first one.

        Refuses rather than answering the earliest known name: a security's pre-corpus name
        is not "the first one we happen to have", and answering it would make a gap in the
        corpus indistinguishable from a security that never changed its name.
        """
        _require_plain_date(day, "day")
        if day < self.first_effective_date:
            raise NameHistoryHorizonError(
                f"{day.isoformat()} is before {self.ts_code}'s first known name, which takes "
                f"effect {self.first_effective_date.isoformat()}; an unrecorded name is "
                "unknown rather than equal to the earliest one on file"
            )
        position = bisect_right([record.effective_from for record in self.records], day)
        return self.records[position - 1]

    def name_on(self, day: date) -> str:
        """What the security was called on `day`."""
        return self.record_on(day).name

    def risk_warning_on(self, day: date) -> RiskWarning:
        """The risk-warning state on `day`, read from the name in effect then."""
        return self.record_on(day).risk_warning

    def pending_at(self, day: date) -> tuple[NameRecord, ...]:
        """Renames announced on or before `day` that had not taken effect yet.

        The half of the two-clock model `name_on()` cannot express. On 2012-03-01 the answer
        for `000001.SZ` is the 平安银行 record: announced, public, and not yet in force.
        """
        _require_plain_date(day, "day")
        return tuple(
            record for record in self.records if record.announced_on <= day < record.effective_from
        )

    def announced_by(self, day: date) -> NameHistory:
        """This history as an observer standing at `day` could have known it.

        The point-in-time filter, on the announcement clock. Refuses to return an empty
        history: a history with no records answers nothing, and a caller who received one
        would be holding a value that raises on every question rather than a verdict.
        """
        _require_plain_date(day, "day")
        visible = tuple(record for record in self.records if record.announced_on <= day)
        if not visible:
            raise NameHistoryHorizonError(
                f"nothing about {self.ts_code} was announced on or before {day.isoformat()}; "
                "the earliest announcement on file is "
                f"{min(record.announced_on for record in self.records).isoformat()}"
            )
        return NameHistory(ts_code=self.ts_code, records=visible)


def build_name_history(ts_code: str, records: Iterable[NameRecord]) -> NameHistory:
    """Assemble a `NameHistory` from name records, in any order.

    Tushare returns `namechange` in descending `start_date` order, so accepting any order and
    sorting here is the actual shape of the data.

    Refuses, rather than repairs, four things: a malformed `ts_code`, a record belonging to
    another security, a malformed field, and two *different* names effective on one date.
    Byte-identical duplicates **collapse** instead of being refused, because they are real:
    one live pull returned 380 exactly duplicated rows, and a duplicate carries no fact that
    the original does not.
    """
    _require_text(ts_code, "ts_code")
    by_date: dict[date, NameRecord] = {}
    for record in records:
        _require_text(record.name, "name")
        _require_plain_date(record.effective_from, "effective_from")
        _require_plain_date(record.announced_on, "announced_on")
        if record.ts_code != ts_code:
            raise NameHistoryError(
                f"a name history for {ts_code} carries {record.ts_code}; one history covers "
                "one security, and mixing two would answer the wrong one's name"
            )
        existing = by_date.get(record.effective_from)
        if existing is not None and existing != record:
            raise NameHistoryError(
                f"{ts_code} has two names effective on "
                f"{record.effective_from.isoformat()} ({existing.name!r} and "
                f"{record.name!r}); a security has one name at a time, so this is two sources "
                "that were never reconciled"
            )
        by_date[record.effective_from] = record
    if not by_date:
        raise NameHistoryError(
            f"a name history needs at least one name record; {ts_code} has none, and an empty "
            "history refuses every question, which is indistinguishable from a failed read"
        )
    return NameHistory(ts_code=ts_code, records=tuple(by_date[key] for key in sorted(by_date)))


def name_histories_from_panel_rows(
    rows: Iterable[Sequence[object]],
) -> Mapping[str, NameHistory]:
    """Rebuild one history per security from stored rows shaped like
    `NAME_HISTORY_PANEL_COLUMNS`.

    The counterpart of the provider's projection. What this function owns is the shape of a
    *stored row* -- its width and the ISO text its two date columns are stored as -- which
    `build_name_history` never sees; the record rules live there.

    A read-only mapping rather than a `dict`, because the corpus covers the whole market and
    a caller mutating one entry would be editing what other callers hold.
    """
    grouped: dict[str, list[NameRecord]] = {}
    for index, row in enumerate(rows):
        if len(row) != len(NAME_HISTORY_PANEL_COLUMNS):
            raise NameHistoryError(
                f"row {index} has {len(row)} values, expected "
                f"{len(NAME_HISTORY_PANEL_COLUMNS)} "
                f"({', '.join(NAME_HISTORY_PANEL_COLUMNS)})"
            )
        subject, name, effective, announced, reason = row
        ts_code = _require_stored_text(subject, index, SUBJECT_COLUMN_NAME)
        grouped.setdefault(ts_code, []).append(
            NameRecord(
                ts_code=ts_code,
                name=_require_stored_text(name, index, NAME_COLUMN),
                effective_from=_parse_iso_date(effective, index, NAME_EFFECTIVE_COLUMN),
                announced_on=_parse_iso_date(announced, index, NAME_ANNOUNCEMENT_COLUMN),
                change_reason=_require_stored_text(reason, index, NAME_REASON_COLUMN),
            )
        )
    return MappingProxyType(
        {ts_code: build_name_history(ts_code, group) for ts_code, group in grouped.items()}
    )


def _require_text(value: str, role: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise NameHistoryError(
            f"{role} must be a non-empty string without surrounding whitespace; got {value!r}"
        )


def _require_stored_text(value: object, index: int, column: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise NameHistoryError(
            f"row {index}: {column} must be a non-empty string without surrounding "
            f"whitespace, got {type(value).__name__} {value!r}"
        )
    return value


def _parse_iso_date(value: object, index: int, column: str) -> date:
    if not isinstance(value, str):
        raise NameHistoryError(
            f"row {index}: {column} must be an ISO date string, got "
            f"{type(value).__name__} {value!r}"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise NameHistoryError(f"row {index}: {column} is not an ISO date: {value!r}") from error


def _require_plain_date(value: date, role: str) -> None:
    """Reject anything that is not exactly a `date`; see `stock_universe._require_plain_date`."""
    if type(value) is not date:
        raise NameHistoryError(
            f"{role} must be a plain datetime.date, got {type(value).__name__} {value!r}; a "
            "datetime is a date subclass and compares against dates without ever equalling one"
        )
