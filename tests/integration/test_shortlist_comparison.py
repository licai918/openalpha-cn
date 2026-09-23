"""`V2-P4-007`: two published shortlists, compared, from the command line only.

`openalpha shortlist get`'s own docstring states the workflow this row finishes -- *"run it, run
it again tomorrow, and compare the two"* -- and `tests/integration/test_shortlist_workflow.py`
step 4 is where somebody had to do the comparing by hand, with a `set` difference written into
the test. This file drives the command that does it, and it drives it through `CliRunner` and
`OpenAlphaSDK` because that is the whole of the row: a library function that diffs two mappings
would be green on a tree where no product surface can reach it.

## Why the baseline is named rather than inferred

`KNOWN_SHORTLIST_VIEW_LIMITATIONS.the_stored_answer_is_addressed_by_content_and_not_by_when_it_
was_run` is a measured property of `V2-P4-062`'s store: `shortlist_id` is
`stable_answer_digest` over the answer, so the store cannot say when a document was written or
how many times an answer was reached -- a wall clock in the key would mint a new document every
day the same shortlist was re-run. "The previous run", therefore, is not a question this
deployment can answer, and a command that guessed at it would be inventing an ordering. Both
addresses are arguments, and the first one is the baseline.

## The fixture, and why the two days differ at all

Taken from `test_shortlist_workflow.py`, which measured all of it: `reversal_1d/v1` over
`daily.close_moves_between_sessions` so two consecutive sessions produce two orderings, and a
`--position-capital` of `2000` so the *affordable* set moves too -- at `100000` the top three come
back identical on both days and there is no difference to compare.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from panel_fixtures import EXCHANGE, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_view import stable_answer_digest
from openalpha_cn.storage.shortlists import (
    SHORTLIST_DOCUMENT_SUFFIX,
    FileShortlistStore,
)

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64
HORIZON: Final[str] = "5d"
SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions",)

DAY_ONE_BUILD: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
DAY_TWO_BUILD: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
DAY_ONE_AS_OF: Final[datetime] = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
DAY_TWO_AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)

ENTERED: Final[list[str]] = ["600000.SH"]
LEFT: Final[list[str]] = ["002415.SZ"]
"""Which names day two's list gained and lost, measured by `test_shortlist_workflow.py`.

