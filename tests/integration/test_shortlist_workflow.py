"""Run it, run it again tomorrow, compare the two, and defend the difference.

`V2-P4-071`, `V2-P4-062` and `V2-P4-049` are three rows and one workflow. This file drives the
whole of it from shipped surfaces only -- `CliRunner`, `TestClient` and `OpenAlphaSDK` -- because
each of the three was filed after a product acceptance found the library sound and the *product*
unreachable, and a test that imported `openalpha_cn.shortlist_view` and called it directly would
pass on a tree where none of the three is fixed.

## The five steps, and which row blocks each

1. Build a cross section at day 1 and run a shortlist against it. Worked before this file.
2. Advance to a second instant in the **same partition year, in a second invocation**.
   `V2-P4-071`: refused verbatim with ``factor_manifest_reversal_1d_v1 year=2026 already holds 1
   subject(s) and this write carries 1; it would drop ['fmn_...']``, because a partition is
   replaced whole and the only escapes offered were to recompute day 1 into the same call or to
   erase it with ``--supersedes-raw``.
3. Run a shortlist at day 2. Reachable only once step 2 is.
4. Retrieve **day 1's** shortlist by its content address and diff the two lists.
   `V2-P4-062`: nothing persisted a shortlist and no route retrieved one, so the three content
   addresses on the answer addressed nothing.
5. Evidence that cannot be resolved to a stored run must not count toward `researched_ratio`.
   `V2-P4-049`: an invented `SignalFrame` beside the literal `run_000000000000000000000000`
   cleared a `--min-researched-ratio 1.0` floor and published 25 candidates at
   ``researched_ratio: 1.0``.

`test_the_whole_workflow_runs_twice_and_the_two_days_can_be_compared` is the one test that walks
all five in order. The tests after it hold the individual properties that walk cannot pin on its
own: that the drop guard still refuses a *restatement* after the append (which is what separates
this from weakening it), that `--supersedes-raw` still removes a build this call does not
re-answer, that the two derived planes append too, what a *refused* retrieval says as against an
unaddressable one, that evidence naming a **stored** run does count, and that all three faces
serve one document.

## The panel, and why the closes have to move

`daily.close_moves_between_sessions` is requested because `reversal_1d/v1` is a one-day return: on
the default generator every close is flat across sessions, every stored value is `0.0`, and the two
days' shortlists would be one list plus a tie-break. The whole of step 4 is the difference between
the two, so a fixture that cannot produce one would make it unfalsifiable.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.run import RunManifest, RunStatus
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FACTOR_TRANSFORMS,
    load_processed_factor_observations,
)
from openalpha_cn.panel_neutralization import (
    FACTOR_NEUTRALIZATIONS,
    load_neutralized_factor_observations,
)
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_view import (
    _SHORTLIST_ID,
    SHORTLIST_ANSWER_UNADDRESSED_KEYS,
    stable_answer_digest,
)
from openalpha_cn.storage.shortlists import SHORTLIST_ID_PATTERN

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64
HORIZON: Final[str] = "5d"

SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions",)

DAY_ONE_BUILD: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on 2026-01-15, after that session's close and therefore about it."""

DAY_TWO_BUILD: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
"""The next session's, built in a **second** invocation. That second invocation is `V2-P4-071`."""

DAY_ONE_AS_OF: Final[datetime] = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
"""07:00 Asia/Shanghai on 2026-01-16: after day one's build and before day two's exists."""

DAY_TWO_AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""20:00 Asia/Shanghai on 2026-01-16, after that session published and after its build."""

SHORTLIST_SIZE: Final[int] = 3
POSITION_CAPITAL: Final[str] = "2000"
"""A budget that buys one 100-share lot of a name near 20 yuan and not one much above it.

Chosen by measurement rather than for roundness, and the measurement is the whole of step 4. At
`100000` every listed name is affordable on both sessions, the factor ordering is the only thing
moving, and the top three come back **identical** on the two days -- the closes drift by less than
one place in the ranking. At `2000` the affordable set itself moves with the prices, so a name
enters the list and a name leaves it: exactly the comparison this file exists to make possible,
and one no assertion below has to name a price to describe.
"""

UNRESOLVABLE_RUN: Final[str] = "run_000000000000000000000000"
"""The literal the `V2-P4-049` probe cleared a 1.0 floor with. Well-formed and stored nowhere."""

ENTERED: Final[list[str]] = ["600000.SH"]
LEFT: Final[list[str]] = ["002415.SZ"]
"""Which names day two's shortlist gained and lost against day one's.

