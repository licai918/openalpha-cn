"""Who may take the visibility-filtered read, and who may reach the evidence plane.

Two audits with one subject: `V2-P3-002`'s two structural claims, each held by a check rather
than by a sentence in a docstring.

## 1. The filtered read is an allowlist, exactly as `query()` is

`PanelStore.read_visible_at` hands back a partition **minus** every row whose `available_time`
post-dates `as_of`. That is a deliberately short answer, and P2 declined to make `query()` do
it for a reason that has not stopped being true: "a filtered read hands back a short partition,
and every consumer above this plane reads shortness as missing data rather than as withheld
data" -- `build_index_membership` refuses a gap in the month sequence,
`load_industry_histories`' `answerable_through` exists because a read stopping short of an
interval's closing row "reassembles an interval that never ends", and `build_stock_universe`
refuses a delisting whose listing was filtered away.

P3 did not overturn that argument; it built a second door and left the first one shut. What
changed is that shortness is now *stated*: `PanelVisibleReadOutcome.withheld_row_count` is a
number the caller cannot get rows without also getting, and `visible_last_event_time` says how
far the answer reaches, which the first number does not imply.

**This file's own claim about the type split was too strong and is corrected here.** It read
"the outcome is a different type, so a filtered read cannot be handed to a reader expecting a
whole partition without mypy objecting". Measured under `mypy --strict`:
`PanelVisibleReadOutcome.rows` and `PanelReadOutcome.rows` have the *identical* static type, the
three rebuilders above take **rows** rather than outcomes, and
`stock_universe_from_panel_rows(list(filtered.rows), ...)` therefore type-checks clean. The type
checker refuses exactly one thing -- passing a whole outcome where the other outcome was
expected. So the allowlist below is not a belt beside a brace; **it is the obstacle**, and
`tests/integration/panel/test_visibility_filtered_read.py::
test_the_two_read_outcomes_expose_rows_at_the_same_static_type` pins the fact that makes that
true. `tests/unit/panel/test_query_callers.py` established the form; this is the same instrument
on the second door, and it is deliberately an allowlist and not a ban -- a future reader with a
real need may take the path, and what it may not do is take it silently.

## 2. Factor observations are kept off the evidence plane by the import graph

`V2-P3-002` forbids factor observations from `ParquetEvidenceStore`. `panel_factors.py`'s
docstring states precisely how strong that is and this file is the executable half.

The claim is *not* that the store would reject them. `EvidenceSnapshot.kind` is
`str(min_length=1, max_length=64)`, so `EvidenceSnapshot(kind="factor_observation", ...)` is
constructible and `ParquetEvidenceStore.append` would take it; `evidence/builder.py`'s closed
`_NORMALIZERS` table refuses an unknown kind on the *normalisation* path from a `ProviderBatch`,
which is not the store's front door. The claim is that the module producing factor observations
has no edge to either package and cannot acquire one without failing a test -- which makes
writing the conversion a reviewed act rather than an available one. That is what a structural
obstacle is; "impossible" would be an overclaim, and this repository has already paid once for
a docstring that made one (`domain/panel_batch.py`'s "a future `panel-batch/v2` is detectable",
which it was not).
"""

from __future__ import annotations

import ast
from pathlib import Path

import grimp

from openalpha_cn.domain.panel_batch import (
    CLOCK_COLUMN_NAMES,
    RESERVED_COLUMN_NAMES,
    SUBJECT_COLUMN_NAME,
)
from openalpha_cn.panel.store import AVAILABILITY_COLUMN, EVENT_TIME_COLUMN, SUBJECT_COLUMN

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "openalpha_cn"

FILTERED_READ = "read_visible_at"
"""The `PanelStore` method that answers with a deliberately short partition."""

