"""User-facing prose may not present model usage recording as something a shipped path does.

**The code fact this guard stands on**, measured at `2d197af`, re-measured at `ea88999`, and held
by the premise test below rather than by this paragraph. The usage ledger exists: `build_storage`
constructs `SQLiteModelUsageStore` for every runtime directory, so every shipped path -- CLI, REST,
SDK -- creates its `model_usage` table in `state.sqlite3`. Nothing shipped writes a row to it. The
one writer is `OpenAICompatibleProvider._record_usage`, which appends a `ModelUsageRecord`
(tokens, attempts, and a cost estimated from user-configured prices) when the provider was built
with a `usage_store` and the endpoint reported usage. No module under `src/` or `scripts/`
constructs that provider -- only `tests/unit/models/` does -- and no CLI command, REST route or
SDK method reads or writes the ledger. Beneath that sits a wider fact: no shipped path calls a
model at all. The one model call under `src/` is `StructuredSignalAgent.analyze`'s
`generate_json`, and nothing under `src/` or `scripts/` constructs that agent or a
`ModelRegistry`; a model is reached only through code a user writes -- an agent they build and
pass in, as `OpenAlphaSDK(agents=...)` accepts. So "Token and estimated cost are recorded" is
true of a component a deployer can wire, and false of the product as it ships.

Two couplings:

1. **The premise.** `test_no_shipped_path_calls_a_model_or_records_usage` reads the syntax tree
   of every `.py` file under `src/` and `scripts/`, counts the places that could make a model
   call or usage recording reachable -- per module, enclosing scope and kind -- and holds the
   counts equal to `USAGE_SITES`, where each place carries the reason it neither calls a model
   nor records usage. The day a shipped path starts doing either in a way the scan reads, that
   test fails and asks for the documents to be rewritten -- and for this guard to be retired or
   inverted -- instead of this guard silently blocking claims that have become true. The ways
   the scan cannot read are listed below; a change made one of those ways passes it.

2. **The prose.** `README.md`, `README.en.md`, `docs/why-openalpha-cn.zh-CN.md` and
   `docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md` may not present Token, usage or
   cost recording, or billing, as something the product does unless the same clause says no
   shipped path does it. Section 057 of the marketing pack is the model of a true sentence:
   "模型调用的 Token 与估算成本另有账本，但要接入自带用量追踪的 Provider 才会写入，
   出厂路径不会自动生成账单。"
   `d4af27c` corrected that one sentence without a test; this module is the test.

**How the guard in (2) reads a document.**

- *Blocks and clauses* come from `tests/prose_clauses.py`, the reader
  `test_attribution_claims_match_known_limitations.py` shares; its docstring states how a block
  is folded and where a clause ends. A heading, a hook and a table row are each read.
- *Usage markers.* A clause names usage recording when any one of these holds:
  - it holds one of `USAGE_TERMS`;
  - it matches `MODEL_COST` (模型, at most four characters with no punctuation between, then
    成本: 模型成本, 模型能力与成本);
  - it names a token -- "Token" in any case as an English word, plural included -- beside one of
    the words in `TOKEN_ACCOUNTING`. A bare token is not enough: these documents say Token for a
    data-service credential more often than for model usage;
  - it holds `COST` (成本, except as 交易成本 or 机会成本, the two costs the validation layer
    really attributes, or as 低成本, "low cost") together with one of
    `COST_BEARERS` (模型, 委员会, 调用, 配置, Agent, LLM, Token) and one of `COST_MEASURES`
    (比较, 分析, 增加了多少, 多花, 记下, 记录, 追踪, 统计, 核算). This is the shape of section
    037's two per-configuration cost claims, which the first version of this guard missed;
  - it holds `ENGLISH_COST` ("cost", "costs", "spend") beside one of `ENGLISH_RECORDING`
    (record, track, account, meter, ledger, persist, as word starts: "recorded" counts).
- *Claims.* A clause that names usage recording is a claim unless it holds one of
  `SHIPPED_PATH_CONDITIONS`, not directly negated (`prose_clauses.holds_unnegated_phrase`). The
  condition exempts only the clause it sits in.
- *`ALLOWLIST`* holds the true non-claims the markers catch -- a statement about the test suite,
  a topic named as a channel suggestion -- each pinned to one clause by that clause's whole text
  as the shared reader produces it (soft wraps folded, runs of whitespace collapsed), with a
  short excerpt that only finds the clause, and the reason the clause is true. A clause is exempt
  only while its text equals the pinned text, so any change to its words -- a claim appended, a
  word swapped -- ends the exemption and the prose test fails on it until someone re-reads the
  clause and re-pins it or rewrites it, which
  `test_a_claim_written_into_an_allowlisted_clause_ends_its_exemption` measures. The test
  `test_every_usage_allowlist_entry_exempts_exactly_one_flagged_clause` fails on an entry whose
  excerpt finds no clause or several, whose clause no longer reads as pinned, or whose clause
  the guard no longer flags. With the prose test that makes the allowlist the census: every
  clause the guard flags in the four files is either rewritten or listed there.

**What the prose guard cannot see.** `test_the_usage_guards_stated_blind_spots_are_real`
measures each of these.

- A clause naming none of the markers is never a claim, however plainly it implies recording:
  "你可以比较各委员会各自多花了多少。" passes, and so does a cost named with a bearer but without
  one of `COST_MEASURES`: "每个委员会的成本一目了然。".
- Within one clause, one condition phrase exempts every marker in it, even a contradicting one:
  "Token 与估算成本持续入账，出厂路径不会自动生成账单。" passes.
- A claim split across two blocks or two clauses is read as two halves, each innocent.
- The other direction: a credential token beside an accounting word is read as usage
  ("Token 持久保存在环境变量里。" is flagged), a transaction cost spelled other than 交易成本
  beside a bearer and a measuring verb is read as a model cost ("佣金成本随每个 Agent 的成交一起
  记录。" is flagged), and a condition worded outside `SHIPPED_PATH_CONDITIONS` is read as a claim.

**What the premise test reads.** Seven names, `SHIPPED_PATH_NAMES` -- `OpenAICompatibleProvider`,
`ModelUsageRecord`, `StructuredSignalAgent`, `ModelRegistry`, `generate_json`, the provider's
`usage_store` parameter and `StorageContainer.model_usage_store` -- and the usage table.

- *A name* is counted wherever the code names it: a bare name that is it, or that an import in
  the same module binds to it (`import ... as` and `from ... import ... as`, resolved the way
  `tests/unit/test_repository_assets.py`'s multiprocessing audit resolves them, in any scope of
  the module); an attribute spelled that way on any object; an import of it; a keyword argument
  spelled that way; and a string exactly equal to it (`getattr(module, "generate_json")`, an
  `__all__` entry). A definition is not a use: a class, a function or a parameter of that name
  is not counted.
- *The usage table* is counted wherever a string, or a literal part of an f-string, names
  `model_usage` as a whole identifier (`model_usage_store` and `model_usage_archive` do not),
  and separately wherever that string is SQL writing it: `INSERT [OR ...] INTO`, `REPLACE INTO`
  or `UPDATE`, with the table bare, quoted, backticked, bracketed or schema-prefixed. A write
  whose table is computed -- an f-string placeholder, `{}`, `%s` or `%(name)s` where the table
  name would stand -- is counted as a kind of its own, because it could be any table.
- A docstring is never counted: it can name all seven and call nothing.
- *Counts, not presence.* A second construction in a scope that already names the provider
  changes that scope's count and fails exactly as a new scope does.

**What the premise test cannot see.** It reads names and literals, never values or behaviour.

- A name built at run time -- `importlib` with a computed module or attribute name,
  `getattr(obj, "generate" + "_json")` -- or a table name assembled from pieces.
- A value traced through anything but an import. `record_type = governance.ModelUsageRecord` is
  counted where the attribute is named, and `record_type(**fields)` is not counted at all, so a
  call through a local binding adds nothing to its scope's count.
- A model or a ledger reached without these seven names: an agent that posts to an HTTP endpoint
  itself, a provider class written anew under another name, a record rebuilt by `model_copy`
  from one read back, or usage written to a table other than `model_usage`.
- The React workbench under `web/`: it is not Python, and it reaches the product only through
  the REST routes, which are scanned.

`test_the_premise_scan_sees_each_shape_of_model_call_and_usage_recording` pins what the scan
reads, one synthetic module per shape, and
`test_the_premise_goes_red_on_each_injection_the_review_measured` appends each injection the
review of `D7` measured -- seven of which the first version of this scan passed -- and each
model-call entry point to a real module under `src/`, and a usage write to one under
`scripts/`, and requires the premise to fail every time.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from prose_clauses import clauses, holds_unnegated_phrase, marker_pattern

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"
MARKETING: Final[Path] = ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md"

GUARDED_FILES: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA, MARKETING)


# --- Part 1: the premise -- no shipped path calls a model or records usage --------------------

NON_TEST_SOURCE_ROOTS: Final[tuple[Path, ...]] = (ROOT / "src", ROOT / "scripts")
"""Where shipped code lives. `tests/` is the one tree allowed to build the provider freely."""

PROVIDER_NAMED: Final[str] = "names the provider"
RECORD_NAMED: Final[str] = "names the usage record"
AGENT_NAMED: Final[str] = "names the model-backed agent"
REGISTRY_NAMED: Final[str] = "names the model registry"
MODEL_CALL_NAMED: Final[str] = "names the model call"
PROVIDER_STORE_NAMED: Final[str] = "names the provider's usage store"
RUNTIME_STORE_NAMED: Final[str] = "names the runtime's usage store"
TABLE_NAMED: Final[str] = "names the usage table"
TABLE_WRITTEN: Final[str] = "writes the usage table"
COMPUTED_TABLE_WRITTEN: Final[str] = "writes a computed table"

SHIPPED_PATH_NAMES: Final[dict[str, str]] = {
    "OpenAICompatibleProvider": PROVIDER_NAMED,
    "ModelUsageRecord": RECORD_NAMED,
    "StructuredSignalAgent": AGENT_NAMED,
    "ModelRegistry": REGISTRY_NAMED,
    "generate_json": MODEL_CALL_NAMED,
    "usage_store": PROVIDER_STORE_NAMED,
    "model_usage_store": RUNTIME_STORE_NAMED,
}
"""Each name through which a shipped path could reach a model call or the usage ledger, and the
kind of site a place that names it is. The provider, the record and the two stores are the usage
half; the agent, the registry and `generate_json` are the model-call entry points."""

USAGE_TABLE: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9_])model_usage(?![A-Za-z0-9_])")

_SQL_NAME: Final[str] = r"[`\"\[]?[A-Za-z_][A-Za-z0-9_]*[`\"\]]?"
_SQL_WRITE: Final[str] = (
    rf"\b(?:insert|replace|update)\b(?:\s+or\s+\w+)?(?:\s+into)?\s+(?:{_SQL_NAME}\s*\.\s*)?[`\"\[]?"
)
USAGE_TABLE_WRITE: Final[re.Pattern[str]] = re.compile(
    _SQL_WRITE + r"model_usage(?![A-Za-z0-9_])", re.IGNORECASE
)
"""SQL that writes the ledger's table: `INSERT [OR ...] INTO`, `REPLACE INTO`, `UPDATE`, the table
bare, quoted, backticked, bracketed or behind a schema prefix (`main.model_usage`)."""

PLACEHOLDER: Final[str] = "\x00"
"""What stands in for an f-string's formatted value when its literal parts are joined."""