Written out rather than asserted as "the two differ", because "they differ" passes on a fixture
where the second run simply failed to read anything -- and that is the exact shape `V2-P4-061`'s
own file warns about. `reversal_1d/v1` is `lower_is_better` over a one-day return, and
`daily.close_moves_between_sessions` is what makes two consecutive sessions produce two orderings
at all.
"""

BASELINE: Final[dict[str, Any]] = {
    "components": ({"factor": "reversal_1d/v1", "weight": 1.0},),
    "tier": "raw",
    "shortlist_size": SHORTLIST_SIZE,
    "position_capital": POSITION_CAPITAL,
    "as_of": DAY_ONE_AS_OF,
    "years": (YEAR,),
    "exchange": EXCHANGE,
    "horizon": HORIZON,
    "minimum_tradable_ratio": 0.0,
    "minimum_researched_ratio": 0.0,
    "maximum_ranking_age_days": 3_650,
    "code_commit": COMMIT,
    "config_digest": CONFIG_DIGEST,
}
"""One declaration for all three faces, with every gate bar inert so a refusal below is the one
the test at hand raised and never a leftover from the fixture."""


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A runtime directory holding exactly what `openalpha panel build` writes, and no factor.

    Function-scoped state is not needed and module scope is: the tests below write factor
    partitions into it in order, which is the workflow itself.
    """
    root = tmp_path_factory.mktemp("shortlist-workflow")
    store = PanelStore(root / "panel")
    write_generated_panel(store, generate_panel(shapes=SHAPES))
    return root


@pytest.fixture
def rest(runtime_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=runtime_dir)) as client:
        yield client


def _build(runtime_dir: Path, *instants: datetime) -> tuple[int, str]:
    """`openalpha factor build --tier raw` at `instants`, as the command line runs it."""
    arguments = [
        "factor",
        "build",
        "--factor",
        "reversal_1d/v1",
        "--tier",
        "raw",
        "--year",
        str(YEAR),
        "--exchange",
        EXCHANGE,
        "--max-staleness-days",
        "30",
        "--code-commit",
        COMMIT,
        "--runtime-dir",
        str(runtime_dir),
        "--json",
    ]
    for instant in instants:
        arguments.extend(["--as-of", instant.isoformat()])
    result = CliRunner().invoke(app, arguments)
    return result.exit_code, result.stdout


def _shortlist_arguments(
    runtime_dir: Path, parameters: Mapping[str, Any], *, evidence: Path | None = None
) -> list[str]:
    """One parameter mapping as `openalpha shortlist run`'s argv, keyed off the mapping."""
    arguments = [
        "shortlist",
        "run",
        "--tier",
        str(parameters["tier"]),
        "--shortlist-size",
        str(parameters["shortlist_size"]),
        "--position-capital",
        str(parameters["position_capital"]),
        "--horizon",
        str(parameters["horizon"]),
        "--min-tradable-ratio",
        str(parameters["minimum_tradable_ratio"]),
        "--min-researched-ratio",
        str(parameters["minimum_researched_ratio"]),
        "--max-ranking-age-days",
        str(parameters["maximum_ranking_age_days"]),
        "--exchange",
        str(parameters["exchange"]),
        "--as-of",
        parameters["as_of"].isoformat(),
        "--code-commit",
        str(parameters["code_commit"]),
        "--config-digest",
        str(parameters["config_digest"]),
        "--runtime-dir",
        str(runtime_dir),
        "--json",
    ]
    for component in parameters["components"]:
        arguments.extend(["--component", f"{component['factor']}={component['weight']}"])
    for year in parameters["years"]:
        arguments.extend(["--year", str(year)])
    if evidence is not None:
        arguments.extend(["--evidence", str(evidence)])
    return arguments


def _run_shortlist(
    runtime_dir: Path, parameters: Mapping[str, Any], *, evidence: Path | None = None
) -> tuple[int, dict[str, Any]]:
    """`openalpha shortlist run --json`'s exit code and its body.

    A *gate refusal* exits `1` and still prints the whole verdict, which is the distinction this
    command exists for; a *fault* -- a panel that cannot be read, a request that cannot be put --
    exits non-zero with a sentence rather than a verdict. So the body is parsed when there is one
    and the raw output is handed back under `output` when there is not, rather than the helper
    raising `JSONDecodeError` and hiding which of the two happened.

    **The two are told apart by the document's own shape, not by whether stdout starts with
    `{`** (`V2-P5-047`). That was the discriminator until `--json` began answering a fault with a
    refusal document too, at which point a fault parsed cleanly as a verdict and every caller of
    this helper silently got the wrong branch -- `KeyError: 'output'` in the one test that read
    it. `status: refused` is the field the refusal carries and no verdict does, so keying on it
    separates the two whatever either shape gains later; `output` still carries everything the
    process printed, so a test asserting on the sentence is unchanged.
    """
    result = CliRunner().invoke(
        app, _shortlist_arguments(runtime_dir, parameters, evidence=evidence)
    )
    body = result.stdout.strip()
    if not body.startswith("{"):
        return result.exit_code, {"output": result.output}
    document = json.loads(body)
    if document.get("status") == "refused":
        return result.exit_code, {"output": result.output, "refusal": document}
    return result.exit_code, document


