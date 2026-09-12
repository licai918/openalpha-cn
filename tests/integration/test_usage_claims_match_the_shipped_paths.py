"""User-facing prose may not present model usage recording as something a shipped path does.

**The code fact this guard stands on**, measured at `2d197af` and held by the premise test below
rather than by this paragraph. The usage ledger exists: `build_storage` constructs
`SQLiteModelUsageStore` for every runtime directory, so every shipped path -- CLI, REST, SDK --
creates its `model_usage` table in `state.sqlite3`. Nothing shipped writes a row to it. The one
writer is `OpenAICompatibleProvider._record_usage`, which appends a `ModelUsageRecord` (tokens,
attempts, and a cost estimated from user-configured prices) when the provider was built with a
`usage_store` and the endpoint reported usage. No module under `src/` or `scripts/` constructs
that provider -- only `tests/unit/models/` does -- and no CLI command, REST route or SDK method
reads or writes the ledger. So "Token and estimated cost are recorded" is true of a component a
deployer can wire, and false of the product as it ships.

Two couplings:

1. **The premise.** The test
   `test_no_shipped_path_constructs_the_usage_recording_provider_or_writes_usage_rows` reads
   the syntax tree of every `.py` file under `src/` and `scripts/` and holds the places that
   name the provider, build a usage record or write the usage table equal to `USAGE_SITES`,
   where each place carries the reason it records nothing. The day a shipped path starts recording
   usage, that test fails and asks for the documents to be rewritten -- and for this guard to be
   retired or inverted -- instead of this guard silently blocking claims that have become true.

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
- *Usage markers.* A clause names usage recording when it holds one of `USAGE_TERMS`, or matches
  `MODEL_COST` (模型, at most four characters with no punctuation between, then 成本: 模型成本,
  模型能力与成本), or names a token -- "Token" in any case as an English word, plural included --
  beside one of the words in `TOKEN_ACCOUNTING`. A bare token is not enough: these documents say
  Token for a data-service credential more often than for model usage.
- *Claims.* A clause that names usage recording is a claim unless it holds one of
  `SHIPPED_PATH_CONDITIONS`, not directly negated (`prose_clauses.holds_unnegated_phrase`). The
  condition exempts only the clause it sits in.
- *`ALLOWLIST`* holds the true non-claims the markers catch -- a statement about the test suite,
  a topic named as a channel suggestion -- each pinned to one clause by a distinctive excerpt and
  carrying the reason it is true. The test
  `test_every_usage_allowlist_entry_exempts_exactly_one_flagged_clause` fails on an entry that
  matches no clause, matches several, or exempts a clause the guard no longer flags. With the
  prose test that makes the allowlist the census: every clause the guard flags in the four files
  is either rewritten or listed there.

**What the prose guard cannot see.** `test_the_usage_guards_stated_blind_spots_are_real`
measures each of these.

- A clause naming none of the markers is never a claim, however plainly it implies recording:
  "你可以比较各委员会各自多花了多少。" passes.
- Within one clause, one condition phrase exempts every marker in it, even a contradicting one:
  "Token 与估算成本持续入账，出厂路径不会自动生成账单。" passes.
- A claim split across two blocks or two clauses is read as two halves, each innocent.
- The other direction: a credential token beside an accounting word is read as usage
  ("Token 持久保存在环境变量里。" is flagged), and a condition worded outside
  `SHIPPED_PATH_CONDITIONS` is read as a claim.

**What the premise test cannot see.** It reads names, calls and string literals, not behaviour:
a provider reached through `importlib` with a computed name, a record rebuilt by `model_copy`
from one read back, or a write through a store other than `model_usage` is invisible to it.
`test_the_premise_scan_sees_each_way_a_module_could_start_recording_usage` pins what it does
see.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from prose_clauses import clauses, holds_unnegated_phrase, marker_pattern

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"
MARKETING: Final[Path] = ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md"

GUARDED_FILES: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA, MARKETING)


# --- Part 1: the premise -- no shipped path records model usage ------------------------------

NON_TEST_SOURCE_ROOTS: Final[tuple[Path, ...]] = (ROOT / "src", ROOT / "scripts")
"""Where shipped code lives. `tests/` is the one tree allowed to build the provider freely."""

USAGE_PROVIDER: Final[str] = "OpenAICompatibleProvider"
USAGE_RECORD: Final[str] = "ModelUsageRecord"

USAGE_TABLE_WRITE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:insert|replace|update)\b(?:\s+or\s+\w+)?(?:\s+into)?\s+model_usage\b", re.IGNORECASE
)
"""SQL that writes the ledger's table: `INSERT [OR ...] INTO`, `REPLACE INTO`, `UPDATE`."""

