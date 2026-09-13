"""No document or diagram may present ChainLin or AKShare as a data source a shipped path uses.

**The premise**, held by `test_the_two_clients_are_constructed_only_for_doctor`.
`ChainLinDataProvider` and `AKShareProvider` are each constructed at exactly one place under
`src/` and `scripts/`: `cli._default_providers`, "the built-in providers `doctor` reports on",
whose only caller is `doctor`. So configuring `CHAINLIN_*` changes one thing a shipped path does:
`openalpha doctor` reports whether the key is present and, with `--probe`, calls the client once
per dataset and reports how each call ended. With the base URL and the key each call sends one
minimal request; with the base URL alone the client refuses before sending anything, every
dataset reports `authentication`, and `doctor --probe` exits non-zero
(`test_the_probe_sends_nothing_without_the_key`). The AKShare adapter takes no credential, and
`doctor` is the only path that constructs it too. Evidence building reads the user's files
(`FileProvider`, in `cli.py` and `sdk.py`), panel building reads Tushare,
`POST /api/v1/evidence/build` takes the batch its caller sends, and a shipped batch item calls no
provider. Both clients are real -- ChainLin's has Bearer auth, a per-minute client-side ceiling
that raises instead of waiting, classified failures and frozen contract tests in
`tests/contract/providers/` -- and a user's own code can construct either and hand its batch to
evidence building, so `README.md`'s sentence that puts 链邻 API and the optional AKShare adapter
on the caller's or the Provider's side, and api-02, which draws both there, are true.
If the premise test fails, a client reaches another path, and the sentences this guard holds were
written for a premise that no longer holds: re-read them, and this guard.

**The claims.** A clause is a claim when it names ChainLin or AKShare (`CLIENT_NAMES`) and holds
one of `CLAIM_MARKERS`: the words this repository used to put either on a data path. They present
it as an entry (入口) or a unified one (统一); as what connects (接入, 连接, 衔接, 可接) or carries
data in (送入, 带入, 纳入, 进入, and 经过 a contract); as what builds, generates or guarantees
evidence (构建, 生成, 保证, 确保) or provides data; as honoured by the batch path (尊重); as usable
once configured (即可) or supported by default (默认支持); or name "ChainLin data" (链邻数据) as
something the product holds. English words of the same kinds are markers too; "accepts" is the one
an English sentence used, README.en.md's AKShare line at `4a37161`. Every such clause -- at
`d4ef5e4` for ChainLin, at `4a37161` for AKShare and in ChainLin's contract document -- was
rewritten; there is no allowlist.

**How it reads.** The four documents as clauses (`tests/prose_clauses.py`), and the diagrams as
units (`tests/diagram_text.py`): brain-01 and brain-02 drew ChainLin's name on one line of a
panel and "已实现 · 统一替代入口" on the next, so a panel's strings are read together. A name is
链邻, ChainLin or AKShare, in any case and as a substring, so `ChainLinDataProvider`,
`chainlin-data/v1` and `AKShareProvider` count. Before a clause is read, a URL and the names of the
separately distributed desktop product (`DESKTOP_PRODUCT`: 链邻桌面…, 链邻涨停复盘…, 链邻安装版,
链邻 Windows 软件, the ChainLin installer) are removed, so "进入 Release 页面" beside a
`chainlin-desktop` link is no claim. A URL is the printable ASCII after `http(s)://`
up to a closing `)`, `]` or `>` (`URL`): whitespace and any character outside ASCII end it, so a
claim written straight after a link, behind full-width punctuation or not, is still read.
ChainLin's own contract document, `docs/api/chainlin-data.zh-CN.md`, is read too, and every
clause of it as naming ChainLin (`CLIENT_DOCUMENTS`), because it speaks of the client's batches
without writing the client's name.

**Two more checks.** `test_env_example_says_who_reads_each_provider_credential` holds the note
above `.env.example`'s data-provider credentials to naming, one sentence each, both ChainLin
variables with `openalpha doctor`, and `TUSHARE_TOKEN` with `TushareProvider`, the two commands
that construct it and `--probe`.
`test_no_document_says_the_contract_tests_hold_clocks_they_do_not_assert` holds a clause that
names ChainLin and its contract tests and says 四时钟 to what those tests assert: all four clocks,
or the clause fails, and they assert two. A clause naming single clocks is not read.

**What it cannot see.** `test_the_stated_limits_are_real` measures each of these but the first,
which `test_the_reference_scan_finds_what_its_docstring_says` measures.

- The premise reads names (`_uses_of`): a class or factory reached by a computed name --
  `getattr(module, "ChainLinDataProvider")` -- is not seen. Every other read counts, not only a
  call: a dict value, a `functools.partial` argument, another name bound to it, an attribute read
  by name (`importlib.import_module(...).ChainLinDataProvider`), and a name an import binds to it,
  followed from module to module (`import ... as X` in one, `X` read in another).
- A claim whose only ChainLin is the desktop product's name: "链邻桌面软件已接入研究链" is not
  read, because the name goes before the clause is.
- A claim worded without a marker. The writing boundary's "真实数据调用仍需用户配置服务地址"
  implied shipped calls through a condition alone; it was rewritten by hand, as was §085's copy.
- A claim split across clauses or blocks, the name in one and the marker in the next: a pronoun
  ("它", "数据随后") carries a claim past this guard.
- In the diagrams, a name and a claim drawn by two different calls, and text a generator computes
  at run time -- an f-string's formatted values, a string built with `+` or `%`, a name -- which
  `tests/diagram_text.py` does not read into a unit.
- An English claim worded outside the few English markers.

**What it refuses that is true.** The same test measures these.

- A clause that names a client with a marker fails even when it denies the claim ("链邻不是统一
  入口"), or when the marker belongs to another subject of the same clause. `D13` split such
  clauses with a semicolon.
- Any clause of ChainLin's contract document that holds a marker, whatever its subject: every
  clause there is read as naming ChainLin, so a semicolon does not help. Its caution "不得把尚未
  配置的服务宣传为已连接" was reworded.
- A link whose path holds text outside ASCII is cut at its first such character, and the rest of
  the path is read as prose: `https://example.com/链邻入口` reads as a claim.
- The premise follows an alias by its name alone, in every module, so a different symbol that
  shares an alias's name is counted as a read too: `from httpx import Client` beside an alias
  `Client` of the class. `test_the_reference_scan_finds_what_its_docstring_says` measures this.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Final

import pytest
from diagram_text import diagram_units
from prose_clauses import clauses

from openalpha_cn.cli import PROBE_FAILURE_STATES, _probe_report
from openalpha_cn.domain.time import Timeline
from openalpha_cn.providers.chainlin import ChainLinDataProvider

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

GUARDED_FILES: Final[tuple[Path, ...]] = (
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "docs" / "why-openalpha-cn.zh-CN.md",
    ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md",
)
"""The four user-facing documents the other prose guards read."""

DIAGRAM_GENERATORS: Final[tuple[Path, ...]] = (
    ROOT / "scripts" / "generate_brain_diagrams.py",
    ROOT / "scripts" / "generate_api_relationship_diagrams.py",
)
"""The generators of the ten diagrams `README.md` embeds; `tests/unit/test_repository_assets.py`
holds every committed SVG equal to what they write."""

CLIENT_DOCUMENTS: Final[dict[str, str]] = {"docs/api/chainlin-data.zh-CN.md": "ChainLin"}
"""Documents about one client, each clause of which is read as naming it.