COMPUTED_TABLE_WRITE: Final[re.Pattern[str]] = re.compile(
    _SQL_WRITE + r"(?:\x00|\{[^}]*\}|%s|%\(\w+\)s)", re.IGNORECASE
)
"""SQL whose written table is computed: a formatted value, `{}`, `%s` or `%(name)s` where the
table name would stand."""


@dataclass(frozen=True, slots=True, order=True)
class UsageSite:
    """One place in a module that could make a model call or usage recording reachable.

    `scope` is the dotted name of the enclosing class and function, or `<module>`.
    """

    path: str
    scope: str
    kind: str


def _import_bindings(tree: ast.AST) -> dict[str, set[str]]:
    """Every name an import in `tree` binds, in any scope, to the dotted paths it stands for."""
    bound: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    bound.setdefault(alias.asname, set()).add(alias.name)
                else:
                    package = alias.name.partition(".")[0]
                    bound.setdefault(package, set()).add(package)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            for alias in node.names:
                bound.setdefault(alias.asname or alias.name, set()).add(f"{module}.{alias.name}")
    return bound


def _docstring_ids(tree: ast.AST) -> set[int]:
    """The `id` of every docstring constant in `tree`: module, class and function docstrings."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


class _ShippedPathScan(ast.NodeVisitor):
    """Count every `UsageSite` in one module's syntax tree."""

    def __init__(self, path: str, tree: ast.AST) -> None:
        self.path = path
        self.scopes: list[str] = []
        self.sites: Counter[UsageSite] = Counter()
        self.bound = _import_bindings(tree)
        self.docstrings = _docstring_ids(tree)

    def _found(self, kind: str) -> None:
        self.sites[UsageSite(self.path, ".".join(self.scopes) or "<module>", kind)] += 1

    def _scoped(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    visit_ClassDef = _scoped
    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped

    def _name_kind(self, name: str) -> str | None:
        """The kind `name` names, directly or through what an import binds it to, or None."""
        if name in SHIPPED_PATH_NAMES:
            return SHIPPED_PATH_NAMES[name]
        for bound in sorted(self.bound.get(name, ())):
            last = bound.rpartition(".")[2]
            if last in SHIPPED_PATH_NAMES:
                return SHIPPED_PATH_NAMES[last]
        return None

    def visit_Name(self, node: ast.Name) -> None:
        kind = self._name_kind(node.id)
        if kind is not None:
            self._found(kind)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in SHIPPED_PATH_NAMES:
            self._found(SHIPPED_PATH_NAMES[node.attr])
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        for name in {node.name.rpartition(".")[2], node.asname}:
            if name in SHIPPED_PATH_NAMES:
                self._found(SHIPPED_PATH_NAMES[name])

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg in SHIPPED_PATH_NAMES:
            self._found(SHIPPED_PATH_NAMES[node.arg])
        self.generic_visit(node)

    def _string(self, value: str, *, whole: bool) -> None:
        if value in SHIPPED_PATH_NAMES:
            self._found(SHIPPED_PATH_NAMES[value])
        if USAGE_TABLE.search(value):
            self._found(TABLE_NAMED)
        if USAGE_TABLE_WRITE.search(value):
            self._found(TABLE_WRITTEN)
        elif whole and COMPUTED_TABLE_WRITE.search(value):
            self._found(COMPUTED_TABLE_WRITTEN)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            self._string(node.value, whole=True)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        """An f-string: its literal parts are read as strings, and its whole text, with each
        formatted value replaced by `PLACEHOLDER`, as SQL that may write a computed table."""
        text = "".join(
            part.value
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else PLACEHOLDER
            for part in node.values
        )
        if COMPUTED_TABLE_WRITE.search(text) and not USAGE_TABLE_WRITE.search(text):
            self._found(COMPUTED_TABLE_WRITTEN)
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                self._string(part.value, whole=False)
            else:
                self.visit(part)


def _usage_sites(sources: Iterable[tuple[str, str]]) -> Counter[UsageSite]:
    """Every `UsageSite` in `sources`, given as `(path label, module source)` pairs, counted."""
    found: Counter[UsageSite] = Counter()
    for path, source in sources:
        tree = ast.parse(source, filename=path)
        scan = _ShippedPathScan(path, tree)
        scan.visit(tree)
        found += scan.sites
    return found


def _non_test_sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for root in NON_TEST_SOURCE_ROOTS
        for path in sorted(root.rglob("*.py"))
    ]


