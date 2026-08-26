"""`V2-P5-022` on the product surfaces: REST, the SDK and `openalpha report export`.

`tests/unit/product/test_report_export.py` measures the licence rule against a function.
This file is the other half this repository insists on -- four acceptances found the same root
cause, a green unit test calling the library directly and no green product path -- so every
claim below starts at a `CliRunner`, a `TestClient` over a real `create_app`, or an
`OpenAlphaSDK`, over a runtime directory built by real commands.

The fixture is a **restricted** provider on purpose. All three shipped adapters declare
`redistribution="restricted"` (`providers/tushare.py:3422`, `akshare.py:48`,
`chainlin.py:124`), so the ordinary state of a real runtime directory is that nothing may be
redistributed -- and an export that quietly published everything would look identical to a
correct one on an `allowed` fixture. The second evidence item is `allowed`, because a gate that
withholds unconditionally passes every test a restricted-only fixture can write while being
just as wrong.

Every `create_app` here is given an explicit `runtime_dir` under `tmp_path`. `create_app()` with
no argument initialises storage in the repository's own `runtime/`, which is not a thing a test
may do.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.cli import app
from openalpha_cn.sdk import OpenAlphaSDK

runner = CliRunner()

RAW_MARKER: Final[str] = "TUSHARE-RAW-ROW-MARKER"
"""A value that exists only inside the restricted provider's payload.

`_LimitUpFacts` is `extra="allow"`, so an extra key survives normalisation verbatim into
`payload["facts"]` -- which is what makes "the provider's bytes are not in the artifact"
assertable over the serialised export instead of over one named field.
"""

SUBJECT: Final[str] = "600519.SH"
"""One subject for both items.

