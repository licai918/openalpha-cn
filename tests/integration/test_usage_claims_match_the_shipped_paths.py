"""User-facing prose may not present model calls, usage records or capability selection as shipped.

**The code fact these guards stand on**, measured at `2d197af`, re-measured at `ea88999`, and held
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

Four couplings. The first three stand on that one fact, the fourth on a narrower one:

1. **The premise.** `test_no_shipped_path_calls_a_model_or_records_usage` reads the syntax tree
   of every `.py` file under `src/` and `scripts/`, counts the places that could make a model
   call or usage recording reachable -- per module, enclosing scope and kind -- and holds the
   counts equal to `USAGE_SITES`, where each place carries the reason it neither calls a model
   nor records usage. The day a shipped path starts doing either in a way the scan reads, that
   test fails and asks for the documents to be rewritten -- and for the prose guards below
   to be retired or inverted -- instead of those guards silently blocking claims that have
   become true. The ways the scan cannot read are listed below; a change made one of those ways
   passes it.

2. **Usage recording in prose.** `README.md`, `README.en.md`, `docs/why-openalpha-cn.zh-CN.md`
   and `docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md` may not present Token, usage
   or cost recording, or billing, as something the product does unless the same clause says no
   shipped path does it. Section 057 of the marketing pack is the model of a true sentence:
   "模型调用的 Token 与估算成本另有账本，但要接入自带用量追踪的 Provider 才会写入，
   出厂路径不会自动生成账单。"
   `d4af27c` corrected that one sentence without a test; this module is the test.

3. **A model call in prose.** The same four files may not present what a model client does
   once called -- classified retry, backoff, schema validation of a model's output, a
   hallucination to diagnose -- as something the product does, unless the same clause says no
   shipped path calls a model. `README.md`'s model bullets are the model of a true sentence:
   they say what happens once a model provider is wired in code, and that no shipped path calls
   one.

4. **Capability selection in prose.** The same four files may not present model capability
   registration, or an endpoint chosen by a model's capabilities, as something code does,
   unless the same clause says no code selects by them. This stands on a narrower fact, and
   saying that no shipped path calls a model does not answer it: nothing registers or selects
   once a provider is wired either. `ModelRegistry` is constructed nowhere under `src/` or
   `scripts/` (the premise test holds that), no module under `src/` reads a model's
   `capabilities` (measured at `d4ef5e4`; no test holds it), and
   `OpenAICompatibleProvider.generate_json` posts to the one `base_url` it was built with.
   `D12` made five such claims conditional on wiring a provider in code, which left them false
   (the review of `D12`, Important 1), and section 040 made one with no condition at all.

**How the guards in (2), (3) and (4) read a document.**

- *Blocks and clauses* come from `tests/prose_clauses.py`, the reader
  `test_attribution_claims_match_known_limitations.py` shares; its docstring states how a block
  is folded and where a clause ends. A heading, a hook and a table row are each read.
- *Usage markers.* A clause names usage recording when any one of these holds:
  - it holds one of `USAGE_TERMS` -- 经济账 among them, since section 037 closed on 算经济账,
    which does not contain 算账;
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
    (record, track, account, meter, ledger, persist, as word starts: "recorded" counts);
  - it holds "usage" as an English word beside one of `ENGLISH_RECORDING`. "Usage" alone is
    ordinary README English: a `## Usage` heading, a `usage: openalpha [-h]` line.
- *Model-call markers.* A clause names a model call when it names a model (`MODEL_WORDS`: 模型,
  or "LLM" or "model" as an English word) and one of `MODEL_CALL_BEHAVIOURS`: 401, 408, 429 or
  5xx, 重试, 退避, 幻觉, or "retry", "backoff", "schema".
- *Capability markers.* A clause names capability selection when it holds `CAPABILITY_CLAIM`
  -- 能力 and 注册 within eight characters, so 模型能力由治理层注册 counts; 注册 then 能力;
  注册表; 按 ... 能力 ... 选; 能力选择; "registry"; or "capability" before a word of selecting or
  registering -- and names a model or a capability (`CAPABILITY_CONTEXT`), so the
  `pnpm audit --registry` line in `README.md` names none.
- *Claims.* A clause that names usage recording is a claim unless it holds `USAGE_CONDITION`, a
  clause that names a model call unless it holds `MODEL_CALL_CONDITION`, and a clause that
  names capability selection unless it holds `CAPABILITY_CONDITION`; each condition exempts its
  own class only. Until `D13` `MODEL_CALL_CONDITION` exempted a usage marker as well, and
  sections 005 and 061 of the marketing pack, which had lost the `usage_store` condition,
  passed on 出厂路径不调用模型 alone. A condition counts only where no negation directly
  precedes it (`prose_clauses.holds_unnegated_match`), and only as a whole phrase -- 出厂路径,
  不, then the verb for what the path does not do -- so a clause that only begins
  出厂路径不需要 states no condition, which the first version of the usage guard accepted.
  Where that verb takes an object, the condition counts only with the object it is about, so
  出厂路径不写入密钥 and "no shipped path sends a model key anywhere" state none; both guards
  accepted them until the review of `D12` measured them (its M-4). A condition exempts only
  the clause it sits in.
- *Allowlists.* `ALLOWLIST`, `MODEL_CALL_ALLOWLIST` and `CAPABILITY_ALLOWLIST` hold the true
  non-claims each guard's markers catch -- a statement about the test suite, a topic named as a
  channel suggestion, a list of questions, a general statement about multi-agent systems --
  each pinned to one clause by that clause's whole text as the shared reader produces it (soft
  wraps folded, runs of whitespace collapsed), with a short excerpt that only finds the clause,
  and the reason the clause is true. A clause is exempt only while its text equals the pinned
  text, so any change to its words -- a claim appended, a word swapped -- ends the exemption and
  the guard's prose test fails on it until someone re-reads the clause and re-pins it or
  rewrites it, which `test_a_claim_written_into_an_allowlisted_clause_ends_its_exemption`
  measures for every entry. `test_every_allowlist_entry_exempts_exactly_one_flagged_clause`
  fails on an entry whose excerpt finds no clause or several, whose clause no longer reads as
  pinned, or whose clause the guard no longer flags. With each prose test, that makes each
  allowlist a census: every clause a guard flags in the four files is either rewritten or
  listed there. A guard's `diagram_allowlist` pins a unit an embedded diagram draws the same
  way, and `test_every_diagram_allowlist_entry_exempts_exactly_one_flagged_unit` holds it.

**What the usage guard cannot see.** `test_the_usage_guards_stated_blind_spots_are_real`
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
  记录。" is flagged), and a condition worded outside `USAGE_CONDITION` is read as a claim --
  since `D13` that includes 出厂路径不调用模型 on its own.

**What the model-call guard cannot see.**
`test_the_model_call_guards_stated_blind_spots_are_real` measures each of these.

- A claim whose model word sits in another clause. Section 097 named the models in one clause
  ("让所有模型共享同一 A 股 EvidenceSnapshot、SignalFrame、风险和回放合同") and put its registry
  and retry claim in the next ("能力注册表描述结构化输出支持，408、429、5xx 分类重试，401 立即
  失败"), which names no model; it was rewritten by hand.
- A model presupposed without a behaviour word passes: "用户无法判断是模型限流还是任务卡死。", and
  so do governance named without one ("模型治理也能通过公开接口管理。") and structured output named
  without the word schema ("模型输出统一为结构化结果。").
- A model named only by its provider class, its vendor or a product is no model word:
  "OpenAI-compatible Provider 对 408、429、5xx 分类重试。",
  "DeepSeek、Qwen 端点返回 429 时自动退避。", "The OpenAI-compatible provider retries 429s
  with backoff." and "GPT 请求失败会指数退避。" all pass. `README.md` calls the client
  "OpenAI-compatible Provider"; the review of `D12` found no clause of these shapes in the four
  files (its M-3).
- A claim split across two blocks is read as two halves, each innocent.
- The other direction: a retry of something else beside an unrelated model word is read as a
  model call ("批量队列支持失败重试，RunManifest 记录模型版本。" is flagged), and a condition
  worded outside `MODEL_CALL_CONDITION` is read as a claim.

**What the capability guard cannot see.**
`test_the_capability_guards_stated_blind_spots_are_real` measures each of these.

- A choice made by capability, worded without a marker: "模型元数据决定调用哪个端点。" passes.
- A claim split across two clauses is read as two halves, each innocent:
  "模型侧维护一份能力清单。注册后按它选端点。" passes.
- The other direction: a registry of something else beside a model word is read as a claim
  ("模型版本登记在 RunManifest 的注册表里。" is flagged), and a condition worded outside
  `CAPABILITY_CONDITION` is read as a claim.

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
- *Calls.* A call whose callee is one of the seven -- bare, through an import binding, or as an
  attribute -- counts again as a kind of its own (`CALLED_KINDS`), so a construction is a site
  its bare name is not. An `__all__` entry traded for a construction in the same scope used to
  leave the count unchanged (the review of `D12`, M-1).

**What the premise test cannot see.** It reads names and literals, never values or behaviour.

- A name built at run time -- `importlib` with a computed module or attribute name,
  `getattr(obj, "generate" + "_json")` -- or a table name assembled from pieces.
- A value traced through anything but an import of one of the seven. An import is resolved
  only when the imported name's own last part is one of them, so `from openalpha_cn.agents
  import DEFAULT_MODEL_AGENT` binds nothing the scan tracks, whatever that name holds.
  `record_type = governance.ModelUsageRecord` is counted where the attribute is named, and
  `record_type(**fields)` is not counted at all, so a call through a local binding adds nothing
  to its scope's count.
- A use traded for another in the same scope when neither is a call: an `__all__` entry removed
  and `PROVIDERS = {"openai-compatible": OpenAICompatibleProvider}` added keeps the scope's
  count of the name, and a caller that builds through `PROVIDERS[...](...)` names nothing
  tracked. `test_the_premise_scans_stated_blind_spot_is_real` measures it.
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
`scripts/`, and requires the premise to fail every time;
`test_the_premise_goes_red_on_the_double_swap_the_review_of_d12_measured` does the same for
the review of `D12`'s double swap across three real modules.
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import pytest
from diagram_text import diagram_strings, diagram_units
from prose_clauses import Clause, clauses, holds_unnegated_match, marker_pattern

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

CALLED_KINDS: Final[dict[str, str]] = {
    PROVIDER_NAMED: "constructs the provider",
    RECORD_NAMED: "constructs a usage record",
    AGENT_NAMED: "constructs the model-backed agent",
    REGISTRY_NAMED: "constructs the model registry",
    MODEL_CALL_NAMED: "calls the model",
    PROVIDER_STORE_NAMED: "calls the provider's usage store",
    RUNTIME_STORE_NAMED: "calls the runtime's usage store",
}
"""The kind a call counts as when its callee resolves to one of `SHIPPED_PATH_NAMES`, by the kind
of the name it calls. A call is counted on top of the name, so a construction is a site the name
alone is not: the review of `D12` traded an `__all__` entry for a construction in the same scope,
the scope's count of the name stayed the same, and the premise stayed green (its M-1)."""

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

    def visit_Call(self, node: ast.Call) -> None:
        """A call of one of the names counts as `CALLED_KINDS`' kind too, on top of the name."""
        callee = node.func
        kind: str | None = None
        if isinstance(callee, ast.Name):
            kind = self._name_kind(callee.id)
        elif isinstance(callee, ast.Attribute):
            kind = SHIPPED_PATH_NAMES.get(callee.attr)
        if kind is not None:
            self._found(CALLED_KINDS[kind])
        self.generic_visit(node)

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
    _site(
        "agents/model.py", "StructuredSignalAgent.analyze", CALLED_KINDS[MODEL_CALL_NAMED]
    ): Counted(1, "The same `self.provider.generate_json(...)`, counted again as a call."),
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
    _site(
        "models/openai_compatible.py",
        "OpenAICompatibleProvider._record_usage",
        CALLED_KINDS[RECORD_NAMED],
    ): Counted(1, "The same construction of the one writer's record, counted again as a call."),
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
    """The premise of both prose guards below, read off the code rather than remembered.

    Equal counts, not a subset: a site that disappears fails too, so a scan that stopped finding
    anything cannot pass as a clean one, and a second occurrence in a counted scope fails as a
    new scope does.
    """
    problems = _premise_problems(_usage_sites(_non_test_sources()))
    assert not problems, (
        "\n".join(problems) + "\nIf a shipped path now calls a model, builds "
        "OpenAICompatibleProvider or StructuredSignalAgent, hands a provider a usage store or "
        "writes model_usage rows, the prose guards' premise is false: rewrite the model and "
        "usage sentences in the four guarded documents to say what the shipped path does now, "
        "and retire or invert test_user_facing_docs_do_not_present_a_model_call_as_shipped, "
        "test_user_facing_docs_do_not_present_usage_recording_as_shipped and, if a registry is "
        "built, test_user_facing_docs_do_not_present_capability_selection_as_shipped. If the "
        "place calls and records nothing, record it in USAGE_SITES with its count and the reason."
    )


