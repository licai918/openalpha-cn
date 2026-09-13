"""The two batch ceilings must be findable outside the source tree (V2-P4-042, V2-P4-043).

`V2-P4-019` lowered `MAX_BATCH_WORKERS` from 32 to 8. `batch_contracts.py`'s docstring records
the reasoning superbly -- the measured 1/2/4/8/16/32 throughput plateau -- but a source comment
is not user documentation, and at `be262ea`
`grep -rn max_concurrency docs README.md README.en.md web CHANGELOG.md` returned **zero hits**
outside the roadmap that filed the defect. A request that worked yesterday answered
`422 Input should be less than or equal to 8` today, and nowhere a caller looks said why.

`V2-P4-043` is the same class of gap one field over: `OPENALPHA_MAX_REQUEST_BYTES` decides
whether a whole-market request can be *put*, and the `413` never named it.

**Why the doc assertions are pinned to the live constants rather than to literals.** A test that
grepped for the string `"8"` would keep passing after someone changed the ceiling and left the
prose behind -- which is precisely the failure mode this row *is*. Every current ceiling asserted
here is read from `batch_contracts`/`config` at run time and then required to appear in the
prose. The numbers written here as literals are not current ceilings: `"32"`, the worker
ceiling's former value, which the prose has to state
(`test_the_http_doc_gives_the_reason_the_worker_ceiling_was_lowered`); `9_840_054`,
`V2-P4-043`'s measured size of a whole-market batch; and the floor `1` of every worker range,
the `ge=1` both `max_concurrency` fields declare.

**Which ceilings a real request drives, and where.** Only the worker ceiling is driven through
the API in this module: `test_the_worker_ceiling_the_api_enforces_is_the_one_the_http_doc_states`
posts `MAX_BATCH_WORKERS + 1` and reads the `422`, then posts `MAX_BATCH_WORKERS` and reads the
`202`. The item cap and the byte ceiling are checked here against `batch_contracts` and `config`
only. Their live refusals are in other modules: `test_validation_refusal_size.py` posts
`MAX_BATCH_ITEMS + 1` batch requests and reads the `422` that names the limit, and
`test_request_body_ceiling.py::test_the_413_names_the_variable_that_raises_the_ceiling` draws
the `413` against a 512-byte configured ceiling and asserts it names
`OPENALPHA_MAX_REQUEST_BYTES`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from diagram_text import diagram_strings
from fastapi.testclient import TestClient
from prose_clauses import clauses

from openalpha_cn.api.app import create_app
from openalpha_cn.batch_contracts import MAX_BATCH_ITEMS, MAX_BATCH_WORKERS
from openalpha_cn.config import OpenAlphaConfig, load_config

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
HTTP_DOC: Final[Path] = ROOT / "docs" / "api" / "http.md"
CHANGELOG: Final[Path] = ROOT / "CHANGELOG.md"
README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"
MARKETING: Final[Path] = ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md"
NOW: Final[datetime] = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


@pytest.fixture
def http_doc() -> str:
    return HTTP_DOC.read_text(encoding="utf-8")


@pytest.fixture
def changelog() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


@pytest.fixture
def readme() -> str:
    return README.read_text(encoding="utf-8")


def _batch_body(*, batch_id: str, max_concurrency: int) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "requests": [
            {
                "run_id": "doc-probe-0",
                "mode": "replay",
                "subject": "600000.SH",
                "as_of": NOW.isoformat(),
                "evidence": [],
                "code_commit": "0123456789abcdef",
                "config_digest": "b" * 64,
                "random_seed": 7,
            }
        ],
        "max_concurrency": max_concurrency,
    }


def test_the_worker_ceiling_the_api_enforces_is_the_one_the_http_doc_states(
    tmp_path: Path, http_doc: str
) -> None:
    """The live `422` and the prose must agree on the same number.

    The request is driven for real so the number in the doc is checked against the ceiling the
    service actually applies, not against the constant alone -- the constant and the route could
    both be right while the prose is stale, and the constant and the prose could both be right
    while the route reads a copy.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))

    refused = client.post(
        "/api/v1/research/batches",
        json=_batch_body(batch_id="over", max_concurrency=MAX_BATCH_WORKERS + 1),
    )
    assert refused.status_code == 422, refused.text
    assert f"less than or equal to {MAX_BATCH_WORKERS}" in refused.text

    accepted = client.post(
        "/api/v1/research/batches",
        json=_batch_body(batch_id="at-ceiling", max_concurrency=MAX_BATCH_WORKERS),
    )
    assert accepted.status_code == 202, accepted.text

    assert "max_concurrency" in http_doc, "the HTTP reference never mentions the field"
    assert str(MAX_BATCH_WORKERS) in http_doc
    assert str(MAX_BATCH_ITEMS) in http_doc or f"{MAX_BATCH_ITEMS:,}" in http_doc


