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

## What the mount changed that nobody had asked it about (`V2-P5-030`)

Everything above was asked with `GET` and with one spelling of each segment. Two questions were
never put, and the answers to both were wrong. Measured through **raw ASGI** -- building the
`scope` by hand -- against a `create_app(web_dir=None)` baseline, because `httpx` normalises
some of these paths before the request is made:

    METHOD    PATH                 WITH MOUNT             NO MOUNT
    POST      /api/v1/nope         405 application/json   404 application/json
    DELETE    /api/v1/nope         405                    404
    OPTIONS   /api/v1/nope         405                    404
    GET       /API/v1/nope         200 text/html          404
    GET       /api./v1/nope        200 text/html          404
    GET       /api /v1/nope        200 text/html          404
    GET       /Assets/missing.js   200 text/html          404

So mounting the build changed what the API's own namespace answers, in two ways at once: a
`405` claims the path exists, and a case typo gets a page. Both are the sentence this fallback
was written to prevent, arriving through doors nobody had opened.

**`//api/v1/nope` is not one of them, and was first reported as though it were.** It looks like
an HTML `200` through a client; raw ASGI shows the server answering `404 application/json`.
`httpx` collapses the leading `//` and sends `/v1/nope`, so what the client measured was its
own URL handling. That is the reason the probes for this row were written against `scope`
directly, and the reason it is written down here: the next reader of a suspicious `200` should
check the client before the server.

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

from openalpha_cn.api.app import SinglePageFallbackFiles, create_app

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


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_an_unknown_api_path_is_a_refusal_under_every_method_and_never_a_405(
    client: TestClient, method: str
) -> None:
    """`405` says the resource exists and declines the verb. For a typo, both halves are false.

    `test_an_unknown_api_path_stays_a_json_refusal` asks with `GET` and only `GET`, and the
    class's own reasoning about method handling was written about `POST /portfolio` -- a
    *client* address, where `405` is the honest answer because the page really is there.
    Nobody asked the same question about the API's namespace. Measured through raw ASGI against
    a `create_app(web_dir=None)` baseline:

        POST    /api/v1/nope   405 application/json   <- with the mount
        POST    /api/v1/nope   404 application/json   <- without it

    So mounting the build changed what a misspelled, renamed or retired API path answers, and
    changed it into a claim about that path existing. A caller that branches on `404` to mean
    "this endpoint is gone" reads `405` as "still there, my verb is wrong" and retries forever.

    `OPTIONS` is in the list because a CORS preflight to a wrong path is exactly the shape of
    this mistake, and `V2-P5-011` is this repository's standing lesson about method surfaces.
    """
    response = client.request(method, "/api/v1/no-such-route")

    assert response.status_code == 404, f"{method} -> {response.status_code}"
    assert response.headers["content-type"].startswith("application/json")
    assert SHELL_MARKER not in response.text


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_a_write_to_a_missing_subresource_is_not_a_405_either(
    client: TestClient, method: str
) -> None:
    """`assets/` is the mount's own namespace, and `missing.js` is still not in it.

    The same lie one namespace over: `405` on a file that does not exist says the deploy is
    fine and the verb is wrong, when the deploy is broken. The rule that fixes both is that a
    `405` may only be returned for something that is actually there --
    `test_a_write_to_a_file_that_is_in_the_build_is_still_a_405` is its other side.
    """
    response = client.request(method, "/assets/index-does-not-exist.js")

    assert response.status_code == 404
    assert SHELL_MARKER not in response.text


def test_a_write_to_a_file_that_is_in_the_build_is_still_a_405(client: TestClient) -> None:
    """The half of the method rule that must not be lost while fixing the other half.

    `index-abc123.js` is really in the build, so "this resource does not take `POST`" is a true
    sentence about it. An implementation that answered `404` for every non-`GET` under a
    build directory would pass the two tests above by deleting the distinction they are about.
    """
    response = client.post("/assets/index-abc123.js")

    assert response.status_code == 405
    assert SHELL_MARKER not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/API/v1/nope",
        "/Api/v1/nope",
        "/api./v1/nope",
        "/api /v1/nope",
        "/HEALTH/no-such-thing",
        "/Docs/no-such-thing",
        "/OpenAPI.json/no-such-thing",
    ],
)
def test_a_reserved_segment_is_claimed_however_a_sloppy_caller_spelled_it(
    client: TestClient, path: str
) -> None:
    """The owner sets were compared with `==` on the raw segment, so `/API/` was a page.

        GET /API/v1/nope   -> 200 text/html    <- baseline, `create_app(web_dir=None)` gives 404
        GET /api./v1/nope  -> 200 text/html
        GET /api /v1/nope  -> 200 text/html

    This is the exact sentence the class docstring says it exists to prevent -- "a client-side
    typo, a stale caller ... comes back as an HTML `200`, and a caller that branches on
    `response.ok` reads a page as a payload" -- and a case typo is a client-side typo.

    Matching a *normalised* segment is deliberately broader than HTTP, where paths are
    case-sensitive and `/API/v1/nope` genuinely is a different resource from `/api/v1/nope`.
    The trade is deliberate and it is not symmetric: the cost of being broad is that a client
    area named `Api` would be shadowed, and
    `test_every_client_area_is_an_address_the_production_server_serves` goes red naming it the
    day anyone writes one. The cost of being narrow is silent, permanent, and paid by whoever
    is reading a stack trace about a page that arrived where JSON was expected.

    Trailing dots and spaces are stripped for the same reason and not a different one: they are
    what a permissive filesystem and a sloppy config file both drop, so `api.` and `api ` are
    ways of writing `api` that no client meant as a page.
    """
    response = client.get(path)

    assert response.status_code == 404, f"{path} -> {response.status_code}"
    assert SHELL_MARKER not in response.text, f"{path} was served the shell"


