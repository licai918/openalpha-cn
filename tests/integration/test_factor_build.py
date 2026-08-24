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

## The tier that still refuses, and why that is the honest answer

The raw and processed tiers build at any prediction instant the panel covers. The neutralised tier
does not, and the bound is arithmetic. It used to be stated by two reads and `V2-P4-026` retracted
one of them: `daily_basic` is now read one session at a time under an availability predicate and
no longer bounds anything here. What remains is `load_industry_histories`, which reads
`index_member_all` through `read_if_ready` and refuses a partition whose newest assignment
post-dates the `as_of`, while `_refuse_a_cross_section_that_is_not_this_panels` requires the
returned section's `as_of` to equal the processed panel's exactly. A residual therefore exists
only at a prediction instant at or after the last stored *assignment* of every membership year
read -- on this generated panel, 2026-01-12. That is `V2-P4-027`'s issue, not this one's, so this
command's job is to be **honest** about it: build what it can, refuse the rest by name, and write
nothing when it refuses. Both halves are driven --
`test_the_neutralised_tier_builds_at_or_after_the_panels_horizon_and_refuses_before_it` and
`test_a_refused_neutralisation_leaves_the_store_exactly_as_it_found_it`.

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, SECURITIES, YEAR
from test_factor_interfaces import BASELINE, PREDICTION_DAYS, RUN_AS_OF, SHAPES, _panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import FACTOR_EXIT, PanelExit, app
from openalpha_cn.domain.factor import FactorDefinition, FactorField, FactorRegistry
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