def test_the_http_doc_gives_the_reason_the_worker_ceiling_was_lowered(http_doc: str) -> None:
    """A ceiling stated without its reason invites the next caller to ask for it back.

    `32` is asserted because the prose has to say what the ceiling *was* for a caller whose
    working request stopped working; a doc that only stated the new number would leave them
    unable to recognise their own failure in it.
    """
    assert "32" in http_doc
    assert "max_concurrency" in http_doc
    lowered = [
        line
        for line in http_doc.splitlines()
        if "max_concurrency" in line or "MAX_BATCH_WORKERS" in line
    ]
    assert lowered, "no line of the HTTP reference discusses the worker ceiling"


def test_the_changelog_records_the_one_change_that_breaks_an_existing_caller(
    changelog: str,
) -> None:
    """`V2-P4-019` shipped with a `CHANGELOG` entry that omitted the narrowing.

    This is the entry a caller diffing releases reads, and the narrowing is the only part of
    that issue that can make a previously working request fail.
    """
    assert "max_concurrency" in changelog
    assert str(MAX_BATCH_WORKERS) in changelog
    assert "V2-P4-042" in changelog


def test_the_request_body_ceiling_is_named_in_the_http_doc_with_its_variable(
    http_doc: str,
) -> None:
    """`V2-P4-043`: the environment variable a caller must raise has to be findable.

    The byte count is read from the live default rather than written down, so a deployment-doc
    number that fell behind `config.max_request_bytes` goes red here.
    """
    assert "OPENALPHA_MAX_REQUEST_BYTES" in http_doc
    assert "413" in http_doc
    assert str(load_config().max_request_bytes) in http_doc


DEPLOYMENTS_THAT_SET_THE_CEILING: Final[tuple[str, ...]] = (
    "Dockerfile",
    "deploy/compose.yml",
    ".env.example",
    "docs/deployment/production.zh-CN.md",
)
"""Every file that names `OPENALPHA_MAX_REQUEST_BYTES` beside a byte count.

The first two are **not documentation**. They are `ENV` and `environment:` entries, so they
*override* whatever `config.py` declares -- which is why a stale number there is a live defect
and not a stale sentence. `docs/api/http.md` is excluded on purpose: it names the variable beside
several other numbers (a `41000000` in a refusal example, `10000` items), and a rule that every
long number near the variable equals the ceiling is false of prose by construction.
"""


