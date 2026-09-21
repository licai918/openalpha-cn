"""Every count the four user-facing documents state is the one the code or the ledger holds.

`README.md`, `README.en.md`, `docs/why-openalpha-cn.zh-CN.md` and the marketing pack state counts
the code can answer: how many factors this build declares and how they split by family, how many
Tushare datasets and panel build targets there are, how many named boundaries a model answer
carries, what the feature ledger totals, what the coverage gate is. The final review found five
of them stale at `d4ef5e4` (its I4): 19 declared factors where `FACTOR_EVALUATORS` holds 21,
Tushare 15/15 where `TUSHARE_DATASETS` holds 16, 十三个目标 where `PANEL_BUILD_TARGETS` holds 14,
nine named boundaries where `KNOWN_MODEL_VIEW_LIMITATIONS` holds 16, and a ledger of 90 with 85
complete where `summary.json` says 185 and 180. Beside them `D13` found the family split and the
count of datasets whose probe needs a subject (five, now six) stale too.

**How a count is read.** Each `DocumentedCount` derives its value from the code or the ledger at
test time and names the phrasings that state it, as patterns whose named groups capture the
number. Every clause of the four documents (`tests/prose_clauses.py`) is searched with every
pattern, and each captured number must equal the derived value. A number is ASCII digits, a
Chinese numeral up to 9999 in its standard form (四, 十三, 二十一, 一百八十五, 两千), an
English number up to ninety-nine (nine, twenty-one), or 唯一 for one, and it is read whole or
not at all: a match never starts inside a longer number. The review of `D13` measured 一百八十五
read as 85 and twenty-one as 1 (its m-11). A number after 第 is an ordinal and is not read: the
same review measured 第 5 个 read as a count of five.

**Every statement is counted.** Each count's `stated` census holds how many of its statements
each document makes, and each document must make exactly that many. So a reword that moves any
one statement out of its patterns' reach fails instead of passing over nothing -- until the
review of `D13` only a count stated nowhere failed, and 21 个已声明因子 passed on README.md's
other statement of the count -- and a statement added or removed on purpose moves the census
with it.

**Counts nothing derives are not stated.** How many files the publication gate scans, what a run's
coverage came to and how many issues the roadmap lists change with the repository and are answered
by nothing this test can read, so the documents stop stating them rather than this test guarding
a copy: `UNDERIVED_COUNTS` fails on any clause that states one.

**What it cannot see.** It reads these phrasings only. A count stated in other words --
"声明了二十一个因子", "a library of 21 factors" -- is not read, nor is one in the embedded
diagrams or in any other document. A numeral written with 万 and an English number above
ninety-nine are not read at all, and a Chinese numeral in no standard form (一百八) is read and
reported as unreadable. The census counts statements, not where they stand: one statement
reworded out of reach and another added to the same document in the same change leave it equal,
and pass. `UNDERIVED_COUNTS` catches its patterns only: "一百七十八个公开文件" passes.
`test_the_count_guards_stated_blind_spots_are_real` measures one paraphrase of each kind, and
`test_a_statement_reworded_out_of_reach_is_reported` the census's limit. The other direction: a
Chinese numeral is read wherever a pattern's unit follows it, so the 一 of a word such as 同一
would read as one. `NUMBER` refuses a numeral after 同, 第, 之, 某, 统 and 单 -- README.md's
同一个判决, "the same verdict", was read as a count of one verdict until it did -- and after no
other word.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final, get_args

from prose_clauses import clauses

from openalpha_cn.backtest.factor_experiment import AttributionVerdict
from openalpha_cn.cli import PANEL_BUILD_TARGETS
from openalpha_cn.model_view import KNOWN_MODEL_VIEW_LIMITATIONS
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS, FACTOR_EVALUATORS, FACTOR_TRANSFORMS
from openalpha_cn.panel_neutralization import FACTOR_NEUTRALIZATIONS
from openalpha_cn.providers.tushare import TUSHARE_DATASETS, TushareProvider

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"
MARKETING: Final[Path] = ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md"
LEDGER_SUMMARY: Final[Path] = ROOT / "artifacts" / "openalpha-v1-feature-coverage" / "summary.json"
ROADMAP: Final[Path] = ROOT / "docs" / "specs" / "v2" / "openalpha-cn-v2-roadmap.md"

GUARDED_FILES: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA, MARKETING)

_NOT_END: Final[str] = r"[^。;\N{FULLWIDTH SEMICOLON}]"


# --- Reading a number ---------------------------------------------------------------------------

_CHINESE_DIGITS: Final[str] = "零一二三四五六七八九"


def _standard_chinese_numeral(value: int) -> str:
    """`value`, from 0 to 9999, as a Chinese numeral in its standard form: 一百八十五, 一千零五,
    一百一十, and 十三 rather than 一十三."""
    if value == 0:
        return "零"
    written, skipped = "", False
    places = (value // 1000, value // 100 % 10, value // 10 % 10, value % 10)
    for digit, unit in zip(places, ("千", "百", "十", ""), strict=True):
        if digit == 0:
            skipped = bool(written)
            continue
        if skipped:
            written, skipped = written + "零", False
        written += _CHINESE_DIGITS[digit] + unit
    return written[1:] if written.startswith("一十") else written


def _chinese_numerals() -> dict[str, int]:
    """Every Chinese numeral from 0 to 9999 in its standard form, and with 两 for a 2 before 千 or
    百 (两千, 两百) and for 2 alone."""
    numerals: dict[str, int] = {}
    for value in range(10_000):
        forms = {_standard_chinese_numeral(value)}
        for unit in ("千", "百"):
            forms |= {form.replace(f"二{unit}", f"两{unit}") for form in forms}
        if value == 2:
            forms.add("两")
        numerals.update(dict.fromkeys(forms, value))
    return numerals


CHINESE_NUMERALS: Final[dict[str, int]] = _chinese_numerals()
"""The Chinese numerals `_number` reads, each with its value. A form outside it -- 一百八, 一万,
十十 -- is no count this reader reads."""

_ENGLISH_UNITS: Final[tuple[str, ...]] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)

_ENGLISH_TENS: Final[tuple[str, ...]] = (
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def _english_numbers() -> dict[str, int]:
    """Every English number from zero to ninety-nine, each compound hyphenated: twenty-one."""
    numbers = {word: value for value, word in enumerate(_ENGLISH_UNITS)}
    for tens_value, tens in enumerate(_ENGLISH_TENS, start=2):
        numbers[tens] = tens_value * 10
        for unit_value, unit in enumerate(_ENGLISH_UNITS[1:10], start=1):
            numbers[f"{tens}-{unit}"] = tens_value * 10 + unit_value
    return numbers


_ENGLISH_NUMBERS: Final[dict[str, int]] = _english_numbers()

DIGITS: Final[str] = r"[0-9]+"

_NUMERAL_CHARACTERS: Final[str] = "零一二两三四五六七八九十百千"

NUMBER: Final[str] = (
    r"(?:(?<![0-9])(?<![0-9]\.)(?<!第)(?<!第\s)[0-9]+"
    rf"|(?<![{_NUMERAL_CHARACTERS}万同第之某统单])[{_NUMERAL_CHARACTERS}]+"
    r"|唯一"
    r"|(?<![A-Za-z])(?<![A-Za-z]-)(?<!hundred )(?<!hundred and )(?<!thousand )"
    r"(?<!thousand and )(?:"
    + "|".join(sorted(_ENGLISH_NUMBERS, key=lambda word: (-len(word), word)))
    + r")(?![A-Za-z]))"
)
"""A count as the documents write it -- digits, a Chinese numeral, 唯一, or an English number --
matched whole. A match never starts inside a longer number: digits not after a digit or after a
digit and a decimal point, a Chinese numeral not after another numeral character or 万, and an
English number not after a hyphenated word or after "hundred" or "thousand", with or without
"and". Digits are not read after 第, with or without a space between, nor a Chinese numeral
directly after 第: an ordinal, not a count. And a Chinese numeral is not read after 同, 之, 某,
统 or 单, where 一 is part of a word."""


def _number(text: str) -> int:
    """`text` as a count: ASCII digits, a numeral `CHINESE_NUMERALS` holds, 唯一, or an English
    number up to ninety-nine. Anything else raises `ValueError`, so a form this reader does not
    know fails loudly."""
    if re.fullmatch(r"[0-9]+", text):
        return int(text)
    if text == "唯一":
        return 1
    if text.lower() in _ENGLISH_NUMBERS:
        return _ENGLISH_NUMBERS[text.lower()]
    if text in CHINESE_NUMERALS:
        return CHINESE_NUMERALS[text]
    raise ValueError(f"not a count this reader reads: {text!r}")


# --- What the code holds ------------------------------------------------------------------------


def _ledger_totals() -> dict[str, int]:
    summary = json.loads(LEDGER_SUMMARY.read_text(encoding="utf-8"))
    return {**summary["totals"], **summary["status_distribution"]}


def _factor_families() -> dict[str, int]:
    counted = Counter(definition.family for definition in FACTOR_DEFINITIONS.definitions)
    return {
        "momentum": counted["momentum_reversal"],
        "volatility": counted["volatility_liquidity"],
        "value": counted["value"],
        "quality": counted["quality"],
        "growth": counted["growth"],
    }


def _datasets_whose_probe_needs_a_subject() -> int:
    """Evidence-plane Tushare datasets whose `doctor --probe` request carries a subject."""
    provider = TushareProvider(token="counted-never-sent")
    return sum(
        1
        for descriptor in TUSHARE_DATASETS
        if descriptor.serves_evidence_plane and provider.probe_subjects(descriptor.dataset)
    )


def _coverage_gate() -> int:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return int(config["tool"]["coverage"]["report"]["fail_under"])


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentedCount:
    """A count the code can answer, where it comes from, and the phrasings that state it.

    `derive` returns the value of each named group the patterns capture -- `n`, or one per part of
    a breakdown. `example` is a statement of it with `{group}` where each number goes. `stated` is
    its census: how many statements of it each document makes, and so must make.
    """

    name: str
    derive: Callable[[], dict[str, int]]
    source: str
    statements: tuple[re.Pattern[str], ...]
    example: str
    stated: dict[Path, int]


DOCUMENTED_COUNTS: Final[tuple[DocumentedCount, ...]] = (
    DocumentedCount(
        name="declared factors",
        derive=lambda: {"n": len(FACTOR_EVALUATORS)},
        source="len(panel_factors.FACTOR_EVALUATORS)",
        statements=(
            re.compile(rf"(?P<n>{NUMBER})\s*个声明因子"),
            re.compile(rf"(?P<n>{NUMBER})\s*个因子的\s*handle"),
            re.compile(rf"(?P<n>{NUMBER})\s+declared\s+factors", re.IGNORECASE),
        ),
        example="{n} 个声明因子",
        stated={README: 2, README_EN: 1},
    ),
    DocumentedCount(
        name="declared factors by family",
        derive=_factor_families,
        source="the families of panel_factors.FACTOR_DEFINITIONS",
        statements=(
            re.compile(
                rf"动量反转\s*(?P<momentum>{DIGITS})\s*/\s*波动流动性\s*(?P<volatility>{DIGITS})"
                rf"\s*/\s*价值\s*(?P<value>{DIGITS})\s*/\s*质量\s*(?P<quality>{DIGITS})"
                rf"\s*/\s*成长\s*(?P<growth>{DIGITS})"
            ),
            re.compile(
                rf"(?P<momentum>{DIGITS})\s+momentum/reversal,\s*"
                rf"(?P<volatility>{DIGITS})\s+volatility/liquidity,\s*(?P<value>{DIGITS})\s+value,"
                rf"\s*(?P<quality>{DIGITS})\s+quality,\s*(?P<growth>{DIGITS})\s+growth"
            ),
        ),
        example=(
            "（动量反转 {momentum} / 波动流动性 {volatility} / 价值 {value} / 质量 {quality} / "
            "成长 {growth}）"
        ),
        stated={README: 1, README_EN: 1},
    ),
    DocumentedCount(
        name="cross-sectional transforms",
        derive=lambda: {"n": len(FACTOR_TRANSFORMS.transform_ids)},
        source="panel_factors.FACTOR_TRANSFORMS.transform_ids",
        statements=(
            re.compile(rf"(?P<n>{NUMBER})\s*个截面变换"),
            re.compile(rf"(?P<n>{NUMBER})\s+cross-sectional\s+transforms?", re.IGNORECASE),
        ),
        example="{n} 个截面变换",
        stated={README: 1, README_EN: 1},
    ),
    DocumentedCount(
        name="industry-and-size neutralisations",
        derive=lambda: {"n": len(FACTOR_NEUTRALIZATIONS.neutralization_ids)},
        source="panel_neutralization.FACTOR_NEUTRALIZATIONS.neutralization_ids",
        statements=(
            re.compile(rf"(?P<n>{NUMBER})\s*个行业与市值中性化"),
            re.compile(
                rf"(?P<n>{NUMBER})\s+industry-and-size\s+neutrali[sz]ations?", re.IGNORECASE
            ),
        ),
        example="{n} 个行业与市值中性化",
        stated={README: 1, README_EN: 1},
    ),
    DocumentedCount(
        name="attribution verdicts",
        derive=lambda: {"n": len(get_args(AttributionVerdict))},
        source="backtest.factor_experiment.AttributionVerdict",
        statements=(
            re.compile(rf"(?P<n>{NUMBER})\s*个判决"),
            re.compile(rf"the\s+(?P<n>{NUMBER})\s+verdicts", re.IGNORECASE),
        ),
        example="{n} 个判决",
        stated={README: 2, README_EN: 1},
    ),
    DocumentedCount(
        name="Tushare datasets",
        derive=lambda: {"n": len(TUSHARE_DATASETS), "m": len(TUSHARE_DATASETS)},
        source="len(providers.tushare.TUSHARE_DATASETS)",
        statements=(
            re.compile(rf"Tushare\s*现为\s*(?P<n>{DIGITS})\s*/\s*(?P<m>{DIGITS})"),
            re.compile(rf"面板数据平面（\s*(?P<n>{NUMBER})\s*个数据集"),
            re.compile(rf"声明的全部\s*(?P<n>{NUMBER})\s*个数据集"),
        ),
        example="（Tushare 现为 {n}/{m}）",
        stated={README: 3},
    ),
    DocumentedCount(
        name="panel-only Tushare datasets",
        derive=lambda: {"n": sum(1 for d in TUSHARE_DATASETS if not d.serves_evidence_plane)},
        source="the TUSHARE_DATASETS descriptors with serves_evidence_plane=False",
        statements=(re.compile(rf"面板专供的\s*(?P<n>{NUMBER})\s*个"),),
        example="面板专供的{n}个走 fetch_panel",
        stated={README: 1},
    ),
    DocumentedCount(
        name="Tushare datasets whose probe needs a subject",
        derive=lambda: {"n": _datasets_whose_probe_needs_a_subject()},
        source="TushareProvider.probe_subjects over the evidence-plane TUSHARE_DATASETS",
        statements=(re.compile(rf"报告期年\s*的\s*(?P<n>{NUMBER})\s*个由\s*provider"),),
        example="需要 ts_code/报告期年的{n}个由 provider 自己给出最小主体",
        stated={README: 1},
    ),
    DocumentedCount(
        name="panel build targets",
        derive=lambda: {"n": len(PANEL_BUILD_TARGETS)},
        source="len(cli.PANEL_BUILD_TARGETS)",
        statements=(
            re.compile(rf"(?P<n>{NUMBER})\s*个目标[，,]\s*按依赖序"),
            re.compile(rf"(?P<n>{NUMBER})\s*个目标覆盖"),
        ),
        example="{n}个目标，按依赖序执行",
        stated={README: 2},
    ),
    DocumentedCount(
        name="named model-view boundaries",
        derive=lambda: {"n": len(KNOWN_MODEL_VIEW_LIMITATIONS)},
        source="len(model_view.KNOWN_MODEL_VIEW_LIMITATIONS), every model answer's limitations",
        statements=(re.compile(rf"(?P<n>{NUMBER})\s+named\s+boundaries", re.IGNORECASE),),
        example="the {n} named boundaries",
        stated={README_EN: 1},
    ),
    DocumentedCount(
        name="features in the ledger",
        derive=lambda: {"n": _ledger_totals()["all_features"]},
        source="summary.json totals.all_features",
        statements=(
            re.compile(rf"对账\s*(?P<n>{NUMBER})\s*项能力"),
            re.compile(rf"(?P<n>{NUMBER})\s*项功能台账"),
        ),
        example="对账 {n} 项能力",
        stated={MARKETING: 2},
    ),
    DocumentedCount(
        name="features the ledger counts complete",
        derive=lambda: {"n": _ledger_totals()["true_completed_features"]},
        source="summary.json totals.true_completed_features",
        statements=(re.compile(rf"真实完成\s*(?P<n>{NUMBER})\s*项"),),
        example="真实完成 {n} 项",
        stated={MARKETING: 1},
    ),
    DocumentedCount(
        name="features the ledger excludes",
        derive=lambda: {"n": _ledger_totals()["EXCLUDED"]},
        source="summary.json status_distribution.EXCLUDED",
        statements=(re.compile(rf"(?P<n>{NUMBER})\s*项因{_NOT_END}{{0,30}}被明确排除"),),
        example="{n} 项因实盘被明确排除",
        stated={MARKETING: 1},
    ),
    DocumentedCount(
        name="features the ledger defers",
        derive=lambda: {"n": _ledger_totals()["DEFERRED"]},
        source="summary.json status_distribution.DEFERRED",
        statements=(
            re.compile(rf"是(?P<n>{NUMBER})\s*Deferred"),
            re.compile(rf"(?P<n>{NUMBER})的" + r"\N{LEFT DOUBLE QUOTATION MARK}暂缓"),
        ),
        example="Flow Builder 是{n} Deferred",
        stated={MARKETING: 2},
    ),
    DocumentedCount(
        name="unreviewed ledger rows",
        derive=lambda: {"n": _ledger_totals()["unreviewed"]},
        source="summary.json totals.unreviewed",
        statements=(
            re.compile(r"UNREVIEWED=(?P<n>[0-9]+)"),
            re.compile(rf"未审计和未知均为\s*(?P<n>{NUMBER})"),
        ),
        example="`UNREVIEWED={n}`",
        stated={README: 1, WHY_OPENALPHA: 2, MARKETING: 1},
    ),
    DocumentedCount(
        name="unknown ledger rows",
        derive=lambda: {"n": _ledger_totals()["unknown"]},
        source="summary.json totals.unknown",
        statements=(
            re.compile(r"UNKNOWN=(?P<n>[0-9]+)"),
            re.compile(rf"未审计和未知均为\s*(?P<n>{NUMBER})"),
        ),
        example="`UNKNOWN={n}`",
        stated={README: 1, WHY_OPENALPHA: 2, MARKETING: 1},
    ),
    DocumentedCount(
        name="the coverage gate",
        derive=lambda: {"n": _coverage_gate()},
        source="pyproject.toml [tool.coverage.report] fail_under",
        statements=(
            re.compile(r"覆盖率超过\s*(?P<n>[0-9]+)\s*%\s*门槛"),
            re.compile(r"--cov-fail-under=(?P<n>[0-9]+)"),
        ),
        example="覆盖率超过 {n}% 门槛",
        stated={README: 1, README_EN: 1, MARKETING: 1},
    ),
)
"""Every count the four documents state that the code or the ledger can answer."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UnderivedCount:
    """A count nothing this test can read derives, so no document may state it.

    `source`, where `reason` speaks for one file, is that file: every number `reason` cites must
    occur in it (`test_a_reason_cites_only_figures_its_source_holds`).
    """

    name: str
    pattern: re.Pattern[str]
    reason: str
    example: str
    source: Path | None = None