ChainLin's contract document said of the client's batches, without writing ChainLin's name, that
they "仍通过统一 `ProviderBatch` 和 `EvidenceSnapshot` 管线做 PIT 校验": the subject was the
document's. No shipped path but `doctor --probe` fetches such a batch, and the probe discards it.
"""

SHIPPED_ROOTS: Final[tuple[Path, ...]] = (ROOT / "src", ROOT / "scripts")
"""Where a shipped path's code lives."""

CLIENT_CLASSES: Final[tuple[str, ...]] = ("ChainLinDataProvider", "AKShareProvider")
"""The two data clients only `doctor` constructs."""


# --- The premise ------------------------------------------------------------------------------


def _reads_with_their_function(tree: ast.AST) -> Iterator[tuple[str, ast.expr]]:
    """Each name or attribute `tree` reads, with the innermost function around it, or `<module>`."""

    def visit(node: ast.AST, function: str) -> Iterator[tuple[str, ast.expr]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Name | ast.Attribute) and isinstance(child.ctx, ast.Load):
                yield function, child
            inner = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else function
            )
            yield from visit(child, inner)

    yield from visit(tree, "<module>")


def _names_bound_to(name: str, trees: Iterable[ast.AST]) -> frozenset[str]:
    """`name`, and every name an import binds to it in any of `trees`, followed through each module
    that imports it again: `from here import name as X` in one module, `from there import X` in
    the next."""
    imports = [
        alias
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    names = {name}
    while added := {
        alias.asname
        for alias in imports
        if alias.name in names and alias.asname and alias.asname not in names
    }:
        names |= added
    return frozenset(names)


def _uses_of(name: str, roots: Iterable[Path]) -> list[tuple[str, str]]:
    """Each place under `roots` that reads `name`, as `(path, enclosing function)`.

    A read is `name` itself, a name an import binds to it in any module under `roots`
    (`_names_bound_to`), or an attribute of either, wherever it is loaded: called, put in a dict,
    handed to `functools.partial` or bound to another name. The review of `D13` wired ChainLin
    into `evidence build` in each of the last three ways, and a scan of calls alone stayed green;
    its final review re-exported the class under an alias in one module and constructed it in
    another, and a scan of each module's own aliases stayed green. An import and a definition are
    not reads. The path is relative to the root's parent.
    """
    parsed = [
        (root, path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for root in roots
        for path in sorted(root.rglob("*.py"))
    ]
    names = _names_bound_to(name, (tree for _, _, tree in parsed))
    return [
        (path.relative_to(root.parent).as_posix(), function)
        for root, path, tree in parsed
        for function, node in _reads_with_their_function(tree)
        if (isinstance(node, ast.Name) and node.id in names)
        or (isinstance(node, ast.Attribute) and node.attr in names)
    ]


def test_the_two_clients_are_constructed_only_for_doctor() -> None:
    """ChainLin and AKShare reach no shipped path but `doctor`, which is what this guard's
    sentences say.

    A failure here means a client now reaches another path. The documents' sentences about it
    were written for a client only `doctor` uses: re-read them and this guard before changing
    the expectation.
    """
    assert len(CLIENT_CLASSES) == len(CLIENT_NAMES), (
        f"CLIENT_CLASSES holds {CLIENT_CLASSES} for the clients {sorted(CLIENT_NAMES)}; every "
        "client the documents are held to must have its class held by this premise"
    )
    for client in CLIENT_CLASSES:
        reads = _uses_of(client, SHIPPED_ROOTS)
        assert reads == [("src/openalpha_cn/cli.py", "_default_providers")], (
            f"{client} is read at {reads}; when this was written only cli._default_providers "
            "read it, to construct it"
        )
    callers = _uses_of("_default_providers", SHIPPED_ROOTS)
    assert callers == [("src/openalpha_cn/cli.py", "doctor")], (
        f"cli._default_providers is read in {callers}; when this was written only doctor read "
        "it, to call it"
    )


class _CountingTransport:
    """A ChainLin transport that counts the requests reaching it and answers each with no data."""

    def __init__(self) -> None:
        self.calls = 0

    def get_json(self, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        return {"schema_version": "chainlin-data/v1", "records": [], "no_data_reason": "probe"}


def _probed(transport: _CountingTransport) -> dict[str, str]:
    """`doctor --probe`'s report on a ChainLin client that has a base URL, over `transport`."""
    provider = ChainLinDataProvider(
        base_url="https://data.chainlin.example/v1",
        api_key_env="CHAINLIN_API_KEY",
        source_license="user-held ChainLin subscription",
        transport=transport,
    )
    return _probe_report(provider)


def test_the_probe_sends_nothing_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """What ChainLin's contract document says `doctor --probe` does, measured on the client.

    With the base URL and no key, every dataset reports `authentication` and no request reaches
    the transport; `authentication` is one of `PROBE_FAILURE_STATES`, so `doctor --probe` exits
    non-zero. With the key, each dataset's one request reaches it.
    """
    monkeypatch.delenv("CHAINLIN_API_KEY", raising=False)
    keyless = _CountingTransport()
    outcomes = _probed(keyless)
    assert set(outcomes.values()) == {"authentication"}, (
        f"without the key the probe reported {outcomes}"
    )
    assert keyless.calls == 0, f"without the key {keyless.calls} requests reached the transport"
    assert "authentication" in PROBE_FAILURE_STATES, (
        f"PROBE_FAILURE_STATES is {sorted(PROBE_FAILURE_STATES)}, so the keyless probe no longer "
        "makes doctor --probe exit non-zero"
    )
    monkeypatch.setenv("CHAINLIN_API_KEY", "secret")
    keyed = _CountingTransport()
    outcomes = _probed(keyed)
    assert set(outcomes.values()) == {"ok"}, f"with the key the probe reported {outcomes}"
    assert keyed.calls == len(outcomes), (
        f"with the key {keyed.calls} requests reached the transport for {len(outcomes)} datasets"
    )


ENV_EXAMPLE: Final[Path] = ROOT / ".env.example"

NOTE_SENTENCES: Final[dict[str, tuple[str, ...]]] = {
    "Tushare": (
        "TUSHARE_TOKEN",
        "TushareProvider",
        "openalpha panel build",
        "openalpha doctor",
        "--probe",
    ),
    "ChainLin": ("CHAINLIN_API_BASE_URL", "CHAINLIN_API_KEY", "openalpha doctor"),
}
"""For each provider, what one sentence of the note above `.env.example`'s data-provider
credentials must name together."""


def test_env_example_says_who_reads_each_provider_credential() -> None:
    """`.env.example` says, right above its data-provider credentials, who reads each of them.

    The model keys' block has said since `8d53724` that no shipped path reads them; the ChainLin
    variables sat under a header that left a reader to assume a shipped path used them, when only
    `openalpha doctor` does. The Tushare sentence said only that `openalpha panel build` fetches
    with the token and that doctor reports it, when `TushareProvider` reads it in its constructor,
    `panel build` and doctor (`cli._default_providers`) each construct one, and `doctor --probe`
    sends requests with it. The note must be the comment lines directly above the section header,
    so the section keeps its shape: `test_compose_passes_through_declared_provider_credentials`
    reads the variables directly below that header. This checks that one sentence names each
    provider's words together, not what that sentence says of them.
    """
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    header = lines.index("# User-owned data provider credentials")
    note: list[str] = []
    for line in reversed(lines[:header]):
        if not line.startswith("#"):
            break
        note.insert(0, line.lstrip("#").strip())
    sentences = re.split(r"(?<=\.)\s+", " ".join(note))
    missing = {
        provider: words
        for provider, words in NOTE_SENTENCES.items()
        if not any(all(word in sentence for word in words) for sentence in sentences)
    }
    assert not missing, (
        "no sentence of the comment above .env.example's data-provider credentials names these "
        f"together: {missing}; the comment reads {note}"
    )


# --- The claims -------------------------------------------------------------------------------

CHAINLIN_NAME: Final[re.Pattern[str]] = re.compile(r"链邻|chainlin", re.IGNORECASE)
"""链邻 or ChainLin, in any case, as a substring."""

AKSHARE_NAME: Final[re.Pattern[str]] = re.compile(r"akshare", re.IGNORECASE)
"""AKShare in any case, as a substring, so `AKShareProvider` and `--extra akshare` count."""

CLIENT_NAMES: Final[dict[str, re.Pattern[str]]] = {
    "ChainLin": CHAINLIN_NAME,
    "AKShare": AKSHARE_NAME,
}
"""The names a clause gives each of `CLIENT_CLASSES`."""

DESKTOP_PRODUCT: Final[re.Pattern[str]] = re.compile(
    r"链邻\s*(?:桌面|涨停复盘|Windows\s*软件|安装(?:版|包|程序))"
    r"|chainlin[-_ ](?:desktop|limit-up|installer)",
    re.IGNORECASE,
)
"""The separately distributed desktop product's own names, which do not name the data client.

A bare 链邻软件 or 链邻安装 is not one of them. `D13`'s pattern removed 链邻 before any 软件, 安装
or Windows, and a claim such as "链邻软件接口已接入研究链" lost the only name it had.
"""

URL: Final[re.Pattern[str]] = re.compile(r"https?://(?:(?![)\]>])[!-~])+")
"""A URL: the printable ASCII after `http(s)://`, up to a closing `)`, `]` or `>`.

Whitespace and every character outside ASCII end it, so full-width punctuation does, and so does
the first Chinese character after an ASCII comma. Chinese puts no space after a link, and a URL
that ran on to the next whitespace took the claim written right after it with it;
`test_a_claim_written_right_after_a_link_is_read` holds this. A link whose path holds text outside
ASCII is cut at its first such character, and the rest of the path is read as prose.
"""

_WITHIN_A_CLAUSE: Final[str] = "[^，。\N{FULLWIDTH SEMICOLON}]*"
"""Any run of characters that crosses no comma, full stop or semicolon."""


def _english(word: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z]){word}(?![A-Za-z])", re.IGNORECASE)


CLAIM_MARKERS: Final[dict[str, re.Pattern[str]]] = {
    "入口": re.compile("入口"),
    "接入": re.compile("接入"),
    "送入": re.compile("送入"),
    "纳入": re.compile("纳入"),
    "带入": re.compile("带入"),
    "进入": re.compile("进入"),
    "衔接": re.compile("衔接"),
    "连接": re.compile("连接"),
    "可接": re.compile("可接"),
    "即可": re.compile("即可"),
    "默认支持": re.compile("默认支持"),
    "统一": re.compile("统一"),
    "尊重": re.compile("尊重"),
    "保证": re.compile("保证"),
    "确保": re.compile("(?<!明)确保"),
    "生成…证据": re.compile(f"生成{_WITHIN_A_CLAUSE}证据"),
    "构建…证据": re.compile(f"构建{_WITHIN_A_CLAUSE}证据"),
    "提供…数据": re.compile(f"提供{_WITHIN_A_CLAUSE}(?:数据|输入)"),
    "经过…合同": re.compile(f"经过{_WITHIN_A_CLAUSE}(?:合同|规范化)"),
    "链邻数据": re.compile("链邻数据(?!库|接口)"),
    "entry": _english("entr(?:y|ies)"),
    "unified": _english("unified"),
    "gateway": _english("gateways?"),
    "one-stop": _english("one-stop"),
    "feeds": _english("feeds?"),
    "ingests": _english("ingests?"),
    "connects": _english("connects?"),
    "plugs into": _english("plugs? into"),
    "ready to use": _english("ready to use"),
    "accepts": _english("accepts?(?!:)"),
    "ChainLin data": re.compile(r"chainlin data(?! api)", re.IGNORECASE),
    "provides data": _english(r"provides?[^,.;]*(?<![A-Za-z])(?:data|input)"),
}
"""The words that make a clause naming a client a claim, each named for the report.

`确保` skips 明确保留 ("explicitly kept"), which holds it by accident; 链邻数据 skips 链邻数据库 and
链邻数据接口, the database the repository does not ship and the API's own name. Fenced code is
read as prose too, so "accepts" skips `Accept:`, the header of the contract document's request.
"""


def _readable(text: str) -> str:
    """`text` with its URLs and the desktop product's name removed."""
    return DESKTOP_PRODUCT.sub(" ", URL.sub(" ", text))


def _clients_named(text: str) -> list[str]:
    """Which of `CLIENT_NAMES` a clause names, once its URLs and the desktop product are gone."""
    readable = _readable(text)
    return [client for client, name in CLIENT_NAMES.items() if name.search(readable)]


def _claim_markers(text: str, subject: str | None = None) -> list[str]:
    """The markers a clause holds when it names a client, or sits in `subject`'s own document,
    after URLs and the desktop product go."""
    if not (_clients_named(text) or subject):
        return []
    readable = _readable(text)
    return [marker for marker, pattern in CLAIM_MARKERS.items() if pattern.search(readable)]


def _guarded_texts(
    documents: dict[str, str], sources: dict[str, str]
) -> list[tuple[str, int, str]]:
    """Every clause of `documents` and every unit of the diagram `sources`, with its line."""
    return [
        (label, clause.line, clause.text)
        for label, document in documents.items()
        for clause in clauses(document)
    ] + [
        (label, unit.line, unit.text)
        for label, source in sources.items()
        for unit in diagram_units(source, filename=label)
    ]


def _client_claims(texts: Iterable[tuple[str, int, str]]) -> list[str]:
    claims: list[str] = []
    for label, line, text in texts:
        subject = CLIENT_DOCUMENTS.get(label)
        if markers := _claim_markers(text, subject):
            clients = _clients_named(text) or [str(subject)]
            claims.append(
                f"{label}:{line} presents {' and '.join(clients)} as a data source "
                f"({', '.join(markers)}): {text!r}"
            )
    return claims


def _documents() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (*GUARDED_FILES, *(ROOT / label for label in CLIENT_DOCUMENTS))
    }


def _diagram_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in DIAGRAM_GENERATORS
    }