def test_every_deployment_that_sets_the_ceiling_sets_the_one_this_service_declares() -> None:
    """`V2-P4-043` raised the default in `config.py` and three artefacts kept the old number.

    **Measured on `c847295`.** `OpenAlphaConfig` declares `33554432`; `Dockerfile` carried
    `OPENALPHA_MAX_REQUEST_BYTES=8388608`, `deploy/compose.yml` carried
    `${OPENALPHA_MAX_REQUEST_BYTES:-8388608}`, and the deployment doc's table said `8388608`. The
    first two are configuration, so the *shipped container ran at 8 MiB* -- and with that value
    `load_config().max_request_bytes` is `8388608`, against which `V2-P4-043`'s own measurement of
    a `MAX_BATCH_ITEMS` batch, **9,840,054 bytes**, is still `413`. The row that exists to make
    that batch postable did not make it postable anywhere it ships.

    `test_the_request_body_ceiling_is_named_in_the_http_doc_with_its_variable` claims in its own
    docstring that "a deployment-doc number that fell behind `config.max_request_bytes` goes red
    here"; that is **false** and this test is why it now is not -- that one reads only
    `docs/api/http.md`, and asserts the live number is *present* rather than that no other number
    contradicts it, so the deployment doc and both container files were outside everything.

    Read against the **declared** default rather than `load_config()`: a developer with
    `OPENALPHA_MAX_REQUEST_BYTES` exported would otherwise make this test agree with their shell
    instead of with the repository.
    """
    declared = int(OpenAlphaConfig.model_fields["max_request_bytes"].default)
    for name in DEPLOYMENTS_THAT_SET_THE_CEILING:
        path = ROOT / name
        found: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if "OPENALPHA_MAX_REQUEST_BYTES" not in line:
                continue
            found.extend(re.findall(r"\d{4,}", line))
        assert found, f"{name} no longer states a byte count for the ceiling"
        assert {int(number) for number in found} == {declared}, (
            f"{name} states {sorted(set(found))} for OPENALPHA_MAX_REQUEST_BYTES and this "
            f"service declares {declared}. The container files are configuration, not prose: a "
            f"stale number there is the ceiling the deployment actually runs with."
        )


def test_the_shipped_container_can_carry_a_whole_market_batch() -> None:
    """The consequence, asserted rather than left to the reader of the number.

    The measurement is `V2-P4-043`'s own: one evidence snapshot per request, a
    `MAX_BATCH_ITEMS` batch is 9,840,054 bytes. What this asserts is that the ceiling the
    container is configured with clears it -- which is the sentence the row's fix was for, and
    which was false of every shipped deployment until `V2-P5-012`.
    """
    measured_whole_market_batch_bytes = 9_840_054
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    configured = int(re.findall(r"OPENALPHA_MAX_REQUEST_BYTES=(\d+)", dockerfile)[0])

    assert configured > measured_whole_market_batch_bytes, (
        f"the container is configured at {configured} bytes and a batch at the ceiling this "
        f"service declares measured {measured_whole_market_batch_bytes}"
    )
    assert configured >= MAX_BATCH_ITEMS, "sanity: the ceiling is bytes, not items"


def _wrap_tolerant(phrase: str) -> str:
    """`phrase` as a pattern that still matches after a reflow puts whitespace inside it.

    `README.md` is hard-wrapped, so a line break -- plus the next line's indentation -- can land
    between any two characters of a Chinese sentence without changing what it says. The spaces
    `phrase` does contain are made optional for the same reason.
    """
    return r"\s*".join(re.escape(char) for char in phrase if not char.isspace())


BATCH_ITEM_CAP: Final[re.Pattern[str]] = re.compile(
    _wrap_tolerant("批量 API 则把最多")
    + r"(?P<stated>[^。]*?)"
    + _wrap_tolerant("个不可变请求放入")
)
"""The item-cap clause of `README.md`'s batch-API sentence ("API 关系图 03"), found by its words.

It anchors on the prose either side of the number, never on the number or on a line number.
What sits between the anchors is captured loosely -- anything short of the sentence's `。` --
and parsed strictly by the test, so a malformed number fails as a malformed number rather than
as a missing sentence.

**Why `README.md` needs its own check.**
`test_the_worker_ceiling_the_api_enforces_is_the_one_the_http_doc_states` asserts only
``str(MAX_BATCH_ITEMS) in http_doc or f"{MAX_BATCH_ITEMS:,}" in http_doc``: true the moment the
right digits appear *anywhere* in `docs/api/http.md`, including two paragraphs away from a stale
claim. And until `e758f0a` nothing in this module read `README.md` at all.

**The stale claim was real.** The sentence said "最多 1000 个" from the day it was written
(`8d13065`, 2026-07-27), and it was true then: the cap was the literal `max_length=1000` on the
batch request and task models -- `MAX_BATCH_ITEMS` did not exist yet. `dd4af2a` (`V2-P4-019`,
2026-08-18) introduced `MAX_BATCH_ITEMS = 10_000` to make a whole-market batch expressible, and
the sentence kept saying 1000 until `e758f0a` (2026-09-12): stale for 25 days, about 3.6 weeks,
rather than never true. `git show` of those three commits is the evidence.
"""

