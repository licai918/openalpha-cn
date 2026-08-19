"""The two-stage funnel, the ranking and the gate, reached from where a user stands.

## What was measured before this file existed

`V2-P4-004`, `V2-P4-005` and `V2-P4-023` ship `CrossSectionScreen`, `rank_candidates` and
`gate_shortlist` under 159 passing tests, and at `824ebff` **not one of those tests started at a
shipped surface**: `grep -ln "CliRunner\\|TestClient\\|OpenAlphaSDK\\|openalpha_cn.cli\\|api.app"`
matched none of the five files that drive them. Two consequences were measured rather than argued:

- **`V2-P4-032`.** `grep -rn "ComponentCrossSection(" src` returned nothing. The funnel's required
  input was constructed only by tests, so `openalpha factor build` wrote tiers into a panel that
  nothing could turn into a cross section a screen reads.
- **`V2-P4-033`.** The CLI had ten commands and none of them; of 34 REST routes,
  `shortlist`/`rank`/`cross`/`funnel`/`candidate` were all absent; `OpenAlphaSDK`'s 32 public
  methods likewise.

So every assertion in this file starts at `CliRunner`, `TestClient` or `OpenAlphaSDK`. A test that
imported `openalpha_cn.shortlist_view` and called it directly would pass on a tree where the three
faces do not exist, which is exactly the state this file was written to make impossible.

## The distinction this file exists to protect

`V2-P4-023`'s gate tells "blocked" from "empty" inside the library -- `ShortlistClearance.__bool__`
raises rather than answering. At a surface that guarantee has to be re-made in JSON and in an exit
code, because a caller reading `{"admitted": []}` cannot ask a dict whether it was refused.
`test_a_blocked_shortlist_and_a_legitimately_empty_one_are_two_different_answers` drives both
halves off **one store, one command line, one flag apart**, so the fixture cannot make them differ
by accident.

## The fixture, and why it is not `store_three_tiers`

Two properties are needed that the shared three-tier fixture does not have, and both were measured
before this file chose to build its own:

- **Two factor builds an `as_of` can sit between.** `store_three_tiers` stamps its builds at
  09:00Z on 2026-01-08 and 2026-01-09, and the generated price panel does not become readable
  until 2026-01-16T08:30Z (`load_daily_bars` goes through `read_if_ready`, which refuses a
  partition whose newest row post-dates the `as_of`). So at every instant where a shortlist can be
  priced at all, *both* of those builds are already visible and no `as_of` separates them. The two
  builds here are at 09:00Z and 13:00Z on 2026-01-16 -- after the panel is readable, four hours
  apart -- so `EARLY_AS_OF` sits between them with the market fully priceable on both sides.
- **A tier that actually carries values.** On this generated eight-security panel every processed
  row comes back `insufficient_cross_section`, so a processed-tier screen has nothing to order at
  all. The raw tier carries a value for all eight, which is why the baseline screens on it.

  That thin processed tier is not merely avoided here -- since `V2-P4-044` it is *driven*, by
  `processed_runtime_dir`, because it is the **declared** configuration and the answer it produces
  is the whole of that issue. It used to reach the gate and come back blaming the evidence plane;
  it is now refused by name, before the gate, saying which floor it missed.

The values themselves come from `compute_factor`'s own documented `evaluators` seam, negated
between the two builds. What is under test here is the **visibility filter**, not the factor
arithmetic: two builds whose values were identical could not show an adapter telling them apart.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import panel_fixtures
import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, SECURITIES, YEAR, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    FACTOR_DEFINITIONS,
    FactorPanel,
    apply_factor_transform,
    compute_factor,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import daily_requirement
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.shortlist_view import ShortlistEvidence, ShortlistRequestError

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
"""The repository root, for the two tests below that hold a *document* to what this face does."""

COMMIT: Final[str] = "abcdef1234567"
CONFIG_DIGEST: Final[str] = "d" * 64

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
"""The one shipped factor a ten-session panel can carry, and it is `lower_is_better`.

That direction is load-bearing for the expectations below: `oriented_value` negates it, so the
*smallest* stored value scores highest and the shortlist is the low end of the column.
"""

FIRST_BUILD: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
SECOND_BUILD: Final[datetime] = datetime(2026, 1, 16, 13, 0, tzinfo=UTC)
"""The two instants this file's factor partition holds a cross section at.

Both on the panel's last stored session and both after it became readable; see this module's
docstring for why they are not `store_three_tiers`' two prediction days.
"""

EARLY_AS_OF: Final[datetime] = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
"""Between the two builds. The whole look-ahead measurement is that a screen asked here carries
the 09:00Z cross section and not one value out of the 13:00Z one, which is in the same file."""

LATE_AS_OF: Final[datetime] = datetime(2026, 1, 16, 14, 0, tzinfo=UTC)
"""After both. Without this half the test above would pass on an adapter that returned nothing."""

MUCH_LATER_AS_OF: Final[datetime] = datetime(2026, 1, 30, 12, 0, tzinfo=UTC)
"""A fortnight after the newest stored cross section, on a session the panel does not hold.

The instant that separates "priced on the cross section's own session" from "priced on the
day the question was asked": 2026-01-30 is a trading day in the stored calendar and there is
no bar for it in the store, so a face that resolved the session from the *request* would ask
for bars that do not exist. A mutation that made exactly that substitution survived the
fixture above, where the request instant and the cross section instant fall on one session.
"""

PRICING_SESSION: Final[date] = date(2026, 1, 16)
"""The session both builds are about: 17:00 and 21:00 Asia/Shanghai on 2026-01-16, after its
close. Every security in the registry has a bar and a published band on it."""

HORIZON: Final[str] = "5d"
SHORTLIST_SIZE: Final[int] = 2
POSITION_CAPITAL: Final[str] = "1250"
"""A budget that buys one 100-share lot of a name at 12.00 yuan and not one at 13.00.

