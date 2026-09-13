"""No document or diagram may say the replay detects, counts or reports look-ahead violations.

**The premise**, held by `test_a_look_ahead_corpus_is_refused_before_the_replay_runs`. A replay
case whose evidence is not yet visible at its own `as_of` cannot be built:
`ReplayCase.validate_point_in_time` raises `LookAheadViolationError` when the case is
constructed, and `ReplayCorpus.load` validates every case the same way
(`backtest/replay.py:54-63`, `:87-90`). So a corpus that reaches `ReplayRunner.run` holds no
look-ahead case, and `ReplayReport.look_ahead_violations` is 0 for every such corpus; its own
docstring says it is not a detection result. What the replay does check is determinism: each
case runs twice, the second time from empty stores, and a difference is a failure.
`README.md:43` says both halves. If the premise test fails, a corpus holding a look-ahead case
can be built again, and the sentences this guard holds were written for a premise that no longer
holds: re-read them, and this guard.

**The claims.** A clause is a claim when it mentions look-ahead (`LOOK_AHEAD_WORDS`) and either
names a look-ahead violation (`VIOLATION_WORDS`) beside a count, zero, detection or report word
(`TALLY_WORDS`), or pairs a check, detection, verification, finding or report word
(`CHECK_WORDS`) with the replay (`REPLAY_WORDS`). At `4a37161` this flagged `README.md:1183`, two
clauses of marketing §059 and api-05's validation table, all rewritten. Every other look-ahead
clause and unit it read there says that look-ahead is refused or kept out, which is true, and none
of them is flagged; `README.md:43` is one.

**How it reads.** The four documents as clauses (`tests/prose_clauses.py`), and the diagrams as
units (`tests/diagram_text.py`). api-05 draws its four validation cards from one data table, a
tuple no call takes, and such a table is one unit: the replay card's "同路径回放" and its
"确定性 / 防前视报告" were read together.

**What it cannot see.** `test_the_stated_limits_are_real` measures each of these.

- A claim worded without these words: "回放能抓出偷看未来的事件" has neither 前视 nor a
  check word.
- A claim split across clauses, the replay in one and the check in the next: "冻结语料回放很
  严格。它会发现前视问题。"
- An English claim worded outside the few English words.
- Text a generator computes at run time, which `tests/diagram_text.py` does not read.

**What it refuses that is true.** The same test measures these.

- A denial in the same words fails: "回放不检测前视违规" names a violation beside a detection
  word. Say what happens instead: a corpus holding look-ahead evidence is refused at load.
- A data table is one unit, so a check word on one of its cards, with 前视 and the replay on
  another, makes a claim no single card makes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from diagram_text import diagram_units
from prose_clauses import clauses
from pydantic import ValidationError

from openalpha_cn.backtest.replay import ReplayCorpus

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

FROZEN_CORPUS: Final[Path] = ROOT / "tests" / "fixtures" / "replay" / "a-share-v1-corpus.json"


# --- The premise ------------------------------------------------------------------------------


def test_a_look_ahead_corpus_is_refused_before_the_replay_runs(tmp_path: Path) -> None:
    """The frozen corpus loads; the same corpus with one case's evidence made visible a day after
    that case's `as_of` does not, so no replay ever meets a look-ahead case to count."""
    corpus = json.loads(FROZEN_CORPUS.read_text(encoding="utf-8"))
    case = corpus["cases"][0]
    later = (datetime.fromisoformat(case["as_of"]) + timedelta(days=1)).isoformat()
    case["evidence"][0]["timeline"].update(
        available_time=later, ingested_time=later, revision_time=later
    )
    look_ahead = tmp_path / "look-ahead-corpus.json"
    look_ahead.write_text(json.dumps(corpus), encoding="utf-8")
    assert ReplayCorpus.load(FROZEN_CORPUS).cases, "the frozen corpus itself no longer loads"
    with pytest.raises(ValidationError, match="look-ahead"):
        ReplayCorpus.load(look_ahead)


# --- The claims -------------------------------------------------------------------------------

LOOK_AHEAD_WORDS: Final[tuple[str, ...]] = ("前视", "look-ahead", "lookahead", "look ahead")
"""Look-ahead, in Chinese and in three English spellings."""

VIOLATION_WORDS: Final[tuple[str, ...]] = ("前视违规", "look-ahead violation")
"""A look-ahead violation: what a count or a zero would be of."""

TALLY_WORDS: Final[tuple[str, ...]] = (
    "零",
    "统计",
    "计数",
    "检测",
    "发现",
    "报告",
    "count",
    "zero",
    "report",
    "detect",
    "found",
    "none",
)
"""The words of a count, a zero, a detection or a report of violations."""