BATCH_WORKER_RANGE: Final[re.Pattern[str]] = re.compile(
    _wrap_tolerant("个不可变请求放入持久队列，以")
    + r"(?P<stated>[^。]*?)"
    + _wrap_tolerant("的受控并发")
)
"""The concurrency range the same sentence states, anchored on the words after its item cap.

Starting from the item-cap clause's own closing words is what ties this to the one sentence. The
other places `README.md` states a concurrency range -- its feature table and its feature list --
use different words, and `test_every_worker_range_the_documents_state_is_the_one_the_api_enforces`
reads those, and this sentence's range again, by a looser rule.

That range went stale once already, in exactly the way the item cap did. It said 1-32 from
`8d13065`, when the bound was the literal `le=32` on both `max_concurrency` fields. `dd4af2a`
(2026-08-18) introduced `MAX_BATCH_WORKERS = 8`, and the sentence kept saying 32 until `74cee0f`
(2026-09-10), with no test reading the number. (`README.md` writes the range with an en dash,
U+2013.)
"""

WELL_FORMED_COUNT: Final[re.Pattern[str]] = re.compile(
    r"[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,2}(?:_[0-9]{3})+|[1-9][0-9]*"
)
"""A count as prose writes it: ASCII digits, ungrouped or grouped in threes by `,` or by `_`.

Used with `fullmatch`, so `10000`, `10,000` and `10_000` are read, and `1,0000`, `100,00`,
`10 000`, a full-width comma and `一万` are not. Stripping separators and calling `int()` instead
would read `1,0000` as 10000 -- a number the sentence does not state -- and would raise
`ValueError` on a bare `,`.
"""

RANGE_JOINERS: Final[str] = "".join(
    ("-", chr(0x2013), chr(0x2014), "~", chr(0xFF5E), chr(0x2012), chr(0x2212), chr(0x301C))
)
"""A hyphen, an en dash, an em dash, a tilde, a full-width tilde, a figure dash, a minus sign
and a wave dash.

Spelled with `chr` because ruff's RUF001 refuses the literal dashes and tildes as look-alikes of
`-` and `~`.
"""

STATED_RANGE: Final[re.Pattern[str]] = re.compile(
    rf"([1-9][0-9]*)\s*[{re.escape(RANGE_JOINERS)}]\s*([1-9][0-9]*)"
)
"""`<floor>-<ceiling>` in ASCII digits, joined by any one of `RANGE_JOINERS`."""


def test_the_readme_batch_api_sentence_states_the_current_item_ceiling(readme: str) -> None:
    """Every copy of `README.md`'s batch-API item-cap clause states `MAX_BATCH_ITEMS`.

    Unlike the HTTP-doc check above, the right digits elsewhere in the file do not count: each
    clause is found by its own words and *its* number is read back. Every occurrence is read,
    not only the first, so a correct copy cannot vouch for a stale one further down.

    Finding no such clause fails too: a reword that drops the anchor words has to break this
    test, not disable it. `README.en.md` states no batch item number, so it has nothing to read.
    """
    occurrences = list(BATCH_ITEM_CAP.finditer(readme))
    assert occurrences, (
        "could not find README.md's batch-API item-cap clause (the words "
        "'批量 API 则把最多 <N> 个不可变请求放入'). Either the wording changed -- update "
        "BATCH_ITEM_CAP to match it -- or the claim was removed, in which case remove this test "
        "with it rather than leave it passing over nothing."
    )
    problems: list[str] = []
    for occurrence in occurrences:
        stated = " ".join(occurrence["stated"].split())
        line = readme.count("\n", 0, occurrence.start("stated")) + 1
        if WELL_FORMED_COUNT.fullmatch(stated) is None:
            problems.append(
                f"README.md:{line} gives the batch item cap as {stated!r}, which is not a count "
                "this test reads (ASCII digits, ungrouped or grouped in threes by ',' or '_': "
                "10000, 10,000, 10_000)."
            )
            continue
        stated_cap = int(stated.replace(",", "").replace("_", ""))
        if stated_cap != MAX_BATCH_ITEMS:
            problems.append(
                f"README.md:{line} states a batch item cap of {stated_cap}, but MAX_BATCH_ITEMS is "
                f"{MAX_BATCH_ITEMS}. V2-P4-019 raised the constant tenfold; update the sentence to "
                "the constant rather than the other way around."
            )
    assert not problems, "\n".join(problems)


