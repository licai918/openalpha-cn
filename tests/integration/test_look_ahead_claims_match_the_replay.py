"""No document or diagram may say the replay detects, counts or reports look-ahead violations.

**The premise**, held by `test_a_look_ahead_corpus_is_refused_before_the_replay_runs`. A replay
case whose evidence is not yet visible at its own `as_of` fails validation:
`ReplayCase.validate_point_in_time` raises `LookAheadViolationError`, and `ReplayCorpus.load`
validates every case. So a corpus that is loaded, or built through validation, holds no
look-ahead case, and `ReplayReport.look_ahead_violations` is 0 for it. A corpus assembled without
validation -- `model_copy` with an update, or `model_construct` -- still reaches
`ReplayRunner.run`, which counts the case (`test_a_corpus_built_without_validation_is_counted`);
`look_ahead_violations`' own docstring says as much, and that the count is not a detection
result. What the replay does check is determinism: each case runs twice, the second time from
empty stores, and a difference is a failure. The 确定性回放 bullet of `README.md` says both
halves. If the premise test fails, a corpus holding a look-ahead case loads again, and the
sentences this guard holds were written for a premise that no longer holds: re-read them, and
this guard.

**The claims.** A clause is a claim when it mentions look-ahead (`LOOK_AHEAD_WORDS`) and either
names a look-ahead violation or bias (`VIOLATION_WORDS`) beside a count, zero, detection or
report word (`TALLY_WORDS`) or a zero written as a numeral (`ZERO_NUMERAL`: 0, 0.0, or 0 in full
width, never the 0s of 300 and 0.5), or pairs a check, detection, verification, test, finding or
report word (`CHECK_WORDS`) with the replay (`REPLAY_WORDS`). A Chinese word is read as a
substring and an English one as a word (`_english`). At `4a37161` this flagged `README.md:1183`,
two clauses of marketing §059 and api-05's validation table, all rewritten. Every other
look-ahead clause and unit it read there says that look-ahead is refused or kept out, which is
true, and none of them is flagged; the 确定性回放 bullet of `README.md` is one.

**How it reads.** The four documents as clauses (`tests/prose_clauses.py`), and the diagrams as
units (`tests/diagram_text.py`). api-05 draws its four validation cards from one data table, a
tuple of rows no call takes, and each row is a unit: the replay card's "同路径回放" and its
"确定性 / 防前视报告" were read together, as one card.

**What it cannot see.** `test_the_stated_limits_are_real` measures each of these.

- A claim worded without these words: "回放能抓出偷看未来的事件" has neither 前视 nor a
  check word, and "回放保证没有前视" has 前视 and the replay but words its check as 保证 and its
  zero as 没有.
- A claim split across clauses, the replay in one and the check in the next: "冻结语料回放很
  严格。它会发现前视问题。"
- An English claim worded outside the few English words.
- Text a generator computes at run time -- an f-string's formatted values, a string built with
  `+` or `%`, a name -- which `tests/diagram_text.py` does not read into a unit.

**What it refuses that is true.** The same test measures these.

- A denial in the same words fails: "回放不检测前视违规" names a violation beside a detection
  word. Say what happens instead: a corpus holding look-ahead evidence is refused at load.
- A 0 that counts nothing is read as a zero beside a violation: "T+0 交易下前视违规被整体拒绝"
  is true and fails, as does a clause with 0% or 第0批 beside one. `ZERO_NUMERAL` skips only the
  0s inside other numbers, and 0% cannot be skipped: "前视违规率为 0%" is a claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from diagram_text import diagram_units
from prose_clauses import clauses
from pydantic import ValidationError

from openalpha_cn.backtest.replay import ReplayCase, ReplayCorpus, ReplayRunner
from openalpha_cn.domain.time import Timeline
from openalpha_cn.storage.migrations import run_migrations
from openalpha_cn.storage.validation import SQLiteValidationStore

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
    that case's `as_of` does not, so a replay of a loaded corpus never meets a look-ahead case to
    count."""
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


