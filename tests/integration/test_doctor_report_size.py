"""`V2-P4-110`: `panel doctor --json` is mostly static prose, and there was no way to decline it.

Measured on `daaabf5` against a generated panel, `--dataset index_daily --year 2026`:

    stdout 16,936 bytes total
      limitations       14,359 bytes   (84.8%)   10 entries
      findings           1,340 bytes
      cross_checks         536 bytes
      datasets             507 bytes
      counts_by_severity    42 bytes
      as_of                 27 bytes
      blocked_datasets      15 bytes
      is_clean               5 bytes

## What the acceptance said, and what the measurement says instead

The report was filed as "most of it is full registry limitation prose **unrelated to the dataset
asked about** -- ask about one dataset, receive the whole ledger". The second half does not
survive measurement and is recorded here because the fix would have been the wrong one.
`panel_doctor.known_limitations` already selects `wanted & set(item.datasets)`, so of the ten
entries returned for `index_daily`, **four** are `KNOWN_INDEX_PRICE_LIMITATIONS` -- exactly that
dataset's own -- and the other six are `storage_limitations()`, which name no dataset because
they are true of every dataset alike and are added by `panel_health_report` on purpose. Asking
about `daily` instead returns a different twelve; asking about three datasets returns
twenty-three. The ledger is scoped.

What is true is the first half, and it is the whole of the defect: **84.8% of the answer is prose
that does not depend on the panel at all.** It is byte-identical on a healthy panel and a broken
one, on the first run and the thousandth, and a caller who wants to know what is wrong with their
store has no way to ask without it -- while the *text* face has reduced it to a count since it
was written, deliberately, with the reason in `_echo_report`: "a human report that buried its own
findings under them would teach its readers to skim both".

## What is fixed

`--limitation-detail/--no-limitation-detail` on the CLI and `?limitation_detail=` on the REST
route. The default is unchanged, which is a decision rather than caution: the prose is the
answer to "what can this dataset never tell me", the registries exist to be read, and a default
that dropped it would make the audit trail opt-in. What changes is that declining it is possible
and costs one flag.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, generate_panel, write_generated_panel
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import known_limitations, storage_limitations

AS_OF: Final[str] = "2026-01-16T07:00:00+00:00"

LIMITATION_KEYS: Final[frozenset[str]] = frozenset({"code", "datasets", "detail", "dates"})
"""Every key a limitation carries in the full payload, so the trimmed one is a strict subset.