def _counted(*sites: tuple[str, str, int]) -> Counter[UsageSite]:
    return Counter({UsageSite("m.py", scope, kind): count for scope, kind, count in sites})


SCAN_CASES: Final[dict[str, tuple[str, Counter[UsageSite]]]] = {
    "a direct construction": (
        "from openalpha_cn.models import OpenAICompatibleProvider\n\n"
        "def build():\n"
        "    return OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "        base_url='https://example.test', api_key_env=None)\n",
        _counted(
            ("<module>", PROVIDER_NAMED, 1),
            ("build", PROVIDER_NAMED, 1),
            ("build", CALLED_KINDS[PROVIDER_NAMED], 1),
        ),
    ),
    "an aliased import, and a call through the alias": (
        "from openalpha_cn.models import OpenAICompatibleProvider as Provider\n\n"
        "def build():\n"
        "    return Provider(provider_id='p', model='m', base_url='https://example.test',\n"
        "        api_key_env=None)\n",
        _counted(
            ("<module>", PROVIDER_NAMED, 1),
            ("build", PROVIDER_NAMED, 1),
            ("build", CALLED_KINDS[PROVIDER_NAMED], 1),
        ),
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
        _counted(("log", RECORD_NAMED, 1), ("log", CALLED_KINDS[RECORD_NAMED], 1)),
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
        _counted(("log", RECORD_NAMED, 1), ("log", CALLED_KINDS[RECORD_NAMED], 1)),
    ),
    "a record built under an alias": (
        "from openalpha_cn.models.governance import ModelUsageRecord as UsageRow\n\n"
        "def log(store, **fields):\n    store.append(UsageRow(**fields))\n",
        _counted(
            ("<module>", RECORD_NAMED, 1),
            ("log", RECORD_NAMED, 1),
            ("log", CALLED_KINDS[RECORD_NAMED], 1),
        ),
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
        _counted(("ask", MODEL_CALL_NAMED, 2), ("ask", CALLED_KINDS[MODEL_CALL_NAMED], 1)),
    ),
    "the model-backed agent built under an alias": (
        "from openalpha_cn.agents import StructuredSignalAgent as ModelAgent\n\n"
        "def build(provider):\n"
        "    return ModelAgent(agent_id='a', evidence_families=frozenset(), provider=provider)\n",
        _counted(
            ("<module>", AGENT_NAMED, 1),
            ("build", AGENT_NAMED, 1),
            ("build", CALLED_KINDS[AGENT_NAMED], 1),
        ),
    ),
    "the model registry built": (
        "from openalpha_cn.models.governance import ModelRegistry\n\n"
        "def registry():\n    return ModelRegistry(())\n",
        _counted(
            ("<module>", REGISTRY_NAMED, 1),
            ("registry", REGISTRY_NAMED, 1),
            ("registry", CALLED_KINDS[REGISTRY_NAMED], 1),
        ),
    ),
    "a construction in place of a re-export (the review of D12's double swap)": (
        "from openalpha_cn.models.openai_compatible import OpenAICompatibleProvider\n\n"
        "__all__ = ['ModelProvider']\n"
        "DEFAULT_PROVIDER = OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "    base_url='https://example.test', api_key_env=None)\n",
        _counted(("<module>", PROVIDER_NAMED, 2), ("<module>", CALLED_KINDS[PROVIDER_NAMED], 1)),
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


DOUBLE_SWAP: Final[dict[str, tuple[str, str]]] = {
    "src/openalpha_cn/models/__init__.py": (
        '    "OpenAICompatibleProvider",\n',
        "\n\nDEFAULT_PROVIDER = OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "    base_url='https://example.test', api_key_env=None)\n",
    ),
    "src/openalpha_cn/agents/__init__.py": (
        '    "StructuredSignalAgent",\n',
        "\n\nDEFAULT_MODEL_AGENT = StructuredSignalAgent(agent_id='a',\n"
        "    evidence_families=frozenset(), provider=None)\n",
    ),
}
"""The review of `D12`'s M-1, per module: the `__all__` entry removed, and a module-scope
construction appended in its place. With `sdk.py` importing `DEFAULT_MODEL_AGENT`, an SDK built on
that agent would call a model."""