def _rest_body(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "components": [dict(component) for component in parameters["components"]],
        "tier": parameters["tier"],
        "shortlist_size": parameters["shortlist_size"],
        "position_capital": parameters["position_capital"],
        "as_of": parameters["as_of"].isoformat(),
        "years": list(parameters["years"]),
        "exchange": parameters["exchange"],
        "horizon": parameters["horizon"],
        "minimum_tradable_ratio": parameters["minimum_tradable_ratio"],
        "minimum_researched_ratio": parameters["minimum_researched_ratio"],
        "maximum_ranking_age_days": parameters["maximum_ranking_age_days"],
        "code_commit": parameters["code_commit"],
        "config_digest": parameters["config_digest"],
    }


def _signal(subject: str, *, as_of: datetime) -> SignalFrame:
    return SignalFrame(
        subject=subject,
        as_of=as_of,
        direction="bullish",
        strength=0.4,
        confidence=0.7,
        horizon=HORIZON,
        evidence_ids=("evd_000000000000000000000001",),
    )


def _stored_run_manifest(
    subject: str, *, as_of: datetime, status: RunStatus = "succeeded", label: str = ""
) -> RunManifest:
    """One stored run for `subject`, at a declared outcome and under a declared name.

    `status` is **not** addressed by `run_manifest_id` (`RUN_MANIFEST_UNADDRESSED_FIELDS`) and
    `run_id` **is**, which together decide the shape of `V2-P4-075`'s test below: three outcomes
    under one `run_id` would be one address, and `SQLiteRunRepository.append_run` refuses the
    second row of a `run_id` anyway, so each arm is given its own `label` and its own address.
    """
    return RunManifest(
        run_id=f"run-{label}{subject}",
        mode="backtest",
        as_of=as_of,
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
        random_seed=7,
        started_at=as_of,
        finished_at=as_of,
        status=status,
    )


def _wire_evidence(
    subjects: Sequence[str], *, as_of: datetime, run_manifest_id: str | None = None
) -> dict[str, Any]:
    """The evidence plane's answers as the wire carries them, one `run_manifest_id` per name.

    `run_manifest_id` overrides every entry with one literal, which is how the `V2-P4-049` probe
    is reproduced: a well-formed address that resolves to nothing.
    """
    return {
        subject: {
            "signal": json.loads(_signal(subject, as_of=as_of).model_dump_json()),
            "run_manifest_id": (
                _stored_run_manifest(subject, as_of=as_of).run_manifest_id
                if run_manifest_id is None
                else run_manifest_id
            ),
        }
        for subject in subjects
    }