def test_the_readme_batch_api_sentence_states_the_current_worker_ceiling(readme: str) -> None:
    """The same sentence's concurrency range is 1 to `MAX_BATCH_WORKERS`, at every copy of it.

    The floor is the `ge=1` both `max_concurrency` fields declare beside `le=MAX_BATCH_WORKERS`
    (`api/app.py`, `batch_contracts.py`). As with the item cap, finding no such range fails
    rather than passes, and a range this test cannot read fails as unreadable.
    """
    occurrences = list(BATCH_WORKER_RANGE.finditer(readme))
    assert occurrences, (
        "could not find the concurrency range in README.md's batch-API sentence (the words "
        "'个不可变请求放入持久队列，以 <floor>-<ceiling> 的受控并发'). Either the wording "
        "changed -- update BATCH_WORKER_RANGE to match it -- or the claim was removed, in which "
        "case remove this test with it."
    )
    problems: list[str] = []
    for occurrence in occurrences:
        stated = " ".join(occurrence["stated"].split())
        line = readme.count("\n", 0, occurrence.start("stated")) + 1
        bounds = STATED_RANGE.fullmatch(stated)
        if bounds is None:
            problems.append(
                f"README.md:{line} gives the batch concurrency range as {stated!r}, which is not "
                "a '<floor>-<ceiling>' range of ASCII digits this test reads."
            )
            continue
        floor, ceiling = int(bounds[1]), int(bounds[2])
        if (floor, ceiling) != (1, MAX_BATCH_WORKERS):
            problems.append(
                f"README.md:{line} states a batch concurrency range of {floor}-{ceiling}, but "
                f"max_concurrency accepts 1-{MAX_BATCH_WORKERS} "
                "(Field(ge=1, le=MAX_BATCH_WORKERS)). Update the sentence to the constant rather "
                "than the other way around."
            )
    assert not problems, "\n".join(problems)


_FULL_WIDTH_DIGITS: Final[str] = f"{chr(0xFF10)}-{chr(0xFF19)}"
"""The full-width digits as a character-class range, spelled with `chr` because RUF001 refuses
them as look-alikes of ASCII digits; `int()` reads them as it reads ASCII ones."""

_DIGIT: Final[str] = f"[0-9{_FULL_WIDTH_DIGITS}]"

_NOT_BESIDE: Final[str] = f"[A-Za-z0-9_.{_FULL_WIDTH_DIGITS}]"

_JOINER: Final[str] = f"[{re.escape(RANGE_JOINERS)}]"

WORKER_RANGE_IN_PROSE: Final[re.Pattern[str]] = re.compile(
    rf"(?<!{_NOT_BESIDE})(?<!{_DIGIT}{_JOINER})"
    rf"(?:between\s+({_DIGIT}+)\s+and\s+({_DIGIT}+)"
    rf"|({_DIGIT}+)\s*(?:{_JOINER}|到|至|\s+to\s+)\s*({_DIGIT}+))"
    rf"(?!{_NOT_BESIDE})(?!\s*{_JOINER}\s*{_DIGIT})",
    re.IGNORECASE,
)
"""Any `<a>-<b>` range in ASCII or full-width digits: joined by one of `RANGE_JOINERS`, by 到,
by 至 or by "to", or written "between <a> and <b>".

Looser than `STATED_RANGE` on purpose: it runs over every clause about concurrency in the
documents `WORKER_RANGE_DOCUMENTS` names, and a range written `1到8` there must be read and
checked rather than skipped. A letter, a digit, `_`
or `.` directly beside either number keeps an identifier such as `V2-P4-019` from reading as the
range 4-19, and a number that a dash joins to a third number -- a date such as 2026-08-18 -- is
not read as a range at all.
"""

CONCURRENCY_WORDS: Final[tuple[str, ...]] = ("并发", "并行", "concurren", "worker")
"""What puts a clause in scope for the worker-range check, matched case-insensitively."""


