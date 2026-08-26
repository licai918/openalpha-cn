"""`V2-P5-001` on the product surfaces: `openalpha portfolio construct` and the SDK.

The rule this repository learned four separate acceptances' worth of times: a policy that is only
reachable by importing modules by hand is not delivered. So every claim below starts at a
`CliRunner` or an `OpenAlphaSDK` over a real runtime directory -- a generated panel, a real
`openalpha factor build`, a real `openalpha shortlist run` whose answer is sealed and stored, and
the construction read back out of that store by its own address.

**What the fixture flatters, said once.** `panel_fixtures.generate_panel` builds closes and factor
values together, so the ordering the shortlist cuts is exactly the ordering the generator intended
and every admitted name is priced on the session it was scored on. The real corpus does not behave
that way -- `V2-P2` measured 5 of 151 sessions where the stored factor tier and the stored closes
disagreed about a security -- and on those sessions the *shortlist* changes, so a construction over
it weights different names. Nothing below depends on which names come back; the assertions are
about the arithmetic over whatever the gate admitted, which is the half that is fixture-independent.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.portfolio import PortfolioLimits
from openalpha_cn.backtest.portfolio_policy import PortfolioConstructionPolicy
from openalpha_cn.backtest.turnover_variants import TurnoverCostModel
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.sdk import OpenAlphaSDK

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64
HORIZON: Final[str] = "5d"
SHAPES: Final[tuple[str, ...]] = ("daily.close_moves_between_sessions",)
BUILD_AT: Final[datetime] = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
AS_OF: Final[datetime] = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
SHORTLIST_SIZE: Final[int] = 3
UNRESOLVABLE_RUN: Final[str] = "run_000000000000000000000000"

TIERS: Final[tuple[str, ...]] = ("0.5", "0.3", "0.2")
"""Three tiers over a three-name shortlist, so every name is its own tier.

