"""`V2-P4-067(b)` on the face its own reproduction command names: `openalpha factor run`.

The row was closed on a fix that landed in `shortlist_view.py`. Its reproduction command goes
through `factor_view.py`, which was never touched, so all three tiers on the factor face refused
without naming a command. Measured on `daaabf5`, `openalpha factor run` against a store holding a
generated panel and no factor partition::

    EXIT=1
    the raw reversal_1d/v1 observations could not be read out of <path>:
    factor_obs_reversal_1d_v1 year=2026 cannot be read at 2026-01-17T04:00:00+00:00:
    ['partition_missing', 'field_missing']

which is verbatim the sentence the row cites as the defect, with `openalpha factor build` absent
from it.

## Why all three tiers and not only `raw`

`shortlist_view._unbuilt_factor_remedy` covered `raw` alone and justified the boundary by
`neutralized` having "two partition spellings depending on the declared neutralization
(`factor_neut_*` and `factor_neutmn_*`)". **That justification is false and this file measures
it.** `panel_neutralization.neutralized_factor_dataset` is keyed by the *definition* and takes no
neutralisation at all -- "the neutralisation is a filter here and the factor is the dataset", in
`load_neutralized_factor_observations`' own words -- so `factor_neut_<key>_v<n>` holds *every*
neutralisation of one factor and `factor_neutmn_*` is a different dataset (the manifests), not a
second spelling of the observations. `processed_factor_dataset` is the same arrangement one plane
down. There is exactly one observation dataset name per definition on each of the three tiers,
computable from the definition alone, so the question `registered_years` is asked cannot be about
the wrong partition.

`test_a_tier_stored_under_one_neutralisation_is_found_by_a_request_naming_another` is that claim
driven rather than argued: the residuals are written under `industry_and_size/v1` and read back
by a request naming `probe_neutral/v1`, and no rebuild is suggested -- which is the outcome the
old docstring predicted would go wrong.

## The separator

Every case below is a store that holds a **real generated panel** and is short exactly one tier.
An empty runtime directory would refuse for reasons that have nothing to do with the factor
plane (no calendar, no registry), so it cannot tell "this factor is not built" from "nothing is".
The tier below the missing one is always present, which is what forces the read under test to be
the one that raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from test_factor_interfaces import BASELINE, BUILT_AT, store_three_tiers
from test_factor_run import PROBE_NEUTRALIZATION
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.factor_view import KNOWN_FACTOR_RUN_LIMITATIONS, FactorViewError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS
from openalpha_cn.panel_neutralization import neutralized_factor_dataset
from openalpha_cn.sdk import OpenAlphaSDK

REMEDY_PREFIX: Final[str] = "openalpha factor build --factor reversal_1d/v1 --tier "
"""The command the refusal has to name, without its tier, so each row asserts its own."""

MISSING_TIER_CASES: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("raw", frozenset()),
    ("processed", frozenset({"raw"})),
    ("neutralized", frozenset({"raw", "processed"})),
)
"""Each tier, paired with the tiers stored beneath it so that tier's own read is the one to raise.

