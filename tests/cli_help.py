"""`--help` rendered as text, for the assertions that read it (`V2-P5-057`).

Six test modules asked the CLI for `--help` and matched a sentence against what came back, and
each of them wrote its own undoing of Rich's rendering: two collapsed whitespace, three also
replaced the option table's `│`, one widened `COLUMNS`, and one joined without a separator
because a limitation code is a single token Rich breaks across lines. Not one of them removed
ANSI. That is the whole of `V2-P5-057`: on a terminal Rich colours `--help`, the escape
sequences land *inside* the sentence, and a substring that reads plainly to a human stops
matching. Locally nothing colours, so all six passed; CI sets `FORCE_COLOR` and five of them
went red the first time this repository's suite ever ran there.

So the two things that are purely about *undoing presentation* live here, once:

* ANSI is stripped, because colour is not part of what any of those tests is asserting; and
* the option table's box rule becomes a space, because a wrapped option help carries a `│`
  between its lines and collapsing whitespace alone would not rejoin the sentence.

What is *not* done here is the join. `test_model_interfaces.py` matches a limitation code with
`"".join(...)` and its neighbours match prose with `" ".join(...)`; those two are claims about
what is being matched, not about the renderer, and a helper that picked one for every caller
would have quietly broken the other. Callers collapse the returned text themselves.

`COLUMNS` is set wide for the same reason ANSI is stripped -- so the text a caller matches does
not depend on the terminal the suite happens to run in -- and not as a substitute for the box
handling above: a wide terminal makes wrapping rare, never impossible.
"""

from __future__ import annotations

import re
from typing import Final

from typer.testing import CliRunner

from openalpha_cn.cli import app

ANSI_ESCAPE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
"""Every CSI sequence, not only the `m` (colour) ones Rich happens to emit today.

Matching `\\x1b\\[[0-9;]*m` alone would leave cursor and erase sequences in the text, and the
defect this module exists for is precisely a sequence nobody expected landing mid-sentence.
"""

HELP_COLUMNS: Final[str] = "200"


def rendered_help(*command: str) -> str:
    """`openalpha <command> --help` as text: no colour, no box rule, wrapping left as whitespace.

    The exit code is asserted here rather than by each caller: a `--help` that does not exit `0`
    is a defect on every one of these paths, and a caller that forgot to check it would match its
    sentence against an empty string and pass for the wrong reason.
    """
    result = CliRunner().invoke(app, [*command, "--help"], env={"COLUMNS": HELP_COLUMNS})

    assert result.exit_code == 0, (
        f"`{' '.join(command)} --help` exited {result.exit_code}: {result.output}"
    )
    return ANSI_ESCAPE.sub("", result.output).replace("│", " ")