@pytest.mark.parametrize("path", ["/Assets/missing.js", "/ASSETS/missing.js", "/assets./x.js"])
def test_a_build_directory_is_claimed_however_it_is_spelled(client: TestClient, path: str) -> None:
    """The half of the case defect that behaves differently on the developer's machine.

    macOS is case-insensitive, so `/Assets/index-abc123.js` resolves to the real file locally
    and the fallback is never consulted; on the Linux the `Dockerfile` ships, the lookup fails,
    `Assets` is not `assets`, and the shell is served with `text/html` for something a
    `<script>` tag asked for. That is precisely the MIME-type error the class docstring says
    the build-directory owner exists to prevent, and it is invisible in development.

    Asserted on a name that is missing under either spelling, so the assertion is about the
    fallback's decision rather than about the filesystem the test happens to run on.
    """
    response = client.get(path)

    assert response.status_code == 404, f"{path} -> {response.status_code}"
    assert SHELL_MARKER not in response.text, f"{path} was served the shell"


def test_both_owner_sets_are_normalised_when_they_are_taken_in_and_not_only_on_the_way_out(
    tmp_path: Path,
) -> None:
    """The comparison has two sides, and today's tree only ever exercises one of them.

    Every reserved root the live route table produces is already lower case with no trailing
    punctuation (`api`, `health`, `docs`, `redoc`, `openapi.json`), and the only build directory
    is `assets`. So normalising the *incoming* segment is enough on this tree, and a mutation
    sweep says so: dropping `_normalised_segment` from either set's construction left all of
    `tests/unit/test_spa_addressability.py` green.

    It is not enough in general, and the failure is the one that matters rather than a
    cosmetic one. A route registered at `/API/...` would put `API` in the reserved set, an
    incoming `/api/v1/nope` would normalise to `api`, the two would not match, and **the API's
    own namespace would start answering as a page** -- the exact defect this class exists to
    prevent, arriving through the half of the comparison nobody was checking. A bundler that
    emits `Assets/` does the same to the build side.

    Driven by constructing the class directly, because neither spelling can be reached through
    `create_app` on this tree and a test that waited for one would be a test that never ran.
    """
    build = tmp_path / "dist"
    (build / "Assets").mkdir(parents=True)
    (build / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    files = SinglePageFallbackFiles(directory=build, reserved_roots=frozenset({"API", "Health."}))

    owned_spellings = (
        "API/v1/nope",
        "api/v1/nope",
        "Health./x",
        "health/x",
        "Assets/a.js",
        "assets/a.js",
    )
    for owned in owned_spellings:
        assert files._is_a_client_location(owned) is False, owned
    for area in ("portfolio", "data-health", "factor-lab/fxp_abc"):
        assert files._is_a_client_location(area) is True, area


@pytest.mark.parametrize("path", ["/.../nope", "/. . ./nope", "/..../nope"])
def test_a_segment_that_is_only_punctuation_is_not_a_location(
    client: TestClient, path: str
) -> None:
    """`_normalised_segment` strips trailing dots and spaces, so a segment of nothing else
    normalises to the empty string -- and the empty string is not a place the router shows.

    `.` and `..` were already refused by name. These are the shapes the normalisation itself
    creates, and a mutation sweep found nothing holding them: turning the empty-root branch
    from `False` to `True` left every other case green, because no test asked for a path whose
    first segment survives `{"", ".", ".."}` and then normalises away.
    """
    response = client.get(path)

    assert response.status_code == 404, f"{path} -> {response.status_code}"
    assert SHELL_MARKER not in response.text


def test_a_client_area_is_not_swallowed_by_the_normalisation(client: TestClient) -> None:
    """Normalising the segment must claim more spellings of the owners and nothing else.

    A rule that casefolded everything into one bucket would start refusing client addresses;
    this is the non-vacuity of the two tests above, asserted on the areas that must keep
    working with their own capitalisation intact.
    """
    for path in ("/data-health", "/factor-lab/fxp_abc", "/portfolio"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        assert SHELL_MARKER in response.text


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