def test_the_premise_goes_red_on_the_double_swap_the_review_of_d12_measured() -> None:
    """Two re-exports traded for two constructions, and the SDK importing one of them.

    Each scope keeps its count of every name -- a construction takes the place of an `__all__`
    entry -- so the scan as `D12` wrote it stayed green, which the review of `D12` measured on
    disk. A call whose callee resolves to one of `SHIPPED_PATH_NAMES` is now a kind of its own
    (`CALLED_KINDS`), so the two constructions are two new sites. Applied in memory, as the
    injections above are.

    It fails on its own. It compares the problems the swap adds to today's and requires exactly
    the two constructions: a scan that stopped counting calls leaves today's tree with problems of
    its own, which the premise test fails on, and adds none here, which this test fails on (the
    review of `D13`, M7).
    """
    sources = dict(_non_test_sources())
    swapped = dict(sources)
    for path, (entry, construction) in DOUBLE_SWAP.items():
        assert sources[path].count(entry) == 1, f"{entry!r} is not in {path} exactly once"
        swapped[path] = sources[path].replace(entry, "", 1) + construction
    sdk = "src/openalpha_cn/sdk.py"
    swapped[sdk] = sources[sdk] + "\n\nfrom openalpha_cn.agents import DEFAULT_MODEL_AGENT\n"
    baseline = set(_premise_problems(_usage_sites(sources.items())))
    added = set(_premise_problems(_usage_sites(swapped.items()))) - baseline
    constructions = (("models/__init__.py", PROVIDER_NAMED), ("agents/__init__.py", AGENT_NAMED))
    expected = {
        f"src/openalpha_cn/{module} :: <module> :: {CALLED_KINDS[kind]}: counted 1, "
        "USAGE_SITES records 0"
        for module, kind in constructions
    }
    assert added == expected, f"the double swap added {sorted(added)}, not the two constructions"


def test_the_premise_scans_stated_blind_spot_is_real() -> None:
    """The trade the module docstring says the scan cannot see, measured: a re-export swapped for
    a table entry in the same scope, and a caller that reaches the provider through the table.

    `models/__init__.py` keeps its count of the provider's name -- a table value takes the place
    of the `__all__` entry -- and `sdk.py` names only `PROVIDERS`, which is none of the seven. A
    change that turns this red has closed the blind spot: delete this test and the sentence in
    the docstring together.
    """
    sources = dict(_non_test_sources())
    models, sdk = "src/openalpha_cn/models/__init__.py", "src/openalpha_cn/sdk.py"
    entry = '    "OpenAICompatibleProvider",\n'
    assert sources[models].count(entry) == 1, f"{entry!r} is not in {models} exactly once"
    swapped = {
        **sources,
        models: sources[models].replace(entry, "", 1)
        + "\n\nPROVIDERS = {'openai-compatible': OpenAICompatibleProvider}\n",
        sdk: sources[sdk] + "\n\nfrom openalpha_cn.models import PROVIDERS\n\n"
        "_PROBE = PROVIDERS['openai-compatible'](provider_id='p', model='m',\n"
        "    base_url='https://example.test', api_key_env=None)\n",
    }
    assert not _premise_problems(_usage_sites(swapped.items())), (
        "the table swap is now seen: update the module docstring and delete this test"
    )


# --- Part 2: user-facing prose may not present usage recording or a model call as shipped -----

USAGE_TERMS: Final[tuple[str, ...]] = (
    "用量",
    "估算成本",
    "估算的成本",
    "尝试次数",
    "入账",
    "账单",
    "计费",
    "算账",
    "经济账",
    "billing",
)
"""Words that name usage recording on their own, matched by `prose_clauses.marker_pattern`.

English "usage" is not among them: it names usage recording only beside a recording word (see
`_names_usage_recording`), so a `## Usage` heading or a `usage: openalpha [-h]` line is none."""

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

ENGLISH_USAGE: Final[re.Pattern[str]] = marker_pattern("usage")

ENGLISH_RECORDING: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])(?:record|track|account|meter|ledger|persist)", re.IGNORECASE
)
"""English words that present a cost or a usage as kept, matched as word starts."""

_CONDITION_END: Final[str] = (
    r"(?=\s*(?:[，。、,.;:：)）|!?\N{FULLWIDTH SEMICOLON}\N{FULLWIDTH EXCLAMATION MARK}"
    r"\N{FULLWIDTH QUESTION MARK}]|\N{EM DASH}|--|$))"
)
"""Where a condition whose verb takes an object must end: at punctuation, a table cell's edge, a
dash or the end of the clause, so that the verb's object is the one the condition is about."""

USAGE_CONDITION: Final[re.Pattern[str]] = re.compile(
    "出厂路径(?:都)?不(?:会)?(?:替你)?(?:自动)?"
    f"(?:记账|生成账单|入账|(?:写入|记录)(?:这张表|账本|用量|任何用量|模型用量)?{_CONDITION_END})"
    f"|no shipped path (?:writes|records)(?: to)?{_CONDITION_END}"
    "|no shipped path (?:writes|records) (?:to )?(?:the (?:ledger|table)|(?:any |model )?usage)"
    "(?![A-Za-z])",
    re.IGNORECASE,
)
"""A statement that no shipped path records usage: 出厂路径, 不, then 记账, 生成账单 or 入账,
or 写入 or 记录 whose only object is the ledger's own (这张表, 账本, 用量) before the phrase ends,
with 都, 会, 替你 or 自动 allowed between; or "no shipped path writes" or "records", with "to"
allowed, ending the phrase or followed by the ledger, the table or usage.

A whole phrase, not a prefix, so 出厂路径不需要额外配置 states nothing. And a verb with another
object states nothing either: 出厂路径不写入密钥 and "No shipped path records your API key" say
something about secrets, and both guards accepted such a phrase until the review of `D12`
measured it (its M-4). The wordings these documents use, not a general detector."""

MODEL_CALL_CONDITION: Final[re.Pattern[str]] = re.compile(
    "出厂路径(?:都)?不(?:会)?(?:调用|发起)(?:任何)?(?:大)?模型"
    r"(?![一-鿿]|\s*(?:api\s*)?(?:keys?|credentials?|secrets?)(?![A-Za-z]))"
    "|no shipped path (?:calls|makes|sends) (?:a |any )?(?:models?|llms?)(?: calls?)?"
    f"(?:{_CONDITION_END}|(?= at all))",
    re.IGNORECASE,
)
"""A statement that no shipped path calls a model: 出厂路径, 不, 调用 or 发起, then 模型 as the
object and not as the first half of another noun (出厂路径不调用模型密钥 states nothing); or "no
shipped path calls a model" and its near variants, ending the phrase ("... sends a model key
anywhere" states nothing, the review of `D12`'s M-4).

It exempts a model-call marker only. Until `D13` it exempted a usage marker too, and a path that
calls no model does record nothing -- but the clause then says nothing about the `usage_store` a
wired provider needs before it records, and sections 005 and 061 of the marketing pack, having
lost that condition, passed on this one (the review of `D12`, Important 1)."""


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
        or (
            ENGLISH_USAGE.search(clause) is not None
            and ENGLISH_RECORDING.search(clause) is not None
        )
    )


def _is_usage_claim(clause: str) -> bool:
    """Whether one clause presents usage recording without saying no shipped path records it.

    Only `USAGE_CONDITION` exempts it; `MODEL_CALL_CONDITION` did until `D13` (see there).
    """
    return _names_usage_recording(clause) and not holds_unnegated_match(clause, USAGE_CONDITION)


MODEL_WORDS: Final[re.Pattern[str]] = re.compile(
    r"模型|(?<![A-Za-z])(?:llm|model)s?(?![A-Za-z])", re.IGNORECASE
)
"""模型 (大模型 included) as a substring; "LLM" and "model" as English words, plural included,
so "AlphaModel" and "LLMOps" name no model."""

MODEL_CALL_BEHAVIOURS: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9])(?:401|408|429|5xx)(?![0-9])|重试|退避|幻觉"
    r"|(?<![A-Za-z])(?:retr(?:y|ies|ied)|backoff|schema)",
    re.IGNORECASE,
)
"""What a model client does once it is called: the HTTP statuses it classifies, retry and
backoff, schema validation, and a hallucination to diagnose.

The capability registry was among these until `D13`. It is not something a called client does
-- no code registers a capability or selects by one, wired or not -- so it has a guard of its
own, `CAPABILITY_CLAIM`, which no statement about model calls exempts."""


def _names_model_call(clause: str) -> bool:
    return (
        MODEL_WORDS.search(clause) is not None and MODEL_CALL_BEHAVIOURS.search(clause) is not None
    )


def _is_model_call_claim(clause: str) -> bool:
    """Whether one clause presents model-call behaviour without saying no shipped path calls."""
    return _names_model_call(clause) and not holds_unnegated_match(clause, MODEL_CALL_CONDITION)


_NO_BREAK: Final[str] = r"[^，。、,;：:\N{FULLWIDTH SEMICOLON}]"
"""One character that is no comma, colon or semicolon: what a capability phrase may span."""

CAPABILITY_CLAIM: Final[re.Pattern[str]] = re.compile(
    rf"能力{_NO_BREAK}{{0,8}}注册|注册{_NO_BREAK}{{0,4}}能力|注册表"
    rf"|按{_NO_BREAK}{{0,6}}能力{_NO_BREAK}{{0,6}}选|能力{_NO_BREAK}{{0,2}}选择"
    r"|(?<![A-Za-z])registr(?:y|ies)(?![A-Za-z])"
    r"|(?<![A-Za-z])capabilit(?:y|ies)(?![A-Za-z])[^.;]{0,40}?"
    r"(?<![A-Za-z])(?:select|choos|resolv|rout|regist)",
    re.IGNORECASE,
)
"""Capability registration, or a choice made by capability.

The markers: 能力 and 注册 within eight characters (能力注册, 模型能力由治理层注册), 注册 then
能力, 注册表, 按 ... 能力 ... 选, 能力选择, "registry", and "capability" followed by a word of
selecting, choosing, resolving, routing or registering."""