The generator's closes on 2026-01-16 run 10.0 to 17.0 in `SECURITIES` order, so this splits the
eight names three/five at a place no assertion below has to name a price to describe.
"""

UNIVERSE_COUNT: Final[int] = 8
TRADEABLE_COUNT: Final[int] = 3
TRADABLE_RATIO: Final[float] = TRADEABLE_COUNT / UNIVERSE_COUNT
"""0.375 -- three of the eight listed names could be bought at this capital. Measured below."""

EARLY_SHORTLIST: Final[tuple[str, ...]] = ("000001.SZ", "000002.SZ")
"""The first build's top two: `lower_is_better` on `+0.01, +0.02, ...` picks the two smallest,
and both happen to be inside the three the market would sell at this capital."""

LATE_SHORTLIST: Final[tuple[str, ...]] = ("600000.SH", "000002.SZ")
"""The second build's, whose values are the first's negated: the ordering inverts, the two
cheapest-scoring names are no longer the two the market will sell, and what survives both stages
is a different pair. That the two lists differ is what makes the look-ahead test's `as_of` the
only thing separating them."""

BASELINE: Final[dict[str, Any]] = {
    "components": ({"factor": "reversal_1d/v1", "weight": 1.0},),
    "tier": "raw",
    "shortlist_size": SHORTLIST_SIZE,
    "position_capital": POSITION_CAPITAL,
    "as_of": EARLY_AS_OF,
    "years": (YEAR,),
    "exchange": EXCHANGE,
    "horizon": HORIZON,
    "minimum_tradable_ratio": 0.0,
    "minimum_researched_ratio": 0.0,
    "maximum_ranking_age_days": 3_650,
    "code_commit": COMMIT,
    "config_digest": CONFIG_DIGEST,
}
"""Every declared parameter of one shortlist run, once, so the three faces are driven from one
dict rather than from three literal argument lists that can drift apart -- which is the drift the
whole `V2-P4-033` finding is about, arriving in the test file itself.

The three bars are the **inert** ones -- floors of zero and a decade of staleness -- so a test
that wants a refusal raises exactly one at its own call site and nothing else in the request is
doing the work.
"""

PROCESSED: Final[dict[str, Any]] = {
    "tier": "processed",
    "transform": "cross_section_standard/v1",
}
"""The overlay that turns `BASELINE` into the **declared** configuration `V2-P4-044` was filed on.

`compute_factor -> apply_factor_transform(cross_section_standard/v1) ->
write_processed_factor_panels` is what `openalpha factor build` runs, so this is not a corner: it
is what every user meets who tries the processed tier on a market narrower than the transform's
`min_cross_section` of 100.
"""


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


def _run_manifest_id(subject: str, *, as_of: datetime) -> str:
    return RunManifest(
        run_id=f"run-{subject}",
        mode="backtest",
        as_of=as_of,
        code_commit=COMMIT,
        config_digest=CONFIG_DIGEST,
        random_seed=7,
        started_at=as_of,
        finished_at=as_of,
        status="succeeded",
    ).run_manifest_id


def _evidence(subjects: tuple[str, ...], *, as_of: datetime) -> dict[str, ShortlistEvidence]:
    """The evidence plane's answers about `subjects`, as this face's request carries them."""
    return {
        subject: ShortlistEvidence(
            signal=_signal(subject, as_of=as_of),
            run_manifest_id=_run_manifest_id(subject, as_of=as_of),
        )
        for subject in subjects
    }


def _wire_evidence(subjects: tuple[str, ...], *, as_of: datetime) -> dict[str, Any]:
    return {
        subject: {
            "signal": json.loads(item.signal.model_dump_json()),
            "run_manifest_id": item.run_manifest_id,
        }
        for subject, item in _evidence(subjects, as_of=as_of).items()
    }


@pytest.fixture(scope="module")
def raw_panel(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, tuple[FactorPanel, ...]]:
    """One store and the two raw builds written into it, kept so a tier can be added to them.

    The builds are returned rather than only written because `processed_runtime_dir` needs the
    `FactorPanel`s themselves -- `apply_factor_transform` takes a panel, not a store -- and
    recomputing them would double the one expensive step in this module.
    """
    root = tmp_path_factory.mktemp("shortlist-panel")
    store = PanelStore(root / "panel")
    panel = generate_panel()
    write_generated_panel(store, panel)
    calendar = panel.calendar()
    builds = tuple(
        compute_factor(
            store,
            REVERSAL,
            as_of=instant,
            subjects=panel.securities,
            universe=frozenset(panel.securities),
            requirements={
                "daily": daily_requirement(
                    calendar, years=(YEAR,), as_of=instant, max_staleness=timedelta(days=30)
                )
            },
            code_commit=COMMIT,
            built_at=instant,
            evaluators={
                REVERSAL.qualified_key: (
                    lambda context, sign=sign: (
                        sign * (SECURITIES.index(context.subject) + 1) / 100.0
                    )
                )
            },
        )
        for instant, sign in ((FIRST_BUILD, 1.0), (SECOND_BUILD, -1.0))
    )
    write_factor_panels(store, builds)
    return root, builds


@pytest.fixture(scope="module")
def runtime_dir(raw_panel: tuple[Path, tuple[FactorPanel, ...]]) -> Path:
    """One store: a generated panel, and one raw factor partition holding two cross sections.

    Module-scoped because no test here mutates it, and the real `compute_factor` /
    `write_factor_panels` over a generated ten-session panel is the expensive half.
    """
    return raw_panel[0]


@pytest.fixture(scope="module")
def processed_runtime_dir(raw_panel: tuple[Path, tuple[FactorPanel, ...]]) -> Path:
    """The same store with `cross_section_standard/v1` applied to both raw builds.

    Built on top of `raw_panel` rather than beside it because `generate_panel` is the expensive
    half and a second copy would buy nothing: the processed partition is a different dataset,
    every other test here screens `tier: raw`, and no raw answer moves when it appears.

    Both builds are transformed in one call because a partition is replaced whole and has no
    append -- `write_processed_factor_panels`' own rule -- so everything belonging to one
    `(dataset, year)` has to arrive together.

    **Every row it writes comes back `insufficient_cross_section`**, and that is the point rather
    than a limitation of the fixture: eight listed securities against the transform's declared
    `min_cross_section` of 100 is the shipped configuration `V2-P4-044` was filed on.
    """
    root, builds = raw_panel
    write_processed_factor_panels(
        PanelStore(root / "panel"),
        [
            apply_factor_transform(
                build, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=build.as_of
            )
            for build in builds
        ],
    )
    return root


WIDE_SECURITIES: Final[tuple[str, ...]] = tuple(f"{600000 + n:06d}.SH" for n in range(120))
"""120 names -- above `CROSS_SECTION_STANDARD`'s `min_cross_section` of 100, and the only width at
which a processed screen on this repository's fixtures produces an answer instead of a refusal."""