def _worker_range_mentions(document: str) -> list[tuple[int, int, int, str]]:
    """Every range in a clause of `document` that names concurrency: line, floor, ceiling, clause.

    Clauses come from `tests/prose_clauses.py`, so a range and its 并发 split by a soft wrap are
    read as one clause.
    """
    found: list[tuple[int, int, int, str]] = []
    for clause in clauses(document):
        if any(word in clause.text.lower() for word in CONCURRENCY_WORDS):
            for match in WORKER_RANGE_IN_PROSE.finditer(clause.text):
                floor, ceiling = (int(bound) for bound in match.groups() if bound is not None)
                found.append((clause.line, floor, ceiling, clause.text))
    return found


def _worker_range_problems(documents: dict[str, str]) -> list[str]:
    """One message per range in `documents` that is not 1 to `MAX_BATCH_WORKERS`."""
    return [
        f"{name}:{line} states a batch concurrency range of {floor}-{ceiling}, but "
        f"max_concurrency accepts 1-{MAX_BATCH_WORKERS} (Field(ge=1, le=MAX_BATCH_WORKERS)): "
        f"{clause!r}"
        for name, document in documents.items()
        for line, floor, ceiling, clause in _worker_range_mentions(document)
        if (floor, ceiling) != (1, MAX_BATCH_WORKERS)
    ]


WORKER_RANGE_DOCUMENTS: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA, MARKETING)
"""The four user-facing documents whose worker ranges are read.

The READMEs have been read since `D7`. The marketing pack states the range four times, in
sections 008, 036, 061 and 065, and until `D13` no test read any of them: writing 1-32 into
section 061 or 065 left this module green (measured on `d4ef5e4`).
"""

MUST_STATE_A_WORKER_RANGE: Final[frozenset[Path]] = frozenset({README, MARKETING})
"""The documents that state a range today, so a reader that stops seeing them fails.

`README.en.md` and `docs/why-openalpha-cn.zh-CN.md` state none today. A range either comes to
state is read and checked like any other, and its absence is not a failure.
"""


