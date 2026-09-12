"""The container security posture `docs/deployment/production.zh-CN.md` §8 claims, held against
the two files that produce it: `deploy/compose.yml` and the `Dockerfile`.

§8 says the container runs as UID/GID `10001`, on a read-only root filesystem, with
`cap_drop: ALL` and `no-new-privileges:true`, and that only `/data` is persistently writable while
`/tmp` is a restricted tmpfs. Two of those used to be held by substring assertions --
`"read_only: true" in compose` -- which `# read_only: true` satisfies just as well, and the other
three by nothing at all.

This module is the static half: what the two files *declare*. It cannot see what Compose makes of
the file (interpolation, override files, `extends`), the image the Dockerfile builds, or the
kernel's view of the running process. That is the runtime half,
`scripts/verify_compose_recovery.py`, which judges the container Compose actually starts -- from
`/proc/<pid>/status`, `/proc/self/mounts`, real write attempts and the account files -- and which
is the step CI's `container` job runs.

## Why the compose file is read by a hand-written reader

No YAML library is a dependency of this repository, runtime or development, and a new dependency is
a supply-chain decision: too large a one to take for reading one file in a test. The file is
written in a small block-style subset of YAML, so `_read_compose` reads exactly that subset --
mappings and sequences by indentation, plain and quoted scalars, folded and literal block scalars,
whole-line and trailing comments -- and raises `ComposeSubsetError` on anything else: flow
collections, anchors, aliases, merge keys, tags, document markers, tab indentation, and sequences
not indented under their key. A rewrite of the file into a construct the reader does not understand
therefore turns these tests red rather than green, and
`test_the_reader_refuses_what_it_does_not_understand` holds that.

What the reader cannot do is interpret the file the way Compose does. It does not substitute
`${VARIABLES}`, apply a `compose.override.yml`, or know that Compose also accepts `True` for `true`
(this reader calls that a broken claim: a false alarm, never a false pass). The running container is
the judge of those, and the runtime half judges it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "compose.yml"
DOCKERFILE = ROOT / "Dockerfile"


class ComposeSubsetError(ValueError):
    """`deploy/compose.yml` uses a YAML construct `_read_compose` does not read."""


_KEY_LINE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+):(?: +(?P<rest>.*))?$")
_BLOCK_SCALAR = re.compile(r"^[|>][+-]?$")
# A value opening a flow collection, an anchor, an alias or a tag (or a reserved indicator).
_UNREAD_VALUE = re.compile(r"^[\[{&*!%@`]")


def _without_comment(line: str) -> str:
    """`line` up to a `#` that opens a comment: at the start or after a space, outside quotes."""
    quote = ""
    for position, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif character in "'\"" and (position == 0 or line[position - 1] == " "):
            quote = character
        elif character == "#" and (position == 0 or line[position - 1] == " "):
            return line[:position]
    return line


def _indent(lines: list[str], index: int) -> int:
    content = lines[index].lstrip(" ")
    if content.startswith("\t"):
        raise ComposeSubsetError(f"line {index + 1}: indented with a tab")
    return len(lines[index]) - len(content)


def _next_node(lines: list[str], index: int) -> int:
    """The index of the next line that carries structure: not blank, not only a comment."""
    while index < len(lines) and not _without_comment(lines[index]).strip():
        index += 1
    return index


def _scalar(text: str, line: int) -> str:
    if _UNREAD_VALUE.match(text):
        raise ComposeSubsetError(
            f"line {line}: {text!r} is a flow collection, anchor, alias or tag"
        )
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"' and "\\" not in text:
        return text[1:-1]
    if text[0] in "'\"":
        raise ComposeSubsetError(
            f"line {line}: {text!r} is quoted in a way this reader does not read"
        )
    if ": " in text or text.endswith(":"):
        raise ComposeSubsetError(f"line {line}: {text!r} is a mapping where a scalar belongs")
    return text


def _value(lines: list[str], index: int, indent: int, rest: str) -> tuple[object, int]:
    """The node after `key:` or `-` on line `index`, and the index of the line after it."""
    if _BLOCK_SCALAR.match(rest):
        body: list[str] = []
        index += 1
        while index < len(lines) and (not lines[index].strip() or _indent(lines, index) > indent):
            body.append(lines[index].strip())
            index += 1
        return (" " if rest[0] == ">" else "\n").join(body).strip(), index
    if rest:
        return _scalar(rest, index + 1), index + 1
    child = _next_node(lines, index + 1)
    if child < len(lines) and _indent(lines, child) > indent:
        return _node(lines, child)
    return None, index + 1


def _node(lines: list[str], index: int) -> tuple[object, int]:
    content = _without_comment(lines[index]).strip()
    if content == "-" or content.startswith("- "):
        return _sequence(lines, index, _indent(lines, index))
    return _mapping(lines, index, _indent(lines, index))


def _mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, object], int]:
    mapping: dict[str, object] = {}
    while index < len(lines) and (depth := _indent(lines, index)) >= indent:
        content = _without_comment(lines[index]).strip()
        match = _KEY_LINE.match(content)
        if depth > indent or match is None:
            raise ComposeSubsetError(f"line {index + 1}: {content!r} is not a key of its mapping")
        if match["key"] in mapping:
            raise ComposeSubsetError(f"line {index + 1}: {match['key']!r} appears twice")
        mapping[match["key"]], index = _value(lines, index, indent, match["rest"] or "")
        index = _next_node(lines, index)
    return mapping, index


def _sequence(lines: list[str], index: int, indent: int) -> tuple[list[object], int]:
    items: list[object] = []
    while index < len(lines) and (depth := _indent(lines, index)) >= indent:
        content = _without_comment(lines[index]).strip()
        if depth > indent or not (content == "-" or content.startswith("- ")):
            raise ComposeSubsetError(
                f"line {index + 1}: {content!r} is not an item of its sequence"
            )
        rest = content[1:].strip()
        if _KEY_LINE.match(rest):
            raise ComposeSubsetError(f"line {index + 1}: {rest!r} is a mapping inside a sequence")
        item, index = _value(lines, index, indent, rest)
        items.append(item)
        index = _next_node(lines, index)
    return items, index


def _read_compose(text: str) -> dict[str, object]:
    """`text` as dicts, lists and strings, for the block-style subset described above."""
    lines = text.splitlines()
    start = _next_node(lines, 0)
    if start == len(lines) or _indent(lines, start) != 0:
        raise ComposeSubsetError("the file does not open with a top-level mapping")
    document, end = _node(lines, start)
    if end != len(lines) or not isinstance(document, dict):
        raise ComposeSubsetError(f"line {end + 1}: the top-level mapping ends before the file does")
    return document


def _service(compose_text: str) -> dict[str, object]:
    services = _read_compose(compose_text).get("services")
    assert isinstance(services, dict), "deploy/compose.yml has no `services:` mapping"
    service = services.get("openalpha")
    assert isinstance(service, dict), "deploy/compose.yml has no `openalpha` service"
    return service


# --- §8, as `deploy/compose.yml` declares it ----------------------------------------------------


def _no_user_override(service: dict[str, object]) -> bool:
    """No `user:`, so the image's own `USER 10001:10001` is what runs -- held by
    `test_the_dockerfile_declares_every_container_property_section_8_claims`."""
    return service.get("user", "10001:10001") == "10001:10001"


def _read_only_root(service: dict[str, object]) -> bool:
    return service.get("read_only") == "true"


def _every_capability_dropped(service: dict[str, object]) -> bool:
    return (
        service.get("cap_drop") == ["ALL"]
        and "cap_add" not in service
        and service.get("privileged", "false") == "false"
    )


def _no_new_privileges(service: dict[str, object]) -> bool:
    return service.get("security_opt") == ["no-new-privileges:true"]


def _only_data_and_a_bounded_tmp(service: dict[str, object]) -> bool:
    """Exactly one volume, on `/data`, and exactly one tmpfs, `/tmp`, capped at 64 MB."""
    return service.get("volumes") == ["openalpha-runtime:/data"] and service.get("tmpfs") == [
        "/tmp:size=64m,mode=1777"
    ]


SECTION_8_COMPOSE_CLAIMS: dict[str, Callable[[dict[str, object]], bool]] = {
    "UID/GID 10001": _no_user_override,
    "read-only root filesystem": _read_only_root,
    "cap_drop: ALL": _every_capability_dropped,
    "no-new-privileges:true": _no_new_privileges,
    "only /data persistently writable, /tmp a restricted tmpfs": _only_data_and_a_bounded_tmp,
}


@pytest.mark.parametrize("claim", list(SECTION_8_COMPOSE_CLAIMS))
def test_compose_declares_every_container_property_section_8_claims(claim: str) -> None:
    assert SECTION_8_COMPOSE_CLAIMS[claim](_service(COMPOSE_FILE.read_text(encoding="utf-8"))), (
        f"deploy/compose.yml no longer declares what production.zh-CN.md §8 claims: {claim}"
    )


def _moved_under_healthcheck(text: str) -> str:
    moved = text.replace("    read_only: true\n", "", 1)
    return moved.replace("    healthcheck:\n", "    healthcheck:\n      read_only: true\n", 1)


def _replaced(original: str, replacement: str) -> Callable[[str], str]:
    def mutate(text: str) -> str:
        assert text.count(original) == 1, f"the mutation's anchor {original!r} is not unique"
        return text.replace(original, replacement)

    return mutate


@pytest.mark.parametrize(
    ("claim", "mutate"),
    [
        pytest.param(
            "read-only root filesystem",
            _replaced("    read_only: true\n", "    # read_only: true\n"),
            id="read_only-commented-out",
        ),
        pytest.param(
            "read-only root filesystem",
            _replaced("    read_only: true\n", "    read_only: false\n"),
            id="read_only-false",
        ),
        pytest.param("read-only root filesystem", _moved_under_healthcheck, id="read_only-moved"),
        pytest.param(
            "cap_drop: ALL",
            _replaced("    cap_drop:\n      - ALL\n", ""),
            id="cap_drop-removed",
        ),
        pytest.param(
            "cap_drop: ALL",
            _replaced("      - ALL\n", "      # - ALL\n"),
            id="cap_drop-emptied",
        ),
        pytest.param(
            "cap_drop: ALL",
            _replaced("      - ALL\n", "      - ALL\n    cap_add:\n      - NET_RAW\n"),
            id="cap_add-alongside",
        ),
        pytest.param(
            "no-new-privileges:true",
            _replaced("      - no-new-privileges:true\n", "      # - no-new-privileges:true\n"),
            id="no-new-privileges-commented-out",
        ),
        pytest.param(
            "no-new-privileges:true",
            _replaced("no-new-privileges:true", "no-new-privileges:false"),
            id="no-new-privileges-false",
        ),
        pytest.param(
            "only /data persistently writable, /tmp a restricted tmpfs",
            _replaced("      - /tmp:size=64m,mode=1777\n", "      - /tmp\n"),
            id="tmpfs-unbounded",
        ),
        pytest.param(
            "only /data persistently writable, /tmp a restricted tmpfs",
            _replaced(
                "      - openalpha-runtime:/data\n",
                "      - openalpha-runtime:/data\n      - ./logs:/app/logs\n",
            ),
            id="second-writable-mount",
        ),
        pytest.param(
            "UID/GID 10001",
            _replaced("    init: true\n", '    init: true\n    user: "0:0"\n'),
            id="user-root",
        ),
    ],
)
def test_a_compose_file_that_breaks_a_claim_fails_that_claim(
    claim: str, mutate: Callable[[str], str]
) -> None:
    """Each edit here breaks one §8 claim, and the claim's check has to notice.

    Three of them leave the old assertion's substring exactly where it was -- commenting
    `read_only: true` out, moving it under another key, commenting out
    `no-new-privileges:true` -- which is what `"read_only: true" in compose` could not tell apart
    from the real thing.
    """
    mutant = mutate(COMPOSE_FILE.read_text(encoding="utf-8"))

    assert not SECTION_8_COMPOSE_CLAIMS[claim](_service(mutant))


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_replaced("    read_only: true\n", "    # read_only: true\n"), id="commented"),
        pytest.param(_moved_under_healthcheck, id="moved"),
        pytest.param(
            _replaced("      - no-new-privileges:true\n", "      # - no-new-privileges:true\n"),
            id="no-new-privileges-commented",
        ),
    ],
)
def test_the_substring_checks_this_module_replaced_could_not_see_these_edits(
    mutate: Callable[[str], str],
) -> None:
    """The measurement behind replacing them: both old substrings survive each of these edits."""
    mutant = mutate(COMPOSE_FILE.read_text(encoding="utf-8"))

    assert "read_only: true" in mutant
    assert "no-new-privileges:true" in mutant


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("services:\n  openalpha:\n    cap_drop: [ALL]\n", id="flow-sequence"),
        pytest.param("services:\n  openalpha: {read_only: true}\n", id="flow-mapping"),
        pytest.param(
            "x-hardening: &hardening\n  read_only: true\n"
            "services:\n  openalpha:\n    <<: *hardening\n",
            id="anchor-and-merge",
        ),
        pytest.param("services:\n  openalpha:\n    read_only: !!bool true\n", id="tag"),
        pytest.param("services:\n  openalpha:\n    cap_drop:\n    - ALL\n", id="compact-sequence"),
        pytest.param("services:\n  openalpha:\n\tread_only: true\n", id="tab-indentation"),
        pytest.param("---\nservices:\n  openalpha:\n    read_only: true\n", id="document-marker"),
    ],
)
def test_the_reader_refuses_what_it_does_not_understand(text: str) -> None:
    """Refusing is what makes the reader safe to build a claim on: a construct it guessed at
    could read as the claim holding when it does not."""
    with pytest.raises(ComposeSubsetError):
        _read_compose(text)


def test_container_delivery_has_persistence_and_recovery_verification() -> None:
    """Moved here from `tests/unit/test_repository_assets.py`, where it was three substrings."""
    compose = _read_compose(COMPOSE_FILE.read_text(encoding="utf-8"))
    volumes = compose.get("volumes")

    assert isinstance(volumes, dict)
    assert "openalpha-runtime" in volumes
    assert _service(COMPOSE_FILE.read_text(encoding="utf-8")).get("volumes") == [
        "openalpha-runtime:/data"
    ]
    assert (ROOT / "scripts" / "verify_compose_recovery.py").is_file()


# --- §8, as the `Dockerfile` declares it --------------------------------------------------------


def _runtime_stage(dockerfile: str) -> list[tuple[str, str]]:
    """The `runtime` stage's instructions as `(NAME, arguments)`.

    Continuations are joined and comment lines dropped, as Docker's own parser does -- so an
    instruction that has been commented out is not in the result, and neither is prose that
    happens to mention one.
    """
    instructions: list[tuple[str, str]] = []
    pending = ""
    for raw in dockerfile.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}" if pending else line
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        name, _, arguments = pending.partition(" ")
        instructions.append((name.upper(), arguments.strip()))
        pending = ""
    starts = [
        position
        for position, (name, arguments) in enumerate(instructions)
        if name == "FROM" and arguments.lower().endswith(" as runtime")
    ]
    assert len(starts) == 1, "the Dockerfile has no single `FROM ... AS runtime` stage"
    stage = instructions[starts[0] + 1 :]
    ends = [position for position, (name, _) in enumerate(stage) if name == "FROM"]
    return stage[: ends[0]] if ends else stage


def _runs_as_10001(stage: list[tuple[str, str]]) -> bool:
    """The stage's last `USER` is what the process runs as, and it is numeric, so it does not
    depend on the account existing at all."""
    users = [arguments for name, arguments in stage if name == "USER"]
    return bool(users) and users[-1] == "10001:10001"


def _account_is_10001(stage: list[tuple[str, str]]) -> bool:
    """The account the stage creates carries the same ids, which is what makes the files it
    `chown`s to `openalpha` -- `/data`, the virtualenv, the web build -- the process's own. An
    account at 10002 leaves the process at 10001 (the `USER` above is numeric) and `/data` owned
    by someone else."""
    runs = " ".join(arguments for name, arguments in stage if name == "RUN")
    ids = re.findall(r"--(uid|gid)[= ](\d+)", runs)
    return {kind for kind, _ in ids} == {"uid", "gid"} and {value for _, value in ids} == {"10001"}


def _volumes_declared(stage: list[tuple[str, str]]) -> list[str]:
    """Every path a `VOLUME` in the stage names, in either of the two forms Docker accepts."""
    volumes: list[str] = []
    for name, arguments in stage:
        if name == "VOLUME":
            volumes.extend(
                json.loads(arguments) if arguments.startswith("[") else arguments.split()
            )
    return volumes


def _only_the_data_volume(stage: list[tuple[str, str]]) -> bool:
    """A `VOLUME` is a persistent mount every container of the image gets, whether compose.yml
    names it or not, filled from the image with the image's ownership. The D9 review's
    `VOLUME ["/data", "/app"]` made `/app/.venv` a persistent directory the account owns while
    every compose claim above still held."""
    return _volumes_declared(stage) == ["/data"]


SECTION_8_DOCKERFILE_CLAIMS: dict[str, Callable[[list[tuple[str, str]]], bool]] = {
    "the process runs as USER 10001:10001": _runs_as_10001,
    "the account it runs as is uid 10001 in gid 10001": _account_is_10001,
    "the image declares no volume but /data": _only_the_data_volume,
}


@pytest.mark.parametrize("claim", list(SECTION_8_DOCKERFILE_CLAIMS))
def test_the_dockerfile_declares_every_container_property_section_8_claims(claim: str) -> None:
    stage = _runtime_stage(DOCKERFILE.read_text(encoding="utf-8"))

    assert SECTION_8_DOCKERFILE_CLAIMS[claim](stage), (
        f"the Dockerfile's runtime stage no longer declares: {claim}"
    )


@pytest.mark.parametrize(
    ("claim", "mutate"),
    [
        pytest.param(
            "the process runs as USER 10001:10001",
            _replaced("USER 10001:10001\n", "USER 10002:10002\n"),
            id="user-10002",
        ),
        pytest.param(
            "the process runs as USER 10001:10001",
            _replaced("USER 10001:10001\n", "# USER 10001:10001\n"),
            id="user-commented-out",
        ),
        pytest.param(
            "the account it runs as is uid 10001 in gid 10001",
            _replaced("--uid 10001", "--uid 10002"),
            id="account-uid-10002",
        ),
        pytest.param(
            "the account it runs as is uid 10001 in gid 10001",
            _replaced("--gid 10001", "--gid 10002"),
            id="account-gid-10002",
        ),
        pytest.param(
            "the image declares no volume but /data",
            _replaced('VOLUME ["/data"]\n', 'VOLUME ["/data", "/app"]\n'),
            id="volume-app",
        ),
        pytest.param(
            "the image declares no volume but /data",
            _replaced('VOLUME ["/data"]\n', "VOLUME /data /app\n"),
            id="volume-app-shell-form",
        ),
    ],
)
def test_a_dockerfile_that_breaks_a_claim_fails_that_claim(
    claim: str, mutate: Callable[[str], str]
) -> None:
    stage = _runtime_stage(mutate(DOCKERFILE.read_text(encoding="utf-8")))

    assert not SECTION_8_DOCKERFILE_CLAIMS[claim](stage)