UNDERIVED_COUNTS: Final[tuple[UnderivedCount, ...]] = (
    UnderivedCount(
        name="how many files the publication gate scans",
        pattern=re.compile(r"[0-9]+\s*个公开文件"),
        reason=(
            "scripts/verify_publication.py scans every file git would publish, and that number "
            "moves with every commit; section 069's hook said 178."
        ),
        example="见过连 178 个公开文件都逐项扫描的吗\N{FULLWIDTH QUESTION MARK}",
    ),
    UnderivedCount(
        name="the coverage a run measured",
        pattern=re.compile(
            r"[0-9]+(?:\.[0-9]+)?\s*%\s*覆盖率|coverage of\s+[0-9]+(?:\.[0-9]+)?\s*%", re.IGNORECASE
        ),
        reason=(
            "A run's coverage moves with every change; the gate it has to clear is fail_under, "
            "which DOCUMENTED_COUNTS holds. Section 084's hook said 87%."
        ),
        example="87% 覆盖率只是表面。",
    ),
    UnderivedCount(
        name="how many issues the roadmap lists",
        pattern=re.compile(r"[0-9]+\s*个\s*issues?", re.IGNORECASE),
        reason=(
            "README.md said 110. The roadmap gives no one figure to derive it from: it records its "
            "own total moving from 110 to 113, and its overview table, which totals 193, and its "
            "issue tables count the same issues and disagree."
        ),
        example="七个阶段 110 个 issue。",
        source=ROADMAP,
    ),
)
"""Counts the documents stopped stating instead of this test guarding a copy of them."""