def _subjects(answer: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(entry["subject"] for entry in answer["funnel"]["shortlist"])


def test_the_whole_workflow_runs_twice_and_the_two_days_can_be_compared(
    runtime_dir: Path, rest: TestClient
) -> None:
    """The five steps in order, from `CliRunner` and `TestClient`, on one store.

    Written before any of the three fixes and failing at **step 2** for the stated reason -- the
    drop guard, verbatim -- which is the wall that made the other four unreachable rather than
    merely untested.
    """
    # 1. a cross section at day one, and a shortlist against it.
    built, report = _build(runtime_dir, DAY_ONE_BUILD)
    assert built == 0, report
    day_one_code, day_one = _run_shortlist(runtime_dir, BASELINE)
    assert day_one_code == 0, day_one
    address = day_one["shortlist_id"]
    assert _subjects(day_one)

    # 2. a second instant in the same partition year, in a second invocation.
    appended, appended_report = _build(runtime_dir, DAY_TWO_BUILD)
    assert appended == 0, appended_report

    # ... and day one's build is still there, which is the half a `--supersedes-raw` escape
    # would have passed while destroying.
    still_there_code, still_there = _run_shortlist(runtime_dir, BASELINE)
    assert still_there_code == 0, still_there
    assert still_there["shortlist_id"] == address

    # 3. a shortlist at day two.
    day_two_code, day_two = _run_shortlist(runtime_dir, {**BASELINE, "as_of": DAY_TWO_AS_OF})
    assert day_two_code == 0, day_two
    assert day_two["cross_section"]["as_of"] == DAY_TWO_BUILD.isoformat()
    assert day_two["shortlist_id"] != address

    # 4. day one's answer, retrieved by its content address, and the two lists diffed.
    held = rest.get(f"/api/v1/shortlists/{address}")
    assert held.status_code == 200, held.text
    assert held.json()["shortlist_id"] == address
    assert _subjects(held.json()) == _subjects(day_one)

    before, after = set(_subjects(day_one)), set(_subjects(day_two))
    assert before != after, "the fixture cannot show a difference this test exists to explain"
    assert (sorted(after - before), sorted(before - after)) == (ENTERED, LEFT)

    # 5. evidence that resolves to no stored run does not count toward `researched_ratio`.
    unresolvable = rest.post(
        "/api/v1/shortlists/run",
        json={
            **_rest_body({**BASELINE, "minimum_researched_ratio": 1.0}),
            "evidence": _wire_evidence(
                _subjects(day_one), as_of=DAY_ONE_AS_OF, run_manifest_id=UNRESOLVABLE_RUN
            ),
        },
    )
    assert unresolvable.status_code == 409, unresolvable.text
    refused = unresolvable.json()
    assert refused["measurement"]["researched_ratio"] == 0.0
    assert refused["is_blocked"] is True
    assert refused["admitted"] is None
    assert refused["evidence_without_a_stored_run"] == sorted(_subjects(day_one))


def test_a_second_instant_is_added_and_the_drop_guard_still_refuses_a_restatement(
    tmp_path: Path,
) -> None:
    """`V2-P4-071`'s two halves: an append is legal and a *restatement* is still refused.

    The second half is what separates this fix from weakening the guard, and it is the assertion a
    carry-forward with no `identity_columns` fails. A rebuild at a stored `as_of` under a different
    `--code-commit` mints a different `manifest_id`, so a merge that carried everything it did not
    literally supply would keep both -- and the year would hold two answers to one cross-section
    question, with two rows for every `(subject, as_of)` and nothing saying which is the answer.
    Refused, and the refusal still names `--supersedes-raw`.

    Driven on its own store rather than on the module fixture, because a refused write must be
    shown to have changed nothing and the workflow test above needs both instants intact.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, generate_panel(shapes=SHAPES))

    assert _build(tmp_path, DAY_ONE_BUILD)[0] == 0
    assert _build(tmp_path, DAY_TWO_BUILD)[0] == 0

    restated = CliRunner().invoke(
        app,
        [
            "factor",
            "build",
            "--factor",
            "reversal_1d/v1",
            "--tier",
            "raw",
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--max-staleness-days",
            "30",
            "--code-commit",
            "9876543210fedcba",
            "--runtime-dir",
            str(tmp_path),
            "--as-of",
            DAY_ONE_BUILD.isoformat(),
        ],
    )
    assert restated.exit_code == 1
    assert "it would drop" in restated.output
    assert "--supersedes-raw" in restated.output

    # And both instants are still screenable, which a refusal that half-wrote would break.
    for as_of, instant in ((DAY_ONE_AS_OF, DAY_ONE_BUILD), (DAY_TWO_AS_OF, DAY_TWO_BUILD)):
        code, answer = _run_shortlist(tmp_path, {**BASELINE, "as_of": as_of})
        assert code == 0, answer
        assert answer["cross_section"]["as_of"] == instant.isoformat()


def test_an_unaddressable_id_and_an_unheld_one_are_two_different_answers(
    runtime_dir: Path, rest: TestClient
) -> None:
    """A malformed address is `bad_request`; a well-formed one nothing is held under is `not_held`.

    One `404` covering both would tell a caller who mistyped an address that their answer had been
    lost. The order of the two checks in `held_shortlist` is what makes them separable at all: the
    shape is refused before the store is asked.
    """
    malformed = rest.get("/api/v1/shortlists/not-an-address")
    assert malformed.status_code == 422, malformed.text
    assert malformed.json()["detail"]["reason"] == "bad_request"

    absent = rest.get(f"/api/v1/shortlists/sla_{'0' * 24}")
    assert absent.status_code == 404, absent.text
    assert absent.json()["detail"]["reason"] == "not_held"

    cli_malformed = CliRunner().invoke(
        app, ["shortlist", "get", "not-an-address", "--runtime-dir", str(runtime_dir)]
    )
    cli_absent = CliRunner().invoke(
        app, ["shortlist", "get", f"sla_{'0' * 24}", "--runtime-dir", str(runtime_dir)]
    )
    assert (cli_malformed.exit_code, cli_absent.exit_code) == (3, 1)


def test_the_three_faces_serve_one_stored_answer(runtime_dir: Path, rest: TestClient) -> None:
    """`openalpha shortlist get`, `GET /api/v1/shortlists/{id}` and the SDK hand back one body.

    `V2-P4-033`'s finding applied to the read side: three renderings of one document that agree
    today is how a caller comes to believe a bar was cleared when a key was merely dropped.
    """
    code, answer = _run_shortlist(runtime_dir, BASELINE)
    assert code == 0, answer
    address = str(answer["shortlist_id"])

    from_cli = CliRunner().invoke(
        app, ["shortlist", "get", address, "--runtime-dir", str(runtime_dir)]
    )
    assert from_cli.exit_code == 0, from_cli.output
    from_rest = rest.get(f"/api/v1/shortlists/{address}")
    assert from_rest.status_code == 200, from_rest.text
    from_sdk = OpenAlphaSDK(runtime_dir=runtime_dir).held_shortlist(address)

    assert json.loads(from_cli.stdout) == from_rest.json() == from_sdk

    listed = CliRunner().invoke(
        app, ["shortlist", "list", "--runtime-dir", str(runtime_dir), "--json"]
    )
    assert listed.exit_code == 0, listed.output
    assert address in json.loads(listed.stdout)["shortlist_ids"]
    assert address in OpenAlphaSDK(runtime_dir=runtime_dir).list_shortlists()
    assert address in rest.get("/api/v1/shortlists").json()["shortlist_ids"]


def test_evidence_that_names_a_stored_run_is_counted_and_evidence_that_does_not_is_not(
    tmp_path: Path,
) -> None:
    """`V2-P4-049`'s two halves on one store, one command line, one file different.

    The negative half alone passes on a tree that dropped **every** supplied answer, which would
    make `researched_ratio` permanently zero and `V2-P4-023`'s floor permanently unreachable --
    the same "unreachable bar" the limitation this row corrects was itself worried about. So the
    same evidence is filed twice: once under a `run_manifest_id` this runtime directory holds a
    run for, and once under `run_000000000000000000000000`. One clears a `1.0` floor and the other
    does not.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, generate_panel(shapes=SHAPES))
    assert _build(tmp_path, DAY_ONE_BUILD)[0] == 0

    code, first = _run_shortlist(tmp_path, BASELINE)
    assert code == 0, first
    shortlisted = _subjects(first)

    sdk = OpenAlphaSDK(runtime_dir=tmp_path)
    for subject in shortlisted:
        sdk.repository.append_run(_stored_run_manifest(subject, as_of=DAY_ONE_AS_OF))

    resolved = tmp_path / "resolved.json"
    resolved.write_text(
        json.dumps(_wire_evidence(shortlisted, as_of=DAY_ONE_AS_OF)), encoding="utf-8"
    )
    invented = tmp_path / "invented.json"
    invented.write_text(
        json.dumps(
            _wire_evidence(shortlisted, as_of=DAY_ONE_AS_OF, run_manifest_id=UNRESOLVABLE_RUN)
        ),
        encoding="utf-8",
    )

    strict = {**BASELINE, "minimum_researched_ratio": 1.0}
    admitted_code, admitted = _run_shortlist(tmp_path, strict, evidence=resolved)
    refused_code, refused = _run_shortlist(tmp_path, strict, evidence=invented)

    assert (admitted_code, refused_code) == (0, 1)
    assert admitted["measurement"]["researched_ratio"] == 1.0
    assert admitted["is_blocked"] is False
    assert admitted["evidence_without_a_stored_run"] == []
    assert [entry["subject"] for entry in admitted["admitted"]] == sorted(shortlisted)

    assert refused["measurement"]["researched_ratio"] == 0.0
    assert refused["is_blocked"] is True
    assert refused["admitted"] is None
    assert refused["unresearched"] == sorted(shortlisted)
    assert refused["evidence_without_a_stored_run"] == sorted(shortlisted)