CHECK_WORDS: Final[tuple[str, ...]] = (
    "检查",
    "检测",
    "验证",
    "发现",
    "报告",
    "check",
    "detect",
    "verif",
    "find",
    "found",
    "report",
)
"""The words of checking, detecting, verifying, finding or reporting."""

REPLAY_WORDS: Final[tuple[str, ...]] = ("回放", "冻结语料", "replay")
"""The replay, by name. English is read as a substring in any case, so "replay" covers
`ReplayCorpus` and "replays"."""

MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "look-ahead": LOOK_AHEAD_WORDS,
    "violation": VIOLATION_WORDS,
    "tally": TALLY_WORDS,
    "check": CHECK_WORDS,
    "replay": REPLAY_WORDS,
}
"""Every word the claim rule reads, by family, matched as case-insensitive substrings.

An English word is a stem, so "counts", "reported" and "verifies" are read; "found" and "find"
are both listed because neither is a stem of the other.
"""


def _holds(text: str, words: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _is_claim(text: str, markers: dict[str, tuple[str, ...]] = MARKERS) -> bool:
    """Whether one clause presents the replay as detecting, counting or reporting look-ahead."""
    if not _holds(text, markers["look-ahead"]):
        return False
    tallied = _holds(text, markers["violation"]) and _holds(text, markers["tally"])
    checked = _holds(text, markers["check"]) and _holds(text, markers["replay"])
    return tallied or checked


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


def _look_ahead_claims(texts: Iterable[tuple[str, int, str]]) -> list[str]:
    return [
        f"{label}:{line} presents the replay as detecting look-ahead: {text!r}"
        for label, line, text in texts
        if _is_claim(text)
    ]


def _documents() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in GUARDED_FILES
    }


def _diagram_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in DIAGRAM_GENERATORS
    }


def test_no_document_or_diagram_says_the_replay_detects_look_ahead() -> None:
    """No clause or diagram unit may present the replay as detecting, counting or reporting
    look-ahead violations.

    Look-ahead must still be read in both, so a reader gone blind fails rather than passes:
    `README.md` names it, and api-05 draws it.
    """
    texts = _guarded_texts(_documents(), _diagram_sources())
    named = {label for label, _, text in texts if _holds(text, LOOK_AHEAD_WORDS)}
    assert {"README.md", "scripts/generate_api_relationship_diagrams.py"} <= named, (
        f"look-ahead is read only in {sorted(named)}; README.md and api-05 named it when this "
        "was written, so the reader has gone blind"
    )
    claims = _look_ahead_claims(texts)
    assert not claims, (
        "\n".join(claims) + "\nA corpus holding look-ahead evidence is refused when it is loaded, "
        "so the replay's count is always 0. Say that; fix a diagram in its generator and "
        "regenerate it."
    )


# --- The guard's own tests --------------------------------------------------------------------

D4A37161_CLAIMS: Final[dict[str, str]] = {
    "README.md:1183": (
        "冻结语料回放再次调用同一 `run_cycle`，用于检查确定性和前视问题\N{FULLWIDTH SEMICOLON}\n"
    ),
    "marketing §059, first": (
        "OpenAlpha CN 使用 60 个交易日、300 个代表性合成事件的冻结语料验证确定性和防前视，"
        "但项目明确不把它当盈利证明。\n"
    ),
    "marketing §059, second": (
        "OpenAlpha CN 更关注测试能否重复、严重前视违规是否为零、固定输入是否得到一致结构。\n"
    ),
}
"""Three claims as they stood at `4a37161`, verbatim."""

D4A37161_REPLAY_CARD: Final[str] = (
    'columns = ((64, "同路径回放", "POST /api/v1/backtests/replay", '
    '("冻结 ReplayCorpus", "ResearchEngine.run_cycle", "确定性 / 防前视报告")),)\n'
)
"""api-05's replay card at `4a37161`: its strings verbatim, in a one-row table."""


def test_the_guard_flags_the_claims_it_was_written_for() -> None:
    """The retroactive power, held: each `4a37161` claim and the replay card are flagged."""
    missed = [
        label
        for label, text in D4A37161_CLAIMS.items()
        if not _look_ahead_claims(_guarded_texts({label: text}, {}))
    ]
    card = _look_ahead_claims(_guarded_texts({}, {"4a37161 api-05": D4A37161_REPLAY_CARD}))
    assert not missed, f"a 4a37161 claim is no longer flagged: {missed}"
    assert len(card) == 1, f"the 4a37161 replay card was read as {card}"


