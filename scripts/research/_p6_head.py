"""The checkout's HEAD as `p6.py` starts, read before any heavy import (`V2-P6-023`).

`p6.py` imports this module before `grid`, `registry` and `openalpha_cn` -- about a third of a
second of imports -- so a commit that lands while the driver is still importing is a commit the
run did not start at, and `p6.main` refuses it (`CheckoutMovedError`) rather than recording it.
It imports the standard library only, and asks git exactly what
`openalpha_cn.runtime.provenance.resolve_code_commit` asks first: `git rev-parse HEAD` anchored
at the driver's own directory. `None` when git has no answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ANCHOR = Path(__file__).resolve().parent
"""Where the driver lives: git walks up from here to the checkout, as `resolve_code_commit`
does from the anchor `p6.py` hands it."""


def read_head(anchor: Path) -> str | None:
    """`git rev-parse HEAD` at `anchor`, or `None` when git is absent, fails or hangs."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=anchor,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head = result.stdout.strip()
    return head if result.returncode == 0 and head else None


HEAD_AT_START = read_head(ANCHOR)