# --- The checks ---------------------------------------------------------------------------------


def _guarded_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GUARDED_FILES}


def _count_statements(documents: dict[Path, str]) -> tuple[list[str], Counter[tuple[str, Path]]]:
    """Every stated count in `documents` that differs from the code, and how many statements of
    each count each document makes."""
    derived = {count.name: count.derive() for count in DOCUMENTED_COUNTS}
    problems: list[str] = []
    found: Counter[tuple[str, Path]] = Counter()
    for path, document in documents.items():
        for clause in clauses(document):
            for count in DOCUMENTED_COUNTS:
                for pattern in count.statements:
                    for match in pattern.finditer(clause.text):
                        found[(count.name, path)] += 1
                        for group, text in match.groupdict().items():
                            if text is None:
                                continue
                            where = f"{path.relative_to(ROOT)}:{clause.line}"
                            try:
                                stated = _number(text)
                            except ValueError:
                                problems.append(
                                    f"{where} states {count.name} as unreadable {text!r}"
                                )
                                continue
                            if stated != derived[count.name][group]:
                                problems.append(
                                    f"{where} states {count.name} as {stated} ({text!r}), but "
                                    f"{count.source} gives {derived[count.name][group]}: "
                                    f"{clause.text!r}"
                                )
    return problems, found