WIDE_VALUED_COUNT: Final[int] = 110
"""How many of the 120 the factor is actually computed for. **Deliberately not all of them.**

A mutant that refused any component whose `admitted_count` was below the *universe* count -- rather
than one that admitted nothing at all -- survived every test here while both fixtures had a value
for every listed name. Full coverage is also the unrealistic case: a factor that answers about
every security in the market is not what a panel looks like.

Still above `min_cross_section`, so the transform standardizes them, and the ten it drops are the
*last* ten by code, which are the ten most expensive closes and were never tradeable at
`WIDE_SCREEN`'s capital -- so the cut below is unmoved by the gap.
"""

WIDE_SCREEN: Final[dict[str, Any]] = {
    "tier": "processed",
    "transform": "cross_section_standard/v1",
    "as_of": LATE_AS_OF,
    "position_capital": "1900",
    "shortlist_size": 3,
}
"""The one processed screen on `wide_runtime_dir` that reaches a verdict, and both bounds are the
funnel's rather than chosen for taste.

`shortlist_size` has to clear the clip block **and** stay under the tradeable count, or stage one
and stage two refuse it in turn -- both were measured on the way here. Winsorizing 120 standardized
values at 1% assigns the upper bound to 2 of them, so a cut of 2 is `cut_inside_the_clip_block`;
`position_capital` of 1900 buys a 100-share lot of every name at or below 19.00 yuan, which on the
generator's closes (10.00 upward in `WIDE_SECURITIES` order) is the first ten, so a cut of 3 is
neither inside the block nor at or above the tradeable count.
"""


