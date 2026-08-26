"""Every address the web app has, measured through the server that actually ships it.

`V2-P5-027`. `web/e2e/routing.spec.ts` proves the client router resolves these addresses, and
it proves it against `vite dev`, which serves `index.html` for anything it cannot match. The
server `openalpha serve` runs does not: `api/app.py` mounts `StaticFiles(html=True)`, and
Starlette's `html=True` falls back to `index.html` only for *directory* requests. Measured on
`12532e3` with a `TestClient` over the real `create_app` and a real `pnpm build`:

    /                    -> 200  shell=True
    /data-health         -> 404  shell=False
    /shortlists          -> 404  shell=False
    /shortlists/sl_abc   -> 404  shell=False
    /factor-lab          -> 404  shell=False
    /factor-lab/fxp_abc  -> 404  shell=False
    /portfolio           -> 404  shell=False

So every bookmarkable URL pages ① through ④ added worked in development and 404'd in
production, and the e2e suite structurally could not see it, because the thing that answered
in development was the dev server rather than the application. That is the gap this module
stands in: it is the only place that asks the *production* server for those addresses.

## The rule, and the two owners that are exempt from it

A `GET`/`HEAD` the build cannot answer is answered with `index.html` at `200`, **unless its
first path segment belongs to somebody else**. Two owners exist, and neither is written down
here or in `app.py` -- both are derived, so a new route or a new build directory is covered
without anybody remembering to add it:

- **the live route table's own first segments** (`api`, `health`, `docs`, `redoc`,
  `openapi.json` today). An unknown path under one of them stays a `404` in that owner's own
  vocabulary. This is the load-bearing half: without it every client-side typo against
  `/api/v1/...` becomes an HTML `200`, and a caller that checks `response.ok` reads a page as
  a payload.
- **a real directory inside the build** (`assets` today). A subresource that is missing is a
  corrupted deploy, and answering it with HTML converts a clean `404` into a MIME-type error
  at the far end, several layers from the cause.

## What was measured about `vite dev` rather than assumed

The obvious specification for this fallback is "do what development does". What development
does was measured on this build (`vite 8.1.5`, port 5199, `curl`), and two of the three
things it was assumed to do are false:

    /data-health          -> 200  shell=True
    /no-such-page         -> 200  shell=True
    /no-such.page         -> 200  shell=True    <- no dot rule
    /stocks/000001.SZ     -> 200  shell=True    <- no dot rule
    /assets/missing.js    -> 200  shell=True    <- a missing subresource is HTML
    /favicon.ico          -> 404                <- special-cased by name in vite
    /api/v1/no-such-route -> 502                <- the dev *proxy*, not the fallback

So `vite` has no extension rule at all, and its protection of `/api` comes from
`vite.config.ts`'s hand-written proxy list (`/api`, `/health`, `/openapi.json` -- already
missing `/docs` and `/redoc`), not from the fallback. This module therefore does **not** copy
development on the two points where copying it would ship a defect: an unknown `/api/` path
stays JSON here, and a missing file under a build directory stays a `404` here. Everywhere
else the two agree, which is the whole point of the row.

## The one thing deliberately not asserted

Nothing here keys on the `Accept` header. `vite` does (`text/html` or `*/*` or absent), and a
rule that answers `curl` differently from a browser is a rule that makes a production incident
unreproducible from the command line. The cost is that a missing extensionless file outside a
build directory renders as the shell; the in-app `NotFoundPage` names the address, so what the
user sees is still "no such location" rather than a blank page.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient

from openalpha_cn.api.app import create_app

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CLIENT_ROUTES_MODULE: Final[Path] = REPO_ROOT / "web" / "src" / "routes.ts"

SHELL_MARKER: Final[str] = '<div id="root">'
"""The one byte-sequence that separates "the shell was served" from "something was served".