def _underived_statements(documents: dict[Path, str]) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{clause.line} states {count.name}: {clause.text!r} -- "
        f"{count.reason}"
        for path, document in documents.items()
        for clause in clauses(document)
        for count in UNDERIVED_COUNTS
        if count.pattern.search(clause.text)
    ]


def _count_failures(documents: dict[Path, str]) -> list[str]:
    """Every stated count in `documents` that differs from the code, every count stated in none
    of them, and every document that makes more or fewer statements of a count than its census."""
    problems, found = _count_statements(documents)
    stated_nowhere = [
        f"no statement of {count.name} found: the wording moved out of its patterns' reach -- "
        "update the patterns -- or the statement was removed, and its entry goes with it"
        for count in DOCUMENTED_COUNTS
        if not any(found[(count.name, path)] for path in documents)
    ]
    off_census = [
        f"{path.relative_to(ROOT)} makes {found[(count.name, path)]} statements of {count.name} "
        f"and its census says {count.stated.get(path, 0)}: a statement was reworded out of its "
        "patterns' reach -- update the patterns -- or was added or removed, and the census moves "
        "with it"
        for count in DOCUMENTED_COUNTS
        for path in documents
        if found[(count.name, path)] != count.stated.get(path, 0)
    ]
    return problems + stated_nowhere + off_census