`ResearchRunRequest` refuses a run whose evidence spans subjects ("all evidence must match the
requested subject", measured -- the first version of this fixture used two stocks and exited
`1`), and that is the right refusal: a decision is about one security. So the two licences
arrive as two *providers* about the same stock, which is also what actually happens -- a
restricted market feed and a permitted file the analyst owns.
"""

RESTRICTED_KIND: Final[str] = "limit_up"
PERMITTED_KIND: Final[str] = "catalyst"

_FACTS: Final[dict[str, dict[str, Any]]] = {
    RESTRICTED_KIND: {"close": 10.5, "pct_change": 9.99, "board_count": 1},
    PERMITTED_KIND: {"headline": "analyst note", "catalyst_type": "research"},
}


def _source(path: Path, *, kind: str, marker: str, base: datetime) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "subject": SUBJECT,
                    "kind": kind,
                    "event_time": (base - timedelta(hours=3)).isoformat(),
                    "available_time": (base - timedelta(hours=2)).isoformat(),
                    "ingested_time": (base - timedelta(hours=2)).isoformat(),
                    "revision_time": (base - timedelta(hours=2)).isoformat(),
                    "source_uri": f"fixture://{kind}/{SUBJECT}",
                    "summary": f"Synthetic {kind} for {SUBJECT}.",
                    "payload": {**_FACTS[kind], "upstream_row_id": marker},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _cli(*arguments: str) -> tuple[int, str]:
    """Exit code and stdout. `result.output` is passed to `assert` messages by the
    callers, because a refusal here goes to stderr and an empty stdout says nothing about
    why."""
    result = runner.invoke(app, list(arguments))
    return result.exit_code, result.stdout if result.exit_code == 0 else result.output


@pytest.fixture(name="exported")
def _exported(tmp_path: Path, frozen_now: datetime) -> tuple[Path, str]:
    """A runtime directory holding one report over two differently-licensed evidence items.

    Built with `openalpha evidence build`, `openalpha research run` and `POST /api/v1/reports`
    rather than by writing rows: what is under test is whether a licence survives the whole way
    from `ProviderMetadata` to a shareable artifact, and a hand-written snapshot would skip the
    half of that journey where the licence is attached.
    """
    runtime = tmp_path / "runtime"
    items: list[dict[str, Any]] = []

    for kind, licence, term, marker in (
        (RESTRICTED_KIND, "tushare-terms", "restricted", RAW_MARKER),
        (PERMITTED_KIND, "CC0-1.0", "allowed", "PUBLIC-ROW-ID"),
    ):
        source = tmp_path / f"{kind}.json"
        _source(source, kind=kind, marker=marker, base=frozen_now)
        code, out = _cli(
            "evidence",
            "build",
            str(source),
            "--as-of",
            frozen_now.isoformat(),
            "--runtime-dir",
            str(runtime),
            "--source-id",
            "tushare" if term == "restricted" else "user.file",
            "--source-license",
            licence,
            "--redistribution",
            term,
        )
        assert code == 0, out
        items.extend(json.loads(out)["items"])

    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")

    code, out = _cli(
        "research",
        "run",
        str(evidence_file),
        "--runtime-dir",
        str(runtime),
        "--subject",
        SUBJECT,
        "--as-of",
        frozen_now.isoformat(),
        "--run-id",
        "run-export",
    )
    assert code == 0, out
    research = json.loads(out)

    with TestClient(create_app(runtime_dir=runtime)) as client:
        created = client.post("/api/v1/reports", json={"research": research})
    assert created.status_code == 200, created.text
    return runtime, created.json()["report_id"]


def test_the_three_faces_render_one_export(exported: tuple[Path, str]) -> None:
    """REST, the SDK and the CLI, byte-for-byte.

    The equality is the point: an export is the artifact a user hands to somebody else, so a
    face that produced a *different* one would be a second licence decision nobody reviewed.
    `test_surface_parity.py` proves all three faces exist; this proves they agree.
    """
    runtime, report_id = exported

    with TestClient(create_app(runtime_dir=runtime)) as client:
        rest = client.get(f"/api/v1/reports/{report_id}/export")
    assert rest.status_code == 200, rest.text

    sdk_export = OpenAlphaSDK(runtime_dir=runtime).export_report(report_id)
    assert sdk_export is not None

    code, out = _cli("report", "export", report_id, "--runtime-dir", str(runtime))
    assert code == 0, out

    assert rest.json() == json.loads(sdk_export.model_dump_json())
    assert rest.json() == json.loads(out)


def test_no_restricted_payload_reaches_any_of_the_three(exported: tuple[Path, str]) -> None:
    """PRD Decision 27, asserted over the bytes each face actually emits.

    Not "the `payload` key is missing" but "the upstream row's value is not in the artifact",
    so a copy smuggled into a summary or a debug field fails here too.
    """
    runtime, report_id = exported

    with TestClient(create_app(runtime_dir=runtime)) as client:
        rest = client.get(f"/api/v1/reports/{report_id}/export")
    sdk_export = OpenAlphaSDK(runtime_dir=runtime).export_report(report_id)
    assert sdk_export is not None
    _, cli_out = _cli("report", "export", report_id, "--runtime-dir", str(runtime))

    assert RAW_MARKER not in rest.text
    assert RAW_MARKER not in sdk_export.model_dump_json()
    assert RAW_MARKER not in cli_out


def test_the_restricted_item_is_named_rather_than_dropped(exported: tuple[Path, str]) -> None:
    """The withheld record still says whose licence withheld it, through the real REST face."""
    runtime, report_id = exported

    with TestClient(create_app(runtime_dir=runtime)) as client:
        body = client.get(f"/api/v1/reports/{report_id}/export").json()

    withheld = [item for item in body["evidence"] if item["body"]["disposition"] == "withheld"]
    assert [item["kind"] for item in withheld] == [RESTRICTED_KIND]
    assert withheld[0]["source_id"] == "tushare"
    assert withheld[0]["body"]["source_license"] == "tushare-terms"
    assert withheld[0]["body"]["redistribution"] == "restricted"
    assert withheld[0]["summary"]


def test_a_permitted_payload_still_travels(exported: tuple[Path, str]) -> None:
    """The half a blanket refusal would also pass.

    Without this, `export_report` could withhold everything unconditionally and every other
    assertion in this file would stay green -- the gate would be indistinguishable from a
    deletion.
    """
    runtime, report_id = exported

    with TestClient(create_app(runtime_dir=runtime)) as client:
        body = client.get(f"/api/v1/reports/{report_id}/export").json()

    included = [item for item in body["evidence"] if item["body"]["disposition"] == "included"]
    assert [item["kind"] for item in included] == [PERMITTED_KIND]
    assert included[0]["body"]["payload"]["facts"]["upstream_row_id"] == "PUBLIC-ROW-ID"
    assert body["included_count"] == 1
    assert body["withheld_count"] == 1


def test_an_unknown_report_is_refused_the_same_way_on_two_faces(tmp_path: Path) -> None:
    """`404` on REST and exit `1` on the CLI, rather than an empty export on either.

    An export with no evidence in it is a real and different answer -- a report whose citations
    the store can no longer produce -- so "no such report" must not render as one.
    """
    runtime = tmp_path / "runtime"

    with TestClient(create_app(runtime_dir=runtime)) as client:
        response = client.get("/api/v1/reports/rpt_nosuchreport/export")
    assert response.status_code == 404

    result = runner.invoke(
        app, ["report", "export", "rpt_nosuchreport", "--runtime-dir", str(runtime)]
    )
    assert result.exit_code == 1
    assert OpenAlphaSDK(runtime_dir=runtime).export_report("rpt_nosuchreport") is None