def test_no_document_or_diagram_presents_a_doctor_only_client_as_a_data_source() -> None:
    """Every clause and diagram unit that names ChainLin or AKShare may hold none of
    `CLAIM_MARKERS`.

    Both must still be read, so a reader gone blind fails rather than passes: `README.md` names
    both, ChainLin's contract document names ChainLin, brain-01 and brain-02 draw it, and api-02
    draws AKShare.
    """
    texts = _guarded_texts(_documents(), _diagram_sources())
    expected = {
        "ChainLin": {
            "README.md",
            "docs/api/chainlin-data.zh-CN.md",
            "scripts/generate_brain_diagrams.py",
        },
        "AKShare": {"README.md", "scripts/generate_api_relationship_diagrams.py"},
    }
    for client, labels in expected.items():
        named = {label for label, _, text in texts if CLIENT_NAMES[client].search(text)}
        assert labels <= named, (
            f"{client} is named only in {sorted(named)}; {sorted(labels)} named it when this "
            "was written, so the reader has gone blind"
        )
    claims = _client_claims(texts)
    assert not claims, (
        "\n".join(claims) + "\nOnly `openalpha doctor` constructs either client. Say what the "
        "client is, or which path uses it; fix a diagram in its generator and regenerate it."
    )


# --- What the contract tests assert -----------------------------------------------------------

