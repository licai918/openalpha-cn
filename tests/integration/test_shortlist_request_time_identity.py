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


UNREMEDIED_TIERS: Final[tuple[str, ...]] = ("processed", "neutralized")
"""The two tiers `_unbuilt_factor_remedy` deliberately leaves without a command.

Named here as executable literals rather than in prose because
`KNOWN_SHORTLIST_VIEW_LIMITATIONS
.only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it` is what records
the asymmetry, and this repository's registry audit requires such a code to appear in test code
that runs.
"""


def test_the_raw_tiers_unreadable_refusal_names_the_command_that_builds_it(
    tmp_path: Path,
) -> None:
    """`V2-P4-067`, the half the row's own docstring claimed was already covered.

    `_resolve_instant` refuses a read that *succeeds and returns nothing* with the build command
    on it. A store with no partition at all never gets there -- the read *raises* first -- and
    that refusal named no command. Measured before the fix:
    `... ['partition_missing', 'field_missing']`, and nothing else.
    """
    result = CliRunner().invoke(
        app, _argv(tmp_path / "rt", code_commit=COMMIT, config_digest=DIGEST)
    )
    assert "partition_missing" in result.stderr
    assert "openalpha factor build --factor reversal_1d/v1 --tier raw" in result.stderr


@pytest.mark.parametrize("tier", UNREMEDIED_TIERS)
def test_the_other_two_tiers_keep_the_unremedied_message_as_declared(
    tmp_path: Path, tier: str
) -> None:
    """The declared boundary, asserted rather than left to the docstring.

    `only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it` says the
    remedy is raw-only because `neutralized` has two partition spellings and asking
    `registered_years` about the wrong one would hand back a rebuild the caller does not need.
    A test that only checked the raw tier would stay green if somebody widened it wrongly, so
    this is the other direction: these two must still refuse *without* a command, and their
    message must still name the dataset, the year and the instant.
    """
    argv = _argv(tmp_path / "rt", code_commit=COMMIT, config_digest=DIGEST)
    argv[argv.index("--tier") + 1] = tier
    result = CliRunner().invoke(app, argv)
    assert result.exit_code != 0
    assert "openalpha factor build" not in result.stderr


def test_the_asymmetry_is_declared_where_a_reader_of_the_module_would_find_it() -> None:
    """The registry entry backing the boundary above, as an executable literal.

    The repository's registry audit reads test *code*, not docstrings, precisely because a
    limitation cited only in prose is a limitation nobody exercises.
    """
    from openalpha_cn.shortlist_view import KNOWN_SHORTLIST_VIEW_LIMITATIONS

    declared = {limitation.code for limitation in KNOWN_SHORTLIST_VIEW_LIMITATIONS}
    assert "only_the_raw_tiers_unreadable_factor_refusal_names_the_command_that_builds_it" in (
        declared
    )