@dataclass(frozen=True, slots=True)
class Counted:
    """How many times a `UsageSite` occurs today, and why none of them calls or records."""

    count: int
    reason: str


def _site(path: str, scope: str, kind: str) -> UsageSite:
    return UsageSite(f"src/openalpha_cn/{path}", scope, kind)


USAGE_SITES: Final[dict[UsageSite, Counted]] = {
    _site("agents/__init__.py", "<module>", AGENT_NAMED): Counted(
        2,
        "The package re-exports the agent class: an import and its `__all__` entry. Nothing "
        "here constructs it.",
    ),
    _site("agents/model.py", "StructuredSignalAgent.analyze", MODEL_CALL_NAMED): Counted(
        1,
        "The one model call under src/: the agent's own `self.provider.generate_json(...)`. "
        "Nothing under src/ or scripts/ constructs `StructuredSignalAgent` (no other site names "
        "it), so this runs only for an agent a user builds and passes as "
        "`OpenAlphaSDK(agents=...)`.",
    ),
    _site("models/__init__.py", "<module>", PROVIDER_NAMED): Counted(
        2,
        "The package re-exports the provider class: an import and its `__all__` entry. Nothing "
        "here constructs it.",
    ),
    _site("models/governance.py", "ModelUsageStore.append", RECORD_NAMED): Counted(
        1, "The Protocol's parameter annotation; a Protocol writes nothing."
    ),
    _site("models/governance.py", "ModelUsageStore.list", RECORD_NAMED): Counted(
        1, "The Protocol's return annotation; a Protocol writes nothing."
    ),
    _site("models/openai_compatible.py", "<module>", RECORD_NAMED): Counted(
        1, "The import `_record_usage` builds its record from."
    ),
    _site(
        "models/openai_compatible.py", "OpenAICompatibleProvider.__init__", PROVIDER_STORE_NAMED
    ): Counted(
        3,
        "The constructor keeps its own `usage_store` parameter (`self.usage_store = "
        "usage_store`) and reports `usage_reporting` from it. Only whoever constructs the "
        "provider fills that parameter, and nothing under src/ or scripts/ constructs it.",
    ),
    _site(
        "models/openai_compatible.py",
        "OpenAICompatibleProvider._record_usage",
        PROVIDER_STORE_NAMED,
    ): Counted(
        2,
        "`_record_usage` returns at `self.usage_store is None` and otherwise appends to the "
        "store: the one write path, reached only by a provider built with a store.",
    ),
    _site(
        "models/openai_compatible.py", "OpenAICompatibleProvider._record_usage", RECORD_NAMED
    ): Counted(1, "The one writer builds its record here; see the entry above."),
    _site("runtime/composition.py", "StorageContainer", RUNTIME_STORE_NAMED): Counted(
        1,
        "The container's field. No CLI command, REST route or SDK method reads it: no other "
        "site names it.",
    ),
    _site("runtime/composition.py", "build_storage", RUNTIME_STORE_NAMED): Counted(
        3,
        "`build_storage` constructs the store and hands it to the container: a local, a "
        "keyword and the local again. Constructing the store creates its empty table and writes "
        "no row.",
    ),
    _site("storage/migrations.py", "_reconcile", COMPUTED_TABLE_WRITTEN): Counted(
        1,
        "The migration engine's repair audit row: the computed table is `SCHEMA_REPAIRS_TABLE`, "
        "which is `schema_repairs`, not the usage table.",
    ),
    _site("storage/models.py", "<module>", RECORD_NAMED): Counted(
        1, "The import the store's annotations and its read-back use."
    ),
    _site("storage/models.py", "SQLiteModelUsageStore.__init__", TABLE_NAMED): Counted(
        1, "`CREATE TABLE IF NOT EXISTS model_usage`: the empty table every runtime gets."
    ),
    _site("storage/models.py", "SQLiteModelUsageStore.append", RECORD_NAMED): Counted(
        1, "The parameter annotation of `append`, whose one caller is `_record_usage`."
    ),
    _site("storage/models.py", "SQLiteModelUsageStore.append", TABLE_NAMED): Counted(
        2, "The idempotency `SELECT` and the `INSERT` of the entry below."
    ),
    _site("storage/models.py", "SQLiteModelUsageStore.append", TABLE_WRITTEN): Counted(
        1,
        "The ledger's own INSERT, reached only through `append`, whose one caller is "
        "`_record_usage`.",
    ),
    _site("storage/models.py", "SQLiteModelUsageStore.list", RECORD_NAMED): Counted(
        2,
        "The return annotation, and `ModelUsageRecord.model_validate_json`, which rebuilds what "
        "was stored and writes nothing.",
    ),
    _site("storage/models.py", "SQLiteModelUsageStore.list", TABLE_NAMED): Counted(
        2, "The two `SELECT`s that read rows back."
    ),
}
"""Every `UsageSite` outside `tests/`, with how often it occurs and why it neither calls a model
nor records usage."""


def _premise_problems(found: Counter[UsageSite]) -> list[str]:
    """One message per site whose count in `found` differs from `USAGE_SITES`."""
    expected = {site: entry.count for site, entry in USAGE_SITES.items()}
    return [
        f"{site.path} :: {site.scope} :: {site.kind}: counted {found.get(site, 0)}, "
        f"USAGE_SITES records {expected.get(site, 0)}"
        for site in sorted(set(found) | set(expected))
        if found.get(site, 0) != expected.get(site, 0)
    ]


