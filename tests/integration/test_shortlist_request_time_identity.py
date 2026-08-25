"""`V2-P4-065`: `--config-digest` is checked when the request is read, not after the store is.

`shortlist_view.shortlist_request` already checked `code_commit` at request time, and the
comment above that check says exactly why: `build_ranking_manifest` "raises the same objection
after a store has already been read and would therefore report a mistyped flag as a fact about
the panel". `config_digest` was left on that later path, so a mistyped one was reported as
`the shortlist ... could not be joined to the evidence this request supplied` -- naming an
evidence join a request had supplied no evidence to, quoting the internal contract
`CandidateRankingManifest`, and only after a whole panel read.

## Why the separator is an empty store rather than a built one

Every case below points at a runtime directory that holds nothing. A request-time refusal
cannot depend on a panel, so an empty store makes the two answers maximally far apart: before
this fix the empty store answered first (`partition_missing`, `field_missing`, exit 1) and the
digest was never examined; after it, the digest answers first (exit 3) and no partition is ever
opened. A fixture with a real panel would have made both orderings produce a refusal and the
test could not have separated them -- which is the shape this repository keeps finding.

`test_a_well_formed_pair_still_reaches_the_panel` is the half that keeps the check honest: a
guard that refused every value would satisfy the five refusal cases and fail only here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.sdk import OpenAlphaSDK

AS_OF: Final[datetime] = datetime(2026, 1, 16, 7, 0, tzinfo=UTC)
COMMIT: Final[str] = "0123456789abcdef"
DIGEST: Final[str] = "a" * 64

REQUEST_FAULT_EXIT: Final[int] = 3
"""`openalpha`'s exit code for a request that cannot be put, as `--code-commit` already used."""

REFUSED: Final[tuple[tuple[str, str, str], ...]] = (
    (COMMIT, "", "--config-digest"),
    (COMMIT, "zz" + "0" * 62, "--config-digest"),
    (COMMIT, "a" * 63, "--config-digest"),
    (COMMIT, "A" * 64, "--config-digest"),
    ("012345", DIGEST, "--code-commit"),
    ("a" * 65, DIGEST, "--code-commit"),
)
"""Six requests and the flag each must name.

The uppercase digest and the 65-character commit are here because the contract is
`^[0-9a-f]{64}$` and `min_length=7, max_length=64`: the old `code_commit` check tested only the
lower bound, so an over-long commit reached `build_ranking_manifest` and was reported as a fact
about the panel exactly as the digest was.
"""


def _argv(runtime_dir: Path, *, code_commit: str, config_digest: str) -> list[str]:
    return [
        "shortlist",
        "run",
        "--tier",
        "raw",
        "--shortlist-size",
        "5",
        "--position-capital",
        "10000",
        "--horizon",
        "5d",
        "--min-tradable-ratio",
        "0.0",
        "--min-researched-ratio",
        "0.0",
        "--max-ranking-age-days",
        "3650",
        "--exchange",
        "SSE",
        "--as-of",
        AS_OF.isoformat(),
        "--code-commit",
        code_commit,
        "--config-digest",
        config_digest,
        "--runtime-dir",
        str(runtime_dir),
        "--json",
        "--component",
        "reversal_1d/v1=1.0",
        "--year",
        "2026",
    ]


def _body(*, code_commit: str, config_digest: str) -> dict[str, Any]:
    return {
        "components": [{"factor": "reversal_1d/v1", "weight": 1.0}],
        "tier": "raw",
        "shortlist_size": 5,
        "position_capital": "10000",
        "as_of": AS_OF.isoformat(),
        "years": [2026],
        "exchange": "SSE",
        "horizon": "5d",
        "minimum_tradable_ratio": 0.0,
        "minimum_researched_ratio": 0.0,
        "maximum_ranking_age_days": 3650,
        "code_commit": code_commit,
        "config_digest": config_digest,
    }


@pytest.mark.parametrize(("code_commit", "config_digest", "flag"), REFUSED)
def test_the_command_line_names_the_flag_before_it_opens_the_store(
    tmp_path: Path, code_commit: str, config_digest: str, flag: str
) -> None:
    result = CliRunner().invoke(
        app, _argv(tmp_path / "rt", code_commit=code_commit, config_digest=config_digest)
    )
    assert result.exit_code == REQUEST_FAULT_EXIT
    stderr = result.stderr
    assert flag in stderr
    assert "panel store" not in stderr
    assert "partition_missing" not in stderr
    assert "CandidateRankingManifest" not in stderr, "the internal contract must not leak"


