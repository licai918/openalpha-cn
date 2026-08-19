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

What enforces the guarantee for the write is the drop guard rather than the read:
`panel_factors._refuse_to_drop_a_stored_build` runs on the merged batch immediately after each
carry-forward, so a merge that lost a build is refused by name. The residue -- a `retain` rule
that carried a row it should have replaced -- is caught one plane up by
`_refuse_two_builds_of_one_factor_at_one_as_of`'s stored-side twin, `identity_columns`.

Adding a name here is a deliberate act with a review attached, which is the property this test
exists to create.
"""


def _calls(tree: ast.AST) -> set[str]:
    """Every `PanelStore` un-gated read called as `<something>.<name>(...)` in `tree`.

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
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = node.func.attr
        if name not in UNGATED_READS:
            continue
        if name == "query" and not any(word.arg == "columns" for word in node.keywords):
            continue
        found.add(name)
    return found


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


def test_the_gated_read_is_what_the_rest_of_the_tree_uses() -> None:
    """The positive half: this allowlist would also be satisfied by a tree where nobody reads
    partitions at all, which would make it vacuous. `panel_ingest` is where the readers live,
    and it must be reaching them through the verdict-returning method.
    """
    ingest = ast.parse((SOURCE / "panel_ingest.py").read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(ingest)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_if_ready"
    ]

    assert len(reads) >= 10, (
        f"panel_ingest makes {len(reads)} gated reads; if the loaders stopped using "
        "read_if_ready() this allowlist would pass while proving nothing"
    )