`factor run` reads the three in order, so a store missing `raw` never reaches the processed read
at all. Naming the written set per row is what makes each row exercise a different `_read` call
rather than three rows all measuring the raw one.
"""


def _argv(runtime_dir: Path, parameters: dict[str, Any]) -> list[str]:
    flags = ["factor", "run", "--runtime-dir", str(runtime_dir), "--json"]
    for name, value in parameters.items():
        flags.append("--" + name.replace("_", "-"))
        flags.append(value.isoformat() if hasattr(value, "isoformat") else str(value))
    return flags


def _rest_body(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        name: (value.isoformat() if hasattr(value, "isoformat") else str(value))
        for name, value in parameters.items()
    }


@pytest.mark.parametrize(
    ("missing", "written"), MISSING_TIER_CASES, ids=[t for t, _ in MISSING_TIER_CASES]
)
def test_the_cli_names_the_build_command_for_whichever_tier_has_no_partition(
    tmp_path: Path, missing: str, written: frozenset[str]
) -> None:
    """`V2-P4-067(b)`, on the row's own reproduction command.

    Before this fix every one of the three rows measured the same sentence with no command in
    it. The `--tier` in the remedy is the tier that is *missing*, not the one `--tier` asked
    for: `factor run` reads all three whatever the caller named, so the actionable line is the
    one that builds the partition the read could not open.
    """
    store_three_tiers(tmp_path, write_tiers=written)

    result = CliRunner().invoke(app, _argv(tmp_path, BASELINE))

    assert result.exit_code == 1
    assert "partition_missing" in result.stderr
    assert REMEDY_PREFIX + missing in result.stderr


@pytest.mark.parametrize(
    ("missing", "written"), MISSING_TIER_CASES, ids=[t for t, _ in MISSING_TIER_CASES]
)
def test_the_rest_face_carries_the_same_remedy_without_the_store_path(
    tmp_path: Path, missing: str, written: frozenset[str]
) -> None:
    """The disclosable half carries the remedy and still not the filesystem layout.

    `_read` builds two messages and only the local one may name the store. A remedy appended to
    one of them and not the other would make the HTTP face the one surface that still cannot
    act on the refusal -- which is the shape this row is about, one transport over.
    """
    store_three_tiers(tmp_path, write_tiers=written)
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: BUILT_AT))

    response = client.post("/api/v1/factors/run", json=_rest_body(BASELINE))

    assert response.status_code == 409
    message = response.json()["detail"]["message"]
    assert REMEDY_PREFIX + missing in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize(
    ("missing", "written"), MISSING_TIER_CASES, ids=[t for t, _ in MISSING_TIER_CASES]
)
def test_the_sdk_raises_the_same_named_refusal(
    tmp_path: Path, missing: str, written: frozenset[str]
) -> None:
    """The third face, so the remedy is a property of `factor_view` rather than of a transport."""
    store_three_tiers(tmp_path, write_tiers=written)
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: BUILT_AT)

    with pytest.raises(FactorViewError) as raised:
        sdk.run_factor_experiment(**BASELINE)

    assert REMEDY_PREFIX + missing in str(raised.value)
    assert REMEDY_PREFIX + missing in raised.value.disclosable


def test_a_tier_stored_under_one_neutralisation_is_found_by_a_request_naming_another(
    tmp_path: Path,
) -> None:
    """The measurement that retires the raw-only boundary, rather than an argument for it.

    `shortlist_view._unbuilt_factor_remedy` excluded the derived tiers because asking
    `registered_years` about "the wrong spelling" would answer "nothing is stored" for a panel
    that holds the other one and hand back a rebuild the caller does not need. There is no wrong
    spelling: the residuals are written here under `industry_and_size/v1` and the dataset they
    land in is named by the *definition*, so a request naming `probe_neutral/v1` opens the same
    partition: the residuals here are written under `probe_neutral/v1` and the request names
    `industry_and_size/v1`, and the read opens the same dataset, filters the rows out and comes
    back empty. That is a `blocked` refusal about the neutralisation, and no build command is
    suggested for a factor whose every tier is on disk.
    """
    store_three_tiers(tmp_path, neutralization=PROBE_NEUTRALIZATION)
    store = PanelStore(tmp_path / "panel")
    definition = FACTOR_DEFINITIONS.get("reversal_1d/v1")
    assert store.registered_years(neutralized_factor_dataset(definition))

    result = CliRunner().invoke(app, _argv(tmp_path, BASELINE))

    assert result.exit_code != 0
    assert "openalpha factor build" not in result.stderr


def test_a_built_panel_that_refuses_for_another_reason_names_no_build_command(
    tmp_path: Path,
) -> None:
    """The other direction, which is what keeps the remedy from being unconditional.

    `_unbuilt_factor_remedy`'s whole boundary is "no partition of this factor at all". A store
    that holds all three tiers and is asked about a range they do not cover must still refuse --
    and must not tell the caller to build what they already built, which is `V2-P4-078`'s
    finding: a command that does not help is worse than none. A test that only asserted the
    remedy appears would stay green on a function that returned it every time.
    """
    store_three_tiers(tmp_path)

    result = CliRunner().invoke(
        app, _argv(tmp_path, {**BASELINE, "start": "2026-01-13", "end": "2026-01-14"})
    )

    assert result.exit_code != 0
    assert "openalpha factor build" not in result.stderr


def test_a_read_that_raises_on_a_year_this_panel_lacks_names_no_build_command(
    tmp_path: Path,
) -> None:
    """The bound itself, on the only fixture that can measure it, found by mutation.

    `_unbuilt_factor_remedy` fires on "no year of this tier is registered" and on nothing else.
    Every other test in this file is either a store with **no** partition (remedy expected) or a
    refusal that never reaches `_read` at all (`FactorRunBlockedError` from `_resolve_instant`,
    which carries no remedy under any implementation) -- so **a mutant that deleted the
    `registered_years` clause and returned the command unconditionally survived all of them.**
    That is this repository's most-recorded shape arriving in the tests written to close an
    instance of it, and this is the fixture that separates the two answers: the panel holds 2026
    on all three tiers, the request asks about 2025, the read *raises* `partition_missing` -- and
    the answer must be the engine's own sentence with nothing appended, because `openalpha factor
    build` for a factor that is already built is `V2-P4-078`'s command-that-does-not-help.
    """
    store_three_tiers(tmp_path)
    unbuilt_year = {
        "start": "2025-01-08",
        "end": "2025-01-09",
        "as_of": "2025-01-17T04:00:00+00:00",
    }

    result = CliRunner().invoke(app, _argv(tmp_path, {**BASELINE, **unbuilt_year}))

    assert result.exit_code == 1
    assert "year=2025" in result.stderr
    assert "partition_missing" in result.stderr
    assert "openalpha factor build" not in result.stderr


def test_the_json_face_of_a_factor_that_is_not_built_is_still_the_enveloped_refusal(
    tmp_path: Path,
) -> None:
    """`--json` does not turn the refusal into a document, and the remedy is on stderr with it."""
    store_three_tiers(tmp_path, write_tiers=frozenset())

    result = CliRunner().invoke(app, _argv(tmp_path, BASELINE))

    assert result.stdout.strip() == ""
    assert REMEDY_PREFIX + "raw" in result.stderr


def test_the_asymmetry_the_shortlist_face_declared_is_no_longer_declared_anywhere() -> None:
    """`only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it` is retired.

    It recorded a boundary whose stated reason -- two partition spellings on the neutralised
    tier -- this file measures to be false, and the boundary is gone with it: all three tiers on
    both faces now name the command. A registry entry left behind after its limitation is closed
    is a limitation nobody can act on, which is the failure the registry audit exists to catch,
    so the code is asserted absent here as an executable literal exactly as it was asserted
    present before.
    """
    from openalpha_cn.shortlist_view import KNOWN_SHORTLIST_VIEW_LIMITATIONS

    declared = {limitation.code for limitation in KNOWN_SHORTLIST_VIEW_LIMITATIONS}
    assert "only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it" not in (
        declared
    )
    assert "the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered" in {
        limitation.code for limitation in KNOWN_FACTOR_RUN_LIMITATIONS
    }


def test_every_face_agrees_the_store_is_short_and_says_the_same_thing(tmp_path: Path) -> None:
    """One store, three faces, one remedy string -- the parity this repository keeps re-proving.

    Written as an equality between the three rendered messages rather than three independent
    substring checks, because three checks all pass on three different sentences that happen to
    contain the same clause.
    """
    store_three_tiers(tmp_path, write_tiers=frozenset({"raw"}))
    sdk = OpenAlphaSDK(runtime_dir=tmp_path, clock=lambda: BUILT_AT)
    client = TestClient(create_app(runtime_dir=tmp_path, clock=lambda: BUILT_AT))

    with pytest.raises(FactorViewError) as raised:
        sdk.run_factor_experiment(**BASELINE)
    rest = client.post("/api/v1/factors/run", json=_rest_body(BASELINE)).json()["detail"]
    cli = CliRunner().invoke(app, _argv(tmp_path, BASELINE))

    assert raised.value.disclosable == rest["message"]
    assert str(raised.value) == cli.stderr.strip()
    assert json.loads(json.dumps(rest))["reason"] == raised.value.reason