CONTRACT_TEST: Final[Path] = ROOT / "tests/contract/providers/test_chainlin_provider.py"
"""ChainLin's frozen contract tests."""

CLOCKS: Final[frozenset[str]] = frozenset(field.name for field in dataclasses.fields(Timeline))
"""The four clocks of a `Timeline`."""

FOUR_CLOCKS: Final[re.Pattern[str]] = re.compile(r"四时钟|four[- ]clocks?", re.IGNORECASE)
"""The four clocks named together."""

CONTRACT_TESTS: Final[re.Pattern[str]] = re.compile(
    r"合约测试|合同测试|contract tests?", re.IGNORECASE
)
"""Contract tests, as the documents name them."""

D4A37161_README_67: Final[str] = (
    "**链邻数据接口 API** 已具备合同优先的 BYOK 客户端，Bearer 认证、四时钟 PIT、"
    "数据修订、客户端限流和失败分类都有冻结合约测试。\n"
)
"""`README.md:67`'s first sentence at `4a37161`, verbatim."""


def _asserted_clocks(path: Path) -> frozenset[str]:
    """The clocks the `assert` statements of `path` read, as attributes named for them."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(
        node.attr
        for statement in ast.walk(tree)
        if isinstance(statement, ast.Assert)
        for node in ast.walk(statement.test)
        if isinstance(node, ast.Attribute) and node.attr in CLOCKS
    )


def _four_clock_claims(
    texts: Iterable[tuple[str, int, str]], asserted: frozenset[str]
) -> list[str]:
    """Each clause saying ChainLin's contract tests hold the four clocks, unless they assert all
    four."""
    if asserted >= CLOCKS:
        return []
    return [
        f"{label}:{line} says ChainLin's contract tests hold the four clocks, and they assert "
        f"{sorted(asserted)}: {text!r}"
        for label, line, text in texts
        if (_clients_named(text) or label in CLIENT_DOCUMENTS)
        and CONTRACT_TESTS.search(text)
        and FOUR_CLOCKS.search(text)
    ]


def test_no_document_says_the_contract_tests_hold_clocks_they_do_not_assert() -> None:
    """A clause naming ChainLin and its contract tests may say 四时钟 only while those tests
    assert all four clocks.

    `README.md:67` said Bearer auth, "四时钟 PIT" and revisions all had frozen contract tests.
    `tests/contract/providers/test_chainlin_provider.py` asserts `available_time` and
    `revision_time`, against a fixture that gives event, available and revision time an instant
    each, so a client that swaps two of them fails there. Only 四时钟 is read: a clause naming
    single clocks the tests do not assert passes, which the last assertion measures.
    """
    asserted = _asserted_clocks(CONTRACT_TEST)
    assert asserted, "no clock is read in ChainLin's contract tests, so the scan has gone blind"
    claims = _four_clock_claims(_guarded_texts(_documents(), _diagram_sources()), asserted)
    assert not claims, "\n".join(claims) + "\nName only the clocks the contract tests assert."
    retired = _guarded_texts({"README.md": D4A37161_README_67}, {})
    assert _four_clock_claims(retired, asserted), (
        "README.md:67 at 4a37161 is no longer flagged; if the contract tests now assert all four "
        "clocks, this check has nothing left to hold"
    )
    single = _guarded_texts({"single clocks": "链邻合约测试覆盖事件时间与入库时间。\n"}, {})
    assert not _four_clock_claims(single, asserted), "a clause naming single clocks is now read"


# --- The guard's own tests --------------------------------------------------------------------

D4EF5E4_CLAIMS: Final[dict[str, str]] = {
    "README.md:28": (
        "- **链邻统一数据入口**：已实现 `chainlin-data/v1` 合同型 Provider\N{FULLWIDTH SEMICOLON}"
        "可作为分散接口的统一替代入口，真实时效与精度由用户所配置的链邻服务、授权和上游数据决定。\n"
    ),
    "README.md:51": (
        "- **合规数据接入**：链邻合同型 Provider 是面向 A 股数据的统一入口"
        "\N{FULLWIDTH SEMICOLON}"
        "用户自有 CSV、JSON、JSONL、Parquet 以及 BYOT/可选研究 Adapter 作为补充。\n"
    ),
    "marketing §061": (
        "每个任务仍执行完整证据、Agent、双委员会、风险、组合与验证链，"
        "链邻 Provider 的限流和错误分类也会被尊重。\n"
    ),
}
"""Three ChainLin claims as they stood at `d4ef5e4`, verbatim."""

D4A37161_CLAIMS: Final[dict[str, str]] = {
    "README.md:274": (
        "默认支持用户自有 CSV、JSON、JSONL、Parquet，用户自带 Token 的 Tushare Pro，"
        "以及可选、受限的 AKShare Adapter。\n"
    ),
    "README.en.md:56": (
        "The project accepts user-owned CSV, JSON, JSONL, and Parquet data, BYOT Tushare, and an "
        "optional constrained AKShare adapter.\n"
    ),
}
"""AKShare's two claims as they stood at `4a37161`, verbatim."""

