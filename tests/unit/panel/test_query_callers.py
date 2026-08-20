"""Who may read a partition without a point-in-time verdict, as an allowlist rather than a hope.

`PanelStore.query()` is public, takes no `as_of`, consults no readiness verdict and carries no
row-level `available_time` predicate: on a real `stock_basic` 2024 partition it returns 152
rows, of which 92 were not knowable at 2024-07-01. The gate is `read_if_ready()`, and it is
**opt-in**. Nothing about `query()`'s type, name or signature stops a caller reaching past it.

That is a live seam rather than a tidiness worry, and P2's technical acceptance named it as the
most dangerous one left for P3, in these terms: `V2-P3-002`'s factor engine faces the cost of
rebuilding the panel once per `as_of` -- measured at 120x a single annual build -- and the most
natural response to that cost is to bypass `read_if_ready` and call `query()` directly,
filtering by `available_time` in the factor layer. The guarantee has then moved out of the
storage plane, and nothing audits it there.

Two things were considered instead of this file and both were rejected, for reasons that are on
the record rather than aesthetic.

**Adding the row-level filter to `query()`** was declined by `V2-P2-001/003/004`'s batch and the
argument still holds: a filtered read hands back a *short* partition, and every consumer above
this plane reads shortness as missing data rather than as withheld data --
`build_index_membership` refuses a gap in the month sequence, `load_industry_histories` refuses
an interval whose closing row was filtered away, `build_stock_universe` refuses a delisting
whose listing was. Turning a fail-closed refusal into a plausible-looking short answer is the
one trade this plane is built not to make. See
`tests/integration/panel/test_lookahead_injection.py`'s "What is deliberately not here".

**Making the danger a required argument** (`query(..., unchecked=True)`) would put the warning at
every call site, which is the right instinct and the wrong instrument: it is a breaking change to
a published `1.0.0` API for a method with exactly one caller in `src/`, and a keyword a caller
types once stops being read the second time.

What is left is the thing the acceptance actually asked for -- *something* auditing it. This
file is that something. It is deliberately an allowlist and not a ban: a future reader with a
real need may take the un-gated path, and what it may not do is take it silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "openalpha_cn"

UNGATED_READS: frozenset[str] = frozenset({"query", "profile_query"})
"""The `PanelStore` methods that return rows without consulting a readiness verdict.

`profile_query` runs the statement `query` would run, with DuckDB's profiler on, so it reaches
the same rows by the same route and belongs to the same allowlist -- an exemption for it would
be an exemption for the read.
"""

QUERY_CALLERS: frozenset[str] = frozenset({"panel/store.py", "panel_ingest.py"})
"""Every `src/` file allowed to call one of `UNGATED_READS`, relative to `src/openalpha_cn`.

The first is `PanelStore` itself: `read_if_ready()` calls `query()` *after* `assess_readiness()`
has answered, which is the whole point of the method. Every reader in the tree -- all fourteen
`panel_ingest` loaders, `panel_doctor`'s cross-checks, `panel_gate`, `panel_view`, the CLI, the
HTTP app and the SDK -- reaches rows through `read_if_ready()`, and
`test_the_gated_read_is_what_the_rest_of_the_tree_uses` below is what keeps that from being
vacuously true.

The second is `panel_ingest.carry_stored_rows_forward` (`V2-P4-071`), and it is granted on the
opposite argument from the one this file was built to refuse. **Nothing it reads is answered
with.** A derived partition is written whole and has no append, so a build that adds one instant
to a year has to put the year's existing rows back in front of its own or destroy them; that
function reads them and hands them straight to `write_partition`. A point-in-time read there
would be the fail-open rather than the safe choice: filtering by `available_time` would carry
only the rows knowable at some instant and would commit a partition **missing** the withheld
ones, which is data destruction with a safety argument in front of it. The guarantee this file
protects is about what a caller may *learn*; a byte put back where it was found teaches nobody
anything.

What enforces the guarantee for the write is a drop guard rather than the read, and **this
paragraph named the wrong one until `V2-P4-073`**. It said `panel_factors._refuse_to_drop_a
_stored_build` "runs on the merged batch immediately after each carry-forward, so a merge that
lost a build is refused by name". It ran after the *manifest* carry-forwards only -- it reads the
catalog's stored subject list, which is a build list on a manifest partition and a securities list
on an observation one -- so the observation merge, which is the larger half of every write this
function serves, was audited by nothing at all. A hole confined to it wrote and exited 0, and the
loss surfaced on the next read.

