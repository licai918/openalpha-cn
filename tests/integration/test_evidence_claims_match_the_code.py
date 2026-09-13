"""What the documents and diagrams say about evidence identity, the Retry-After header and the
evidence lookup tool must be what the code does.

Three checks. Each was written against a line of brain-02 or brain-03 that the final review of
`d4ef5e4` found false, and each reads `GUARDED_FILES` -- `README.md`, `README.en.md`,
`docs/why-openalpha-cn.zh-CN.md`, the marketing pack and `docs/api/*.md` -- and the words the
ten embedded diagrams draw, read from their generators by `tests/diagram_text.py`.

1. **Evidence identity.** A formula of the shape `evidence_id = hash(...)` must name exactly the
   inputs that move `EvidenceSnapshot.evidence_id`. Those are measured rather than listed: each
   input in `IDENTITY_PROBES` is changed alone, and the ones whose change moves the ID are the
   identity. `payload` is named `content_hash` there, the digest of it that the ID folds in. At
   `d4ef5e4` the identity was `subject`, `kind`, `source_id`, `available_time` and
   `content_hash`, and brain-02 drew `evidence_id = hash(source_uri + content_hash)`:
   `source_uri` moves nothing, and three of the five were missing.
2. **Retry-After.** A mention of the header (`Retry-After` or `retry_after`, in any case) is
   allowed only while some module under `src/` reads it. A reader is a string literal naming the
   header, in any case, passed to a call, used as a subscript or compared. brain-02 drew
   "错误分类与 Retry-After", and nothing under `src/` names the header.
3. **The evidence tool.** `EvidenceLookupTool`, or any of `EVIDENCE_TOOL_NAMES`, may be mentioned
   only while some module under `src/openalpha_cn` outside the `tools` package imports it.
   brain-02 drew "只读证据工具", brain-03 "EvidenceLookupTool 只读查询", and four marketing
   sections said the agents query evidence through a read-only tool. No shipped module imports it
   (`runtime/router.py` records the same), and the agents read the evidence their request
   carries: `ResearchEngine.run_cycle` hands `request.evidence` to `AgentContext`, and
   `ResearchRunRequest` refuses evidence not yet visible at its `as_of`.

**How it reads.** A document is read as clauses by `tests/prose_clauses.py`. For checks 2 and 3
each diagram literal is read alone (`diagram_strings`). For check 1 each drawing call's strings
are read together (`diagram_units`), because a formula too long for one line of a panel is drawn
over two of its lines.

**What it cannot see.** `test_the_stated_blind_spots_are_real` measures each of these but a library
that honours the header itself, which no scan of `src/` can show.

- A statement about identity in any shape but the formula -- "evidence_id 由 source_uri 派生" --
  is not read, and neither is a formula written with full-width parentheses, nor one with a
  prefix before the function, the way the code builds the ID (`domain/evidence.py:183`):
  `IDENTITY_FORMULA` wants a function right after the `=`, so neither
  `evidence_id = "ev_" + sha256(...)` nor `evidence_id = f"ev_{sha256(...)}"` is read.
- The header spelled without a separator (`RetryAfter`), or with a non-breaking hyphen (U+2011)
  where `-` goes, is not a mention of it.
- A tool worded outside `EVIDENCE_TOOL_NAMES` is not a mention of it, such as 只读查询接口 or
  "lookup 工具".
- A reader of the header built from pieces (`"Retry" + "-After"`), or a library the code calls
  that honours the header itself, is not seen. And any literal that names the header in a call
  counts as a reader, even one that only logs it.
- An import of the tool through `importlib`, or by a computed name, is not seen. And an import
  is all that is checked, not whether a shipped path calls the tool.
- Text a generator computes at run time is not read (`tests/diagram_text.py`).

**What it refuses that is true.** The same test measures these.

- A formula must name the fields by their code names; one written with 主体 or 类型 fails.
- A mention of the header or the tool fails however it is worded, so a sentence that names
  either only to deny it fails too. Say what the code does instead.
- A reader of the header through a module constant -- `RETRY_AFTER = "Retry-After"`, then
  `headers.get(RETRY_AFTER)` -- is not counted, so a true mention of the header would fail.
- `from openalpha_cn import tools`, then `tools.EvidenceLookupTool`, is not counted as an import
  of the tool, so a true mention of the tool would fail.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from diagram_text import diagram_strings, diagram_units
from prose_clauses import clauses

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PACKAGE: Final[Path] = ROOT / "src" / "openalpha_cn"

GUARDED_FILES: Final[tuple[Path, ...]] = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "docs" / "why-openalpha-cn.zh-CN.md",
    ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md",
    *sorted((ROOT / "docs" / "api").glob("*.md")),
)
"""The four user-facing documents the other prose guards read, and the API reference."""

DIAGRAM_GENERATORS: Final[tuple[Path, ...]] = (
    ROOT / "scripts" / "generate_brain_diagrams.py",
    ROOT / "scripts" / "generate_api_relationship_diagrams.py",
)
"""The generators of the ten diagrams `README.md` embeds; `tests/unit/test_repository_assets.py`
holds every committed SVG equal to what they write."""


def _guarded_documents() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in GUARDED_FILES
    }


def _diagram_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in DIAGRAM_GENERATORS
    }


def _document_clauses(documents: dict[str, str]) -> list[tuple[str, int, str]]:
    return [
        (label, clause.line, clause.text)
        for label, document in documents.items()
        for clause in clauses(document)
    ]


def _diagram_literals(sources: dict[str, str]) -> list[tuple[str, int, str]]:
    return [
        (label, literal.line, literal.text)
        for label, source in sources.items()
        for literal in diagram_strings(source, filename=label)
    ]


def _diagram_units(sources: dict[str, str]) -> list[tuple[str, int, str]]:
    return [
        (label, unit.line, unit.text)
        for label, source in sources.items()
        for unit in diagram_units(source, filename=label)
    ]


# --- 1. Evidence identity ---------------------------------------------------------------------

_BASE_TIME: Final[datetime] = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)

IDENTITY_PROBES: Final[dict[str, object]] = {
    "subject": "000002.SZ",
    "kind": "theme",
    "source_id": "probe-source-2",
    "source_uri": "https://example.invalid/evidence/2",
    "source_license": "probe-license-2",
    "redistribution": "restricted",
    "summary": "a second summary",
    "payload": {"close": 11.0},
    "event_time": _BASE_TIME - timedelta(hours=1),
    "available_time": _BASE_TIME + timedelta(minutes=90),
    "ingested_time": _BASE_TIME + timedelta(hours=3),
    "revision_time": _BASE_TIME + timedelta(hours=3),
}
"""One changed value for every input of an `EvidenceSnapshot`: each model field but the fixed
`schema_version` and the `timeline` that holds the clocks, and each of `Timeline`'s four clocks.
Each value keeps the snapshot valid (`Timeline` refuses an ingestion or revision time before the
available time)."""

IDENTITY_NAMES: Final[dict[str, str]] = {"payload": "content_hash"}
"""`evidence_id` folds in `content_hash`, the digest of `payload` alone, so a formula names that."""


def _snapshot(**changes: object) -> EvidenceSnapshot:
    """A fixed, valid `EvidenceSnapshot`, with the inputs named in `changes` replaced."""
    clocks: dict[str, object] = {
        "event_time": _BASE_TIME,
        "available_time": _BASE_TIME + timedelta(hours=1),
        "ingested_time": _BASE_TIME + timedelta(hours=2),
        "revision_time": _BASE_TIME + timedelta(hours=2),
    }
    fields: dict[str, object] = {
        "subject": "000001.SZ",
        "kind": "limit_up",
        "source_id": "probe-source",
        "source_uri": "https://example.invalid/evidence/1",
        "source_license": "probe-license",
        "redistribution": "allowed",
        "summary": "a summary",
        "payload": {"close": 10.0},
    }
    for name, value in changes.items():
        (clocks if name in clocks else fields)[name] = value
    return EvidenceSnapshot.model_validate({**fields, "timeline": Timeline(**clocks)})


def _identity_fields() -> frozenset[str]:
    """The inputs whose change alone moves `evidence_id`, under the names a formula uses."""
    base = _snapshot().evidence_id
    return frozenset(
        IDENTITY_NAMES.get(name, name)
        for name, value in IDENTITY_PROBES.items()
        if _snapshot(**{name: value}).evidence_id != base
    )


IDENTITY_FORMULA: Final[re.Pattern[str]] = re.compile(
    r"evidence_id\s*=\s*[A-Za-z_][\w.]*\s*\(", re.ASCII
)
"""The opening of a formula: `evidence_id = <function>(`, ASCII parentheses only."""

_DOTTED_NAME: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", re.ASCII)


def _inside_parentheses(text: str, start: int) -> str | None:
    """The text from `start` up to the parenthesis that closes the one just before it, or None."""
    depth = 1
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start:index]
    return None


def _formula_fields(arguments: str) -> frozenset[str]:
    """The field names a formula's arguments hold: a dotted name's last part, calls excluded."""
    names: set[str] = set()
    for match in _DOTTED_NAME.finditer(arguments):
        parts = match.group().split(".")
        if arguments[match.end() :].lstrip().startswith("("):
            parts = parts[:-1]
        if parts:
            names.add(parts[-1])
    return frozenset(names)


def _identity_problems(
    texts: list[tuple[str, int, str]], identity: frozenset[str]
) -> tuple[list[str], int]:
    """Every formula in `texts` that does not name exactly `identity`, and how many were read."""
    problems: list[str] = []
    read = 0
    for label, line, text in texts:
        for match in IDENTITY_FORMULA.finditer(text):
            read += 1
            arguments = _inside_parentheses(text, match.end())
            if arguments is None:
                problems.append(f"{label}:{line} opens a formula it never closes: {text!r}")
                continue
            named = _formula_fields(arguments)
            if named != identity:
                problems.append(
                    f"{label}:{line} derives evidence_id from {sorted(named)}, but the code "
                    f"derives it from {sorted(identity)} -- missing {sorted(identity - named)}, "
                    f"extra {sorted(named - identity)}: {text!r}"
                )
    return problems, read


def test_every_input_of_an_evidence_snapshot_is_probed() -> None:
    """`IDENTITY_PROBES` covers every input, so a field added to the model is measured too.

    The identity it measures is also held to what it was when this was written, so that a change
    to the identity sends someone to re-read the formulas the documents and diagrams show.
    """
    inputs = (set(EvidenceSnapshot.model_fields) - {"schema_version", "timeline"}) | {
        field.name for field in dataclasses.fields(Timeline)
    }
    assert set(IDENTITY_PROBES) == inputs, (
        f"IDENTITY_PROBES varies {sorted(IDENTITY_PROBES)}, but an EvidenceSnapshot's inputs are "
        f"{sorted(inputs)}; give every input a changed value"
    )
    expected = {"subject", "kind", "source_id", "available_time", "content_hash"}
    assert _identity_fields() == expected, (
        f"evidence_id now moves with {sorted(_identity_fields())}; re-read every identity formula "
        "the documents and diagrams show, then update this expectation"
    )


def test_every_identity_formula_names_exactly_what_moves_the_evidence_id() -> None:
    """A formula for `evidence_id` in a document or a diagram names exactly the identity inputs.

    Written against brain-02's `evidence_id = hash(source_uri + content_hash)`. A formula must
    still be read somewhere, so a reader gone blind fails rather than passes.
    """
    texts = _document_clauses(_guarded_documents()) + _diagram_units(_diagram_sources())
    problems, read = _identity_problems(texts, _identity_fields())
    assert read, (
        "no identity formula is read in any guarded document or diagram; brain-02 drew one when "
        "this was written. If it was removed on purpose, remove this assertion with it."
    )
    assert not problems, (
        "\n".join(problems) + "\nName exactly the fields EvidenceSnapshot.evidence_id folds in. "
        "Fix a diagram in its generator, then regenerate it with the generator itself."
    )


# --- 2. The Retry-After header ----------------------------------------------------------------

RETRY_AFTER_MENTION: Final[re.Pattern[str]] = re.compile(r"retry[-_]after", re.IGNORECASE)
"""A mention of the header in prose or a diagram: `Retry-After` or `retry_after`, in any case.
Not "retry after" with a space, which is English, not the header."""

RETRY_AFTER_HEADER: Final[re.Pattern[str]] = re.compile(r"retry-after", re.IGNORECASE)
"""The header's own name, which a reader has to spell out whole."""