D4A37161_CONTRACT_DOCUMENT: Final[str] = (
    "OpenAlpha 在接收时添加自己的 `ingested_time`，然后仍通过统一\n"
    "`ProviderBatch` 和 `EvidenceSnapshot` 管线做 PIT 校验。\n"
)
"""`docs/api/chainlin-data.zh-CN.md:29-30` at `4a37161`, verbatim; it never names ChainLin."""

D4EF5E4_PANELS: Final[str] = (
    'svg.panel(title="A 股证据入口", label="DATA PLANE", lines=("链邻数据接口 API", '
    '"已实现 · 统一替代入口", "用户授权实时 A 股数据"))\n'
    'svg.panel(title="链邻数据接口 API", label="LICENSED DATA", lines=("已实现 · 统一替代入口", '
    '"Bearer 鉴权 · 客户端限流", "时效 / 精度以链邻服务为准"))\n'
)
"""brain-01's and brain-02's ChainLin panels at `d4ef5e4`: their strings verbatim, in a call of
the same kind with the layout arguments left out."""


def test_the_guard_flags_the_claims_it_was_written_for() -> None:
    """The retroactive power, held: each retired claim and both panels are flagged."""
    missed = [
        label
        for label, text in {**D4EF5E4_CLAIMS, **D4A37161_CLAIMS}.items()
        if not _client_claims(_guarded_texts({label: text}, {}))
    ]
    panels = _client_claims(_guarded_texts({}, {"d4ef5e4 brain": D4EF5E4_PANELS}))
    assert not missed, f"a retired claim is no longer flagged: {missed}"
    assert len(panels) == 2, f"the two d4ef5e4 panels were read as {panels}"
    contract = {"docs/api/chainlin-data.zh-CN.md": D4A37161_CONTRACT_DOCUMENT}
    elsewhere = {"README.md": D4A37161_CONTRACT_DOCUMENT}
    assert _client_claims(_guarded_texts(contract, {})), (
        "the contract document's unnamed pipeline claim is no longer flagged"
    )
    assert not _client_claims(_guarded_texts(elsewhere, {})), (
        "the same sentence is flagged outside ChainLin's own document, so it is not the "
        "document's subject that reads it"
    )