PROVIDER_NAMED: Final[str] = "names the provider"
RECORD_BUILT: Final[str] = "builds a usage record"
TABLE_WRITTEN: Final[str] = "writes the usage table"


@dataclass(frozen=True, slots=True, order=True)
class UsageSite:
    """One place in a module that could make usage recording reachable, and what it does there.

    `scope` is the dotted name of the enclosing class and function, or `<module>`.
    """

    path: str
    scope: str
    kind: str


class _UsageSiteScan(ast.NodeVisitor):
    """Collect every `UsageSite` in one module's syntax tree."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.scopes: list[str] = []
        self.sites: set[UsageSite] = set()

    def _found(self, kind: str) -> None:
        self.sites.add(UsageSite(self.path, ".".join(self.scopes) or "<module>", kind))

    def _scoped(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    visit_ClassDef = _scoped
    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == USAGE_PROVIDER:
            self._found(PROVIDER_NAMED)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == USAGE_PROVIDER:
            self._found(PROVIDER_NAMED)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        if USAGE_PROVIDER in (node.name.rsplit(".", 1)[-1], node.asname):
            self._found(PROVIDER_NAMED)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            if node.value == USAGE_PROVIDER:
                self._found(PROVIDER_NAMED)
            if USAGE_TABLE_WRITE.search(node.value):
                self._found(TABLE_WRITTEN)

    def visit_Call(self, node: ast.Call) -> None:
        callee = node.func
        if (isinstance(callee, ast.Name) and callee.id == USAGE_RECORD) or (
            isinstance(callee, ast.Attribute)
            and (
                callee.attr == USAGE_RECORD
                or (isinstance(callee.value, ast.Name) and callee.value.id == USAGE_RECORD)
            )
        ):
            self._found(RECORD_BUILT)
        self.generic_visit(node)


def _usage_sites(sources: Iterable[tuple[str, str]]) -> frozenset[UsageSite]:
    """Every `UsageSite` in `sources`, given as `(path label, module source)` pairs."""
    found: set[UsageSite] = set()
    for path, source in sources:
        scan = _UsageSiteScan(path)
        scan.visit(ast.parse(source, filename=path))
        found |= scan.sites
    return frozenset(found)


def _non_test_sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for root in NON_TEST_SOURCE_ROOTS
        for path in sorted(root.rglob("*.py"))
    ]


USAGE_SITES: Final[dict[UsageSite, str]] = {
    UsageSite("src/openalpha_cn/models/__init__.py", "<module>", PROVIDER_NAMED): (
        "The package re-exports the class (an import and its `__all__` entry); nothing here "
        "constructs it."
    ),
    UsageSite(
        "src/openalpha_cn/models/openai_compatible.py",
        "OpenAICompatibleProvider._record_usage",
        RECORD_BUILT,
    ): (
        "The one writer: it appends a record only when the provider was built with a "
        "`usage_store`, and no non-test module builds the provider (no other site names it)."
    ),
    UsageSite(
        "src/openalpha_cn/storage/models.py", "SQLiteModelUsageStore.append", TABLE_WRITTEN
    ): (
        "The ledger's own INSERT, reached only through `append`, whose one caller is "
        "`_record_usage` above."
    ),
    UsageSite("src/openalpha_cn/storage/models.py", "SQLiteModelUsageStore.list", RECORD_BUILT): (
        "Reading rows back: `ModelUsageRecord.model_validate_json` rebuilds what was stored and "
        "writes nothing."
    ),
}
"""Every `UsageSite` outside `tests/`, each with the reason it leaves the ledger empty."""


def test_no_shipped_path_constructs_the_usage_recording_provider_or_writes_usage_rows() -> None:
    """The premise of the prose guard below, read off the code rather than remembered.

    Equality, not a subset: a site that disappears fails too, so a scan that stopped finding
    anything cannot pass as a clean one.
    """
    found = _usage_sites(_non_test_sources())
    new = sorted(found - USAGE_SITES.keys())
    gone = sorted(USAGE_SITES.keys() - found)
    assert not new and not gone, (
        f"new usage sites: {new}\nsites no longer found: {gone}\n"
        "If a shipped path now builds OpenAICompatibleProvider or writes model_usage rows, the "
        "prose guard's premise is false: rewrite the usage sentences in the four guarded "
        "documents to say what is recorded now, and retire or invert "
        "test_user_facing_docs_do_not_present_usage_recording_as_shipped. "
        "If the site records nothing, add it to USAGE_SITES with the reason."
    )


SCAN_CASES: Final[dict[str, tuple[str, frozenset[UsageSite]]]] = {
    "a direct construction": (
        "from openalpha_cn.models import OpenAICompatibleProvider\n\n"
        "def build():\n"
        "    return OpenAICompatibleProvider(provider_id='p', model='m',\n"
        "        base_url='https://example.test', api_key_env=None)\n",
        frozenset(
            {
                UsageSite("m.py", "<module>", PROVIDER_NAMED),
                UsageSite("m.py", "build", PROVIDER_NAMED),
            }
        ),
    ),
    "an aliased import": (
        "from openalpha_cn.models import OpenAICompatibleProvider as Provider\n",
        frozenset({UsageSite("m.py", "<module>", PROVIDER_NAMED)}),
    ),
    "an attribute of the package": (
        "import openalpha_cn.models as models\n\n"
        "def build():\n"
        "    return models.OpenAICompatibleProvider\n",
        frozenset({UsageSite("m.py", "build", PROVIDER_NAMED)}),
    ),
    "the name as a string": (
        "import importlib\n\n"
        "def build():\n"
        "    return getattr(importlib.import_module('openalpha_cn.models'),\n"
        "        'OpenAICompatibleProvider')\n",
        frozenset({UsageSite("m.py", "build", PROVIDER_NAMED)}),
    ),
    "a record built directly": (
        "def log(store, **fields):\n    store.append(ModelUsageRecord(**fields))\n",
        frozenset({UsageSite("m.py", "log", RECORD_BUILT)}),
    ),
    "a record built by a classmethod": (
        "class Meter:\n"
        "    def log(self, store, fields):\n"
        "        store.append(ModelUsageRecord.model_validate(fields))\n",
        frozenset({UsageSite("m.py", "Meter.log", RECORD_BUILT)}),
    ),
    "a record built through a module": (
        "from openalpha_cn.models import governance\n\n"
        "def log(store, **fields):\n    store.append(governance.ModelUsageRecord(**fields))\n",
        frozenset({UsageSite("m.py", "log", RECORD_BUILT)}),
    ),
    "raw SQL into the table": (
        "def log(connection, row):\n"
        "    connection.execute('INSERT OR IGNORE INTO model_usage VALUES (?, ?, ?)', row)\n",
        frozenset({UsageSite("m.py", "log", TABLE_WRITTEN)}),
    ),
    "what writes nothing": (
        '"""Mentions OpenAICompatibleProvider and INSERTs nothing into model_usage_store."""\n\n'
        "def read(connection, record: ModelUsageRecord) -> None:\n"
        "    connection.execute('SELECT payload FROM model_usage')\n"
        "    connection.execute('CREATE TABLE IF NOT EXISTS model_usage (payload TEXT)')\n",
        frozenset(),
    ),
}
"""Synthetic modules, each with the sites the scan must report for it."""


def test_the_premise_scan_sees_each_way_a_module_could_start_recording_usage() -> None:
    """The scan behind the premise, held to modules whose sites are known.

    The last case is the other direction: a docstring naming the provider, an annotation naming
    the record, and a `SELECT` or `CREATE TABLE` on the ledger write nothing, and must not look
    as though they do.
    """
    wrong = {
        label: sorted(_usage_sites([("m.py", source)]))
        for label, (source, expected) in SCAN_CASES.items()
        if _usage_sites([("m.py", source)]) != expected
    }
    assert not wrong, f"the premise scan misread: {wrong}"


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
    r"成本|记录|记下|入账|持久|写入|账本|核算|(?<![A-Za-z])(?:cost|accounting|ledger|record|persist)",
    re.IGNORECASE,
)
"""Words that make a token a usage figure. English ones match as word starts: "recorded" counts."""

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
    """A clause the guard flags that is true as written, and why."""

    path: Path
    excerpt: str
    reason: str


ALLOWLIST: Final[tuple[AllowedClause, ...]] = (
    AllowedClause(
        path=MARKETING,
        excerpt="FinOps、模型成本专题、团队采购评估",
        reason=(
            "Section 037's channel suggestion names model cost as the topic of the channels to "
            "post in; it presents nothing as recorded."
        ),
    ),
    AllowedClause(
        path=MARKETING,
        excerpt="模型错误、Token 和成本也有确定性用例",
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


def test_user_facing_docs_do_not_present_usage_recording_as_shipped() -> None:
    """Every clause the guard flags in the four files is a violation unless `ALLOWLIST` lists it.

    Written against the 23 usage lines the D7 brief counted in the marketing pack, the extra ones
    its review found, and `README.md`'s and `README.en.md`'s model-governance bullets -- each
    presented Token, attempt or cost recording as a shipped capability.
    """
    violations: list[str] = []
    for path in GUARDED_FILES:
        entries = [entry for entry in ALLOWLIST if entry.path == path]
        for flagged in _flagged_clauses(path.read_text(encoding="utf-8")):
            if not any(entry.excerpt in flagged.text for entry in entries):
                violations.append(
                    f"{path.relative_to(ROOT)}:{flagged.line} presents usage recording as "
                    f"shipped: {flagged.text!r}"
                )
    assert not violations, (
        "\n".join(violations) + "\nSay in the same clause that no shipped path records it -- "
        "one of SHIPPED_PATH_CONDITIONS, as section 057 does -- or reword the claim. Add an "
        "ALLOWLIST entry only for a clause that is true as written."
    )


def test_every_usage_allowlist_entry_exempts_exactly_one_flagged_clause() -> None:
    """An `ALLOWLIST` entry is a statement about one clause; this holds it to that clause."""
    problems: list[str] = []
    for entry in ALLOWLIST:
        label = f"ALLOWLIST entry {entry.excerpt!r} ({entry.path.relative_to(ROOT)})"
        if entry.path not in GUARDED_FILES:
            problems.append(f"{label} is for a file the guard does not read")
            continue
        matches = [
            clause
            for clause in clauses(entry.path.read_text(encoding="utf-8"))
            if entry.excerpt in clause.text
        ]
        if len(matches) != 1:
            problems.append(
                f"{label} matches {len(matches)} clauses, at lines {[c.line for c in matches]}; "
                "it must match exactly one"
            )
            continue
        if not _is_usage_claim(matches[0].text):
            problems.append(
                f"{label} exempts the clause at line {matches[0].line}, which the guard no "
                "longer flags; remove the entry"
            )
    assert not problems, "\n".join(problems)


# --- The classifier's own tests ---------------------------------------------------------------


def _flagged_texts(document: str) -> list[str]:
    return [flagged.text for flagged in _flagged_clauses(document)]


def test_the_usage_guard_tells_a_claim_from_a_stated_condition() -> None:
    """Minimal clauses in the shapes the guard exists to tell apart, each judged alone."""
    claims = (
        "Token 与估算成本持续入账。",
        "系统记录模型、尝试次数、输入输出 Token。",
        "classified model retry plus persistent token and configured-cost accounting;",
        "批量、恢复、模型成本、筛选、观察池和报告中心又让系统可以长期使用。",
        "模型能力与成本也由治理层管理。",
        "SQLite 任务、决策、Checkpoint、模型用量、观察池和报告都能跨进程保存。",
        "Per-request usage is metered for every run.",
        "并非出厂路径不会自动记账，Token 与估算成本持续入账。",
    )
    non_claims = (
        "模型调用的 Token 与估算成本另有账本，但要接入自带用量追踪的 Provider 才会写入，"
        "出厂路径不会自动生成账单。",
        "a token and configured-cost ledger that no shipped path writes to;",
        "链邻服务地址和 Token 由用户安全配置，不写进镜像。",
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
and for the marketing bodies the clause (or two) the claim sits in."""


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
        "one condition exempts every marker in its clause": (
            "Token 与估算成本持续入账，出厂路径不会自动生成账单。"
        ),
        "a claim split across a blank line": "模型调用的 Token\n\n也有记录。\n",
        "a claim split across two clauses": "模型调用的 Token 很多。每一次都有记录。",
    }
    flagged = {
        "a credential token beside an accounting word": "Token 持久保存在环境变量里。",
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