Chosen so the three tier weights land as three *different* target weights -- 40%, 24% and 16% of
an 80% book. An even vector, or one tier holding all three names, would render the same numbers a
plain equal-weight policy renders, and no assertion below could tell the two apart.
"""


def _cli(*arguments: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, list(arguments))
    return result.exit_code, result.stdout, result.output


def _build_panel_and_factor(root: Path) -> None:
    write_generated_panel(PanelStore(root / "panel"), generate_panel(shapes=SHAPES))
    code, _, output = _cli(
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
        BUILD_AT.isoformat(),
        "--json",
    )
    assert code == 0, output


def _shortlist(root: Path, *, evidence: Path | None, researched: str) -> tuple[int, dict[str, Any]]:
    arguments = [
        "shortlist",
        "run",
        "--component",
        "reversal_1d/v1=1.0",
        "--tier",
        "raw",
        "--shortlist-size",
        str(SHORTLIST_SIZE),
        "--position-capital",
        "2000",
        "--year",
        str(YEAR),
        "--horizon",
        HORIZON,
        "--min-tradable-ratio",
        "0.0",
        "--min-researched-ratio",
        researched,
        "--max-ranking-age-days",
        "3650",
        "--exchange",
        EXCHANGE,
        "--as-of",
        AS_OF.isoformat(),
        "--code-commit",
        COMMIT,
        "--config-digest",
        CONFIG_DIGEST,
        "--runtime-dir",
        str(root),
        "--json",
    ]
    if evidence is not None:
        arguments.extend(["--evidence", str(evidence)])
    code, stdout, _ = _cli(*arguments)
    return code, json.loads(stdout.strip())


def _wire_evidence(subjects: Sequence[str], *, run_manifest_id: str | None = None) -> str:
    return json.dumps(
        {
            subject: {
                "signal": json.loads(
                    SignalFrame(
                        subject=subject,
                        as_of=AS_OF,
                        direction="bullish",
                        strength=0.4,
                        confidence=0.7,
                        horizon=HORIZON,
                        evidence_ids=("evd_000000000000000000000001",),
                    ).model_dump_json()
                ),
                "run_manifest_id": (
                    _manifest(subject).run_manifest_id
                    if run_manifest_id is None
                    else run_manifest_id
                ),
            }
            for subject in subjects
        }
    )


def _manifest(subject: str) -> RunManifest:
    return RunManifest(
        run_id=f"run-{subject}",
        mode="backtest",
        as_of=AS_OF,
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
        random_seed=7,
        started_at=AS_OF,
        finished_at=AS_OF,
        status="succeeded",
    )


@pytest.fixture(scope="module")
def admitted(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    """A runtime directory holding one stored shortlist the gate *admitted*, and its answer.

    Module-scoped because the whole panel/factor/evidence build is the same for every assertion
    below and none of them writes to it.
    """
    root = tmp_path_factory.mktemp("portfolio-construction")
    _build_panel_and_factor(root)
    code, first = _shortlist(root, evidence=None, researched="0.0")
    assert code == 0, first
    shortlisted = [entry["subject"] for entry in first["funnel"]["shortlist"]]

    sdk = OpenAlphaSDK(runtime_dir=root)
    for subject in shortlisted:
        sdk.repository.append_run(_manifest(subject))
    resolved = root / "resolved.json"
    resolved.write_text(_wire_evidence(shortlisted), encoding="utf-8")

    code, answer = _shortlist(root, evidence=resolved, researched="1.0")
    assert code == 0, answer
    assert answer["is_blocked"] is False
    assert len(answer["admitted"]) == SHORTLIST_SIZE, answer
    return root, answer


def _construct(root: Path, shortlist_id: str, *extra: str) -> tuple[int, str, str]:
    arguments = ["portfolio", "construct", shortlist_id]
    for weight in TIERS:
        arguments.extend(["--tier-weight", weight])
    arguments.extend(["--runtime-dir", str(root), "--max-position-weight", "0.45", *extra])
    return _cli(*arguments)


def _construct_with_default_caps(root: Path, shortlist_id: str) -> tuple[int, str, str]:
    """The same construction with no `--max-position-weight`, i.e. the shipped 25% default.

    Its own helper because the default *erases the tier structure* on a three-name list -- 40%,
    24% and 16% under a 25% cap trims to 25/24/16 and then redistributes -- and that is a fact
    worth asserting rather than working around silently. The helper above opens the cap to 45% so
    the tiering and the trimming can be measured apart; this one leaves it where a caller who
    types nothing finds it.
    """
    arguments = ["portfolio", "construct", shortlist_id]
    for weight in TIERS:
        arguments.extend(["--tier-weight", weight])
    arguments.extend(["--runtime-dir", str(root), "--json"])
    return _cli(*arguments)


def test_the_cli_weights_a_stored_shortlist_and_labels_the_answer_a_heuristic(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """The whole chain, from the command line: panel, factor, shortlist, construction.

    Three assertions that no one of them implies. The label is on the body (`V2-P5-001`'s stated
    requirement). The weights are the tier vector's own -- 40/24/16 of an 80% book, three
    different numbers, so an equal-weight implementation is red here. And the subjects are exactly
    the names the gate admitted, in rank order, so a construction over some other list is red too.
    """
    root, answer = admitted
    code, stdout, output = _construct(root, answer["shortlist_id"], "--json")
    assert code == 0, output
    body = json.loads(stdout.strip())

    assert body["method"] == "heuristic, not optimized"
    assert [target["weight"] for target in body["targets"]] == [
        "0.400000",
        "0.240000",
        "0.160000",
    ]
    assert [target["subject"] for target in body["targets"]] == [
        entry["subject"] for entry in sorted(answer["admitted"], key=lambda row: row["rank"])
    ]
    assert [target["tier"] for target in body["targets"]] == [1, 2, 3]
    assert body["cash_weight"] == "0.200000"

    _, defaulted, _ = _construct_with_default_caps(root, answer["shortlist_id"])
    capped = json.loads(defaulted.strip())

    assert [target["weight"] for target in capped["targets"]] == [
        "0.250000",
        "0.250000",
        "0.250000",
    ]
    assert [target["was_adjusted"] for target in capped["targets"]] == [True, True, True]


def test_the_terminal_rendering_carries_the_label_beside_the_numbers(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """A caveat only `--json` carries is a caveat the person reading the weights never sees."""
    root, answer = admitted
    code, stdout, output = _construct(root, answer["shortlist_id"])

    assert code == 0, output
    assert stdout.splitlines()[0] == "method: heuristic, not optimized"
    assert "tier 1" in stdout


def test_the_sdk_and_the_cli_serve_one_construction(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """Byte equality, `shortlist_view`'s rule applied to this face: two renderings of one answer
    that disagree about which keys exist is how a caller comes to believe a cap held."""
    root, answer = admitted
    _, stdout, _ = _construct(root, answer["shortlist_id"], "--json")
    sdk = OpenAlphaSDK(runtime_dir=root)

    rendered = sdk.construction_view(
        sdk.construct_portfolio(
            shortlist_id=answer["shortlist_id"],
            policy=PortfolioConstructionPolicy(
                tier_weights=tuple(Decimal(weight) for weight in TIERS),
                limits=PortfolioLimits(max_position_weight=Decimal("0.45")),
            ),
        )
    )

    assert json.dumps(rendered, ensure_ascii=False, sort_keys=True) == stdout.strip()


def test_a_shortlist_the_gate_refused_cannot_be_turned_into_weights_on_either_face(
    tmp_path: Path,
) -> None:
    """The refusal, driven twice, on its own store.

    A refused list is stored -- `openalpha shortlist run` exits `1` and still files the answer --
    so it *has* an address and a caller can ask for weights over it. Building them would launder
    the gate's refusal into a set of numbers, which is the "empty success" `V2-P1-013` exists to
    make unavailable arriving one plane later.

    Its own runtime directory rather than the module fixture, because the refusal has to come from
    a shortlist run that was actually refused, and the fixture's was admitted.
    """
    _build_panel_and_factor(tmp_path)
    code, first = _shortlist(tmp_path, evidence=None, researched="0.0")
    assert code == 0, first
    shortlisted = [entry["subject"] for entry in first["funnel"]["shortlist"]]
    invented = tmp_path / "invented.json"
    invented.write_text(
        _wire_evidence(shortlisted, run_manifest_id=UNRESOLVABLE_RUN), encoding="utf-8"
    )

    refused_code, refused = _shortlist(tmp_path, evidence=invented, researched="1.0")
    assert (refused_code, refused["admitted"]) == (int(PanelExit.unhealthy), None)

    code, _, output = _construct(tmp_path, refused["shortlist_id"], "--json")
    sdk = OpenAlphaSDK(runtime_dir=tmp_path)

    assert code == int(PanelExit.bad_request)
    assert "refused by the gate" in output
    with pytest.raises(ValueError, match="refused by the gate"):
        sdk.construct_portfolio(
            shortlist_id=refused["shortlist_id"],
            policy=PortfolioConstructionPolicy(
                tier_weights=tuple(Decimal(weight) for weight in TIERS)
            ),
        )


def test_an_industry_cap_is_refused_on_this_face_rather_than_silently_unenforceable(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """The measured state of the shipped chain: `shortlist_view` builds its ranking with
    `exposures=None`, so no stored answer carries an industry for any name.

    A cap over names with no industry is satisfied by every book, so it is refused. Asserted from
    the command line because that is where a caller would type the flag and read the sentence.
    """
    root, answer = admitted
    code, _, output = _construct(
        root, answer["shortlist_id"], "--max-industry-weight", "0.20", "--json"
    )

    assert code == int(PanelExit.bad_request)
    assert "carry no `industry_code`" in output
    assert all("industry_code" not in row for row in answer["admitted"])


def test_a_turnover_budget_typed_on_the_command_line_damps_the_move_and_reports_both_numbers(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """The third step, driven. The book starts at 5% in the top-ranked name and the budget is a
    tenth of the move the policy wants.

    Both numbers are asserted, and the control is the same command with no budget: without the
    contrast, `turnover <= budget` is satisfied by a build that returns the previous book
    unchanged, and `turnover_before_budget` is satisfied by one that ignores `--previous-weight`.
    """
    root, answer = admitted
    top = min(answer["admitted"], key=lambda row: row["rank"])["subject"]
    previous = ["--previous-weight", f"{top}=0.05"]

    _, undamped, _ = _construct(root, answer["shortlist_id"], "--json", *previous)
    _, damped, output = _construct(
        root, answer["shortlist_id"], "--json", "--turnover-budget", "0.075", *previous
    )
    without = json.loads(undamped.strip())
    with_budget = json.loads(damped.strip())

    assert without["turnover_damping"] is None
    assert Decimal(without["turnover"]) == Decimal("0.750000")
    assert Decimal(with_budget["turnover_before_budget"]) == Decimal("0.750000")
    assert Decimal(with_budget["turnover"]) <= Decimal("0.075")
    assert with_budget["turnover_damping"] is not None, output
    weights = {target["subject"]: Decimal(target["weight"]) for target in with_budget["targets"]}
    assert Decimal("0.05") < weights[top] < Decimal("0.40")


def test_a_previous_weight_flag_that_is_not_a_pair_is_refused_before_any_store_is_opened(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """A malformed flag is `bad_request` and names the shape it wanted.

    The address handed in is a well-formed one this runtime directory *does* hold, so the refusal
    below cannot be the store's -- which is the confusion that made an earlier test in this
    repository green under every implementation.
    """
    root, answer = admitted
    code, _, output = _construct(
        root, answer["shortlist_id"], "--previous-weight", "000001.SZ", "--json"
    )

    assert code == int(PanelExit.bad_request)
    assert "SUBJECT=WEIGHT" in output


# --- the third face (`V2-P5-013`) ----------------------------------------------------------


@pytest.fixture(scope="module")
def rest(admitted: tuple[Path, dict[str, Any]]) -> Iterator[TestClient]:
    """`POST /api/v1/portfolio/construct` over the same runtime directory the CLI just used.

    Module-scoped for the fixture it depends on: nothing below writes to that store, and a
    per-test `create_app` would re-run the migration engine once per assertion.
    """
    root, _ = admitted
    with TestClient(create_app(runtime_dir=root)) as client:
        yield client


def _rest_body(shortlist_id: str, **limits: str) -> dict[str, Any]:
    """The CLI invocation `_construct` types, as the request body this route takes.

    The three `limits` keys are stated rather than left to default because `_construct` states
    them: `--max-position-weight 0.45` is not the shipped default and the other two are, so a
    body that omitted them would be comparing a 45% cap against a 25% one.
    """
    return {
        "shortlist_id": shortlist_id,
        "policy": {
            "tier_weights": list(TIERS),
            "limits": {
                "max_position_weight": "0.45",
                "max_total_exposure": "0.80",
                "min_cash_weight": "0",
                **limits,
            },
        },
    }


def test_the_rest_route_serves_the_construction_the_cli_and_the_sdk_serve(
    admitted: tuple[Path, dict[str, Any]], rest: TestClient
) -> None:
    """`V2-P5-013`: the third face, and it renders through `construction_view` like the other two.

    Byte equality against the CLI's own `--json`, which is the assertion
    `test_the_sdk_and_the_cli_serve_one_construction` already makes for the SDK. Two renderings
    of one answer that disagree about which keys exist is how a caller comes to believe a cap
    held; a third rendering makes that three ways to be wrong rather than two.
    """
    root, answer = admitted
    _, stdout, _ = _construct(root, answer["shortlist_id"], "--json")

    response = rest.post("/api/v1/portfolio/construct", json=_rest_body(answer["shortlist_id"]))

    assert response.status_code == 200, response.text
    assert json.dumps(response.json(), ensure_ascii=False, sort_keys=True) == stdout.strip()


def test_the_rest_route_refuses_a_gate_refused_shortlist_in_the_cli_s_own_words(
    tmp_path: Path,
) -> None:
    """The refusal, driven on the third face, and asserted *equal* to the first face's.

    Its own runtime directory rather than the module fixture, because the refusal has to come
    from a shortlist run that was actually refused and the fixture's was admitted.

    The equality is the point rather than the substring. `V2-P4-101` made the API's refusal
    byte-identical to pydantic's for the same fault and pinned it with an equality assertion
    against the other route, precisely so the two could not drift into two dialects; a `422`
    here reading only "cannot construct" would satisfy a substring check on `refused` and lose
    the sentence that says what to do instead.
    """
    _build_panel_and_factor(tmp_path)
    code, first = _shortlist(tmp_path, evidence=None, researched="0.0")
    assert code == 0, first
    shortlisted = [entry["subject"] for entry in first["funnel"]["shortlist"]]
    invented = tmp_path / "invented.json"
    invented.write_text(
        _wire_evidence(shortlisted, run_manifest_id=UNRESOLVABLE_RUN), encoding="utf-8"
    )
    refused_code, refused = _shortlist(tmp_path, evidence=invented, researched="1.0")
    assert (refused_code, refused["admitted"]) == (int(PanelExit.unhealthy), None)

    cli_code, _, cli_output = _construct(tmp_path, refused["shortlist_id"], "--json")
    with TestClient(create_app(runtime_dir=tmp_path)) as client:
        response = client.post(
            "/api/v1/portfolio/construct", json=_rest_body(refused["shortlist_id"])
        )

    assert cli_code == int(PanelExit.bad_request)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["reason"] == "bad_request"
    assert detail["message"] == cli_output.strip(), (
        "both faces refuse the same construction, so they must say so in the same words"
    )


def test_an_industry_cap_is_refused_on_the_rest_face_in_the_cli_s_own_words(
    admitted: tuple[Path, dict[str, Any]], rest: TestClient
) -> None:
    """The second refusal driven across both faces, so the equality above is not a one-off.

    A single asserted-equal message can be satisfied by a route that hard-codes that one
    sentence. This one is raised at a different place in `portfolio_policy` and reaches the
    boundary through the same `except`.
    """
    root, answer = admitted
    _, _, cli_output = _construct(
        root, answer["shortlist_id"], "--max-industry-weight", "0.20", "--json"
    )

    response = rest.post(
        "/api/v1/portfolio/construct",
        json=_rest_body(answer["shortlist_id"], max_industry_weight="0.20"),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["message"] == cli_output.strip()


def test_a_malformed_address_and_an_absent_one_stay_two_answers_on_this_route(
    rest: TestClient,
) -> None:
    """`404` and `422` are the shortlist plane's own two rows, and this route reuses that table.

    Both are driven, because either alone is green under a build that has no route at all:
    FastAPI answers an unrouted `POST` with `404` and a `detail` that is the **string**
    `"Not Found"`. So each assertion checks the envelope *and* that `detail` is this module's
    `{reason, message}` object, which the router's own 404 is not.
    """
    absent = rest.post("/api/v1/portfolio/construct", json=_rest_body("sla_" + "0" * 24))
    malformed = rest.post("/api/v1/portfolio/construct", json=_rest_body("not-an-address"))

    assert absent.status_code == 404, absent.text
    assert isinstance(absent.json()["detail"], dict), absent.text
    assert absent.json()["detail"]["reason"] == "not_held"
    assert malformed.status_code == 422, malformed.text
    assert isinstance(malformed.json()["detail"], dict), malformed.text
    assert malformed.json()["detail"]["reason"] == "bad_request"


def test_a_tier_vector_that_does_not_sum_to_one_is_refused_alike_on_both_faces(
    admitted: tuple[Path, dict[str, Any]], rest: TestClient
) -> None:
    """The policy's own validator, met from the command line and over HTTP.

    `PortfolioConstructionPolicy` is the request body's own field rather than a set of loose
    numbers this route re-assembles, so the sentence a caller reads is pydantic's -- the same
    one the CLI prints, because the CLI builds the same model. Asserted as containment rather
    than equality, since pydantic wraps a `model_validator` message with its own `Value error, `
    prefix on the HTTP face and with a location header on the terminal one.
    """
    root, answer = admitted
    arguments = ["portfolio", "construct", answer["shortlist_id"]]
    for weight in ("0.5", "0.3"):
        arguments.extend(["--tier-weight", weight])
    arguments.extend(["--runtime-dir", str(root), "--json"])
    cli_code, _, cli_output = _cli(*arguments)

    body = _rest_body(answer["shortlist_id"])
    body["policy"]["tier_weights"] = ["0.5", "0.3"]
    response = rest.post("/api/v1/portfolio/construct", json=body)

    assert cli_code == int(PanelExit.bad_request)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list), "a body pydantic itself rejected is a list of field errors"
    sentence = "tier weights must sum to exactly 1"
    assert sentence in detail[0]["msg"]
    assert sentence in cli_output


# --- V2-P5-024: the buffered arm beside the unbuffered one, on both faces ----------------------
#
# The same stored shortlist, the same tier vector and the same 45% cap the construction tests
# use, so the unbuffered arm here *is* the book those tests already pinned at 40/24/16. Every
# number below is therefore a statement about the band and nothing else.
#
# The previous book is chosen to give four requested moves of four different sizes -- 0.100,
# 0.020, 0.005 and 0.030 -- because a band set at any one of them then suppresses a known subset
# and nothing else. Equal moves would make the staircase blind to an off-by-one in the
# comparison, which is the same trap `tests/unit/backtest/test_turnover_variants.py` documents.

DROPPED_NAME: Final[str] = "999999.SZ"
"""A held name the shortlist does not admit, so its requested move is its whole weight."""

TURNOVER_STAIRCASE: Final[tuple[tuple[str, str, int], ...]] = (
    ("0", "0.155000", 4),
    ("0.005", "0.150000", 3),
    ("0.020", "0.130000", 2),
    ("0.030", "0.100000", 1),
)
"""`(band, buffered turnover, names traded)`. The row's declared seam is the last rung: the
buffered arm trades `0.100` where the unbuffered one trades `0.155`, a 35% reduction."""


def _previous_book(answer: dict[str, Any]) -> dict[str, str]:
    """A previous book giving four distinct requested moves against the 40/24/16 target."""
    ranked = [entry["subject"] for entry in sorted(answer["admitted"], key=lambda row: row["rank"])]
    return {
        ranked[0]: "0.300000",  # -> 0.400000, a move of 0.100
        ranked[1]: "0.235000",  # -> 0.240000, a move of 0.005
        ranked[2]: "0.140000",  # -> 0.160000, a move of 0.020
        DROPPED_NAME: "0.030000",  # -> 0,        a move of 0.030
    }


def _variants(root: Path, shortlist_id: str, answer: dict[str, Any], *extra: str):
    arguments = ["portfolio", "turnover-variants", shortlist_id]
    for weight in TIERS:
        arguments.extend(["--tier-weight", weight])
    for subject, weight in _previous_book(answer).items():
        arguments.extend(["--previous-weight", f"{subject}={weight}"])
    arguments.extend(["--runtime-dir", str(root), "--max-position-weight", "0.45", *extra])
    return _cli(*arguments)


@pytest.mark.parametrize(("band", "turnover", "traded"), TURNOVER_STAIRCASE)
def test_the_cli_buffered_arm_trades_less_than_the_unbuffered_one_at_every_band(
    admitted: tuple[Path, dict[str, Any]], band: str, turnover: str, traded: int
) -> None:
    """`V2-P5-024`'s own declared integration seam: 缓冲版换手显著低于无缓冲版.

    Every rung of the staircase, from the command line, over a really stored shortlist. The two
    rungs whose band sits exactly on a requested move are where a `<` instead of a `<=` shows up.
    """
    root, answer = admitted
    code, stdout, output = _variants(
        root, answer["shortlist_id"], answer, "--buffer", band, "--json"
    )

    assert code == 0, output
    body = json.loads(stdout.strip())
    unbuffered, buffered = body["arms"]

    assert unbuffered["label"] == "unbuffered"
    assert buffered["label"] == "buffered"
    assert unbuffered["turnover"] == "0.155000"
    assert buffered["turnover"] == turnover
    assert buffered["names_traded"] == traded
    assert unbuffered["names_traded"] == 4


def test_the_cli_cannot_be_asked_for_one_arm(admitted: tuple[Path, dict[str, Any]]) -> None:
    """The row is *默认并列出报*: both arms are on every answer, on both faces."""
    root, answer = admitted
    code, stdout, output = _variants(
        root, answer["shortlist_id"], answer, "--buffer", "0.030", "--json"
    )

    assert code == 0, output
    body = json.loads(stdout.strip())
    assert [arm["label"] for arm in body["arms"]] == ["unbuffered", "buffered"]

    code, terminal, output = _variants(root, answer["shortlist_id"], answer, "--buffer", "0.030")
    assert code == 0, output
    assert "unbuffered" in terminal
    assert "buffered" in terminal
    assert terminal.splitlines()[0] == "method: heuristic, not optimized"


def test_the_terminal_face_says_the_saving_and_the_price_are_the_same_number(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """The identity that removed a column, printed where the person reading the numbers is."""
    root, answer = admitted
    code, stdout, output = _variants(root, answer["shortlist_id"], answer, "--buffer", "0.030")

    assert code == 0, output
    assert "the band saved 0.055000 of turnover and put the book exactly 0.055000 away" in stdout
    assert "these are the same number, one for one" in stdout


def test_a_name_the_shortlist_dropped_and_the_band_kept_is_named_on_the_command_line(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """A buffered run still holding a name its own ranking no longer admits says which name."""
    root, answer = admitted
    code, stdout, output = _variants(root, answer["shortlist_id"], answer, "--buffer", "0.030")

    assert code == 0, output
    assert f"retained by the band though the ranking dropped it: {DROPPED_NAME}" in stdout

    code, narrow, output = _variants(root, answer["shortlist_id"], answer, "--buffer", "0.020")
    assert code == 0, output
    assert "retained by the band" not in narrow


def test_no_declared_rate_publishes_no_money_figure_and_says_why(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """An invented default would be multiplied by every turnover number printed."""
    root, answer = admitted
    code, stdout, output = _variants(root, answer["shortlist_id"], answer, "--buffer", "0.030")

    assert code == 0, output
    assert "no cost figure --" in stdout
    assert "would be a number this module invented" in stdout


def test_a_declared_rate_costs_both_arms_and_the_saving_between_them(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    root, answer = admitted
    code, stdout, output = _variants(
        root,
        answer["shortlist_id"],
        answer,
        "--buffer",
        "0.030",
        "--cost-per-unit-turnover",
        "0.001",
        "--cost-definition",
        "commission and stamp duty",
        "--json",
    )

    assert code == 0, output
    body = json.loads(stdout.strip())
    assert body["arms"][0]["turnover_cost"] == "0.000155000"
    assert body["arms"][1]["turnover_cost"] == "0.000100000"
    assert body["cost_saved"] == "0.000055000"
    assert body["cost_absence_reason"] is None


def test_a_rate_without_a_definition_is_refused_before_any_store_is_opened(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """A money figure whose meaning is unrecorded cannot be reproduced or compared."""
    root, answer = admitted
    code, _stdout, output = _variants(
        root,
        answer["shortlist_id"],
        answer,
        "--buffer",
        "0.030",
        "--cost-per-unit-turnover",
        "0.001",
    )

    assert code == PanelExit.bad_request, output
    assert "--cost-per-unit-turnover needs --cost-definition" in output


def test_a_band_outside_the_unit_interval_is_refused(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    root, answer = admitted
    code, _stdout, output = _variants(root, answer["shortlist_id"], answer, "--buffer", "1.5")

    assert code == PanelExit.bad_request, output
    assert "is not a weight" in output


def test_the_sdk_and_the_cli_serve_one_paired_report(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """Byte equality, `construction_view`'s rule applied to this face."""
    root, answer = admitted
    _code, stdout, _output = _variants(
        root, answer["shortlist_id"], answer, "--buffer", "0.030", "--json"
    )
    sdk = OpenAlphaSDK(runtime_dir=root)

    rendered = sdk.turnover_variant_view(
        sdk.turnover_variants(
            shortlist_id=answer["shortlist_id"],
            policy=PortfolioConstructionPolicy(
                tier_weights=tuple(Decimal(weight) for weight in TIERS),
                limits=PortfolioLimits(max_position_weight=Decimal("0.45")),
            ),
            buffer=Decimal("0.030"),
            previous={
                subject: Decimal(weight) for subject, weight in _previous_book(answer).items()
            },
        )
    )

    assert json.dumps(rendered, ensure_ascii=False, sort_keys=True) == stdout.strip()