Written out rather than asserted as "the two differ", for that file's stated reason: "they
differ" passes on a fixture whose second run simply failed to read anything.
"""

BASELINE: Final[dict[str, Any]] = {
    "components": ({"factor": "reversal_1d/v1", "weight": 1.0},),
    "tier": "raw",
    "shortlist_size": 3,
    "position_capital": "2000",
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


def _build(runtime_dir: Path, *instants: datetime) -> tuple[int, str]:
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


def _run_shortlist(
    runtime_dir: Path, parameters: Mapping[str, Any], *, evidence: Path | None = None
) -> tuple[int, dict[str, Any]]:
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
    result = CliRunner().invoke(app, arguments)
    body = result.stdout.strip()
    if not body.startswith("{"):
        return result.exit_code, {"output": result.output}
    return result.exit_code, json.loads(body)


def _compare(
    runtime_dir: Path, baseline: str, current: str, *, json_output: bool = True
) -> tuple[int, str]:
    arguments = ["shortlist", "compare", baseline, current, "--runtime-dir", str(runtime_dir)]
    if json_output:
        arguments.append("--json")
    result = CliRunner().invoke(app, arguments)
    return result.exit_code, result.output


def _compared(runtime_dir: Path, baseline: str, current: str) -> dict[str, Any]:
    code, output = _compare(runtime_dir, baseline, current)
    assert code == 0, output
    return json.loads(output.strip())


def _stored_run_manifest(subject: str, *, as_of: datetime) -> RunManifest:
    return RunManifest(
        run_id=f"run-{subject}-{as_of.isoformat()}",
        mode="backtest",
        as_of=as_of,
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
        random_seed=7,
        started_at=as_of,
        finished_at=as_of,
        status="succeeded",
    )


def _wire_evidence(
    subjects: Sequence[str], *, as_of: datetime, bearish: Sequence[str] = ()
) -> dict[str, Any]:
    return {
        subject: {
            "signal": json.loads(
                SignalFrame(
                    subject=subject,
                    as_of=as_of,
                    direction="bearish" if subject in bearish else "bullish",
                    strength=-0.4 if subject in bearish else 0.4,
                    confidence=0.7,
                    horizon=HORIZON,
                    evidence_ids=("evd_000000000000000000000001",),
                ).model_dump_json()
            ),
            "run_manifest_id": _stored_run_manifest(subject, as_of=as_of).run_manifest_id,
        }
        for subject in subjects
    }


def _subjects(answer: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(entry["subject"] for entry in answer["funnel"]["shortlist"])


@pytest.fixture(scope="module")
def two_days(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One store carrying two published shortlists, one per session, both with evidence.

    Module scope because building the panel and two factor partitions is the expensive half and
    nothing below mutates the store. Every test takes its addresses out of this mapping.
    """
    root = tmp_path_factory.mktemp("shortlist-comparison")
    write_generated_panel(PanelStore(root / "panel"), generate_panel(shapes=SHAPES))
    assert _build(root, DAY_ONE_BUILD)[0] == 0
    assert _build(root, DAY_TWO_BUILD)[0] == 0

    unresearched_code, unresearched = _run_shortlist(root, BASELINE)
    assert unresearched_code == 0, unresearched
    day_one_names = _subjects(unresearched)

    day_two_probe_code, day_two_probe = _run_shortlist(root, {**BASELINE, "as_of": DAY_TWO_AS_OF})
    assert day_two_probe_code == 0, day_two_probe
    day_two_names = _subjects(day_two_probe)

    sdk = OpenAlphaSDK(runtime_dir=root)
    for subject in set(day_one_names) | set(day_two_names):
        sdk.repository.append_run(_stored_run_manifest(subject, as_of=DAY_ONE_AS_OF))
        sdk.repository.append_run(_stored_run_manifest(subject, as_of=DAY_TWO_AS_OF))

    # The one name that stays on both lists and changes its mind between them. Read off the
    # fixture rather than named, so a fixture that stopped holding anything in common fails
    # here -- where the reason is legible -- instead of inside an assertion about a diff.
    held = sorted(set(day_one_names) & set(day_two_names))
    assert held, "the two sessions share no name, so no reason can be shown to change"
    turned = held[0]

    day_one_evidence = root / "day-one-evidence.json"
    day_one_evidence.write_text(
        json.dumps(_wire_evidence(day_one_names, as_of=DAY_ONE_AS_OF)), encoding="utf-8"
    )
    day_two_evidence = root / "day-two-evidence.json"
    day_two_evidence.write_text(
        json.dumps(_wire_evidence(day_two_names, as_of=DAY_TWO_AS_OF, bearish=(turned,))),
        encoding="utf-8",
    )

    one_code, one = _run_shortlist(root, BASELINE, evidence=day_one_evidence)
    assert one_code == 0, one
    two_code, two = _run_shortlist(
        root, {**BASELINE, "as_of": DAY_TWO_AS_OF}, evidence=day_two_evidence
    )
    assert two_code == 0, two

    return {
        "runtime_dir": root,
        "day_one": one,
        "day_two": two,
        "held": held,
        "turned": turned,
    }


def test_the_command_names_what_entered_and_what_left_between_two_published_answers(
    two_days: dict[str, Any],
) -> None:
    """The row's headline, driven end to end: added and removed, from two content addresses.

    `ENTERED`/`LEFT` rather than "they differ", because the second form passes on a run that read
    nothing at all -- and both lists are asserted, so a comparison that reported one side and
    dropped the other cannot pass either.
    """
    comparison = _compared(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
    )

    assert comparison["added"] == ENTERED
    assert comparison["removed"] == LEFT
    assert comparison["held"] == two_days["held"]
    assert comparison["summary"]["added"] == 1
    assert comparison["summary"]["removed"] == 1


