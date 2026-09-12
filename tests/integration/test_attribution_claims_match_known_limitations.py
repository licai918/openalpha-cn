"""User-facing attribution claims must not overstate what `OutcomeValidator` produces.

Two couplings, both anchored on `KNOWN_ATTRIBUTION_LIMITATIONS`
(`src/openalpha_cn/backtest/validation.py:49`) rather than on a hand-written category list:

1. `docs/specs/v2/openalpha-cn-v2-prd.md`'s user-story table must not mark a row bare `IN`
   ("v2 范围内，不打折", legend :206) when that row's own Story text names a category
   `KNOWN_ATTRIBUTION_LIMITATIONS` records as structurally absent. `S65` ("Rule, factor, model
   and Agent attribution reconciled to final result") is the concrete instance today, but the
   check is written generically over every `S<n>` row so a future row making the same kind of
   claim is caught the same way `S65` was, without anyone having to remember to extend a list.

2. `README.md`, `README.en.md` and `docs/why-openalpha-cn.zh-CN.md` are OpenAlpha CN's
   user-facing surface (`why-openalpha` is linked from both the README headers -- README.md:5 --
   and body -- README.md:1109) and must not present rule/factor/agent/model-style attribution for
   a structurally absent category as something the product delivers. `d4af27c` fixed this exact
   overclaim in README.md/README.en.md/docs/marketing/ without adding a test; this is that test,
   for the two READMEs plus `docs/why-openalpha-cn.zh-CN.md`, which `d4af27c` did not touch and
   which still made the claim at :26 when this test was written.

   **`docs/marketing/` is deliberately excluded.** Two attribution-style lines there (around :176
   and :494 of `docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md`) are scheduled for
   `D7`, which is expected to fix those lines and extend `GUARDED_FILES` below to include that
   file once it does -- this paragraph is what `D7` should find when it looks for this guard.

**What the guard in (2) actually catches, and what it cannot.** It is a lexical heuristic over
markdown text, not a semantic reader, and both directions of error are possible by construction:

- It only looks at a "logical unit" (a table row, a heading, or a bullet/paragraph with its
  soft-wrapped continuation lines folded back together -- see `_logical_units`) that contains the
  literal word "归因" or "attribution". A claim phrased without that word (e.g. "拆分为规则、
  因子和 Agent 层面", the wording `d4af27c` actually landed at README.md:1185) is invisible to
  it. This is an accepted blind spot, not an oversight: every violation found in this repository
  so far used that word, and the `d4af27c`-fixed prose in README.md mostly avoids it entirely at
  the sentences that used to say it, which is part of *why* those sentences now pass.
- Within such a unit, it requires **at least two** distinct absent categories (by their English
  or Chinese names -- see `CATEGORY_MARKERS`) to be named, not one. A single incidental mention
  (the `` `factor run` `` CLI command sharing a sentence with an unrelated "六格归因"/"six-cell
  attribution grid", at README.md:450 and README.en.md:90 -- the factor-plane's IC/decile grid,
  nothing to do with return attribution) is common in this repository's prose and would be a
  false positive at a threshold of one. Every real violation found so far named two or more
  categories together, which is the shape this threshold is tuned to. The accepted cost is a
  blind spot for a claim that overstates exactly one absent category in isolation.
- A unit that also carries one of `ABSENCE_PHRASES` (the "结构性从不产生/产出而非(仅)被(收窄|
  窄化)" family, or "structurally never produced, not merely narrowed") is read as caveated, not
  claimed, and passes. This is a fixed phrase list measured against this repository's actual
  wording, not a general negation detector -- a differently-worded caveat could still be
  misclassified as a claim.
- Chinese substrings (`因子`, `智能体`, `模型`, `规则`) are matched with no word segmentation, so
  they count wherever they occur as a substring. English tokens are matched with `\\b...\\b` word
  boundaries, which treats a hyphen as a separator -- "per-agent" contains a standalone `agent`
  token this way. Plural forms ("agents"/"factors"/"models") do **not** count; only the singular
  does. Both choices were forced by a real false positive found while writing this test:
  README.en.md's un-bulleted four-clock feature list ("... deterministic baseline agents, a
  secure OpenAI-compatible BYOK model boundary, ... durable per-agent resume ..., ... reconciled
  attribution, ...") would otherwise read as naming `agent` (via "per-agent") and `model` (via
  "model boundary") beside an unrelated "attribution" mention several commas away.
  `_logical_units` below stops that specific false positive by never merging plain (non-bulleted)
  paragraph lines across a soft-wrap, so "attribution" and the two unrelated words never land in
  the same unit -- see its docstring for what that costs.

Every occurrence of "归因"/"attribution" in the three guarded files was read by hand while this
test was written, to confirm the thresholds above classify each one correctly (see the `D6`
report for the full list). `test_the_overclaim_guard_tells_a_claim_from_a_caveated_mention` below
pins the classifier's claim-vs-caveat behaviour on synthetic strings, independently of whatever
these three files currently say.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, get_args

from openalpha_cn.backtest.validation import KNOWN_ATTRIBUTION_LIMITATIONS
from openalpha_cn.domain.validation import AttributionTerm

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

PRD: Final[Path] = ROOT / "docs" / "specs" / "v2" / "openalpha-cn-v2-prd.md"
README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"

GUARDED_FILES: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA)
"""User-facing prose checked by part (2).

