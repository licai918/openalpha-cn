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
NOW: Final[datetime] = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


@pytest.fixture
def http_doc() -> str:
    return HTTP_DOC.read_text(encoding="utf-8")


@pytest.fixture
def changelog() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


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
