"""Every Markdown table row carries the number of cells its header declares (`V2-P5-070`).

A row with *more* cells than its header does not overflow -- GitHub drops the excess silently,
so the last column simply stops being rendered. Measured against GitHub's own renderer rather
than against a reading of the spec:

    input   | A | B |
            |---|---|
            | one | two | THREE |
    output  <td>one</td> <td>two</td>          # THREE is gone

The two ways a row gets an extra cell are worth naming separately, because only one of them
looks like a mistake in the source:

* the header is short -- the roadmap's P4 table declared six columns while 92 of its 114 rows
  carried seven (`ID | 标题 | 类型 | 依赖 | 说明 | 验收 | PRD`), so every one of those rows had
  its `PRD` reference dropped from the page; and
* an unescaped `|` inside inline code, which splits the cell **and** breaks the code span,
  again measured on the real renderer:

    input   | `x|y` | z |
    output  <td>`x</td> <td>y`</td>            # not code, and two cells

  `V2-P4-015` and `V2-P4-022` were repaired once by escaping the pipe as `\\|`; this audit is
  what stops the next one going unnoticed for another phase.

A row with *fewer* cells is padded with an empty one at the end (also measured), which is why
a row that omits an optional middle column has to spell it as an empty cell rather than leave
it out -- otherwise its last value lands in the wrong column.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

SCANNED: Final[tuple[Path, ...]] = (
    *sorted((ROOT / "docs").rglob("*.md")),
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "CHANGELOG.md",
)

SEPARATOR_ROW: Final[re.Pattern[str]] = re.compile(r"\|[\s:\-|]+\|")
UNESCAPED_PIPE: Final[re.Pattern[str]] = re.compile(r"(?<!\\)\|")
"""`\\|` is the only escape GitHub honours, and it honours it everywhere -- inline code
included, which is exactly where this repository keeps losing cells."""


def _cell_count(line: str) -> int:
    body = line.strip()
    body = body.removeprefix("|")
    body = body.removesuffix("|") if body.endswith("|") and not body.endswith("\\|") else body
    return len(UNESCAPED_PIPE.split(body))


def _mismatched_rows(path: Path) -> list[str]:
    """`path:line got N, header declares M` for every row that does not fit its table."""
    lines = path.read_text(encoding="utf-8").split("\n")
    offenders: list[str] = []
    index = 0
    while index < len(lines):
        separator = SEPARATOR_ROW.fullmatch(lines[index].strip())
        if not (separator and index and lines[index - 1].strip().startswith("|")):
            index += 1
            continue

        declared = _cell_count(lines[index - 1])
        index += 1
        while index < len(lines) and lines[index].strip().startswith("|"):
            found = _cell_count(lines[index])
            if found != declared:
                offenders.append(
                    f"{path.relative_to(ROOT).as_posix()}:{index + 1} has {found} cells, "
                    f"header declares {declared}"
                )
            index += 1
    return offenders


def test_every_documentation_table_row_fits_the_header_it_is_under() -> None:
    """The audit. A row that does not fit loses its last column, silently, on the page."""
    offenders = [line for path in SCANNED if path.exists() for line in _mismatched_rows(path)]

    assert offenders == [], (
        f"{len(offenders)} table rows do not carry their header's cell count; each one loses "
        f"its trailing column when rendered:\n  " + "\n  ".join(offenders[:20])
    )


def test_an_escaped_pipe_does_not_split_a_cell() -> None:
    """The counting rule this audit rests on, asserted rather than assumed."""
    assert _cell_count(r"| a\|b | c |") == 2
    assert _cell_count("| a|b | c |") == 3
