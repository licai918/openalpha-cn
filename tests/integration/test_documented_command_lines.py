"""Every `openalpha` command line the READMEs and `docs/` print, resolved and run (`V2-P5-048`).

`V2-P4-094` built the same shape one channel over: it parses the command lines out of
`openalpha model --help` and executes them, because "a `--help` example that has not been run is
a claim like any other". The READMEs are the same kind of claim and had never been run. Four
defects were found by doing it, and three of them were **already-fixed defects that had been
fixed only in the help text**:

1. **The factor workflow's steps 3 and 4 are printed as consecutive and are not.** Step 3 is
   `factor build --tier processed`, which writes `raw` and `processed`; step 4 passes
   `--neutralization industry_and_size/v1`, which reads the `neutralized` tier that step 3 never
   wrote. Measured: step 3 exits `0`, step 4 exits `1` with `No neutralized partition of this
   factor is registered in this panel at all`. Step 3 has to run **twice**, once per tier.
2. **The refusal in (1) prints a remedy that does not run either.** `Build it first: openalpha
   factor build --factor reversal_1d/v1 --tier neutralized --year <year>` exits `3` --
   `--neutralization is required for --tier neutralized`. A refusal naming a command the reader
   cannot copy is a second defect wearing the first one's clothes, and only executing it finds
   it.
3. **`model evaluate`'s and `daily-run`'s README examples were `V2-P4-094`'s own broken ones.**
   That row changed the two printed by `--help` and left the three copies in `README.md`,
   `README.en.md` and `docs/HANDOFF_CURRENT.md` exactly as they were: `--horizon 5d` (which
   purges the first fold's training set to nothing) and `--as-of 2026-01-20T04:00:00+00:00`
   (which every 2026 panel refuses), with `daily-run` omitting `--as-of` altogether.
4. **`docs/HANDOFF_CURRENT.md` still carries `--waive-max-staleness`,** which `V2-P4-100`
   removed from `factor build --help` after measuring that it exits `1`.

Three of those four are one failure repeated: **a fix applied to the source of an example and
not to its copies.** That is what this file is against, and why it reads the documents rather
than holding its own copy of anything.

## What this file can see, and what it cannot

**It sees**: every line in a fenced block of `README.md`, `README.en.md` or `docs/**/*.md` that
begins `openalpha ` or `uv run openalpha `, with shell and PowerShell continuations joined.
`DOCUMENTED_COMMANDS` is an equality over the command each line names, so a documented command
that is neither executed nor declared unrunnable is red and names itself.

**It cannot see, and says so per line in `NOT_EXECUTED`**: anything reaching the paid Tushare
provider (`panel build` -- there is no offline path to a panel at all, which is why the fixture
below imports the test tree), anything starting a server (`serve`), anything probing this
machine's credentials (`doctor`), and every line whose arguments are illustrative placeholders
(`sla_0123…`, `sig_0123…`, `prd_0123…`) rather than addresses any store holds. Those last are
still parsed, so a flag renamed under them is caught; they are simply not run.

**It also cannot see prose.** A command that runs perfectly while the paragraph beside it
describes something else is invisible here, and the equivalence claim `V2-P5-047` corrected was
exactly that shape.

## The obstacle this fixture is evidence of

`openalpha panel build` goes exclusively through the paid Tushare provider, so there is **no
offline path to a panel**. Getting one costs the four imports below -- `sys.path` into `tests/`,
then `generate_panel` and `write_generated_panel` -- which is not a thing a reader of the README
can do, and is the single largest obstacle to using this product from the documentation alone.
Recorded here rather than in a report, because this file is the place where the cost is actually
paid.
"""

from __future__ import annotations

import re
import shlex
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, NamedTuple

import click
import pytest
from panel_fixtures import (
    EXCHANGE,
    LAST_DAY,
    WINDOW_FIRST,
    generate_panel,
    write_generated_panel,
)
from typer.main import get_command
from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.panel.store import PanelStore

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