@pytest.mark.parametrize("build", ["model_copy", "model_construct"])
def test_a_corpus_built_without_validation_is_counted(tmp_path: Path, build: str) -> None:
    """The exception the premise states. `model_copy` with an update, and `model_construct`, each
    build a corpus holding a look-ahead case without validating it; `ReplayRunner.run` takes it,
    and `look_ahead_violations` counts the case, which fails."""
    corpus = ReplayCorpus.load(FROZEN_CORPUS)
    case = corpus.cases[0]
    evidence = case.evidence[0]
    later = case.as_of + timedelta(days=1)
    late = evidence.model_copy(
        update={
            "timeline": Timeline(
                event_time=evidence.timeline.event_time,
                available_time=later,
                ingested_time=later,
                revision_time=later,
            )
        }
    )
    if build == "model_copy":
        unvalidated = corpus.model_copy(
            update={"cases": (case.model_copy(update={"evidence": (late,)}),)}
        )
    else:
        unvalidated = ReplayCorpus.model_construct(
            schema_version=corpus.schema_version,
            trading_days=corpus.trading_days,
            cases=(ReplayCase.model_construct(**{**dict(case), "evidence": (late,)}),),
        )

    def clock() -> datetime:
        return case.as_of

    validation_path = tmp_path / "state.sqlite3"
    run_migrations(validation_path, clock=clock)
    report = ReplayRunner(
        code_commit="0123456789abcdef", config_digest="d" * 64, random_seed=7
    ).run(
        corpus=unvalidated,
        state_path=tmp_path / "replay.sqlite3",
        validation_store=SQLiteValidationStore(validation_path),
        clock=clock,
    )
    assert (report.total_cases, report.look_ahead_violations, report.succeeded) == (1, 1, 0), (
        f"the unvalidated look-ahead corpus was reported as {report}"
    )


# --- The claims -------------------------------------------------------------------------------

LOOK_AHEAD_WORDS: Final[tuple[str, ...]] = ("前视", "look-ahead", "lookahead", "look ahead")
"""Look-ahead, in Chinese and in three English spellings."""

VIOLATION_WORDS: Final[tuple[str, ...]] = ("前视违规", "前视偏差", "look-ahead violation")
"""A look-ahead violation or bias: what a count or a zero would be of."""

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

ZERO_NUMERAL: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.\uff0e\uff10-\uff19])[0\uff10](?:[.\uff0e][0\uff10]+)?"
    r"(?![\w\uff10-\uff19]|[.\uff0e][\d\uff10-\uff19])",
    re.ASCII,
)
"""A zero written as a numeral: 0 standing alone, with or without decimal zeros (0.0, 0.00), in
ASCII or in full width (U+FF10, with the full-width point U+FF0E). Not the 0 of 300, 2020, 0.5,
0.05, 05, v0 or a full-width ten. Word characters are ASCII here, so a 0 straight after a Chinese
character is read. So is a 0 that counts nothing, such as T+0 or 第0批; the module docstring lists
that as an over-reach."""

CHECK_WORDS: Final[tuple[str, ...]] = (
    "检查",
    "检测",
    "验证",
    "校验",
    "发现",
    "报告",
    "测试",
    "check",
    "detect",
    "verif",
    "test",
    "find",
    "found",
    "report",
)
"""The words of checking, detecting, verifying, testing, finding or reporting."""

REPLAY_WORDS: Final[tuple[str, ...]] = ("回放", "冻结语料", "replay")
"""The replay, by name: "replay" covers "replays", and `ReplayCorpus` and `CorpusReplay` too,
since a CamelCase name's capitals start and end its words (`_english`)."""

MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "look-ahead": LOOK_AHEAD_WORDS,
    "violation": VIOLATION_WORDS,
    "tally": TALLY_WORDS,
    "check": CHECK_WORDS,
    "replay": REPLAY_WORDS,
}
"""Every word the claim rule reads, by family. A Chinese word is matched as a substring and an
English word as a word (`_english`), in any case: "counts", "reported" and "verifies" are read,
and foundation, nonetheless and latest are not. "found" and "find" are both listed because
neither is an inflection of the other.
"""

ENGLISH_SUFFIXES: Final[str] = "s|es|ed|ing|ings|ion|ions|y|ies|ied|ying|ication|ications"
"""The inflections an English word of `MARKERS` is read with: counts, reported, detection,
verifies, verification, findings."""


def _english(word: str) -> re.Pattern[str]:
    """`word` read as an English word, in any case: where a word starts (after a non-letter, or at
    a capital after a lower-case letter, inside a CamelCase name), whole or with one of
    `ENGLISH_SUFFIXES`, and followed by no lower-case letter (a capital may follow)."""
    return re.compile(
        rf"(?:(?<![A-Za-z])|(?<=[a-z])(?=[A-Z]))(?i:{re.escape(word)})"
        rf"(?:(?i:{ENGLISH_SUFFIXES}))?(?![a-z])"
    )


def _holds(text: str, words: Iterable[str]) -> bool:
    """Whether `text` holds one of `words`: an English word as a word, any other as a substring."""
    return any(_english(word).search(text) if word.isascii() else word in text for word in words)


def _is_claim(
    text: str,
    markers: dict[str, tuple[str, ...]] = MARKERS,
    zero: re.Pattern[str] | None = ZERO_NUMERAL,
) -> bool:
    """Whether one clause presents the replay as detecting, counting or reporting look-ahead."""
    if not _holds(text, markers["look-ahead"]):
        return False
    counted = _holds(text, markers["tally"]) or bool(zero and zero.search(text))
    tallied = _holds(text, markers["violation"]) and counted
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
        "so the replay of a loaded corpus has none to count. Say that; fix a diagram in its "
        "generator and regenerate it."
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


CLAIMS_THE_FIX_REVIEW_WROTE: Final[dict[str, str]] = {
    "the numeral 0": "前视违规始终为 0，冻结语料一次都没漏过。\n",
    "0 with a counter": "严重前视违规为 0 例。\n",
    "0 in English": "look-ahead violations: 0\n",
    "测试": "冻结语料回放测试确定性与防前视。\n",
}
"""The review of the `D13` fixes put the first and the last into `README.md`, and the guard
passed: it read 零 but not the numeral 0, and 检查 but not 测试."""


def test_the_claims_the_fix_review_wrote_are_read() -> None:
    """A zero written as the numeral 0, and a check worded as 测试, are read."""
    missed = [
        label
        for label, text in CLAIMS_THE_FIX_REVIEW_WROTE.items()
        if not _look_ahead_claims(_guarded_texts({label: text}, {}))
    ]
    assert not missed, f"a claim the review of the fixes wrote went unread: {missed}"