MARKER_SENTENCES: Final[dict[str, str]] = {
    "入口": "链邻是数据入口。",
    "接入": "链邻已经接入。",
    "送入": "链邻把行情送入研究。",
    "纳入": "链邻被纳入研究链。",
    "带入": "链邻把行情带入研究。",
    "进入": "链邻行情进入研究。",
    "衔接": "链邻衔接授权行情。",
    "连接": "链邻连接授权服务。",
    "可接": "链邻可接授权行情。",
    "即可": "配好链邻即可研究。",
    "默认支持": "默认支持 AKShare。",
    "统一": "链邻是统一方案。",
    "尊重": "链邻的限流会被尊重。",
    "保证": "链邻保证行情一致。",
    "确保": "链邻确保行情一致。",
    "生成…证据": "链邻行情生成证据。",
    "构建…证据": "链邻行情构建证据。",
    "提供…数据": "链邻提供行情数据。",
    "经过…合同": "链邻行情经过合同。",
    "链邻数据": "链邻数据很全。",
    "entry": "ChainLin is the market's entry.",
    "unified": "ChainLin is unified.",
    "gateway": "ChainLin is a gateway.",
    "one-stop": "ChainLin is one-stop.",
    "feeds": "ChainLin feeds research.",
    "ingests": "The workbench ingests ChainLin.",
    "connects": "The workbench connects to ChainLin.",
    "plugs into": "ChainLin plugs into research.",
    "ready to use": "ChainLin is ready to use.",
    "accepts": "The project accepts AKShare.",
    "ChainLin data": "ChainLin data is complete.",
    "provides data": "ChainLin provides quotes and data.",
}
"""One sentence per marker, which that marker catches and no other does."""


def test_every_claim_marker_is_needed() -> None:
    """Each marker is held by a sentence only it catches, so dropping one fails here."""
    assert set(MARKER_SENTENCES) == set(CLAIM_MARKERS), (
        f"MARKER_SENTENCES holds {sorted(MARKER_SENTENCES)}, CLAIM_MARKERS {sorted(CLAIM_MARKERS)}"
    )
    wrong = {
        marker: _claim_markers(sentence)
        for marker, sentence in MARKER_SENTENCES.items()
        if _claim_markers(sentence) != [marker]
    }
    assert not wrong, f"a sentence is not caught by its own marker alone: {wrong}"


TRUE_SENTENCES: Final[tuple[str, ...]] = (
    "链邻 Provider 已实现客户端合同。",
    "链邻 Provider 的修订语义也被明确保留。",
    "仓库不内置或转售链邻商业数据。",
    "不公开链邻数据库、用户知识库、Token 或商业原始数据。",
    "链邻数据接口 API 的客户端只由 doctor 构造。",
    "点击上方按钮，或进入 [链邻桌面软件 Release 页面]"
    "(https://github.com/ss8875/openalpha-cn/releases/tag/chainlin-desktop-v1.0.9) 下载。",
    "无法下载：进入 [Release 页面]"
    "(https://github.com/ss8875/openalpha-cn/releases/tag/chainlin-desktop-v1.0.9) 重新下载。",
    "Third-party data and the ChainLin installer retain their own licensing boundaries.",
    "链邻 API、用户文件、Tushare 和可选 AKShare Adapter 位于调用方或 Provider 侧。",
    "进入 [版本页面](https://github.com/ss8875/openalpha-cn/releases/tag/chainlin-v1.0.9) 查看。",
    "链邻 Provider 已实现客户端合同，面板构建不调用它，证据来自用户文件。",
)
"""True sentences that name ChainLin, its database, its API, its desktop product or AKShare; each
must pass. At `20fec55` the first four, the seventh and the ninth stood verbatim in the four
documents, and the sixth and the eighth shortened sentences of `README.md` and `README.en.md`. The
fifth and the last two were written for this test: the fifth names the API by its own name, and in
each of the last two one rule alone keeps it from reading as a claim -- the link names ChainLin
only inside its URL, and 构建 and 证据 sit in different comma-separated parts of one clause."""