@pytest.fixture(scope="module")
def wide_runtime_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A market wide enough for the shipped transform to actually standardize it.

    **Why this costs a second panel generation.** `min_cross_section` is 100 and the only declared
    transform is `cross_section_standard/v1`, so on the eight-security panel every processed screen
    is refused and `shortlist_view` never renders one. Two claims are unobservable without a wider
    market, and both are load-bearing:

    - `V2-P4-050`'s headline -- that the transform which chose the numbers is now on the published
      answer. A mutant hardcoding `declaration.transform` to `None` survived every other test here.
    - `V2-P4-044`'s other direction -- that the new refusal is **narrow**. A refusal with no test
      showing a good panel getting through is a refusal that can quietly grow.

    `panel_fixtures.generate_panel` reads its module-level `SECURITIES`, so the width is swapped
    around the one call that consumes it and restored in a `finally`: the global is shared with
    every other fixture in the suite and a leaked value would rewrite panels this module does not
    own.
    """
    root = tmp_path_factory.mktemp("shortlist-wide")
    store = PanelStore(root / "panel")
    original = panel_fixtures.SECURITIES
    panel_fixtures.SECURITIES = WIDE_SECURITIES
    try:
        panel = generate_panel()
        write_generated_panel(store, panel)
    finally:
        panel_fixtures.SECURITIES = original
    built = compute_factor(
        store,
        REVERSAL,
        as_of=FIRST_BUILD,
        subjects=panel.securities[:WIDE_VALUED_COUNT],
        universe=frozenset(panel.securities),
        requirements={
            "daily": daily_requirement(
                panel.calendar(), years=(YEAR,), as_of=FIRST_BUILD, max_staleness=timedelta(days=30)
            )
        },
        code_commit=COMMIT,
        built_at=FIRST_BUILD,
        evaluators={
            REVERSAL.qualified_key: (
                lambda context: (WIDE_SECURITIES.index(context.subject) + 1) / 1000.0
            )
        },
    )
    write_factor_panels(store, [built])
    write_processed_factor_panels(
        store,
        [
            apply_factor_transform(
                built, CROSS_SECTION_STANDARD, code_commit=COMMIT, built_at=FIRST_BUILD
            )
        ],
    )
    yield root
    return root


def _cli(
    runtime_dir: Path, parameters: dict[str, Any], *, json_output: bool = True
) -> tuple[int, str]:
    """`openalpha shortlist run` over one parameter dict, as (exit code, stdout).

    `json_output=False` is the human-readable face, which one test below reads because a
    reader of a terminal has to be told "refused" in words rather than infer it from a table
    that happens to be empty.
    """
    arguments = [
        "shortlist",
        "run",
        "--runtime-dir",
        str(runtime_dir),
        "--tier",
        str(parameters["tier"]),
        "--shortlist-size",
        str(parameters["shortlist_size"]),
        "--position-capital",
        str(parameters["position_capital"]),
        "--as-of",
        parameters["as_of"].isoformat(),
        "--exchange",
        str(parameters["exchange"]),
        "--horizon",
        str(parameters["horizon"]),
        "--min-tradable-ratio",
        str(parameters["minimum_tradable_ratio"]),
        "--min-researched-ratio",
        str(parameters["minimum_researched_ratio"]),
        "--max-ranking-age-days",
        str(parameters["maximum_ranking_age_days"]),
    ]
    for flag in ("code_commit", "config_digest"):
        value = parameters[flag]
        if value is not None:
            arguments += [f"--{flag.replace('_', '-')}", str(value)]
    for component in parameters["components"]:
        arguments += ["--component", f"{component['factor']}={component['weight']}"]
    if parameters.get("transform"):
        arguments += ["--transform", str(parameters["transform"])]
    if parameters.get("neutralization"):
        arguments += ["--neutralization", str(parameters["neutralization"])]
    for year in parameters["years"]:
        arguments += ["--year", str(year)]
    if parameters.get("evidence_path"):
        arguments += ["--evidence", str(parameters["evidence_path"])]
    if json_output:
        arguments.append("--json")
    result = CliRunner().invoke(app, arguments)
    return result.exit_code, result.output


def _rest_body(parameters: dict[str, Any]) -> dict[str, Any]:
    body = {
        key: value
        for key, value in parameters.items()
        if key not in {"as_of", "years", "components", "evidence_path"}
    }
    body["as_of"] = parameters["as_of"].isoformat()
    body["years"] = list(parameters["years"])
    body["components"] = [dict(component) for component in parameters["components"]]
    return body


def _sdk_arguments(parameters: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in parameters.items() if key != "evidence_path"}


@pytest.fixture
def rest(runtime_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(runtime_dir=runtime_dir)) as client:
        yield client


@pytest.fixture
def rest_processed(processed_runtime_dir: Path) -> Iterator[TestClient]:
    """`rest` over the store that also holds a processed tier.

    A fixture of its own rather than a parameter, so that depending on it is what orders the
    processed partition's write before the client is built -- an ordering a test that took
    `rest` and `processed_runtime_dir` separately would be relying on pytest to get right.
    """
    with TestClient(create_app(runtime_dir=processed_runtime_dir)) as client:
        yield client


# --- 1. end to end, from a panel with factor data to a ranked, gated shortlist -------------------


def test_a_panel_with_factor_data_reaches_a_ranked_gated_shortlist_through_the_command_line(
    runtime_dir: Path, tmp_path: Path
) -> None:
    """A stored factor tier, cut, joined to the evidence plane and admitted -- in one command.

    The dead end `V2-P4-032` named, driven end to end: three stored tiers and no way to turn them
    into the `Sequence[ComponentCrossSection]` `CrossSectionScreen.select` requires.

    Every number here is measured off the panel rather than chosen: eight listed names, all eight
    scored off the stored cross section, three the market would sell at this capital, two cut, and
    both of those researched -- so `researched_ratio` is `1.0` and the declared floor of `0.5`
    admits the list.
    """
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(_wire_evidence(EARLY_SHORTLIST, as_of=EARLY_AS_OF)), encoding="utf-8"
    )
    code, out = _cli(
        runtime_dir,
        {**BASELINE, "minimum_researched_ratio": 0.5, "evidence_path": path},
    )
    assert code == 0, out
    body = json.loads(out)

    assert body["is_blocked"] is False
    assert body["cross_section"]["as_of"] == FIRST_BUILD.isoformat()
    assert body["cross_section"]["pricing_session"] == PRICING_SESSION.isoformat()
    assert body["cross_section"]["components"] == [
        {
            "factor_id": REVERSAL.factor_id,
            "row_count": UNIVERSE_COUNT,
            "clipped_count": 0,
            "admitted_count": UNIVERSE_COUNT,
            "stored_coverage": {"computed": UNIVERSE_COUNT},
        }
    ]
    assert body["funnel"]["coverage"] == "shortlisted"
    assert [entry["subject"] for entry in body["funnel"]["shortlist"]] == list(EARLY_SHORTLIST)
    assert body["measurement"] == {
        "universe_count": UNIVERSE_COUNT,
        "scored_count": UNIVERSE_COUNT,
        "tradeable_count": TRADEABLE_COUNT,
        "shortlist_count": SHORTLIST_SIZE,
        "candidate_count": SHORTLIST_SIZE,
        "tradable_ratio": TRADABLE_RATIO,
        "researched_ratio": 1.0,
        "ranking_age_days": body["measurement"]["ranking_age_days"],
    }
    assert [candidate["subject"] for candidate in body["admitted"]] == list(EARLY_SHORTLIST)
    assert body["unresearched"] == []


# --- 2. blocked is not empty --------------------------------------------------------------------


def test_a_blocked_shortlist_and_a_legitimately_empty_one_are_two_different_answers(
    runtime_dir: Path,
) -> None:
    """One store, one command line, **one flag apart** -- and the two answers are distinguishable.

    With no evidence supplied, nothing on the shortlist has been researched, so
    `candidate_count / shortlist_count` is `0.0`. Under a declared floor of `0.5` the gate refuses
    and names `researched_ratio_below_floor` with both sides of the comparison; under a declared
    floor of `0.0` the same list is **admitted** with an empty candidate list, which is a real
    answer and not a refusal.

    The two runs measure the *identical* market -- the assertion on `measurement` is what pins
    that -- so nothing but the declared bar separates them. At `824ebff` the user's only list
    endpoint answered `{"items":[],"excluded":[],"reviewed":0}` for both.
    """
    blocked_code, blocked_out = _cli(runtime_dir, {**BASELINE, "minimum_researched_ratio": 0.5})
    empty_code, empty_out = _cli(runtime_dir, {**BASELINE, "minimum_researched_ratio": 0.0})
    blocked = json.loads(blocked_out)
    admitted = json.loads(empty_out)

    assert blocked_code == 1
    assert blocked["is_blocked"] is True
    assert blocked["admitted"] is None
    assert [block["code"] for block in blocked["blocks"]] == ["researched_ratio_below_floor"]
    refusal = blocked["blocks"][0]
    assert refusal["measured"] == 0.0
    assert refusal["required"] == 0.5
    assert "0 of the 2 shortlisted names" in refusal["detail"]
    assert "0.0000" in refusal["detail"]
    assert "0.5000" in refusal["detail"]

    assert empty_code == 0
    assert admitted["is_blocked"] is False
    assert admitted["admitted"] == []
    assert admitted["blocks"] == []

    assert blocked["measurement"] == admitted["measurement"]
    assert blocked["unresearched"] == admitted["unresearched"] == list(EARLY_SHORTLIST)


def test_the_human_readable_cli_says_refused_in_words_rather_than_printing_an_empty_table(
    runtime_dir: Path,
) -> None:
    """Without `--json`, the verdict is the **first** line and is a word, not a blank table.

    A reader who has to infer "refused" from an empty section is the same reader the JSON body's
    `null`-versus-`[]` distinction exists for, one channel over -- and a refused list still prints
    its two shortlisted names, because "these are the names, and here is why you may not publish
    them" is the answer.
    """
    code, out = _cli(runtime_dir, {**BASELINE, "minimum_researched_ratio": 0.5}, json_output=False)
    assert code == 1
    assert out.splitlines()[0].startswith("verdict    REFUSED by ['researched_ratio_below_floor']")
    assert "unresearched" in out
    for subject in EARLY_SHORTLIST:
        assert subject in out


def test_the_rest_face_answers_a_blocked_shortlist_with_409_and_the_bar_it_missed(
    rest: TestClient,
) -> None:
    """`409` plus a verdict body, never `200` with an empty list.

    `GET /api/v1/panel/gate`'s own arrangement, which the product acceptance named the standard
    for the whole repository: the status code is the deliverable and the refusal still carries
    every block, the measurement it was read against, and the funnel's own coverage code.
    """
    refused = rest.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, "minimum_researched_ratio": 0.5})
    )
    assert refused.status_code == 409
    body = refused.json()
    assert "detail" not in body
    assert body["is_blocked"] is True
    assert body["admitted"] is None
    assert [block["code"] for block in body["blocks"]] == ["researched_ratio_below_floor"]
    assert body["measurement"]["shortlist_count"] == SHORTLIST_SIZE
    assert body["measurement"]["candidate_count"] == 0

    admitted = rest.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, "minimum_researched_ratio": 0.0})
    )
    assert admitted.status_code == 200
    assert admitted.json()["is_blocked"] is False
    assert admitted.json()["admitted"] == []


def test_the_rest_face_admits_a_researched_list_and_returns_its_candidates(
    rest: TestClient,
) -> None:
    """The other side of the same route: evidence supplied, the floor met, `200` with candidates."""
    response = rest.post(
        "/api/v1/shortlists/run",
        json={
            **_rest_body({**BASELINE, "minimum_researched_ratio": 0.5}),
            "evidence": _wire_evidence(EARLY_SHORTLIST, as_of=EARLY_AS_OF),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_blocked"] is False
    assert [candidate["subject"] for candidate in body["admitted"]] == list(EARLY_SHORTLIST)
    assert {candidate["direction"] for candidate in body["admitted"]} == {"bullish"}


# --- 3. no factor value stamped after the as_of reaches the cross section ------------------------


def test_a_factor_value_stamped_after_the_requested_as_of_never_reaches_the_cross_section(
    runtime_dir: Path,
) -> None:
    """The same store, two `as_of`s, and the adapter tells the two builds apart.

    One partition holds a cross section at 09:00Z and another at 13:00Z on 2026-01-16, and
    `EARLY_AS_OF` sits between them. The 13:00Z values are **in the file** at both reads; the only
    thing that keeps them out of the first answer is the `available_time` filter
    `load_factor_observations` applies through `read_visible_at`, and the instant resolution this
    face does on top of it.

    Three assertions, and each is needed. That the early answer carries the early instant would
    pass on an adapter that ignored the later build entirely; that the late answer carries the
    later instant would pass on one that ignored `as_of`; and the disjointness of the two value
    sets is what makes the first two statements about the *values* rather than about a label.
    """
    sdk = OpenAlphaSDK(runtime_dir=runtime_dir)
    early = sdk.run_shortlist(**_sdk_arguments({**BASELINE, "as_of": EARLY_AS_OF}))
    late = sdk.run_shortlist(**_sdk_arguments({**BASELINE, "as_of": LATE_AS_OF}))

    assert early.cross_section_as_of == FIRST_BUILD
    assert late.cross_section_as_of == SECOND_BUILD

    early_values = {
        subject: value
        for component in early.components
        for subject, value, _coverage in component.values
        if value is not None
    }
    late_values = {
        subject: value
        for component in late.components
        for subject, value, _coverage in component.values
        if value is not None
    }
    assert len(early_values) == len(late_values) == UNIVERSE_COUNT
    assert set(early_values.values()).isdisjoint(late_values.values())
    assert all(value > 0 for value in early_values.values())
    assert all(value < 0 for value in late_values.values())

    assert [entry.subject for entry in early.funnel.shortlist] == list(EARLY_SHORTLIST)
    assert [entry.subject for entry in late.funnel.shortlist] == list(LATE_SHORTLIST)


def test_a_fortnight_old_cross_section_is_still_priced_on_its_own_session(
    runtime_dir: Path,
) -> None:
    """Asking today about a cross section built two weeks ago prices it on *its* session.

    The ordinary state of a real store: the panel's newest factor build is however old the last
    `openalpha factor build` was, and the question is asked now. What must not happen is the
    shortlist being offered to a session the factor values never saw -- and on this store that
    session has no bars at all, so the substitution is not merely wrong, it is unanswerable.

    `cross_section_as_of` is reported precisely because this answer is older than the `as_of` that
    was asked for; see `the_cross_section_may_be_older_than_the_as_of_that_was_asked_for`.
    """
    result = OpenAlphaSDK(runtime_dir=runtime_dir).run_shortlist(
        **_sdk_arguments({**BASELINE, "as_of": MUCH_LATER_AS_OF})
    )

    assert result.cross_section_as_of == SECOND_BUILD
    assert result.pricing_session == PRICING_SESSION
    assert [entry.subject for entry in result.funnel.shortlist] == list(LATE_SHORTLIST)


def test_evidence_about_a_name_the_cut_did_not_reach_is_reported_rather_than_dropped(
    rest: TestClient,
) -> None:
    """A caller who researched more names than the cut holds is told which answers went unused.

    `rank_candidates` refuses a signal for a name the funnel did not shortlist, and that rule is
    right for the record and wrong for a face: which names make the cut moves with the `as_of` and
    with every declared bar, so a caller who researched last week's list would be refused for
    having done more work rather than less. This face narrows the join -- and says so, because the
    first version dropped them silently and a mutation proved that invisible.
    """
    extra = "601318.SH"
    assert extra not in EARLY_SHORTLIST
    response = rest.post(
        "/api/v1/shortlists/run",
        json={
            **_rest_body({**BASELINE, "minimum_researched_ratio": 0.5}),
            "evidence": _wire_evidence((*EARLY_SHORTLIST, extra), as_of=EARLY_AS_OF),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["evidence_not_shortlisted"] == [extra]
    assert [candidate["subject"] for candidate in body["admitted"]] == list(EARLY_SHORTLIST)
    assert body["measurement"]["candidate_count"] == SHORTLIST_SIZE


def test_the_shortlist_is_priced_on_the_cross_sections_own_session(runtime_dir: Path) -> None:
    """Stage two prices the names on the session the factor cross section is about, and says so.

    Not the request's own day: a caller asking at 12:00Z is answered off the 2026-01-16 session,
    because that is the session the stored cross section at 09:00Z was computed after. A face that
    priced on the day the *question* was asked would offer buys against bars its own factor values
    had never seen.
    """
    result = OpenAlphaSDK(runtime_dir=runtime_dir).run_shortlist(**_sdk_arguments(BASELINE))
    assert result.pricing_session == PRICING_SESSION
    assert result.cross_section_as_of == FIRST_BUILD


# --- the three faces, and the refusals that are not a verdict ------------------------------------


def test_the_three_faces_answer_one_shortlist_from_one_request(
    runtime_dir: Path, rest: TestClient
) -> None:
    """One store, one declaration -- the CLI, HTTP and the SDK cannot come to cut three lists.

    `gate_manifest_id` is the address of the *declaration* (the ranking's own address plus the
    three bars) and does not move with the wall clock, so it is the one field that must be equal
    across three faces that each stamp their own `built_at`.
    """
    code, out = _cli(runtime_dir, BASELINE)
    assert code == 0, out
    from_cli = json.loads(out)
    from_rest = rest.post("/api/v1/shortlists/run", json=_rest_body(BASELINE)).json()
    from_sdk = OpenAlphaSDK(runtime_dir=runtime_dir).run_shortlist(**_sdk_arguments(BASELINE))

    assert from_cli["gate_manifest_id"] == from_rest["gate_manifest_id"]
    assert from_cli["gate_manifest_id"] == from_sdk.clearance.manifest.gate_manifest_id
    assert from_cli["ranking_manifest_id"] == from_rest["ranking_manifest_id"]
    assert from_cli["funnel"]["shortlist"] == from_rest["funnel"]["shortlist"]
    assert [entry["subject"] for entry in from_cli["funnel"]["shortlist"]] == [
        entry.subject for entry in from_sdk.funnel.shortlist
    ]


def test_a_request_the_panel_cannot_answer_is_refused_by_name_on_both_channels(
    runtime_dir: Path, rest: TestClient
) -> None:
    """A year the store never held: `panel_unreadable`, exit 1 and `409` -- never an empty list."""
    unheld: Final[dict[str, Any]] = {**BASELINE, "years": (YEAR - 3,)}
    code, _out = _cli(runtime_dir, unheld)
    assert code == 1
    response = rest.post("/api/v1/shortlists/run", json=_rest_body(unheld))
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "panel_unreadable"


def test_a_factor_no_registry_declares_is_a_bad_request_rather_than_an_empty_shortlist(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`bad_request`: exit 3 and `422`. No amount of building fixes a mistyped factor."""
    mistyped: Final[dict[str, Any]] = {
        **BASELINE,
        "components": ({"factor": "no_such_factor/v1", "weight": 1.0},),
    }
    code, _out = _cli(runtime_dir, mistyped)
    assert code == 3
    response = rest.post("/api/v1/shortlists/run", json=_rest_body(mistyped))
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"
    assert "reversal_1d/v1" in response.json()["detail"]["message"]


