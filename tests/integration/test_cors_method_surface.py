"""The CORS method list must cover what this service actually serves (`V2-P5-011`).

`api/app.py` pinned `allow_methods=["GET", "POST"]` by hand, and the roadmap row states the
consequence as a v2 risk: a `PUT`/`DELETE`/`PATCH` route added later would be refused by the
browser before it ever reached FastAPI.

**Measured on `c847295`, the list is already narrower than the route table it guards, which is
sharper than the row.** Driving `create_app()` through `TestClient`, a preflight naming each
method answers:

| requested method | status |
|---|---|
| `GET` | `200` |
| `POST` | `200` |
| `HEAD` | `400 Disallowed CORS method` |
| `PUT` / `PATCH` / `DELETE` | `400 Disallowed CORS method` |

`HEAD` is refused **today**, and the application declares four `HEAD` routes (FastAPI adds one
to every `GET`). Nothing is broken by that in practice -- `HEAD` is a CORS-safelisted method, so
a browser never preflights it -- but it is the proof that the hand-written list and the route
table had no way of staying in step, which is the defect the row is really about.

So the guard here is not "PUT is allowed" alone. It is
`test_every_method_the_route_table_declares_survives_a_preflight`, which reads the methods off
the running application and would go red the day a route declares one the CORS layer does not.

**A claim this file used to make, and the test that falsified it.** The first version of this
docstring said `allow_methods=["*"]` and the explicit tuple were observationally identical --
that Starlette expanded `"*"` to the same list -- and therefore that no assertion could separate
them. Measured on `starlette 1.3.1`, that is **false**: `"*"` expands to `ALL_METHODS`, which is
`DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT`, while the explicit tuple advertises the six
without `OPTIONS`, and `Access-Control-Allow-Methods` carries the difference verbatim.
`test_the_documented_method_list_is_the_one_a_preflight_is_told` reads that header and the
document together, and it is what found the error: the document had been written from the same
false belief and the test went red on it.

**What is also asserted** is the pair of things widening the methods must not quietly take
with it: the origin allow-list and the credentials flag. CORS is not authorization -- this
service has none (audit `F101`) -- so the method list is a compatibility surface, while the
origin list is the part that actually decides who may talk to a browser-hosted caller.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app

NOW: Final[datetime] = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
ALLOWED_ORIGIN: Final[str] = "http://127.0.0.1:5173"
FOREIGN_ORIGIN: Final[str] = "https://console.example.invalid"

V2_WRITE_METHODS: Final[tuple[str, ...]] = ("PUT", "PATCH", "DELETE")
"""The three the row names. None is served today; all three are what a v2 REST face would add."""


def _preflight(client: TestClient, method: str, *, origin: str = ALLOWED_ORIGIN):
    """One browser CORS preflight for `method`, exactly as a browser issues it."""
    return client.options(
        "/api/v1/evidence",
        headers={
            "origin": origin,
            "access-control-request-method": method,
            "access-control-request-headers": "content-type",
        },
    )


def test_a_v2_write_method_is_not_refused_before_it_reaches_the_route(tmp_path: Path) -> None:
    """`PUT`, `PATCH` and `DELETE` must clear the preflight the browser sends first.

    On `c847295` each of the three answered `400 Disallowed CORS method` -- the row's claim,
    reproduced. The assertion is on the advertised method list as well as the status, because a
    `200` whose `Access-Control-Allow-Methods` omits the requested method is not something a
    browser will act on.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))

    for method in V2_WRITE_METHODS:
        response = _preflight(client, method)
        assert response.status_code == 200, f"{method}: {response.text}"
        advertised = {
            part.strip()
            for part in response.headers.get("access-control-allow-methods", "").split(",")
        }
        assert method in advertised, f"{method} is not in {advertised}"


def test_every_method_the_route_table_declares_survives_a_preflight(tmp_path: Path) -> None:
    """The guard that cannot go stale: the CORS list is read against the routes themselves.

    A hand-written list is a second statement of the route table, and the two had already
    diverged before anyone added a `PUT`: `HEAD` is on four routes and was refused. This test
    derives the question from the application under test, so a thirteenth route declaring a
    method the CORS layer does not allow arrives red rather than arriving in a browser.
    """
    application = create_app(runtime_dir=tmp_path, clock=lambda: NOW)
    client = TestClient(application)

    served = {
        method
        for route in application.routes
        for method in (getattr(route, "methods", None) or ())
        if method != "OPTIONS"
    }
    assert served >= {"GET", "HEAD", "POST"}, (
        f"the route table stopped being recognisable: {served}"
    )

    refused = {method for method in sorted(served) if _preflight(client, method).status_code != 200}
    assert refused == set(), (
        f"these methods are served by a route and refused by CORS: {sorted(refused)}"
    )


def test_widening_the_methods_did_not_widen_the_origins(tmp_path: Path) -> None:
    """An origin outside the allow-list is still refused, for a widened method too.

    This is the assertion that keeps `V2-P5-011` from being "fixed" by `allow_origins=["*"]`,
    which would pass every other test in this file. It is checked on `DELETE` specifically --
    the method that could not be preflighted at all before -- so it exercises the new path
    rather than the old one.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))

    response = _preflight(client, "DELETE", origin=FOREIGN_ORIGIN)

    assert response.status_code == 400, response.text
    assert "Disallowed CORS origin" in response.text
    assert "access-control-allow-origin" not in response.headers

    simple = client.get("/health", headers={"origin": FOREIGN_ORIGIN})
    assert simple.status_code == 200
    assert "access-control-allow-origin" not in simple.headers


def test_the_widened_surface_still_carries_no_credentials(tmp_path: Path) -> None:
    """`allow_credentials` stays off, which is what makes a fixed origin list meaningful.

    A widened method set plus credentials is the combination that turns a permissive CORS
    configuration into a cross-site request forgery surface. This service authenticates nothing
    (audit `F101`), so there is no cookie to send -- and this assertion is what keeps that true
    if one ever arrives.
    """
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))

    for method in ("GET", *V2_WRITE_METHODS):
        response = _preflight(client, method)
        assert "access-control-allow-credentials" not in response.headers, method


def test_the_documented_method_list_is_the_one_a_preflight_is_told(tmp_path: Path) -> None:
    """`docs/api/http.md` states the method list; the preflight is what a browser believes.

    Read off both sides rather than written down here, for
    `test_the_documented_header_table_is_the_one_the_service_sends`'s reason: a table nobody
    checks is a description of whichever version was current when it was typed.
    """
    doc = (Path(__file__).resolve().parents[2] / "docs" / "api" / "http.md").read_text(
        encoding="utf-8"
    )
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: NOW))

    advertised = _preflight(client, "GET").headers["access-control-allow-methods"]

    assert f"`{advertised}`" in doc, (
        f"the preflight advertises {advertised!r} and the document does not say so"
    )
    for origin in (ALLOWED_ORIGIN, "http://localhost:5173"):
        assert f"`{origin}`" in doc, f"{origin} is allowed and not documented"
