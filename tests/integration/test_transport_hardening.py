"""The transport must meter what it reads and say what it is (`V2-P5-012`).

Two audit findings, one middleware.

**`F100` -- the request-size check read `Content-Length` and nothing else**, so a chunked request
bypassed it completely. Reproduced on `c847295` through `TestClient`: against a deliberately tiny
**1,024-byte** ceiling, a chunked `POST /api/v1/research/batches` carrying **36,000,030 bytes**
was answered `422 json_invalid` -- a *parser* verdict, which is only reachable once the whole body
has already been read. `tracemalloc` measured a **108,346,472-byte** peak for that single request
(three times the body: Starlette accumulates the chunks in a list, then joins them). That is the
memory-exhaustion consequence, measured rather than argued.

**`F102` -- three headers missing and the rest appended rather than replaced.**
`Strict-Transport-Security`, `Cross-Origin-Embedder-Policy` and `Cross-Origin-Resource-Policy`
were absent; and a route setting its own `x-frame-options: SAMEORIGIN` produced two raw header
lines, which a browser reads as `SAMEORIGIN, DENY`. Also measured on `c847295`.

## Why two different clients drive the same middleware

`TestClient` is what the rest of this suite uses and it is the surface a caller meets, but it
materialises a generator body *before* the application runs (`starlette.testclient`'s `receive`
calls `httpx.Request.read()`), so the whole body arrives in one `http.request` message. Through
it, "the meter counts the body it is handed" is provable and "the meter stops reading" is not --
the pull count is the same under every implementation, which would be an assertion that cannot
separate the two answers.

So the streaming half is driven through `httpx2.ASGITransport`, which pulls one chunk per
`receive` and therefore *can* tell a middleware that stops reading from one that drains first.
Same application object, same middleware, real HTTP client; only the body's laziness differs.
Measured on the fix: **1 of 400 chunks pulled**, 100 KB read instead of 40 MB.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Final

import httpx2
import pytest
import uvicorn
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import HSTS_MAX_AGE_SECONDS, create_app
from openalpha_cn.cli import app as cli_app

CEILING: Final[int] = 4_096
"""Small on purpose: the defect is about the *rule*, not about the default's size.

`tests/integration/test_request_body_ceiling.py` is where the real default is measured against
the two item ceilings this service declares. Driving the rule at 4 KiB keeps every case here
under a second.
"""

CHUNK: Final[bytes] = b"z" * 100_000

REQUIRED_HEADERS: Final[dict[str, str]] = {
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-embedder-policy": "require-corp",
    "cross-origin-resource-policy": "same-origin",
    "strict-transport-security": f"max-age={HSTS_MAX_AGE_SECONDS}; includeSubDomains",
}
"""Every hardening header, with the three `F102` named last.

The first six are asserted alongside the new three deliberately: `tests/integration/
test_evidence_interfaces.py` checks three of them on `/health`, and a middleware rewritten to add
headers is exactly the change that could drop one on the way.
"""

runner = CliRunner()


def _chunked_body(chunks: int) -> Iterator[bytes]:
    for _ in range(chunks):
        yield CHUNK


def test_a_chunked_body_past_the_ceiling_is_refused_rather_than_parsed(tmp_path: Path) -> None:
    """The row's own witness, at the surface a caller uses.

    The assertion is `413` **and** `reason == "request_too_large"`, because the `422` this
    answered on `c847295` is also a refusal -- what made it a defect is that it was the JSON
    parser's refusal, reached only after the body it was refusing had been read in full. A test
    asserting merely "not 2xx" would have been green on the broken code.

    `content-length` is asserted absent on the outgoing request, because a body that declared its
    length was never the bypass; if the client ever starts declaring one, this test would silently
    become a second copy of `test_request_body_ceiling.py` instead of the chunked case.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))

    response = client.post(
        "/api/v1/research/batches",
        content=_chunked_body(4),
        headers={"content-type": "application/json"},
    )

    assert "content-length" not in response.request.headers, dict(response.request.headers)
    assert response.request.headers["transfer-encoding"] == "chunked"
    assert response.status_code == 413, response.text[:300]
    detail = response.json()["detail"]
    assert detail["reason"] == "request_too_large"
    assert detail["limit_bytes"] == CEILING
    assert detail["declared_bytes"] is None, "nothing was declared, so nothing may be reported"
    assert detail["measured_bytes"] > CEILING
    assert "OPENALPHA_MAX_REQUEST_BYTES" in detail["message"]