def test_a_signal_stamped_at_another_instant_is_a_bad_request_not_a_blocked_panel(
    rest: TestClient,
) -> None:
    """The caller's own evidence is the caller's to fix, so it wears `422` and not `409`.

    `rank_candidates` refuses a signal whose `as_of` is not the ranking's, and it is right to --
    a conclusion about another day is a conclusion about another question. What that refusal must
    not do at a face is arrive as `blocked`, which is the row that means "the stored panel cannot
    answer this" and whose remedy is a build: a caller who mistyped one instant would be sent to
    rebuild a panel that is in perfect order.
    """
    stale = _wire_evidence(EARLY_SHORTLIST, as_of=LATE_AS_OF)
    response = rest.post(
        "/api/v1/shortlists/run",
        json={**_rest_body(BASELINE), "evidence": stale},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "bad_request"
    assert "evidence this request supplied" in detail["message"]


def test_no_shortlist_response_names_the_store_on_disk(runtime_dir: Path, rest: TestClient) -> None:
    """A refusal that echoed the runtime directory would answer a question about the deployment
    to whoever could reach the port. `panel_view.PANEL_STORE_PLACEHOLDER`'s rule, unchanged.

    The status assertion is what stops this passing vacuously on a `404`: the route has to exist
    and have refused for the body to be worth checking.
    """
    response = rest.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, "years": (YEAR - 3,)})
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "panel_unreadable"
    assert "this service's panel store" in response.json()["detail"]["message"]
    assert str(runtime_dir) not in response.text