CAPABILITY_CONTEXT: Final[re.Pattern[str]] = re.compile(
    r"模型|能力|(?<![A-Za-z])(?:llms?|models?|capabilit(?:y|ies))(?![A-Za-z])", re.IGNORECASE
)
"""What makes a registry a model's: a model or a capability named in the same clause, so
`pnpm audit --registry https://registry.npmjs.org` names none."""

CAPABILITY_CONDITION: Final[re.Pattern[str]] = re.compile(
    r"没有(?:任何)?代码(?:会)?据此(?:选择|挑选|路由)"
    r"|no code (?:selects|chooses|routes)[^.;]{0,30}? by (?:it|them)(?![A-Za-z])",
    re.IGNORECASE,
)
"""A statement that nothing chooses by a registered capability: 没有代码据此选择 (任何 and 会
allowed, 挑选 or 路由 for 选择), or "no code selects ... by it". The wordings these documents use,
not a general detector. Neither `MODEL_CALL_CONDITION` nor `USAGE_CONDITION` stands in for it:
wiring a provider in code registers nothing and selects nothing."""


def _names_capability_selection(clause: str) -> bool:
    return (
        CAPABILITY_CLAIM.search(clause) is not None
        and CAPABILITY_CONTEXT.search(clause) is not None
    )


def _is_capability_claim(clause: str) -> bool:
    """Whether one clause presents capability registration or selection as something code does."""
    return _names_capability_selection(clause) and not holds_unnegated_match(
        clause, CAPABILITY_CONDITION
    )


@dataclass(frozen=True, slots=True)
class FlaggedClause:
    """A clause a guard reads as a claim, with the 1-based line it starts on."""

    line: int
    text: str


def _flagged_clauses(
    document: str, is_claim: Callable[[str], bool] = _is_usage_claim
) -> list[FlaggedClause]:
    return [
        FlaggedClause(clause.line, clause.text)
        for clause in clauses(document)
        if is_claim(clause.text)
    ]


@dataclass(frozen=True, slots=True, kw_only=True)
class AllowedClause:
    """A clause a guard flags that is true as written, pinned by its whole text, and why.

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
"""The usage guard's true non-claims. An entry is for a clause that is true as written, never
for a claim waiting to be rewritten.

After the D7 rewrite the usage guard flagged exactly these two clauses in the four files.
"""

MODEL_CALL_ALLOWLIST: Final[tuple[AllowedClause, ...]] = (
    AllowedClause(
        path=MARKETING,
        excerpt="哪个模型、哪条提示词、哪一步重试",
        clause=(
            "**推广正文：** TradingAgents 和 AI Hedge Fund 展示了多智能体投研的想象力，"
            "但多数用户真正踩坑的地方，不是角色不够多，而是结果出来后无法回答：用了哪批数据、"
            "哪个版本、哪个模型、哪条提示词、哪一步重试、为什么最终通过风险门。"
        ),
        reason=(
            "Section 007 lists the questions users cannot answer about a result. The model and "
            "the retry are two separate items of that list, and neither is presented as a model "
            "call this product makes; the section's next sentence names RunManifest, which "
            "does record model and prompt versions (model_versions, prompt_versions)."
        ),
    ),
    AllowedClause(
        path=MARKETING,
        excerpt="数据为空、模型超时、Schema 解析失败",
        clause=(
            "**推广正文：** 多智能体系统链路越长，排错越难：数据为空、模型超时、Schema 解析失败、"
            "风险门阻断、成交规则拒单，都可能表现成"
            "\N{LEFT DOUBLE QUOTATION MARK}没有结果\N{RIGHT DOUBLE QUOTATION MARK}。"
        ),
        reason=(
            "Section 029 opens on a general statement about multi-agent systems: a model "
            "timeout is one of five failures that can look like no result. The section's claim "
            "about this product's model layer is a clause of its own, which states that no "
            "shipped path calls a model."
        ),
    ),
)
"""The model-call guard's true non-claims, pinned the same way as `ALLOWLIST`.

At `ea88999` the guard as `D12` committed it flags 27 clauses in the four files: these two, and
25 claims, all of which `D12` rewrote (re-measured in `D13`). The 29 this docstring used to give
came from a draft that also read 结构化输出: it flagged two clauses more, lines 316 (a channel
suggestion) and 470 (an attribution caveat) of the marketing pack, neither of them a claim, and
the marker was dropped rather than the two pinned.
"""

CAPABILITY_ALLOWLIST: Final[tuple[AllowedClause, ...]] = ()
"""The capability guard's true non-claims: none. Every clause it flagged at `d4ef5e4`
(`PRE_D13_CAPABILITY_CLAIMS`) was a claim, and each was rewritten."""

USAGE_DIAGRAM_ALLOWLIST: Final[tuple[AllowedClause, ...]] = (
    AllowedClause(
        path=ROOT / "scripts" / "generate_brain_diagrams.py",
        excerpt="Token / 尝试次数账本",
        clause=(
            "模型治理边界，OPTIONAL MODEL ENHANCEMENT，需代码接入 \N{MIDDLE DOT} Schema 校验 "
            "\N{MIDDLE DOT} 408/429/5xx 重试，Token / 尝试次数账本 \N{MIDDLE DOT} "
            "出厂路径不调用模型"
        ),
        reason=(
            "brain-03's 模型治理边界 box as D12 rewrote it names the ledger beside "
            "出厂路径不调用模型 and promises nothing about when a row is written, so it is true as "
            "drawn. Since D13 MODEL_CALL_CONDITION no longer exempts a usage marker, so the usage "
            "guard reads it as a claim. Rewording the box into a USAGE_CONDITION form, such as "
            "出厂路径不记账, retires this entry."
        ),
    ),
)
"""The usage guard's true non-claims among the units the embedded diagrams draw, pinned the same
way, by the generator's path and the unit's whole text."""


@dataclass(frozen=True, slots=True)
class ProseGuard:
    """One prose guard over `GUARDED_FILES` and the embedded diagrams: what it reads as a claim,
    and what it pins in each."""

    claim: str
    is_claim: Callable[[str], bool]
    allowlist: tuple[AllowedClause, ...]
    remedy: str
    diagram_allowlist: tuple[AllowedClause, ...] = ()


USAGE_GUARD: Final[ProseGuard] = ProseGuard(
    claim="usage recording",
    is_claim=_is_usage_claim,
    allowlist=ALLOWLIST,
    remedy=(
        "Say in the same clause that no shipped path records it -- USAGE_CONDITION, as section "
        "057 does -- or reword the claim. Saying no shipped path calls a model is not enough."
    ),
    diagram_allowlist=USAGE_DIAGRAM_ALLOWLIST,
)

MODEL_CALL_GUARD: Final[ProseGuard] = ProseGuard(
    claim="a model call",
    is_claim=_is_model_call_claim,
    allowlist=MODEL_CALL_ALLOWLIST,
    remedy=(
        "Say in the same clause that no shipped path calls a model -- MODEL_CALL_CONDITION, as "
        "README.md's model bullets do -- or reword the claim."
    ),
)

CAPABILITY_GUARD: Final[ProseGuard] = ProseGuard(
    claim="capability registration or capability-based selection",
    is_claim=_is_capability_claim,
    allowlist=CAPABILITY_ALLOWLIST,
    remedy=(
        "Say in the same clause that no code selects by it -- CAPABILITY_CONDITION, as "
        "README.md's model row does -- or drop the claim: ModelRegistry is constructed nowhere "
        "and nothing reads a model's capabilities, whether or not a provider is wired."
    ),
)

GUARDS: Final[dict[str, ProseGuard]] = {
    "usage": USAGE_GUARD,
    "model-call": MODEL_CALL_GUARD,
    "capability": CAPABILITY_GUARD,
}


def _guarded_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GUARDED_FILES}


def _violations(documents: dict[Path, str], guard: ProseGuard) -> list[str]:
    """One message per clause of `documents` that `guard` flags and no entry of it pins."""
    violations: list[str] = []
    for path, document in documents.items():
        pinned = {entry.clause for entry in guard.allowlist if entry.path == path}
        for flagged in _flagged_clauses(document, guard.is_claim):
            if flagged.text not in pinned:
                violations.append(
                    f"{path.relative_to(ROOT)}:{flagged.line} presents {guard.claim} as "
                    f"shipped: {flagged.text!r}"
                )
    return violations


def test_user_facing_docs_do_not_present_usage_recording_as_shipped() -> None:
    """Every clause the usage guard flags in the four files is a violation unless `ALLOWLIST`
    pins it.

    A flagged clause is exempt only when an entry for its file pins exactly its text. Written
    against the 23 usage lines the D7 brief counted in the marketing pack, the extra ones its
    review found, and `README.md`'s and `README.en.md`'s model-governance bullets -- each
    presented Token, attempt or cost recording as a shipped capability.
    """
    violations = _violations(_guarded_documents(), USAGE_GUARD)
    assert not violations, (
        "\n".join(violations) + f"\n{USAGE_GUARD.remedy} Pin an ALLOWLIST entry only for a "
        "clause that is true as written."
    )


def test_user_facing_docs_do_not_present_a_model_call_as_shipped() -> None:
    """Every clause the model-call guard flags is a violation unless `MODEL_CALL_ALLOWLIST` pins
    it.

    Written against the 25 claims it flags at `ea88999` beside its two pins, re-measured in
    `D13` (this docstring said 27 and 20): `README.md`'s model row, two model bullets and fourth
    advantage, `README.en.md`'s two model bullets, `why-openalpha`'s 研究运行 row, and 18 clauses
    of the marketing pack, section 097's hook among them. Each presented classified retry,
    capability registration, schema validation or a model to diagnose as what the product does.
    No shipped path calls a model -- the premise test above holds that -- so each is true only
    of a provider a user wires in code. Capability registration left this guard in `D13` for a
    guard of its own.
    """
    violations = _violations(_guarded_documents(), MODEL_CALL_GUARD)
    assert not violations, (
        "\n".join(violations) + f"\n{MODEL_CALL_GUARD.remedy} Pin a MODEL_CALL_ALLOWLIST entry "
        "only for a clause that is true as written."
    )


def test_user_facing_docs_do_not_present_capability_selection_as_shipped() -> None:
    """Every clause the capability guard flags is a violation unless `CAPABILITY_ALLOWLIST` pins
    it.

    Written against the six clauses of `PRE_D13_CAPABILITY_CLAIMS`: `README.md`'s model row and
    model bullet, and sections 005, 034, 040 and 061 of the marketing pack. `ModelRegistry` is
    constructed nowhere under `src/` or `scripts/` -- the premise test above holds that -- and
    `OpenAICompatibleProvider` posts to the one `base_url` it was built with, so no code
    registers a capability or selects an endpoint by one, whether or not a provider is wired.
    """
    violations = _violations(_guarded_documents(), CAPABILITY_GUARD)
    assert not violations, (
        "\n".join(violations) + f"\n{CAPABILITY_GUARD.remedy} Pin a CAPABILITY_ALLOWLIST entry "
        "only for a clause that is true as written."
    )


@pytest.mark.parametrize("name", GUARDS)
def test_each_prose_test_fails_on_a_claim_it_exists_to_catch(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prose test that stopped reading its guard's verdicts would still pass on today's
    documents, which hold no claim. This hands each prose test the real documents plus one claim
    of its kind -- `README.md`'s model row or bullet as it stood before `D7`, `D12` or `D13` --
    appended to `README.md`, and requires the prose test to fail."""
    claim, prose_test = {
        "usage": (
            PRE_D7_CLAIMS["README.md:17"],
            test_user_facing_docs_do_not_present_usage_recording_as_shipped,
        ),
        "model-call": (
            PRE_D12_MODEL_CALL_CLAIMS["README.md:17"],
            test_user_facing_docs_do_not_present_a_model_call_as_shipped,
        ),
        "capability": (
            PRE_D13_CAPABILITY_CLAIMS["README.md:37"],
            test_user_facing_docs_do_not_present_capability_selection_as_shipped,
        ),
    }[name]
    documents = _guarded_documents()
    documents[README] += f"\n{claim}"
    monkeypatch.setattr(sys.modules[__name__], "_guarded_documents", lambda: documents)
    with pytest.raises(AssertionError):
        prose_test()


