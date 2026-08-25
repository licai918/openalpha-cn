"""The modes one research cycle can run in -- declared once (`V2-P4-001`, `V2-P4-003`).

`V2-P4-001` adds `paper` and `daily` to a set that was written out **three times**: as a
`Literal` on `RunManifest.mode` (`domain/run.py`), again as a `Literal` on
`ResearchRunRequest.mode` (`domain/run_request.py`, where `runtime/engine.py` used to hold
it), and a third time as a `StrEnum` of Typer choices in `cli.py`. `V2-P4-003` exists
because editing two of the three leaves the suite green: the CLI would keep refusing
`--mode paper` while both contracts accepted it, and nothing in the repository compared the
three lists.

**This module is that issue's fix, made by construction rather than by assertion.** There
is one declaration -- the enum below -- and the two contracts and the CLI all name it, so
"two of three" is no longer a reachable state and a test that compares three lists has
nothing left to compare. `V2-P4-003`'s suggested remedy (a test asserting the three agree)
would have kept the three declarations and added a fourth thing to maintain; the roadmap
entry is satisfied here instead, and
`tests/unit/domain/test_run_mode.py::test_every_declared_mode_reaches_both_contracts_and_the_cli`
holds the *single source* property itself -- that the contracts and the CLI accept exactly
this enum's members -- so a future contributor who reintroduces a hand-written list is the
one who goes red.

## Why a `StrEnum` and not a `Literal` alias

A `Literal` alias would keep the published JSON Schema shape (`"enum": [...]` inline on the
property) and could be shared by the two contracts, but `cli.py` needs a real `Enum` class
for Typer to render and validate `--mode`, so a `Literal` alias leaves the third
declaration standing. A `StrEnum` serves all three, and it costs nothing where it matters:
`model_dump(mode="json")` emits the member's **value**, so `{"mode": "live"}` is byte-for-byte
what it always was. That is load-bearing rather than incidental -- `RunManifest`'s stored
payload and `ResearchEngine._load_or_start_recovery`'s `request_digest` are both canonical
JSON over these dumps, so a representation change here would have moved a stored digest
while claiming to only widen a set
(`tests/unit/domain/test_run_mode.py::test_the_enum_serialises_to_the_bare_string_the_literal_did`).

The only visible change is in the generated schema: `mode` is now a `$ref` into `$defs`
rather than an inline `enum`. `docs/api/schemas/run-manifest-v3.json` carries the whole set
either way, and `web/src/types.ts` has never mirrored `RunManifest.mode`.

## What the two new modes mean, and what this module deliberately does not decide

`paper` is a forward simulation that never reaches a broker (PRD S57 / D19, `V2-P5-004`);
`daily` is the scheduled production cycle (`V2-P4-021`'s `daily-run`). Neither has runtime
behaviour attached yet -- this issue is the contract window, not the feature -- so nothing
here branches on a mode, and nothing should: a mode that changes what the engine *does*
belongs at the call site that does it, not in the enum that names it.
"""

from enum import StrEnum
from typing import Final


class RunMode(StrEnum):
    """The declared modes of one shared research cycle.

    Members are ordered oldest-first rather than alphabetically, so the two `V2-P4-001`
    additions read as additions. Order is not semantic: nothing sorts or compares modes,
    and `RunMode` deliberately does not override `__bool__` or define an ordering, because
    no mode is an "off" value and no mode is greater than another.
    """

    live = "live"
    """A real research cycle against point-in-time evidence."""

    replay = "replay"
    """A frozen corpus re-run, for determinism proofs (`backtest/replay.py`)."""

    backtest = "backtest"
    """A historical evaluation cycle."""

    paper = "paper"
    """A forward simulation that never reaches a broker (`V2-P4-001`; PRD S57, D19)."""

    daily = "daily"
    """The scheduled production cycle (`V2-P4-001`; PRD S5, `V2-P4-021`'s `daily-run`)."""


RUN_MODES: Final[tuple[RunMode, ...]] = tuple(RunMode)
"""Every declared mode, in declaration order.

Derived from the enum rather than restated, so it cannot disagree with it. Exists for the
audits that need to iterate the set without importing `enum` machinery at the call site.
"""