def test_the_meter_stops_pulling_the_stream_instead_of_draining_it(tmp_path: Path) -> None:
    """Metering, not buffering: the transport is never asked for the rest of the body.

    This is the assertion that separates "count as it arrives and stop" from "read it all, then
    notice it was too big" -- and the second of those is a fix that leaves the memory
    consequence exactly where it was. `httpx2.ASGITransport` pulls one chunk per `receive`, so
    the count of chunks the generator was asked for is a direct measurement of how much of a
    40 MB body this service agreed to hold.

    The bound is two chunks rather than one so the assertion is about the rule and not about
    where `CEILING` happens to fall inside a chunk.
    """
    application = create_app(runtime_dir=tmp_path, max_request_bytes=CEILING)
    pulled = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal pulled
        for _ in range(400):
            pulled += 1
            yield CHUNK

    async def scenario() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/v1/research/batches",
                content=body(),
                headers={"content-type": "application/json"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 413, response.text[:300]
    assert pulled <= 2, (
        f"the meter read {pulled * len(CHUNK)} bytes of a {400 * len(CHUNK)}-byte body against a "
        f"{CEILING}-byte ceiling: it drained the stream rather than stopping at the limit"
    )


def test_a_chunked_body_at_the_ceiling_still_arrives_whole(tmp_path: Path) -> None:
    """The guard against closing `F100` by refusing every undeclared body.

    A body of exactly `CEILING` bytes, sent chunked, must reach the route and be parsed. It is
    padded with the whitespace JSON allows between tokens, so the byte count is exact while the
    document stays valid, and the expected answer is the route's *own* refusal --
    `declared_ceiling_exceeded` on `requests`, which `requests: Field(min_length=1)` issues.

    That answer is the point. A middleware that truncated at the ceiling would leave the parser
    a broken document and the refusal would be `json_invalid`; a middleware that refused all
    chunked bodies would answer `413`. Only a body that arrived whole reaches a complaint about
    the *contents* of `requests`.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))
    document = json.dumps({"batch_id": "at-the-ceiling", "requests": [], "max_concurrency": 1})
    padded = document[:-1] + " " * (CEILING - len(document)) + "}"
    assert len(padded.encode()) == CEILING

    response = client.post(
        "/api/v1/research/batches",
        content=iter([padded.encode()]),
        headers={"content-type": "application/json"},
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 422, f"{response.status_code}: {response.text[:300]}"
    detail = response.json()["detail"]
    assert detail["reason"] == "declared_ceiling_exceeded", detail
    assert detail["field"] == "requests"


def test_the_two_gates_issue_one_refusal_a_client_can_switch_on(tmp_path: Path) -> None:
    """`docs/api/http.md` makes `detail.reason` the key a client switches on, so it must not fork.

    Both refusals carry the same `reason` and the same `limit_bytes`; what tells them apart is
    which of `declared_bytes`/`measured_bytes` is non-null, and exactly one always is. The
    declared side keeps `declared_bytes` meaning what the document already says it means, so the
    shape is added to rather than changed.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))

    declared = client.post(
        "/api/v1/research/batches",
        content=b"x" * (CEILING + 1),
        headers={"content-type": "application/json"},
    ).json()["detail"]
    streamed = client.post(
        "/api/v1/research/batches",
        content=_chunked_body(1),
        headers={"content-type": "application/json"},
    ).json()["detail"]

    assert declared["reason"] == streamed["reason"] == "request_too_large"
    assert declared["limit_bytes"] == streamed["limit_bytes"] == CEILING
    assert declared["declared_bytes"] == CEILING + 1
    assert declared["measured_bytes"] is None
    assert streamed["declared_bytes"] is None
    assert streamed["measured_bytes"] is not None
    assert "floor" in streamed["message"], (
        "a number read off a stream that was cut is a lower bound, and saying otherwise would "
        "invite a caller to size their next request against it"
    )


def test_every_answer_carries_the_headers_the_audit_named(tmp_path: Path) -> None:
    """`F102`'s three, plus the six that were already there and must survive the rewrite."""
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))

    response = client.get("/health")

    assert response.status_code == 200
    for name, expected in REQUIRED_HEADERS.items():
        assert response.headers.get(name, "").startswith(expected), (
            f"{name}: {response.headers.get(name)!r}"
        )


def test_a_route_setting_a_policy_header_is_replaced_not_appended_to(tmp_path: Path) -> None:
    """`F102`'s second half, made observable.

    No route in this application sets one of these headers today, so appending and replacing are
    indistinguishable on the shipped route table -- which is why the difference went unnoticed.
    A route that sets one is added here to make the two answers separable: under the old
    behaviour the response carried two raw `x-frame-options` lines and a browser read
    `SAMEORIGIN, DENY` (measured on `c847295`); under replacement there is one line and it is
    this service's own.

    The route is added to a real `create_app()` application and driven through `TestClient`, so
    the middleware under test is the installed one.
    """
    application = create_app(runtime_dir=tmp_path, max_request_bytes=CEILING)

    @application.get("/probe/opinionated")
    def opinionated() -> PlainTextResponse:
        return PlainTextResponse("ok", headers={"x-frame-options": "SAMEORIGIN"})

    response = TestClient(application).get("/probe/opinionated")

    assert response.status_code == 200
    lines = [value for name, value in response.headers.raw if name.lower() == b"x-frame-options"]
    assert lines == [b"DENY"], lines


