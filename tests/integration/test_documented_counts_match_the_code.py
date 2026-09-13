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
pattern, and each captured number -- ASCII digits, a Chinese numeral up to 99 (四, 十三, 二十一),
an English number word up to twenty, or 唯一 for one -- must equal the derived value. Each count
must be found at least once, so a reword that moves a statement out of its patterns' reach fails
instead of passing over nothing; a statement removed on purpose takes its entry with it.

**Counts nothing derives are not stated.** How many files the publication gate scans, what a run's
coverage came to and how many issues the roadmap lists change with the repository and are answered
by nothing this test can read, so the documents stop stating them rather than this test guarding
a copy: `UNDERIVED_COUNTS` fails on any clause that states one.

**What it cannot see.** It reads these phrasings only. A count stated in other words --
"声明了二十一个因子", "a library of 21 factors" -- is not read, nor is one in the embedded
diagrams or in any other document, and a Chinese numeral above 99 is not parsed.
`UNDERIVED_COUNTS` catches its patterns only: "一百七十八个公开文件" passes.
`test_the_count_guards_stated_blind_spots_are_real` measures one paraphrase of each kind. The
other direction: a Chinese numeral is read wherever a pattern's unit follows it, so the 一 of a
word such as 同一 would read as one. `NUMBER` refuses a numeral after 同, 第, 之, 某, 统 and 单 --
README.md's 同一个判决, "the same verdict", was read as a count of one verdict until it did --
and no other such word.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
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

_CHINESE_DIGITS: Final[dict[str, int]] = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_ENGLISH_NUMBERS: Final[dict[str, int]] = {
    word: value
    for value, word in enumerate(
        [
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
            "twenty",
        ]
    )
}

DIGITS: Final[str] = r"[0-9]+"

NUMBER: Final[str] = (
    r"(?:[0-9]+|(?<![同第之某统单])[零一二两三四五六七八九十]+|唯一|(?<![A-Za-z])(?:"
    + "|".join(_ENGLISH_NUMBERS)
    + r")(?![A-Za-z]))"
)
"""A count as the documents write it: digits, a Chinese numeral, 唯一, or an English word."""


def _number(text: str) -> int:
    """`text` as a count: ASCII digits, a Chinese numeral up to 99, 唯一, or an English word up to
    twenty. Anything else raises `ValueError`, so a form this reader does not know fails loudly."""
    if re.fullmatch(r"[0-9]+", text):
        return int(text)
    if text == "唯一":
        return 1
    if text.lower() in _ENGLISH_NUMBERS:
        return _ENGLISH_NUMBERS[text.lower()]
    tens, ten, ones = text.partition("十")
    if not ten:
        if len(text) == 1 and text in _CHINESE_DIGITS:
            return _CHINESE_DIGITS[text]
        raise ValueError(f"not a count this reader reads: {text!r}")
    if len(tens) > 1 or len(ones) > 1 or any(c not in _CHINESE_DIGITS for c in tens + ones):
        raise ValueError(f"not a count this reader reads: {text!r}")
    return (_CHINESE_DIGITS[tens] if tens else 1) * 10 + (_CHINESE_DIGITS[ones] if ones else 0)


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
    a breakdown. `example` is a statement of it with `{group}` where each number goes.
    """

    name: str
    derive: Callable[[], dict[str, int]]
    source: str
    statements: tuple[re.Pattern[str], ...]
    example: str


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
    ),
    DocumentedCount(
        name="panel-only Tushare datasets",
        derive=lambda: {"n": sum(1 for d in TUSHARE_DATASETS if not d.serves_evidence_plane)},
        source="the TUSHARE_DATASETS descriptors with serves_evidence_plane=False",
        statements=(re.compile(rf"面板专供的\s*(?P<n>{NUMBER})\s*个"),),
        example="面板专供的{n}个走 fetch_panel",
    ),
    DocumentedCount(
        name="Tushare datasets whose probe needs a subject",
        derive=lambda: {"n": _datasets_whose_probe_needs_a_subject()},
        source="TushareProvider.probe_subjects over the evidence-plane TUSHARE_DATASETS",
        statements=(re.compile(rf"报告期年\s*的\s*(?P<n>{NUMBER})\s*个由\s*provider"),),
        example="需要 ts_code/报告期年的{n}个由 provider 自己给出最小主体",
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
    ),
    DocumentedCount(
        name="named model-view boundaries",
        derive=lambda: {"n": len(KNOWN_MODEL_VIEW_LIMITATIONS)},
        source="len(model_view.KNOWN_MODEL_VIEW_LIMITATIONS), every model answer's limitations",
        statements=(re.compile(rf"(?P<n>{NUMBER})\s+named\s+boundaries", re.IGNORECASE),),
        example="the {n} named boundaries",
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
    ),
    DocumentedCount(
        name="features the ledger counts complete",
        derive=lambda: {"n": _ledger_totals()["true_completed_features"]},
        source="summary.json totals.true_completed_features",
        statements=(re.compile(rf"真实完成\s*(?P<n>{NUMBER})\s*项"),),
        example="真实完成 {n} 项",
    ),
    DocumentedCount(
        name="features the ledger excludes",
        derive=lambda: {"n": _ledger_totals()["EXCLUDED"]},
        source="summary.json status_distribution.EXCLUDED",
        statements=(re.compile(rf"(?P<n>{NUMBER})\s*项因{_NOT_END}{{0,30}}被明确排除"),),
        example="{n} 项因实盘被明确排除",
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
            "own total moving from 110 to 113, and its overview table and its issue tables count "
            "different things."
        ),
        example="七个阶段 110 个 issue。",
        source=ROADMAP,
    ),
)
"""Counts the documents stopped stating instead of this test guarding a copy of them."""


# --- The checks ---------------------------------------------------------------------------------


def _guarded_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GUARDED_FILES}


def _count_statements(documents: dict[Path, str]) -> tuple[list[str], set[str]]:
    """Every stated count in `documents` that differs from the code, and the counts found."""
    derived = {count.name: count.derive() for count in DOCUMENTED_COUNTS}
    problems: list[str] = []
    found: set[str] = set()
    for path, document in documents.items():
        for clause in clauses(document):
            for count in DOCUMENTED_COUNTS:
                for pattern in count.statements:
                    for match in pattern.finditer(clause.text):
                        found.add(count.name)
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
    """Every stated count in `documents` that differs from the code, and every count stated in
    none of them."""
    problems, found = _count_statements(documents)
    return problems + [
        f"no statement of {count.name} found: the wording moved out of its patterns' reach -- "
        "update the patterns -- or the statement was removed, and its entry goes with it"
        for count in DOCUMENTED_COUNTS
        if count.name not in found
    ]


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
        "nine": 9,
        "Sixteen": 16,
        "唯一": 1,
    }
    misread = {text: _number(text) for text, value in read.items() if _number(text) != value}
    accepted: list[str] = []
    for text in ("一百", "十十", "二十二十", "twenty-one", "\N{FULLWIDTH DIGIT ONE}6"):
        try:
            _number(text)
        except ValueError:
            continue
        accepted.append(text)
    assert not misread and not accepted, f"misread: {misread}; accepted: {accepted}"


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
    the sentence in the docstring together.
    """
    paraphrases = {
        "a count in words no pattern names": "这个 build 声明了二十一个因子。",
        "an English count in other words": "Above the panel sits a library of 21 factors.",
        "a Chinese numeral above 99": "OpenAlpha CN 对账一百八十五项能力。",
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