`web/index.html`'s mount point. Asserting a `200` alone cannot tell the shell from a stray
asset, and asserting the whole file cannot survive a rebuild.
"""

CLIENT_PATH_LITERAL: Final[re.Pattern[str]] = re.compile(r"""["`](/[^"`$\s]*)""")
"""Every absolute path literal in `web/src/routes.ts`, string or template alike.

A template literal stops at its first `${`, so ``/shortlists/${encodeURIComponent(id)}``
yields `/shortlists/` -- which is all this module needs, since what it checks is the *first
segment* and whether the server has already claimed it.
"""

EXPECTED_CLIENT_ROOTS: Final[frozenset[str]] = frozenset(
    {"", "data-health", "shortlists", "factor-lab", "portfolio"}
)
"""The client router's root segments, as an equality rather than a floor.

A floor ("at least these are served") stays green when a sixth area lands on a segment the
server has already claimed, which is the single way this fallback can silently stop covering a
page. An equality goes red naming the new segment and makes its author look at the reserved
set. Measured off `routes.ts` at `12532e3`.
"""


@pytest.fixture(name="web_build")
def _web_build() -> Iterator[Path]:
    """A build with the two shapes that matter: the shell, and a subresource directory.

    Synthetic rather than `web/dist`, so this runs in `tests/unit` without a `pnpm build`
    having happened. The properties it needs from a real build are only these two, and the
    real build is exercised end to end by `web/e2e/production-routing.spec.ts`.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "dist"
        (root / "assets").mkdir(parents=True)
        (root / "index.html").write_text(
            f"<!doctype html><html><body>{SHELL_MARKER}</div></body></html>",
            encoding="utf-8",
        )
        (root / "assets" / "index-abc123.js").write_text("export const built = true;\n")
        yield root


@pytest.fixture(name="client")
def _client(web_build: Path) -> Iterator[TestClient]:
    """The real application, with storage in a temp directory rather than `runtime/`."""
    with tempfile.TemporaryDirectory() as directory:
        application = create_app(runtime_dir=Path(directory) / "runtime", web_dir=web_build)
        with TestClient(application) as opened:
            yield opened


def _client_root_segments() -> set[str]:
    text = CLIENT_ROUTES_MODULE.read_text(encoding="utf-8")
    return {literal.strip("/").split("/")[0] for literal in CLIENT_PATH_LITERAL.findall(text)}


def test_the_client_routes_this_module_stands_for_are_the_ones_on_disk() -> None:
    """The extraction itself, before anything is asserted with it.

    Without this the regex could match nothing and every parametrised case below would range
    over an empty set while printing green -- the failure mode `V2-P4-038` names for a floor.
    """
    assert _client_root_segments() == set(EXPECTED_CLIENT_ROOTS)


@pytest.mark.parametrize("segment", sorted(EXPECTED_CLIENT_ROOTS))
def test_every_client_area_is_an_address_the_production_server_serves(
    client: TestClient, segment: str
) -> None:
    """Each area's own address, and one child address under it.

    The child matters separately: `/shortlists` is a directory-shaped request that Starlette's
    `html=True` would already have answered, while `/shortlists/sl_abc` is the one that 404'd.
    """
    for path in (f"/{segment}", f"/{segment}/probe-id".replace("//", "/")):
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        assert SHELL_MARKER in response.text, f"{path} served something that is not the shell"
        assert response.headers["content-type"].startswith("text/html"), path


def test_an_unknown_location_reaches_the_shell_so_the_router_can_name_it(
    client: TestClient,
) -> None:
    """`AppRouter`'s `NotFoundPage` is the only thing that knows this app's route table.

    The server deliberately does not hold a second copy of it. A path list restated in Python
    is the defect `V2-P5-011` measured on `CORS_ALLOWED_METHODS`: two statements of one fact,
    with nothing keeping them equal, and the copy already behind the original.
    """
    response = client.get("/no-such-page")

    assert response.status_code == 200
    assert SHELL_MARKER in response.text


def test_an_unknown_api_path_stays_a_json_refusal(client: TestClient) -> None:
    """The load-bearing negative. An HTML `200` here turns every client typo into a page.

    This is the case a naive fallback gets wrong, and it was watched going red against one:
    with `get_response` falling back to `index.html` for any `404`, this test failed with
    `200` and `shell=True` while every test above it passed.
    """
    response = client.get("/api/v1/no-such-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert SHELL_MARKER not in response.text


def test_every_prefix_the_route_table_claims_refuses_in_its_own_vocabulary(
    client: TestClient,
) -> None:
    """Derived from the live routes, so a new route family is covered without a new line here.

    Replacing the derivation with a written-down `{"api"}` leaves this red on `/health/...`,
    `/docs/...`, `/redoc/...` and `/openapi.json/...`, which is the difference between a rule
    and a list.
    """
    claimed = {
        route.path.split("/")[1]
        for route in client.app.routes  # type: ignore[attr-defined]
        if isinstance(getattr(route, "path", None), str) and route.path.count("/") >= 1
    }
    claimed.discard("")

    assert claimed == {"api", "health", "docs", "redoc", "openapi.json"}
    for segment in sorted(claimed):
        response = client.get(f"/{segment}/no-such-thing-under-here")
        assert response.status_code == 404, f"/{segment}/... -> {response.status_code}"
        assert SHELL_MARKER not in response.text, f"/{segment}/... was served the shell"


def test_a_missing_subresource_is_a_refusal_rather_than_html(client: TestClient) -> None:
    """`assets/` exists in the build, so a file that is not in it is a broken deploy.

    `vite dev` serves the shell here (measured, see this module's docstring). Copying it would
    hand the browser `text/html` for a `<script>` and surface a MIME error instead of a `404`.
    """
    response = client.get("/assets/index-does-not-exist.js")

    assert response.status_code == 404
    assert SHELL_MARKER not in response.text


@pytest.mark.parametrize("path", ["/%2e%2e/etc/passwd", "/%2e%2e%2fetc/passwd", "/..%2fetc/passwd"])
def test_an_escape_attempt_is_not_dressed_up_as_a_location(client: TestClient, path: str) -> None:
    """A path that climbs out of the build is not a page, so it does not get the page.

    Percent-encoded, because `httpx` normalises a literal `../` away before the request is
    made and the assertion would then be about the client rather than the server. Encoded, the
    raw path reaches Starlette, which decodes it into `scope["path"]` and hands `..` straight
    to the mount.

    Nothing is *leaked* either way -- `StaticFiles` refuses to serve outside its directory --
    so what this pins is the answer's shape: a `404`, not an HTML `200` that tells a scanner
    the address exists.
    """
    response = client.get(path)

    assert response.status_code == 404
    assert SHELL_MARKER not in response.text


def test_a_file_that_is_in_the_build_is_still_served_from_the_build(client: TestClient) -> None:
    """The fallback must not shadow the files it exists to complement."""
    response = client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert "export const built = true;" in response.text


def test_a_write_to_a_client_address_is_not_answered_with_a_page(client: TestClient) -> None:
    """`POST /portfolio` is a mistake, and a `200` full of HTML is the wrong way to say so.

    `StaticFiles` refuses non-`GET`/`HEAD` with `405` before it ever reports a missing file, so
    the fallback never sees it -- asserted here because that ordering is the reason, and an
    implementation that caught every `HTTPException` instead of only `404` would lose it.
    """
    response = client.post("/portfolio")

    assert response.status_code == 405
    assert SHELL_MARKER not in response.text


def test_a_head_request_to_a_client_address_reports_the_shell(client: TestClient) -> None:
    """A browser prefetch and a link checker both use `HEAD`; both must see the page exist."""
    response = client.head("/factor-lab")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_serving_without_a_build_leaves_the_api_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `web_dir` means no mount and no fallback -- the shape every API-only test runs in.

    `create_app()` with no build is what `tests/unit/test_surface_parity.py` measures the route
    table through, so a fallback that registered itself unconditionally would change what that
    file counts.

    `OPENALPHA_WEB_DIR` is cleared rather than trusted to be unset: `create_app(web_dir=None)`
    reads it through `load_config()`, so a developer who exports it would otherwise get a
    *mounted* application here and a green run that measured the opposite of the claim.
    """
    monkeypatch.delenv("OPENALPHA_WEB_DIR", raising=False)
    with tempfile.TemporaryDirectory() as directory:
        application = create_app(runtime_dir=Path(directory) / "runtime", web_dir=None)
        with TestClient(application) as opened:
            response = opened.get("/data-health")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