# --- 6. V2-P4-044: a cross section the transform refused says so, and says what to do -----------


def test_a_processed_tier_over_a_thin_market_names_the_floor_it_missed(
    processed_runtime_dir: Path, rest_processed: TestClient
) -> None:
    """The declared configuration, on the shipped panel: `insufficient_cross_section`.

    `CROSS_SECTION_STANDARD` declares `min_cross_section=100`, this panel lists eight names, so
    `apply_factor_transform` refuses the whole cross section and stores that code on every row.
    Before this test the surface answered `409` whose only block was
    `researched_ratio_not_measurable` -- a bar on the **evidence plane**, whose implied remedy is
    to research names that do not exist -- and neither `insufficient_cross_section` nor
    `min_cross_section` appeared anywhere in the answer.

    The bar is the panel gate's refusal: name what failed, and say what to do about it.
    """
    response = rest_processed.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, **PROCESSED})
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "blocked"
    message = detail["message"]
    assert "insufficient_cross_section" in message
    assert "min_cross_section" in message
    assert "100" in message
    assert "cross_section_standard/v1" in message
    assert "reversal_1d/v1" in message


def test_the_evidence_plane_is_not_blamed_for_a_cross_section_the_transform_refused(
    processed_runtime_dir: Path, rest_processed: TestClient
) -> None:
    """`researched_ratio_not_measurable` must stop serving this cause.

    It is a true statement about the *gate* -- the ratio genuinely is not a number -- and it was
    the **only** thing the caller was told, for a run in which the evidence plane was never
    reached. A caller who acted on it would go and research eight names the screen had already
    discarded.
    """
    body = rest_processed.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, **PROCESSED})
    ).text

    assert "researched_ratio_not_measurable" not in body


def test_a_thin_cross_section_is_refused_by_name_on_the_command_line_too(
    processed_runtime_dir: Path,
) -> None:
    """The same verdict at the face a human reads, rather than only over HTTP."""
    code, out = _cli(processed_runtime_dir, {**BASELINE, **PROCESSED})

    assert code == 1, out
    assert "insufficient_cross_section" in out
    assert "min_cross_section" in out