def test_a_name_that_stayed_carries_its_rank_change_and_the_sign_says_which_way(
    two_days: dict[str, Any],
) -> None:
    """S44's `rank change`, with the sign convention pinned rather than left to a reader.

    `rank_change` is `baseline rank - current rank`, so **positive means the name moved up**.
    Asserted against the two ranks it was derived from rather than as a bare number, because a
    convention that silently flipped would still produce a plausible integer.
    """
    comparison = _compared(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
    )
    entries = {entry["subject"]: entry for entry in comparison["entries"]}

    for subject in two_days["held"]:
        entry = entries[subject]
        assert entry["status"] == "held"
        assert entry["rank_change"] == entry["baseline"]["rank"] - entry["current"]["rank"]
        if entry["rank_change"] != 0:
            assert "rank" in entry["changes"]


def test_a_candidate_whose_direction_reversed_is_reported_as_a_changed_reason(
    two_days: dict[str, Any],
) -> None:
    """ "理由变化" -- the third of the row's three, and the one only `admitted` can carry.

    The fixture files the same name bullish on day one and bearish on day two, so this separates
    a comparison that reads the ranking plane from one that reads the published candidates: the
    name's rank may or may not have moved, and its *conclusion* certainly did.
    """
    comparison = _compared(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
    )
    entry = next(item for item in comparison["entries"] if item["subject"] == two_days["turned"])

    assert entry["baseline"]["direction"] == "bullish"
    assert entry["current"]["direction"] == "bearish"
    assert "direction" in entry["changes"]
    assert comparison["summary"]["reason_changed"] >= 1


def test_comparing_an_answer_with_itself_reports_no_movement_at_all(
    two_days: dict[str, Any],
) -> None:
    """The control every diff needs, and the one a "report everything as changed" bug fails.

    Allowed rather than refused: "did anything change since the answer I published?" is a real
    question and the honest answer to asking it of one answer is "nothing".
    """
    address = two_days["day_one"]["shortlist_id"]

    comparison = _compared(two_days["runtime_dir"], address, address)

    assert comparison["added"] == []
    assert comparison["removed"] == []
    assert comparison["summary"]["rank_changed"] == 0
    assert comparison["summary"]["reason_changed"] == 0
    assert all(entry["changes"] == [] for entry in comparison["entries"])


def test_the_comparison_names_both_addresses_and_which_of_them_is_the_baseline(
    two_days: dict[str, Any],
) -> None:
    """The store cannot order two answers, so the body says which one the caller called first."""
    comparison = _compared(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
    )

    assert comparison["baseline"]["shortlist_id"] == two_days["day_one"]["shortlist_id"]
    assert comparison["current"]["shortlist_id"] == two_days["day_two"]["shortlist_id"]
    assert comparison["baseline"]["as_of"] == DAY_ONE_AS_OF.isoformat()
    assert comparison["current"]["as_of"] == DAY_TWO_AS_OF.isoformat()


def test_reversing_the_two_addresses_reverses_added_and_removed(
    two_days: dict[str, Any],
) -> None:
    """A comparison is directional, and the direction is the argument order.

    Without this the whole feature could be symmetric -- reporting a set difference with no
    opinion about which way -- and every assertion above would still pass.
    """
    forwards = _compared(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
    )
    backwards = _compared(
        two_days["runtime_dir"],
        two_days["day_two"]["shortlist_id"],
        two_days["day_one"]["shortlist_id"],
    )

    assert backwards["added"] == forwards["removed"]
    assert backwards["removed"] == forwards["added"]
    forwards_ranks = {entry["subject"]: entry["rank_change"] for entry in forwards["entries"]}
    backwards_ranks = {entry["subject"]: entry["rank_change"] for entry in backwards["entries"]}
    assert all(
        backwards_ranks[subject] == -change
        for subject, change in forwards_ranks.items()
        if change is not None
    )