`panel_factors._refuse_a_merge_that_lost_a_stored_build` is the guard that makes the sentence
true. It asks the same question of the merged batch's own build column rather than of the catalog,
so it holds on both kinds and on all three planes, and every writer runs it immediately after the
catalog-side guard. The residue -- a `retain` rule that carried a row it should have replaced --
is caught one plane up by `_refuse_two_builds_of_one_factor_at_one_as_of`'s stored-side twin,
`identity_columns`.

Adding a name here is a deliberate act with a review attached, which is the property this test
exists to create.

**A file is the wrong unit for the second entry, and `UNGATED_READERS` below is where that is
repaired.** The argument in the two paragraphs above is about one function; the permission is
over a module of fourteen loaders. `V2-P4-081` measured what that gap admits, and the narrower
map is what the file-level entry now delegates to.
"""


def _read_method(node: ast.AST, doors: frozenset[str]) -> str | None:
    """The door among `doors` that `node` calls as `<something>.<name>(...)`, or `None`.

    Matched on the call's *shape* rather than on the receiver's type, because this test parses
    files rather than type-checking them and `query` is not a unique name in this tree --
    `EvidenceStore.query(as_of=, subject=, kind=)` is a different method on a different plane
    and must not be flagged. What separates them is `columns`: `PanelStore.query`'s signature
    makes it keyword-**only** and mandatory, so every real call carries it and no other
    `query()` here does. `profile_query` needs no discriminator; the name is its own.

    The residue is a call that splats its arguments (`store.query(**built))`), which this
    cannot see. That is the same limit every AST allowlist in this repository has, and it is
    named rather than papered over: this file raises the cost of the bypass and does not make
    it impossible.

    One function for both allowlists, rather than a copy per door set. The discriminator is a
    property of `query` wherever it is called, so a second copy of it would be a second thing
    to keep true.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    name = node.func.attr
    if name not in doors:
        return None
    if name == "query" and not any(word.arg == "columns" for word in node.keywords):
        return None
    return name


def _calls(tree: ast.AST) -> set[str]:
    """Every `PanelStore` un-gated read called anywhere in `tree`, whatever encloses it."""
    return {
        found for node in ast.walk(tree) if (found := _read_method(node, UNGATED_READS)) is not None
    }


def _functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined in `tree`, by name -- `async def` counted like `def`.

    **`V2-P4-081`.** Both maps below and the fixpoint under them walked `ast.FunctionDef`
    alone, so an `async def load_*` was invisible to all three: it could take the un-gated door
    without appearing in `UNGATED_READERS`, and it could read a partition some third way
    without appearing in `test_no_loader_reaches_a_partition_outside_this_map`'s residue. No
    such function exists in `src/` today, which is exactly the condition under which closing
    the hole costs nothing and leaving it open buys nothing -- and
    `tests/unit/test_import_layering.py`'s own walk already spells the pair out
    (`ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef`), so a one-node-kind walk here was
    an inconsistency rather than a considered boundary. `test_both_maps_read_an_async_def_the
    _same_way_they_read_a_def` is what keeps this sentence from drifting back.
    """
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _reads_by_function(tree: ast.AST, doors: frozenset[str]) -> dict[str, tuple[str, ...]]:
    """Each enclosing function in `tree` mapped to the doors among `doors` that it calls."""
    found: dict[str, list[str]] = {}
    for name, node in _functions(tree).items():
        for inner in ast.walk(node):
            door = _read_method(inner, doors)
            if door is not None:
                found.setdefault(name, []).append(door)
    return {name: tuple(sorted(doors_called)) for name, doors_called in found.items()}


def test_only_the_store_itself_reads_a_partition_without_a_readiness_verdict() -> None:
    offenders = {
        str(path.relative_to(SOURCE))
        for path in sorted(SOURCE.rglob("*.py"))
        if str(path.relative_to(SOURCE)) not in QUERY_CALLERS
        and _calls(ast.parse(path.read_text(encoding="utf-8")))
    }

    assert offenders == set(), (
        f"{sorted(offenders)} call one of {sorted(UNGATED_READS)} without going through "
        "PanelStore.read_if_ready(). That is the un-gated read: query() takes no as_of and "
        "filters no row by availability, so a caller doing its own point-in-time filtering has "
        "moved the guarantee out of the storage plane. If that is intended, add the file to "
        "QUERY_CALLERS and say in the diff what now enforces the guarantee"
    )


def test_the_allowlist_names_files_that_exist_and_actually_make_the_call() -> None:
    """An allowlist entry that no longer calls anything is a permission nobody revoked.

    The same failure mode as a `# noqa` left behind after the code moved: it grants exactly the
    thing it was meant to constrain, to whatever is written there next.
    """
    for name in QUERY_CALLERS:
        path = SOURCE / name
        assert path.is_file(), f"QUERY_CALLERS names {name}, which does not exist"
        assert _calls(ast.parse(path.read_text(encoding="utf-8"))), (
            f"QUERY_CALLERS names {name}, which no longer calls any of {sorted(UNGATED_READS)}; "
            "remove the entry rather than leaving the exemption standing"
        )


UNGATED_READERS: dict[str, tuple[str, ...]] = {
    "carry_stored_rows_forward": ("query",),
}
"""Every function in `panel_ingest` that takes the un-gated door, and which door it takes.

