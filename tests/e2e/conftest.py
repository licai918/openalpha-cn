"""The fixtures the online chain runs on. Everything they are made of is in `e2e_support.py`,
including the design note for this whole subtree -- read that first.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest
from e2e_support import (
    BUILD_TARGETS,
    EXCHANGE,
    RUNTIME_DIR_VARIABLE,
    SESSION_SCOPED_DATASETS,
    BuiltPanel,
    CLIResult,
    E2EEnvironmentError,
    InjectPartition,
    MutatePartition,
    StoredPartition,
    attempt_build,
    build_year,
    catalogued_path,
    console_script,
    read_stored_partition,
    require_opt_in,
    require_the_report_matches_what_landed,
    rewrite_partition,
    run_build,
)

from openalpha_cn.panel_view import panel_store


@pytest.fixture(scope="session")
def built_panel(tmp_path_factory: pytest.TempPathFactory) -> BuiltPanel:
    """A real panel: either one an earlier run built, or one built here from live rows.

    Session-scoped because building is measured in tens of minutes (see `e2e_support.py`'s
    "Cost"). The defect-injection tests mutate the panel and restore it rather than taking a
    copy, so that a `price` partition of roughly a million rows is neither copied nor rebuilt
    once per test.
    """
    require_opt_in()
    workspace = tmp_path_factory.mktemp("e2e-cwd")

    reused = os.environ.get(RUNTIME_DIR_VARIABLE)
    if reused:
        runtime_dir = Path(reused)
        store = panel_store(runtime_dir)
        years = sorted(
            {
                year
                for dataset in SESSION_SCOPED_DATASETS
                for year in store.registered_years(dataset)
            }
        )
        if not years:
            raise E2EEnvironmentError(
                f"{RUNTIME_DIR_VARIABLE}={reused} holds no partition for any of "
                f"{list(SESSION_SCOPED_DATASETS)}; unset it to build one"
            )
        return BuiltPanel(
            runtime_dir=runtime_dir, year=max(years), exchange=EXCHANGE, build_reports=None
        )

    runtime_dir = tmp_path_factory.mktemp("e2e-runtime")
    # One instant for the whole build, read here and passed to every `panel build` as `--as-of`.
    # The five targets are five invocations spanning tens of minutes, and each would otherwise
    # bound its own session loop at its own clock -- which is how this suite's first run produced
    # a panel whose datasets stopped on different days. `_refuse_split_horizon` would now refuse
    # such a build outright; pinning it is the other half of that fix and the half that lets the
    # build succeed rather than merely fail loudly.
    as_of = datetime.now(UTC)
    year = build_year(workspace, runtime_dir, as_of)
    reports: dict[str, Any] = {}
    waiver: CLIResult | None = None
    for target in BUILD_TARGETS:
        if target == "trade_cal":
            continue
        if target == "price":
            # The one build this fixture is allowed to retry, and the retry is recorded rather
            # than hidden. `panel build --dataset price` defaults to `--halts`, which reads the
            # year's halt corpus back to give `write_daily_panel`'s explained-share guard
            # something real to check against; on live 2026 rows that read raises (see
            # `test_the_price_build_completes_with_the_halt_guard_it_defaults_to`). Falling
            # back to `--no-halts` is what lets the other twenty tests examine a panel at all,
            # and `halt_waiver` is what stops that fallback from being a silent pass.
            attempt = attempt_build(workspace, runtime_dir, target=target, year=year, as_of=as_of)
            if attempt.exit_code == 0:
                reports[target] = attempt.payload()
                continue
            waiver = attempt
            reports[target] = run_build(
                workspace,
                runtime_dir,
                target=target,
                year=year,
                as_of=as_of,
                extra=("--no-halts",),
            )
            continue
        reports[target] = run_build(workspace, runtime_dir, target=target, year=year, as_of=as_of)
    require_the_report_matches_what_landed(panel_store(runtime_dir), reports, year=year)
    return BuiltPanel(
        runtime_dir=runtime_dir,
        year=year,
        exchange=EXCHANGE,
        build_reports=reports,
        halt_waiver=waiver,
    )


@pytest.fixture
def cli_workspace(tmp_path: Path) -> Path:
    """A working directory with no `.env` in it. See `e2e_support.run_cli`."""
    return tmp_path


@pytest.fixture
def served(built_panel: BuiltPanel, cli_workspace: Path) -> Iterator[str]:
    """A real `openalpha serve` process over the built panel, and its base URL.

    A subprocess rather than `TestClient`: `serve` resolves its own configuration, binds a
    socket and runs uvicorn, and none of that is exercised by an ASGI transport. The port is
    taken from the operating system and released before uvicorn binds it, which is the usual
    small race; a bind failure surfaces as the readiness poll timing out with the child's own
    stderr attached.

    In `conftest.py` rather than beside the tests that first used it because two modules now
    need one HTTP face over one panel -- the chain's and `test_pit_injection_online.py`'s --
    and a second copy of a fixture that spawns a server is a second thing to keep in step.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = os.environ.copy()
    environment["OPENALPHA_RUNTIME_DIR"] = str(built_panel.runtime_dir)
    # uvicorn's own log goes to a file rather than to a pipe nobody drains: a `PIPE` that fills
    # its 64 KiB buffer blocks the writing process, so an access log long enough to fill it
    # would wedge the very server this fixture is waiting on.
    log = cli_workspace / "serve.log"
    with log.open("w", encoding="utf-8") as sink:
        child = subprocess.Popen(
            [*console_script(), "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=cli_workspace,
            env=environment,
            stdout=sink,
            stderr=subprocess.STDOUT,
            text=True,
        )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(
                    f"`openalpha serve` exited {child.returncode} before it answered: "
                    f"{log.read_text(encoding='utf-8')[:2000]}"
                )
            try:
                with urlopen(f"{base}/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        else:  # pragma: no cover - a server that never binds is the finding
            raise AssertionError(
                "`openalpha serve` did not answer /health within 60s: "
                f"{log.read_text(encoding='utf-8')[:2000]}"
            )
        yield base
    finally:
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged child
            child.kill()


@pytest.fixture
def withheld_partition(built_panel: BuiltPanel) -> Iterator[Callable[..., Path]]:
    """Take one Parquet file out from under the catalog, and put it back afterwards.

    A move rather than a copy of the whole panel: the `daily` partition is around a million
    rows, and duplicating it once per defect test would cost more than the fetch that produced
    it. The `finally` restores the exact bytes, so a failure inside a test cannot leave the
    session-scoped panel broken for the tests that follow.

    `replacement` is what separates the defects the catalog distinguishes: `None` deletes the
    file (`partition_file_missing`), and bytes that are not Parquet leave one that cannot be
    opened (`partition_file_unreadable`).
    """
    taken: list[tuple[Path, Path]] = []

    def _withhold(dataset: str, year: int, *, replacement: bytes | None = None) -> Path:
        target = catalogued_path(built_panel.store, dataset=dataset, year=year)
        moved = target.with_name(target.name + ".withheld")
        shutil.move(str(target), str(moved))
        taken.append((target, moved))
        if replacement is not None:
            target.write_bytes(replacement)
        return target

    try:
        yield _withhold
    finally:
        for target, moved in reversed(taken):
            target.unlink(missing_ok=True)
            shutil.move(str(moved), str(target))


@pytest.fixture
def injected_partition(built_panel: BuiltPanel) -> Iterator[InjectPartition]:
    """Rewrite one real partition with rows injected into it, and put the build's own back.

    The counterpart of `withheld_partition` one level down: that one takes a *file* away from
    the catalog, this one changes what the rows say and makes the catalog agree, which is the
    only way an injected row is visible to `evaluate_readiness` at all.

    Restoration writes the partition the build produced back through the same writer, so the
    Parquet file, the catalog's coverage census and its content hash are all recomputed rather
    than patched -- `test_restoring_a_rewritten_partition_reproduces_the_catalog_row_the_build_
    wrote` is the assertion that this really does return the panel to where it started, and it
    is what lets the session-scoped panel survive an injection for the tests that follow.
    """
    store = built_panel.store
    originals: list[StoredPartition] = []

    def _inject(dataset: str, year: int, mutate: MutatePartition, fetched_at: datetime) -> None:
        original = read_stored_partition(store, dataset=dataset, year=year)
        originals.append(original)
        rewrite_partition(store, mutate(original), as_of=fetched_at)

    try:
        yield _inject
    finally:
        for original in reversed(originals):
            rewrite_partition(
                store,
                original,
                as_of=original.coverage.as_of,
                fetched_at=original.coverage.fetched_at,
            )