@pytest.mark.parametrize(("code_commit", "config_digest", "flag"), REFUSED)
def test_the_http_face_names_the_same_flag(
    tmp_path: Path, code_commit: str, config_digest: str, flag: str
) -> None:
    client = TestClient(create_app(runtime_dir=tmp_path / "api", clock=lambda: AS_OF))
    response = client.post(
        "/api/v1/shortlists/run", json=_body(code_commit=code_commit, config_digest=config_digest)
    )
    assert response.status_code == 422, response.text
    assert flag in response.text
    assert "CandidateRankingManifest" not in response.text


@pytest.mark.parametrize(("code_commit", "config_digest", "flag"), REFUSED)
def test_the_sdk_face_names_the_same_flag(
    tmp_path: Path, code_commit: str, config_digest: str, flag: str
) -> None:
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "sdk", clock=lambda: AS_OF)
    payload = _body(code_commit=code_commit, config_digest=config_digest)
    payload["as_of"] = AS_OF
    payload["position_capital"] = Decimal("10000")
    with pytest.raises(Exception) as caught:
        sdk.run_shortlist(**payload)  # type: ignore[arg-type]
    assert flag in str(caught.value)


def test_a_well_formed_pair_still_reaches_the_panel(tmp_path: Path) -> None:
    """The half that separates a real check from one that refuses everything.

    A legal pair against the same empty store must get *past* both checks and be refused by the
    panel instead -- so the exit code moves from the request fault to the gate's own, and the
    message names the missing partition rather than a flag.
    """
    result = CliRunner().invoke(
        app, _argv(tmp_path / "rt", code_commit=COMMIT, config_digest=DIGEST)
    )
    assert result.exit_code != REQUEST_FAULT_EXIT
    assert "--config-digest" not in result.stderr
    assert "--code-commit" not in result.stderr
    assert "partition_missing" in result.stderr


REMEDIED_TIERS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("raw", ()),
    ("processed", ("--transform", "cross_section_standard/v1")),
)
"""Each tier this face can reach a factor read on, and the flags that tier's request needs.

**The flags are the whole point of this constant, and their absence is what made the test this
one replaces unable to say anything.** `V2-P4-067(b)`'s ninth-wave acceptance found the boundary
that pinned `_unbuilt_factor_remedy` to `raw` was justified by a false claim; re-measuring the
test that held the other two tiers *unremedied* found it never reached a factor read at all.
Driven with no `--transform`, `openalpha shortlist run --tier processed` exits **3** with
`a processed-tier screen needs a --transform`, which is `shortlist_request` refusing before any
store is opened -- so `assert "openalpha factor build" not in result.stderr` was asserted against
a sentence that could not have contained it under any implementation, and would have stayed green
whichever way the boundary went. That is the shape this repository keeps finding: the assertion
exists, and on that fixture it cannot separate the two answers.

**`neutralized` is absent by measurement rather than by omission**, and
`test_the_neutralized_tier_is_refused_by_this_face_before_any_partition_is_opened` is that
measurement: `run_shortlist` refuses the tier unconditionally at request time, so no store state
can put a neutralised read on this face. `_rows_for` carries the remedy on that branch anyway --
`_declared_transform`'s rule, that a precondition is stated at the read rather than inferred from
a resolver two calls away, because a `ShortlistRunRequest` is a frozen dataclass and is still
constructible directly -- and the branch is *not* claimed to be covered here. The factor face
reaches all three and `tests/integration/test_factor_unbuilt_remedy.py` drives them.
"""