FENCE: Final[re.Pattern[str]] = re.compile(r"^```(\w*)\s*$")
INVOCATION: Final[re.Pattern[str]] = re.compile(r"^(?:uv run )?openalpha ")
ELISION: Final[tuple[str, ...]] = ("...", "…")

runner = CliRunner()


EXECUTED: Final[frozenset[str]] = frozenset(
    {
        "factor list",
        "factor describe",
        "factor build",
        "factor run",
        "model evaluate",
        "model daily-run",
        "model predictions",
    }
)
"""The commands whose documented lines are executed verbatim and must exit `0`.

Every one of them is reachable from a generated panel with no credential and no network, which
is the whole criterion -- this set is not a judgement about which commands matter, it is the
measured subset a test can actually drive.
"""


NOT_EXECUTED: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "panel build": "reaches the paid Tushare provider; there is no offline path to a panel, "
        "which is what this file's own fixture has to work around",
        "panel doctor": "answers about a partition at a stated `as_of`, and the documented line "
        "leaves it to the wall clock -- correct against a panel built up to today, refused "
        "against the year-keyed fixture here, so an exit code asserted on it would measure the "
        "fixture's build window rather than the line",
        "data-check": "`panel doctor`'s reason exactly; the gate reads the same partitions at "
        "the same wall clock",
        "doctor": "probes this machine's provider credentials, so its exit code is a fact about "
        "the developer's `.env` and not about the line",
        "serve": "starts the server; a test that ran it would not return",
        "evidence build": "takes an events file the surrounding prose describes rather than "
        "ships, and both copies are PowerShell blocks in `docs/api/`",
        "shortlist run": "refuses on this fixture with `researched_ratio_not_measurable`, which "
        "is honest: no evidence run has been made against the generated names, and the "
        "documented line assumes the research plane the section before it describes",
        "shortlist get": "its argument is the placeholder `sla_0123456789abcdef01234567`",
        "shortlist list": "reads a store the placeholder above never filled, so `0` here would "
        "assert nothing the empty listing does not already",
        "model prediction": "its argument is the placeholder `prd_0123456789abcdef01234567`",
        "portfolio construct": "its argument is the placeholder `sla_0123456789abcdef01234567`",
        "portfolio turnover-variants": "its argument is the placeholder `sl_2026_03_02`",
        "validation statistics": "its `--signal` arguments are the placeholders "
        "`sig_0123456789abcdef01234567` and `sig_89abcdef0123456701234567`",
        "validation segmented": "`validation statistics`'s reason, plus a `--plan "
        "./segments.json` the section describes rather than ships",
    }
)
"""Every documented command that is **not** executed here, and why.

Three kinds, and the distinction is what stops this becoming a list of excuses: unreachable
without a credential, a network or a running server (`panel build`, `doctor`, `serve`);
answering about the wall clock, so that an assertion would measure this fixture's build window
rather than the documented line (`panel doctor`, `data-check`); and illustrative -- an argument
that is a placeholder address by construction, which no fixture can make real.

A command that moves out of this table has to have its reason deleted, and one that arrives has
to be given one, because `test_every_documented_command_is_executed_or_declared` is an equality.
"""


class DocumentedLine(NamedTuple):
    """One `openalpha` invocation, and where a reader finds it."""

    document: str
    line_number: int
    command: str
    argv: tuple[str, ...]
    raw: str

    def __str__(self) -> str:  # pragma: no cover - identifies a parametrised case
        return f"{self.document}:{self.line_number} {self.command}"


def _fenced_blocks(text: str) -> list[tuple[str, int, str]]:
    """Every fenced block as `(language, opening line number, body)`."""
    found: list[tuple[str, int, str]] = []
    language: str | None = None
    start = 0
    buffer: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        opening = FENCE.match(line)
        if opening and language is None:
            language, start, buffer = opening.group(1), number, []
        elif line.strip() == "```" and language is not None:
            found.append((language, start, "\n".join(buffer)))
            language = None
        elif language is not None:
            buffer.append(line)
    return found