def _read_prose(text: str, *, filename: str) -> list[Clause]:
    """A document's clauses. `filename` is taken so a prose read and a diagram read are one call;
    a document has no syntax tree to name it in."""
    del filename
    return clauses(text)


def _allowlist_problems(
    documents: dict[Path, str],
    guard: ProseGuard,
    allowlist: Iterable[AllowedClause] | None = None,
    reader: Callable[..., list[Clause]] = _read_prose,
) -> list[str]:
    """Why each entry of `guard`'s allowlist no longer describes one flagged clause.

    The excerpt must find exactly one clause of the entry's file, that clause must still read
    exactly as pinned, and the guard must still flag it. `allowlist` replaces the guard's own
    entries, for the self-test below and for a diagram allowlist, whose files are generators
    read with `reader=diagram_units`. The reader is given the file's path as `filename`, which
    `diagram_units` hands `ast.parse`.
    """
    problems: list[str] = []
    for entry in guard.allowlist if allowlist is None else allowlist:
        label = f"allowlist entry {entry.excerpt!r} ({entry.path.relative_to(ROOT)})"
        if entry.path not in documents:
            problems.append(f"{label} is for a file the guard does not read")
            continue
        read = reader(documents[entry.path], filename=entry.path.relative_to(ROOT).as_posix())
        matches = [clause for clause in read if entry.excerpt in clause.text]
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
        if not guard.is_claim(found.text):
            problems.append(
                f"{label} exempts the clause at line {found.line}, which the guard no longer "
                "flags; remove the entry"
            )
    return problems


@pytest.mark.parametrize("name", GUARDS)
def test_every_allowlist_entry_exempts_exactly_one_flagged_clause(name: str) -> None:
    """An allowlist entry is a statement about one clause; this holds it to that clause.

    A pinned clause that was reworded, fixed, deleted or copied fails here; one that was
    reworded fails the guard's prose test as well, because its new text is pinned by no entry.
    """
    problems = _allowlist_problems(_guarded_documents(), GUARDS[name])
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
    assert not _allowlist_problems(documents, USAGE_GUARD, [entry]), "a current entry was reported"
    unreported = [
        label
        for label, (bad, docs) in stale.items()
        if not _allowlist_problems(docs, USAGE_GUARD, [bad])
    ]
    assert not unreported, f"the allowlist check did not report: {unreported}"


ALLOWLIST_INSERTIONS: Final[dict[str, dict[str, str]]] = {
    "usage": {
        "zh": "，每次运行的 Token 与估算成本都会自动入账",
        "en": ", and every run's token usage and cost are recorded",
    },
    "model-call": {
        "zh": "，模型调用按 408、429 与 5xx 分类重试",
        "en": ", and every model call is retried on 408, 429 and 5xx",
    },
    "capability": {
        "zh": "，模型能力由治理层注册",
        "en": ", and each model is chosen by its registered capabilities",
    },
}
"""A claim of each guard's kind to write into a pinned clause, straight after its excerpt, by
the clause's language. None holds a clause end, so the claim lands inside the pinned clause."""


@pytest.mark.parametrize("name", GUARDS)
def test_a_claim_written_into_an_allowlisted_clause_ends_its_exemption(name: str) -> None:
    """The review of `D7` wrote a claim into each pinned usage clause and both tests stayed green.

    For each entry, a claim from `ALLOWLIST_INSERTIONS` is written into the real document right
    after the entry's excerpt; the guard's prose test and allowlist test must then both fail.
    While the exemption was granted by excerpt, both passed.
    """
    guard = GUARDS[name]
    documents = _guarded_documents()
    still_exempt: list[str] = []
    for entry in guard.allowlist:
        insertion = ALLOWLIST_INSERTIONS[name]["en" if entry.clause.isascii() else "zh"]
        edited = documents[entry.path].replace(entry.excerpt, entry.excerpt + insertion, 1)
        assert edited != documents[entry.path], f"{entry.excerpt!r} is not in its document"
        changed = {**documents, entry.path: edited}
        if not _violations(changed, guard) or not _allowlist_problems(changed, guard):
            still_exempt.append(entry.excerpt)
    assert not still_exempt, (
        f"a claim written into these pinned clauses went unreported: {still_exempt}"
    )


# --- The classifiers' own tests ---------------------------------------------------------------


def _flagged_texts(document: str, is_claim: Callable[[str], bool] = _is_usage_claim) -> list[str]:
    return [flagged.text for flagged in _flagged_clauses(document, is_claim)]