**The file-level entry above is too coarse for this one file, and `V2-P4-081` is what it cost.**
`QUERY_CALLERS` answers *which files may* -- the right granularity for `panel/store.py`, which is
the gate's own implementation and whose single `self.query(...)` is what `read_if_ready` runs
after `assess_readiness` has answered. It is the wrong granularity for `panel_ingest.py`:
`V2-P4-071` put that file on the list for one function's sake, and the permission it actually
granted covers every function in it, the fourteen loaders included. Every argument in
`QUERY_CALLERS`' second paragraph is about `carry_stored_rows_forward` and none of it is true of
anything else in the module, so the exemption is written where the argument is.

The map cannot be satisfied by silence. Adding an un-gated read to any other function in
`panel_ingest` puts that function's name in this map's measured half and nothing in its declared
half, whatever else the function does and whatever else it calls -- which is the property the
fixpoint below could not have, because it widens toward callers and a loader that calls
`load_trading_calendar` first is admitted by that call rather than judged on its own read.
"""


def test_no_other_function_in_this_module_takes_the_un_gated_door() -> None:
    """The reads, enumerated, rather than the readers.

    `test_no_loader_reaches_a_partition_outside_this_map` asks whether a function reaches a gate
    *somewhere*, and `V2-P4-081` measured what that is not: a `load_*` that calls
    `load_trading_calendar` -- which every real loader does, because they all need the calendar --
    and then reads its own partition through `store.query()` satisfies it exactly. Reaching a gate
    is not the same as reaching a gate for *this* read, and the fixpoint cannot tell the two apart
    by construction, because widening toward callers is what makes it able to follow a chain at
    all.

    So this is the half that answers the question the file is named for. It does not ask which
    functions reach a verdict; it asks which calls return rows without one, and requires the set
    of functions making them to be exactly the one function that argued for the right.
    """
    ingest = ast.parse((SOURCE / "panel_ingest.py").read_text(encoding="utf-8"))

    assert _reads_by_function(ingest, UNGATED_READS) == UNGATED_READERS, (
        "panel_ingest's un-gated reads are not the ones this map declares. query() takes no "
        "as_of and filters no row by availability, so a function reading through it has moved "
        "the point-in-time guarantee out of the storage plane -- and being on QUERY_CALLERS is "
        "a permission granted to this file for carry_stored_rows_forward's argument, which is "
        "that nothing it reads is answered with. If a second function has that same property, "
        "name it here and say in the diff why its rows are put back rather than answered"
    )


GATED_READS: frozenset[str] = frozenset({"read_if_ready", "read_visible_at"})
"""The `PanelStore` methods that consult a readiness verdict before returning rows.