def test_a_shortlist_answer_reports_which_securities_stage_one_could_not_score(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`ScoreCensus.excluded_by_coverage` reaches the caller.

    A rendered answer that printed `row_count: 8` beside `scored_count: 0` and nothing between
    them left the reader no way to tell "the rows carried no value" from "the components did not
    overlap". Both are stage-one findings and they have different remedies.
    """
    body = rest.post("/api/v1/shortlists/run", json=_rest_body(BASELINE)).json()

    assert body["funnel"]["excluded_by_coverage"] == {
        "incomplete_components": 0,
        "not_admissible": 0,
        "not_valued": 0,
    }
    assert body["cross_section"]["components"][0]["admitted_count"] == UNIVERSE_COUNT
    assert body["cross_section"]["components"][0]["stored_coverage"] == {"computed": UNIVERSE_COUNT}


# --- 7. V2-P4-045: no caller-supplied number reaches the user as a bare 500 ----------------------


def test_a_capital_beyond_what_the_execution_policy_can_represent_is_a_bad_request(
    runtime_dir: Path, rest: TestClient
) -> None:
    """Measured: `1e25` answered `200`, `1e26` answered a bare `500` with `Internal Server Error`.

    `ShortlistSpec.position_capital` is bounded below (`gt=0`) and not above, while every sibling
    numeric on this request is bounded on both sides. The fill's notional is quantized to cents,
    so a notional at or above `10**26` needs more than the 28 significant digits `decimal`'s
    context carries and raises `InvalidOperation` -- an `ArithmeticError`, which passes every
    `except ShortlistViewError` on all three faces.
    """
    response = rest.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, "position_capital": "1e26"})
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "bad_request"
    assert "--position-capital" in detail["message"]
    assert "100000000000000000000000000" in detail["message"]


def test_the_largest_representable_capital_is_still_answered(
    runtime_dir: Path, rest: TestClient
) -> None:
    """The bound is the policy's own ceiling and not a round number chosen to be safe.

    Without this half, a fix that refused every capital above a million would pass the test
    above and would be a different defect.
    """
    response = rest.post(
        "/api/v1/shortlists/run",
        json=_rest_body({**BASELINE, "position_capital": "99999999999999999999999999"}),
    )

    assert response.status_code == 200


def test_an_unrepresentable_capital_exits_bad_request_rather_than_internal_error(
    runtime_dir: Path,
) -> None:
    """Measured at `5` -- `internal_error`, "the command itself broke". It did not; the request
    was unanswerable and no amount of rebuilding the panel would have helped."""
    code, out = _cli(runtime_dir, {**BASELINE, "position_capital": "1e26"})

    assert code == 3, out


def test_a_capital_python_cannot_even_parse_is_refused_the_same_way(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`1e400` is a finite `Decimal` far above the ceiling, so the comparison alone catches it."""
    response = rest.post(
        "/api/v1/shortlists/run", json=_rest_body({**BASELINE, "position_capital": "1e400"})
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"


@pytest.mark.parametrize("capital", ["NaN", "Infinity", "-Infinity"])
def test_a_capital_that_is_not_a_number_is_refused_before_it_is_compared(
    runtime_dir: Path, capital: str
) -> None:
    """The command line is the face that can deliver one, and `NaN` is why the guard is a guard.

    `Decimal("NaN")` constructs happily, so `cli._factor_amount` hands it straight on -- while
    pydantic's `finite_number` rule means the HTTP face never can. And `Decimal("NaN") >= x` does
    not answer `False` the way a float would: it **raises** `InvalidOperation`. So a ceiling test
    written as a bare comparison would have reproduced `V2-P4-045`'s own defect through a
    different door, on the one face that can reach it.

    Added because a mutant that deleted the `is_finite` half survived the three tests above. It
    was a hole in them rather than a redundant check: without it this exits `5`.
    """
    code, out = _cli(runtime_dir, {**BASELINE, "position_capital": capital})

    assert code == 3, out


# --- 8. V2-P4-046: one literal input, one verdict, on all three faces ----------------------------


@pytest.mark.parametrize("field", ["code_commit", "config_digest"])
def test_an_explicitly_empty_provenance_field_is_refused_on_every_face(
    runtime_dir: Path, rest: TestClient, field: str
) -> None:
    """`""` published a list on the command line and was refused over HTTP.

    `cli.py` wrote `code_commit or None`, so an explicitly typed empty string was
    indistinguishable from an omitted flag and silently became the server's own git commit -- a
    shortlist stamped with a provenance the caller never declared. README calls the three faces
    equivalent.
    """
    declared: dict[str, Any] = {**BASELINE, field: ""}

    response = rest.post("/api/v1/shortlists/run", json=_rest_body(declared))
    code, out = _cli(runtime_dir, declared)

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "bad_request"
    assert code == 3, out
    with pytest.raises(ShortlistRequestError):
        OpenAlphaSDK(runtime_dir=runtime_dir).run_shortlist(**_sdk_arguments(declared))


@pytest.mark.parametrize("field", ["code_commit", "config_digest"])
def test_an_omitted_provenance_field_still_resolves_from_the_process(
    runtime_dir: Path, field: str
) -> None:
    """The other half: omitting the flag must still resolve server-side.

    Without it, a fix that simply refused `""` everywhere would also have broken the documented
    fallback, and the test above would not have noticed.
    """
    code, out = _cli(runtime_dir, {**BASELINE, field: None})

    assert code == 0, out
    assert json.loads(out)["is_blocked"] is False


# --- 9. V2-P4-050: a flag that moves nothing is refused rather than discarded --------------------


def test_a_neutralization_on_a_tier_that_has_none_is_refused_rather_than_discarded(
    runtime_dir: Path, rest: TestClient
) -> None:
    """`_resolve_neutralization` is reached only on the neutralized tier.

    Measured: a raw-tier screen with and without `--neutralization` returned byte-identical
    bodies and `200`. The caller asked for a neutralised screen and got a raw one, with nothing
    on the answer saying so. `--transform` on the raw tier is already refused for this exact
    reason; this is the same rule, one flag over.
    """
    asked: dict[str, Any] = {**BASELINE, "neutralization": "industry_and_size/v1"}

    response = rest.post("/api/v1/shortlists/run", json=_rest_body(asked))
    code, out = _cli(runtime_dir, asked)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "bad_request"
    assert "--neutralization" in detail["message"]
    assert code == 3, out


def test_the_rendered_answer_says_which_declaration_produced_it(
    runtime_dir: Path, rest: TestClient
) -> None:
    """A published answer that records `tier` and not the transform is not a content address.

    On the processed tier the transform is what chose the numbers, and it appeared in neither the
    rendered body nor `CandidateRankingManifest.scoring_policy` -- so two runs of the same factor
    under two different transforms were indistinguishable after the fact.
    """
    body = rest.post("/api/v1/shortlists/run", json=_rest_body(BASELINE)).json()

    assert body["declaration"] == {
        "tier": "raw",
        "transform": None,
        "neutralization": None,
        "exchange": EXCHANGE,
        "years": [YEAR],
        "components": [
            {"factor_id": REVERSAL.factor_id, "factor": "reversal_1d/v1", "weight": 1.0}
        ],
    }


def test_a_processed_answer_publishes_the_transform_that_chose_its_numbers(
    wide_runtime_dir: Path,
) -> None:
    """The half the raw-tier test above cannot show, because `None` is what it expects.

    A mutant that hardcoded `declaration.transform` to `None` survived every other test in this
    file, and it could, because on the eight-security panel no processed screen renders at all.
    That is the exact field `V2-P4-050` is about: without it a published shortlist said
    `tier: "processed"` and never which transform produced the ordering.
    """
    code, out = _cli(
        wide_runtime_dir,
        {**BASELINE, **WIDE_SCREEN},
    )

    assert code == 0, out
    declaration = json.loads(out)["declaration"]
    assert declaration["tier"] == "processed"
    assert declaration["transform"] == "cross_section_standard/v1"


def test_the_terminal_face_says_where_the_unscored_names_went(wide_runtime_dir: Path) -> None:
    """The `--json` census reaches the face a human actually reads, or the two faces disagree.

    `listed -> scored` is a subtraction printed with nothing beside it, and on this panel it drops
    ten names. A reader of a refused list needs that breakdown more than a program does; adding
    `excluded_by_coverage` to the JSON body alone would have been a new divergence rather than a
    fix, which is the thing `shortlist_view` exists to prevent.
    """
    code, out = _cli(wide_runtime_dir, {**BASELINE, **WIDE_SCREEN}, json_output=False)

    assert code == 0, out
    assert f"'not_valued': {len(WIDE_SECURITIES) - WIDE_VALUED_COUNT}" in out


def test_the_terminal_face_prints_no_unscored_line_when_nobody_was_dropped(
    runtime_dir: Path,
) -> None:
    """The other half, so the line is conditional rather than always-on.

    All eight names score on the raw panel, so every cell is zero and a row of noughts would be
    noise on the one face where noise costs the most.
    """
    code, out = _cli(runtime_dir, BASELINE, json_output=False)

    assert code == 0, out
    assert "unscored" not in out


def test_a_market_that_clears_the_floor_is_not_refused_by_the_new_block(
    wide_runtime_dir: Path,
) -> None:
    """`V2-P4-044`'s refusal must be narrow, and only a wide panel can show that it is.

    120 securities clears `min_cross_section=100`, so `apply_factor_transform` standardizes them
    and the stored rows read `processed` rather than `insufficient_cross_section`. The screen then
    runs to a verdict. Without this, "refuse when a component admits nothing" and "refuse every
    processed screen" would be indistinguishable.
    """
    code, out = _cli(
        wide_runtime_dir,
        {**BASELINE, **WIDE_SCREEN},
    )
    body = json.loads(out)

    assert code == 0, out
    assert body["is_blocked"] is False
    assert body["cross_section"]["components"][0]["stored_coverage"] == {
        "processed": WIDE_VALUED_COUNT
    }
    assert body["cross_section"]["components"][0]["admitted_count"] == WIDE_VALUED_COUNT
    assert body["measurement"]["universe_count"] == len(WIDE_SECURITIES)
    assert body["funnel"]["excluded_by_coverage"]["not_valued"] == (
        len(WIDE_SECURITIES) - WIDE_VALUED_COUNT
    )
    assert body["funnel"]["coverage"] == "shortlisted"


# --- 10. V2-P4-051: 422 carries two body schemas and `detail` is not the discriminator ----------


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("unparseable as_of", {"as_of": "not-a-date"}),
        ("misspelled field", {"shortlistsize": 2}),
        ("non-numeric position_capital", {"position_capital": "abc"}),
    ],
)
def test_the_documented_discriminator_holds_for_every_422_this_route_emits(
    rest: TestClient, name: str, payload: dict[str, Any]
) -> None:
    """`docs/api/http.md` told clients to branch on `"detail" in body`.

    That is true of this module's `{"reason","message"}` object **and** of FastAPI's own
    validation `list`, so a client following the documented rule raised
    `TypeError: list indices must be integers` on five different inputs. The discriminator has to
    be the detail's *shape*, which is what the docs now say.
    """
    response = rest.post("/api/v1/shortlists/run", json={**_rest_body(BASELINE), **payload})
    body = response.json()

    assert response.status_code == 422
    detail = body["detail"]
    if isinstance(detail, dict):
        assert set(detail) == {"reason", "message"}
    else:
        assert isinstance(detail, list)
        assert all("loc" in entry for entry in detail)