GENERATORS: Final[tuple[Path, ...]] = (
    ROOT / "scripts" / "generate_brain_diagrams.py",
    ROOT / "scripts" / "generate_api_relationship_diagrams.py",
)

DRAWN_COUNT: Final[re.Pattern[str]] = re.compile(
    r"(?P<n>[一二两三四五六七八九十]|\d+)\s*(?:级|重|类|条|项)"
)
"""A count a drawing states about the things beside it: 五级, 三重, 四类, 五条, 两项."""

COUNTS_THAT_NAME_NO_LIST: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "五类失败分类": "ProviderFailure's five kinds (providers/base.py), not a list drawn here",
        "四类入口": "REST, SDK, CLI and the web workbench, which this function draws as cards "
        "rather than as one list",
        "两项条款": "the two rule terms a validation claims (backtest/validation.py:313-346)",
    }
)
"""Counts a generator states that name something other than a list it draws, and what they name.

The rule below reads a function's own list literals, so a true count of something else -- the
provider failure kinds, the four faces, the batch ceiling -- has no list to match and is
declared here instead. An entry is a statement about one phrase; a phrase that starts matching
a list again, or stops being drawn, is reported by the test that follows.
"""


def _drawn_counts(path: Path) -> list[tuple[int, str, int, set[int]]]:
    """Every count a generator's functions state, with the lengths of that function's lists."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str, int, set[int]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        lengths = {
            len(child.value.elts)
            for child in ast.walk(node)
            if isinstance(child, ast.Assign) and isinstance(child.value, ast.List | ast.Tuple)
        }
        if not lengths:
            continue
        for child in ast.walk(node):
            if not (isinstance(child, ast.Constant) and isinstance(child.value, str)):
                continue
            for match in DRAWN_COUNT.finditer(child.value):
                phrase = child.value[match.start() : match.end() + 4].strip()
                found.append((child.lineno, phrase, _number(match.group("n")), lengths))
    return found


def test_a_count_a_diagram_states_is_the_length_of_the_list_it_draws() -> None:
    """`brain-01` said 五级专业流水线 over a `stages` list holding four, and the four were drawn.

    A count in a drawing is a claim like any other, and this is the one kind no other guard
    reads: the retired-claims guard takes a generator one string literal at a time, and the
    counts above read the documents rather than the drawings. The rule is the cheapest one that
    holds: a count stated inside a function that builds a list must be one of that function's
    list lengths, or be declared in `COUNTS_THAT_NAME_NO_LIST` with what it does name.
    """
    problems: list[str] = []
    for path in GENERATORS:
        for line, phrase, stated, lengths in _drawn_counts(path):
            if any(phrase.startswith(known) for known in COUNTS_THAT_NAME_NO_LIST):
                continue
            if stated not in lengths:
                problems.append(
                    f"{path.relative_to(ROOT)}:{line} draws {phrase!r}, and the lists this "
                    f"function builds hold {sorted(lengths)}"
                )

    assert not problems, "\n".join(problems)


def test_every_declared_diagram_count_is_still_drawn() -> None:
    """A declaration outlives its phrase the moment the phrase is reworded; this says so."""
    drawn = {phrase for path in GENERATORS for _, phrase, _, _ in _drawn_counts(path)}
    unused = [
        known
        for known in COUNTS_THAT_NAME_NO_LIST
        if not any(phrase.startswith(known) for phrase in drawn)
    ]

    assert not unused, f"{unused} is declared here and drawn nowhere"


def test_every_count_the_documents_state_is_the_one_the_code_holds() -> None:
    """Each stated count equals its derived value, and each count is stated at least once."""
    failures = _count_failures(_guarded_documents())
    assert not failures, (
        "\n".join(failures)
        + "\nUpdate the document to the code's count rather than the other way round."
    )


def test_a_count_stated_nowhere_is_reported() -> None:
    """Documents stating none of the counts fail on every one of them, so a reader gone blind or
    a statement reworded out of reach cannot pass as a clean read."""
    failures = _count_failures({README: "OpenAlpha CN 面向中国 A 股。\n"})
    silent = [
        count.name
        for count in DOCUMENTED_COUNTS
        if not any(f"no statement of {count.name} found" in failure for failure in failures)
    ]
    assert not silent, f"a count stated nowhere went unreported: {silent}"


def test_a_statement_reworded_out_of_reach_is_reported() -> None:
    """One statement reworded out of its patterns' reach fails while the count is still stated
    elsewhere, and so does one added.

    The review of `D13` (its m-11) changed README.md's 21 个声明因子 to 21 个已声明因子: README.md's
    21 个因子的 handle and README.en.md still stated the count, so the check that each count is
    stated somewhere passed. Each count's `stated` census holds how many statements each document
    makes, so the one fewer is reported, and one more too, so the census cannot fall behind the
    documents and let a later reword pass. The census counts statements, not their places: the
    last case, a reword and an addition in the same document, passes, as the module docstring says.
    """
    documents = _guarded_documents()
    statement = f"{len(FACTOR_EVALUATORS)} 个声明因子"
    assert documents[README].count(statement) == 1, f"README.md no longer states {statement} once"
    reworded = documents[README].replace(statement, statement.replace("个声明", "个已声明"), 1)
    edits = {
        "a statement reworded out of reach": reworded,
        "a statement added": f"{documents[README]}\n\n{statement}。\n",
    }
    unreported = [
        label
        for label, text in edits.items()
        if not any(
            "README.md" in failure and "declared factors" in failure
            for failure in _count_failures({**documents, README: text})
        )
    ]
    both = _count_failures({**documents, README: f"{reworded}\n\n{statement}。\n"})
    assert not unreported and not both, (
        f"unreported: {unreported}; a reword with an addition in the same document reported: {both}"
    )
    assert all(count.stated for count in DOCUMENTED_COUNTS), "a count has an empty census"
    assert {path for count in DOCUMENTED_COUNTS for path in count.stated} <= set(GUARDED_FILES)


def test_no_document_states_a_count_nothing_derives() -> None:
    """A count no code answers is written without its number, not guarded as a copy."""
    statements = _underived_statements(_guarded_documents())
    assert not statements, "\n".join(statements)


FIGURE: Final[re.Pattern[str]] = re.compile(r"(?<![0-9])(?<![0-9]\.)[0-9]+(?![0-9])(?!\.[0-9])")
"""A whole number standing alone: not part of a decimal such as 51.36, and not cut short by a
sentence's full stop."""