def test_the_terminal_rendering_names_every_moved_security(two_days: dict[str, Any]) -> None:
    """The face a human reads, which `--json` cannot stand in for.

    `openalpha shortlist run` has both renderings for `shortlist_view`'s stated reason -- two
    shapes for one verdict is how a caller comes to believe something the other shape denies --
    and a comparison whose only face is a JSON blob is one a scheduled job can use and a person
    cannot.
    """
    code, output = _compare(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
        json_output=False,
    )

    assert code == 0, output
    assert ENTERED[0] in output
    assert LEFT[0] in output
    assert "added" in output
    assert "removed" in output


def test_an_address_nothing_is_held_under_is_told_apart_from_one_that_is_not_an_address(
    two_days: dict[str, Any],
) -> None:
    """`held_shortlist`'s two refusals, reached through this command rather than restated in it.

    Both sides are checked, because a command that validated only its first argument would pass
    every other test in this file.
    """
    good = two_days["day_one"]["shortlist_id"]
    unheld = "sla_000000000000000000000000"

    assert _compare(two_days["runtime_dir"], unheld, good)[0] == int(PanelExit.unhealthy)
    assert _compare(two_days["runtime_dir"], good, unheld)[0] == int(PanelExit.unhealthy)
    assert _compare(two_days["runtime_dir"], "not-an-address", good)[0] == int(
        PanelExit.bad_request
    )
    assert _compare(two_days["runtime_dir"], good, "not-an-address")[0] == int(
        PanelExit.bad_request
    )


def test_two_answers_to_two_different_questions_are_refused_rather_than_diffed(
    two_days: dict[str, Any],
) -> None:
    """A comparison that cannot say what stayed the same is arithmetic, not an answer.

    Two screens of different questions can share no name, or share every name for reasons that
    have nothing to do with either -- so the difference would be a true sentence about two lists
    and a false impression about one market. The refusal names the key that differs, because the
    remedy is to pick two answers to one question and only the key says which one to change.

    Both halves of `COMPARABLE_KEYS` that a caller can move on this fixture are driven: the
    weighted components, which live inside `declaration`, and the horizon, which is rendered
    beside it. A refusal that checked only the top level would pass the second and fail the
    first.
    """
    root = two_days["runtime_dir"]
    day_one = two_days["day_one"]["shortlist_id"]

    reweighted_code, reweighted = _run_shortlist(
        root, {**BASELINE, "components": ({"factor": "reversal_1d/v1", "weight": 2.0},)}
    )
    assert reweighted_code == 0, reweighted
    assert reweighted["shortlist_id"] != day_one

    rehorizoned_code, rehorizoned = _run_shortlist(root, {**BASELINE, "horizon": "10d"})
    assert rehorizoned_code == 0, rehorizoned

    weight_code, weight_output = _compare(root, day_one, reweighted["shortlist_id"])
    horizon_code, horizon_output = _compare(root, day_one, rehorizoned["shortlist_id"])

    assert weight_code == int(PanelExit.bad_request)
    assert "components" in weight_output
    assert horizon_code == int(PanelExit.bad_request)
    assert "horizon" in horizon_output


def test_the_sdk_serves_the_same_comparison_the_command_line_prints(
    two_days: dict[str, Any],
) -> None:
    """One shape for two faces, `shortlist_view`'s own argument for existing at all."""
    sdk = OpenAlphaSDK(runtime_dir=two_days["runtime_dir"])

    served = sdk.compare_shortlists(
        baseline_id=two_days["day_one"]["shortlist_id"],
        current_id=two_days["day_two"]["shortlist_id"],
    )
    printed = _compared(
        two_days["runtime_dir"],
        two_days["day_one"]["shortlist_id"],
        two_days["day_two"]["shortlist_id"],
    )

    assert served == printed