FILTERED_READ_CALLERS: frozenset[str] = frozenset(
    {"panel_factors.py", "panel_neutralization.py", "panel_doctor.py", "panel_ingest.py"}
)
"""Every `src/` file allowed to call `read_visible_at`, relative to `src/openalpha_cn`.

The first entry is the factor engine -- the caller `V2-P3-002` added the method for. Its inputs
are year partitions being read at a mid-year `as_of`, which `read_if_ready` refuses whole
(roadmap section 11), and its output partition has the same shape when it is read back: an
observation's `available_time` is the `as_of` it was computed at, so a year of daily cross
sections has a `max_available_time` in December.

Adding a name here is a deliberate act with a review attached, which is the property this test
exists to create. A diff that grants it must say what the new caller does about shortness --
specifically, whether it can tell a withheld row from an absent one, because the three domain
rebuilders named in this module's docstring cannot.

**`panel_neutralization.py` (`V2-P3-004`) is the second, and here is its answer to that.** It
takes the filtered read in exactly two places -- `load_neutralized_factor_observations` and
`load_factor_neutralization_manifests` -- both of which read back **its own output partitions**,
whose rows have the factor plane's mid-year shape for the factor plane's reason. Neither
reassembles anything: a neutralised row is decoded on its own and carries every field it needs,
so a withheld row is a later `as_of` of the same factor rather than a hole in a structure. That
is precisely the property the three domain rebuilders lack -- `build_index_membership` refuses a
gap in a month sequence, `load_industry_histories` needs `answerable_through` because a read that
stops short of a closing row reassembles an interval that never ends, and `build_stock_universe`
refuses a delisting whose listing was filtered away.

What the entry is *not* spent on is the neutralisation's two **foreign** inputs. The industry
memberships and the market caps are read through `panel_ingest.load_industry_histories` and
`panel_ingest.load_daily_valuations`, which take `read_if_ready` -- the un-gated door that
refuses a partition whose newest row post-dates `as_of` rather than filtering it. So the newly
allowlisted module reads its foreign data at a *stricter* setting than the already-allowlisted
one reads its own.

**`V2-P4-026` moved one of those two, and only one.** `load_daily_valuations` now takes the
filtered door through `panel_ingest._read_visible_price_session`; `load_industry_histories` does
not and is not going to, for the reason the paragraph above gives. The fourth entry below is
that grant, and this paragraph is the correction of the sentence above it.

**`panel_doctor.py` (`V2-P3-019`) is the third, and its answer is the sharpest of the three
because it is the only caller here that is not reading a partition it wrote.** `_factor_seal_check`
takes the filtered read over the six derived partitions -- three tiers of answers and the three
manifest partitions that address them -- and holds each build's stored cross section against the
digest its manifest declares. A hash over a *short* cross section would be the exact failure this
allowlist exists to prevent, so the property that makes it sound is stated rather than assumed:
**a factor build is either wholly visible or wholly withheld.** Every write path on all three
tiers stamps all four clocks of every row with the build's own `as_of` (see
`panel_factors.factor_observation_batch`), so one build's rows share one `available_time` and
`read_visible_at` can never return a proper subset of one. A read at an earlier `as_of` sees fewer
builds; it never sees half of one, and a build the answer partition drops is dropped from the
manifest partition in the same breath. The report therefore has nothing to reassemble and no
interval to leave open -- which is `panel_neutralization`'s answer with the reason stated one
level deeper, because here a short read would produce a *false accusation* rather than a missing
row.

The un-filtered door was not an option: `read_if_ready` refuses a year partition whose newest
`available_time` post-dates `as_of`, which for a year of daily cross sections is December, so a
health report run at any instant inside the year would have been unable to look at the plane at
all -- which is the state `V2-P3-019` found and is fixing.

**`panel_ingest.py` (`V2-P4-026`) is the fourth, and its answer is the strongest of the four,
because for this caller the question has a measured answer rather than an argued one.** The
single call site is `_read_visible_price_session`, reached only from `load_daily_valuations`,
and it always passes `filters={"trade_date": <one session>}`. It is the first caller in `src/`
to pass `filters` at all, and that is the whole of why it is sound:
`providers/tushare.py::_daily_close_timeline` dates every price row's `available_time` at
`DAILY_AVAILABILITY_TIME` on its own `trade_date`, so one session's rows carry one availability
instant, and `_build_visible_census_sql` takes the withheld count inside the caller's own
filters. **A session read is therefore all-or-nothing.** Measured on the generated fixture panel
at `as_of` 2026-01-12T04:00Z: 2026-01-09 answers 7 rows with `withheld_row_count == 0` -- the
eighth security was halted and has no row at all, so an *absent* row arrives as an absent row --
while 2026-01-12, 2026-01-13 and 2026-01-16 each answer 0 rows with `withheld_row_count == 8`.
"Withheld" and "absent" are two different pairs of numbers on this door, which is exactly what
`build_index_membership`, `load_industry_histories` and `build_stock_universe` cannot say.

The caller does not merely *observe* that difference, it **refuses on it**: a session with rows
withheld and none visible raises rather than answering `{}`, and a session with some of each
raises too, because that mixture is the all-or-nothing property failing and a partial session is
what would be indistinguishable from a thin one. Both refusals are named separately from the
readiness codes, and a session whose 16:30 has not arrived at `as_of` is refused before any
partition is touched. `tests/integration/panel/test_daily_panel_ingest.py` drives all four
doors.
"""


