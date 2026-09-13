"""A throwaway copy of `src/openalpha_cn` under a test's `tmp_path`, and `lint-imports` run over it.

D13 I-D. The tests that prove a layering contract is live used to plant their violation in this
checkout -- rewrite `backtest/candidate_ranking.py` with one more import, or create
`domain/_layering_gate_probe.py` -- run the linter, and put the tree back in `finally`. Putting it
back was `Path.write_text`, which writes CRLF on Windows, so both Windows legs of CI left four
tracked modules changed after every run; a run killed before its `finally` would have left the
violation in `src/` for the next one; and anything else importing from the checkout meanwhile saw
the planted import. The violation is planted in a copy instead, and this checkout is not written.

**How the copy, and not the checkout, is what gets linted.** `grimp` finds the root package with
`importlib.util.find_spec` (`grimp/adaptors/packagefinder.py`), which answers with the module this
process has already imported -- the checkout's -- whatever `sys.path` says. So the linter runs in a
child process, through the `lint-imports` console script CI runs, from the copy's `src/`:
`importlinter.cli.lint_imports` puts its working directory at the front of `sys.path`, and the
copy's `src/` also leads `PYTHONPATH`, ahead of the editable install's `.pth` line naming this
checkout's `src/`. `lint_copy` does not take that on trust. Before it lints, it asks the same
interpreter, from the same directory and with the same environment, where `openalpha_cn`
resolves, and refuses to answer unless the answer is the copy -- because a planted violation the
linter never read looks exactly like a contract that stayed green.

The child is its own process, so importlinter's `dictConfig` cannot reach this process's loggers;
`tests/import_linter_containment.py` is for the linter run in this process, which the checks over
the real tree still do. `--no-cache`, and a working directory inside the copy, so neither the
linter's cache nor grimp's is written near this checkout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
PACKAGE: Final[str] = "openalpha_cn"

_WHERE_THE_PACKAGE_RESOLVES: Final[str] = (
    "import importlib.util, os, sys\n"
    "sys.path.insert(0, os.getcwd())\n"
    f"print(importlib.util.find_spec({PACKAGE!r}).origin)\n"
)
"""The lookup grimp makes, preceded by the `sys.path` insertion `lint_imports` makes first."""


@dataclass(frozen=True)
class LintAnswer:
    """What `lint-imports` said about a copy."""

    exit_code: int
    report: str
    """Its output with every run of whitespace collapsed to one space, so an edge the console
    wrapped onto two lines still reads as one."""


def copy_package(destination: Path) -> Path:
    """Copy `src/openalpha_cn` to `destination/src/openalpha_cn`; return the copy's package dir."""
    package = destination / "src" / PACKAGE
    shutil.copytree(
        ROOT / "src" / PACKAGE,
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return package


def lint_copy(package: Path, *contracts: str) -> LintAnswer:
    """`lint-imports --no-cache` over a `copy_package` result, limited to `contracts` if any."""
    source_root = package.parent
    if source_root.resolve().is_relative_to(ROOT):
        raise ValueError(f"{package} is inside this checkout; lint a copy_package() result")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(source_root), *filter(None, [os.environ.get("PYTHONPATH")])]
        ),
    }

    resolved = subprocess.run(
        [sys.executable, "-c", _WHERE_THE_PACKAGE_RESOLVES],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    origin = Path(resolved.stdout.strip())
    if origin.parent.resolve() != package.resolve():
        raise AssertionError(
            f"{PACKAGE} resolves to {origin} in the linter's environment, not to the copy at "
            f"{package}; a lint from there would read this checkout and prove nothing"
        )

    executable = shutil.which("lint-imports", path=os.path.dirname(sys.executable))
    if executable is None:
        raise AssertionError(f"no lint-imports console script beside {sys.executable}")
    finished = subprocess.run(
        [
            executable,
            "--config",
            str(ROOT / "pyproject.toml"),
            "--no-cache",
            *(f"--contract={contract}" for contract in contracts),
        ],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return LintAnswer(
        exit_code=finished.returncode,
        report=" ".join((finished.stdout + finished.stderr).split()),
    )
