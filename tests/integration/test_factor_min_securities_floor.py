"""What `--min-securities` actually admits, and what its `--help` says it admits (`V2-P4-104`).

One flag feeds **two** studies with different floors, and until this issue the help named one of
them as though it were the only one:

    "two points always correlate perfectly, which is why the contract's own floor is 3"

There is no "the contract" here. `factor_view.factor_request` hands the same integer to
`FactorICSpec.min_securities`, whose floor is `factor_ic.MINIMUM_IC_SECURITIES = 3`, and to
`RedundancySpec.min_securities`, whose floor is
`factor_redundancy.MINIMUM_REDUNDANCY_SECURITIES = 4`. The effective floor on this flag is the
higher of the two, and a caller who believed the help got, measured on `d748796`::

    openalpha factor run ... --min-securities 3
    -> 1 validation error for RedundancySpec
       min_securities
         Input should be greater than or equal to 4 [type=greater_than_equal, input_value=3, ...]
           For further information visit https://errors.pydantic.dev/2.13/v/greater_than_equal
       EXIT=3

Two defects in four lines. The help states a floor the face does not have, and the refusal names
a **pydantic model the caller has never heard of** and a flag they did not type -- there is no
`--min-securities` anywhere in it, and no way to get from `RedundancySpec` back to the option
that fed it without reading this repository's source.

## Which half was wrong, decided by measurement rather than by preference

The validation. Both floors are arithmetic and neither can move:

- three points are the first cross section at which `|r| < 1` is attainable, so `3` is a real
  floor for an information coefficient (`factor_ic.MINIMUM_IC_SECURITIES`);
- at `n = 3` an untied rank correlation takes only `+-0.5` and `+-1` over the six permutations of
  three ranks, so **no threshold at or below 0.5 can distinguish anything** and a redundancy
  *verdict* needs a fourth point before it means something
  (`factor_redundancy.MINIMUM_REDUNDANCY_SECURITIES`).

Lowering the redundancy floor to match the help would make every `--redundancy-threshold <= 0.5`
call every pair redundant, which is the survival row the acceptance criterion is read off. So the
help is the half that moves, and the refusal is the half that learns to name the flag.

Both faces that resolve through `factor_request` are driven here -- `openalpha factor run` and
`POST /api/v1/factors/run` -- because the fix is in the shared resolver and a repair that named
the flag on only one of them would be the drift `test_factor_interfaces.py` exists to stop.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.factor_ic import MINIMUM_IC_SECURITIES
from openalpha_cn.backtest.factor_redundancy import MINIMUM_REDUNDANCY_SECURITIES
from openalpha_cn.cli import PanelExit, app

runner = CliRunner()

BASELINE: Final[dict[str, Any]] = {
    "factor": "reversal_1d/v1",
    "transform": "cross_section_standard/v1",
    "neutralization": "industry_and_size/v1",
    "start": "2026-01-08",
    "end": "2026-01-09",
    "as_of": "2026-01-14T09:00:00+00:00",
    "exchange": "SZSE",
    "horizon": "1d",
    "ic_method": "spearman",
    "min_securities": MINIMUM_REDUNDANCY_SECURITIES,
    "min_as_ofs": 2,
    "group_count": 2,
    "min_securities_per_group": 1,
    "position_capital": "100000",
    "min_periods": 2,
    "participation_cap": "0.01",
    "min_rebalances": 2,
    "redundancy_threshold": 0.9,
    "retention_floor": 0.5,
    "code_commit": "0123456789abcdef",
}
"""A request every rule of `factor_request` admits, so only `min_securities` is ever under test.