def test_a_stored_answer_that_was_edited_does_not_open(runtime_dir: Path) -> None:
    """The seal, driven. A document whose answer no longer hashes to its key is not served.

    `open_shortlist`'s whole job, and the one integrity property a byte store can deliver: this is
    against a partial write and against a clobber, and never against an author who recomputed the
    digest beside their edit.
    """
    code, answer = _run_shortlist(runtime_dir, BASELINE)
    assert code == 0, answer
    address = str(answer["shortlist_id"])

    document = runtime_dir / "shortlists" / f"{address}.json"
    original = document.read_text(encoding="utf-8")
    held = json.loads(original)
    held["answer"]["funnel"]["shortlist"][0]["subject"] = "999999.SZ"
    document.write_text(json.dumps(held), encoding="utf-8")
    try:
        edited = CliRunner().invoke(
            app, ["shortlist", "get", address, "--runtime-dir", str(runtime_dir)]
        )
        assert edited.exit_code == 1
        assert "hashes to" in edited.output
    finally:
        document.write_text(original, encoding="utf-8")


def test_every_measurement_key_is_addressed_or_excluded_by_name(runtime_dir: Path) -> None:
    """`SHORTLIST_ANSWER_UNADDRESSED_KEYS` partitions the rendered `measurement`, both ways.

    The audit shape `RUN_MANIFEST_UNADDRESSED_FIELDS` established: a key added to the rendering is
    red here until it is either measured to move the address or given a reason in that constant.
    Both directions, because a set naming a key the body does not carry is a permission nobody
    revoked -- `test_the_allowlist_names_files_that_exist_and_actually_make_the_call` one plane
    over -- and a key that is *supposed* to move the address and does not is the whole of what an
    unaddressed field costs.
    """
    code, answer = _run_shortlist(runtime_dir, BASELINE)
    assert code == 0, answer
    measurement = dict(answer["measurement"])
    assert set(measurement) >= SHORTLIST_ANSWER_UNADDRESSED_KEYS

    body = {key: value for key, value in answer.items() if key != "shortlist_id"}
    assert stable_answer_digest(body) == answer["shortlist_id"]

    for key, value in measurement.items():
        moved = {**body, "measurement": {**measurement, key: (value or 0) + 1}}
        addressed = stable_answer_digest(moved) != answer["shortlist_id"]
        assert addressed is (key not in SHORTLIST_ANSWER_UNADDRESSED_KEYS), key


