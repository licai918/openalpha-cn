"""The fixtures the online chain runs on. Everything they are made of is in `e2e_support.py`,
including the design note for this whole subtree -- read that first.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from e2e_support import (
    BUILD_TARGETS,
    EXCHANGE,
    RUNTIME_DIR_VARIABLE,
    SESSION_SCOPED_DATASETS,
    BuiltPanel,
    CLIResult,
    E2EEnvironmentError,
    attempt_build,
    build_year,
    catalogued_path,
    require_opt_in,
    require_the_report_matches_what_landed,
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