def _calls(tree: ast.AST, name: str) -> bool:
    """Whether `tree` calls `<something>.<name>(...)` anywhere.

    Matched on the attribute name alone, unlike `test_query_callers.py`'s `columns` keyword
    discriminator, because `read_visible_at` is not an English word that another class in this
    tree happens to use -- it exists once, on `PanelStore`.

    **The residue was named wrongly and is corrected.** It said "a call that splats its arguments
    is invisible to an AST allowlist". Measured: it is not -- `store.read_visible_at(*args)`,
    `store.read_visible_at(requirement, **kwargs)` and `store.read_visible_at(*args, **kwargs)`
    are all `ast.Call` nodes with an `ast.Attribute` func, so all three are caught, and so is a
    chained receiver such as `self._store.read_visible_at(...)`. Argument shape is irrelevant to
    this matcher; only the *callee expression* matters.

    What actually escapes is a call whose callee is not an attribute access at the call site:
    binding the method to a name first (`reader = store.read_visible_at; reader(...)`) or going
    through `getattr(store, "read_visible_at")(...)`. Both are measured in
    `test_the_detector_sees_the_call_shapes_it_claims_to_and_names_the_two_it_does_not`, which is
    the shape of a bypass a reviewer can recognise on sight -- which is the property this
    allowlist buys, since it never claimed to be unbypassable.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        for node in ast.walk(tree)
    )


def _source_modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


CAUGHT_SHAPES: tuple[str, ...] = (
    "store.read_visible_at(requirement, year=2026, columns=())",
    "store.read_visible_at(*args)",
    "store.read_visible_at(requirement, **kwargs)",
    "store.read_visible_at(*args, **kwargs)",
    "self._store.read_visible_at(**kwargs)",
)
"""Call shapes this detector sees. The three splat forms are here because the docstring it
replaced claimed they were not."""

ESCAPING_SHAPES: tuple[str, ...] = (
    "reader = store.read_visible_at\nreader(requirement)",
    'getattr(store, "read_visible_at")(requirement)',
)
"""Call shapes this detector does not see: the callee is not an attribute access at the call."""


def test_the_detector_sees_the_call_shapes_it_claims_to_and_names_the_two_it_does_not() -> None:
    """`_calls`' own residue, measured rather than asserted from the shape of the code.

    Both directions are here for the reason the allowlist has two tests: the positive half stops
    the detector quietly matching nothing, and the negative half is the honest boundary. If a
    later change strengthens `_calls` -- resolving a bound-method alias, say -- the second loop
    goes red, and that is the intended signal: the docstring's residue paragraph is then wrong
    and has to be narrowed rather than left standing as a warning about a hole that closed.
    """
    for source in CAUGHT_SHAPES:
        assert _calls(ast.parse(source), FILTERED_READ), f"{source!r} should be caught"

    for source in ESCAPING_SHAPES:
        assert not _calls(ast.parse(source), FILTERED_READ), (
            f"{source!r} is now caught; `_calls` was strengthened, so this module's residue "
            "paragraph overstates the hole and must be narrowed"
        )


def test_only_the_allowlisted_modules_take_the_visibility_filtered_read() -> None:
    offenders = {
        str(path.relative_to(SOURCE))
        for path in _source_modules()
        if str(path.relative_to(SOURCE)) not in FILTERED_READ_CALLERS | {"panel/store.py"}
        and _calls(ast.parse(path.read_text(encoding="utf-8")), FILTERED_READ)
    }

    assert offenders == set(), (
        f"{sorted(offenders)} call PanelStore.{FILTERED_READ}(), which answers with a partition "
        "minus every row that was not knowable at as_of. Every consumer above this plane that "
        "was written against read_if_ready reads a short answer as missing data. If this caller "
        "can tell a withheld row from an absent one, add it to FILTERED_READ_CALLERS and say so "
        "in the diff"
    )


def test_the_allowlist_names_files_that_exist_and_actually_make_the_call() -> None:
    """An allowlist entry that no longer calls anything is a permission nobody revoked.

    `test_query_callers.py`'s own second test, for its own reason: the same failure mode as a
    `# noqa` left behind after the code moved, granting exactly the thing it was meant to
    constrain to whatever is written there next.
    """
    for name in FILTERED_READ_CALLERS:
        path = SOURCE / name
        assert path.is_file(), f"FILTERED_READ_CALLERS names {name}, which does not exist"
        assert _calls(ast.parse(path.read_text(encoding="utf-8")), FILTERED_READ), (
            f"FILTERED_READ_CALLERS names {name}, which no longer calls {FILTERED_READ}(); "
            "remove the entry rather than leaving the exemption standing"
        )


def test_the_engine_takes_the_filtered_read_and_not_the_un_gated_one() -> None:
    """The positive half, without which the allowlist above would be satisfied by a tree where
    the factor engine read nothing at all.

    Two directions, because they fail differently. The engine must reach rows through
    `read_visible_at` -- if it stopped, this file would be policing a door nobody uses. And it
    must **not** reach them through `query()`: `tests/unit/panel/test_query_callers.py` already
    fails on that, and asserting it here too is what keeps the two audits from being read as
    alternatives. `read_visible_at` runs the whole readiness rule table and compensates exactly
    one code; `query()` runs none of it.
    """
    engine = ast.parse((SOURCE / "panel_factors.py").read_text(encoding="utf-8"))

    assert _calls(engine, FILTERED_READ)
    assert not _calls(engine, "query")
    assert not _calls(engine, "profile_query")
    assert not _calls(engine, "read_if_ready")


def test_the_availability_column_this_module_filters_on_is_the_one_the_batch_contract_writes() -> (
    None
):
    """`panel/store.py` restates `"available_time"` rather than importing it, because
    `openalpha_cn.panel` imports no sibling subpackage at all. Two copies of a string that has
    to be identical is exactly the drift `PANEL_BATCH_SCHEMA_VERSIONS_READABLE` already carries
    a pin for, and this is that pin: the store's predicate names a column every panel row
    actually has, and the batch contract reserves the name so no provider column can shadow it.
    """
    assert AVAILABILITY_COLUMN in CLOCK_COLUMN_NAMES
    assert AVAILABILITY_COLUMN in RESERVED_COLUMN_NAMES


def test_the_two_columns_the_second_gate_reads_are_pinned_the_same_way() -> None:
    """`V2-P3-002`'s review added two more restated names, so they get the same pin.

    `read_visible_at` now aggregates `event_time` (to say how far the visible rows reach) and
    probes `subject` (to re-decide `subject_missing`), both by literal name in SQL. Each is
    restated in `panel/store.py` for the reason `AVAILABILITY_COLUMN` is -- `openalpha_cn.panel`
    imports no sibling subpackage -- and two copies of a string that has to be identical is the
    drift the assertion above exists for. A predicate naming a column no panel row carries would
    make the re-decided checks refuse every partition; a `subject` a provider column could shadow
    would make the probe answer about the wrong thing.
    """
    assert EVENT_TIME_COLUMN in CLOCK_COLUMN_NAMES
    assert EVENT_TIME_COLUMN in RESERVED_COLUMN_NAMES
    assert SUBJECT_COLUMN == SUBJECT_COLUMN_NAME
    assert SUBJECT_COLUMN in RESERVED_COLUMN_NAMES


def test_no_top_level_panel_module_can_reach_the_evidence_plane_at_all() -> None:
    """`V2-P3-002`'s "forbidden from `ParquetEvidenceStore`", as a live import-graph question.

    `ParquetEvidenceStore` is in `openalpha_cn.storage` and the normaliser that feeds it is in
    `openalpha_cn.evidence`. No `panel_*` module may import either, so the module that produces
    `FactorObservation`s has no way to hand one to that store without a diff that adds an edge
    a test refuses. `tests/unit/test_panel_ingest_import_isolation.py::
    test_no_top_level_panel_module_reaches_a_composition_root_or_a_credential` asks the same
    graph a broader question for a different reason (a credential, a composition root); this
    one is asserted here as well, over the discovered module list, because the reason it holds
    for the factor engine is `V2-P3-002`'s own and would survive a change to that one's
    forbidden set.
    """
    graph = grimp.build_graph("openalpha_cn")
    modules = sorted(
        f"openalpha_cn.{path.stem}"
        for path in SOURCE.glob("panel_*.py")
        if not path.stem.startswith("__")
    )

    assert "openalpha_cn.panel_factors" in modules
    for module in modules:
        for plane in ("openalpha_cn.storage", "openalpha_cn.evidence"):
            assert not graph.direct_import_exists(
                importer=module, imported=plane, as_packages=True
            ), f"{module} must not reach {plane}: factor observations write the panel plane only"


def test_the_evidence_store_is_reachable_from_somewhere_so_the_check_above_is_not_vacuous() -> None:
    """The sentinel for the assertion above: if `openalpha_cn.storage` had no importer at all,
    or `grimp` had stopped resolving these names, every `assert not ...` would pass while
    proving nothing about the boundary."""
    graph = grimp.build_graph("openalpha_cn")

    assert graph.direct_import_exists(
        importer="openalpha_cn.sdk", imported="openalpha_cn.storage", as_packages=True
    )
    assert graph.direct_import_exists(
        importer="openalpha_cn.panel_factors", imported="openalpha_cn.panel", as_packages=True
    )
