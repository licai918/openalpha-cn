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
prose behind -- which is precisely the failure mode this row *is*. Every number asserted here is
read from `batch_contracts`/`config` at run time and then required to appear in the prose, and
each is paired with the live `422`/`413` from a real request, so the documentation and the
behaviour cannot drift apart without one of these going red.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app
from openalpha_cn.batch_contracts import MAX_BATCH_ITEMS, MAX_BATCH_WORKERS
from openalpha_cn.config import OpenAlphaConfig, load_config

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
HTTP_DOC: Final[Path] = ROOT / "docs" / "api" / "http.md"
CHANGELOG: Final[Path] = ROOT / "CHANGELOG.md"
README: Final[Path] = ROOT / "README.md"
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
    assert str(MAX_BATCH_ITEMS) in http_doc or "10,000" in http_doc


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
``str(MAX_BATCH_ITEMS) in http_doc or "10,000" in http_doc``: true the moment the right digits
appear *anywhere* in `docs/api/http.md`, including two paragraphs away from a stale claim. And
until `e758f0a` nothing in this module read `README.md` at all.

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
use different words and are not read by this module.

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

RANGE_JOINERS: Final[str] = "".join(("-", chr(0x2013), chr(0x2014), "~", chr(0xFF5E)))
"""A hyphen, an en dash, an em dash, a tilde and a full-width tilde.

Spelled with `chr` because ruff's RUF001 refuses the literal dash and full-width tilde as
look-alikes of `-` and `~`.
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