def _invocations(body: str, language: str) -> list[str]:
    """The `openalpha` lines in one block, with continuations joined.

    PowerShell continues with a backtick and everything else with a backslash; `docs/api/` uses
    the first and the READMEs the second, so both are joined rather than one being read as a
    line that stops early.
    """
    joined = body.replace("`\n", " ") if language == "powershell" else body.replace("\\\n", " ")
    return [
        re.sub(r"\s+#.*$", "", line.strip()).strip()
        for line in joined.splitlines()
        if INVOCATION.match(line.strip())
    ]


def _is_group(command: object) -> bool:
    """Whether `command` has subcommands, asked of the object rather than of a base class.

    **`isinstance(command, click.Group)` is `False` for every command in this tree**, and the
    first version of this file used it. Typer 0.20 builds its groups out of
    `typer._click.core.Command`, a vendored hierarchy that does not subclass `click.Group` at
    all -- so the resolver's `while` loop never entered, every line resolved to the root group,
    and the assertion `not isinstance(command, click.Group)` passed **vacuously** for all fifty
    of them. Measured, not reasoned about: the equality test one function down went red with
    `{''}` on the left, which is the only reason any of it was noticed.
    """
    return hasattr(command, "commands")


def _resolve(argv: list[str]) -> tuple[object, list[str], str]:
    """Walk the live command tree as far as the line names a command, and return the remainder."""
    command: object = get_command(app)
    named: list[str] = []
    rest = list(argv)
    while rest and _is_group(command):
        child = command.commands.get(rest[0])  # type: ignore[attr-defined]
        if child is None:
            break
        named.append(rest[0])
        command, rest = child, rest[1:]
    return command, rest, " ".join(named)


def _declared_options(command: object) -> frozenset[str]:
    """Every long option `command` accepts, off the live parameter list."""
    return frozenset(
        option
        for parameter in command.params  # type: ignore[attr-defined]
        for option in (*parameter.opts, *parameter.secondary_opts)
        if option.startswith("--")
    )


def _undeclared_options(command: object, rest: list[str]) -> list[str]:
    """The long options a documented line names that its command does not declare.

    Introspection rather than `Command.parse_args`, and the reason is the same measurement that
    produced `_is_group`: this Typer raises `typer._click.exceptions.NoSuchOption`, which is not
    a `click.ClickException`, so a parser-based check has to name an exception hierarchy that
    has already moved once. A parameter list cannot move without this reading it.
    """
    return [
        token
        for token in rest
        if token.startswith("--") and token.split("=", 1)[0] not in _declared_options(command)
    ]


def _documented_lines() -> tuple[DocumentedLine, ...]:
    """Every documented invocation in the two READMEs and `docs/`, in document order.

    Lines carrying an elision (`...`, `…`) are dropped rather than declared, because they are
    prose about a command rather than a command: `openalpha model prediction prd_…` names no
    argument at all. Everything else is here, and `test_the_documents_still_carry_command_lines`
    pins the count so that a fence renamed to `text` cannot silently empty this file.
    """
    documents = [REPOSITORY_ROOT / "README.md", REPOSITORY_ROOT / "README.en.md"]
    documents += sorted(REPOSITORY_ROOT.joinpath("docs").rglob("*.md"))

    found: list[DocumentedLine] = []
    for path in documents:
        for language, number, body in _fenced_blocks(path.read_text(encoding="utf-8")):
            for raw in _invocations(body, language):
                if any(mark in raw for mark in ELISION):
                    continue
                tokens = shlex.split(raw)
                # `uv run openalpha …` drops three tokens and `openalpha …` drops one. Both
                # spellings appear -- the READMEs use the first and `docs/HANDOFF_CURRENT.md`
                # the second -- and getting this wrong resolves every line to the root group,
                # which reads as "no command named" rather than as a slicing bug.
                argv = tokens[3:] if tokens[0] == "uv" else tokens[1:]
                _, _, command = _resolve(argv)
                found.append(
                    DocumentedLine(
                        document=str(path.relative_to(REPOSITORY_ROOT)),
                        line_number=number,
                        command=command,
                        argv=tuple(argv),
                        raw=raw,
                    )
                )
    return tuple(found)