def test_a_neutralized_tier_screen_is_refused_by_name_and_says_what_this_face_lacks(
    runtime_dir: Path,
) -> None:
    """`a_neutralized_tier_screen_needs_exposures_this_face_does_not_load`, driven.

    **The entry existed and nothing exercised it.** `V2-P4-033` recorded that this face refuses
    the neutralised tier by name, and the only occurrence of the code anywhere in `tests/` was the
    set literal `tests/unit/test_shortlist_view.py` keeps for the registry audit -- a string, not
    a branch. A mutation rewriting the refusal's whole stated *reason* survived the sweep
    `V2-P4-028` ran, which is how that was found.

    **The reason is what this asserts, because `V2-P4-028` changed it.** The entry used to say
    the obstacle was the instant: the loader read `index_member_all` through `read_if_ready` and
    answered only at an `as_of` at or after the newest stored assignment. It is day-scoped now, so
    what is missing is a **request contract** -- a shortlist request carries no membership years,
    no trading calendar and no neutralisation to decide what the exposures *are* -- and the
    message has to say that rather than a bound that no longer exists.

    Driven from the command line at `bad_request`, not from `run_shortlist`: the whole point of
    the entry is what a caller is told. The request is built before it is refused, so
    `--transform` and `--neutralization` are supplied -- a request the face rejects for its own
    reason rather than one `shortlist_request` never assembled.
    """
    result = CliRunner().invoke(
        app,
        [
            *_shortlist_arguments(runtime_dir, {**BASELINE, "tier": "neutralized"}),
            "--transform",
            "cross_section_standard/v1",
            "--neutralization",
            "industry_and_size/v1",
        ],
    )

    assert result.exit_code == int(PanelExit.bad_request), result.output
    assert "no membership years, no trading calendar and no neutralisation" in result.stderr
    assert "The instant is no longer the obstacle" in result.stderr
    assert "read_if_ready" not in result.stderr


def test_the_retrieval_shape_and_the_stores_own_shape_are_one_literal() -> None:
    """`shortlist_view`'s refusal pattern and `storage/shortlists.py`'s are the same shape.

    They are written twice, in two modules, because `shortlist_view` may not import `storage` --
    the `ShortlistDocumentStore` Protocol is what keeps that edge absent, and `lint-imports`'
    `storage does not depend upward` contract keeps the other direction absent too. Two copies of
    one literal with nothing making them agree is how a retrieval comes to refuse an address the
    store would have served, so the pair is held equal here rather than left to inspection.
    """
    assert SHORTLIST_ID_PATTERN.pattern == _SHORTLIST_ID.pattern


