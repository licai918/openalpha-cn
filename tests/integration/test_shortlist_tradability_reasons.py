"""`V2-P4-066`: the tradable tier used to name nothing -- not the rule, not the security.

The P4 third-round product acceptance ran a whole-market screen and read back
``funnel 5545 listed -> 5542 scored -> 5533 tradeable -> 25 shortlisted``, ``measured
tradable=0.9978``. The acceptor went and checked by hand that exactly nine names were halted that
day, that exactly nine were dropped, and that all nine carried ``coverage=computed`` in the factor
partition -- **so the second tier's arithmetic was right**. What the product could not say was
*which nine* and *under which rule*: the answer's ``funnel`` object held only
``clip_block``/``coverage``/``excluded_by_coverage``/``scored_count``/``shortlist``/
``tied_at_the_cut``/``tradeable_count``, ``excluded_by_coverage`` covers **stage one only**, and
the words ``halted``, ``below_board_minimum``, ``up_limit`` and ``not_tradable`` appeared nowhere
in the whole body. ``--min-tradable-ratio`` gates exactly that ratio, so a list could be refused
for a market fact the user had no way to see.

The census underneath was never the missing part. ``TradeabilityCensus`` has carried
``refused_by_verdict`` and ``rejection_reasons`` since `V2-P4-005`; neither reached a shipped
surface. That is why this file drives `CliRunner` and `TestClient` and never `run_shortlist`: a
test that imported the module would have been green throughout the defect.

## The fixture, and why these two sessions

Both arms run against one generated panel and one runtime directory, at two pricing sessions:

- **2026-01-06** priced at ``--position-capital 2000`` refuses two of eight scored names under
  **two different rules**: ``600519.SH`` is the ``price_limits.one_price_limit_up`` shape's locked
  security, so `AShareExecutionPolicy` rejects the buy in its own words, and one name is over
  budget for a single board lot, which is decided before the policy runs. Two rules and one policy
  reason in one answer is what stops an assertion passing on an implementation that reports a
  single bucket.
- **2026-01-13** priced at ``--position-capital 100000`` refuses nobody. It is what separates
  "every cell is reported and all of them are zero" from "the cells are omitted when empty" --
  `ScoreCensus.excluded_by_coverage`'s own stated rule, one tier down: *"nobody was
  `below_board_minimum`" and "nothing looked" are different claims*.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn import shortlist_view as shortlist_view_module
from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.cross_section import REFUSED_VERDICT_ORDER
from openalpha_cn.cli import app
from openalpha_cn.panel.store import PanelStore

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64

SHAPES: Final[tuple[str, ...]] = (
    "daily.close_moves_between_sessions",
    "price_limits.one_price_limit_up",
)
"""The locked bar is the whole point; the moving closes keep the ordering from being one tie."""

REFUSED_BUILD: Final[datetime] = datetime(2026, 1, 6, 9, 0, tzinfo=UTC)
REFUSED_AS_OF: Final[datetime] = datetime(2026, 1, 6, 23, 0, tzinfo=UTC)
CLEAN_BUILD: Final[datetime] = datetime(2026, 1, 13, 9, 0, tzinfo=UTC)
CLEAN_AS_OF: Final[datetime] = datetime(2026, 1, 13, 23, 0, tzinfo=UTC)

LOCKED: Final[str] = "600519.SH"
"""`price_limits.one_price_limit_up`'s security, whose bar publishes its upper limit at its own
one price -- so `AShareExecutionPolicy` refuses the buy and hands back its own sentence."""

LIMIT_UP_REASON: Final[str] = "buy cannot fill on a one-price limit-up bar"

OVER_BUDGET: Final[str] = "688981.SH"
"""The STAR-board name. `position_quantity` cannot buy its 200-share board lot out of 2,000 yuan,
so there is no order for the policy to refuse and the verdict is decided before it is called --
which is why this entry carries no `reason` and `600519.SH` does."""

BASELINE: Final[dict[str, Any]] = {
    "components": ({"factor": "reversal_1d/v1", "weight": 1.0},),
    "tier": "raw",
    "shortlist_size": 3,
    "position_capital": "2000",
    "as_of": REFUSED_AS_OF,
    "years": (YEAR,),
    "exchange": EXCHANGE,
    "horizon": "5d",
    "minimum_tradable_ratio": 0.0,
    "minimum_researched_ratio": 0.0,
    "maximum_ranking_age_days": 3_650,
    "code_commit": COMMIT,
    "config_digest": CONFIG_DIGEST,
}


@pytest.fixture(scope="module")
def runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One panel and one factor tier, built at both sessions the tests below price on."""
    root = tmp_path_factory.mktemp("shortlist-tradability")
    store = PanelStore(root / "panel")
    write_generated_panel(store, generate_panel(shapes=SHAPES))
    for instant in (REFUSED_BUILD, CLEAN_BUILD):
        built = CliRunner().invoke(
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
                str(root),
                "--as-of",
                instant.isoformat(),
                "--json",
            ],
        )
        assert built.exit_code == 0, built.output
    return root