def _retry_after_readers(package: Path) -> list[str]:
    """Each place a module under `package` passes the header's name to a call, a subscript or a
    comparison, as `path:line`."""
    readers: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            candidates: list[ast.expr] = []
            if isinstance(node, ast.Call):
                candidates = [*node.args, *(keyword.value for keyword in node.keywords)]
            elif isinstance(node, ast.Subscript):
                candidates = [node.slice]
            elif isinstance(node, ast.Compare):
                candidates = [node.left, *node.comparators]
            readers.extend(
                f"{path.relative_to(package.parent).as_posix()}:{candidate.lineno}"
                for candidate in candidates
                if isinstance(candidate, ast.Constant)
                and isinstance(candidate.value, str)
                and RETRY_AFTER_HEADER.fullmatch(candidate.value.strip())
            )
    return readers


def _retry_after_mentions(
    documents: dict[str, str], sources: dict[str, str]
) -> list[tuple[str, int, str]]:
    return [
        (label, line, text)
        for label, line, text in _document_clauses(documents) + _diagram_literals(sources)
        if RETRY_AFTER_MENTION.search(text)
    ]


def test_retry_after_is_mentioned_only_while_something_reads_it() -> None:
    """No document or diagram may name the Retry-After header while nothing under `src/` reads it.

    Written against brain-02's Provider Contract panel, which drew "错误分类与 Retry-After" when
    no module under `src/` named the header at all.
    """
    readers = _retry_after_readers(PACKAGE)
    mentions = _retry_after_mentions(_guarded_documents(), _diagram_sources())
    assert readers or not mentions, (
        "\n".join(f"{label}:{line} names Retry-After: {text!r}" for label, line, text in mentions)
        + "\nNo module under src/ reads the header. Say what the code does -- ProviderFailure "
        "carries a category and a retryable flag -- and fix a diagram in its generator."
    )