def test_no_shipped_path_calls_a_model_or_records_usage() -> None:
    """The premise of the prose guard below, read off the code rather than remembered.

    Equal counts, not a subset: a site that disappears fails too, so a scan that stopped finding
    anything cannot pass as a clean one, and a second occurrence in a counted scope fails as a
    new scope does.
    """
    problems = _premise_problems(_usage_sites(_non_test_sources()))
    assert not problems, (
        "\n".join(problems) + "\nIf a shipped path now calls a model, builds "
        "OpenAICompatibleProvider or StructuredSignalAgent, hands a provider a usage store or "
        "writes model_usage rows, the prose guard's premise is false: rewrite the model and "
        "usage sentences in the four guarded documents to say what the shipped path does now, "
        "and retire or invert test_user_facing_docs_do_not_present_usage_recording_as_shipped. "
        "If the place calls and records nothing, record it in USAGE_SITES with its count and "
        "the reason."
    )


def _counted(*sites: tuple[str, str, int]) -> Counter[UsageSite]:
    return Counter({UsageSite("m.py", scope, kind): count for scope, kind, count in sites})


SCAN_CASES: Final[dict[str, tuple[str, Counter[UsageSite]]]] = {
    "a direct construction": (
        "from openalpha_cn.models import OpenAICompatibleProvider\n\n"
        "def build():\n"
        "    return OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "        base_url='https://example.test', api_key_env=None)\n",
        _counted(("<module>", PROVIDER_NAMED, 1), ("build", PROVIDER_NAMED, 1)),
    ),
    "an aliased import, and a call through the alias": (
        "from openalpha_cn.models import OpenAICompatibleProvider as Provider\n\n"
        "def build():\n"
        "    return Provider(provider_id='p', model='m', base_url='https://example.test',\n"
        "        api_key_env=None)\n",
        _counted(("<module>", PROVIDER_NAMED, 1), ("build", PROVIDER_NAMED, 1)),
    ),
    "an attribute of the package": (
        "import openalpha_cn.models as models\n\n"
        "def build():\n"
        "    return models.OpenAICompatibleProvider\n",
        _counted(("build", PROVIDER_NAMED, 1)),
    ),
    "the name as a string": (
        "import importlib\n\n"
        "def build():\n"
        "    return getattr(importlib.import_module('openalpha_cn.models'),\n"
        "        'OpenAICompatibleProvider')\n",
        _counted(("build", PROVIDER_NAMED, 1)),
    ),
    "a second construction in a scope that already names the provider": (
        "from openalpha_cn.models import OpenAICompatibleProvider\n\n"
        "__all__ = ['OpenAICompatibleProvider']\n"
        "CLASSES = {'openai-compatible': OpenAICompatibleProvider}\n",
        _counted(("<module>", PROVIDER_NAMED, 3)),
    ),
    "a record built directly": (
        "def log(store, **fields):\n    store.append(ModelUsageRecord(**fields))\n",
        _counted(("log", RECORD_NAMED, 1)),
    ),
    "a record built by a classmethod": (
        "class Meter:\n"
        "    def log(self, store, fields):\n"
        "        store.append(ModelUsageRecord.model_validate(fields))\n",
        _counted(("Meter.log", RECORD_NAMED, 1)),
    ),
    "a record built through a module": (
        "from openalpha_cn.models import governance\n\n"
        "def log(store, **fields):\n    store.append(governance.ModelUsageRecord(**fields))\n",
        _counted(("log", RECORD_NAMED, 1)),
    ),
    "a record built under an alias": (
        "from openalpha_cn.models.governance import ModelUsageRecord as UsageRow\n\n"
        "def log(store, **fields):\n    store.append(UsageRow(**fields))\n",
        _counted(("<module>", RECORD_NAMED, 1), ("log", RECORD_NAMED, 1)),
    ),
    "a record class bound to a local first (only the binding counts)": (
        "def log(store, **fields):\n"
        "    from openalpha_cn.models import governance\n"
        "    record_type = governance.ModelUsageRecord\n"
        "    store.append(record_type(**fields))\n"
        "    store.append(record_type(**fields))\n",
        _counted(("log", RECORD_NAMED, 1)),
    ),
    "a usage store handed to a provider built through a lookup": (
        "def wire(storage, classes):\n"
        "    return classes['openai-compatible'](usage_store=storage.model_usage_store)\n",
        _counted(("wire", PROVIDER_STORE_NAMED, 1), ("wire", RUNTIME_STORE_NAMED, 1)),
    ),
    "a usage store set after construction, and passed by name": (
        "def wire(provider, store, factory):\n"
        "    provider.usage_store = store\n"
        "    return factory(**{'usage_store': store})\n",
        _counted(("wire", PROVIDER_STORE_NAMED, 2)),
    ),
    "raw SQL into the table": (
        "def log(connection, row):\n"
        "    connection.execute('INSERT OR IGNORE INTO model_usage VALUES (?, ?, ?)', row)\n",
        _counted(("log", TABLE_NAMED, 1), ("log", TABLE_WRITTEN, 1)),
    ),
    "the table quoted, bracketed, backticked or schema-prefixed": (
        "def log(connection, row):\n"
        "    connection.execute('INSERT INTO \"model_usage\" VALUES (?)', row)\n"
        "    connection.execute('REPLACE INTO [model_usage] VALUES (?)', row)\n"
        "    connection.execute('UPDATE `model_usage` SET payload = ?', row)\n"
        "    connection.execute('INSERT INTO main.model_usage VALUES (?)', row)\n",
        _counted(("log", TABLE_NAMED, 4), ("log", TABLE_WRITTEN, 4)),
    ),
    "a table computed by an f-string and by a format string": (
        "TABLE = 'model_usage'\n\n"
        "def log(connection, row):\n"
        "    connection.execute(f'INSERT INTO {TABLE} VALUES (?)', row)\n"
        "    connection.execute('INSERT INTO {} VALUES (?)'.format(TABLE), row)\n"
        "    connection.execute(f'INSERT INTO model_usage VALUES ({row})')\n",
        _counted(
            ("<module>", TABLE_NAMED, 1),
            ("log", COMPUTED_TABLE_WRITTEN, 2),
            ("log", TABLE_NAMED, 1),
            ("log", TABLE_WRITTEN, 1),
        ),
    ),
    "a model call, directly and by name": (
        "def ask(provider):\n"
        "    provider.generate_json(system='s', user='u', schema={})\n"
        "    return getattr(provider, 'generate_json')(system='s', user='u', schema={})\n",
        _counted(("ask", MODEL_CALL_NAMED, 2)),
    ),
    "the model-backed agent built under an alias": (
        "from openalpha_cn.agents import StructuredSignalAgent as ModelAgent\n\n"
        "def build(provider):\n"
        "    return ModelAgent(agent_id='a', evidence_families=frozenset(), provider=provider)\n",
        _counted(("<module>", AGENT_NAMED, 1), ("build", AGENT_NAMED, 1)),
    ),
    "the model registry built": (
        "from openalpha_cn.models.governance import ModelRegistry\n\n"
        "def registry():\n    return ModelRegistry(())\n",
        _counted(("<module>", REGISTRY_NAMED, 1), ("registry", REGISTRY_NAMED, 1)),
    ),
    "what names nothing it could call or write": (
        '"""Mentions OpenAICompatibleProvider, ModelUsageRecord, generate_json and\n'
        'model_usage_store, and INSERTs nothing INTO model_usage."""\n\n'
        "class ModelRegistry:\n"
        '    """A class of the same name is a definition, not a use."""\n\n'
        "def generate_json(system, user, schema, usage_store=None):\n"
        "    return {}\n\n"
        "def read(connection):\n"
        "    connection.execute('SELECT payload FROM model_usage')\n"
        "    connection.execute('CREATE TABLE IF NOT EXISTS model_usage (payload TEXT)')\n"
        "    connection.execute('INSERT INTO model_usage_archive VALUES (?)')\n"
        "    connection.execute('INSERT INTO schema_repairs VALUES (?)')\n",
        _counted(("read", TABLE_NAMED, 2)),
    ),
}
"""Synthetic modules, each with the sites the scan must count for it."""


