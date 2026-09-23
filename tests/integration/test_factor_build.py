"""`openalpha factor build`, on a store nothing but `panel build` ever wrote (`V2-P3-019`).

## The dead end this file removes

`V2-P3-015` shipped `factor run` and recorded, by name, that nothing could feed it:
`nothing_in_this_repository_builds_a_factor_panel_from_a_command_line`. The acceptance review
reproduced the consequence exactly::

    openalpha factor run --runtime-dir <a store only `panel build` wrote> --factor ... \\
      -> factor_obs_... year=2026 cannot be read: ['partition_missing', 'field_missing']  EXIT=1
    openalpha panel build --dataset factor_obs_...
      -> 'factor_obs_...' is not one of this command's build targets ([13 targets])

Two commands, two refusals, no third door -- and `compute_factor`, `apply_factor_transform` and
`apply_factor_neutralization` had **zero** usage examples outside one integration test, so the
remedy was to reverse-engineer `daily_requirement`, `universe`, `subjects`, `max_staleness` and
`membership_years` out of a 7,760-line module. `test_the_dead_end_the_acceptance_review_found_is_
closed_end_to_end` is that whole sequence, driven.

## The tier that still refuses, and how narrow that has become

The raw and processed tiers build at any prediction instant the panel covers. The neutralised tier
is narrower, and the bound is arithmetic. It used to be stated by two whole-partition reads and
both have now been retracted: `V2-P4-026` put `daily_basic` on a session-level availability
predicate, and `V2-P4-028` put `index_member_all` on `panel_ingest.load_industry_cross_section`,
a day-scoped door. The sentence that stood here -- "a residual exists only at a prediction instant
at or after the last stored *assignment* of every membership year read, on this generated panel
2026-01-14" -- is false now, and
`test_the_neutralised_tier_builds_at_the_mid_window_instants_it_used_to_refuse` is the same call
that used to prove it, turned round.

What is left is one session wide. `_refuse_a_cross_section_that_is_not_this_panels` requires the
returned section's `as_of` to equal the processed panel's exactly, and both foreign reads are
taken for the day that instant falls on -- so an instant before that day's own close has no
session to read. This command's job is to be **honest** about that: build what it can, refuse the
rest by name, and write nothing when it refuses.
`test_a_refused_neutralisation_leaves_the_store_exactly_as_it_found_it` drives the refusal at
`UNPUBLISHED_INSTANT`, noon on a session, and checks that the raw tier builds there.

## Two faces, not three

`factor build` is on the command line and in the SDK and deliberately not on HTTP, which is
`openalpha panel build`'s own arrangement: it writes panel partitions, a partition is replaced
whole, and the service ships with no authentication. `test_no_http_route_builds_a_factor_partition`
pins the absence so it stays a decision.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from cli_help import rendered_help
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, SECURITIES, YEAR, GeneratedPanel
from test_factor_interfaces import BASELINE, PREDICTION_DAYS, RUN_AS_OF, SHAPES, _panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import FACTOR_EXIT, PanelExit, app
from openalpha_cn.domain.factor import FactorDefinition, FactorField, FactorRegistry
from openalpha_cn.domain.panel_batch import PanelColumn, TimelineColumns
from openalpha_cn.domain.stock_universe import (
    DELISTING_EVENT,
    LISTING_EVENT,
    STOCK_BASIC_DATASET,
)
from openalpha_cn.factor_view import (
    BUILD_VIEW_SCHEMA_VERSION,
    REQUIREMENT_BUILDERS,
    FactorBuildReport,
    FactorRequestError,
    FactorViewError,
    build_factor_panels,
    build_rows,
    build_view,
    factor_build_request,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    factor_observation_dataset,
    load_factor_observations,
)
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER
from openalpha_cn.runtime.provenance import resolve_code_commit
from openalpha_cn.sdk import OpenAlphaSDK

COMMIT: Final[str] = "abcdef1234567"

BUILD_INSTANTS: Final[tuple[datetime, ...]] = tuple(
    datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC) for day in PREDICTION_DAYS
)
"""The two prediction instants `test_factor_interfaces.store_three_tiers` stamps by hand.

Reused verbatim so that a panel this command builds and one that fixture builds are comparable at
all -- `test_the_command_line_build_reproduces_the_fixtures_own_stored_tiers` compares them for
exactly that reason.
"""

HORIZON_INSTANT: Final[datetime] = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
"""A prediction instant at or after the generated panel's own stored horizon.

17:00 Asia/Shanghai on the panel's last stored session, which is both a trading day (the industry
read needs one) and past every stored partition's newest row.

**It was the earliest instant at which the neutralised tier was assemblable at all, and it has
stopped being the earliest twice.** `V2-P4-026` put `daily_basic` on a session-level availability
predicate, which left the membership partition binding -- on this generator its newest assignment
becomes knowable 2026-01-13T16:00Z. `V2-P4-028` removed that too, and
`tests/integration/panel/test_factor_neutralizations.py::
test_across_the_whole_window_only_the_industry_read_ever_refuses_an_in_year_as_of` measures that
every session of the window now admits a whole build. This constant stays at the horizon because
it is the instant that cannot stop working for a reason outside this file, which is what makes it
the control the mid-window build is compared against.
"""

STALE_PANEL_INSTANT: Final[datetime] = datetime(2026, 1, 17, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on the Saturday after the panel's newest session (2026-01-16).

The one instant in this window where `--max-staleness-days` can decide anything, and finding it
is `V2-P4-064`'s doing. At `BUILD_INSTANTS` the panel is half an hour old, so every legal value
of the flag clears (`_build_staleness` refuses anything below 1), and once the registry stopped
taking the bar there was no value left that moved the answer. Here the newest `daily` row is
1d2h behind, so `1` is refused and `2` builds -- one flag value apart, on one store, at one
instant.

A **non-session** day rather than a later session: `_price_requirement` extends `required_dates`
to `_sessions_published_through`, so an `as_of` on the next open session (2026-01-19) makes that
session *due* and the refusal becomes `date_gap` at every bound -- measured, exit 1 for 1, 2, 3
and 5 -- which would be the answer moving for a reason that has nothing to do with the flag.
Saturday leaves the census satisfied and the clock the only thing that has changed.
"""

UNPUBLISHED_INSTANT: Final[datetime] = datetime(2026, 1, 8, 4, 0, tzinfo=UTC)
"""**Noon** Asia/Shanghai on 2026-01-08 -- a session whose own 16:30 has not arrived.

The one instant on this fixture at which the first tier builds and the third does not, and after
`V2-P4-028` the only kind left: `daily_basic` refuses a session that has not published, by name,
where a bare row predicate would have answered it with a cross section in which every security
lands in `without_market_cap`. That is `V2-P4-026`'s own bound, inherited here because the
membership partition no longer states one -- see
`test_the_neutralised_tier_builds_at_the_mid_window_instants_it_used_to_refuse`.
"""

REGISTRY_SECURITIES: Final[int] = len(SECURITIES)
"""How many codes the generated registry knows, delisted ones included."""

RENDERED_BUILD_LEAVES: Final[int] = 19
"""How many scalar leaves `build_view` produces on `_build_parameters()`'s build.

Pinned rather than bounded, for `RENDERED_LEAF_COUNT`'s measured reason one file over: a `>=`
bound would let a key vanish from the transport while the per-key audit went on passing on a
narrower body.

**An empty container contributes no leaf**, which is a real limit of this shape and is stated
rather than left to be discovered: `manifest_ids.neutralized` is `[]` on a `--tier processed`
build and the perturbation loop cannot reach it. That key is held by the equality against
`_expected_build_view` instead, and by
`test_the_report_names_what_it_actually_stored`'s explicit `coverage["neutralized"] == {}`.
"""


def _sdk(runtime_dir: Path) -> OpenAlphaSDK:
    return OpenAlphaSDK(runtime_dir=runtime_dir, clock=lambda: RUN_AS_OF)


@pytest.fixture
def panel_only(tmp_path: Path) -> Path:
    """A runtime directory holding exactly what `openalpha panel build` writes, and no more.

    Written through `write_generated_panel`, which is the same set of `panel_ingest` writers
    `panel build` drives, so every write-time guard ran. `_panel` is
    `test_factor_interfaces`' own generator -- borrowed rather than re-written, because a second
    generator would be a second panel for the two files to disagree about, and its one substitution
    (a `total_mv` that actually varies) is argued there.

    Crucially it holds **no factor partition at all**, which is the starting condition the whole
    file is about.
    """
    from panel_fixtures import write_generated_panel

    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, _panel())
    return tmp_path


def _build_parameters(**overrides: Any) -> dict[str, Any]:
    """One build's parameters, stated once so the two faces are driven from one dict."""
    return {
        "factor": "reversal_1d/v1",
        "tier": "processed",
        "transform": "cross_section_standard/v1",
        "neutralization": "",
        "as_ofs": list(BUILD_INSTANTS),
        "years": [YEAR],
        "exchange": EXCHANGE,
        "max_staleness_days": 30,
        "waive_max_staleness": False,
        "subjects": [],
        "supersedes_raw": [],
        "supersedes_processed": [],
        "supersedes_neutralized": [],
        "code_commit": COMMIT,
        **overrides,
    }