DOCUMENTED: Final[tuple[DocumentedLine, ...]] = _documented_lines()


def test_the_documents_still_carry_command_lines() -> None:
    """A floor, so a broken fence or a renamed heading cannot quietly empty every test here.

    Measured at `94a0af2`: 50 invocations across 7 files, 6 more carrying elisions. The
    assertion is a floor rather than the number, because adding documentation must not be the
    thing that goes red -- what must go red is documentation disappearing from this file's view.
    """
    assert len(DOCUMENTED) >= 50
    assert len({line.document for line in DOCUMENTED}) >= 7


@pytest.mark.parametrize("line", DOCUMENTED, ids=str)
def test_every_documented_line_names_a_command_that_exists_and_parses(
    line: DocumentedLine,
) -> None:
    """The cheap half, over **every** line including the ones no fixture can run.

    This is what catches a renamed or deleted option under an illustrative command -- `openalpha
    portfolio construct sla_0123…` can never be executed here, but `--tier-weight` disappearing
    from under it is drift a reader would meet, and this sees it.
    """
    command, rest, named = _resolve(list(line.argv))

    assert not _is_group(command), (
        f"{line.document}:{line.line_number} names no command: {line.raw}"
    )
    undeclared = _undeclared_options(command, rest)

    assert not undeclared, (
        f"{line.document}:{line.line_number} `openalpha {named}` does not declare "
        f"{', '.join(undeclared)}: {line.raw}"
    )


def test_every_documented_command_is_executed_or_declared() -> None:
    """The equality that makes the two tables above decisions rather than notes.

    A documented command in neither set is red and names itself; a declared reason for a command
    no document mentions any more is red too, which is what stops `NOT_EXECUTED` outliving the
    lines it excuses.
    """
    documented = {line.command for line in DOCUMENTED}

    assert documented - EXECUTED == set(NOT_EXECUTED)
    assert set(NOT_EXECUTED) <= documented, (
        f"{set(NOT_EXECUTED) - documented} is excused here and documented nowhere"
    )
    assert documented >= EXECUTED, (
        f"{EXECUTED - documented} is executed here and documented nowhere"
    )
    assert all(NOT_EXECUTED.values()), "a declared exclusion with no reason is not a decision"


BUILT_SESSIONS: Final[tuple[date, ...]] = tuple(
    date(2026, 1, day) for day in (6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20)
)
"""Every session the documented model block needs a raw cross section on.

The README's own block says so, in a comment beside its single `factor build` line telling
the reader to repeat it once a day through 2026-01-16 -- so building them here is following
the documentation rather than supplementing it. Through 2026-01-20 because that is the day the
block's own `--as-of` used to name, before `V2-P5-048` replaced it with an instant after the
year that the partition gate actually admits.
"""