def test_every_field_the_comparison_reports_is_the_one_the_answer_carried(
    two_days: dict[str, Any],
) -> None:
    """The binding between the unit fixture's shape and what `shortlist_view` really renders.

    `tests/unit/test_shortlist_comparison_rules.py` builds answers by hand, which is the only way to
    reach a refused list beside an admitted one cheaply -- and a hand-built fixture is a thing
    that drifts. This reads every side of a real comparison back against the real answer it came
    from, field by field, so a key `shortlist_view` renames goes red here rather than being
    discovered when a `KeyError` reaches a caller.
    """
    day_one, day_two = two_days["day_one"], two_days["day_two"]
    comparison = _compared(
        two_days["runtime_dir"], day_one["shortlist_id"], day_two["shortlist_id"]
    )
    entries = {entry["subject"]: entry for entry in comparison["entries"]}

    for answer, role in ((day_one, "baseline"), (day_two, "current")):
        header = comparison[role]
        assert header["shortlist_id"] == answer["shortlist_id"]
        assert header["as_of"] == answer["as_of"]
        assert header["cross_section_as_of"] == answer["cross_section"]["as_of"]
        assert header["is_blocked"] == answer["is_blocked"]
        assert header["blocks"] == [block["code"] for block in answer["blocks"]]
        assert header["shortlist_count"] == answer["measurement"]["shortlist_count"]
        assert header["candidate_count"] == answer["measurement"]["candidate_count"]

        for screened in answer["funnel"]["shortlist"]:
            side = entries[screened["subject"]][role]
            assert (side["rank"], side["score"]) == (screened["rank"], screened["score"])
            assert side["shortlisted"] is True
        for published in answer["admitted"]:
            side = entries[published["subject"]][role]
            assert side["admitted"] is True
            assert side["direction"] == published["direction"]
            assert side["confidence"] == published["confidence"]
            assert side["risk_flags"] == published["risk_flags"]
            assert side["run_manifest_id"] == published["run_manifest_id"]

    assert comparison["declaration"] == day_one["declaration"] == day_two["declaration"]
    assert comparison["horizon"] == day_one["horizon"]


def test_the_terminal_rendering_announces_a_refused_side_and_is_not_the_json_body(
    two_days: dict[str, Any],
) -> None:
    """Three properties of the human face that `--json` cannot stand in for (mutation sweep).

    A sweep found all three unasserted. **The refusal line**: `admitted` is `null` on a blocked
    answer, so every name on that side reads `admitted: false`, and a rendering that did not say
    the answer was *refused* would show a market in which nothing was published and no reason
    why -- `shortlist_view` calls that distinction the one the whole issue turns on. **Which
    side**: the line names `baseline` or `current`, and a rendering that named the wrong one
    sends a reader to re-run the wrong day. **Not JSON**: the previous rendering test asserted
    only that two security codes and the word `added` appear in the output, and all three appear
    in the JSON body too -- so flipping `--json`'s default passed it.
    """
    root = two_days["runtime_dir"]
    refused_code, refused = _run_shortlist(root, {**BASELINE, "minimum_researched_ratio": 1.0})
    assert refused_code == 1, refused
    assert refused["is_blocked"] is True
    assert refused["admitted"] is None

    code, output = _compare(
        root, two_days["day_one"]["shortlist_id"], refused["shortlist_id"], json_output=False
    )

    assert code == 0, output
    assert not output.strip().startswith("{")
    assert "refused" in output
    assert "current" in output
    assert "researched_ratio_below_floor" in output
    assert "baseline" in output.split("refused")[0]