def _cli_arguments(runtime_dir: Path, parameters: dict[str, Any]) -> list[str]:
    """One parameter dict as the command line, keyed off the dict rather than hand-written.

    `test_factor_interfaces._cli_arguments`' rule and its reason: a hand-written argument list is a
    second copy of the parameter set, and the two drift.
    """
    repeated = {
        "as_ofs": "--as-of",
        "years": "--year",
        "subjects": "--subject",
        "supersedes_raw": "--supersedes-raw",
        "supersedes_processed": "--supersedes-processed",
        "supersedes_neutralized": "--supersedes-neutralized",
    }
    flags = ["factor", "build", "--runtime-dir", str(runtime_dir), "--json"]
    for name, value in parameters.items():
        if name in repeated:
            for item in value:
                flags.extend(
                    [repeated[name], item.isoformat() if hasattr(item, "isoformat") else str(item)]
                )
            continue
        if name == "waive_max_staleness":
            if value:
                flags.append("--waive-max-staleness")
            continue
        if value is None or value == "":
            continue
        flags.extend(["--" + name.replace("_", "-"), str(value)])
    return flags


def sdk_build(runtime_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    """The SDK's answer, or the fault it raised, in one comparable shape.

    `FactorViewError` and not the three subclasses, so a fault this file did not anticipate is
    compared against the command line's envelope rather than escaping as a test error -- which is
    the direction that would hide a face enveloping one fault two ways.
    """
    try:
        report = _sdk(runtime_dir).build_factor_panels(**parameters)
    except FactorViewError as error:
        return {"reason": error.reason, "message": error.disclosable}
    return build_view(report)


def _without_store(text: str, runtime_dir: Path) -> str:
    """`text` with this store's own location replaced by the name a `disclosable` message uses.

    The two faces are driven against two stores -- a second face against the first's store would be
    a *rebuild*, which the drop guard judges rather than the builder -- so their local messages
    legitimately differ by a filesystem path and nothing else. Comparing the placeholder forms is
    what makes the rest of the message an equality. Both spellings, longest first, for
    `factor_view._without_store_path`'s measured reason: a macOS temporary directory resolves
    through a symlink.
    """
    store = runtime_dir / "panel"
    for path in sorted({str(store), str(store.resolve())}, key=len, reverse=True):
        text = text.replace(path, PANEL_STORE_PLACEHOLDER)
    return text


def cli_build(runtime_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    """The command line's answer, or the fault it exited on, in the same shape."""
    result = CliRunner().invoke(app, _cli_arguments(runtime_dir, parameters))
    if result.exit_code == int(PanelExit.ok):
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed
    return {"exit_code": result.exit_code, "stderr": result.stderr}


# --- the dead end, closed -----------------------------------------------------------------------


def test_the_dead_end_the_acceptance_review_found_is_closed_end_to_end(panel_only: Path) -> None:
    """The acceptance review's transcript, replayed, and where the road now ends instead.

    Five steps, in the order an operator meets them, all through the shipped registry:

    1. `factor run` against a store only `panel build` wrote -- `panel_unreadable`,
       `partition_missing` on the **raw** factor dataset. The review's first refusal.
    2. `panel build --dataset factor_obs_...` -- not one of that command's build targets. The
       review's second refusal, and the reason there was no third door.
    3. `factor build --tier processed` -- this issue's deliverable. Both partitions stored.
    4. `factor run` again -- refused **later**. The raw dataset is no longer named at all; what is
       missing now is `factor_neut_...`, which is a different question with a different owner.
    5. `factor build --tier neutralized` over the same days -- **all three tiers stored**, and
       `factor run` then answers.

    **Step five used to be the end of the road and `V2-P4-028` opened it.** The prediction days
    here are 2026-01-08 and 2026-01-09, and until now a residual for either could only be built
    at or after the last stored *assignment* of the membership year they fall in -- 2026-01-14 on
    this generator -- so this step was a `blocked` exit naming
    `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon` and
    `not_yet_knowable`. `V2-P4-026` had already retracted `daily_basic`'s half of that bound;
    `V2-P4-028` puts the membership read on `load_industry_cross_section`, which takes the day as
    an argument, so a membership event later than the day being priced no longer refuses it.

    **Step six is what makes step five worth having.** `factor run` over the same two days is
    driven at the end and is asserted to *answer*, so the transcript now closes rather than
    stopping one refusal further along than it used to. It is the difference `V2-P4-028` exists
    for: what changed is not that a loader returns a value, it is that an operator standing at
    the command line reaches the third tier.

    `tests/integration/test_factor_run.py` drives the complete sealed experiment over this same
    generated panel, and `test_the_command_line_build_reproduces_the_fixtures_own_stored_tiers`
    proves the panel this command writes is that one, to the content address.
    """
    runner = CliRunner()
    run_arguments = [
        "factor",
        "run",
        "--runtime-dir",
        str(panel_only),
        "--json",
        *[
            item
            for name, value in BASELINE.items()
            for item in (
                "--" + name.replace("_", "-"),
                value.isoformat() if hasattr(value, "isoformat") else str(value),
            )
        ],
    ]

    before = runner.invoke(app, run_arguments)
    assert before.exit_code == int(FACTOR_EXIT["panel_unreadable"])
    assert "partition_missing" in before.stderr

    unbuildable = runner.invoke(
        app,
        [
            "panel",
            "build",
            "--runtime-dir",
            str(panel_only),
            "--dataset",
            factor_observation_dataset(FACTOR_DEFINITIONS.get("reversal_1d/v1")),
            "--year",
            str(YEAR),
        ],
    )
    assert unbuildable.exit_code != 0
    assert "build targets" in unbuildable.stderr

    built = cli_build(panel_only, _build_parameters())
    assert built["tier"] == "processed"
    assert built["partitions"]

    after = runner.invoke(app, run_arguments)
    assert after.exit_code == int(FACTOR_EXIT["panel_unreadable"])
    assert "factor_obs_reversal_1d_v1" not in after.stderr
    assert "factor_neut_reversal_1d_v1" in after.stderr

    neutralised = cli_build(
        panel_only, _build_parameters(tier="neutralized", neutralization="industry_and_size/v1")
    )
    assert neutralised["tier"] == "neutralized"
    assert neutralised["manifest_ids"]["neutralized"]

    answered = runner.invoke(app, run_arguments)
    assert answered.exit_code == 0, answered.stderr
    assert json.loads(answered.stdout)["experiment_id"]


def test_a_raw_only_build_stores_the_partition_factor_run_reads(panel_only: Path) -> None:
    """`--tier raw` is a complete answer for the tier it names, read back through the real loader.

    Read back with `load_factor_observations` rather than off the report, because the report is
    what the writer *said* and the loader is the visibility-filtered door every consumer takes. A
    build whose report was right and whose partition was unreadable would be the worst of both.
    """
    report = _sdk(panel_only).build_factor_panels(
        **_build_parameters(tier="raw", transform="", neutralization="")
    )
    observations = load_factor_observations(
        PanelStore(panel_only / "panel"),
        FACTOR_DEFINITIONS.get("reversal_1d/v1"),
        years=(YEAR,),
        as_of=RUN_AS_OF,
    )

    assert report.tier == "raw"
    assert report.manifest_ids["processed"] == ()
    assert report.manifest_ids["neutralized"] == ()
    assert {observation.as_of for observation in observations} == set(BUILD_INSTANTS)
    assert {observation.subject for observation in observations} == set(SECURITIES)
    assert report.coverage["raw"]["computed"] > 0


def test_the_command_line_build_reproduces_the_fixtures_own_stored_tiers(
    panel_only: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The command builds the same panel the hand-written fixture does, to the `manifest_id`.

    The strongest available statement that this command is not a second, subtly different builder:
    `store_three_tiers` assembles the raw and processed tiers by calling `compute_factor` and
    `apply_factor_transform` directly with hand-chosen arguments, and the `manifest_id` is a content
    address over every determinant of the build -- the subjects, the universe, the inputs' partition
    hashes and the commit. Equal addresses mean the command chose every one of those the same way.

    A separate store for the fixture, so neither write can influence the other.
    """
    from test_factor_interfaces import store_three_tiers

    by_hand = tmp_path_factory.mktemp("by-hand")
    store_three_tiers(by_hand)
    fixture_rows = load_factor_observations(
        PanelStore(by_hand / "panel"),
        FACTOR_DEFINITIONS.get("reversal_1d/v1"),
        years=(YEAR,),
        as_of=RUN_AS_OF,
    )

    report = _sdk(panel_only).build_factor_panels(**_build_parameters(code_commit=COMMIT))

    assert set(report.manifest_ids["raw"]) == {row.manifest_id for row in fixture_rows}


# --- the two faces agree ------------------------------------------------------------------------


def test_the_two_build_faces_store_one_panel_from_one_request(
    panel_only: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """One parameter dict, two faces, one panel -- compared by content address.

    Two stores rather than one, because the second face against the first's store would be a
    *rebuild*, which the drop guard judges rather than the builder. Comparing the two reports whole
    is what makes this an equality about every number the faces report, and comparing the
    `manifest_ids` is what makes it an equality about what was stored.
    """
    other = tmp_path_factory.mktemp("cli-store")
    from panel_fixtures import write_generated_panel

    write_generated_panel(PanelStore(other / "panel"), _panel())

    through_sdk = sdk_build(panel_only, _build_parameters())
    on_the_command_line = cli_build(other, _build_parameters())

    assert through_sdk == on_the_command_line
    assert through_sdk["manifest_ids"]["raw"]
    assert through_sdk["schema_version"] == BUILD_VIEW_SCHEMA_VERSION


STALE_PANEL_ROW: Final[dict[str, Any]] = {
    "as_ofs": [STALE_PANEL_INSTANT],
    "tier": "raw",
    "transform": "",
}
"""The `common` half of the `max_staleness_days` row -- see `STALE_PANEL_INSTANT`.

Applied to the baseline **and** to the varied build, which is what keeps the sweep's isolation
property intact: exactly one parameter still differs between the two answers being compared.
A row that moved `as_ofs` on the varied side alone would prove that *something* moved and
nothing about which flag moved it, which is the failure this whole sweep exists to prevent.
"""


@pytest.mark.parametrize(
    ("parameter", "value", "common"),
    [
        ("factor", "momentum_20_sessions/v1", {}),
        ("tier", "raw", {}),
        ("as_ofs", [BUILD_INSTANTS[0]], {}),
        ("years", [YEAR, YEAR - 1], {}),
        ("exchange", "SSE", {}),
        ("max_staleness_days", 1, STALE_PANEL_ROW),
        ("subjects", [SECURITIES[0], SECURITIES[1]], {}),
        ("code_commit", "0f1e2d3c4b5a6978", {}),
        ("supersedes_raw", ["fbm_0000000000000000000000ff"], {}),
    ],
)
def test_every_declared_build_parameter_reaches_the_answer_on_both_faces(
    panel_only: Path,
    tmp_path_factory: pytest.TempPathFactory,
    parameter: str,
    value: Any,
    common: dict[str, Any],
) -> None:
    """Vary one parameter alone; the answer must move, and both faces must move together.

    `V2-P1-016`'s finding and `V2-P3-015`'s repair of it, on the builder. An equivalence test that
    feeds the same literal to both faces proves the two paths agree and proves nothing about
    whether either carried the caller's value to the judgement -- that was measured once, on an SDK
    that hardcoded `exchange` while 1,815 tests stayed green. So each varied value is asserted to
    move the answer **and** to move it to the same place on both faces, in one iteration.

    Every varied value here is chosen to be reachable rather than merely different: `--exchange
    SSE` and a 1-day staleness bound are refusals this store really produces, `--years` reaching
    into an uncovered year is another, and the rest are successful builds with different content
    addresses. `--transform` and `--neutralization` have no second declared value and are covered
    by `test_a_tier_option_that_decides_nothing_is_refused_rather_than_ignored` instead, which is
    the shape `VARIATIONS` uses one file over.

    **`common` exists because `V2-P4-064` took one row's reachability away, and putting it back
    honestly needed a second instant rather than a second parameter.** The 1-day bound used to be
    a refusal at `BUILD_INSTANTS` -- but the read it refused was the *registry*, which is on an
    event clock and has no business being judged by a session bound; taking it off that read is
    the fix. At those instants the price panel is half an hour old, so no legal value of the flag
    decides anything there any more, and the row would have had to become an exemption --
    i.e. the one parameter whose reach nothing checks, which is precisely Task 39's finding
    reappearing inside the test written to prevent it. `common` is applied to **both** sides
    instead, so the comparison still isolates one parameter; see `STALE_PANEL_ROW`.
    """
    baseline_store = tmp_path_factory.mktemp("baseline")
    varied_store = tmp_path_factory.mktemp("varied")
    from panel_fixtures import write_generated_panel

    for target in (baseline_store, varied_store):
        write_generated_panel(PanelStore(target / "panel"), _panel())

    parameters = _build_parameters(**common, **{parameter: value})
    if parameter == "tier":
        parameters["transform"] = ""

    baseline = sdk_build(panel_only, _build_parameters(**common))
    varied = sdk_build(baseline_store, parameters)
    through_the_command_line = cli_build(varied_store, parameters)

    assert varied != baseline, f"{parameter} did not reach the answer"
    if "reason" in varied:
        assert through_the_command_line["exit_code"] == int(FACTOR_EXIT[varied["reason"]])
        assert varied["message"] in _without_store(through_the_command_line["stderr"], varied_store)
    else:
        assert through_the_command_line == varied


def test_the_build_sweep_covers_every_declared_parameter() -> None:
    """The sweep is a table, and this is the run of it.

    A parameter added to `factor_build_request` with no row in the sweep would leave it silently one
    short, which is `test_the_sweep_below_covers_every_declared_run_parameter`'s own argument. The
    exemptions are named rather than skipped inside the sweep: the two spec handles have exactly one
    declared value each, the two remaining `supersedes` lists are the same contract as the one that
    is swept, and `waive_max_staleness` is `max_staleness_days`' other half and is driven by
    `test_a_freshness_bound_is_stated_or_waived_and_never_defaulted`.
    """
    import inspect

    declared = set(inspect.signature(factor_build_request).parameters) - {
        "factors",
        "transforms",
        "neutralizations",
    }
    swept = {
        "factor",
        "tier",
        "as_ofs",
        "years",
        "exchange",
        "max_staleness_days",
        "subjects",
        "code_commit",
        "supersedes_raw",
    }
    exempt = {
        "transform",
        "neutralization",
        "waive_max_staleness",
        "supersedes_processed",
        "supersedes_neutralized",
    }

    assert set(_build_parameters()) == declared
    assert swept | exempt == declared
    assert swept & exempt == set()


# --- the tier that refuses ----------------------------------------------------------------------


def test_the_neutralised_tier_builds_at_the_mid_window_instants_it_used_to_refuse(
    panel_only: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`V2-P4-028`'s acceptance, driven from the SDK against the store `panel build` wrote.

    **The refused half of this test was the whole of `V2-P4-027`/`028`.** Until now, this exact
    call -- `--tier neutralized` at 2026-01-08 and 2026-01-09, four and five sessions before the
    generated panel's own horizon -- was refused `blocked` with `not_yet_knowable`, because
    `load_industry_market_cap_cross_section` read the memberships through
    `load_industry_histories`, and `read_if_ready` decides that verdict on a partition's **max**
    `available_time`. This fixture's 2026 membership partition holds an assignment opening
    2026-01-14, so the whole year was unreadable until then, and on the real corpus that shape is
    the annual constituent review. Nothing about the store changed; the read did.

    The horizon instant is kept beside it, unchanged, because a builder that answered everywhere
    would pass a success-only test while having lost its bound: the two are asserted to produce
    the **same** coverage distribution per instant, so what moved is which instants are reachable
    and not what a reachable one contains.

    What still refuses is driven by
    `test_a_refused_neutralisation_leaves_the_store_exactly_as_it_found_it`, and it is now
    `V2-P4-026`'s bound rather than this one's -- a session whose own close has not published.

    **What this test cannot show, stated rather than left to be discovered.** The shipped
    `industry_and_size/v1` declares `min_cross_section = 100` and this panel holds eight names,
    so every neutralised row is `insufficient_cross_section` at *both* instants and no residual is
    computed at either. The coverage distribution is asserted rather than only its total, which is
    what separates "the industry cross section was assembled" from "the loader handed back
    nothing" -- an empty cross section would be refused by the engine's own coverage guard and
    never reach a report at all. That the residual is a **number** at an instant this issue
    unblocked is `tests/integration/panel/test_factor_neutralizations.py::
    test_a_residual_is_computed_at_an_instant_the_unfiltered_door_still_refuses`, which can use a
    probe spec an eight-name panel clears.
    """
    at_the_horizon = tmp_path_factory.mktemp("at-horizon")
    from panel_fixtures import write_generated_panel

    write_generated_panel(PanelStore(at_the_horizon / "panel"), _panel())

    mid_window = sdk_build(
        panel_only,
        _build_parameters(tier="neutralized", neutralization="industry_and_size/v1"),
    )
    at_horizon = sdk_build(
        at_the_horizon,
        _build_parameters(
            tier="neutralized",
            neutralization="industry_and_size/v1",
            as_ofs=[HORIZON_INSTANT],
        ),
    )

    assert mid_window["tier"] == "neutralized"
    assert mid_window["manifest_ids"]["neutralized"]
    assert mid_window["coverage"]["neutralized"] == {
        "insufficient_cross_section": REGISTRY_SECURITIES * len(BUILD_INSTANTS)
    }
    assert mid_window["coverage"]["raw"] == {"computed": REGISTRY_SECURITIES * len(BUILD_INSTANTS)}

    assert at_horizon["tier"] == "neutralized"
    assert at_horizon["manifest_ids"]["neutralized"]
    assert at_horizon["coverage"]["neutralized"] == {
        "insufficient_cross_section": REGISTRY_SECURITIES
    }


def test_the_tier_option_help_states_the_bound_the_builder_actually_applies(
    panel_only: Path,
) -> None:
    """`V2-P4-103`. `--tier`'s option help kept a bound `V2-P4-028` had already retracted.

    The two halves of one `--help` output disagreed. This command's docstring said, correctly,
    that the old bound "IS GONE, and with it the reason the neutralised tier was a year-end
    operation", and that what remains is one session wide. Two paragraphs down, in the option
    table, `--tier` still said::

        `--tier neutralized` only succeeds at a prediction instant at or after the panel's own
        stored horizon

    A caller reading the option they are about to type -- which is the half of `--help` anybody
    reads -- was told to wait for the panel's horizon before building a residual at all. On this
    fixture that is `2026-01-16`, and the build below runs at `2026-01-08` and `2026-01-09`.

    **The prose is asserted against a build in the same test on purpose.** A test that only
    greps `--help` proves the sentence changed, not that the sentence is now true, and this file
    exists because a sentence that was true once stopped being true without anything going red.
    So the command line actually writes the neutralised tier before the horizon first, and the
    help assertions run against a claim that has just been measured.

    `test_the_neutralised_tier_builds_at_the_mid_window_instants_it_used_to_refuse` drives the
    same window through the SDK and compares the coverage distribution against the horizon
    instant; this is the command-line face and its `--help`.
    """
    built = cli_build(
        panel_only,
        _build_parameters(tier="neutralized", neutralization="industry_and_size/v1"),
    )

    assert "exit_code" not in built, built
    assert built["tier"] == "neutralized"
    assert built["manifest_ids"]["neutralized"], "the third tier was written before the horizon"
    assert max(BUILD_INSTANTS) < HORIZON_INSTANT, (
        "the build above has to sit strictly before the panel's stored horizon or it proves "
        "nothing about the retracted bound"
    )

    rendered = re.sub(r"\s+", " ", rendered_help("factor", "build"))
    assert "at or after the panel's own stored horizon" not in rendered
    assert "stored horizon" not in rendered, (
        "the retracted bound must not survive in any spelling on this option"
    )
    assert "at or after that day's own close" in rendered
    assert "on a day the exchange was open" in rendered


def test_a_refused_neutralisation_leaves_the_store_exactly_as_it_found_it(
    panel_only: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A build that cannot finish writes **nothing**, including the two tiers it could have.

    The failure this ordering exists for: a builder that stored raw and processed and gave up on
    the residual would leave exactly the store shape `factor run` refuses one command later with
    `the_three_tiers_must_have_been_built_at_the_same_instants` -- a message about a different
    thing, one command too late, on a partition the operator now has to unpick.

    Asserted by reading the raw partition back, because "the report says nothing was written" and
    "nothing was written" are two claims and only the second one matters.

    **`V2-P4-028` moved which instant this test has to be taken at, and the move is the finding.**
    It used to be taken at `BUILD_INSTANTS` -- 17:00 Asia/Shanghai on 2026-01-08 and 2026-01-09 --
    because the membership partition refused every instant before its newest assignment. Those
    instants now build all three tiers, so the refusal here is `V2-P4-026`'s bound instead:
    `UNPUBLISHED_INSTANT` is **noon** on 2026-01-08, before that session's own 16:30, and
    `daily_basic` refuses the day by name rather than answering it with an empty cross section.
    The raw tier still builds at that instant -- measured, and it is what keeps this test about
    the ordering rather than about a store nothing could have been written to.
    """
    from panel_fixtures import write_generated_panel

    untouched = tmp_path_factory.mktemp("first-tier-only")
    write_generated_panel(PanelStore(untouched / "panel"), _panel())
    first_tier = sdk_build(
        untouched,
        _build_parameters(
            tier="raw", transform="", neutralization="", as_ofs=[UNPUBLISHED_INSTANT]
        ),
    )
    assert first_tier["tier"] == "raw", (
        "this test needs an instant where the first tier builds and the third does not; a store "
        "that refused every tier would pass it while measuring nothing about the ordering"
    )

    refused = sdk_build(
        panel_only,
        _build_parameters(
            tier="neutralized",
            neutralization="industry_and_size/v1",
            as_ofs=[UNPUBLISHED_INSTANT],
        ),
    )

    assert refused["reason"] == "blocked"
    assert "that session had not published yet" in refused["message"]
    assert "Nothing was written" in refused["message"]

    # The message contract, which used to be driven on the instant above and has to travel with
    # the refusal rather than with the instant: a caller told only `blocked` cannot act, so the
    # limitation code and both remedies are required to be in it.
    assert (
        "the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed"
        in refused["message"]
    )
    assert "--tier\nprocessed" in refused["message"] or "--tier processed" in refused["message"]
    assert "move --as-of" in refused["message"]

    with pytest.raises(Exception, match="partition_missing"):
        load_factor_observations(
            PanelStore(panel_only / "panel"),
            FACTOR_DEFINITIONS.get("reversal_1d/v1"),
            years=(YEAR,),
            as_of=RUN_AS_OF,
        )


def test_a_tier_option_that_decides_nothing_is_refused_rather_than_ignored() -> None:
    """Both directions of the conditional-option rule, with a `match=` that says which.

    An accepted-and-unused option is one a caller reads as having taken effect: somebody who passes
    `--tier raw --neutralization industry_and_size/v1` believes they asked for three tiers. The
    missing direction is the ordinary one. Both refusals name the flag and the tier, so the message
    is the repair.
    """
    with pytest.raises(FactorRequestError, match=r"--transform is required for --tier processed"):
        factor_build_request(**_build_parameters(transform=""))
    with pytest.raises(
        FactorRequestError, match=r"--neutralization is required for --tier neutralized"
    ):
        factor_build_request(**_build_parameters(tier="neutralized"))
    with pytest.raises(FactorRequestError, match=r"--transform .* decides nothing for --tier raw"):
        factor_build_request(**_build_parameters(tier="raw"))
    with pytest.raises(
        FactorRequestError, match=r"--neutralization .* decides nothing for --tier processed"
    ):
        factor_build_request(**_build_parameters(neutralization="industry_and_size/v1"))


def test_a_freshness_bound_is_stated_or_waived_and_never_defaulted() -> None:
    """`--max-staleness-days` and `--waive-max-staleness`: exactly one, always.

    The one decision `run_factor_experiment` can leave to `panel doctor` and a builder cannot,
    because `compute_factor` takes the requirement rather than making one and every requirement
    builder refuses to default this field. Three refusals, each with its own `match=`: neither,
    both, and a bound no partition could satisfy.
    """
    with pytest.raises(FactorRequestError, match="state --max-staleness-days N or"):
        factor_build_request(**_build_parameters(max_staleness_days=None))
    with pytest.raises(FactorRequestError, match="state two different bounds"):
        factor_build_request(**_build_parameters(waive_max_staleness=True))
    with pytest.raises(FactorRequestError, match="--max-staleness-days must be at least 1"):
        factor_build_request(**_build_parameters(max_staleness_days=0))

    waived = factor_build_request(
        **_build_parameters(max_staleness_days=None, waive_max_staleness=True)
    )
    assert waived.max_staleness is None


def test_the_waiver_this_command_offers_is_refused_by_the_engine_that_reads_the_bound(
    panel_only: Path,
) -> None:
    """`V2-P4-100`'s third account: `--help` offered two options and only one of them builds.

    The help said *"state it or waive it with --waive-max-staleness; there is no third option"*
    and the command's own printed example used the waiver. Run verbatim, it exits `1`:
    `compute_factor._validate_requirements` refuses a waived `max_staleness` for **every** dataset
    a factor reads, because the engine reads through `read_visible_at` and a waived bound accepts
    a slice reaching arbitrarily far short of `as_of` while every structural check clears.

    The request contract keeps the flag -- it is what makes `factor_build_request` able to refuse
    neither-and-both -- so this is a wall to name rather than a flag to delete, and both halves
    are driven here: the waived build refuses by that rule and the bounded one writes a tier.
    """
    waived = CliRunner().invoke(
        app,
        _cli_arguments(
            panel_only,
            _build_parameters(
                tier="raw", transform="", max_staleness_days=None, waive_max_staleness=True
            ),
        ),
    )
    assert waived.exit_code == 1, waived.output
    assert "the daily requirement waives max_staleness" in waived.output
    assert "State a bound" in waived.output

    bounded = CliRunner().invoke(
        app, _cli_arguments(panel_only, _build_parameters(tier="raw", transform=""))
    )
    assert bounded.exit_code == 0, bounded.output
    assert json.loads(bounded.output)["coverage"]["raw"] == {"computed": 16}

    rendered = re.sub(r"\s+", " ", rendered_help("factor", "build"))
    assert "there is no third option" not in rendered
    assert "on this face the two options are not two" in rendered
    assert "measured NOT to reach a build" in rendered
    assert "--year 2026 --max-staleness-days 30 --runtime-dir ./runtime" in rendered
    assert "--year 2026 --waive-max-staleness --runtime-dir ./runtime" not in rendered


def test_a_request_that_cannot_be_put_names_the_rule_that_refused_it() -> None:
    """Every remaining `factor_build_request` rule, each with a `match=` narrow enough to say which.

    A bare `pytest.raises(FactorRequestError)` would pass for any of a dozen reasons and would keep
    passing after the rule it was written for was deleted -- `test_factor_view_rules.py`'s standing
    requirement, applied to the builder's own eight refusals.
    """
    with pytest.raises(FactorRequestError, match=r"--tier must be one of"):
        factor_build_request(**_build_parameters(tier="residual"))
    with pytest.raises(FactorRequestError, match="names no prediction instant"):
        factor_build_request(**_build_parameters(as_ofs=[]))
    with pytest.raises(FactorRequestError, match="must be a timezone-aware instant"):
        factor_build_request(**_build_parameters(as_ofs=[datetime(2026, 1, 8, 9, 0)]))
    with pytest.raises(FactorRequestError, match="names the same instant twice"):
        factor_build_request(**_build_parameters(as_ofs=[BUILD_INSTANTS[0], BUILD_INSTANTS[0]]))
    with pytest.raises(FactorRequestError, match="names no partition year"):
        factor_build_request(**_build_parameters(years=[]))
    with pytest.raises(FactorRequestError, match="names a year twice"):
        factor_build_request(**_build_parameters(years=[YEAR, YEAR]))
    with pytest.raises(FactorRequestError, match=r"--year \[1889\] is outside"):
        factor_build_request(**_build_parameters(years=[1889]))
    with pytest.raises(FactorRequestError, match="exchange must be a non-empty name"):
        factor_build_request(**_build_parameters(exchange=" SZSE "))
    with pytest.raises(FactorRequestError, match="--code-commit must be at least 7"):
        factor_build_request(**_build_parameters(code_commit="abc"))
    with pytest.raises(FactorRequestError, match="--subject names the same value twice"):
        factor_build_request(**_build_parameters(subjects=[SECURITIES[0], SECURITIES[0]]))
    with pytest.raises(FactorRequestError, match="--subject was given an empty value"):
        factor_build_request(**_build_parameters(subjects=["  "]))


def test_a_rebuild_that_would_drop_a_stored_build_is_refused_and_names_the_remedy(
    panel_only: Path,
) -> None:
    """The drop guard, enveloped as this face's own `blocked` and carrying the flag that fixes it.

    A partition is replaced whole, so a second build of one year under a different `--code-commit`
    would silently drop the first. `write_factor_panels` refuses it; what this face adds is the
    **remedy**, which the guard cannot name because `supersedes` is three different options one
    plane up. Driven rather than asserted, and the repair is driven too -- a refusal with an
    unusable remedy is not a better refusal.
    """
    first = _sdk(panel_only).build_factor_panels(**_build_parameters(tier="raw", transform=""))

    clash = sdk_build(
        panel_only, _build_parameters(tier="raw", transform="", code_commit="9876543210fedcba")
    )
    assert clash["reason"] == "blocked"
    assert "--supersedes-raw" in clash["message"]

    repaired = _sdk(panel_only).build_factor_panels(
        **_build_parameters(
            tier="raw",
            transform="",
            code_commit="9876543210fedcba",
            supersedes_raw=list(first.manifest_ids["raw"]),
        )
    )
    assert repaired.manifest_ids["raw"] != first.manifest_ids["raw"]


def test_a_delisted_name_is_evaluated_and_coded_rather_than_dropped(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The subjects are the registry's whole membership; the universe is the day's listed set.

    `compute_factor` requires the two separately because `not_in_universe` is one of its answers,
    and a builder that passed `universe.listed_on(day)` as *both* would make that answer
    unreachable -- a delisted name would simply vanish from the census, which is the shape
    `KNOWN_UNIVERSE_LIMITATIONS.a_listed_only_registry_is_invisible_to_every_downstream_check`
    exists about.

    **This test exists because that mutation survived the rest of the file.** Every other fixture
    here is generated without `universe.delisted_security`, so every registry code is listed on
    every prediction day and the two sets are equal -- an assertion on that fixture cannot tell
    them apart, which is exactly the "the assertion exists but the fixture cannot separate the two
    answers" failure this repository has measured more than ten times. This one adds the shape that
    separates them.
    """
    from panel_fixtures import DELISTED_SECURITY, generate_panel, write_generated_panel

    runtime_dir = tmp_path_factory.mktemp("delisting")
    panel = generate_panel(shapes=(*SHAPES, "universe.delisted_security"))
    write_generated_panel(PanelStore(runtime_dir / "panel"), panel)

    report = _sdk(runtime_dir).build_factor_panels(**_build_parameters(tier="raw", transform=""))

    # The registry carries one code the price panel never quotes -- a name that terminated before
    # the range -- which is exactly the security a listed-only subject list would lose.
    assert DELISTED_SECURITY not in panel.securities
    assert report.subject_count == len(panel.securities) + 1
    assert all(count == len(panel.securities) for count in report.universe_counts)
    assert report.coverage["raw"]["not_in_universe"] == len(BUILD_INSTANTS)


def test_the_registry_is_read_at_each_prediction_instant_and_not_once_for_the_build(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Two instants, one termination between them, and the universe must shrink at the second.

    The calendar and the registry are read inside `_computed`, once per prediction instant, and
    that is not an economy foregone: both loaders take an `as_of`, so reading once at the latest
    instant would derive an earlier cross section's `required_dates` from sessions that had not
    been published when it was stamped, and reading once at the earliest would under-cover the
    later ones.

    **Written because the mutation survived.** Moving the registry read to `request.as_ofs[0]`
    left every other test in this file green, because the two baseline instants are consecutive
    sessions and nothing separates them.

    **The separator used to be `--max-staleness-days` and `V2-P4-064` took it away, which is the
    better outcome.** The old version put the two instants eight days apart under a seven-day
    bound: the first was inside it, the second was not, and a build that read the registry once
    would have succeeded where this one had to be refused. That worked only because the *registry*
    was being judged by a session-cadence bound -- which is the defect `V2-P4-064` fixed, so the
    guard was resting on it. What replaces it is the substantive consequence rather than a bound:
    `universe.termination_on_the_newest_session` files a delisting dated 2026-01-16 whose
    `available_time` is midnight that day, so it is withheld at 2026-01-08T17:00+08 and visible at
    2026-01-16T17:00+08, and `delist_date` is exclusive -- the name is listed at the first instant
    and not at the second.

    **It fails in one direction, and `V2-P4-113` corrected the claim that it failed in both.**
    What stood here said `universe_counts` reads `[8, 7]`, that a registry pinned at `as_ofs[0]`
    gives `[8, 8]` and one pinned at `as_ofs[-1]` gives `[7, 7]`. Only the first of the three was
    true. Remeasured on `037ffa8`, mutating this build's registry read alone:

    - `as_of=request.as_ofs[0]` is **red**, but by refusal rather than by a count: the snapshot is
      dated 2026-01-08 and `listed_on(2026-01-16)` is beyond it, so `_computed` raises and the
      build is blocked. It never reaches a `universe_counts` at all, so `[8, 8]` was never its
      answer.
    - `as_of=request.as_ofs[-1]` is **green over this whole file** -- `36 passed`, identical to
      baseline. It answers `[8, 7]`, which is the correct answer, not `[7, 7]`.

    The `[7, 7]` was the answer to a different mutation than the one named. What this test covers
    is therefore the *early* read; the late read is look-ahead and this fixture cannot see it,
    because `universe_counts` is structurally blind to it.
    `test_a_registry_read_at_the_last_instant_hands_an_earlier_one_a_security_that_had_not_listed`
    below is the guard that does see it, and its docstring carries the proof of the blindness.

    The control below stands unchanged and still does its job: the `plain` shape, with no
    lifecycle event inside the window at all, answers `[8, 8]` for both instants, so the `[8, 7]`
    above is measuring the read and not the fixture.
    """
    from panel_fixtures import write_generated_panel
    from test_factor_interfaces import SHAPES

    runtime_dir = tmp_path_factory.mktemp("per-instant")
    write_generated_panel(
        PanelStore(runtime_dir / "panel"),
        _panel((*SHAPES, "universe.termination_on_the_newest_session")),
    )
    unterminated = tmp_path_factory.mktemp("no-termination")
    write_generated_panel(PanelStore(unterminated / "panel"), _panel())

    straddling = sdk_build(
        runtime_dir,
        _build_parameters(
            tier="raw",
            transform="",
            as_ofs=[BUILD_INSTANTS[0], HORIZON_INSTANT],
            max_staleness_days=30,
        ),
    )
    without_the_event = sdk_build(
        unterminated,
        _build_parameters(
            tier="raw",
            transform="",
            as_ofs=[BUILD_INSTANTS[0], HORIZON_INSTANT],
            max_staleness_days=30,
        ),
    )

    assert "reason" not in straddling, straddling
    assert straddling["universe_counts"] == [8, 7]
    # The control: without the lifecycle event the two instants are indistinguishable, which is
    # the answer a build that read the registry once would give -- so the assertion above is
    # measuring the read and not the fixture.
    assert without_the_event["universe_counts"] == [8, 8]


LATE_LISTED_SECURITY: Final[str] = "000005.SZ"
"""A registry-only code whose whole lifecycle falls *between* the two prediction instants.

Registry-only for `universe.delisted_security`'s reason: a code the price grid never quotes puts
a lifecycle row into `stock_basic` without moving the priced cross section, which
`write_daily_panel`'s explained-share floor judges. `000004.SZ` is that shape's name, so this one
takes the next free code.
"""

LATE_LISTED_ON: Final[date] = date(2026, 1, 12)
LATE_DELISTED_ON: Final[date] = date(2026, 1, 14)
"""Both strictly after 2026-01-08 and strictly before 2026-01-16 -- the two instants' own days.

`delist_date` is exclusive, so at 2026-01-16 this name is *known and not listed*: it is in
`StockUniverse.securities`, and `listed_on` leaves it out. That is the state the assertion below
counts, and it is reachable at the second instant only.
"""


def _with_a_lifecycle_between_the_instants(panel: GeneratedPanel) -> GeneratedPanel:
    """`panel` with two extra `stock_basic` rows, appended through the batch's own constructor.

    `dataclasses.replace` rather than a new `PANEL_SHAPES` entry: this form is wanted by exactly
    one test, and `GeneratedPanel`'s own docstring blesses `replace` on a panel. Every write-time
    guard still runs -- `write_generated_panel` drives the real `write_stock_universe`.
    """
    registry = panel.batch(STOCK_BASIC_DATASET)
    added = ((LISTING_EVENT, LATE_LISTED_ON), (DELISTING_EVENT, LATE_DELISTED_ON))
    stamps = tuple(
        datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=8)
        for _event, day in added
    )
    columns = tuple(
        PanelColumn(
            column.name,
            column.kind,
            column.values
            + tuple(
                {
                    "lifecycle_event": event,
                    "lifecycle_date": day.isoformat(),
                    "exchange": EXCHANGE,
                }[column.name]
                for event, day in added
            ),
        )
        for column in registry.columns
    )
    timeline = TimelineColumns(
        event_time=registry.timeline.event_time + stamps,
        available_time=registry.timeline.available_time + stamps,
        ingested_time=registry.timeline.ingested_time + stamps,
        revision_time=registry.timeline.revision_time + stamps,
    )
    augmented = replace(
        registry,
        subjects=(*registry.subjects, LATE_LISTED_SECURITY, LATE_LISTED_SECURITY),
        columns=columns,
        timeline=timeline,
    )
    return replace(panel, batches={**panel.batches, STOCK_BASIC_DATASET: augmented})


def test_a_registry_read_at_the_last_instant_hands_an_earlier_one_a_security_that_had_not_listed(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The look-ahead direction of the per-instant registry read, which nothing measured before.

    ## What `V2-P4-064` claimed, and what `V2-P4-113` measured

    `test_the_registry_is_read_at_each_prediction_instant_and_not_once_for_the_build` above says
    it "fails in both directions", with `as_ofs[0]` answering `[8, 8]` and `as_ofs[-1]` answering
    `[7, 7]`. Both halves of that were remeasured on `037ffa8` and both were wrong. `as_ofs[0]`
    does go red, but by *refusal* and not by a count -- `listed_on(2026-01-16)` is beyond a
    snapshot dated 2026-01-08, so the build is blocked. And `as_ofs[-1]` **survives the whole
    file**: 36 passed, byte-identical to baseline. It answers `[8, 7]`, which is the correct
    answer.

    ## Why `universe_counts` cannot see a late read, on any fixture

    Not a thin fixture -- a structural blindness, and worth stating because it is what makes the
    remedy a different assertion rather than a different panel. `stock_basic` is
    `ClockStrategy.calendar_static`, so `panel_ingest._knowable_through_the_same_day` makes a row
    dated `D` visible to every `as_of` on `D` and to none before it. `listed_on(day)` keeps an
    entry when `listed_on <= day` and `day < delisted_on`. So a row can only *change* the answer
    for `day` when its own date is at or before `day` -- and that is exactly the condition under
    which it was already visible at `day`'s own instant. Reading the registry *later* therefore
    cannot move `listed_on(day)` for any earlier `day`, on this fixture or any other. (Reading it
    *earlier* is a different matter, and the snapshot horizon catches that one.)

    A fixture whose `available_time` trailed its `delist_date` does not reach it either: this
    dataset's visibility keys off the lifecycle **date** through the census bound, not off the
    stored `available_time`, so such a row is visible at the earlier instant regardless.

    ## What a late read does move: the subject list

    `_computed` derives `subjects` from `universe.securities`, which is every *visible* row
    whatever its date -- and that set is not date-filtered, so it is where a late read shows.
    This name's rows are dated 2026-01-12 and 2026-01-14. At the first instant they are not yet
    knowable, so the security is not in the registry at all and is scored at neither instant. At
    the second both are visible, and the name is a subject that `listed_on` excludes -- one
    `not_in_universe` observation, at the second instant only.

    A registry pinned at `as_ofs[-1]` puts that same name into the **first** instant's subject
    list, where it is a security that had not listed yet: a name knowable on 2026-01-16 read into
    a cross section stamped 2026-01-08. `not_in_universe` counts 2 instead of 1, and that is the
    mutant dying.
    """
    from panel_fixtures import generate_panel, write_generated_panel

    runtime_dir = tmp_path_factory.mktemp("late-registry")
    write_generated_panel(
        PanelStore(runtime_dir / "panel"),
        _with_a_lifecycle_between_the_instants(generate_panel(shapes=SHAPES)),
    )

    report = _sdk(runtime_dir).build_factor_panels(
        **_build_parameters(
            tier="raw",
            transform="",
            as_ofs=[BUILD_INSTANTS[0], HORIZON_INSTANT],
            max_staleness_days=30,
        )
    )

    # The count the docstring above explains is blind: correct and late-pinned both answer this.
    assert report.universe_counts == (8, 8)
    # The one that separates them. One instant knows this name, the other cannot.
    assert report.coverage["raw"]["not_in_universe"] == 1


def test_a_factor_reading_a_dataset_with_no_requirement_builder_is_refused_by_name(
    panel_only: Path,
) -> None:
    """The closed table's own refusal, driven, and the audit that it currently covers everything.

    `REQUIREMENT_BUILDERS` is closed because `compute_factor` refuses to invent a
    `ReadinessRequirement` -- a builder that guessed would ask a weaker question than the reader
    asks. The equality is what makes "closed" checkable; the refusal is what makes it useful. A
    factor declaring a dataset outside the table is *declarable* (`FactorField`'s check is
    syntactic and says so), so this really is reachable, and the probe registry is how it is
    reached without adding a twentieth shipped factor.

    `V2-P3-016` added the seventh row and it is the third that takes a `TradingCalendar` rather
    than a `dataset=` keyword, which is why `_CALENDAR_SCOPED_REQUIREMENTS` is a named set beside
    the table instead of a tuple literal inside the loop that dispatches on it.
    """
    declared = {dataset for item in FACTOR_DEFINITIONS.definitions for dataset in item.datasets}

    assert declared <= set(REQUIREMENT_BUILDERS)
    assert set(REQUIREMENT_BUILDERS) == {
        "daily",
        "daily_basic",
        "index_daily",
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
    }

    probe = FactorDefinition(
        key="probe_band",
        version=1,
        family="momentum_reversal",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="stk_limit", column="up_limit"),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )
    request = factor_build_request(
        **_build_parameters(factor="probe_band/v1", tier="raw", transform=""),
        factors=FactorRegistry((probe,)),
    )
    with pytest.raises(FactorRequestError, match=r"reads \['stk_limit'\], which this command has"):
        build_factor_panels(PanelStore(panel_only / "panel"), request, built_at=RUN_AS_OF)


def test_a_statement_factor_states_its_readiness_question_on_the_announcement_axis(
    panel_only: Path,
) -> None:
    """The other branch of `_requirements`: a period-indexed dataset gets its own builder.

    `daily`/`daily_basic` take a calendar and derive `required_dates` from it; the four statement
    endpoints share `financial_statement_requirement`, which waives `required_dates` (a year's
    announcement days are the disclosure calendar of ~5,500 issuers) and re-derives
    `required_fields` from the dataset name. Both branches have to be exercised, or a build of a
    value, quality or growth factor would be reaching a branch nothing ran.

    The outcome is asserted as a *named* answer rather than as a success: this generated panel
    holds two report periods and `revenue_yoy/v1` declares `lookback_periods=5`, so the honest
    result is a refusal about the window rather than a cross section, and it arrives from
    `compute_factor` -- past the requirement builder, which is the branch this test is about. What
    matters here is that the readiness question was stated at all and that the refusal names the
    axis rather than a class.
    """
    answer = sdk_build(
        panel_only, _build_parameters(factor="revenue_yoy/v1", tier="raw", transform="")
    )

    assert answer["reason"] == "panel_unreadable"
    assert "revenue_yoy/v1" in answer["message"]
    assert "period window that spans a year boundary" in answer["message"]


def test_an_undeclared_transform_is_refused_by_the_builder_and_names_the_declared_one() -> None:
    """`--transform` resolves through the registry, so a handle nothing declares is a bad request.

    Separate from `test_a_tier_option_that_decides_nothing_is_refused_rather_than_ignored`, which
    covers the *absent* and *superfluous* directions; this is the *wrong* one, and it is the branch
    that turns the registry's own `ValueError` into this face's single "the question cannot be put"
    type.
    """
    with pytest.raises(FactorRequestError, match="cross_section_standard/v1"):
        factor_build_request(**_build_parameters(transform="zscore/v1"))
    with pytest.raises(FactorRequestError, match="industry_and_size/v1"):
        factor_build_request(**_build_parameters(tier="neutralized", neutralization="industry/v1"))


# --- what the faces render ----------------------------------------------------------------------


def _leaf_paths(node: object, prefix: tuple[str | int, ...] = ()) -> Iterator[tuple[Any, ...]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaf_paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaf_paths(value, (*prefix, index))
    else:
        yield prefix


def _at(node: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        node = node[step]
    return node


def _expected_build_view(report: FactorBuildReport) -> dict[str, Any]:
    """What `build_view` must render, written out here from the report rather than from the code.

    A second, independent statement of the mapping -- which is the only form of per-key audit
    available to a rendering with no seal behind it. Every key is compared, so a key that stopped
    being rendered, started being rendered from the wrong field, or gained a stale value fails
    here. `panel_view.py` had 100% line coverage on 54 rendered keys and 19 of them were never
    asserted; this is what that measurement asks for.
    """
    return {
        "schema_version": "factor-build-view/v1",
        "factor": report.factor,
        "factor_id": report.factor_id,
        "tier": report.tier,
        "as_ofs": [instant.isoformat() for instant in report.as_ofs],
        "subject_count": report.subject_count,
        "universe_counts": list(report.universe_counts),
        "manifest_ids": {tier: list(ids) for tier, ids in report.manifest_ids.items()},
        "coverage": {tier: dict(counts) for tier, counts in report.coverage.items()},
        "partitions": list(report.partitions),
    }


def test_every_key_the_build_faces_render_is_separately_falsifiable(panel_only: Path) -> None:
    """Walk every scalar leaf of the rendered body and require an assertion to notice each.

    Two halves, and neither is enough alone. The equality against `_expected_build_view` pins every
    key to a value derived independently of `build_view`. The perturbation loop then proves that
    equality is not vacuous: change any one leaf and it must fail, so a key nothing depends on
    cannot hide inside a body that happens to compare equal.
    """
    report = _sdk(panel_only).build_factor_panels(**_build_parameters())
    rendered = build_view(report)
    expected = _expected_build_view(report)
    paths = list(_leaf_paths(rendered))

    assert rendered == expected
    assert len(paths) == RENDERED_BUILD_LEAVES, (
        f"the rendered build report now has {len(paths)} scalar leaves and this audit expected "
        f"{RENDERED_BUILD_LEAVES}; a leaf gained or lost is a change to what the faces hand out"
    )
    survivors = []
    for path in paths:
        edited = json.loads(json.dumps(rendered))
        parent = _at(edited, path[:-1])
        value = _at(edited, path)
        parent[path[-1]] = (value + 1) if isinstance(value, int) else str(value) + "x"
        if edited == expected:
            survivors.append(path)

    assert survivors == [], f"{len(survivors)} rendered key(s) no assertion holds: {survivors}"


def test_the_report_names_what_it_actually_stored(panel_only: Path) -> None:
    """Each rendered field against the thing it is a projection of, not against another rendering.

    `partitions` is the one worth naming: it is the writers' own `PartitionRef`s, so an empty tuple
    here would mean the guards ran and nothing reached disk -- which is the exact failure a report
    built from the request rather than from the write would hide.
    """
    report = _sdk(panel_only).build_factor_panels(**_build_parameters())
    definition = FACTOR_DEFINITIONS.get("reversal_1d/v1")

    assert report.factor == "reversal_1d/v1"
    assert report.factor_id == definition.factor_id
    assert report.tier == "processed"
    assert report.as_ofs == BUILD_INSTANTS
    assert report.subject_count == REGISTRY_SECURITIES
    assert len(report.universe_counts) == len(BUILD_INSTANTS)
    assert all(0 < count <= REGISTRY_SECURITIES for count in report.universe_counts)
    assert len(report.manifest_ids["raw"]) == len(BUILD_INSTANTS)
    assert sum(report.coverage["raw"].values()) == REGISTRY_SECURITIES * len(BUILD_INSTANTS)
    assert report.coverage["neutralized"] == {}
    assert set(report.partitions) == {
        f"{factor_observation_dataset(definition)}@{YEAR}",
        f"factor_manifest_{definition.key}_v{definition.version}@{YEAR}",
        f"factor_proc_{definition.key}_v{definition.version}@{YEAR}",
        f"factor_procmn_{definition.key}_v{definition.version}@{YEAR}",
    }


def test_the_terminal_rendering_shows_every_tier_including_the_ones_not_built(
    panel_only: Path,
) -> None:
    """Three rows always, so "not asked for" and "missing" are not one reading.

    `attribution_rows`' rule on the other command, and it matters more here: the next thing this
    operator does is run an experiment, and an experiment needs all three tiers. A build report
    that silently omitted the tier it did not write would be the last chance to notice.

    Driven through the command with `--json` removed, so what is asserted is the text a human sees
    rather than a helper the command happens to call.
    """
    arguments = [
        flag for flag in _cli_arguments(panel_only, _build_parameters()) if flag != "--json"
    ]
    printed = CliRunner().invoke(app, arguments)
    assert printed.exit_code == int(PanelExit.ok), printed.stderr
    body = printed.stdout
    rows = build_rows(
        _sdk(panel_only).build_factor_panels(**_build_parameters())  # a byte-identical rebuild
    )

    assert [tier for tier, _builds, _rows, _coverage in rows] == [
        "raw",
        "processed",
        "neutralized",
    ]
    assert rows[2] == ("neutralized", "-", "-", "-")
    assert rows[0][1] == str(len(BUILD_INSTANTS))
    assert "computed" in rows[0][3]
    for tier, builds, observations, coverage in rows:
        assert f"{tier:<15} {builds:>6}  {observations:>4}  {coverage}" in body
    assert "reversal_1d/v1" in body
    assert "openalpha factor run" in body


def test_a_malformed_prediction_instant_is_a_bad_request_on_the_command_line(
    panel_only: Path,
) -> None:
    """`--as-of` is parsed before anything else and a bad one is exit `3`, not a crash.

    The command line's own conversion, and it is deliberately **not** `_panel_as_of`: that helper
    defaults an empty value to the wall clock, and a *prediction* instant must never be defaulted
    -- a cross section stamped at "now" is one nobody asked for, at a day nobody named, stored
    under that instant forever. Both a date with no offset and a date with no time are refused,
    because both are the shapes a caller actually types.
    """
    runner = CliRunner()
    base = _cli_arguments(panel_only, _build_parameters(as_ofs=[]))
    naive = runner.invoke(app, [*base, "--as-of", "2026-01-08"])
    nonsense = runner.invoke(app, [*base, "--as-of", "yesterday"])

    assert nonsense.exit_code == int(PanelExit.bad_request)
    assert "--as-of expects an ISO-8601 instant with an offset" in nonsense.stderr
    assert naive.exit_code == int(FACTOR_EXIT["bad_request"])
    assert "must be a timezone-aware instant" in naive.stderr


def test_no_http_route_builds_a_factor_partition(panel_only: Path) -> None:
    """The absence, pinned so it stays a decision rather than an oversight.

    `openalpha panel build` has no HTTP twin either, and the reason is the same one sharpened: this
    writes panel partitions, a partition is replaced whole, and the service ships with no
    authentication of its own. A `POST` that replaced a stored partition would hand that to whoever
    could reach the port. Checked against the live route table rather than by trying one URL, so a
    route added under any path is caught.
    """
    application = create_app(runtime_dir=panel_only)
    posts = {
        route.path  # type: ignore[attr-defined]
        for route in application.routes
        if "POST" in getattr(route, "methods", set())
    }

    assert "/api/v1/factors/build" not in posts
    assert not any("build" in path and "factor" in path for path in posts)
    assert TestClient(application).post("/api/v1/factors/build", json={}).status_code == 404


# --- V2-P4-052: `--code-commit ""` means the same thing on both faces ----------------------------


def test_an_explicitly_empty_code_commit_is_refused_on_both_build_faces(panel_only: Path) -> None:
    """`V2-P4-046`'s defect, on the second of the two `factor` commands that carried it.

    An empty-string default plus `_resolved_code_commit(code_commit or None)` gives the parser no
    value that means "the caller typed an empty one", so `""` collapsed into *omitted* and was
    resolved from this process's git -- while `factor_build_request`, which the SDK reaches
    directly, refuses it by name. The artifact that lands is a **stored factor partition** stamped
    with a commit the caller never declared, and `code_commit` is inside every observation's
    build column, so the mis-stamp outlives the command that made it.

    **The command line is written out rather than routed through `_cli_arguments`, and that is
    load-bearing.** That helper drops any parameter whose value is `""` -- which is the right
    rule for `--neutralization` and `--transform`, the two flags whose empty string means "this
    tier has none" -- so a test that passed `code_commit=""` through it would have asserted on a
    command line with no `--code-commit` on it at all, i.e. on the fallback, and would have been
    green under both the defect and the fix.
    """
    arguments = [
        *_cli_arguments(panel_only, _build_parameters(code_commit=COMMIT)),
        "--code-commit",
        "",
    ]

    result = CliRunner().invoke(app, arguments)
    refused = sdk_build(panel_only, _build_parameters(code_commit=""))

    assert result.exit_code == int(FACTOR_EXIT["bad_request"]), result.stdout
    assert "--code-commit must be at least 7 characters" in result.stderr
    assert refused["reason"] == "bad_request"
    assert "--code-commit must be at least 7 characters" in refused["message"]
    with pytest.raises(Exception, match="partition_missing"):
        load_factor_observations(
            PanelStore(panel_only / "panel"),
            FACTOR_DEFINITIONS.get("reversal_1d/v1"),
            years=(YEAR,),
            as_of=RUN_AS_OF,
        )


def test_an_omitted_code_commit_still_resolves_from_the_process_on_the_build_face(
    panel_only: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The fallback the fix above must not take with it, on this command.

    `--code-commit` is documented as defaulting to the real commit this process runs from, so a
    fix that refused every falsy value would trade one wrong stamp for a command nobody can run
    without one.

    **Asserted on the `manifest_id`s rather than on a rendered string, because `build_view` does
    not render `code_commit` at all.** The address is a content address over every determinant of
    the build including the commit -- which is
    `test_the_command_line_build_reproduces_the_fixtures_own_stored_tiers`' own argument -- so
    driving the same build twice into two stores, once with the resolved commit typed out and once
    with the flag omitted, and requiring identical addresses says two things at once: the fallback
    resolved, and it resolved to `resolve_code_commit()` and not to some other value that merely
    also has seven characters. The negative control is the third build, at this file's literal
    `COMMIT`, whose addresses must differ -- without it an implementation that ignored the commit
    entirely would satisfy the equality.
    """
    from panel_fixtures import write_generated_panel

    typed_out = tmp_path_factory.mktemp("resolved-commit")
    write_generated_panel(PanelStore(typed_out / "panel"), _panel())
    other = tmp_path_factory.mktemp("other-commit")
    write_generated_panel(PanelStore(other / "panel"), _panel())

    omitted = _build_parameters()
    del omitted["code_commit"]
    without = CliRunner().invoke(app, _cli_arguments(panel_only, omitted))
    declared = CliRunner().invoke(
        app, _cli_arguments(typed_out, _build_parameters(code_commit=resolve_code_commit()))
    )
    unrelated = CliRunner().invoke(
        app, _cli_arguments(other, _build_parameters(code_commit=COMMIT))
    )

    assert without.exit_code == int(PanelExit.ok), without.stderr
    assert declared.exit_code == int(PanelExit.ok), declared.stderr
    assert unrelated.exit_code == int(PanelExit.ok), unrelated.stderr
    resolved_ids = json.loads(without.stdout)["manifest_ids"]
    assert resolved_ids == json.loads(declared.stdout)["manifest_ids"]
    assert resolved_ids != json.loads(unrelated.stdout)["manifest_ids"]


# --- V2-P4-064: one bar, six cadences ------------------------------------------------------------


def test_the_event_driven_registry_is_not_bound_by_the_session_cadence_bar(
    panel_only: Path,
) -> None:
    """`--max-staleness-days` is a *session* bound, and `stock_basic` does not publish on sessions.

    `V2-P4-064` measured a factor build refused on a price panel **one day old**:
    `the security registry cannot be read ...: ['stale']; stock_basic reaches 2026-01-19T16:00Z,
    which is 17 days, 17:00:00 behind ... (tolerance 5 days)`. The registry is event-driven -- its
    newest instant is the last time some security listed or delisted -- so its age measures the
    market's corporate-action calendar and not this fetch. The refusal `_build_staleness` raises
    when the flag is omitted says what it is for in the opposite terms: "a price panel whose
    newest session is a month old has missed a month of the market" -- a *session* quantity.
    Those are two different quantities and one number cannot bound both, so
    the only way to run the command was to set the bar to 20--25 days, which switches off the
    check it exists for.

    **The same repository already answers this correctly one command over.** `panel doctor` reads
    `DATASET_CADENCE`, and a dataset declared `event_driven` gets `max_staleness=None` with the
    reason on the record -- "a year with no rows is an ordinary year, not a missed fetch".
    `factor build` read the caller's flag straight through instead.

    **What the generated fixture can and cannot show.** Its registry carries one lifecycle event
    per security, all dated `LISTED_ON` (2026-01-02), against a build instant of
    2026-01-08T17:00+08 -- a gap of 6d17h, so a five-day bar separates the two answers. The real
    corpus's gap was 17d17h at the same bar: the same sign and the same cause, three times the
    magnitude, because a real registry's newest event is whenever the exchange last admitted or
    removed a name rather than the first session of the fixture window. What the fixture would not
    show is a registry that happens to be fresh -- a live build on a day something listed -- which
    is exactly the day on which this defect is invisible.
    """
    result = CliRunner().invoke(
        app,
        _cli_arguments(
            panel_only, _build_parameters(tier="raw", transform="", max_staleness_days=5)
        ),
    )

    assert result.exit_code == int(PanelExit.ok), result.output
    assert json.loads(result.stdout)["coverage"]["raw"] == {"computed": 16}


# --- V2-P4-108: a day the exchange was shut is a verdict, not a traceback ------------------------


NON_SESSION_INSTANT: Final[datetime] = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
"""17:00 Asia/Shanghai on a Saturday inside the generated window.

The neighbouring Monday, 2026-01-12, builds all three tiers at exit `0` -- measured -- so the
fixture separates "the exchange was shut" from "this panel cannot answer at all"."""


def test_a_prediction_instant_on_a_closed_exchange_is_blocked_and_not_a_bare_traceback(
    panel_only: Path,
) -> None:
    """`V2-P4-108`. `--tier neutralized` on a non-session day exited `5` with nothing to act on.

    `_neutralized` catches `_PANEL_FAULTS` around `load_industry_market_cap_cross_section`, and
    `PriceDataError` was not in that tuple -- so `_read_visible_price_session`'s refusal
    ("2026-01-10 is not an open session on the SZSE calendar, so there are no daily_basic rows to
    read for it"), which is a **verdict**, escaped every guard on this face and reached
    `cli._panel_command` as an unanticipated exception: exit `5`, "a defect in the command, not a
    verdict about the panel -- nothing was checked", and the exception's own sentence withheld
    because an unanticipated frame can be holding the credential. The withholding is right; the
    fault being unanticipated was not. That is `V2-P4-060`'s shape exactly, one refusal over.

    **The tier is the whole of it, and it is measured rather than assumed.** At the same instant
    `--tier raw` and `--tier processed` both exit `0`: neither reads a session-scoped price
    partition for the day being priced, so neither can meet this refusal. Only the residual does,
    through `load_daily_valuations`. A test that drove the default tier would have been green.

    **Pre-existing rather than introduced by `V2-P4-028`.** That issue moved the industry read to
    a day-scoped door; the `daily_basic` read has been day-scoped since `V2-P4-026` and raises the
    same refusal from the same function, so the identical call was reachable before either move.

    The message that replaces the traceback is the one this refusal already had --
    `the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed`, whose own sentence
    already said "on a day the exchange was open" -- so what changed is that the arm is reachable,
    not what it says.
    """
    parameters = _build_parameters(
        tier="neutralized", neutralization="industry_and_size/v1", as_ofs=[NON_SESSION_INSTANT]
    )

    result = CliRunner().invoke(app, _cli_arguments(panel_only, parameters))
    refused = sdk_build(panel_only, parameters)

    assert result.exit_code == int(FACTOR_EXIT["blocked"]), result.output
    assert "unhandled" not in result.output
    assert "is not an open session" in result.output
    assert (
        "the_builder_cannot_produce_a_residual_for_a_session_that_has_not_closed" in result.output
    )
    assert refused["reason"] == "blocked"
    assert "is not an open session" in refused["message"]
    assert "Nothing was written" in refused["message"]


# --- V2-P4-109: one exit code, three remedies, and only one of them applies ----------------------


UNCLOSED_SESSION_INSTANT: Final[datetime] = datetime(2026, 1, 12, 1, 0, tzinfo=UTC)
"""09:00 Asia/Shanghai on Monday 2026-01-12 -- an open session, hours before its own 16:30 close.

The Saturday above and this Monday are the two states `V2-P4-108`'s refusal collapsed into one
sentence, and the difference between them is the whole of `V2-P4-109`: the Saturday will *never*
become a session and the Monday becomes one that afternoon. `PanelExit`'s own docstring says the
codes exist so a CI job can tell "re-fetch the data" from "edit the command line", and both of
these exit `1` with a message listing both remedies.
"""


def test_the_two_states_that_share_exit_one_no_longer_share_a_remedy(
    panel_only: Path,
) -> None:
    """`V2-P4-109`. Same code, same message, opposite answers -- measured before it was split.

    On `daaabf5` both instants below produced the same closing sentence: "Build --tier processed
    at this instant, or move --as-of to after the session's close, or name the missing year, or
    fetch the later sessions first." Three of those four are wrong for a Saturday -- the exchange
    is never going to open on 2026-01-10, so no fetch and no waiting produces that session -- and
    "fetch the later sessions first" is wrong for the Monday, whose session exists and simply has
    not published yet.

    The discriminator is `TradingCalendar.day_status`, which is three-valued for exactly this
    reason and is already loaded by `_neutralized` before the read that raises. Asserted in both
    directions per instant, because a message that named *every* remedy would satisfy any
    single-direction check -- which is the message this test replaces.
    """
    runner = CliRunner()

    closed = runner.invoke(
        app,
        _cli_arguments(
            panel_only,
            _build_parameters(
                tier="neutralized",
                neutralization="industry_and_size/v1",
                as_ofs=[NON_SESSION_INSTANT],
            ),
        ),
    )
    unclosed = runner.invoke(
        app,
        _cli_arguments(
            panel_only,
            _build_parameters(
                tier="neutralized",
                neutralization="industry_and_size/v1",
                as_ofs=[UNCLOSED_SESSION_INSTANT],
            ),
        ),
    )

    assert closed.exit_code == int(FACTOR_EXIT["blocked"]) == int(PanelExit.unhealthy) == 1
    assert unclosed.exit_code == int(FACTOR_EXIT["blocked"])

    assert "the exchange was never open on that day" in closed.output
    assert "fetch the later sessions" not in closed.output
    assert "wait" not in closed.output.lower()

    assert "has not published yet" in unclosed.output
    assert "the exchange was never open on that day" not in unclosed.output
    assert "no fetch and no later run produces one" not in unclosed.output
    assert "fetch the later sessions" not in unclosed.output


def test_the_roadmap_records_the_exit_code_this_command_actually_issues() -> None:
    """`V2-P4-108`'s row said the fix yields exit `3`. It yields `1`, and always did.

    `FACTOR_EXIT["blocked"]` is `PanelExit.unhealthy`, which is `1`; the envelope name in the row
    is right and the number beside it was not. Held as a test rather than fixed silently because
    a roadmap row is what the next reader plans against, and this one would have had them writing
    `if [ $? -eq 3 ]` for a command that exits `1`.
    """
    root = Path(__file__).resolve().parents[2]
    roadmap = (root / "docs" / "specs" / "v2" / "openalpha-cn-v2-roadmap.md").read_text(
        encoding="utf-8"
    )
    row = next(line for line in roadmap.splitlines() if line.startswith("| `V2-P4-108`"))

    assert int(FACTOR_EXIT["blocked"]) == 1
    assert f"修后 exit **{int(FACTOR_EXIT['blocked'])}**" in row
    assert "修后 exit 3" not in row


def test_the_shared_exit_code_is_declared_rather_than_left_to_be_discovered() -> None:
    """The half of `V2-P4-109` that could not be closed, named where a reader of the face looks.

    Splitting the *message* is what this wave did. Splitting the *code* was considered and
    refused with a reason: `bad_request` means "no amount of re-fetching fixes it", and a day
    reported `closed` by the loaded calendar can also be a day whose `trade_cal` partition is
    short -- in which case re-fetching is exactly the remedy. Answering `3` there would tell a CI
    job to stop retrying a panel that a retry would repair, which is the mistake in the more
    expensive direction.
    """
    from openalpha_cn.factor_view import KNOWN_FACTOR_RUN_LIMITATIONS

    assert "a_closed_day_and_an_unclosed_session_share_one_exit_code" in {
        limitation.code for limitation in KNOWN_FACTOR_RUN_LIMITATIONS
    }