def test_superseding_a_build_this_call_does_not_re_answer_still_removes_it(
    tmp_path: Path,
) -> None:
    """`--supersedes-raw` keeps its second meaning after the carry-forward: it *deletes*.

    This assertion was added because a mutant survived, and the mutant is worth recording. With
    the carry-forward in place, a rebuild at a stored `as_of` under a different commit is already
    refused by the identity collision above, so `supersedes`' first meaning -- "this call replaces
    that build" -- no longer needs the merge to know about it. Deleting the `superseded` clause
    from the retain rule therefore turned nothing red.

    What it silently broke is the other thing `supersedes` has always been able to do: remove a
    stored build this call is **not** re-answering, which is how a bad build gets out of a year.
    Before the carry-forward that fell out of the whole-partition replace; after it, a merge that
    ignored `superseded` would carry the build straight back in and the command would report
    success having changed nothing -- the exact silent no-op `write_factor_panels` refuses an
    unmatched `supersedes` for.

    The design is not the thing that was wrong here, so this is an assertion rather than a
    revision: driven end to end, day one's cross section is screenable, then named on
    `--supersedes-raw`, then gone.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, generate_panel(shapes=SHAPES))

    # Day one alone first, so its own report names the one build this test will remove.
    first_code, first_report = _build(tmp_path, DAY_ONE_BUILD)
    assert first_code == 0, first_report
    doomed = json.loads(first_report)["manifest_ids"]["raw"]
    assert len(doomed) == 1
    assert _build(tmp_path, DAY_TWO_BUILD)[0] == 0

    before, answer = _run_shortlist(tmp_path, BASELINE)
    assert before == 0, answer
    assert answer["cross_section"]["as_of"] == DAY_ONE_BUILD.isoformat()

    removed = CliRunner().invoke(
        app,
        [
            "factor",
            "build",
            "--factor",
            "reversal_1d/v1",
            "--tier",
            "raw",
            "--year",
            str(YEAR),
            "--exchange",
            EXCHANGE,
            "--max-staleness-days",
            "30",
            "--code-commit",
            COMMIT,
            "--runtime-dir",
            str(tmp_path),
            "--as-of",
            DAY_TWO_BUILD.isoformat(),
            "--supersedes-raw",
            doomed[0],
        ],
    )
    assert removed.exit_code == 0, removed.output

    after, gone = _run_shortlist(tmp_path, BASELINE)
    assert after == 1, gone
    assert "no raw-tier cross section" in gone["output"]

    # ... and day two is untouched, so this removed one build rather than emptying the year.
    still, day_two = _run_shortlist(tmp_path, {**BASELINE, "as_of": DAY_TWO_AS_OF})
    assert still == 0, day_two
    assert day_two["cross_section"]["as_of"] == DAY_TWO_BUILD.isoformat()


@pytest.mark.parametrize(
    ("tier", "extra"),
    [
        ("processed", ["--transform", "cross_section_standard/v1"]),
        (
            "neutralized",
            [
                "--transform",
                "cross_section_standard/v1",
                "--neutralization",
                "industry_and_size/v1",
            ],
        ),
    ],
)
def test_the_two_derived_planes_append_a_second_instant_as_well(
    tmp_path: Path, tier: str, extra: list[str]
) -> None:
    """The append is on all three writers, and half a fix would relocate the wall.

    `V2-P4-061`'s own file recorded what a half fix costs: moving `load_daily_bars` alone onto the
    session read relocated the same refusal onto `stk_limit` rather than removing it. The write
    side has the identical shape -- three writers, one constraint -- so a raw-only append would
    leave `openalpha factor build --tier processed` refusing a second invocation with the same
    sentence about a different dataset.

    The two derived planes hold **every policy of one factor** in one partition, which is the one
    way their identity differs from raw's: `(transform_id, event_time)` and
    `(neutralization_id, event_time)` rather than `(event_time)` alone. Both instants are read
    back through the tier's own loader, so this measures the stored partition rather than the
    command's exit code.

    The neutralised instants were on and after 2026-01-12 because a residual existed only at a
    prediction instant at or after the last stored assignment of every membership year read --
    `V2-P4-027`'s bound, inherited from `tests/integration/test_factor_build.py` rather than
    re-argued. **`V2-P4-028` removed that bound** and the instants are left where they are: what
    this test measures is that a *second* instant appends to each derived plane's partition, and
    both build fine wherever they sit, so moving them would change nothing here.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, generate_panel(shapes=(*SHAPES, "industry.coverage_hole")))

    def build(instant: datetime) -> tuple[int, str]:
        result = CliRunner().invoke(
            app,
            [
                "factor",
                "build",
                "--factor",
                "reversal_1d/v1",
                "--tier",
                tier,
                *extra,
                "--year",
                str(YEAR),
                "--exchange",
                EXCHANGE,
                "--max-staleness-days",
                "30",
                "--code-commit",
                COMMIT,
                "--runtime-dir",
                str(tmp_path),
                "--as-of",
                instant.isoformat(),
            ],
        )
        return result.exit_code, result.output

    first_code, first_output = build(DAY_ONE_BUILD)
    assert first_code == 0, first_output
    second_code, second_output = build(DAY_TWO_BUILD)
    assert second_code == 0, second_output

    definition = FACTOR_DEFINITIONS.get("reversal_1d/v1")
    read_at = datetime(2026, 2, 1, tzinfo=UTC)
    if tier == "processed":
        rows = load_processed_factor_observations(
            store,
            definition,
            FACTOR_TRANSFORMS.get("cross_section_standard/v1"),
            years=(YEAR,),
            as_of=read_at,
        )
    else:
        rows = load_neutralized_factor_observations(
            store,
            definition,
            FACTOR_NEUTRALIZATIONS.get("industry_and_size/v1"),
            years=(YEAR,),
            as_of=read_at,
        )
    assert sorted({row.as_of for row in rows}) == [DAY_ONE_BUILD, DAY_TWO_BUILD]