def test_the_premise_scan_sees_each_shape_of_model_call_and_usage_recording() -> None:
    """The scan behind the premise, held to modules whose sites are known.

    The case whose record class is first bound to a local is the stated limit, measured: the
    two calls through the local add nothing. The last case is the other direction: a docstring,
    a definition, a parameter, a `SELECT`, a `CREATE TABLE` and a write to another table
    name the ledger at most, and must not look as though they call a model or write it.
    """
    wrong = {
        label: sorted(_usage_sites([("m.py", source)]).items())
        for label, (source, expected) in SCAN_CASES.items()
        if _usage_sites([("m.py", source)]) != expected
    }
    assert not wrong, f"the premise scan misread: {wrong}"


PREMISE_INJECTIONS: Final[dict[str, tuple[str, str]]] = {
    "I1: the provider built in a new sdk.py function": (
        "src/openalpha_cn/sdk.py",
        "\n\ndef _probe_build_provider():\n"
        "    from openalpha_cn.models import OpenAICompatibleProvider\n"
        "    return OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "        base_url='https://example.test', api_key_env=None)\n",
    ),
    "S1: raw SQL into the table in cli.py": (
        "src/openalpha_cn/cli.py",
        "\n\ndef _probe_insert(connection, row):\n"
        "    connection.execute('insert into model_usage values (?, ?, ?)', row)\n",
    ),
    "S2: a record appended to the runtime's store in api/app.py": (
        "src/openalpha_cn/api/app.py",
        "\n\ndef _probe_record(storage, **fields):\n"
        "    from openalpha_cn.models.governance import ModelUsageRecord\n"
        "    storage.model_usage_store.append(ModelUsageRecord(**fields))\n",
    ),
    "I2: an aliased record in runtime/composition.py": (
        "src/openalpha_cn/runtime/composition.py",
        "\n\nfrom openalpha_cn.models.governance import ModelUsageRecord as UsageRow\n\n"
        "def _probe_log(store, **fields):\n    store.append(UsageRow(**fields))\n",
    ),
    "I3: the record class bound to a local in api/app.py": (
        "src/openalpha_cn/api/app.py",
        "\n\ndef _probe_local(store, **fields):\n"
        "    from openalpha_cn.models import governance\n"
        "    record_type = governance.ModelUsageRecord\n"
        "    store.append(record_type(**fields))\n",
    ),
    "I4: the provider built at module scope of models/__init__.py": (
        "src/openalpha_cn/models/__init__.py",
        "\n\n_PROBE_DEFAULT = OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "    base_url='https://example.test', api_key_env=None)\n",
    ),
    "I5a: a table of provider classes in models/__init__.py": (
        "src/openalpha_cn/models/__init__.py",
        "\n\nPROVIDER_CLASSES = {'openai-compatible': OpenAICompatibleProvider}\n",
    ),
    "I5b: a provider built through that table with the runtime's store": (
        "src/openalpha_cn/runtime/composition.py",
        "\n\ndef _probe_wire(storage):\n"
        "    from openalpha_cn.models import PROVIDER_CLASSES\n"
        "    return PROVIDER_CLASSES['openai-compatible'](provider_id='p', model='m',\n"
        "        base_url='https://example.test', api_key_env=None,\n"
        "        usage_store=storage.model_usage_store)\n",
    ),
    "I6: the table quoted": (
        "src/openalpha_cn/cli.py",
        "\n\ndef _probe_quoted(connection, row):\n"
        "    connection.execute('INSERT INTO \"model_usage\" VALUES (?, ?, ?)', row)\n",
    ),
    "I7: the table schema-prefixed": (
        "src/openalpha_cn/cli.py",
        "\n\ndef _probe_schema(connection, row):\n"
        "    connection.execute('INSERT INTO main.model_usage VALUES (?, ?, ?)', row)\n",
    ),
    "I8: the table computed by an f-string": (
        "src/openalpha_cn/cli.py",
        "\n\n_PROBE_USAGE_TABLE = 'model_usage'\n\n"
        "def _probe_fstring(connection, row):\n"
        "    connection.execute(f'INSERT INTO {_PROBE_USAGE_TABLE} VALUES (?, ?, ?)', row)\n",
    ),
    "the model-backed agent built in sdk.py": (
        "src/openalpha_cn/sdk.py",
        "\n\ndef _probe_agent(provider):\n"
        "    from openalpha_cn.agents import StructuredSignalAgent\n"
        "    return StructuredSignalAgent(agent_id='a', evidence_families=frozenset(),\n"
        "        provider=provider)\n",
    ),
    "the model-backed agent built under an alias in runtime/composition.py": (
        "src/openalpha_cn/runtime/composition.py",
        "\n\nfrom openalpha_cn.agents import StructuredSignalAgent as ModelAgent\n\n"
        "def _probe_alias_agent(provider):\n"
        "    return ModelAgent(agent_id='a', evidence_families=frozenset(), provider=provider)\n",
    ),
    "the model registry built in api/app.py": (
        "src/openalpha_cn/api/app.py",
        "\n\ndef _probe_registry():\n"
        "    from openalpha_cn.models.governance import ModelRegistry\n"
        "    return ModelRegistry(())\n",
    ),
    "a model called from cli.py": (
        "src/openalpha_cn/cli.py",
        "\n\ndef _probe_call(provider):\n"
        "    return provider.generate_json(system='s', user='u', schema={})\n",
    ),
    "a model called by name from cli.py": (
        "src/openalpha_cn/cli.py",
        "\n\ndef _probe_getattr(provider):\n"
        "    return getattr(provider, 'generate_json')(system='s', user='u', schema={})\n",
    ),
    "a usage row written from a script": (
        "scripts/verify_compose_recovery.py",
        "\n\ndef _probe_script(connection, row):\n"
        "    connection.execute('INSERT INTO model_usage VALUES (?, ?, ?)', row)\n",
    ),
}
"""Code appended to a real shipped module, each an event the premise exists to announce.

`I1`..`I8`, `S1` and `S2` are the injections the review of `D7` measured against the first
version of this scan, which went green on `I2`..`I8`; the rest are the model-call entry points
this version adds, and a usage write from `scripts/`, the other tree the premise reads. Each is
appended to the module's text in memory -- nothing is written to `src/` or `scripts/` -- and the
appended text is valid Python in that module."""


