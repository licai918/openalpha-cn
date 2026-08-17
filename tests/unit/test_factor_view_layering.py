"""Layering and envelope proofs for `openalpha_cn.factor_view` (`V2-P3-015`).

`tests/unit/test_panel_ingest_import_isolation.py` pins every top-level `panel_*` module's
dependency set and forbids all of them from reaching `backtest`, `storage`, `runtime`, `api` and
`providers`. `factor_view.py` is a top-level module that must reach `backtest` -- its whole job is
to drive the five factor leaves -- so it is deliberately **not** named `panel_*`, and that means
none of those tests cover it. This file is what stands there instead.

The question a new top-level module has to answer is the one that file asks four times: is it a
pattern or an evasion? An evasion leaves a guarded package's real dependency set untouched and
only moves it out of the metric's sight. This one cannot be, because every edge runs *into*
`factor_view` from the three faces above it and *out of* it into the planes below, never back --
`openalpha_cn.panel`'s import closure, `openalpha_cn.backtest`'s and `openalpha_cn.storage`'s are
all byte-for-byte what they were before this module existed.

The other half is the envelopes. `factor_view` names its faults and the two channels each hold a
table keyed by those names, so a fault added with no row is a `KeyError` at that channel's
boundary rather than a silently mis-enveloped refusal. That only works if the tables really are
keyed by every name, which is checked here off the live class hierarchy rather than off a list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import grimp

from openalpha_cn.api.app import FACTOR_HTTP_STATUS
from openalpha_cn.cli import FACTOR_EXIT, PanelExit
from openalpha_cn.factor_view import (
    FactorPanelUnreadableError,
    FactorRequestError,
    FactorRunBlockedError,
    FactorViewError,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

ALLOWED_FACTOR_VIEW_DEPENDENCIES: Final[set[str]] = {
    "openalpha_cn.backtest",
    "openalpha_cn.domain",
    "openalpha_cn.panel",
    "openalpha_cn.panel_factors",
    "openalpha_cn.panel_ingest",
    "openalpha_cn.panel_neutralization",
    "openalpha_cn.panel_view",
}
"""Exactly the seven `factor_view` may join, stated in full rather than derived from another row.

Each is there for its own reason and not by family resemblance: `backtest` for the five factor
leaves and the execution policy, `panel_factors` and `panel_neutralization` for the three stored
tiers, `panel_ingest` for the six loaders a label needs, `panel` for `PanelStore`, `panel_view`
for the one definition of where a panel lives, and `domain` for every contract underneath.

**`openalpha_cn.storage` is the absence that matters**, and it is not an accident of the current
design: the store this face writes to is declared as a `Protocol` beside the consumer
(`ExperimentDocumentStore`) precisely so that `storage/factor_experiments.py` can satisfy it
structurally with no import in either direction. An edge here would put `openalpha_cn.storage` one
hop from `openalpha_cn.backtest` on the reachability graph `storage-no-upward-deps` checks.
"""

FORBIDDEN_FOR_A_FACE: Final[set[str]] = {
    "openalpha_cn.api",
    "openalpha_cn.agents",
    "openalpha_cn.evidence",
    "openalpha_cn.models",
    "openalpha_cn.product",
    "openalpha_cn.providers",
    "openalpha_cn.runtime",
    "openalpha_cn.storage",
}
"""What a shared rendering must not see.

`providers` is a credential inside the module that builds response bodies; `runtime` and `storage`
are composition roots, so an answer that could reach them would depend on how the process was
wired rather than on what is in the panel; `api` is an inversion of the direction the whole thing
runs in. `test_no_top_level_panel_module_reaches_a_composition_root_or_a_credential` says the same
for the six `panel_*` modules, and this is that sentence for the module that file cannot see.
"""


def _direct_internal_dependencies(module: str, graph: grimp.ImportGraph) -> set[str]:
    siblings = {
        name
        for name in graph.modules
        if name.count(".") == 1 and name.startswith("openalpha_cn.") and name != module
    }
    return {
        sibling
        for sibling in siblings
        if graph.direct_import_exists(importer=module, imported=sibling, as_packages=True)
    }


def test_the_factor_face_joins_exactly_the_planes_it_renders() -> None:
    """An equality rather than a subset: a module that stops importing one of the packages it
    exists to join has lost its reason to be top-level, which is
    `test_panel_ingest_depends_only_on_domain_and_panel`'s own argument."""
    graph = grimp.build_graph("openalpha_cn")

    dependencies = _direct_internal_dependencies("openalpha_cn.factor_view", graph)

    assert dependencies == ALLOWED_FACTOR_VIEW_DEPENDENCIES


def test_the_factor_face_reaches_no_composition_root_and_no_credential() -> None:
    """Stated separately from the equality above, because the two fail differently.

    The equality goes red for any change to the set; this one names *which* forbidden package was
    reached, which is the actionable half when a future edit adds one.
    """
    graph = grimp.build_graph("openalpha_cn")

    leaked = _direct_internal_dependencies("openalpha_cn.factor_view", graph) & FORBIDDEN_FOR_A_FACE

    assert leaked == set()


