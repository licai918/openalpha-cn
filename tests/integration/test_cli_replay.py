"""`openalpha replay run`'s `--code-commit`/`--config-digest` defaults (V2-P0B-009).

`ReplayReport` (the command's stdout payload) carries no `code_commit`/`config_digest`
field of its own -- it is an aggregate report, not a per-case manifest -- so unlike
`research run` (see `test_cli_research.py`), the only way to observe what value
actually reached the deterministic core is to intercept it at the point `cli.py`'s
resolved values are handed to `ReplayRunner.__init__` (the sole place `sdk.replay()`
constructs one). This still runs the real CLI command end to end; only the constructor
call is spied on, and the real `__init__` still runs afterward.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openalpha_cn.backtest.replay import ReplayRunner
from openalpha_cn.cli import app

runner = CliRunner()


def test_replay_run_without_explicit_flags_resolves_a_real_code_commit_and_config_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps({"schema_version": "replay-corpus/v1", "trading_days": [], "cases": []}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    original_init = ReplayRunner.__init__

    def _capture_init(
        self: ReplayRunner, *, code_commit: str, config_digest: str, random_seed: int
    ) -> None:
        captured["code_commit"] = code_commit
        captured["config_digest"] = config_digest
        captured["random_seed"] = random_seed
        original_init(
            self, code_commit=code_commit, config_digest=config_digest, random_seed=random_seed
        )

    monkeypatch.setattr(ReplayRunner, "__init__", _capture_init)

    result = runner.invoke(
        app,
        [
            "replay",
            "run",
            str(corpus_path),
            "--runtime-dir",
            str(tmp_path / "runtime"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["code_commit"] != "development"
    assert captured["config_digest"] != "0" * 64
    assert len(str(captured["config_digest"])) == 64
    assert captured["random_seed"] == 7


def test_replay_run_still_accepts_explicit_flags_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(
        json.dumps({"schema_version": "replay-corpus/v1", "trading_days": [], "cases": []}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    original_init = ReplayRunner.__init__

    def _capture_init(
        self: ReplayRunner, *, code_commit: str, config_digest: str, random_seed: int
    ) -> None:
        captured["code_commit"] = code_commit
        captured["config_digest"] = config_digest
        original_init(
            self, code_commit=code_commit, config_digest=config_digest, random_seed=random_seed
        )

    monkeypatch.setattr(ReplayRunner, "__init__", _capture_init)

    result = runner.invoke(
        app,
        [
            "replay",
            "run",
            str(corpus_path),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--code-commit",
            "0123456789abcdef",
            "--config-digest",
            "a" * 64,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured["code_commit"] == "0123456789abcdef"
    assert captured["config_digest"] == "a" * 64