MARKER_SENTENCES: Final[dict[tuple[str, str], str]] = {
    ("look-ahead", "前视"): "冻结语料回放检查前视问题。",
    ("look-ahead", "look-ahead"): "The replay checks look-ahead.",
    ("look-ahead", "lookahead"): "The replay checks lookahead.",
    ("look-ahead", "look ahead"): "The replay checks for look ahead.",
    ("violation", "前视违规"): "系统统计前视违规。",
    ("violation", "前视偏差"): "前视偏差恒为零。",
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
    ("check", "测试"): "回放测试前视。",
    ("check", "校验"): "回放校验前视。",
    ("check", "test"): "The replay tests look-ahead.",
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
    """Dropping any one word from `MARKERS` lets its sentence pass, so every word is pinned; so
    does dropping `ZERO_NUMERAL`, and a 0 inside another number is not a zero."""
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
    zero_claims = (
        "前视违规始终为 0。",
        "前视违规为0例。",
        "前视违规率为 0%。",
        "前视违规率为 0.0%。",
        "前视违规为\N{FULLWIDTH DIGIT ZERO}例。",
        "Look-ahead violations: 0.",
    )
    for claim in zero_claims:
        assert _is_claim(claim) and not _is_claim(claim, zero=None), (
            f"{claim!r} is not made a claim by ZERO_NUMERAL alone"
        )
    numbers = (
        "阈值 0.5、1.0 版、300 个事件、2020 年起、第 05 批、v0 语料若含前视违规，"
        "加载时就被整体拒绝。"
    )
    assert not _is_claim(numbers), f"a 0 inside another number was read as a zero: {numbers!r}"
    wide = (
        "\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT ZERO} 个语料若含前视违规，加载时就被整体拒绝。"
    )
    assert not _is_claim(wide), f"the 0 of a full-width ten was read as a zero: {wide!r}"
    decimals = "阈值 0.05 的语料若含前视违规，加载时就被整体拒绝。"
    assert not _is_claim(decimals), f"the 0s of 0.05 were read as a zero: {decimals!r}"


TRUE_ENGLISH_SENTENCES: Final[dict[str, str]] = {
    "found in foundation": (
        "The foundation of the replay is a corpus that refuses a look-ahead violation at load.\n"
    ),
    "none in nonetheless": (
        "Nonetheless, a corpus holding a look-ahead violation is refused at load.\n"
    ),
    "test in latest": "The latest replay refuses any corpus that holds look-ahead evidence.\n",
}
"""True sentences in which an English word of the rule sits inside another word. The final review
of `D13` found the rule reading "found" in foundation and "none" in nonetheless, and flagging the
first two."""

CAMEL_CASE_CLAIMS: Final[tuple[str, ...]] = (
    "The ReplayCorpus checks look-ahead.",
    "The CorpusReplay checks look-ahead.",
)
"""Claims whose replay is written inside a CamelCase name, at its start and at its end."""


def test_an_english_word_is_read_as_a_word() -> None:
    """An English word of `MARKERS` is read where a word starts, whole or inflected, and at a
    capital inside a CamelCase name; never inside another word."""
    flagged = [
        label
        for label, text in TRUE_ENGLISH_SENTENCES.items()
        if _look_ahead_claims(_guarded_texts({label: text}, {}))
    ]
    assert not flagged, f"an English word inside another word was read: {flagged}"
    unread = [text for text in CAMEL_CASE_CLAIMS if not _is_claim(text)]
    assert not unread, f"a word inside a CamelCase name went unread: {unread}"


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
        "a check and a zero worded outside the words": (
            {"保证 and 没有": "回放保证没有前视。\n"},
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
        "a 0 that counts nothing: T+0": ({"T+0": "T+0 交易下前视违规被整体拒绝。\n"}, {}),
        "a 0 that counts nothing: a 0% position": (
            {"0%": "仓位为 0% 时，含前视违规的语料同样在加载时被整体拒绝。\n"},
            {},
        ),
        "a 0 that counts nothing: an ordinal": (
            {"第0批": "第0批语料若含前视违规，加载时就被整体拒绝。\n"},
            {},
        ),
    }
    passed = [
        label
        for label, (documents, sources) in refused.items()
        if not _look_ahead_claims(_guarded_texts(documents, sources))
    ]
    assert not passed, f"a stated over-reach no longer happens: {passed}"
    two_cards = (
        'columns = ((64, "同路径回放", ("两遍比对", "前视语料加载即拒")), '
        '(394, "结果归因", ("验证结果",)))\n'
    )
    assert not _look_ahead_claims(_guarded_texts({}, {"two cards": two_cards})), (
        "a check word on one card of a table and 前视 on another are read together again"
    )
    refused_truth = "含前视证据的语料在加载时就被整体拒绝，不会进入回放。\n"
    assert not _look_ahead_claims(_guarded_texts({"the bullet's shape": refused_truth}, {})), (
        "the true sentence of README.md's 确定性回放 bullet is read as a claim"
    )