def test_the_premise_goes_red_on_each_injection_the_review_measured() -> None:
    """Every injection must fail the premise, measured against the real source tree.

    `test_no_shipped_path_calls_a_model_or_records_usage` passing on the unmodified tree is the
    other half: together they show the premise tells today's code from each of these edits.
    """
    sources = dict(_non_test_sources())
    passed = []
    for label, (path, addition) in PREMISE_INJECTIONS.items():
        injected = {**sources, path: sources[path] + addition}
        if not _premise_problems(_usage_sites(injected.items())):
            passed.append(label)
    assert not passed, f"the premise stayed green on: {passed}"


# --- Part 2: user-facing prose may not present usage recording as shipped --------------------

USAGE_TERMS: Final[tuple[str, ...]] = (
    "用量",
    "估算成本",
    "估算的成本",
    "尝试次数",
    "入账",
    "账单",
    "计费",
    "算账",
    "usage",
    "billing",
)
"""Words that name usage recording on their own, matched by `prose_clauses.marker_pattern`."""

USAGE_TERM_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    marker_pattern(term) for term in USAGE_TERMS
)

MODEL_COST: Final[re.Pattern[str]] = re.compile(r"模型[一-鿿A-Za-z0-9 ]{0,4}成本")
"""模型, then at most four characters that are not punctuation, then 成本."""

TOKEN: Final[re.Pattern[str]] = marker_pattern("token")

TOKEN_ACCOUNTING: Final[re.Pattern[str]] = re.compile(
    r"成本|记录|记下|入账|持久|写入|账本|核算|消耗|"
    r"(?<![A-Za-z])(?:cost|accounting|ledger|record|persist)",
    re.IGNORECASE,
)
"""Words that make a token a usage figure. English ones match as word starts: "recorded" counts."""

COST: Final[re.Pattern[str]] = re.compile(r"(?<!交易)(?<!机会)(?<!低)成本")
"""成本, except as 交易成本, 机会成本 or 低成本."""

COST_BEARERS: Final[re.Pattern[str]] = re.compile(
    r"模型|委员会|调用|配置|(?<![A-Za-z])(?:agent|llm|token)s?(?![A-Za-z])", re.IGNORECASE
)
"""What makes a 成本 a model-side cost: a model, a committee, a call, a configuration, an Agent,
an LLM or a token."""

COST_MEASURES: Final[re.Pattern[str]] = re.compile(
    r"比较|分析|增加了多少|多花|记下|记录|追踪|统计|核算"
)
"""Verbs that present such a cost as measured or kept."""

ENGLISH_COST: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])(?:costs?|spend(?:s|ing)?)(?![A-Za-z])", re.IGNORECASE
)

ENGLISH_RECORDING: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])(?:record|track|account|meter|ledger|persist)", re.IGNORECASE
)
"""English words that present a cost as kept, matched as word starts."""

SHIPPED_PATH_CONDITIONS: Final[tuple[str, ...]] = ("出厂路径不", "出厂路径都不", "no shipped path")
"""The wordings these documents use to say no shipped path records usage.

A fixed list measured against the rewritten sentences, not a general detector: a caveat worded
any other way is read as a claim.
"""


def _names_usage_recording(clause: str) -> bool:
    return (
        any(pattern.search(clause) for pattern in USAGE_TERM_PATTERNS)
        or MODEL_COST.search(clause) is not None
        or (TOKEN.search(clause) is not None and TOKEN_ACCOUNTING.search(clause) is not None)
        or (
            COST.search(clause) is not None
            and COST_BEARERS.search(clause) is not None
            and COST_MEASURES.search(clause) is not None
        )
        or (
            ENGLISH_COST.search(clause) is not None and ENGLISH_RECORDING.search(clause) is not None
        )
    )


def _is_usage_claim(clause: str) -> bool:
    """Whether one clause presents usage recording without saying no shipped path does it."""
    return _names_usage_recording(clause) and not holds_unnegated_phrase(
        clause, SHIPPED_PATH_CONDITIONS
    )


@dataclass(frozen=True, slots=True)
class FlaggedClause:
    """A clause the guard reads as a usage claim, with the 1-based line it starts on."""

    line: int
    text: str


def _flagged_clauses(document: str) -> list[FlaggedClause]:
    return [
        FlaggedClause(clause.line, clause.text)
        for clause in clauses(document)
        if _is_usage_claim(clause.text)
    ]


@dataclass(frozen=True, slots=True, kw_only=True)
class AllowedClause:
    """A clause the guard flags that is true as written, pinned by its whole text, and why.

    `clause` is the clause exactly as `prose_clauses.clauses` reads it -- soft wraps folded,
    runs of whitespace collapsed -- so re-wrapping the paragraph keeps the pin and changing any
    word breaks it. `excerpt` only finds the clause, so a broken pin can say which one it was.
    """

    path: Path
    excerpt: str
    clause: str
    reason: str


ALLOWLIST: Final[tuple[AllowedClause, ...]] = (
    AllowedClause(
        path=MARKETING,
        excerpt="FinOps、模型成本专题、团队采购评估",
        clause="**建议渠道：** FinOps、模型成本专题、团队采购评估。",
        reason=(
            "Section 037's channel suggestion names model cost as the topic of the channels to "
            "post in; it presents nothing as recorded."
        ),
    ),
    AllowedClause(
        path=MARKETING,
        excerpt="模型错误、Token 和成本也有确定性用例",
        clause="模型错误、Token 和成本也有确定性用例。",
        reason=(
            "Section 084 describes the test suite, and truly: "
            "tests/unit/models/test_model_governance.py::"
            "test_provider_classifies_retry_and_persists_usage_cost builds the provider with a "
            "SQLiteModelUsageStore and asserts the stored tokens, attempts and estimated_cost. "
            "The sentence says those cases exist, not that a shipped path records usage."
        ),
    ),
)
"""The guarded files' true non-claims. An entry is for a clause that is true as written, never
for a claim waiting to be rewritten.

After the D7 rewrite the guard flagged exactly these two clauses in the four files.
"""


def _guarded_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GUARDED_FILES}


def _usage_violations(documents: dict[Path, str]) -> list[str]:
    """One message per flagged clause of `documents` that no `ALLOWLIST` entry pins."""
    violations: list[str] = []
    for path, document in documents.items():
        pinned = {entry.clause for entry in ALLOWLIST if entry.path == path}
        for flagged in _flagged_clauses(document):
            if flagged.text not in pinned:
                violations.append(
                    f"{path.relative_to(ROOT)}:{flagged.line} presents usage recording as "
                    f"shipped: {flagged.text!r}"
                )
    return violations