Two, and the second is not a relaxation of the first. `read_visible_at` runs the **same**
`evaluate_readiness` over the **same** `PartitionState`s and blocks on every issue
`read_if_ready` blocks on, substituting a row predicate for a refusal only where the rule table
found nothing but `ROW_FILTERABLE_ISSUE_CODES`; it then re-decides the two scope-sensitive codes
over the rows it is about to hand back. What it is *not* is `query()`, which consults no verdict
at all, and that is the line this file draws.

Who may take the filtered door, and what each caller does about a short answer, is a separate
allowlist with a separate argument: `tests/unit/panel/test_visible_read_callers.py`.
"""

GATED_READERS: dict[str, tuple[str, ...]] = {
    "_read_visible_event_dated_rows": ("read_visible_at",),
    "_read_visible_price_session": ("read_visible_at",),
    "load_adjustment_histories": ("read_if_ready",),
    "load_index_membership": ("read_if_ready",),
    "load_index_prices": ("read_if_ready",),
    "load_industry_histories": ("read_if_ready",),
    "load_industry_trees": ("read_if_ready",),
    "load_trading_calendar": ("read_if_ready",),
}
"""Every function in `panel_ingest` that reaches rows, and the door it reaches them through.

**A map rather than a count, and `V2-P4-076` is why.** This was `len(reads) >= 10` over
`read_if_ready` alone, and the number is a proxy for the thing that matters -- that the loaders
reach rows through a verdict-returning method -- which drifts the moment two loaders come to
*share* a door. `V2-P4-076` moved `load_stock_universe`, `load_suspensions` and
`load_name_histories` onto one shared reader, which removed three call sites and added none, and
the count fell to 7 while the coverage rose. A threshold lowered to match would have been the
guard being edited to fit the tree instead of the other way round.

The map cannot drift that way: a loader that stopped reading through a gated door disappears
from it, a new door appears in it, and either is a diff somebody signs. The four loaders that
are no longer here by name reach `_read_visible_event_dated_rows` instead, which is, and
`test_no_loader_reaches_a_partition_outside_this_map` is the half that says nobody else does.