@pytest.fixture
def rest(runtime_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=runtime_dir)) as client:
        yield client


def _arguments(runtime_dir: Path, parameters: Mapping[str, Any]) -> list[str]:
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
    ]
    for component in parameters["components"]:
        arguments.extend(["--component", f"{component['factor']}={component['weight']}"])
    for year in parameters["years"]:
        arguments.extend(["--year", str(year)])
    return arguments


def _run(runtime_dir: Path, parameters: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    result = CliRunner().invoke(app, [*_arguments(runtime_dir, parameters), "--json"])
    body = result.stdout.strip()
    if not body.startswith("{"):
        return result.exit_code, {"output": result.output}
    return result.exit_code, json.loads(body)


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


def test_the_tradable_tier_names_every_rule_and_every_security_it_dropped(
    runtime_dir: Path,
) -> None:
    """The whole of `V2-P4-066` on the `--json` face: the rules, the names, and the arithmetic.

    Eight scored, six tradeable, and the two that went are named -- one refused before the
    execution policy ran (`below_board_minimum`, decided by `--position-capital`) and one refused
    *by* it, carrying the policy's own sentence rather than a re-derivation here.

    The census cross-check is what stops this being a decorative list: the named entries are
    partitioned by verdict, and each partition has to equal the cell `refused_by_verdict` already
    reported, which is `ScoreCensus`' un-fudgeable-arithmetic rule applied to the tier that had
    none of it on a shipped surface.
    """
    code, answer = _run(runtime_dir, BASELINE)
    assert code == 0, answer

    funnel = answer["funnel"]
    assert funnel["scored_count"] == 8
    assert funnel["tradeable_count"] == 6

    refused = funnel["refused_by_verdict"]
    assert set(refused) == set(REFUSED_VERDICT_ORDER), (
        "every cell of the tier's vocabulary is reported, occurred or not. A set and not a list: "
        "the CLI serialises with sort_keys, so key order is the serialiser's and the declared "
        "order survives on `untradeable`, which is a list, and is asserted there"
    )
    assert refused == {
        "unbarred": 0,
        "unbanded": 0,
        "below_board_minimum": 1,
        "rejected": 1,
    }
    assert funnel["rejection_reasons"] == {LIMIT_UP_REASON: 1}

    named = funnel["untradeable"]
    assert funnel["untradeable_not_named"] == 0
    assert len(named) == funnel["scored_count"] - funnel["tradeable_count"] == 2
    assert [entry["subject"] for entry in named] == [OVER_BUDGET, LOCKED]
    assert not {entry["subject"] for entry in named} & {
        entry["subject"] for entry in funnel["shortlist"]
    }

    assert [entry["verdict"] for entry in named] == ["below_board_minimum", "rejected"], (
        "named in REFUSED_VERDICT_ORDER, so a reader scanning for one rule finds it in one place "
        "and two runs of one screen name the same securities in the same order"
    )
    by_verdict = {entry["verdict"]: entry for entry in named}
    assert by_verdict["rejected"] == {
        "subject": LOCKED,
        "verdict": "rejected",
        "reason": LIMIT_UP_REASON,
    }
    assert by_verdict["below_board_minimum"]["reason"] is None, (
        "only the policy's own refusal carries a reason; the other three are this tier's rules"
    )

    # ... and the named entries reconcile against the census, cell by cell.
    counted: dict[str, int] = {verdict: 0 for verdict in REFUSED_VERDICT_ORDER}
    for entry in named:
        counted[entry["verdict"]] += 1
    assert counted == refused


def test_a_clean_session_reports_every_cell_at_zero_rather_than_omitting_them(
    runtime_dir: Path,
) -> None:
    """ "Nobody was `below_board_minimum`" and "nothing looked" are different claims.

    `ScoreCensus.excluded_by_coverage`'s stated rule, one tier down, and the assertion that stops
    the test above passing on an implementation that reports only what occurred. On this session
    every scored name is buyable, so the four cells are present and zero and the named list is
    empty -- which a caller can tell apart from a build that never looked.
    """
    code, answer = _run(
        runtime_dir,
        {**BASELINE, "as_of": CLEAN_AS_OF, "position_capital": "100000"},
    )
    assert code == 0, answer

    funnel = answer["funnel"]
    assert funnel["scored_count"] == funnel["tradeable_count"] == 8
    assert funnel["refused_by_verdict"] == dict.fromkeys(REFUSED_VERDICT_ORDER, 0)
    assert funnel["rejection_reasons"] == {}
    assert funnel["untradeable"] == []
    assert funnel["untradeable_not_named"] == 0


def test_the_tradable_floor_refusal_names_the_rules_that_cost_the_ratio(
    runtime_dir: Path,
) -> None:
    """`--min-tradable-ratio` is the gate this row is about, and it used to say only a number.

    The refusal reported `6 of the 8 securities ... could be bought`, split the loss between the
    two stages, and stopped -- so a user who had just been refused had no way to learn that one
    name was limit-up and one was over budget, which are two different remedies (wait a session,
    or raise `--position-capital`).

    Driven at the terminal face as well as at `--json`, because the block's `detail` is what a
    person reads off stderr and the row is a *product* row.
    """
    refused = {**BASELINE, "minimum_tradable_ratio": 0.9}
    code, answer = _run(runtime_dir, refused)
    assert code == 1, answer
    assert answer["is_blocked"] is True

    blocks = {block["code"]: block for block in answer["blocks"]}
    assert "tradable_ratio_below_floor" in blocks
    detail = blocks["tradable_ratio_below_floor"]["detail"]
    assert "below_board_minimum" in detail
    assert "rejected" in detail
    assert LOCKED in detail
    assert OVER_BUDGET in detail
    assert LIMIT_UP_REASON in detail, (
        "the execution policy's own sentence is the half a user acts on: 'rejected' says the "
        "policy refused and only the sentence says a different session would fix it"
    )
    assert "unbarred" not in detail and "unbanded" not in detail, (
        "a sentence naming what went wrong must not bury it in the verdicts that did not occur; "
        "the census reports all four cells and this reports the ones that cost the ratio"
    )

    printed = CliRunner().invoke(app, _arguments(runtime_dir, refused))
    assert printed.exit_code == 1, printed.output
    assert "untradeable" in printed.output
    assert "below_board_minimum" in printed.output
    assert LOCKED in printed.output


def test_the_terminal_face_explains_stage_two_even_when_nothing_is_refused(
    runtime_dir: Path,
) -> None:
    """`unscored`'s sibling, and it has to be there on a list that *shipped*.

    The assertion above rides on the block detail, so it would pass on a build that only explained
    stage two when `--min-tradable-ratio` had already refused the list -- which is the smaller
    half of the row. `funnel 8 listed -> 8 scored -> 6 tradeable` is the line a person reads on a
    successful run, and until this row the second arrow had no explanation under it at any exit
    code.

    The clean session is the other direction: stage two refused nobody, so the line is omitted
    rather than printed as a row of noughts -- `unscored`'s own rule -- and the `--json` face
    still reports all four cells, which `test_a_clean_session_...` holds.
    """
    printed = CliRunner().invoke(app, _arguments(runtime_dir, BASELINE))
    assert printed.exit_code == 0, printed.output
    assert "untradeable {'below_board_minimum': 1, 'rejected': 1}" in printed.output
    assert f"below_board_minimum  {OVER_BUDGET}" in printed.output
    assert f"rejected             {LOCKED} ({LIMIT_UP_REASON})" in printed.output

    clean = CliRunner().invoke(
        app,
        _arguments(runtime_dir, {**BASELINE, "as_of": CLEAN_AS_OF, "position_capital": "100000"}),
    )
    assert clean.exit_code == 0, clean.output
    assert "untradeable" not in clean.output


def test_the_named_list_is_bounded_and_says_how_many_it_left_out(
    runtime_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MAX_NAMED_UNTRADEABLE` truncates and `untradeable_not_named` carries the residual.

    Driven with the ceiling lowered rather than with a market of fifty-one refused names, because
    the property under test is the *bound*, not the number: the shipped fifty leaves this
    repository's own whole-market acceptance -- nine names lost at this tier -- complete, so a
    fixture that reached it would be a fixture about nothing else. A mutation sweep found this
    assertion missing, which is how it comes to exist: `untradeable_not_named` hardcoded to `0`
    survived every other test in this file, and a truncated list reported as a complete one is
    exactly the reading `V2-P4-066` was filed against.

    The rendering reads the constant at call time, so the patch reaches the CLI running in this
    process -- and both halves are asserted, because a cap that truncated without counting and a
    counter that counted without truncating are two different defects.
    """
    monkeypatch.setattr(shortlist_view_module, "MAX_NAMED_UNTRADEABLE", 1)

    code, answer = _run(runtime_dir, BASELINE)
    assert code == 0, answer

    funnel = answer["funnel"]
    assert funnel["scored_count"] - funnel["tradeable_count"] == 2
    assert [entry["subject"] for entry in funnel["untradeable"]] == [OVER_BUDGET]
    assert funnel["untradeable_not_named"] == 1
    assert funnel["refused_by_verdict"] == {
        "unbarred": 0,
        "unbanded": 0,
        "below_board_minimum": 1,
        "rejected": 1,
    }, "the counts are keyed by a vocabulary and stay exact whatever the named list drops"
    assert funnel["rejection_reasons"] == {LIMIT_UP_REASON: 1}

    printed = CliRunner().invoke(app, _arguments(runtime_dir, BASELINE))
    assert printed.exit_code == 0, printed.output
    assert "... and 1 more, all counted above" in printed.output


def test_all_three_faces_carry_the_same_tradability_reasons(
    runtime_dir: Path, rest: TestClient
) -> None:
    """One renderer, three faces -- so the REST body cannot drift from the command line's.

    `shortlist_view` is the single rendering and this holds it to that: the same declaration over
    HTTP has to come back with the same four cells, the same reasons and the same names, and the
    same `shortlist_id` addressing them.
    """
    code, cli = _run(runtime_dir, BASELINE)
    assert code == 0, cli

    served = rest.post("/api/v1/shortlists/run", json=_rest_body(BASELINE))
    assert served.status_code == 200, served.text
    body = served.json()

    assert body["shortlist_id"] == cli["shortlist_id"]
    for key in ("refused_by_verdict", "rejection_reasons", "untradeable", "untradeable_not_named"):
        assert body["funnel"][key] == cli["funnel"][key], key