def _uncited_figures(count: UnderivedCount) -> list[str]:
    """The numbers `count.reason` cites that its `source` does not contain, standing alone."""
    if count.source is None:
        return []
    text = count.source.read_text(encoding="utf-8")
    return [
        figure
        for figure in FIGURE.findall(count.reason)
        if re.search(rf"(?<![0-9]){figure}(?![0-9])", text) is None
    ]


def test_a_reason_cites_only_figures_its_source_holds() -> None:
    """A reason that speaks for a file may cite only numbers that file contains.

    The roadmap entry once gave README.md's 110 as standing "where the phases sum to 119", and the
    roadmap holds no 119 at all (the review of `D13`, M2). A planted reason citing a number no
    source holds, at the end of its sentence as 119 was, is reported, so the check cannot pass by
    reading nothing.
    """
    sourced = [count for count in UNDERIVED_COUNTS if count.source is not None]
    assert sourced, "no underived count names the file its reason speaks for"
    planted = replace(sourced[0], reason="the phases sum to 9999999.")
    assert _uncited_figures(planted) == ["9999999"], "a figure its source does not hold passed"
    uncited = {count.name: figures for count in sourced if (figures := _uncited_figures(count))}
    assert not uncited, f"a reason cites figures its source does not hold: {uncited}"