def test_a_stored_answer_renamed_on_disk_is_not_served_under_its_new_name(
    runtime_dir: Path,
) -> None:
    """The other half of the seal: self-consistent is not the same as *the one asked for*.

    `open_shortlist` proves a document's answer hashes to the address the document carries, which
    a **renamed** file passes: it is intact, and it is filed under a key its own content does not
    carry. `FileShortlistStore.put` cannot produce that state -- it derives the filename from the
    payload -- so this is the clobber case rather than a reachable code path, and it is exactly
    what a seal is for. Driven rather than asserted in prose, because a check nothing exercises is
    a check that can be deleted for tidiness.
    """
    code, answer = _run_shortlist(runtime_dir, BASELINE)
    assert code == 0, answer
    address = str(answer["shortlist_id"])

    held = runtime_dir / "shortlists" / f"{address}.json"
    renamed = runtime_dir / "shortlists" / f"sla_{'1' * 24}.json"
    renamed.write_bytes(held.read_bytes())
    try:
        served = CliRunner().invoke(
            app, ["shortlist", "get", f"sla_{'1' * 24}", "--runtime-dir", str(runtime_dir)]
        )
        assert served.exit_code == 1, served.output
        assert "a key its own content does not carry" in served.output
    finally:
        renamed.unlink()


def test_a_run_that_did_not_finish_does_not_resolve_the_evidence_filed_under_it(
    tmp_path: Path,
) -> None:
    """`V2-P4-075`: `failed` and `interrupted` runs used to clear a `1.0` floor.

    The P4 fourth-round acceptance stored a `RunManifest(status="failed")` and a
    `RunManifest(status="interrupted")`, filed evidence for every shortlisted name against their
    addresses, and asked for `--min-researched-ratio 1.0`. Both answered `exit 0`,
    `researched_ratio=1.0`, `unresolvable=[]`, `is_blocked=False` -- while the refusal this floor
    raises was calling that ratio "a fact about which runs finished".

    **The succeeded arm is what makes the two answers separable.** Without it this test passes on
    a tree that dropped *every* supplied answer, which would make `researched_ratio` permanently
    zero -- the exact failure mode
    `test_evidence_that_names_a_stored_run_is_counted_and_evidence_that_does_not_is_not` was
    written against one row earlier. Three arms, one store, one command line, one file different.

    **And the reported reason has to separate the two ways an address fails to resolve.** A run
    nobody made and a run that did not finish have different remedies -- research the name, or go
    and look at why the run broke -- so `evidence_without_a_stored_run` stays *empty* on the two
    refused arms and the names appear under `evidence_from_an_unfinished_run` instead. Folding
    them into one bucket would make this test pass while telling a user the deployment holds no
    run for an address it holds a run for.
    """
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, generate_panel(shapes=SHAPES))
    assert _build(tmp_path, DAY_ONE_BUILD)[0] == 0

    code, first = _run_shortlist(tmp_path, BASELINE)
    assert code == 0, first
    shortlisted = _subjects(first)
    assert shortlisted

    sdk = OpenAlphaSDK(runtime_dir=tmp_path)
    arms: tuple[RunStatus, ...] = ("failed", "interrupted", "succeeded")
    files: dict[str, Path] = {}
    for status in arms:
        for subject in shortlisted:
            sdk.repository.append_run(
                _stored_run_manifest(subject, as_of=DAY_ONE_AS_OF, status=status, label=status)
            )
        path = tmp_path / f"{status}.json"
        path.write_text(
            json.dumps(
                {
                    subject: {
                        "signal": json.loads(
                            _signal(subject, as_of=DAY_ONE_AS_OF).model_dump_json()
                        ),
                        "run_manifest_id": _stored_run_manifest(
                            subject, as_of=DAY_ONE_AS_OF, status=status, label=status
                        ).run_manifest_id,
                    }
                    for subject in shortlisted
                }
            ),
            encoding="utf-8",
        )
        files[status] = path

    strict = {**BASELINE, "minimum_researched_ratio": 1.0}
    answers = {status: _run_shortlist(tmp_path, strict, evidence=files[status]) for status in arms}

    assert [answers[status][0] for status in arms] == [1, 1, 0]
    for status in ("failed", "interrupted"):
        body = answers[status][1]
        assert body["measurement"]["researched_ratio"] == 0.0, status
        assert body["is_blocked"] is True, status
        assert body["admitted"] is None, status
        assert body["unresearched"] == sorted(shortlisted), status
        assert body["evidence_from_an_unfinished_run"] == sorted(shortlisted), status
        assert body["evidence_without_a_stored_run"] == [], status

    succeeded = answers["succeeded"][1]
    assert succeeded["measurement"]["researched_ratio"] == 1.0
    assert succeeded["is_blocked"] is False
    assert succeeded["evidence_from_an_unfinished_run"] == []
    assert succeeded["evidence_without_a_stored_run"] == []
    assert [entry["subject"] for entry in succeeded["admitted"]] == sorted(shortlisted)

    # ... and the terminal face says which of the two happened, in its own words.
    printed = CliRunner().invoke(
        app,
        [
            argument
            for argument in _shortlist_arguments(tmp_path, strict, evidence=files["failed"])
            if argument != "--json"
        ],
    )
    assert printed.exit_code == 1, printed.output
    assert "unfinished" in printed.output
    assert "holds a run for that did not finish" in printed.output
    assert "holds no run for" not in printed.output