def test_nothing_below_the_factor_face_imports_it_and_all_three_faces_do() -> None:
    """The pattern-or-evasion question, asked directly.

    Every edge runs into this module from a face and out of it into a plane. The negative half is
    checked against every package it imports, so a cycle introduced later fails here; the positive
    half is the sanity check that keeps the negative one from passing vacuously on a module nobody
    uses.

    `openalpha_cn.api` is excluded from the negative half and asserted in the positive one: it is
    the HTTP *face*, so it imports this module by design. Every other member of both sets sits
    below it, including `runtime` -- whose composition root types its experiment-store field
    against the concrete `FileExperimentStore` rather than against the `Protocol` precisely so
    that this edge stays absent.
    """
    graph = grimp.build_graph("openalpha_cn")
    below_it = (ALLOWED_FACTOR_VIEW_DEPENDENCIES | FORBIDDEN_FOR_A_FACE) - {"openalpha_cn.api"}

    for below in sorted(below_it):
        assert not graph.direct_import_exists(
            importer=below, imported="openalpha_cn.factor_view", as_packages=True
        ), f"{below} must not import openalpha_cn.factor_view"
    for face in ("openalpha_cn.cli", "openalpha_cn.api", "openalpha_cn.sdk"):
        assert graph.direct_import_exists(
            importer=face, imported="openalpha_cn.factor_view", as_packages=True
        ), f"sanity check: {face} is supposed to run through the shared face"


def test_the_document_store_reaches_nothing_above_it() -> None:
    """`storage/factor_experiments.py` holds strings, and the signature is what proves it.

    The store satisfies `factor_view.ExperimentDocumentStore` structurally. If it imported the
    Protocol -- or the record, or `open_experiment` -- `openalpha_cn.storage` would reach
    `openalpha_cn.backtest` and `storage-no-upward-deps` would break; this asserts the absence
    directly, so the reason the contract still holds is visible here rather than only in
    `lint-imports`' exit code.
    """
    graph = grimp.build_graph("openalpha_cn")

    for forbidden in ("openalpha_cn.factor_view", "openalpha_cn.backtest", "openalpha_cn.panel"):
        assert not graph.direct_import_exists(
            importer="openalpha_cn.storage.factor_experiments", imported=forbidden
        ), f"storage/factor_experiments.py must not import {forbidden}"


def test_every_factor_view_fault_has_a_row_in_both_channel_tables() -> None:
    """Every `FactorViewError` subclass's `reason` is a key of both envelope tables.

    `_factor_refusal` looks its status up by `error.reason` and `_factor_fail` looks its exit code
    up the same way, which is what keeps a fault from being enveloped as whichever branch an
    `isinstance` chain happened to end on. The price is that a subclass added with no row raises
    `KeyError` at that boundary; this is what makes the `KeyError` unreachable in practice, and it
    reads the live class hierarchy so a fourth subclass arrives red rather than unguarded.

    The base class's own `reason` is deliberately **not** required to have a row: it is never
    raised, and giving it one would invite a future subclass to inherit an envelope instead of
    choosing one.
    """
    subclasses = {subclass.reason for subclass in FactorViewError.__subclasses__()}

    assert subclasses == {"bad_request", "panel_unreadable", "blocked"}
    assert subclasses <= set(FACTOR_HTTP_STATUS)
    assert subclasses <= set(FACTOR_EXIT)
    assert FactorRequestError.reason == "bad_request"
    assert FactorPanelUnreadableError.reason == "panel_unreadable"
    assert FactorRunBlockedError.reason == "blocked"
    assert FactorViewError.reason not in FACTOR_HTTP_STATUS


def test_the_two_channel_tables_are_siblings_and_not_twins() -> None:
    """The rows that do not correspond, written down rather than discovered.

    `PANEL_HTTP_STATUS` and `PanelExit` have exactly one row that does not correspond and say so;
    these two have two, and both are deliberate:

    - **`not_found`** exists only on HTTP, because only HTTP has a route whose path names a
      resource. A run never answers it -- a run that found nothing to read is a statement about
      the panel -- and the command line has no `factor get`.
    - **`blocked`, `panel_unreadable` and `conflict` are three HTTP rows and one exit code.** All
      three are `409` and all three are exit `1`, so neither channel can tell all three apart on
      the envelope alone; `detail.reason` and the message are what separate them. That is the
      same arrangement `PANEL_HTTP_STATUS`' two `409`s already have, and it is recorded because a
      client switching on the status code alone would otherwise think it had.
    """
    assert set(FACTOR_HTTP_STATUS) - set(FACTOR_EXIT) == {"not_found"}
    assert set(FACTOR_EXIT) - set(FACTOR_HTTP_STATUS) == set()
    assert (
        FACTOR_HTTP_STATUS["blocked"]
        == FACTOR_HTTP_STATUS["panel_unreadable"]
        == FACTOR_HTTP_STATUS["conflict"]
        == 409
    )
    assert (
        FACTOR_EXIT["blocked"]
        == FACTOR_EXIT["panel_unreadable"]
        == FACTOR_EXIT["conflict"]
        == PanelExit.unhealthy
    )
    assert FACTOR_EXIT["answered"] == PanelExit.ok
    assert FACTOR_EXIT["bad_request"] == PanelExit.bad_request
    assert FACTOR_EXIT["internal_error"] == PanelExit.internal_error
    # 2 stays reserved for click's own UsageError on this command too.
    assert PanelExit.bad_request != 2
    assert 2 not in {int(code) for code in FACTOR_EXIT.values()}