def test_true_sentences_about_chainlin_pass() -> None:
    """URLs, the desktop product, 明确保留 and the API's own name are not read as claims."""
    wrongly = {sentence: _claim_markers(sentence) for sentence in TRUE_SENTENCES}
    assert not any(wrongly.values()), f"a true sentence was read as a claim: {wrongly}"


CLAIMS_RIGHT_AFTER_A_LINK: Final[dict[str, str]] = {
    "a markdown link, then the claim": "[链邻数据接口](https://example.com/api)已接入研究链。",
    "a bare URL, then a comma and the claim": (
        "链邻 Provider 文档见 https://example.com/x，已接入研究链。"
    ),
    "a full-width right parenthesis": "链邻 Provider 文档见 https://example.com/x）已接入研究链。",
    "a full-width colon": "链邻 Provider 文档见 https://example.com/x：已接入研究链。",
    "a corner bracket": (
        "链邻 Provider 文档见\N{LEFT CORNER BRACKET}https://example.com/x"
        "\N{RIGHT CORNER BRACKET}已接入研究链。"
    ),
    "a double angle bracket": (
        "链邻 Provider 文档见\N{LEFT DOUBLE ANGLE BRACKET}https://example.com/x"
        "\N{RIGHT DOUBLE ANGLE BRACKET}已接入研究链。"
    ),
    "a lenticular bracket": (
        "链邻 Provider 文档见\N{LEFT BLACK LENTICULAR BRACKET}https://example.com/x"
        "\N{RIGHT BLACK LENTICULAR BRACKET}已接入研究链。"
    ),
    "a closing quotation mark": (
        "链邻 Provider 文档见\N{LEFT DOUBLE QUOTATION MARK}https://example.com/x"
        "\N{RIGHT DOUBLE QUOTATION MARK}已接入研究链。"
    ),
    "an ASCII comma, then the claim": "链邻 Provider 文档见 https://example.com/x,已接入研究链。",
    "an ASCII parenthesis, then the name": "[文档](https://example.com/x)ChainLin 已接入研究链。",
    "an ASCII square bracket, then the name": "[https://example.com/x]ChainLin 已接入研究链。",
    "an autolink, then the name": "<https://example.com/x>ChainLin 已接入研究链。",
}
"""Claims written straight after a link, each of which must be read.

The review of `D13` put the first into `README.md`, and the guard passed: `\\S+` ran the URL on to
the next whitespace, and Chinese puts none after a link. The review of the fixes found the URL
still ran on through the full-width punctuation its list left out; the next six probes are those
marks, and the ASCII comma after them is the same fault. The last three hold the ASCII closing
brackets, which end a URL even when ASCII text follows."""


CLAIMS_BESIDE_A_DESKTOP_WORD: Final[dict[str, str]] = {
    "链邻软件": "链邻软件接口已接入研究链。",
    "链邻安装": "链邻安装后即可接入研究链。",
}
"""The final review of `D13` found `DESKTOP_PRODUCT` taking 链邻 with it from any clause where 软件,
安装 or Windows followed: the only name went, and the claim with it."""

DESKTOP_NAMES_BESIDE_A_MARKER: Final[tuple[str, ...]] = (
    "进入链邻桌面软件的下载页面。",
    "进入链邻涨停复盘策略软件的下载页面。",
    "进入链邻安装版的下载页面。",
    "进入链邻 Windows 软件的下载页面。",
    "The ChainLin installer is the entry to the desktop product.",
)
"""The desktop product's names as the four documents write them, each beside a marker (进入, or
"entry"). Each name is removed before the clause is read, so none of these is a claim."""


def test_a_desktop_word_does_not_hide_the_client() -> None:
    """Only the desktop product's own names are removed: 链邻 stays beside a bare 软件 or 安装."""
    missed = [
        label
        for label, sentence in CLAIMS_BESIDE_A_DESKTOP_WORD.items()
        if "接入" not in _claim_markers(sentence)
    ]
    assert not missed, f"a claim beside a desktop word went unread: {missed}"
    read = {sentence: _claim_markers(sentence) for sentence in DESKTOP_NAMES_BESIDE_A_MARKER}
    assert not any(read.values()), f"a desktop product's name was read as the client: {read}"


def test_a_claim_written_right_after_a_link_is_read() -> None:
    """A URL ends at whitespace, a closing ASCII bracket or the first character outside ASCII."""
    missed = [
        label
        for label, sentence in CLAIMS_RIGHT_AFTER_A_LINK.items()
        if "接入" not in _claim_markers(sentence)
    ]
    assert not missed, f"a claim right after a link went unread: {missed}"