Named rather than inlined because the assertion that matters is which key `--no-limitation-detail`
removes and which three it keeps: a caller who declines the prose must still learn *which*
limitations apply, or the flag turns a large honest answer into a small dishonest one.
"""


@pytest.fixture
def panel(tmp_path: Path) -> Path:
    write_generated_panel(PanelStore(tmp_path / "panel"), generate_panel())
    return tmp_path


def _argv(runtime_dir: Path, *datasets: str, extra: tuple[str, ...] = ()) -> list[str]:
    flags = ["panel", "doctor", "--year", "2026", "--as-of", AS_OF, "--exchange", EXCHANGE]
    for dataset in datasets:
        flags += ["--dataset", dataset]
    return [*flags, "--json", "--runtime-dir", str(runtime_dir), *extra]


def _payload(runtime_dir: Path, *datasets: str, extra: tuple[str, ...] = ()) -> dict[str, Any]:
    result = CliRunner().invoke(app, _argv(runtime_dir, *datasets, extra=extra))
    assert result.stdout.strip(), result.stderr
    return dict(json.loads(result.stdout))


def test_the_ledger_this_command_returns_is_already_scoped_to_the_datasets_asked_about(
    panel: Path,
) -> None:
    """The half of the report that measurement falsified, held so it is not "fixed" again.

    Asking about one dataset returns that dataset's own limitations plus the storage plane's,
    and asking about three returns three datasets' worth. Compared against
    `known_limitations`/`storage_limitations` directly rather than against a count, because a
    count assertion passes on a payload that returned the *wrong* ten entries.
    """
    one = _payload(panel, "index_daily")
    three = _payload(panel, "index_daily", "daily", "stock_basic")

    expected = {item.code for item in known_limitations(("index_daily",))} | {
        item.code for item in storage_limitations()
    }
    assert {entry["code"] for entry in one["limitations"]} == expected
    assert len(three["limitations"]) > len(one["limitations"])
    assert {entry["code"] for entry in one["limitations"]} < {
        entry["code"] for entry in three["limitations"]
    }


def test_the_prose_is_most_of_the_answer_and_can_now_be_declined(panel: Path) -> None:
    """`V2-P4-110`'s actual defect: 84.8% of the bytes do not depend on the panel.

    Both payloads are taken from the same store at the same instant, so the only difference is
    the flag. The findings are asserted equal across the two because a flag that shrank the
    report by dropping a *finding* would be a far worse answer than the one it replaced.
    """
    full = _payload(panel, "index_daily")
    trimmed = _payload(panel, "index_daily", extra=("--no-limitation-detail",))

    assert full["findings"] == trimmed["findings"]
    assert full["datasets"] == trimmed["datasets"]
    assert [entry["code"] for entry in full["limitations"]] == [
        entry["code"] for entry in trimmed["limitations"]
    ]

    full_bytes = len(json.dumps(full).encode())
    trimmed_bytes = len(json.dumps(trimmed).encode())
    assert trimmed_bytes * 3 < full_bytes, (full_bytes, trimmed_bytes)


def test_declining_the_prose_still_says_which_limitations_apply(panel: Path) -> None:
    """The trimmed entry is a strict subset of the full one, not a different thing.

    A caller who declines the detail keeps `code`, `datasets` and `dates` -- enough to look the
    entry up and enough to know a limitation with dates has fired on specific days. Only the
    paragraph goes. Asserted as a set relation rather than by naming three keys, so a key added
    to the payload later has to be classified deliberately.
    """
    trimmed = _payload(panel, "index_daily", extra=("--no-limitation-detail",))
    full = _payload(panel, "index_daily")

    assert set(full["limitations"][0]) == LIMITATION_KEYS
    for entry in trimmed["limitations"]:
        assert set(entry) == LIMITATION_KEYS - {"detail"}


def test_the_default_is_unchanged_so_no_reader_of_this_payload_loses_anything(
    panel: Path,
) -> None:
    """The decision recorded as a test: declining is opt-in, and the audit trail stays the default.

    A registry that is only served when asked for is a registry that stops being read. The
    complaint was that there was no way to decline it, not that it should be gone.
    """
    default = _payload(panel, "index_daily")

    assert all("detail" in entry for entry in default["limitations"])
    assert all(entry["detail"] for entry in default["limitations"])


def test_the_rest_face_takes_the_same_decision_through_a_query_parameter(panel: Path) -> None:
    """Three faces, one answer -- the parity this repository keeps re-proving.

    The CLI payload and the REST body are compared to each other rather than each to a literal:
    two independent assertions can both pass on two payloads that disagree, which is what the
    cross-face parity rows in this roadmap keep finding.
    """
    client = TestClient(create_app(runtime_dir=panel))
    query = {
        "dataset": ["index_daily"],
        "year": [2026],
        "as_of": AS_OF,
        "exchange": EXCHANGE,
        "calendar": "true",
        "limitation_detail": "false",
    }

    response = client.get("/api/v1/panel/health", params=query)

    assert response.status_code in (200, 409)
    assert response.json() == _payload(panel, "index_daily", extra=("--no-limitation-detail",))


def test_the_text_face_names_the_flag_that_answers_the_question_it_declines_to(
    panel: Path,
) -> None:
    """`_echo_report` reduced the limitations to a count long before this row, and said so.

    Its comment gives the reason -- "a human report that buried its own findings under them
    would teach its readers to skim both" -- and its line told the reader `--json carries them
    in full`, which was the only other option there was. Now there are two, and the line that
    points at them has to name both or the flag is undiscoverable.
    """
    result = CliRunner().invoke(
        app, [flag for flag in _argv(panel, "index_daily") if flag != "--json"]
    )

    assert "--json" in result.stdout
    assert "--no-limitation-detail" in result.stdout