# --- 3. The evidence lookup tool --------------------------------------------------------------

EVIDENCE_TOOL_NAMES: Final[tuple[str, ...]] = (
    "EvidenceLookupTool",
    "只读工具",
    "证据工具",
    "证据查询工具",
    "evidence lookup tool",
    "read-only tool",
    "evidence tool",
)
"""The class's name and the phrases the documents and diagrams used for it, matched in any case
as substrings: 证据工具 also catches "只读证据工具", "evidence tool" also catches "read-only
evidence tool", and "evidence tools" counts. `test_every_mention_marker_is_needed` holds each
entry to a sentence that only it catches."""

TOOL_MODULES: Final[frozenset[str]] = frozenset(
    {"openalpha_cn.tools", "openalpha_cn.tools.evidence"}
)
"""The package that defines and re-exports the tool, and the module that defines it."""


def _absolute_module(relative: Path, node: ast.ImportFrom) -> str:
    """`node`'s module as an absolute dotted name, for a file at `relative` (from `src/`)."""
    if node.level == 0:
        return node.module or ""
    package = list(relative.with_suffix("").parts[:-1])
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def _evidence_tool_importers(package: Path) -> list[str]:
    """Each module under `package`, outside its own `tools` package, that imports the tool.

    An import counts when it names the tool (or `*`) from `openalpha_cn.tools` or
    `openalpha_cn.tools.evidence`, imports the `evidence` module from the package, or imports
    either module whole. A relative import is resolved first.
    """
    importers: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package.parent)
        if relative.parts[:2] == (package.name, "tools"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _absolute_module(relative, node)
                names = {alias.name for alias in node.names}
                if module in TOOL_MODULES and (
                    names & {"EvidenceLookupTool", "*"}
                    or (module == "openalpha_cn.tools" and "evidence" in names)
                ):
                    importers.append(f"{relative.as_posix()}:{node.lineno}")
            elif isinstance(node, ast.Import) and any(
                alias.name in TOOL_MODULES for alias in node.names
            ):
                importers.append(f"{relative.as_posix()}:{node.lineno}")
    return importers


def _evidence_tool_mentions(
    documents: dict[str, str], sources: dict[str, str]
) -> list[tuple[str, int, str]]:
    return [
        (label, line, text)
        for label, line, text in _document_clauses(documents) + _diagram_literals(sources)
        if any(name.lower() in text.lower() for name in EVIDENCE_TOOL_NAMES)
    ]


def test_the_evidence_tool_is_mentioned_only_while_a_shipped_module_imports_it() -> None:
    """No document or diagram may present `EvidenceLookupTool` while no shipped module imports it.

    Written against brain-02's 证据存储 panel ("只读证据工具"), brain-03's run_cycle panel
    ("EvidenceLookupTool 只读查询") and four marketing sections that said the agents query
    evidence through a read-only tool.
    """
    importers = _evidence_tool_importers(PACKAGE)
    mentions = _evidence_tool_mentions(_guarded_documents(), _diagram_sources())
    assert importers or not mentions, (
        "\n".join(
            f"{label}:{line} names the evidence tool: {text!r}" for label, line, text in mentions
        )
        + "\nNo module under src/openalpha_cn outside tools/ imports EvidenceLookupTool. The "
        "agents read the evidence their request carries; say that instead."
    )


# --- The checks' own tests --------------------------------------------------------------------

TOOL_NAME_SENTENCES: Final[dict[str, str]] = {
    "EvidenceLookupTool": "研究内核调用 EvidenceLookupTool。",
    "只读工具": "Agent 通过只读工具查询。",
    "证据工具": "Agent 使用证据工具。",
    "证据查询工具": "Agent 使用证据查询工具。",
    "evidence lookup tool": "Agents call the evidence lookup tool.",
    "read-only tool": "Agents query through a read-only tool.",
    "evidence tool": "Agents call the evidence tool.",
}
"""One sentence per entry of `EVIDENCE_TOOL_NAMES`, caught by that entry and by no other."""


def test_every_mention_marker_is_needed() -> None:
    """Each tool name, and each spelling of the header, is held by a sentence.

    Every sentence in `TOOL_NAME_SENTENCES` is caught by its own entry and by no other, so
    dropping any entry from `EVIDENCE_TOOL_NAMES` fails here. `Retry-After` and `retry_after` are
    both mentions of the header, in any case; "retry after" with a space is English and is not.
    """
    assert set(TOOL_NAME_SENTENCES) == set(EVIDENCE_TOOL_NAMES), (
        f"TOOL_NAME_SENTENCES holds {sorted(TOOL_NAME_SENTENCES)}, EVIDENCE_TOOL_NAMES "
        f"{sorted(EVIDENCE_TOOL_NAMES)}; give every name a sentence"
    )
    for name, sentence in TOOL_NAME_SENTENCES.items():
        catching = [other for other in EVIDENCE_TOOL_NAMES if other.lower() in sentence.lower()]
        assert catching == [name], f"{sentence!r} is caught by {catching}, not by {name!r} alone"
        assert _evidence_tool_mentions({"d": sentence}, {}), f"{sentence!r} was not read"
    mentions = ("Provider 读取 Retry-After。", "It reads RETRY_AFTER.", "retry-after is parsed.")
    missed = [text for text in mentions if not _retry_after_mentions({"d": text}, {})]
    assert not missed, f"a spelling of the header was not read as a mention: {missed}"
    english = "We retry after a short wait."
    assert not _retry_after_mentions({"d": english}, {}), f"{english!r} was read as the header"


D4EF5E4_BRAIN_02: Final[str] = (
    "svg.panel(\n"
    '    title="Provider Contract",\n'
    '    lines=("ProviderMetadata / ProviderBatch", "认证 · 限流 · 新鲜度", '
    '"错误分类与 Retry-After"),\n'
    ")\n"
    "svg.panel(\n"
    '    title="EvidenceSnapshot",\n'
    '    lines=("evidence_id = hash(source_uri + content_hash)", '
    '"可见时点 · 哈希 · 来源 · 许可 · 修订"),\n'
    ")\n"
    'svg.panel(title="证据存储", lines=("Parquet 分区", "DuckDB PIT 查询", "只读证据工具"))\n'
)
"""brain-02's three false lines as `scripts/generate_brain_diagrams.py` drew them at `d4ef5e4`,
each in a panel of the shape it had there."""


def test_the_checks_flag_the_lines_they_were_written_for() -> None:
    """Each check's retroactive power, held on brain-02 as it was at `d4ef5e4`."""
    sources = {"d4ef5e4 brain-02": D4EF5E4_BRAIN_02}
    problems, read = _identity_problems(_diagram_units(sources), _identity_fields())
    assert read == 1 and len(problems) == 1, f"the identity formula was read as {problems}"
    assert "missing ['available_time', 'kind', 'source_id', 'subject']" in problems[0], problems
    assert "extra ['source_uri']" in problems[0], problems
    assert [line for _, line, _ in _retry_after_mentions({}, sources)] == [3], (
        "brain-02's Retry-After line was not read"
    )
    assert [line for _, line, _ in _evidence_tool_mentions({}, sources)] == [9], (
        "brain-02's evidence-tool line was not read"
    )


def test_the_identity_check_takes_a_formula_over_two_lines_and_reports_an_unclosed_one() -> None:
    """A formula split over two lines of a panel is read whole; one left open is reported."""
    identity = _identity_fields()
    two_lines = (
        'svg.panel(lines=("evidence_id = hash(subject | kind | source_id |", '
        '"available_time | content_hash)"))\n'
    )
    nested = (
        "evidence_id = sha256(subject | kind | source_id | available_time.isoformat() "
        "| content_hash)"
    )
    unclosed = 'svg.panel(lines=("evidence_id = hash(subject | kind",))\n'
    assert _identity_problems(_diagram_units({"two": two_lines}), identity) == ([], 1)
    assert _identity_problems([("nested", 1, nested)], identity) == ([], 1)
    problems, read = _identity_problems(_diagram_units({"open": unclosed}), identity)
    assert read == 1 and "never closes" in problems[0], problems


def test_the_reader_and_importer_scans_find_what_their_docstrings_say(tmp_path: Path) -> None:
    """The two scans of `src/`, run over a synthetic package, one file per case."""
    package = tmp_path / "openalpha_cn"
    files = {
        "tools/__init__.py": "from openalpha_cn.tools.evidence import EvidenceLookupTool\n",
        "tools/evidence.py": '"""Retry-After is named in a docstring only."""\n',
        "runtime/by_name.py": "from openalpha_cn.tools import EvidenceLookupTool\n",
        "runtime/by_module.py": "from openalpha_cn.tools.evidence import EvidenceLookupTool\n",
        "runtime/whole.py": "import openalpha_cn.tools.evidence\n",
        "runtime/submodule.py": "from openalpha_cn.tools import evidence\n",
        "runtime/relative.py": "from ..tools import EvidenceLookupTool\n",
        "runtime/other_tool.py": "from openalpha_cn.tools.base import ResearchTool\n",
        "api/get.py": 'value = response.headers.get("Retry-After")\n',
        "api/subscript.py": 'value = response.headers["retry-after"]\n',
        "api/compare.py": 'found = name == "RETRY-AFTER"\n',
        "api/docstring.py": '"""Honours Retry-After."""\n',
    }
    for relative, text in files.items():
        (package / relative).parent.mkdir(parents=True, exist_ok=True)
        (package / relative).write_text(text, encoding="utf-8")
    assert sorted(_evidence_tool_importers(package)) == [
        "openalpha_cn/runtime/by_module.py:1",
        "openalpha_cn/runtime/by_name.py:1",
        "openalpha_cn/runtime/relative.py:1",
        "openalpha_cn/runtime/submodule.py:1",
        "openalpha_cn/runtime/whole.py:1",
    ]
    assert sorted(_retry_after_readers(package)) == [
        "openalpha_cn/api/compare.py:1",
        "openalpha_cn/api/get.py:1",
        "openalpha_cn/api/subscript.py:1",
    ]


def test_the_stated_blind_spots_are_real(tmp_path: Path) -> None:
    """Each limit the module docstring states, measured, in both directions.

    A case it says is not seen that starts being reported has closed a blind spot, and a case it
    says fails that starts passing has closed an over-reach: change the docstring with it.
    """
    identity = _identity_fields()
    unseen_formulas = {
        "prose about identity": "evidence_id 由 source_uri 与 content_hash 派生。",
        "full-width parentheses": "evidence_id = hash（source_uri + content_hash）",
        "a string prefix joined by +": 'evidence_id = "ev_" + sha256(source_uri + content_hash)',
        "an f-string, as the code writes it": 'evidence_id = f"ev_{sha256(source_uri)}"',
    }
    read_anyway = {
        label: text
        for label, text in unseen_formulas.items()
        if _identity_problems([(label, 1, text)], identity)[1]
    }
    assert not read_anyway, f"a stated blind spot of the identity check is now read: {read_anyway}"
    unseen_mentions = {
        "RetryAfter": ("Provider 读取 RetryAfter。", _retry_after_mentions),
        "U+2011": ("Provider 读取 Retry\N{NON-BREAKING HYPHEN}After。", _retry_after_mentions),
        "只读查询接口": ("Agent 通过只读查询接口获取证据。", _evidence_tool_mentions),
        "lookup 工具": ("Agent 通过 lookup 工具获取证据。", _evidence_tool_mentions),
    }
    mentioned = [
        label for label, (text, mentions) in unseen_mentions.items() if mentions({"d": text}, {})
    ]
    assert not mentioned, f"a stated blind spot of the mention checks is now read: {mentioned}"
    run_time = 'label = "Retry"\nsvg.text(1, 2, f"{label}-After")\n'
    assert not _retry_after_mentions({}, {"run time": run_time}), (
        "a header name a generator computes at run time is now read"
    )
    package = tmp_path / "openalpha_cn"
    (package / "api").mkdir(parents=True)
    (package / "api" / "pieces.py").write_text(
        'value = headers.get("Retry" + "-After")\n', encoding="utf-8"
    )
    (package / "api" / "dynamic.py").write_text(
        'import importlib\ntool = importlib.import_module("openalpha_cn.tools.evidence")\n',
        encoding="utf-8",
    )
    assert not _retry_after_readers(package), "a reader built from pieces is now seen"
    assert not _evidence_tool_importers(package), "an importlib import of the tool is now seen"
    (package / "api" / "log.py").write_text('logger.info("Retry-After")\n', encoding="utf-8")
    assert _retry_after_readers(package) == ["openalpha_cn/api/log.py:1"], (
        "a logging call naming the header is no longer counted as a reader"
    )

    synonyms = "evidence_id = hash(主体 | 类型 | source_id | available_time | content_hash)"
    assert _identity_problems([("synonyms", 1, synonyms)], identity)[0], (
        "a formula written with synonyms passed; the docstring says it fails"
    )
    denials = {
        "Retry-After": ("代码不读取 Retry-After 头。", _retry_after_mentions),
        "the tool": ("出厂路径不导入 EvidenceLookupTool。", _evidence_tool_mentions),
    }
    passed = [label for label, (text, mentions) in denials.items() if not mentions({"d": text}, {})]
    assert not passed, f"a denial passed, but the docstring says it fails: {passed}"
    truths = tmp_path / "truths" / "openalpha_cn"
    (truths / "api").mkdir(parents=True)
    (truths / "api" / "constant.py").write_text(
        'RETRY_AFTER = "Retry-After"\nvalue = headers.get(RETRY_AFTER)\n', encoding="utf-8"
    )
    (truths / "api" / "package.py").write_text(
        "from openalpha_cn import tools\n\ntool = tools.EvidenceLookupTool\n", encoding="utf-8"
    )
    assert not _retry_after_readers(truths), "a reader through a module constant is now counted"
    assert not _evidence_tool_importers(truths), (
        "`from openalpha_cn import tools` is now counted as an import of the tool"
    )