def test_the_usage_guard_tells_a_claim_from_a_stated_condition() -> None:
    """Minimal clauses in the shapes the usage guard exists to tell apart, each judged alone.

    The four claims after the negated condition are the review of `D7`'s false negatives, and
    the three after them its Minor finding M-1: a prefix of a condition that states no
    condition. The five after those are the review of `D12`'s M-4, a condition verb with another
    object, and the last two are what `D13` stopped `MODEL_CALL_CONDITION` from exempting, the
    second in the shape of section 005 at `d4ef5e4`. Among the non-claims, the four after the
    credential are the lines the review of `D7` required the cost rule to leave alone, the two
    after them hold each half of the cost rules to its other half, `## Usage` and the `usage:`
    line are that review's Minor finding M-2, and the last two are true sentences `D13` wrote.
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
        "Token 与估算成本持续入账，出厂路径不需要额外配置。",
        "出厂路径不依赖大模型密钥，接入模型后 Token 与估算成本持续入账。",
        "No shipped path needs an API key, and every call's token usage is recorded.",
        "Token 与估算成本持续入账，出厂路径不写入密钥。",
        "Token 与估算成本持续入账，出厂路径不记录密钥。",
        "No shipped path records your API key, and every call's token usage is recorded.",
        "No shipped path writes secrets to disk, and model costs are recorded per run.",
        "出厂路径不会记录你的密钥，每次调用的 Token 与成本都自动入账。",
        "Token 与估算成本另有账本，出厂路径不调用模型。",
        "模型侧的 Token、尝试次数与估算成本账本要在代码中接入模型 Provider 才会用上，"
        "出厂路径不调用模型。",
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
        "## Usage",
        "usage: openalpha [-h] [--json]",
        "for a model provider you construct in your own code (no shipped path calls a model): "
        "classified retry, and a token and configured-cost usage ledger that no shipped path "
        "writes to -- only a provider built with a usage store records into it;",
        "Token 与成本账本要接自带用量追踪的 Provider 才写入，出厂路径不记账\N{FULLWIDTH SEMICOLON}",
    )
    missed = [claim for claim in claims if not _is_usage_claim(claim)]
    wrongly = [text for text in non_claims if _is_usage_claim(text)]
    assert not missed and not wrongly, f"read as no claim: {missed}; read as a claim: {wrongly}"


def test_the_model_call_guard_tells_a_claim_from_a_stated_condition() -> None:
    """Minimal clauses in the shapes the model-call guard exists to tell apart, each alone.

    Seven claims each name a single behaviour -- 429, 退避 and 幻觉, and 401, 408, 5xx and
    "backoff", which no test pinned until the review of `D12` measured that deleting any of them
    passed (its M-2) -- so dropping any one of those markers shows here; 重试, "retry" and
    "schema" are each pinned alone by `PRE_D12_MODEL_CALL_CLAIMS`. The last five claims carry a
    condition that does not count: negated, a prefix that states no condition, an English
    sentence that says something else about the shipped path, and -- the review of `D12`'s M-4
    -- a condition verb whose object is not a model. The last non-claim names a registry and no
    behaviour: capability registration left this guard in `D13`.
    """
    claims = (
        "模型调用按 408、429、5xx 分类重试。",
        "模型侧对 429 等错误进行分类退避。",
        "Classified model retries on 408, 429 and 5xx.",
        "The LLM's output gets schema validation and bounded retries.",
        "模型请求遇到 429 时自动等待。",
        "模型调用失败时指数退避。",
        "你可以判断失败来自模型幻觉。",
        "模型调用遇到 401 立即失败。",
        "模型请求遇到 408 时等待。",
        "模型端点返回 5xx 时等待。",
        "Model calls use exponential backoff.",
        "并非出厂路径不调用模型，模型调用按 429 退避。",
        "出厂路径不依赖模型密钥，模型调用按 429 退避。",
        "No shipped path needs a model key, and model calls retry on 429.",
        "模型调用按 429 分类退避，出厂路径不调用模型密钥。",
        "No shipped path sends a model key anywhere, and model calls retry on 429.",
    )
    non_claims = (
        "在代码中接入模型 Provider 后，对 408、429、5xx 分类重试，出厂路径不调用模型。",
        "a model provider wired in through the SDK gets bounded retries: "
        "no shipped path calls a model.",
        "Tushare 请求失败会有界重试，频率超限时等待配额窗口。",
        "SQLite 批量队列支持并发、进度、取消、重试和重启恢复。",
        "RunManifest 记录代码、配置、Provider、模型、Prompt、随机种子和环境。",
        "对提示词工程、模型路由、成本优化和 Agent 评测都很有价值。",
        "AlphaModel 的预测批次按 schema 校验。",
        "OpenAlpha CN 保存每个 Agent 的结构化输出，因子、智能体与模型份额结构性不产生。",
        "for a model provider you construct in your own code (no shipped path calls a model): "
        "classified retry, and a token and configured-cost usage ledger that no shipped path "
        "writes to -- only a provider built with a usage store records into it;",
        "模型客户端库定义了能力注册表与能力元数据，但没有代码据此选择端点。",
    )
    missed = [claim for claim in claims if not _is_model_call_claim(claim)]
    wrongly = [text for text in non_claims if _is_model_call_claim(text)]
    assert not missed and not wrongly, f"read as no claim: {missed}; read as a claim: {wrongly}"


def test_the_capability_guard_tells_a_claim_from_a_stated_condition() -> None:
    """Minimal clauses in the shapes the capability guard exists to tell apart, each alone.

    The first seven claims each name one marker -- 能力注册, 注册表, 注册 then 能力, 按 ... 能力
    ... 选, 能力选择, "registry", and "capability" before a verb of registering -- so dropping
    any one of them shows here; the eighth is section 040's wording at `d4ef5e4`. The next two
    carry a condition that is not this guard's -- wiring a provider registers nothing, so neither
    出厂路径不调用模型 nor a usage condition exempts -- and the last is negated. Among the
    non-claims, the first is the wording `README.md` uses now, and the rest name a registry, or
    a capability, that is no model's.
    """
    claims = (
        "模型侧还有能力注册。",
        "模型注册表按厂商分组。",
        "为每个模型注册能力。",
        "按模型能力挑选端点。",
        "模型的能力选择由元数据决定。",
        "The model registry lists each endpoint.",
        "Capabilities are registered once a model provider is wired.",
        "模型能力由治理层注册。",
        "模型侧的能力注册要在代码中接入模型 Provider 才会用上，出厂路径不调用模型。",
        "Token 账本与能力注册表都已就绪，出厂路径不会自动记账。",
        "并非没有代码据此选择端点，模型按能力注册表挑选。",
    )
    non_claims = (
        "模型客户端库定义了能力注册表与能力元数据，但没有代码据此选择端点。",
        "pnpm audit --audit-level high --registry https://registry.npmjs.org",
        "后者探的是 provider 凭证与能力，前者读的是面板本身。",
        "OpenAlpha CN 登记已知限制的注册表有 35 个。",
    )
    missed = [claim for claim in claims if not _is_capability_claim(claim)]
    wrongly = [text for text in non_claims if _is_capability_claim(text)]
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
    "marketing:306, the closing promise": (
        "想看多智能体如何真正算经济账，现在就下载 OpenAlpha CN。\n"
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
and for the marketing bodies the clause (or two) the claim sits in. The first version of this
guard missed the three from section 037, which the review of `D7` measured; the closing promise
also stood unchanged after `D7`, until `D12`."""


def test_the_usage_claims_this_guard_was_written_for_are_flagged() -> None:
    """The guard's retroactive power, held: every pre-D7 claim must still be flagged."""
    missed = [label for label, text in PRE_D7_CLAIMS.items() if not _flagged_clauses(text)]
    assert not missed, f"the guard no longer flags a pre-D7 usage claim: {missed}"


PRE_D12_MODEL_CALL_CLAIMS: Final[dict[str, str]] = {
    "README.md:17": (
        "| 模型治理 | 模型能力注册、408/429/5xx 分类重试\N{FULLWIDTH SEMICOLON}"
        "Token/尝试次数/估算成本账本要接入自带用量追踪的 Provider 才会写入，"
        "出厂路径不会自动记账 |\n"
    ),
    "README.md:36": (
        "- **模型可插拔**：无 LLM 时可确定性运行\N{FULLWIDTH SEMICOLON}"
        "接入模型后强制结构化输出、Schema 校验和有界重试。\n"
    ),
    "README.md:37": (
        "- **模型治理与核算**：按模型能力选择兼容端点，对 408/429/5xx 分类重试"
        "\N{FULLWIDTH SEMICOLON}Token、尝试次数与按用户单价估算的成本有持久账本，"
        "但要接入自带用量追踪的 Provider 才会写入，出厂路径不会自动记账。\n"
    ),
    "README.md:1105": (
        "4. 无 LLM 也能确定性运行，接入 LLM 时强制结构化输出和有界重试\N{FULLWIDTH SEMICOLON}\n"
    ),
    "README.en.md:42": (
        "- deterministic operation without an LLM, plus schema validation and bounded retries "
        "when a model is used;\n"
    ),
    "README.en.md:45": (
        "- classified model retry, plus a token and configured-cost usage ledger that no "
        "shipped path writes to: only a provider built with a usage store records into it;\n"
    ),
    "why-openalpha:24": (
        "| 研究运行 | 无 LLM 也可确定性运行\N{FULLWIDTH SEMICOLON}模型输出强制 Schema | "
        "降低模型漂移、解析失败和演示式功能 |\n"
    ),
    "marketing:68": (
        "模型注册表区分能力，对 408、429、5xx 分类重试，对认证错误立即失败\N{FULLWIDTH SEMICOLON}\n"
    ),
    "marketing:118": "模型侧也区分 408、429、5xx 和 401，决定重试还是立即终止。\n",
    "marketing:290": (
        "模型能力注册表描述是否支持结构化输出等能力，408、429、5xx 按策略重试，"
        "401 立即失败\N{FULLWIDTH SEMICOLON}\n"
    ),
    "marketing:298": (
        "OpenAlpha CN 将模型错误分类：408、429 与 5xx 执行有界指数退避，认证失败立即终止，"
        "避免无意义重试\N{FULLWIDTH SEMICOLON}\n"
    ),
    "marketing:494": (
        "你可以判断失败来自数据延迟、模型幻觉、风险门过严、A 股不可成交，"
        "还是策略本身没有超额，而不是简单删除一次不好看的回测。\n"
    ),
    "marketing:504": (
        "模型侧还有能力注册与分类退避，防止批量运行变成不可控调用风暴，"
        "Token 与成本账本则要接入自带用量追踪的 Provider 才会写入，出厂路径不会自动记账。\n"
    ),
    "marketing:528": "链邻数据接入、模型重试、风险决定和组合账本都有稳定 ID 关联。\n",
    "marketing:634": "模型可以通过 OpenAI-compatible BYOK 接入，Schema 与重试由治理层处理。\n",
    "marketing:796": (
        "**开场钩子：** 模型工程师别错过，这里不只比答案，还能在同一实验台上比能力、重试和增量。\n"
    ),
}
"""Model-call claims as they stood at `ea88999`, verbatim: whole lines for the READMEs, the
table rows and the hook, and for the marketing bodies the clause the claim sits in."""


