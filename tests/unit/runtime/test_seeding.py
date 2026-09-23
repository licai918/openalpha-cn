"""The random-seed registry: proves the mechanism, not a fabricated consumer.

Before this module existed, `random_seed` was carried faithfully through every layer
(`ResearchRunRequest` -> `ResearchEngine.run_cycle` -> `RunManifest`) and never once read
to seed anything. Nothing in the engine is stochastic today -- the baseline agents are
deterministic arithmetic -- so this suite proves the seeding *mechanism* itself: given the
same seed, every registered random source produces the same sequence; a different seed
produces a different one. `tests/unit/runtime/test_engine_seeding.py` is the companion
proof that `ResearchEngine.run_cycle` actually calls this with `request.random_seed`.
"""

from __future__ import annotations

import os
import random
from collections.abc import Iterator

import pytest

from openalpha_cn.runtime import seeding
from openalpha_cn.runtime.seeding import register_random_source, seed_everything

_BLAS_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


@pytest.fixture(autouse=True)
def _reset_registered_sources() -> Iterator[None]:
    """Restore the module-level registry after every test so a test-only fake source
    registered here never leaks into another test file or the real engine."""
    original = dict(seeding._registered_sources)
    yield
    seeding._registered_sources.clear()
    seeding._registered_sources.update(original)


def test_seed_everything_reseeds_stdlib_random_deterministically() -> None:
    seed_everything(42)
    first = [random.random() for _ in range(5)]
    seed_everything(42)
    second = [random.random() for _ in range(5)]
    seed_everything(43)
    third = [random.random() for _ in range(5)]

    assert first == second
    assert first != third


def test_register_random_source_makes_seed_everything_reach_any_registered_source() -> None:
    """Proves the registry is generic without depending on numpy being installed: a
    test-only fake random source is registered exactly like the real ones, and
    `seed_everything` must reach it too -- the brief's "any registered random source"
    property, made concrete."""
    seen: list[int] = []
    register_random_source("fake-source", seen.append)

    seed_everything(123)

    assert seen == [123]

    seed_everything(456)

    assert seen == [123, 456]


def test_stdlib_random_is_registered_by_default() -> None:
    assert "random" in seeding._registered_sources


def test_seed_everything_pins_every_blas_thread_env_var_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _BLAS_THREAD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    seed_everything(1)

    for name in _BLAS_THREAD_ENV_VARS:
        assert os.environ[name] == "1"


def test_seed_everything_sets_pythonhashseed_for_processes_spawned_afterward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PYTHONHASHSEED` cannot change hash randomization for the interpreter already
    running this test -- CPython only reads it once, at process start. Setting it here
    is for a subprocess or worker spawned *after* this call, which inherits `os.environ`
    at spawn time -- documented explicitly so this isn't mistaken for a live effect on
    the current process."""
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)

    seed_everything(987)

    assert os.environ["PYTHONHASHSEED"] == "987"


def test_seed_everything_seeds_numpy_random_when_numpy_is_installed() -> None:
    """Guarded: numpy is not a runtime dependency of this package yet (ADR-0003) and is
    not installed in this project's default dev environment. This only exercises
    anything in a numpy-equipped environment (e.g. CI's `uv sync --all-extras`); it is
    skipped, not failed, everywhere else."""
    numpy = pytest.importorskip("numpy")

    seed_everything(7)
    first = numpy.random.rand(3).tolist()
    seed_everything(7)
    second = numpy.random.rand(3).tolist()

    assert first == second