def test_a_browser_can_read_the_oversize_refusal(tmp_path: Path) -> None:
    """The `413` must pass through the CORS layer, or a browser only sees a network failure.

    `SecurityHeadersMiddleware` short-circuits, and while it sat *outside* `CORSMiddleware` that
    short circuit skipped the layer that adds `Access-Control-Allow-Origin`. So the refusal
    `V2-P4-043` worded so carefully -- which number was exceeded, which variable raises it --
    was unreadable by exactly the caller most likely to hit it. The two are now registered the
    other way round.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))
    origin = "http://127.0.0.1:5173"

    response = client.post(
        "/api/v1/research/batches",
        content=b"x" * (CEILING + 1),
        headers={"content-type": "application/json", "origin": origin},
    )

    assert response.status_code == 413, response.text[:200]
    assert response.headers.get("access-control-allow-origin") == origin


def test_openalpha_serve_does_not_announce_its_server_software(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`F102`: `--no-server-header` was passed in the `Dockerfile` and not by `openalpha serve`.

    So the same application banner-advertised `uvicorn` in one deployment and not the other, and
    the one that leaked is the one a developer runs.

    `uvicorn.Server.run` is what is stubbed, not `uvicorn.run`: that leaves `uvicorn.Config`
    genuinely constructed from the keyword arguments `cli.py` passes, so a misspelled keyword is
    a real `TypeError` here rather than something a mock would absorb, and `server_header` is
    read back off the object uvicorn itself would have served with. `config.load()` is never
    called, so no application is built.
    """
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(self: uvicorn.Server, sockets: object = None) -> None:
        captured["config"] = self.config
        self.started = True

    monkeypatch.setattr(uvicorn.Server, "run", _capture)

    result = runner.invoke(cli_app, ["serve", "--host", "127.0.0.1", "--port", "8123"])

    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.server_header is False, "uvicorn will send `server: uvicorn` unless told not to"
    assert config.host == "127.0.0.1"
    assert config.port == 8123


def test_the_documented_header_table_is_the_one_the_service_sends(tmp_path: Path) -> None:
    """`docs/api/http.md` now lists all nine, and a table that drifts is worse than no table.

    Both sides are read rather than written: the values come off a live `/health` response and
    the document is searched for each of them verbatim. So changing a header without changing
    the table -- or changing the table without changing a header -- is red, which is the only
    thing that makes documentation a claim rather than a description of some earlier version.
    """
    doc = (Path(__file__).resolve().parents[2] / "docs" / "api" / "http.md").read_text(
        encoding="utf-8"
    )
    response = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING)).get(
        "/health"
    )

    for name in REQUIRED_HEADERS:
        sent = response.headers[name]
        assert f"`{name}`" in doc, f"{name} is sent and not documented"
        assert f"`{sent}`" in doc, f"{name} is documented with a value it does not send: {sent!r}"


def test_a_refusal_this_middleware_decides_is_hardened_like_any_other_answer(
    tmp_path: Path,
) -> None:
    """The `413` and the `400` short-circuit the application, and must not short-circuit these.

    A mutation sweep is why this exists: inverting the `== "http.response.start"` test inside
    the refusal path -- so the headers landed on the body message instead of the start -- left
    every other case in this file green, because they all read headers off a `200`. A refusal is
    exactly the answer a browser is most likely to render, and one with no content policy on it
    is the gap this middleware exists to close.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))

    too_large = client.post(
        "/api/v1/research/batches",
        content=b"x" * (CEILING + 1),
        headers={"content-type": "application/json"},
    )
    streamed = client.post(
        "/api/v1/research/batches",
        content=_chunked_body(1),
        headers={"content-type": "application/json"},
    )

    assert too_large.status_code == 413
    assert streamed.status_code == 413
    for response in (too_large, streamed):
        for name, expected in REQUIRED_HEADERS.items():
            assert response.headers.get(name, "").startswith(expected), (
                f"{response.status_code} carries no {name}"
            )


def test_an_unparseable_content_length_is_refused_before_the_body_is_read(tmp_path: Path) -> None:
    """`400`, and it is this middleware's `400` rather than the framework's.

    This branch predates `V2-P5-012` and had no test at all -- the sweep found its status code
    and its message both free to change. It is the one refusal here that is *not* about size:
    a `Content-Length` that is not a number is a malformed request, and answering `413` would
    tell a caller to send less of something it never successfully declared.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, max_request_bytes=CEILING))

    response = client.post(
        "/api/v1/research/batches",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "not-a-number"},
    )

    assert response.status_code == 400, response.text[:200]
    assert response.json()["detail"] == "Invalid Content-Length header."
    assert response.headers["x-frame-options"] == "DENY"