@pytest.mark.parametrize(
    ("tier", "flags"), REMEDIED_TIERS, ids=[tier for tier, _ in REMEDIED_TIERS]
)
def test_every_tier_of_an_unbuilt_factor_names_the_command_that_builds_it(
    tmp_path: Path, tier: str, flags: tuple[str, ...]
) -> None:
    """`V2-P4-067(b)`, widened from `raw` after the reason for holding back failed.

    `_unbuilt_factor_remedy` shipped raw-only because `neutralized` was said to have "two
    partition spellings depending on the declared neutralization (`factor_neut_*` and
    `factor_neutmn_*`)". `neutralized_factor_dataset` is keyed by the definition and takes no
    neutralisation; `factor_neutmn_*` is the manifest dataset, the structural twin of
    `factor_procmn_*`, which the same paragraph did not treat as making `processed` ambiguous.
    All three tiers have one observations dataset per definition, so all three can be asked the
    `registered_years` question the remedy is gated on.

    Each row exits 1 rather than 3 -- that is what says the request was put and the store was
    what refused it, which is precisely what the replaced test could not say.
    """
    argv = _argv(tmp_path / "rt", code_commit=COMMIT, config_digest=DIGEST)
    argv[argv.index("--tier") + 1] = tier
    result = CliRunner().invoke(app, [*argv, *flags])

    assert result.exit_code == 1
    assert "partition_missing" in result.stderr
    assert f"openalpha factor build --factor reversal_1d/v1 --tier {tier}" in result.stderr


def test_a_read_that_raises_on_a_year_this_panel_lacks_names_no_build_command(
    tmp_path: Path,
) -> None:
    """The same bound on this face, and the same mutant is what found it missing.

    Deleting the `registered_years` clause from `_unbuilt_factor_remedy` -- making the command
    unconditional -- survived every test on both faces, because every one of them pointed at a
    store that held nothing. The panel here holds 2026 and the request asks about 2025, so the
    read raises with the factor demonstrably built: a refusal telling this caller to run
    `openalpha factor build` would send them to rebuild what they already have, which is exactly
    `V2-P4-078`'s finding.
    """
    from test_factor_interfaces import store_three_tiers

    store_three_tiers(tmp_path)
    argv = _argv(tmp_path, code_commit=COMMIT, config_digest=DIGEST)
    argv[argv.index("--year") + 1] = "2025"
    argv[argv.index("--as-of") + 1] = "2025-01-16T07:00:00+00:00"

    result = CliRunner().invoke(app, argv)

    assert result.exit_code == 1
    assert "year=2025" in result.stderr
    assert "openalpha factor build" not in result.stderr


def test_the_neutralized_tier_is_refused_by_this_face_before_any_partition_is_opened(
    tmp_path: Path,
) -> None:
    """Why `REMEDIED_TIERS` has two rows and not three, as a measurement rather than a note.

    `run_shortlist`'s first statement refuses `tier == "neutralized"` outright -- this face loads
    no industry and market-cap cross section, so a request contract rather than a partition is
    what is missing -- and that refusal is `bad_request`. Asserting it here is what keeps the
    two-row constant above honest: if that guard were ever lifted, this goes red and whoever
    lifts it is told that a third row is now reachable and owed a test.
    """
    argv = _argv(tmp_path / "rt", code_commit=COMMIT, config_digest=DIGEST)
    argv[argv.index("--tier") + 1] = "neutralized"

    result = CliRunner().invoke(
        app,
        [
            *argv,
            "--transform",
            "cross_section_standard/v1",
            "--neutralization",
            "industry_and_size/v1",
        ],
    )

    assert result.exit_code == REQUEST_FAULT_EXIT
    assert "a neutralized-tier shortlist needs" in result.stderr
    assert "partition_missing" not in result.stderr


def test_the_boundary_that_survives_is_declared_where_a_reader_would_find_it() -> None:
    """The registry entry backing the bound above, as an executable literal.

    The repository's registry audit reads test *code*, not docstrings, precisely because a
    limitation cited only in prose is a limitation nobody exercises. What is declared has
    changed with the boundary: the tier asymmetry is gone and what remains is the *state* the
    remedy fires on -- "no year of this tier is registered at all" and nothing else -- which is
    `_unbuilt_dataset_remedy`'s bound and `V2-P4-078`'s finding, and is the one thing about this
    function a caller still has to know.
    """
    from openalpha_cn.factor_view import KNOWN_FACTOR_RUN_LIMITATIONS
    from openalpha_cn.shortlist_view import KNOWN_SHORTLIST_VIEW_LIMITATIONS

    declared = {limitation.code for limitation in KNOWN_SHORTLIST_VIEW_LIMITATIONS}
    assert "only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it" not in (
        declared
    )
    assert "the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered" in {
        limitation.code for limitation in KNOWN_FACTOR_RUN_LIMITATIONS
    }