def test_the_model_call_claims_this_guard_was_written_for_are_flagged() -> None:
    """The model-call guard's retroactive power, held: every pre-D12 claim must be flagged."""
    missed = [
        label
        for label, text in PRE_D12_MODEL_CALL_CLAIMS.items()
        if not _flagged_clauses(text, _is_model_call_claim)
    ]
    assert not missed, f"the guard no longer flags a pre-D12 model-call claim: {missed}"


PRE_D13_USAGE_CLAIMS: Final[dict[str, str]] = {
    "README.en.md:45": (
        "- for a model provider wired in through the SDK, since no shipped path calls a model: "
        "classified retry, and a token and configured-cost usage ledger that only a provider "
        "built with a usage store writes to;\n"
    ),
    "marketing:44": (
        "模型侧的能力注册与 Token、尝试次数、估算成本账本要在代码中接入模型 Provider 才会用上，"
        "出厂路径不调用模型。\n"
    ),
    "marketing:240": (
        "在代码中接入模型 Provider 后，模型调用按 408、429、5xx 分类重试，尝试、Token 与成本另有"
        "账本，出厂路径不调用模型\N{FULLWIDTH SEMICOLON}\n"
    ),
    "marketing:290": (
        "在代码中接入模型 Provider 后，408、429、5xx 按策略重试，401 立即失败，Token、尝试次数和"
        "按用户单价估算的成本另有账本，出厂路径不调用模型。\n"
    ),
    "marketing:298": (
        "在代码中接入模型 Provider 后，408、429 与 5xx 才执行有界指数退避、认证失败立即终止，"
        "每次尝试、Token 和估算成本另有账本，出厂路径不调用模型。\n"
    ),
    "marketing:412": (
        "链邻 Provider 的数据入口、批量任务的进度和恢复也有记录，模型调用的 Token 与重试次数要在"
        "代码中接入模型才可能入账，出厂路径不调用模型。\n"
    ),
    "marketing:494": (
        "链邻数据接口错误会明确分类，证据不足会 abstain，模型 Token 与重试次数要在代码中接入模型"
        "才可能入账、出厂路径不调用模型，批量任务中断也能恢复。\n"
    ),
    "marketing:504": (
        "模型侧的能力注册、分类退避与 Token 成本账本要在代码中接入模型 Provider 才会用上，"
        "出厂路径不调用模型。\n"
    ),
    "marketing:536": (
        "OpenAlpha CN 将并发设置在 1\N{EN DASH}8 的有界范围，任务中心负责调度，链邻 Provider "
        "执行客户端速率控制，模型侧的 429 等错误分类退避要在代码中接入模型才会用上，每次尝试与 "
        "Token 成本也才可能入账，出厂路径不调用模型。\n"
    ),
}
"""Usage clauses as they stood at `d4ef5e4`, verbatim: the whole line for `README.en.md`, and for
the marketing bodies the clause the claim sits in. Each passed only on 出厂路径不调用模型 or "no
shipped path calls a model", which since `D13` exempts no usage marker; sections 005 and 061 had
also lost the `usage_store` condition they carried at `ea88999`."""


def test_the_usage_claims_d13_found_are_flagged() -> None:
    """The usage guard's retroactive power over what `D13` found, held."""
    missed = [label for label, text in PRE_D13_USAGE_CLAIMS.items() if not _flagged_clauses(text)]
    assert not missed, f"the usage guard no longer flags a pre-D13 usage claim: {missed}"


PRE_D13_CAPABILITY_CLAIMS: Final[dict[str, str]] = {
    "README.md:17": (
        "| 模型治理 | 能力注册与 408/429/5xx 分类重试要在代码中接入模型 Provider 才会用上，"
        "出厂路径不调用模型\N{FULLWIDTH SEMICOLON}Token/尝试次数/估算成本账本要在构造 Provider "
        "时传入 `usage_store` 才会写入，出厂路径不会自动记账 |\n"
    ),
    "README.md:37": (
        "- **模型治理边界**：在代码中接入模型 Provider 后按模型能力选择兼容端点，对 408/429/5xx "
        "分类重试，出厂路径不调用模型\N{FULLWIDTH SEMICOLON}Token、尝试次数与按用户单价估算的成本"
        "有持久账本，但要在构造 Provider 时传入 `usage_store` 才会写入，出厂路径不会自动记账。\n"
    ),
    "marketing:44": PRE_D13_USAGE_CLAIMS["marketing:44"],
    "marketing:282": (
        "在代码中接入 OpenAI-compatible 模型后，才有 Schema 校验、有界重试和能力注册，"
        "出厂路径不调用模型。\n"
    ),
    "marketing:330": (
        "链邻数据通过统一 Provider 生成证据，模型能力由治理层注册，调用成本要接入自带用量追踪的 "
        "Provider 才会入账，出厂路径不会自动记账。\n"
    ),
    "marketing:504": PRE_D13_USAGE_CLAIMS["marketing:504"],
}
"""Capability clauses as they stood at `d4ef5e4`, verbatim: whole lines for `README.md`, and for
the marketing bodies the clause the claim sits in. `D12` made five of them conditional on wiring
a provider in code, which does not make them true; section 040's carried no condition at all."""


def test_the_capability_claims_this_guard_was_written_for_are_flagged() -> None:
    """The capability guard's retroactive power, held: every pre-D13 claim must be flagged."""
    missed = [
        label
        for label, text in PRE_D13_CAPABILITY_CLAIMS.items()
        if not _flagged_clauses(text, _is_capability_claim)
    ]
    assert not missed, f"the capability guard no longer flags a pre-D13 claim: {missed}"


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
        "a condition worded outside USAGE_CONDITION": ("Token 与估算成本持续入账，但默认不开启。"),
    }
    closed = {
        label: _flagged_texts(text) for label, text in unflagged.items() if _flagged_texts(text)
    }
    opened = [label for label, text in flagged.items() if not _flagged_texts(text)]
    assert not closed and not opened, (
        f"a stated blind spot is now flagged: {closed}; a stated misreading no longer happens: "
        f"{opened} -- update the docstring with it"
    )


def test_the_model_call_guards_stated_blind_spots_are_real() -> None:
    """Each limit the module docstring states for the model-call guard, measured.

    The first `unflagged` row is section 097's body as it stood at `ea88999`: its registry and
    retry claim sat one clause after its model word, and was rewritten by hand. The last four
    are the review of `D12`'s M-3: a model named only by its provider class, vendor or product.
    """
    unflagged = {
        "a claim whose model word is in the clause before": (
            "OpenAlpha CN 让所有模型共享同一 A 股 EvidenceSnapshot、SignalFrame、风险和回放合同"
            "\N{FULLWIDTH SEMICOLON}能力注册表描述结构化输出支持，408、429、5xx 分类重试，"
            "401 立即失败\N{FULLWIDTH SEMICOLON}"
        ),
        "a model failure presupposed without a behaviour word": (
            "用户无法判断是模型限流还是任务卡死。"
        ),
        "structured output without the word schema": "模型输出统一为结构化结果。",
        "governance named without a behaviour": "模型治理也能通过公开接口管理。",
        "a claim split across a blank line": "模型调用\n\n按 429 分类退避。\n",
        "a model named by provider class": "OpenAI-compatible Provider 对 408、429、5xx 分类重试。",
        "a model named by vendor": "DeepSeek、Qwen 端点返回 429 时自动退避。",
        "an English provider class": "The OpenAI-compatible provider retries 429s with backoff.",
        "a model named by product": "GPT 请求失败会指数退避。",
    }
    flagged = {
        "a batch retry beside an unrelated model word": (
            "批量队列支持失败重试，RunManifest 记录模型版本。"
        ),
        "a condition worded outside MODEL_CALL_CONDITION": (
            "模型调用按 429 分类退避，但默认不开启。"
        ),
    }
    closed = {
        label: _flagged_texts(text, _is_model_call_claim)
        for label, text in unflagged.items()
        if _flagged_texts(text, _is_model_call_claim)
    }
    opened = [
        label for label, text in flagged.items() if not _flagged_texts(text, _is_model_call_claim)
    ]
    assert not closed and not opened, (
        f"a stated blind spot is now flagged: {closed}; a stated misreading no longer happens: "
        f"{opened} -- update the docstring with it"
    )