def test_the_json_face_emits_one_deterministic_byte_sequence(
    two_days: dict[str, Any],
) -> None:
    """Sorted keys and one spelling per run, so two days of output diff to the difference.

    A sweep flipped `sort_keys` and `ensure_ascii` on this command's `json.dumps` and killed
    neither, because every assertion in this file parses the body before looking at it. A CLI
    whose key order moved between invocations would make `openalpha shortlist compare ... --json
    > today.json && diff yesterday.json today.json` useless, which is the obvious thing to do
    with this command's output.
    """
    root = two_days["runtime_dir"]
    addresses = (two_days["day_one"]["shortlist_id"], two_days["day_two"]["shortlist_id"])

    first = _compare(root, *addresses)[1].strip()
    again = _compare(root, *addresses)[1].strip()

    assert first == again
    assert first == json.dumps(json.loads(first), ensure_ascii=False, sort_keys=True)


SHANGHAI: Final[str] = "上交所"
"""An exchange name in the script this product's market actually uses.

`exchange`'s only request-time rule is `shortlist_view`'s "a non-empty name with no surrounding
whitespace", so this is a value the face accepts, not a value forced past a guard.
"""


def test_the_json_face_prints_a_non_ascii_exchange_as_itself_rather_than_as_escapes(
    two_days: dict[str, Any], tmp_path: Path
) -> None:
    """`ensure_ascii=False` on this command's `json.dumps`, killed rather than assumed equivalent.

    ## The survivor this closes

    A mutation sweep flipped `ensure_ascii` here and pytest stayed green, and the survivor was
    classified **provably equivalent**. It is not. It is equivalent *on the fixture only*:
    `EXCHANGE` is `"SZSE"`, every other string in the body is a `ts_code`, a factor key or an
    ISO instant, and `json.dumps` of an all-ASCII mapping is byte-identical under either setting.
    The test above cannot see it either, for the same reason -- it re-encodes the parsed body
    with `ensure_ascii=False` and compares, which on ASCII input is a tautology.

    The mutant is reachable through the ordinary face. `comparison` carries `declaration`
    verbatim (`shortlist_compare` builds it as `dict(baseline["declaration"])`), `declaration`
    carries `exchange`, and `exchange` is checked only for being a non-empty unpadded string. So
    `--exchange 上交所` renders as `\\u4e0a\\u4ea4\\u6240` under the mutant: still valid JSON, no
    longer the name anybody typed, and unreadable in the terminal this command prints to.

    ## Why the documents are rewritten rather than rebuilt

    A second full panel-and-two-factor build to change one string is the expensive way to ask a
    cheap question. These are the fixture's own documents, re-addressed after the edit through
    `stable_answer_digest` -- the same function `held_shortlist` verifies them with, so they are
    genuine held answers rather than smuggled ones. **Both** sides are moved together on purpose:
    `declaration` is in `COMPARABLE_KEYS`, so changing one alone would be refused as two answers
    to two different questions and would never reach the rendering. They are written to a fresh
    store, leaving the module-scoped fixture exactly as it was found.
    """
    source = two_days["runtime_dir"] / "shortlists"
    store = FileShortlistStore(tmp_path / "shortlists")
    addresses: list[str] = []
    for shortlist_id in (two_days["day_one"]["shortlist_id"], two_days["day_two"]["shortlist_id"]):
        document = json.loads(
            (source / f"{shortlist_id}{SHORTLIST_DOCUMENT_SUFFIX}").read_text(encoding="utf-8")
        )
        answer = document["answer"]
        assert answer["declaration"]["exchange"] == EXCHANGE, "the fixture stopped being ASCII"
        answer["declaration"]["exchange"] = SHANGHAI
        readdressed = stable_answer_digest(
            {key: value for key, value in answer.items() if key != "shortlist_id"}
        )
        answer["shortlist_id"] = readdressed
        document["shortlist_id"] = readdressed
        store.put(
            shortlist_id=readdressed,
            payload=json.dumps(document, ensure_ascii=False, sort_keys=True),
        )
        addresses.append(readdressed)

    code, output = _compare(tmp_path, *addresses)

    assert code == 0, output
    assert f'"exchange": "{SHANGHAI}"' in output
    assert "\\u" not in output
    assert json.loads(output.strip())["declaration"]["exchange"] == SHANGHAI