MARKER_SENTENCES: Final[dict[tuple[str, str], str]] = {
    ("look-ahead", "前视"): "冻结语料回放检查前视问题。",
    ("look-ahead", "look-ahead"): "The replay checks look-ahead.",
    ("look-ahead", "lookahead"): "The replay checks lookahead.",
    ("look-ahead", "look ahead"): "The replay checks for look ahead.",
    ("violation", "前视违规"): "系统统计前视违规。",
    ("violation", "look-ahead violation"): "It counts look-ahead violations.",
    ("tally", "零"): "前视违规恒为零。",
    ("tally", "统计"): "系统统计前视违规。",
    ("tally", "计数"): "前视违规有计数。",
    ("tally", "检测"): "系统检测前视违规。",
    ("tally", "发现"): "系统发现前视违规。",
    ("tally", "报告"): "系统报告前视违规。",
    ("tally", "count"): "It counts look-ahead violations.",
    ("tally", "zero"): "Look-ahead violations are zero.",
    ("tally", "report"): "It reports look-ahead violations.",
    ("tally", "detect"): "It detects look-ahead violations.",
    ("tally", "found"): "Look-ahead violations were found.",
    ("tally", "none"): "Look-ahead violations: none.",
    ("check", "检查"): "回放检查前视问题。",
    ("check", "检测"): "回放检测前视问题。",
    ("check", "验证"): "回放验证防前视。",
    ("check", "发现"): "回放发现前视问题。",
    ("check", "报告"): "回放给出防前视报告。",
    ("check", "check"): "The replay checks look-ahead.",
    ("check", "detect"): "The replay detects look-ahead.",
    ("check", "verif"): "The replay verifies look-ahead.",
    ("check", "find"): "The replay finds look-ahead.",
    ("check", "found"): "The replay found look-ahead.",
    ("check", "report"): "The replay reports look-ahead.",
    ("replay", "回放"): "回放检查前视问题。",
    ("replay", "冻结语料"): "冻结语料检查前视问题。",
    ("replay", "replay"): "The replay checks look-ahead.",
}
"""One sentence per word of `MARKERS`: a claim, and no claim once that one word is dropped from
its family."""


def _without(family: str, word: str) -> dict[str, tuple[str, ...]]:
    """`MARKERS` with one word dropped from one family."""
    return {
        name: tuple(kept for kept in words if (name, kept) != (family, word))
        for name, words in MARKERS.items()
    }


def test_every_word_the_rule_reads_is_needed() -> None:
    """Dropping any one word from `MARKERS` lets its sentence pass, so every word is pinned."""
    listed = {(family, word) for family, words in MARKERS.items() for word in words}
    assert set(MARKER_SENTENCES) == listed, (
        f"MARKER_SENTENCES covers {sorted(MARKER_SENTENCES)}; MARKERS lists {sorted(listed)}"
    )
    unpinned = [
        key
        for key, sentence in MARKER_SENTENCES.items()
        if not _is_claim(sentence) or _is_claim(sentence, _without(*key))
    ]
    assert not unpinned, f"a word of MARKERS is not needed by its sentence: {unpinned}"


def test_the_stated_limits_are_real() -> None:
    """Each limit the module docstring states, measured in both directions.

    A case in `unseen` that starts being flagged has closed a blind spot, and one in `refused`
    that starts passing has closed an over-reach: change the docstring with it.
    """
    unseen = {
        "a claim in other words": ({"a claim in other words": "回放能抓出偷看未来的事件。\n"}, {}),
        "a claim split across two clauses": (
            {"split": "冻结语料回放很严格。它会发现前视问题。\n"},
            {},
        ),
        "an English claim outside the words": (
            {"English": "The replay catches peeking ahead.\n"},
            {},
        ),
        "text computed at run time": (
            {},
            {"run time": 'card = "冻结语料回放"\nsvg.text(1, 2, f"{card} · 检查前视")\n'},
        ),
    }
    flagged_anyway = [
        label
        for label, (documents, sources) in unseen.items()
        if _look_ahead_claims(_guarded_texts(documents, sources))
    ]
    assert not flagged_anyway, f"a stated blind spot is now flagged: {flagged_anyway}"
    refused = {
        "a denial": ({"a denial": "回放不检测前视违规。\n"}, {}),
        "a check word on another card of one table": (
            {},
            {
                "one table": (
                    'columns = ((64, "同路径回放", ("两遍比对", "前视语料加载即拒")), '
                    '(394, "结果归因", ("验证结果",)))\n'
                )
            },
        ),
    }
    passed = [
        label
        for label, (documents, sources) in refused.items()
        if not _look_ahead_claims(_guarded_texts(documents, sources))
    ]
    assert not passed, f"a stated over-reach no longer happens: {passed}"
    refused_truth = "含前视证据的语料在加载时就被整体拒绝，不会进入回放。\n"
    assert not _look_ahead_claims(_guarded_texts({"README.md:43's shape": refused_truth}, {})), (
        "README.md:43's true sentence is read as a claim"
    )