def test_the_capability_guards_stated_blind_spots_are_real() -> None:
    """Each limit the module docstring states for the capability guard, measured."""
    unflagged = {
        "a choice worded without a marker": "模型元数据决定调用哪个端点。",
        "a claim split across two clauses": "模型侧维护一份能力清单。注册后按它选端点。",
    }
    flagged = {
        "a registry of something else beside a model word": (
            "模型版本登记在 RunManifest 的注册表里。"
        ),
        "a condition worded outside CAPABILITY_CONDITION": "模型按能力注册表选端点，但默认不开启。",
    }
    closed = {
        label: _flagged_texts(text, _is_capability_claim)
        for label, text in unflagged.items()
        if _flagged_texts(text, _is_capability_claim)
    }
    opened = [
        label for label, text in flagged.items() if not _flagged_texts(text, _is_capability_claim)
    ]
    assert not closed and not opened, (
        f"a stated blind spot is now flagged: {closed}; a stated misreading no longer happens: "
        f"{opened} -- update the docstring with it"
    )


# --- The embedded diagrams --------------------------------------------------------------------

DIAGRAM_GENERATORS: Final[tuple[Path, ...]] = (
    ROOT / "scripts" / "generate_brain_diagrams.py",
    ROOT / "scripts" / "generate_api_relationship_diagrams.py",
)
"""The generators of the ten diagrams `README.md` embeds; `tests/unit/test_repository_assets.py`
holds every committed SVG equal to what they write."""


def _diagram_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in DIAGRAM_GENERATORS
    }


def _diagram_units_by_generator(sources: dict[str, str]) -> list[tuple[str, Clause]]:
    """Every drawing unit of `sources`, each with the generator it came from.

    The generator's path goes to `ast.parse` as `filename`, so a generator that stops parsing is
    named in the SyntaxError instead of `<unknown>`.
    """
    return [
        (label, unit)
        for label, source in sources.items()
        for unit in diagram_units(source, filename=label)
    ]


def _diagram_violations(guard: ProseGuard, sources: dict[str, str]) -> list[str]:
    """One message per clause of a drawing call or data row that `guard` reads as a claim and its
    `diagram_allowlist` does not pin, by the generator's path and the unit's whole text."""
    pinned = {
        (entry.path.relative_to(ROOT).as_posix(), entry.clause) for entry in guard.diagram_allowlist
    }
    return [
        f"{label}:{unit.line} draws {guard.claim} as shipped: {unit.text!r}"
        for label, unit in _diagram_units_by_generator(sources)
        if guard.is_claim(unit.text) and (label, unit.text) not in pinned
    ]


@pytest.mark.parametrize("name", GUARDS)
def test_the_embedded_diagrams_do_not_present_a_model_call_or_usage_recording_as_shipped(
    name: str,
) -> None:
    """Every prose guard over the words the embedded diagrams draw, one unit per drawing call.

    Written against brain-03's 模型治理边界 box, which read "能力注册 · Schema 校验 · 408/429/5xx
    重试" and "Token / 尝试次数 / 估算成本持久化" as system behaviour. Only a guard's
    `diagram_allowlist` applies here: one entry, the usage guard's pin on that box as `D12`
    rewrote it. The box must still be read, so a reader or generator list gone blind fails rather
    than passes. Three other cost words the diagrams drew as shipped -- "agent_outputs /
    routing_path / 成本" in brain-03, "Retry / Recovery · 成本账本" in brain-01 and
    "同一成本与恢复语义" in brain-05 -- carry no marker these guards read, and were rewritten by
    hand.
    """
    guard = GUARDS[name]
    sources = _diagram_sources()
    assert any("模型治理边界" in unit.text for _, unit in _diagram_units_by_generator(sources)), (
        "brain-03's 模型治理边界 panel, which this test was written against, is no longer read"
    )
    violations = _diagram_violations(guard, sources)
    assert not violations, (
        "\n".join(violations) + f"\n{guard.remedy} Fix the generator, then regenerate the SVG "
        "with the generator itself."
    )


def test_every_diagram_allowlist_entry_exempts_exactly_one_flagged_unit() -> None:
    """A diagram pin is held to its unit the way a prose pin is held to its clause.

    A pinned box that was reworded, fixed, deleted or drawn twice fails here, and one that was
    reworded fails the diagram test above as well, because its new text is pinned by no entry.
    """
    sources = {ROOT / label: source for label, source in _diagram_sources().items()}
    problems = [
        problem
        for guard in GUARDS.values()
        for problem in _allowlist_problems(
            sources, guard, guard.diagram_allowlist, reader=diagram_units
        )
    ]
    assert not problems, "\n".join(problems)


BROKEN_GENERATOR: Final[str] = 'svg.panel(1, 2, title="模型治理边界",\n'
"""A generator cut off inside a drawing call, which `ast.parse` refuses."""


def test_a_diagram_read_names_the_generator_it_could_not_parse() -> None:
    """Both diagram reads hand `ast.parse` the generator's path as `filename`.

    A generator that stops parsing is then named in the SyntaxError instead of `<unknown>`: the
    diagram test reads two generators one after the other, and the pin census reads the pinned
    one. `tests/diagram_text.py` took `filename` for exactly this.
    """
    label = "scripts/generate_broken_diagrams.py"
    pin = replace(USAGE_DIAGRAM_ALLOWLIST[0], path=ROOT / label)
    reads: dict[str, Callable[[], object]] = {
        "_diagram_violations": lambda: _diagram_violations(USAGE_GUARD, {label: BROKEN_GENERATOR}),
        "_allowlist_problems": lambda: _allowlist_problems(
            {ROOT / label: BROKEN_GENERATOR}, USAGE_GUARD, (pin,), reader=diagram_units
        ),
    }
    named: dict[str, str | None] = {}
    for name, read in reads.items():
        with pytest.raises(SyntaxError) as raised:
            read()
        named[name] = raised.value.filename
    assert named == dict.fromkeys(reads, label), f"a diagram read named its source as {named}"


PRE_D12_DIAGRAM_BOX: Final[str] = (
    "svg.panel(\n"
    "    1000,\n"
    "    532,\n"
    "    376,\n"
    "    138,\n"
    '    title="模型治理边界",\n'
    '    label="OPTIONAL MODEL ENHANCEMENT",\n'
    "    color=CORAL,\n"
    '    lines=("能力注册 \N{MIDDLE DOT} Schema 校验 \N{MIDDLE DOT} 408/429/5xx 重试", '
    '"Token / 尝试次数 / 估算成本持久化"),\n'
    ")\n"
)
"""brain-03's 模型治理边界 box as `scripts/generate_brain_diagrams.py` drew it at `ea88999`."""


@pytest.mark.parametrize("name", GUARDS)
def test_the_diagram_guards_flag_the_box_they_were_written_for(name: str) -> None:
    """Each guard's retroactive power over the diagrams, held: the pre-D12 box is one claim."""
    violations = _diagram_violations(GUARDS[name], {"ea88999 box": PRE_D12_DIAGRAM_BOX})
    assert len(violations) == 1, f"the pre-D12 box was read as {violations}"


DIAGRAM_SOURCE: Final[
    str
] = '''"""A generator's docstring names 模型 and 408 重试, and is never drawn."""

STAGES = [
    (1, "02", "研究编排", ("持久批量队列 1-8 并发", "run_cycle")),
]


def draw(svg):
    """Nor is a function's."""
    svg.panel(1, 2, title="模型治理边界", lines=("Schema 校验 408 重试", "出厂路径不调用模型"))
    svg.text(3, 4, "第一句。第二句")
    svg.pill(5, 6, svg.label("内层"), "外层")
'''
"""A synthetic generator holding each shape `tests/diagram_text.py` reads, and a docstring."""


def test_the_diagram_reader_reads_what_its_docstring_says() -> None:
    """A data row with its nested lines is one unit; a panel's title and lines are one unit; a
    sentence end splits a unit; a nested call is a unit of its own; no docstring is read.

    `diagram_strings` returns every literal alone, docstrings excepted.
    """
    units = sorted(clause.text for clause in diagram_units(DIAGRAM_SOURCE))
    expected_units = sorted(
        (
            "02，研究编排，持久批量队列 1-8 并发，run_cycle",
            "模型治理边界，Schema 校验 408 重试，出厂路径不调用模型",
            "第一句。",
            "第二句",
            "外层",
            "内层",
        )
    )
    strings = sorted(clause.text for clause in diagram_strings(DIAGRAM_SOURCE))
    expected_strings = sorted(
        (
            "02",
            "研究编排",
            "持久批量队列 1-8 并发",
            "run_cycle",
            "模型治理边界",
            "Schema 校验 408 重试",
            "出厂路径不调用模型",
            "第一句。第二句",
            "内层",
            "外层",
        )
    )
    assert units == expected_units, f"diagram_units read {units}"
    assert strings == expected_strings, f"diagram_strings read {strings}"