def _document_name(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _worker_range_failures(documents: dict[str, str]) -> list[str]:
    """Why `documents`, keyed by repository path, fail the worker-range check, or `[]`.

    Every document in `MUST_STATE_A_WORKER_RANGE` must yield at least one range -- a missing key
    counts as a document with none -- and every range in any document must be 1 to
    `MAX_BATCH_WORKERS`.
    """
    blind = [
        f"no worker range found in any {name} clause about concurrency; it stated one when this "
        "was written, so either the reader has gone blind or the range was reworded out of reach"
        for name in sorted(_document_name(path) for path in MUST_STATE_A_WORKER_RANGE)
        if not _worker_range_mentions(documents.get(name, ""))
    ]
    return blind + _worker_range_problems(documents)


def _worker_range_documents() -> dict[str, str]:
    return {
        _document_name(path): path.read_text(encoding="utf-8") for path in WORKER_RANGE_DOCUMENTS
    }


def test_every_worker_range_the_documents_state_is_the_one_the_api_enforces() -> None:
    """Every worker range the four user-facing documents state is 1 to `MAX_BATCH_WORKERS`.

    `README.md` states the range three times -- the feature table, the feature list and the
    batch-API sentence -- and until `D7` nothing read the first two. The marketing pack states it
    four times, and until `D13` nothing read any of them. A clause is in scope when it names 并发,
    并行, "concurren..." or "worker...", and every range in it is read, in any form
    `WORKER_RANGE_IN_PROSE` reads. `README.en.md` and `docs/why-openalpha-cn.zh-CN.md` state no
    worker range today, so they add nothing until they do.

    Finding no range in `README.md` or in the marketing pack fails: a change that stops this
    reader seeing the ranges it sees today has to break the test, not empty it. What it cannot
    see: a worker count written without a range ("最多 8 路并发", "up to 8 workers", or section
    065's hook, "允许 8 并发"), or a range in a clause that names none of `CONCURRENCY_WORDS`;
    `test_the_worker_range_reader_reads_what_its_docstring_says` measures both.
    """
    failures = _worker_range_failures(_worker_range_documents())
    assert not failures, "\n".join(failures)


def test_a_stale_range_planted_in_each_of_the_four_documents_is_reported() -> None:
    """The test above reads each of the four documents, not only the ones that state a range today.

    A stale range is appended to each real document in turn, in memory, and the check the test
    above runs must report it in that document. A document dropped from `WORKER_RANGE_DOCUMENTS`
    fails here instead of passing unread.
    """
    documents = _worker_range_documents()
    planted = f"\n\n持久任务队列支持 1{RANGE_JOINERS[1]}32 并发。\n"
    unreported: list[str] = []
    for path in (README, README_EN, WHY_OPENALPHA, MARKETING):
        name = _document_name(path)
        failures = _worker_range_failures({**documents, name: documents[name] + planted})
        if not any(failure.startswith(f"{name}:") for failure in failures):
            unreported.append(name)
    assert not unreported, f"a stale range planted in these documents went unreported: {unreported}"


def test_the_worker_range_reader_reads_what_its_docstring_says() -> None:
    """Stale ranges in each form must be reported; counts, other ranges and identifiers must not.

    This is what holds the check itself. The real documents state 1-8 wherever they state a
    range, so the test above cannot tell a check of both bounds from a check of one, and cannot
    show that a `README.md` or a marketing pack with no range fails; the `whole` cases below do.
    """
    dash = RANGE_JOINERS[1]
    stale = {
        "a stale ceiling": f"持久批量队列 1{dash}32 并发、逐项进度",
        "a stale floor": f"持久任务队列支持 2{dash}8 并发。",
        "a range written with 到": "持久任务队列支持 1到32 路并发。",
        "English, soft-wrapped": "- bounded batches of 1-32\n  concurrent requests;\n",
        "English 'to'": "- bounded concurrent batches of 1 to 32 requests;\n",
        "English 'between ... and'": "Between 1 and 32 workers run at once.\n",
        "并行 instead of 并发": f"持久任务队列支持 1{dash}32 路并行。",
        "a minus sign": f"持久任务队列支持 1{chr(0x2212)}32 并发。",
        "a figure dash": f"持久任务队列支持 1{chr(0x2012)}32 并发。",
        "a wave dash": f"持久任务队列支持 1{chr(0x301C)}32 并发。",
        "full-width digits": (
            f"持久任务队列支持 {chr(0xFF11)}{dash}{chr(0xFF13)}{chr(0xFF12)} 并发。"
        ),
    }
    missed = [label for label, text in stale.items() if not _worker_range_problems({label: text})]
    ignored = {
        "a count without a range": "最多 8 路并发。",
        "an English count without a range": "up to 8 workers.",
        "a range in a clause about something else": f"每批 1{dash}32 个标的。",
        "an identifier": "V2-P4-019 lowered the worker ceiling.",
    }
    wrongly = {
        label: _worker_range_mentions(text)
        for label, text in ignored.items()
        if _worker_range_mentions(text)
    }
    valid = f"持久任务队列支持 1{dash}{MAX_BATCH_WORKERS} 并发。"
    readme, marketing = _document_name(README), _document_name(MARKETING)
    whole = {
        "a README.md with no range": {readme: "持久任务队列支持并发。", marketing: valid},
        "a marketing pack with no range": {readme: valid, marketing: "持久任务队列支持并发。"},
        "a stale range in README.en.md": {
            readme: valid,
            marketing: valid,
            _document_name(README_EN): "- bounded batches of 1-32 concurrent requests;\n",
        },
    }
    unreported = [label for label, docs in whole.items() if not _worker_range_failures(docs)]
    clean = _worker_range_failures({readme: valid, marketing: valid})
    dated = f"自 2026-08-18 起支持 1{dash}{MAX_BATCH_WORKERS} 并发。"
    dated_read = [(floor, ceiling) for _, floor, ceiling, _ in _worker_range_mentions(dated)]
    assert not missed and not wrongly and not unreported and not clean, (
        f"missed stale ranges: {missed}; misread: {wrongly}; whole-check cases not reported: "
        f"{unreported}; a clean pair reported: {clean}"
    )
    assert dated_read == [(1, MAX_BATCH_WORKERS)], (
        f"a date beside a valid range was read as {dated_read}, not the one range it holds"
    )


DIAGRAM_GENERATORS: Final[tuple[Path, ...]] = (
    ROOT / "scripts" / "generate_brain_diagrams.py",
    ROOT / "scripts" / "generate_api_relationship_diagrams.py",
)
"""The generators of the ten diagrams `README.md` embeds; `tests/unit/test_repository_assets.py`
holds every committed SVG equal to what they write."""

ITEM_RANGE_IN_DIAGRAMS: Final[re.Pattern[str]] = re.compile(
    rf"(?<!{_NOT_BESIDE})1\s*{_JOINER}\s*({_DIGIT}+)\s*个不可变请求"
)
"""A batch item range as the diagrams draw it: `1-<n> 个不可变请求`."""


def _diagram_ceiling_problems(sources: dict[str, str]) -> list[str]:
    """Every batch ceiling the diagram `sources` draw that is not the one the API enforces.

    Each string literal is read alone (`diagram_strings`): a worker range in one that names 并发,
    并行, "concurren..." or "worker...", and an item range in one that reads `1-<n> 个不可变请求`.
    Finding neither kind fails too, so a reader gone blind cannot pass.
    """
    worker_ranges: list[tuple[str, int, int, int]] = []
    item_caps: list[tuple[str, int, int]] = []
    for name, source in sources.items():
        for string in diagram_strings(source):
            if any(word in string.text.lower() for word in CONCURRENCY_WORDS):
                for match in WORKER_RANGE_IN_PROSE.finditer(string.text):
                    floor, ceiling = (int(bound) for bound in match.groups() if bound is not None)
                    worker_ranges.append((name, string.line, floor, ceiling))
            item_caps.extend(
                (name, string.line, int(match[1]))
                for match in ITEM_RANGE_IN_DIAGRAMS.finditer(string.text)
            )
    problems: list[str] = []
    if not worker_ranges:
        problems.append("no diagram string draws a worker range; three did when this was written")
    if not item_caps:
        problems.append("no diagram string draws a batch item range; one did when this was written")
    problems.extend(
        f"{name}:{line} draws a batch concurrency range of {floor}-{ceiling}, but "
        f"max_concurrency accepts 1-{MAX_BATCH_WORKERS}"
        for name, line, floor, ceiling in worker_ranges
        if (floor, ceiling) != (1, MAX_BATCH_WORKERS)
    )
    problems.extend(
        f"{name}:{line} draws a batch item cap of {cap}, but MAX_BATCH_ITEMS is {MAX_BATCH_ITEMS}"
        for name, line, cap in item_caps
        if cap != MAX_BATCH_ITEMS
    )
    return problems


def test_every_batch_ceiling_the_diagrams_draw_is_the_one_the_api_enforces() -> None:
    """The embedded diagrams draw the batch ceilings too, and nothing read them until `D12`.

    brain-01, brain-03 and api-03 drew the worker range as 1-32 and api-03 drew the item cap as
    1-1000, the numbers `README.md` carried before `74cee0f` and `e758f0a` corrected its prose.
    """
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in DIAGRAM_GENERATORS
    }
    problems = _diagram_ceiling_problems(sources)
    assert not problems, (
        "\n".join(problems) + "\nFix the generator, then regenerate the SVG with the generator "
        "itself."
    )


def test_the_diagram_ceiling_reader_reports_what_its_docstring_says() -> None:
    """Stale ceilings are reported, current ones are not, and drawing neither kind fails."""
    current = (
        f'svg.card(lines=("1-{MAX_BATCH_ITEMS} 个不可变请求", "1-{MAX_BATCH_WORKERS} 并发"))\n'
    )
    stale = 'svg.card(lines=("1-1000 个不可变请求", "1-32 CONCURRENCY"))\n'
    blind = 'svg.card(lines=("持久批量研究",))\n'
    assert not _diagram_ceiling_problems({"current.py": current}), "current ceilings reported"
    assert len(_diagram_ceiling_problems({"stale.py": stale})) == 2, "a stale ceiling unreported"
    assert len(_diagram_ceiling_problems({"blind.py": blind})) == 2, "an empty read passed"