`docs/marketing/` is excluded on purpose -- see the module docstring's "deliberately excluded"
paragraph, which `D7` should find and act on.
"""

CATEGORY_VOCABULARY: Final[tuple[str, ...]] = get_args(
    AttributionTerm.model_fields["category"].annotation
)
"""`AttributionTerm.category`'s own `Literal` args, read from the type rather than retyped:
`('rule', 'factor', 'agent', 'model')` today."""


def _absent_categories() -> frozenset[str]:
    """Which of `CATEGORY_VOCABULARY` `KNOWN_ATTRIBUTION_LIMITATIONS` records as never produced.

    Matches on each limitation's `code` (its identifier), not its prose `detail`: `detail`
    strings narrate history and freely use category words in sentences that are not claims about
    today's absence -- limitation #1's detail recounts the deleted V2-P5-005 split "across a
    rule, a factor and the agents," but a rule term is very much produced today
    (`TRANSACTION_COST_TERM`/`FORGONE_BENCHMARK_TERM` are both `category="rule"`); matching its
    `detail` would wrongly mark "rule" absent. A `code` is an underscore-joined identifier, so
    membership is checked against its `_`-split tokens rather than as a substring. Applied to the
    four current codes: `an_agent_contribution_would_need_a_counterfactual_...` contributes
    `agent`; `neither_a_factor_nor_a_model_term_is_ever_produced_here` contributes `factor` and
    `model`; the other two codes contain none of `CATEGORY_VOCABULARY`'s words. Today's result is
    `{"agent", "factor", "model"}` -- "rule" is never a member because no code names it, matching
    that rule is the one category actually produced.
    """
    absent: set[str] = set()
    for limitation in KNOWN_ATTRIBUTION_LIMITATIONS:
        tokens = set(limitation.code.split("_"))
        absent |= {category for category in CATEGORY_VOCABULARY if category in tokens}
    return frozenset(absent)


ABSENT_CATEGORIES: Final[frozenset[str]] = _absent_categories()


# --- Part 1: the PRD may not mark a row bare "IN" for attribution it cannot produce ---------

STORY_ROW_ID: Final[re.Pattern[str]] = re.compile(r"^\|\s*(S\d+)\s*\|")


def _prd_story_rows(text: str) -> dict[str, tuple[str, str, str]]:
    """Every `| S<n> | Story | 状态 | 说明 |` row in the PRD, keyed by ID: (story, status, note).

    Matched on the ID column's shape (`S` + digits) rather than by locating one table by its
    header, since the same four-column shape recurs across the PRD's user-story tables --
    `grep -c '^| S[0-9]' docs/specs/v2/openalpha-cn-v2-prd.md` finds 97 rows -- and `S65` is
    distinguished from the rest only by its ID, not by which table it sits in.
    """
    rows: dict[str, tuple[str, str, str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not STORY_ROW_ID.match(line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == 4, (
            f"expected 4 columns (ID, Story, 状态, 说明) in PRD row {line!r}, found "
            f"{len(cells)}: {cells}. This parser assumes that shape; if the table was reshaped, "
            "update the parser rather than let it silently misread cells."
        )
        story_id, story, status, note = cells
        rows[story_id] = (story, status, note)
    return rows


def test_prd_story_status_is_not_bare_in_when_it_names_a_structurally_absent_category() -> None:
    """A PRD row may not claim `IN` ("v2 范围内，不打折") for attribution it cannot produce.

    `S65` ("Rule, factor, model and Agent attribution reconciled to final result") was marked
    bare `**IN**`, naming three categories (`factor`, `model`, `agent`) that
    `KNOWN_ATTRIBUTION_LIMITATIONS` records as structurally absent from `OutcomeValidator`'s
    output -- not narrowed, never produced. `IN` promises delivery "不打折" (legend :206); that
    is false of this row as written. `S54` is the precedent for the corrected shape: `IN-降级`
    plus a note explaining what is, and is not, delivered.

    Written generically over every `S<n>` row (matched on ID shape, not hardcoded to "S65") so a
    future row making the same kind of claim is caught the same way, keeping the coupling on
    `KNOWN_ATTRIBUTION_LIMITATIONS` rather than on this one row number. Checked this does not
    fire on unrelated rows that merely share vocabulary: `S63` ("Multiple-testing controls for
    broad factor and model searches") names `factor`/`model` but never says "attribution", so the
    same gate that finds `S65` excludes it; `S79` ("Portfolio and attribution dashboards") says
    "attribution" but names no specific category, so it has nothing to compare against
    `KNOWN_ATTRIBUTION_LIMITATIONS` and is excluded too.
    """
    assert ABSENT_CATEGORIES, "derivation produced no absent categories -- nothing to check"

    rows = _prd_story_rows(PRD.read_text(encoding="utf-8"))
    assert "S65" in rows, "S65 has moved, been renumbered or removed; relocate this coupling"

    violations: list[tuple[str, str, str, frozenset[str]]] = []
    for story_id, (story, status, _note) in rows.items():
        if "attribution" not in story.lower():
            continue
        named_absent = frozenset(
            category
            for category in ABSENT_CATEGORIES
            if re.search(rf"\b{category}s?\b", story, re.IGNORECASE)
        )
        if not named_absent:
            continue
        if status.strip("*~ ") == "IN":
            violations.append((story_id, story, status, named_absent))

    assert not violations, "\n".join(
        f"{story_id} is marked bare {status!r} but its Story text names {sorted(categories)}, "
        f"which KNOWN_ATTRIBUTION_LIMITATIONS records as structurally absent from "
        f"OutcomeValidator's output: {story!r}"
        for story_id, story, status, categories in violations
    )


# --- Part 2: user-facing prose may not present an absent category as delivered --------------

CATEGORY_MARKERS: Final[dict[str, tuple[str, ...]]] = {
    "rule": ("rule", "规则"),
    "factor": ("factor", "因子"),
    "agent": ("agent", "智能体"),
    "model": ("model", "模型"),
}
"""Bilingual surface forms for each category in `CATEGORY_VOCABULARY`.

Bridging English code vocabulary to this repository's mixed zh-CN/en prose is inherently a
hand-authored step -- the *set* of categories to look for is derived from
`KNOWN_ATTRIBUTION_LIMITATIONS`, but no source in this repository lists "the Chinese word for
each category," so that mapping is written here once rather than per test.
"""

ATTRIBUTION_MARKERS: Final[tuple[str, ...]] = ("归因", "attribution")

ABSENCE_PHRASES: Final[tuple[str, ...]] = (
    "结构性从不产生",
    "结构性不产生",
    "结构性从不产出",
    "结构性不产出",
    "structurally never produced",
    "structurally absent",
)
"""Phrasing this repository has actually used to state a category is absent, not narrowed.

README.md:44 uses "结构性从不产生而非被收窄"; README.md:1185 uses "结构性不产生而非被收窄" (no
"从"); README.en.md:53 uses "structurally never produced, not merely narrowed". A fixed list
measured against real usage, not a general negation detector -- see the module docstring.
"""


def _contains_marker(text: str, marker: str) -> bool:
    if marker.isascii():
        return re.search(rf"\b{re.escape(marker)}\b", text, re.IGNORECASE) is not None
    return marker in text


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(
        phrase.lower() in lowered if phrase.isascii() else phrase in text for phrase in phrases
    )


_BULLET: Final[re.Pattern[str]] = re.compile(r"^(-|\*|\d+\.)\s")


def _logical_units(text: str) -> list[str]:
    """Split markdown prose into the units a reader would read as one claim.

    A table row (starts with `|`) and a heading (starts with `#`) are already complete on one
    physical line and are kept as-is. A bullet (`- `/`* `/`1. `) starts a new unit, and an
    *indented* line that immediately follows it (no intervening blank line, heading, table row or
    new bullet) is folded into that bullet's unit with a single space -- this is what reassembles
    a claim markdown soft-wrapped across several physical lines, such as README.en.md's :51-54
    bullet, into the one sentence it actually is.

    An unindented line that is not itself a bullet, heading or table row is kept as its own,
    separate unit -- plain (non-bulleted) paragraph prose is deliberately **not** merged across
    its own soft-wraps. This was forced by a real false positive: this repository's long
    unbulleted paragraphs mix unrelated feature mentions across lines (README.en.md's four-clock
    intro names "per-agent" resume and a "model boundary" on different lines from its one
    unrelated "reconciled attribution" mention), and merging the whole paragraph into one unit
    made `_overclaimed_categories` see all three together. Scanning plain paragraphs line-by-line
    trades away catching a claim an author splits across two lines of ordinary prose -- something
    no claim in this repository's history has actually done, every one found so far sat inside a
    single bullet (whose wrap *is* reassembled) or a single table row -- for not manufacturing
    that false positive.
    """
    units: list[str] = []
    current: list[str] = []
    in_bullet = False

    def flush() -> None:
        nonlocal in_bullet
        if current:
            units.append(" ".join(current))
            current.clear()
        in_bullet = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith(("|", "#")):
            flush()
            units.append(stripped)
            continue
        if _BULLET.match(stripped):
            flush()
            current.append(stripped)
            in_bullet = True
            continue
        if in_bullet and raw_line[:1].isspace():
            current.append(stripped)
            continue
        flush()
        units.append(stripped)
    flush()
    return units


def _overclaimed_categories(unit: str, absent: frozenset[str]) -> frozenset[str]:
    """Absent categories `unit` presents as delivered attribution, or empty if not a claim.

    Empty whenever: the unit never mentions "归因"/"attribution" at all; it names fewer than two
    distinct absent categories (see module docstring for why the threshold is two, not one); or
    it names two-or-more but also carries one of `ABSENCE_PHRASES`, which this repository's own
    fixed wording uses to state the absence rather than claim delivery.
    """
    if not _contains_any(unit, ATTRIBUTION_MARKERS):
        return frozenset()
    named = frozenset(
        category
        for category in absent
        if any(_contains_marker(unit, marker) for marker in CATEGORY_MARKERS[category])
    )
    if len(named) < 2:
        return frozenset()
    if _contains_any(unit, ABSENCE_PHRASES):
        return frozenset()
    return named


def test_user_facing_docs_do_not_present_an_absent_attribution_category_as_delivered() -> None:
    """`README.md`, `README.en.md` and `docs/why-openalpha-cn.zh-CN.md` must not overclaim.

    `docs/why-openalpha-cn.zh-CN.md:26`'s "验证改进" row lists `规则/因子/Agent 归因对账`
    ("rule/factor/Agent attribution reconciliation") as what OpenAlpha CN's approach delivers --
    a slash list naming two categories (`factor`, `Agent`) `KNOWN_ATTRIBUTION_LIMITATIONS`
    records as structurally absent, with no caveat. `README.md` and `README.en.md` made the same
    kind of claim before `d4af27c`, which corrected both without adding a test; this is that
    test, extended to the one guarded file `d4af27c` did not touch.
    """
    assert ABSENT_CATEGORIES, "derivation produced no absent categories -- nothing to check"

    violations: list[tuple[Path, str, frozenset[str]]] = []
    for path in GUARDED_FILES:
        for unit in _logical_units(path.read_text(encoding="utf-8")):
            claimed = _overclaimed_categories(unit, ABSENT_CATEGORIES)
            if claimed:
                violations.append((path, unit, claimed))

    assert not violations, "\n".join(
        f"{path.relative_to(ROOT)} presents {sorted(categories)} as delivered attribution "
        f"without stating their absence: {unit!r}"
        for path, unit, categories in violations
    )


def test_the_overclaim_guard_tells_a_claim_from_a_caveated_mention() -> None:
    """Mutation check for `_overclaimed_categories` itself, independent of the three real files.

    Not excerpts of any real document -- minimal strings in the two shapes the guard exists to
    tell apart, so a future change to `CATEGORY_MARKERS`/`ABSENCE_PHRASES`/the threshold that
    breaks the distinction fails here even if all three guarded files happen to be clean at the
    time.
    """
    claim = "本项目对规则/因子/Agent 三类归因均已完整支持。"
    caveated = "因子与 Agent 归因结构性从不产生而非被收窄，仅规则类目两项条款可查。"

    assert _overclaimed_categories(claim, ABSENT_CATEGORIES) == frozenset({"factor", "agent"})
    assert not _overclaimed_categories(caveated, ABSENT_CATEGORIES)