def _priced_year_with_raw_tiers(root: Path) -> Path:
    """A whole priced 2026, plus the raw tiers the model block's own comment asks for.

    `sys.path` reaches `tests/` through `conftest.py`, and `generate_panel` /
    `write_generated_panel` are the two functions that stand in for the `openalpha panel build`
    a reader would run. Four load-bearing imports for a panel is the obstacle this module's
    docstring names, and it is paid here.

    A function rather than only a fixture because two callers need this store in **different**
    states: the sweep wants one the documented `factor build` lines may fill in, and the remedy
    test needs one where the processed and neutralized tiers are still absent -- which is what
    provokes the refusal it reads. Sharing one module-scoped store made the second pass alone
    and fail in a full run, measured, because the sweep had built the tiers first.
    """
    store = PanelStore(root / "panel")
    panel = generate_panel(
        shapes=("daily.close_moves_between_sessions",), window=(WINDOW_FIRST, LAST_DAY)
    )
    write_generated_panel(store, panel)

    for session in BUILT_SESSIONS:
        instant = datetime(session.year, session.month, session.day, 9, 0, tzinfo=UTC)
        built = runner.invoke(
            app,
            [
                "factor",
                "build",
                "--factor",
                "reversal_1d/v1",
                "--tier",
                "raw",
                "--as-of",
                instant.isoformat().replace("+00:00", "+00:00"),
                "--year",
                "2026",
                "--max-staleness-days",
                "30",
                "--runtime-dir",
                str(root),
                "--exchange",
                EXCHANGE,
            ],
        )
        assert built.exit_code == 0, built.output
    return root