**`V2-P4-083` removed the fourth and deliberately left three, which is the review this map is
for.** `load_statement_histories` went the way `V2-P4-076`'s three did: `ClockStrategy.
announcement` dates a filing's availability at midnight on its own `ann_date`, so the
per-event-date census reconciles exactly, and the whole-year refusal was costing
`panel_doctor._ambiguity_check` every read at an instant inside the year.

**Six rows still take `read_if_ready`, and they are five different reasons rather than one
backlog.** Written out because "these are the ones nobody has moved yet" is exactly the sentence
this map exists to stop somebody from having to assume:

- **`load_trading_calendar`** -- `V2-P4-076`'s measurement, unchanged. `calendar_publication`
  makes a year partition's newest availability instant the *earliest* in it, so
  `not_yet_knowable` cannot fire at any `as_of` inside the year and there is no refusal to
  remove.
- **`load_industry_trees`** -- `V2-P4-083`'s, and the same conclusion from the other side. Every
  row of an `index_classify` vintage carries one `taxonomy_date`, so the partition's newest
  availability instant is its **only** one: the refusal is all-or-nothing and can only mean "you
  asked before this taxonomy existed", never "the panel has moved on".
- **`load_industry_histories`** -- `V2-P4-027`'s, and it is a signature rather than a clock. It
  returns histories whose only bound is `answerable_through`, a *year*, so a mid-year `as_of` has
  no honest bound to offer; `load_industry_cross_section` is the reader that takes the day as an
  argument and therefore took the filtered door for this dataset. See
  `tests/unit/panel/test_visible_read_callers.py`.
- **`load_adjustment_histories`** -- `V2-P4-079`'s, and the one where the wall is real and the
  door still does not fit. `compress_adjustment_batch` stores a step function, so a row
  predicate collapses `covered_through` onto the year's opening anchor; see the loader's own
  docstring and `tests/integration/panel/test_whole_partition_doors.py` for the numbers and for
  the two edits outside `panel_ingest` that a move would need.
- **`load_index_membership` and `load_index_prices`** -- about callers rather than clocks. The
  first has none in `src/` at all (`test_whole_partition_doors.py` pins it, so the day something
  reaches for it the door has to be judged on that caller's instant). The second had none
  anywhere until `V2-P4-083` gave it `panel_doctor.REBUILDABLE_INDEX_PRICES`, which reads at the
  report's own `as_of` and over years the caller named -- a rebuild rather than a point-in-time
  question, so the whole-partition verdict is the one it wants.
"""


def test_the_gated_read_is_what_the_rest_of_the_tree_uses() -> None:
    """The positive half: this allowlist would also be satisfied by a tree where nobody reads
    partitions at all, which would make it vacuous. `panel_ingest` is where the readers live,
    and it must be reaching them through a verdict-returning method.
    """
    ingest = ast.parse((SOURCE / "panel_ingest.py").read_text(encoding="utf-8"))

    assert _reads_by_function(ingest, GATED_READS) == GATED_READERS, (
        "panel_ingest's gated reads are not the ones this map declares; if the loaders stopped "
        "using a verdict-returning method this allowlist would pass while proving nothing"
    )


def _plain_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The module-level names `node` calls directly -- `foo(...)`, not `store.foo(...)`."""
    return {
        inner.func.id
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
    }


def test_no_loader_reaches_a_partition_outside_this_map() -> None:
    """The half a map of *readers* still needs: that the map is the whole set of them.

    Every public `load_*` in `panel_ingest` reaches rows through a gated store method, directly
    or through some chain of this module's own functions that does. Resolved as a fixpoint
    rather than at a fixed depth, because the depth is not one and pinning it would be a second
    thing to get wrong: `load_industry_cross_section` is two hops out --
    `_read_visible_membership_rows` is `V2-P4-027`'s door and since `V2-P4-076` it is a call into
    the shared `_read_visible_event_dated_rows` rather than a fourth copy of it.

    **What this does and does not decide, corrected by `V2-P4-081`.** The fixpoint widens
    *toward callers*, so admission here means "calls something that reaches a gate", and a
    loader that calls `load_trading_calendar` -- which every real loader does -- is admitted by
    that call whatever it then does with its own partition. That is not a hole to be plugged in
    this test: widening toward callers is the only way to follow a chain at all, and narrowing
    it to "reaches a gate and reads nothing else" would be
    `test_no_other_function_in_this_module_takes_the_un_gated_door` written twice, in the weaker
    place. So the two halves are two questions. That one enumerates the reads: no call returns
    rows without a verdict except the one function that argued for it. This one enumerates the
    readers: no `load_*` reaches rows by some *third* route -- a method in neither `GATED_READS`
    nor `UNGATED_READS`, which neither map would see and which a `load_*` with no path to a gate
    at all is the visible shape of.
    """
    ingest = ast.parse((SOURCE / "panel_ingest.py").read_text(encoding="utf-8"))
    functions = _functions(ingest)
    reaching = set(GATED_READERS)
    while True:
        widened = {
            name for name, node in functions.items() if _plain_calls(node) & reaching
        } - reaching
        if not widened:
            break
        reaching |= widened

    ungated = sorted(
        name for name in functions if name.startswith("load_") and name not in reaching
    )

    assert ungated == [], (
        f"{ungated} are load_* functions that reach no gated store method by any chain of this "
        "module's own functions; a loader that reaches rows some third way is what this file "
        "exists to make visible"
    )


ASYNC_READERS: str = """
async def load_probe_async(store, *, year):
    return store.query(dataset="d", year=year, columns=("subject",))


async def load_probe_async_gated(store, *, year):
    return store.read_if_ready(requirement, year=year, columns=("subject",))
"""
"""A module with no `def` in it at all, only `async def`, holding one read through each door."""


def test_both_maps_read_an_async_def_the_same_way_they_read_a_def() -> None:
    """`V2-P4-081`'s secondary finding, kept from drifting back rather than written down.

    See `_functions` for why the gap was closed instead of named. Asserted on a synthetic module
    because `src/` has no `async def` outside `api/app.py`'s ASGI callables, and a guard whose
    only witness is the tree it guards goes quiet exactly when the tree changes.
    """
    tree = ast.parse(ASYNC_READERS)

    assert sorted(_functions(tree)) == ["load_probe_async", "load_probe_async_gated"]
    assert _reads_by_function(tree, UNGATED_READS) == {"load_probe_async": ("query",)}
    assert _reads_by_function(tree, GATED_READS) == {"load_probe_async_gated": ("read_if_ready",)}