def test_user_facing_docs_do_not_present_usage_recording_as_shipped() -> None:
    """Every clause the guard flags in the four files is a violation unless `ALLOWLIST` pins it.

    A flagged clause is exempt only when an entry for its file pins exactly its text. Written
    against the 23 usage lines the D7 brief counted in the marketing pack, the extra ones its
    review found, and `README.md`'s and `README.en.md`'s model-governance bullets -- each
    presented Token, attempt or cost recording as a shipped capability.
    """
    violations = _usage_violations(_guarded_documents())
    assert not violations, (
        "\n".join(violations) + "\nSay in the same clause that no shipped path records it -- "
        "one of SHIPPED_PATH_CONDITIONS, as section 057 does -- or reword the claim. Pin an "
        "ALLOWLIST entry only for a clause that is true as written."
    )


def _allowlist_problems(
    documents: dict[Path, str], allowlist: Iterable[AllowedClause] = ALLOWLIST
) -> list[str]:
    """Why each `ALLOWLIST` entry no longer describes exactly one flagged clause of `documents`.

    The excerpt must find exactly one clause of the entry's file, that clause must still read
    exactly as pinned, and the guard must still flag it.
    """
    problems: list[str] = []
    for entry in allowlist:
        label = f"ALLOWLIST entry {entry.excerpt!r} ({entry.path.relative_to(ROOT)})"
        if entry.path not in documents:
            problems.append(f"{label} is for a file the guard does not read")
            continue
        matches = [
            clause for clause in clauses(documents[entry.path]) if entry.excerpt in clause.text
        ]
        if len(matches) != 1:
            problems.append(
                f"{label} matches {len(matches)} clauses, at lines {[c.line for c in matches]}; "
                "it must match exactly one"
            )
            continue
        found = matches[0]
        if found.text != entry.clause:
            problems.append(
                f"{label}: the clause at line {found.line} now reads {found.text!r}, not the "
                f"pinned {entry.clause!r}; re-read it, then rewrite it, re-pin it or remove the "
                "entry"
            )
            continue
        if not _is_usage_claim(found.text):
            problems.append(
                f"{label} exempts the clause at line {found.line}, which the guard no longer "
                "flags; remove the entry"
            )
    return problems


def test_every_usage_allowlist_entry_exempts_exactly_one_flagged_clause() -> None:
    """An `ALLOWLIST` entry is a statement about one clause; this holds it to that clause.

    A pinned clause that was reworded, fixed, deleted or copied fails here; one that was
    reworded fails the prose test as well, because its new text is pinned by no entry.
    """
    problems = _allowlist_problems(_guarded_documents())
    assert not problems, "\n".join(problems)


def test_the_allowlist_check_reports_each_way_an_entry_can_go_stale() -> None:
    """`_allowlist_problems` held to entries whose defect is known, one per way to go stale.

    Every real entry is current, so the test above cannot show that any of these checks
    reports anything; these entries can.
    """
    clause = "模型错误、Token 和成本也有确定性用例。"
    entry = AllowedClause(path=MARKETING, excerpt="模型错误", clause=clause, reason="measured")
    documents = {MARKETING: f"{clause}\n"}
    unflagged = "模型错误也有确定性用例。"
    stale = {
        "a file the guard does not read": (replace(entry, path=ROOT / "CHANGELOG.md"), documents),
        "an excerpt that finds no clause": (replace(entry, excerpt="账单"), documents),
        "an excerpt that finds two clauses": (entry, {MARKETING: f"{clause}\n\n{clause}\n"}),
        "a clause that no longer reads as pinned": (
            entry,
            {MARKETING: "模型错误、Token 和成本也有确定性测试。\n"},
        ),
        "a clause the guard no longer flags": (
            replace(entry, clause=unflagged),
            {MARKETING: f"{unflagged}\n"},
        ),
    }
    assert not _allowlist_problems(documents, [entry]), "a current entry was reported"
    unreported = [
        label for label, (bad, docs) in stale.items() if not _allowlist_problems(docs, [bad])
    ]
    assert not unreported, f"the allowlist check did not report: {unreported}"


ALLOWLIST_INSERTIONS: Final[dict[str, str]] = {
    "zh": "，每次运行的 Token 与估算成本都会自动入账",
    "en": ", and every run's token usage and cost are recorded",
}
"""A usage claim to write into a pinned clause, straight after its excerpt, by the clause's
language. Neither holds a clause end, so the claim lands inside the pinned clause."""


def test_a_claim_written_into_an_allowlisted_clause_ends_its_exemption() -> None:
    """The review of `D7` wrote a claim into each pinned clause and both tests stayed green.

    For each entry, the claim in `ALLOWLIST_INSERTIONS` is written into the real document right
    after the entry's excerpt; the prose test and the allowlist test must then both fail. While
    the exemption was granted by excerpt, both passed.
    """
    documents = _guarded_documents()
    still_exempt: list[str] = []
    for entry in ALLOWLIST:
        insertion = ALLOWLIST_INSERTIONS["en" if entry.clause.isascii() else "zh"]
        edited = documents[entry.path].replace(entry.excerpt, entry.excerpt + insertion, 1)
        assert edited != documents[entry.path], f"{entry.excerpt!r} is not in its document"
        changed = {**documents, entry.path: edited}
        if not _usage_violations(changed) or not _allowlist_problems(changed):
            still_exempt.append(entry.excerpt)
    assert not still_exempt, (
        f"a claim written into these pinned clauses went unreported: {still_exempt}"
    )


# --- The classifier's own tests ---------------------------------------------------------------


def _flagged_texts(document: str) -> list[str]:
    return [flagged.text for flagged in _flagged_clauses(document)]


def test_the_usage_guard_tells_a_claim_from_a_stated_condition() -> None:
    """Minimal clauses in the shapes the guard exists to tell apart, each judged alone.

    The four claims after the negated condition are the review of `D7`'s false negatives. The
    four non-claims after the credential are the lines that review required the cost rule to
    leave alone, and the two after them hold each half of the cost rules to its other half: a
    cost beside a measuring verb with no bearer, and an English cost with no recording word.
    """
    claims = (
        "Token 与估算成本持续入账。",
        "系统记录模型、尝试次数、输入输出 Token。",
        "classified model retry plus persistent token and configured-cost accounting;",
        "批量、恢复、模型成本、筛选、观察池和报告中心又让系统可以长期使用。",
        "模型能力与成本也由治理层管理。",
        "SQLite 任务、决策、Checkpoint、模型用量、观察池和报告都能跨进程保存。",
        "Per-request usage is metered for every run.",
        "并非出厂路径不会自动记账，Token 与估算成本持续入账。",
        "这个项目把每次 Agent 调用的成本都记下来了。",
        "每次调用的 Token 消耗可追踪。",
        "Model costs are recorded for every run.",
        "classified model retry plus persistent configured-cost accounting;",
    )
    non_claims = (
        "模型调用的 Token 与估算成本另有账本，但要接入自带用量追踪的 Provider 才会写入，"
        "出厂路径不会自动生成账单。",
        "a token and configured-cost ledger that no shipped path writes to;",
        "链邻服务地址和 Token 由用户安全配置，不写进镜像。",
        "这样你可以看到策略收益究竟来自判断，还是被高换手成本吞噬。",
        "相比 AI Hedge Fund 并行运行大量风格 Agent，"
        "这种按证据选择的方式更适合控制 A 股批量研究成本。",
        "TradingAgents 和 AI Hedge Fund 的魅力来自模型驱动的多角色推理，"
        "但开发者常被 API 成本、限流和模型漂移卡住。",
        "想低成本学习 AI 量化架构，或者比较规则与模型表现，下载 OpenAlpha CN 非常合适。",
        "价格、基准与成本放在一起比较才有意义。",
        "A-share T+1, board lot, suspension, limit-lock, and transaction-cost constraints;",
        "OpenAlpha CN 让 Agent 输出、风险决定与费用各有记录可查，"
        "归因只认领交易成本与空仓机会成本两项规则条款。",
        "在一些多智能体 Demo 里，同一份材料会传给每个角色，既浪费 Token，也容易产生重复观点。",
        "佣金、过户费和印花税计入交易成本。",
        "研究结果由调用方显式送入委员会、筛选、报告、观察池或组合核算。",
        "对提示词工程、模型路由、成本优化和 Agent 评测都很有价值。",
    )
    missed = [claim for claim in claims if not _is_usage_claim(claim)]
    wrongly = [text for text in non_claims if _is_usage_claim(text)]
    assert not missed and not wrongly, f"read as no claim: {missed}; read as a claim: {wrongly}"