@pytest.fixture(scope="module")
def documented_runtime(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The store the documented lines are executed against, built once for the whole sweep."""
    return _priced_year_with_raw_tiers(tmp_path_factory.mktemp("documented"))


@pytest.fixture
def runtime_without_derived_tiers(tmp_path: Path) -> Path:
    """A store holding `raw` and neither derived tier, per test.

    Function-scoped deliberately: the refusal the remedy test reads only exists while the
    processed and neutralized partitions are absent, and the sweep's own `factor build` lines
    write them into the module-scoped store.
    """
    return _priced_year_with_raw_tiers(tmp_path / "runtime")


def _accepts(command: click.Command, option: str) -> bool:
    """Whether `command` declares `option`, measured off the live parameter list.

    Measured rather than tabulated because the two substitutions below must not be applied to a
    command that does not take them: `openalpha model predictions --exchange …` exits `2`, and a
    hard-coded list of which commands take what would be a third copy of the CLI's own shape --
    the exact failure this file exists to catch.
    """
    return any(option in parameter.opts for parameter in command.params)


EXECUTED_LINES: Final[tuple[DocumentedLine, ...]] = tuple(
    line for line in DOCUMENTED if line.command in EXECUTED
)


def test_the_executable_documented_lines_are_actually_reached() -> None:
    """A floor on the executed subset, for `test_the_documents_still_carry_command_lines`' reason.

    Without it, a README edit that turned every `factor build` line into prose would leave the
    parametrised test below with nothing to run and this file green about nothing.
    """
    assert len(EXECUTED_LINES) >= 10
    assert {line.command for line in EXECUTED_LINES} == EXECUTED


@pytest.mark.parametrize("line", EXECUTED_LINES, ids=str)
def test_the_documented_line_runs_as_written(
    line: DocumentedLine, documented_runtime: Path
) -> None:
    """Executed verbatim, exit `0`.

    Exit `0` and not "not a crash": every defect this file found was an *honest refusal* of a
    request that could not be met, so a test admitting exit `1` would have passed against all
    four of them.

    Two substitutions, both of things a reader supplies from their own installation and neither
    touching the documented arguments: `--runtime-dir`, which the prose supplies separately, and
    `--exchange`, because `panel_fixtures` generates an SZSE calendar while the commands default
    to SSE. Both are appended only where the command declares them.
    """
    command, _, _ = _resolve(list(line.argv))
    arguments = list(line.argv)
    if _accepts(command, "--runtime-dir"):
        arguments += ["--runtime-dir", str(documented_runtime)]
    if _accepts(command, "--exchange"):
        arguments += ["--exchange", EXCHANGE]

    result = runner.invoke(app, arguments)

    assert result.exit_code == 0, (
        f"{line.document}:{line.line_number} does not run as written\n{line.raw}\n{result.output}"
    )


PLACEHOLDERS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "<year>": "2026",
        "<instant>": "2026-01-08T09:00:00+00:00",
        "<days>": "30",
        "<transform>": "cross_section_standard/v1",
        "<key>": "industry_and_size/v1",
    }
)
"""What a reader supplies for each angle-bracket the `factor build` remedy asks for.

Every placeholder the remedy can print has a row, asserted below -- so a remedy that grows a
`<subject>` nobody can fill goes red here rather than in a terminal. The values are the ones the
README's own factor block uses, which is the closest thing to "what a reader would type".
"""


def _unnamed_required_options(command: object, rest: list[str]) -> list[str]:
    """The options `command` requires that `rest` does not name.

    Read off `Parameter.required` on the live command, so an option that becomes required
    arrives here rather than in a reader's terminal.
    """
    named = {token.split("=", 1)[0] for token in rest if token.startswith("--")}
    missing: list[str] = []
    for parameter in command.params:  # type: ignore[attr-defined]
        if not getattr(parameter, "required", False):
            continue
        long = [option for option in parameter.opts if option.startswith("--")]
        if long and not named.intersection(long):
            missing.append(long[0])
    return missing


def test_the_remedy_a_missing_neutralized_tier_prints_names_every_option_that_tier_requires(
    runtime_without_derived_tiers: Path,
) -> None:
    """Defect (2) of this module's docstring, which only executing the refusal could find.

    `factor run --neutralization …` against a panel with no neutralized tier refuses with
    `Build it first:` and a command line. That line named `--factor`, `--tier` and `--year` and
    stopped, and it does not run for **any** tier: `--as-of` is required, so all three exited
    `2`; `processed` and `neutralized` then exit `3` on `--transform`/`--neutralization`, both
    of which say in their own refusal that there is "no default a spec would be honest to have".

    **Asserted as "names every required option" rather than as "exits 0", and the distinction is
    the point.** The remedy's values are angle-bracket placeholders -- `<instant>`, `<key>` --
    because an instant and a neutralisation are declarations about a study that a message must
    not invent. So the honest property is that nothing *required* is left out of the shape, and
    that is read off `Parameter.required` on the live command rather than off a copy of it.
    """
    documented = next(
        line
        for line in DOCUMENTED
        if line.command == "factor run" and "--neutralization" in line.argv
    )
    refusal = runner.invoke(
        app,
        [
            *documented.argv,
            "--runtime-dir",
            str(runtime_without_derived_tiers),
            "--exchange",
            EXCHANGE,
        ],
    )
    assert refusal.exit_code == 1, (
        "the documented `factor run` is what provokes this refusal, so it must reach the "
        f"neutralized read rather than stop at its own arguments:\n{refusal.output}"
    )

    printed = re.search(r"`(openalpha factor build [^`]+)`", refusal.output)
    assert printed is not None, f"the refusal names no remedy:\n{refusal.output}"

    remedy = shlex.split(printed.group(1))[1:]
    command, rest, _ = _resolve(remedy)

    assert not _is_group(command), f"the refusal's remedy names no command: {printed.group(1)}"
    assert not _undeclared_options(command, rest), printed.group(1)

    missing = _unnamed_required_options(command, rest)

    assert not missing, (
        f"the remedy this refusal prints leaves out {', '.join(missing)}, which "
        f"`openalpha factor build` requires:\n{printed.group(1)}"
    )

    filled = [PLACEHOLDERS.get(token, token) for token in remedy]

    assert not any(token.startswith("<") for token in filled), (
        f"this remedy carries a placeholder `PLACEHOLDERS` does not fill: {printed.group(1)}"
    )
    result = runner.invoke(
        app, [*filled, "--runtime-dir", str(runtime_without_derived_tiers), "--exchange", EXCHANGE]
    )

    assert result.exit_code == 0, (
        "a reader who copies this remedy and supplies the values it asks for must get a build, "
        f"not a second refusal:\n{printed.group(1)}\n{result.output}"
    )