def test_the_number_reader_reads_what_its_docstring_says() -> None:
    """Each form the reader claims is read to its value, forms it does not claim raise, and the
    一 of 同一 is no count: README.md's 同一个判决 was read as one verdict before `NUMBER` refused
    a numeral after 同."""
    problems, found = _count_statements({README: "同一个字面输入在三个面上必须得到同一个判决。\n"})
    assert not problems and not found, f"a word read as a count: {problems} {sorted(found)}"
    unreadable, _ = _count_statements({README: "十十个目标，按依赖序执行。\n"})
    assert any("unreadable" in problem for problem in unreadable), (
        f"a numeral the reader cannot read was not reported: {unreadable}"
    )
    read = {
        "21": 21,
        "四": 4,
        "两": 2,
        "十": 10,
        "十三": 13,
        "十四": 14,
        "二十一": 21,
        "一百": 100,
        "一百零五": 105,
        "一百八十五": 185,
        "两千": 2000,
        "nine": 9,
        "Sixteen": 16,
        "twenty-one": 21,
        "Ninety-Nine": 99,
        "唯一": 1,
    }
    misread = {text: _number(text) for text, value in read.items() if _number(text) != value}
    accepted: list[str] = []
    for text in (
        "十十",
        "二十二十",
        "一百八",
        "一万",
        "twenty one",
        "one hundred",
        "\N{FULLWIDTH DIGIT ONE}6",
    ):
        try:
            _number(text)
        except ValueError:
            continue
        accepted.append(text)
    assert not misread and not accepted, f"misread: {misread}; accepted: {accepted}"


def _read_before(unit: str, text: str) -> list[int | str]:
    """Each number `NUMBER` captures directly before `unit` in `text`, read by `_number`, or
    "unreadable" where `_number` raises."""
    values: list[int | str] = []
    for match in re.finditer(rf"(?P<n>{NUMBER})\s*{unit}", text, re.IGNORECASE):
        try:
            values.append(_number(match["n"]))
        except ValueError:
            values.append("unreadable")
    return values


NUMERALS_IN_CONTEXT: Final[dict[tuple[str, str], list[int | str]]] = {
    ("项功能台账", "OpenAlpha CN 共有一百八十五项功能台账。"): [185],
    ("项功能台账", "两千项功能台账。"): [2000],
    ("declared factors", "The panel serves twenty-one declared factors."): [21],
    ("个判决", "第 5 个判决是最后一个。"): [],
    ("个判决", "第5个判决是最后一个。"): [],
    ("个判决", "第 15 个判决是最后一个。"): [],
    ("个声明因子", "第二十一个声明因子是最后一个。"): [],
    ("个判决", "同一个判决。"): [],
    ("个判决", "它平均给出 1.5 个判决。"): [],
    ("项功能台账", "对账一万零五项功能台账。"): [],
    ("declared factors", "It serves one hundred five declared factors."): [],
    ("declared factors", "It serves one hundred and five declared factors."): [],
    ("declared factors", "It serves a hundred and twenty-one declared factors."): [],
    ("declared factors", "It serves one thousand five declared factors."): [],
    ("declared factors", "It serves one thousand and five declared factors."): [],
    ("declared factors", "Its top-ten declared factors lead the ranking."): [],
    ("项功能台账", "一百八项功能台账。"): ["unreadable"],
}
"""A unit, a sentence, and the numbers the reader must take from directly before that unit."""