def test_a_body_the_route_cannot_parse_at_all_is_still_a_shaped_422(rest: TestClient) -> None:
    """Malformed JSON and a wrong `Content-Type` never reach this module's own refusal at all."""
    for content, headers in (
        ("{not json", {"Content-Type": "application/json"}),
        ("x", {"Content-Type": "text/plain"}),
    ):
        response = rest.post("/api/v1/shortlists/run", content=content, headers=headers)

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)


def test_the_http_reference_gives_a_rule_that_survives_both_422_shapes() -> None:
    """The prose is the defect here, so the prose is what this test holds.

    `docs/api/http.md` told clients to switch on `"detail" in body`. Both `422` shapes have that
    key, so the rule silently selected the wrong branch and the next line -- `body["detail"]
    ["reason"]` -- raised `TypeError` on five ordinary malformed requests. The corrected rule has
    to name the detail's **shape**, which is the only thing that actually separates them.
    """
    reference = (ROOT / "docs" / "api" / "http.md").read_text(encoding="utf-8")
    shortlists = reference[reference.index("## Shortlists") :]

    assert "isinstance(detail, dict)" in shortlists
    assert "**an object**" in shortlists
    assert "**a list**" in shortlists
    assert '`"detail" in body` first' not in shortlists


def test_the_readme_exit_table_accounts_for_every_code_this_command_can_issue() -> None:
    """A table that omits two of the five codes is a table a CI job cannot switch on.

    `2` is Click's own `UsageError` -- what a missing `--component` returns -- and `5` is
    `internal_error`. Both were reachable from `openalpha shortlist run` and neither had a row,
    so a pipeline meeting either would read the table, find nothing, and treat it as the `1` it
    is not.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("`admitted`") : readme.index("`admitted: []`")]

    assert "`2` /" in table
    assert "`5` /" in table
