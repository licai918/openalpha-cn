"""Central seeding entry point: the one place `random_seed` actually takes effect.

Before this module existed, `random_seed` was carried faithfully through every layer --
`ResearchRunRequest` (`runtime/contracts.py`) -> `ResearchEngine.run_cycle`
(`runtime/engine.py`) -> `RunManifest` (`domain/run.py`) -- and never once read to seed
anything. Nothing in the engine is stochastic today: the baseline agents are
deterministic arithmetic, and `backtest/event_study.py`'s own bootstrap seeds from its
own, separate `EventStudyRequest.random_seed` field via a local `random.Random(...)`
instance it owns completely -- calling this module's `seed_everything()` never touches
that instance's state, since it only reseeds the *global* `random` module. So this
module's job, per the task-17 brief, is to build and prove the seeding *mechanism*,
honestly, without inventing a consumer just to make it look used:

- `register_random_source()` / `seed_everything()` is a small, generic, pluggable
  registry: `seed_everything(seed)` calls every registered source's own reseed function
  with `seed`. Stdlib `random` registers itself below, at import time, unconditionally.
  `numpy.random`, when numpy is importable, registers itself too -- guarded by an
  optional import, since numpy is not a runtime dependency of this package yet
  (ADR-0003); P4 is the phase that will actually consume it.
- `ResearchEngine.run_cycle` calls `seed_everything(request.random_seed)` once, at the
  same deterministic boundary every other per-run input is threaded through, so
  `random_seed` genuinely reaches "any registered random source" for this run. This is
  proven by `tests/unit/runtime/test_seeding.py` (the registry itself: same seed -> same
  sequence, different seed -> different sequence, for stdlib `random` and for a
  test-registered fake source) and `tests/unit/runtime/test_engine_seeding.py` (the
  wiring: `run_cycle` really calls `seed_everything` with `request.random_seed`).
- `seed_everything()` also pins every BLAS/OpenMP thread-count environment variable
  ADR-0003 names (`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
  `VECLIB_MAXIMUM_THREADS`, `NUMEXPR_NUM_THREADS`) to `"1"`, and sets `PYTHONHASHSEED`
  to the given seed. Neither has any observable effect today: no BLAS library is ever
  imported, and `PYTHONHASHSEED` cannot change hash randomization for the
  *already-running* interpreter -- a well-known CPython limitation, since it is only
  read once, at process start. Both are still mechanically in place now, ahead of P4,
  exactly as ADR-0003 asks ("the numerical stack's introduction must pin thread counts
  in the same change"): pinning happens *before* the numerical stack exists, so P4 does
  not have to remember to add it. `PYTHONHASHSEED` is set purely for a subprocess or
  worker spawned *after* this call, which inherits `os.environ` at spawn time.

No production component consumes `random_seed` today. This module makes that statement
literally true and testable, rather than aspirational: `seed_everything` has exactly one
registered source in the default (no-numpy) environment -- stdlib `random` -- and
nothing in `agents/baseline.py` calls `random` at all.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable

__all__ = ["register_random_source", "seed_everything"]

RandomSeeder = Callable[[int], None]

# ADR-0003's determinism hazard: BLAS/OpenMP floating-point reduction order changes
# with thread count unless these are pinned. The numerical stack does not exist yet,
# so pinning these has no observable effect today -- see the module docstring.
_BLAS_THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

_registered_sources: dict[str, RandomSeeder] = {}


def register_random_source(name: str, seeder: RandomSeeder) -> None:
    """Register a `seed(int) -> None` callable under `name`.

    Every future call to `seed_everything()` invokes every currently registered
    seeder. Registering an already-used `name` replaces its seeder.
    """
    _registered_sources[name] = seeder


def seed_everything(seed: int) -> None:
    """Seed every registered random source and pin BLAS/OpenMP thread counts.

    Called once per run, at `ResearchEngine.run_cycle`'s deterministic boundary, with
    `request.random_seed`. Idempotent and side-effect-safe to call repeatedly with the
    same seed.
    """
    for pinned_var in _BLAS_THREAD_ENV_VARS:
        os.environ[pinned_var] = "1"
    os.environ["PYTHONHASHSEED"] = str(seed)
    for seeder in _registered_sources.values():
        seeder(seed)


register_random_source("random", random.seed)

try:
    import numpy as _numpy
except ImportError:
    pass
else:
    register_random_source("numpy.random", _numpy.random.seed)