def test_the_sdk_unbuffered_arm_is_the_book_construct_portfolio_returns(
    admitted: tuple[Path, dict[str, Any]],
) -> None:
    """One construction, seen twice -- so every difference between the arms is the band.

    Constructing separately for each arm would let a second difference in, and the comparison
    would stop being a comparison of one thing.
    """
    root, answer = admitted
    sdk = OpenAlphaSDK(runtime_dir=root)
    policy = PortfolioConstructionPolicy(
        tier_weights=tuple(Decimal(weight) for weight in TIERS),
        limits=PortfolioLimits(max_position_weight=Decimal("0.45")),
    )
    previous = {subject: Decimal(weight) for subject, weight in _previous_book(answer).items()}

    construction = sdk.construct_portfolio(
        shortlist_id=answer["shortlist_id"], policy=policy, previous=previous
    )
    paired = sdk.turnover_variants(
        shortlist_id=answer["shortlist_id"],
        policy=policy,
        buffer=Decimal("0.030"),
        previous=previous,
        cost_model=TurnoverCostModel(
            cost_per_unit_turnover=Decimal("0.001"), definition="commission and stamp duty"
        ),
    )

    assert dict(paired.unbuffered.weights) == {
        target.subject: target.weight for target in construction.targets if target.weight
    }
    assert paired.unbuffered.turnover == construction.turnover
    assert paired.buffered.turnover < paired.unbuffered.turnover
    assert paired.method == construction.method