def test_a_numeral_is_read_whole_or_not_at_all() -> None:
    """A count is read from the first character of its numeral, never from inside a longer one.

    The review of `D13` (its m-11) measured 一百八十五项 read as 85, twenty-one as 1 and 第 5 个
    as a count of five, and 第二十一个 was read as eleven the same way. A number is now read whole;
    an ordinal, a decimal, a numeral with 万, a number after "hundred" or "thousand" or inside a
    hyphenated word (top-ten), and an English number above ninety-nine are not read at all; and a
    numeral in no standard form is read and reported as unreadable.
    """
    wrong = {
        case: read
        for case, expected in NUMERALS_IN_CONTEXT.items()
        if (read := _read_before(*case)) != expected
    }
    assert not wrong, f"read otherwise than NUMERALS_IN_CONTEXT states: {wrong}"


ROADMAP_OVERVIEW_TOTAL: Final[re.Pattern[str]] = re.compile(
    r"^\|[^|\n]*\|\s*\*\*合计\*\*\s*\|\s*\*\*(?P<total>[0-9]+)\*\*\s*\|", re.MULTILINE
)
"""The 合计 row of the roadmap's overview table, with its issue total in bold."""

ROADMAP_ISSUE_ROW: Final[re.Pattern[str]] = re.compile(
    r"^\| `V2-[A-Z0-9]+-[0-9]+` \|", re.MULTILINE
)
"""A row of one of the roadmap's issue tables: an issue id in backticks as its first cell."""


def test_the_roadmaps_overview_total_and_its_issue_rows_disagree() -> None:
    """The roadmap entry's reason, measured rather than remembered.

    The reason used to say that the overview table and the issue tables "count different things".
    The review of `D13` (its m-11) found that they count the same issues and disagree -- the
    overview table's 合计 row says 193, and the issue tables hold 254 rows at `20fec55` -- and the
    reason now says so. It cites the total, so the total is read here and the rows are counted.
    The day the two agree, the roadmap gives one figure to derive the count from: this fails, and
    the entry has to be re-read.
    """
    entry = next(count for count in UNDERIVED_COUNTS if count.source == ROADMAP)
    roadmap = ROADMAP.read_text(encoding="utf-8")
    totals = [int(match["total"]) for match in ROADMAP_OVERVIEW_TOTAL.finditer(roadmap)]
    rows = len(ROADMAP_ISSUE_ROW.findall(roadmap))
    assert len(totals) == 1, f"the overview table's 合计 row was read {len(totals)} times"
    assert str(totals[0]) in FIGURE.findall(entry.reason), (
        f"the roadmap entry's reason does not cite the overview total {totals[0]}: {entry.reason}"
    )
    assert rows and rows != totals[0], (
        f"the roadmap's issue tables hold {rows} rows and its overview table totals {totals[0]}"
    )


def test_a_stale_count_written_into_the_readme_is_reported() -> None:
    """Each count's example, stated one higher than the code has it and appended to `README.md`,
    must be reported under that count; each underived example must be reported too. Today's
    documents state every count correctly, so the test above cannot show either check reads."""
    documents = _guarded_documents()
    unreported: list[str] = []
    for count in DOCUMENTED_COUNTS:
        wrong = {group: value + 1 for group, value in count.derive().items()}
        planted = f"{documents[README]}\n\n{count.example.format(**wrong)}\n"
        problems, _ = _count_statements({**documents, README: planted})
        if not any(f"states {count.name} as " in problem for problem in problems):
            unreported.append(count.name)
    for underived in UNDERIVED_COUNTS:
        planted = f"{documents[README]}\n\n{underived.example}\n"
        if not _underived_statements({README: planted}):
            unreported.append(underived.name)
    assert not unreported, f"a stale count written into README.md went unreported: {unreported}"


def test_the_count_guards_stated_blind_spots_are_real() -> None:
    """Each limit the module docstring states, measured: these paraphrases are read as nothing.

    A change that starts reading one of them has closed a stated blind spot: delete the row and
    the sentence in the docstring together. The census's own limit, a reword and an addition in
    one document, is measured by `test_a_statement_reworded_out_of_reach_is_reported`.
    """
    paraphrases = {
        "a count in words no pattern names": "这个 build 声明了二十一个因子。",
        "an English count in other words": "Above the panel sits a library of 21 factors.",
        "a numeral written with 万": "OpenAlpha CN 对账一万零五项能力。",
        "an English number above ninety-nine": (
            "The panel serves one hundred and five declared factors."
        ),
        "an underived count in Chinese numerals": (
            "见过连一百七十八个公开文件都扫描的吗\N{FULLWIDTH QUESTION MARK}"
        ),
    }
    read = {
        label: text
        for label, text in paraphrases.items()
        if _count_statements({README: text})[1] or _underived_statements({README: text})
    }
    assert not read, f"a stated blind spot is now read: {read} -- update the docstring with it"