`min_securities` defaults to the *redundancy* floor rather than to a literal `4`, so this file
cannot come to disagree with the constant it is about.
"""

OPTIONS: Final[dict[str, str]] = {
    "factor": "--factor",
    "transform": "--transform",
    "neutralization": "--neutralization",
    "start": "--start",
    "end": "--end",
    "as_of": "--as-of",
    "exchange": "--exchange",
    "horizon": "--horizon",
    "ic_method": "--ic-method",
    "min_securities": "--min-securities",
    "min_as_ofs": "--min-as-ofs",
    "group_count": "--group-count",
    "min_securities_per_group": "--min-securities-per-group",
    "position_capital": "--position-capital",
    "min_periods": "--min-periods",
    "participation_cap": "--participation-cap",
    "min_rebalances": "--min-rebalances",
    "redundancy_threshold": "--redundancy-threshold",
    "retention_floor": "--retention-floor",
    "code_commit": "--code-commit",
}


def cli_run(runtime_dir: Path, **overrides: Any) -> Any:
    arguments = ["factor", "run", "--runtime-dir", str(runtime_dir)]
    for key, value in {**BASELINE, **overrides}.items():
        arguments.extend([OPTIONS[key], str(value)])
    return runner.invoke(app, arguments)


def rest_run(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/v1/factors/run", json={**BASELINE, **overrides})


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(runtime_dir=tmp_path / "api"), raise_server_exceptions=False)


def rendered_help() -> str:
    """`factor run --help` with the option table's box drawing removed.

    The option help is wrapped inside a bordered table, so a sentence spanning two lines carries
    a `|` between them and collapsing whitespace alone would not rejoin it. Copied from
    `test_factor_build.py`'s own help assertion for the same reason.
    """
    printed = runner.invoke(app, ["factor", "run", "--help"]).output
    return re.sub(r"\s+", " ", printed.replace("│", " "))


def test_the_two_floors_this_one_flag_feeds_are_still_different() -> None:
    """The premise of the whole file, asserted rather than assumed.

    If somebody later reconciled the two constants, every assertion below would still pass while
    testing nothing: the "effective floor is the higher of two" story would have quietly become
    "there is one floor", and the help this issue rewrote would be wrong again in the other
    direction. This is the guard that makes that a red test rather than a silent no-op.
    """
    assert MINIMUM_IC_SECURITIES == 3
    assert MINIMUM_REDUNDANCY_SECURITIES == 4
    assert MINIMUM_REDUNDANCY_SECURITIES > MINIMUM_IC_SECURITIES


def test_the_option_help_states_the_floor_this_face_actually_enforces() -> None:
    """`V2-P4-104`. The stale sentence goes, and what replaces it names both studies.

    Asserting the removal *and* the replacement, because either alone is escapable: deleting the
    claim leaves a caller with no floor at all, and adding a correct sentence beside the wrong one
    leaves a `--help` that contradicts itself -- which is exactly the shape `V2-P4-103` found two
    options up on `factor build --tier`.
    """
    rendered = rendered_help()

    assert "the contract's own floor is 3" not in rendered, (
        "the flag feeds two contracts and 3 is only the looser one's floor"
    )
    assert f"floor on this option is {MINIMUM_REDUNDANCY_SECURITIES}" in rendered
    assert "redundancy" in rendered
    assert "information coefficient" in rendered or "IC" in rendered


def test_the_cli_refusal_names_the_option_rather_than_the_pydantic_model(
    tmp_path: Path,
) -> None:
    """The refusal a caller who believed the old help would have got.

    Four assertions, and the negative ones carry the finding:

    - `RedundancySpec` must **not** appear. It is an internal class name; a caller cannot act on
      it, cannot find it in `--help`, and cannot tell from it which of twenty options to change.
    - `errors.pydantic.dev` must not appear. A link to a validation library's generic error page
      is not a remedy for a domain floor.
    - `--min-securities` must appear, spelled as the caller typed it.
    - both floors must appear, because "the floor is 4" without "and the IC study's is 3" makes
      the number look arbitrary -- and the reason a caller can pass 3 to an IC-only study
      elsewhere and not here is the whole content of the refusal.
    """
    result = cli_run(tmp_path / "runtime", min_securities=MINIMUM_IC_SECURITIES)

    assert result.exit_code == int(PanelExit.bad_request) == 3, result.output
    output = result.output
    assert "RedundancySpec" not in output, "an internal model name is not a remedy"
    assert "errors.pydantic.dev" not in output
    assert "--min-securities" in output
    assert f"at least {MINIMUM_REDUNDANCY_SECURITIES}" in output
    # Not a bare `str(MINIMUM_IC_SECURITIES) in output`: a mutation sweep killed that assertion
    # by inspection rather than by running it -- `MINIMUM_REDUNDANCY_SECURITIES - 1` is also
    # `3` and appears in the same sentence, so searching for the digit passes on a message that
    # has dropped the IC floor entirely. The phrase is what carries the meaning.
    assert f"information coefficient needs {MINIMUM_IC_SECURITIES} securities" in output
    assert "redundancy" in output


def test_the_rest_face_refuses_the_same_value_with_the_same_reason(client: TestClient) -> None:
    """The shared resolver, driven from the other face that reaches it.

    `factor_request` is the one place all three faces resolve through, so a refusal written into
    it must arrive intact on HTTP too. `422` and the `bad_request` reason, because a floor is a
    property of the request rather than of the store -- no amount of building fixes it.
    """
    response = rest_run(client, min_securities=MINIMUM_IC_SECURITIES)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), "a factor refusal is the object body, not a field-error list"
    assert detail["reason"] == "bad_request"
    assert "--min-securities" in detail["message"]
    assert "RedundancySpec" not in detail["message"]
    assert str(MINIMUM_REDUNDANCY_SECURITIES) in detail["message"]


def test_the_floor_itself_is_admitted_and_the_value_below_it_is_the_only_one_refused(
    tmp_path: Path,
) -> None:
    """The boundary, from both sides, so the refusal is a floor and not a blanket.

    A repair that refused every `min_securities` would pass both tests above. At
    `MINIMUM_REDUNDANCY_SECURITIES` the request must be *put* -- it then fails at exit 1 because
    this runtime directory holds no panel, which is a statement about the store and precisely the
    different row `test_factor_interfaces.py::
    test_a_request_that_cannot_be_put_is_a_different_row_on_every_face` keeps apart from `3`. The
    exit code is the assertion: `3` means "that question cannot be asked" and `1` means "it was
    asked and this store cannot answer".
    """
    admitted = cli_run(tmp_path / "runtime", min_securities=MINIMUM_REDUNDANCY_SECURITIES)
    assert admitted.exit_code == int(PanelExit.unhealthy) == 1, admitted.output
    assert "--min-securities" not in admitted.output
    assert "cannot be read" in admitted.output

    refused = cli_run(tmp_path / "runtime", min_securities=MINIMUM_REDUNDANCY_SECURITIES - 1)
    assert refused.exit_code == int(PanelExit.bad_request) == 3, refused.output


def test_a_value_under_the_ic_floor_is_still_refused_and_still_says_so(tmp_path: Path) -> None:
    """Below *both* floors the message must not become narrower than the request is wrong.

    `--min-securities 2` violates the IC floor as well as the redundancy one. The refusal is
    written against the higher floor, which is the correct remedy at any value below it -- so
    what this pins is that the message still names the option and still gives a number the caller
    can act on, rather than falling through to a second, thinner branch.
    """
    result = cli_run(tmp_path / "runtime", min_securities=MINIMUM_IC_SECURITIES - 1)

    assert result.exit_code == int(PanelExit.bad_request) == 3, result.output
    assert "--min-securities" in result.output
    assert str(MINIMUM_REDUNDANCY_SECURITIES) in result.output