def test_the_stated_limits_are_real() -> None:
    """Each limit the module docstring states, measured in both directions.

    A case in `unseen` that starts being flagged has closed a blind spot, and one in `refused`
    that starts passing has closed an over-reach: change the docstring with it.
    """
    unseen = {
        "a claim through a condition alone": (
            "链邻 Provider 已实现客户端合同，真实数据调用仍需用户配置服务地址、Token 和合法授权。\n"
        ),
        "a claim carried by a pronoun into the next clause": (
            "链邻 Provider 已实现客户端合同。数据随后被固化为不可变证据。\n"
        ),
        "an English claim outside the markers": "ChainLin powers every research run.\n",
        "a claim whose only name is the desktop product's": "链邻桌面软件已接入研究链。\n",
    }
    two_calls = 'svg.text(1, 2, "链邻数据接口 API")\nsvg.text(1, 3, "已实现 · 统一替代入口")\n'
    flagged_anyway = {
        label: claims
        for label, text in unseen.items()
        if (claims := _client_claims(_guarded_texts({label: text}, {})))
    }
    assert not flagged_anyway, f"a stated blind spot is now flagged: {flagged_anyway}"
    assert not _client_claims(_guarded_texts({}, {"two calls": two_calls})), (
        "a name and a claim drawn by two calls are now read together"
    )
    run_time = 'name = "链邻数据接口 API"\nsvg.text(1, 2, f"{name} · 统一替代入口")\n'
    assert not _client_claims(_guarded_texts({}, {"run time": run_time})), (
        "text a generator computes at run time is now read"
    )
    two_rows = 'ROWS = (\n    (1, "链邻数据接口 API"),\n    (2, "统一替代入口"),\n)\n'
    assert not _client_claims(_guarded_texts({}, {"two rows": two_rows})), (
        "a name in one row of a table and a marker in another are read together again"
    )
    refused = {
        "a denial": "链邻不是统一入口。\n",
        "another subject's marker": (
            "链邻 Provider 负责认证、限流和错误分类，四时钟保证 Agent 只读当时可见信息。\n"
        ),
        "a URL whose path is not ASCII": "文档见 https://example.com/链邻入口。\n",
    }
    passed = [
        label
        for label, text in refused.items()
        if not _client_claims(_guarded_texts({label: text}, {}))
    ]
    assert not passed, f"a stated over-reach no longer happens: {passed}"
    caution = {"docs/api/chainlin-data.zh-CN.md": "不得把尚未配置的服务宣传为已连接。\n"}
    assert _client_claims(_guarded_texts(caution, {})), (
        "a true caution in ChainLin's contract document is no longer refused"
    )


def test_the_reference_scan_finds_what_its_docstring_says(tmp_path: Path) -> None:
    """`_uses_of` over a synthetic tree, one shape per file.

    Found: an alias, an attribute, a nested function, a dict value, a `functools.partial`
    argument, a binding to another name, an attribute read through `importlib.import_module`, a
    name re-exported under an alias and read in another module, directly or as an attribute of
    its package, and -- the over-reach the module docstring states -- an unrelated `Client` that
    shares the alias's name. Not found: a definition, which is no read, and `getattr` by a
    string, the blind spot the module docstring states. The factory renamed and re-exported the
    same way is found where it is called.
    """
    root = tmp_path / "pkg"
    files = {
        "aliased.py": (
            "from openalpha_cn.providers import ChainLinDataProvider as Client\n\n\n"
            "def build():\n    return Client(base_url=None)\n"
        ),
        "attribute.py": (
            "import openalpha_cn.providers.chainlin as chainlin\n\n"
            "PROVIDER = chainlin.ChainLinDataProvider(base_url=None)\n"
        ),
        "nested.py": (
            "def outer():\n    def inner():\n        return ChainLinDataProvider()\n\n"
            "    return inner\n"
        ),
        "definition.py": "class ChainLinDataProvider:\n    pass\n",
        "registry.py": (
            "from openalpha_cn.providers import ChainLinDataProvider\n\n\n"
            'def build(name):\n    return {"chainlin": ChainLinDataProvider}[name]()\n'
        ),
        "partial.py": (
            "import functools\n\nfrom openalpha_cn.providers import ChainLinDataProvider\n\n\n"
            "def build():\n    return functools.partial(ChainLinDataProvider, base_url=None)()\n"
        ),
        "bound.py": (
            "from openalpha_cn.providers import ChainLinDataProvider\n\n"
            "FACTORY = ChainLinDataProvider\n"
        ),
        "by_name_string.py": (
            "import openalpha_cn.providers as providers\n\n"
            'PROVIDER = getattr(providers, "ChainLinDataProvider")()\n'
        ),
        "by_importlib.py": (
            "import importlib\n\n"
            'PROVIDER = importlib.import_module("openalpha_cn.providers").ChainLinDataProvider()\n'
        ),
        "reexport.py": (
            "from openalpha_cn.providers.chainlin import ChainLinDataProvider as Client\n"
        ),
        "through_the_reexport.py": (
            "from openalpha_cn.providers import Client\n\n\ndef build():\n    return Client()\n"
        ),
        "through_the_package.py": (
            "import openalpha_cn.providers as providers\n\nPROVIDER = providers.Client()\n"
        ),
        "unrelated.py": "from httpx import Client\n\n\ndef fetch():\n    return Client()\n",
    }
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    found = sorted(_uses_of("ChainLinDataProvider", [root]))
    assert found == [
        ("pkg/aliased.py", "build"),
        ("pkg/attribute.py", "<module>"),
        ("pkg/bound.py", "<module>"),
        ("pkg/by_importlib.py", "<module>"),
        ("pkg/nested.py", "inner"),
        ("pkg/partial.py", "build"),
        ("pkg/registry.py", "build"),
        ("pkg/through_the_package.py", "<module>"),
        ("pkg/through_the_reexport.py", "build"),
        ("pkg/unrelated.py", "fetch"),
    ], f"the reference scan found {found}"
    renamed = tmp_path / "renamed"
    renamed.mkdir()
    (renamed / "commands.py").write_text(
        "def _default_providers():\n    return []\n", encoding="utf-8"
    )
    (renamed / "reexport.py").write_text(
        "from renamed.commands import _default_providers as defaults\n", encoding="utf-8"
    )
    (renamed / "caller.py").write_text(
        "from renamed.reexport import defaults\n\n\ndef run():\n    return defaults()\n",
        encoding="utf-8",
    )
    callers = _uses_of("_default_providers", [renamed])
    assert callers == [("renamed/caller.py", "run")], f"the renamed factory was read at {callers}"