PRE_D7_CLAIMS: Final[dict[str, str]] = {
    "README.md:17": (
        "| 模型治理 | 模型能力注册、408/429/5xx 分类重试、Token/尝试次数/估算成本持久账本 |\n"
    ),
    "README.md:37": (
        "- **模型治理与核算**：按模型能力选择兼容端点，"
        "对 408/429/5xx 分类重试，"
        "并持久记录 Token、尝试次数与按用户单价估算的成本。\n"
    ),
    "README.en.md:45": (
        "- classified model retry plus persistent token and configured-cost accounting;\n"
    ),
    "marketing:304": (
        "**开场钩子：** 终于不用月底猜账单了，"
        "这个项目把每次 Agent 调用的 Token 和成本都记下来了。\n"
    ),
    "marketing:68": (
        "Token 与估算成本持续入账\N{FULLWIDTH SEMICOLON}每个 Agent 节点都有恢复状态。\n"
    ),
    "marketing:306, the per-configuration cost": (
        "你可以比较确定性基线、单 Agent、Bull/Bear 和风险委员会各自增加了多少成本，"
        "再结合后续验证判断是否值得。\n"
    ),
    "marketing:306, cost beside model performance": (
        "最终，模型表现不再只有\N{LEFT DOUBLE QUOTATION MARK}感觉更聪明"
        "\N{RIGHT DOUBLE QUOTATION MARK}，还可以与成本、稳定性和结果增量一起分析。\n"
    ),
    "marketing:322": (
        "模型调用的 Token、尝试次数和估算成本同时入账，事件研究与多日组合报告再评价结果。\n"
    ),
    "marketing:330": "链邻数据通过统一 Provider 生成证据，模型能力与成本也由治理层管理。\n",
    "marketing:412": (
        "链邻 Provider 的数据入口、模型调用的 Token 与重试、批量任务的进度和恢复也有记录。\n"
    ),
    "marketing:544": ("SQLite 任务、决策、Checkpoint、模型用量、观察池和报告都能跨进程保存。\n"),
    "marketing:822": "批量、恢复、模型成本、筛选、观察池和报告中心又让系统可以长期使用。\n",
}
"""Usage claims as they stood at `2d197af`, verbatim: whole lines for the READMEs and the hook,
and for the marketing bodies the clause (or two) the claim sits in. The two section 037 cost
claims were missed by this guard's first version, which the review of `D7` measured."""


def test_the_usage_claims_this_guard_was_written_for_are_flagged() -> None:
    """The guard's retroactive power, held: every pre-D7 claim must still be flagged."""
    missed = [label for label, text in PRE_D7_CLAIMS.items() if not _flagged_clauses(text)]
    assert not missed, f"the guard no longer flags a pre-D7 usage claim: {missed}"


WRAPPED_CLAIMS: Final[dict[str, str]] = {
    "plain paragraph, wrapped inside 估算成本": "模型侧持久记录 Token 与估\n算成本，便于复盘。\n",
    "bullet, wrapped between the token and its accounting word": (
        "- 模型调用的 Token\n  持续入账。\n"
    ),
    "English bullet, wrapped before 'accounting'": (
        "- classified model retry plus persistent token and configured-cost\n  accounting;\n"
    ),
    "blockquote, lazy continuation": "> 模型调用的 Token 与成本\n都会记下来。\n",
}
"""A usage claim split over two lines in the block forms these documents use."""


def test_a_usage_claim_is_read_whole_across_a_soft_wrap() -> None:
    """The guard reads folded blocks: where a line breaks must not decide the verdict.

    The last assertion is the same fold in the other direction: a condition phrase broken over
    two lines still exempts its clause.
    """
    missed = [form for form, text in WRAPPED_CLAIMS.items() if not _flagged_clauses(text)]
    assert not missed, f"a usage claim split over lines was not read whole in: {missed}"
    wrapped_condition = "- Token 与估算成本另有账本，出厂路\n  径不会自动记账。\n"
    assert not _flagged_clauses(wrapped_condition), (
        f"a condition broken over two lines no longer exempts its clause: {wrapped_condition!r}"
    )


def test_the_usage_guards_stated_blind_spots_are_real() -> None:
    """Each limit the module docstring states, measured, so the docstring cannot drift from it.

    A change that starts flagging one of the `unflagged` rows has closed a stated blind spot:
    delete the row here and the sentence in the docstring together. The `flagged` rows are the
    other direction -- text that is no claim, read as one.
    """
    unflagged = {
        "a paraphrase without a marker": "你可以比较各委员会各自多花了多少。",
        "a cost with a bearer but no measuring verb": "每个委员会的成本一目了然。",
        "one condition exempts every marker in its clause": (
            "Token 与估算成本持续入账，出厂路径不会自动生成账单。"
        ),
        "a claim split across a blank line": "模型调用的 Token\n\n也有记录。\n",
        "a claim split across two clauses": "模型调用的 Token 很多。每一次都有记录。",
    }
    flagged = {
        "a credential token beside an accounting word": "Token 持久保存在环境变量里。",
        "a transaction cost not spelled 交易成本": "佣金成本随每个 Agent 的成交一起记录。",
        "a condition worded outside SHIPPED_PATH_CONDITIONS": (
            "Token 与估算成本持续入账，但默认不开启。"
        ),
    }
    closed = {
        label: _flagged_texts(text) for label, text in unflagged.items() if _flagged_texts(text)
    }
    opened = [label for label, text in flagged.items() if not _flagged_texts(text)]
    assert not closed and not opened, (
        f"a stated blind spot is now flagged: {closed}; a stated misreading no longer happens: "
        f"{opened} -- update the docstring with it"
    )