**It was the earliest instant at which the neutralised tier was assemblable at all, and after
`V2-P4-026` it is no longer the earliest.** `daily_basic` is now read one session at a time under
an availability predicate, so the binding constraint is the membership partition, whose newest
assignment on this generator becomes knowable 2026-01-11T16:00Z --
`tests/integration/panel/test_factor_neutralizations.py::
test_across_the_whole_window_only_the_industry_read_ever_refuses_an_in_year_as_of` measures that
five of the ten sessions now admit a whole build. This constant stays at the horizon because what
the test below it measures is that the builder answers *somewhere* and refuses *somewhere*, and
the horizon is the instant that cannot stop working for a reason outside this file.
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
    5. `factor build --tier neutralized` over the same days -- refused **by name**, with
       `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon` in the message and
       nothing written.

    **Step five is the honest end of this issue's road and is asserted as such rather than papered
    over.** The prediction days here are 2026-01-08 and 2026-01-09, and a residual for either can
    only be built at or after the last stored *assignment* of the membership year they fall in --
    2026-01-12 on this generator -- so the refusal stands and is still `not_yet_knowable`. What
    changed under `V2-P4-026` is which dataset says so: `daily_basic` answers both days, and the
    block is `index_member_all` alone (`V2-P4-027`). What `V2-P3-019` changed is *which* refusal an
    operator gets: from "this repository has no way to build this at all" to a named, stored
    boundary with an issue number on it, reached after two of the three tiers really were built.

    That the third tier is reachable **where the arithmetic allows** is a separate claim with its
    own test, on its own store:
    `test_the_neutralised_tier_builds_at_or_after_the_panels_horizon_and_refuses_before_it`.
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

    refused = cli_build(
        panel_only, _build_parameters(tier="neutralized", neutralization="industry_and_size/v1")
    )
    assert refused["exit_code"] == int(FACTOR_EXIT["blocked"])
    assert (
        "the_builder_cannot_produce_a_residual_before_its_years_stored_horizon" in refused["stderr"]
    )


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


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("factor", "momentum_20_sessions/v1"),
        ("tier", "raw"),
        ("as_ofs", [BUILD_INSTANTS[0]]),
        ("years", [YEAR, YEAR - 1]),
        ("exchange", "SSE"),
        ("max_staleness_days", 1),
        ("subjects", [SECURITIES[0], SECURITIES[1]]),
        ("code_commit", "0f1e2d3c4b5a6978"),
        ("supersedes_raw", ["fbm_0000000000000000000000ff"]),
    ],
)
def test_every_declared_build_parameter_reaches_the_answer_on_both_faces(
    panel_only: Path,
    tmp_path_factory: pytest.TempPathFactory,
    parameter: str,
    value: Any,
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
    """
    baseline_store = tmp_path_factory.mktemp("baseline")
    varied_store = tmp_path_factory.mktemp("varied")
    from panel_fixtures import write_generated_panel

    for target in (baseline_store, varied_store):
        write_generated_panel(PanelStore(target / "panel"), _panel())

    parameters = _build_parameters(**{parameter: value})
    if parameter == "tier":
        parameters["transform"] = ""

    baseline = sdk_build(panel_only, _build_parameters())
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


def test_the_neutralised_tier_builds_at_or_after_the_panels_horizon_and_refuses_before_it(
    panel_only: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Both sides of `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`.

    The refusal is the interesting half and it is not enough on its own: a builder that refused
    *every* neutralisation would pass a refusal-only test while being useless, so the succeeding
    instant is driven in the same test. The message is required to name the limitation code and
    both remedies, because a caller told only `blocked` cannot act.

    `V2-P4-026` narrowed what the refused half proves, and the narrowing is stated because the
    assertions cannot see it: the refused instants are 2026-01-08 and 2026-01-09, and what blocks
    them is now the **membership** partition alone -- `daily_basic` answers both days.
    `tests/integration/panel/test_factor_neutralizations.py::
    test_across_the_whole_window_only_the_industry_read_ever_refuses_an_in_year_as_of` is the
    census that separates the two datasets; this test is about the builder's envelope.
    """
    at_the_horizon = tmp_path_factory.mktemp("at-horizon")
    from panel_fixtures import write_generated_panel

    write_generated_panel(PanelStore(at_the_horizon / "panel"), _panel())

    early = sdk_build(
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

    assert early["reason"] == "blocked"
    assert (
        "the_builder_cannot_produce_a_residual_before_its_years_stored_horizon" in early["message"]
    )
    assert "not_yet_knowable" in early["message"]
    assert "--tier\nprocessed" in early["message"] or "--tier processed" in early["message"]
    assert "move --as-of" in early["message"]

    assert at_horizon["tier"] == "neutralized"
    assert at_horizon["manifest_ids"]["neutralized"]
    assert sum(at_horizon["coverage"]["neutralized"].values()) == REGISTRY_SECURITIES


def test_a_refused_neutralisation_leaves_the_store_exactly_as_it_found_it(
    panel_only: Path,
) -> None:
    """A build that cannot finish writes **nothing**, including the two tiers it could have.

    The failure this ordering exists for: a builder that stored raw and processed and gave up on
    the residual would leave exactly the store shape `factor run` refuses one command later with
    `the_three_tiers_must_have_been_built_at_the_same_instants` -- a message about a different
    thing, one command too late, on a partition the operator now has to unpick.

    Asserted by reading the raw partition back, because "the report says nothing was written" and
    "nothing was written" are two claims and only the second one matters.
    """
    refused = sdk_build(
        panel_only,
        _build_parameters(tier="neutralized", neutralization="industry_and_size/v1"),
    )

    assert refused["reason"] == "blocked"
    assert "Nothing was written" in refused["message"]
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

    # The option table is drawn inside a box, so a wrapped option help carries `|` characters
    # between its lines; collapsing whitespace alone would not rejoin the sentence.
    printed = CliRunner().invoke(app, ["factor", "build", "--help"]).output
    rendered = re.sub(r"\s+", " ", printed.replace("│", " "))
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
    """Two instants, one freshness bound, and only the later one is stale.

    The calendar and the registry are read inside `_computed`, once per prediction instant, and
    that is not an economy foregone: both loaders take an `as_of`, so reading once at the latest
    instant would derive an earlier cross section's `required_dates` from sessions that had not
    been published when it was stamped, and reading once at the earliest would under-cover the
    later ones.

    **Also written because the mutation survived.** Moving the registry read to
    `request.as_ofs[0]` left every other test in this file green, because the two baseline instants
    are consecutive sessions and no bound separates them. Here they are eight days apart under a
    seven-day bound: the first build is inside it and the second is not, so a build that read the
    registry once would succeed where this one must be refused. The refusal is required to name
    the **later** instant's own arithmetic, so a refusal that happened for the earlier instant's
    reasons would not satisfy it either.
    """
    from panel_fixtures import write_generated_panel

    runtime_dir = tmp_path_factory.mktemp("per-instant")
    write_generated_panel(PanelStore(runtime_dir / "panel"), _panel())

    inside = sdk_build(
        runtime_dir,
        _build_parameters(
            tier="raw", transform="", as_ofs=[BUILD_INSTANTS[0]], max_staleness_days=7
        ),
    )
    straddling = sdk_build(
        runtime_dir,
        _build_parameters(
            tier="raw",
            transform="",
            as_ofs=[BUILD_INSTANTS[0], HORIZON_INSTANT],
            max_staleness_days=7,
        ),
    )

    assert "reason" not in inside, inside
    assert straddling["reason"] == "panel_unreadable"
    assert HORIZON_INSTANT.isoformat() in straddling["message"]
    assert BUILD_INSTANTS[0].isoformat() not in straddling["message"]


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
