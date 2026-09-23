import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.replay import ReplayCorpus, ReplayRunner
from openalpha_cn.domain.evidence import LookAheadViolationError
from openalpha_cn.storage.migrations import read_status, run_migrations
from openalpha_cn.storage.sqlite import SQLiteRunRepository
from openalpha_cn.storage.validation import SQLiteValidationStore

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "replay" / "a-share-v1-corpus.json"


def test_frozen_corpus_runs_300_events_across_60_trading_days(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    corpus = ReplayCorpus.load(CORPUS_PATH)

    assert corpus.schema_version == "openalpha-replay-corpus/v1"
    assert len(corpus.trading_days) == 60
    assert len(corpus.cases) == 300

    state_path = tmp_path / "replay.sqlite3"
    validation_db_path = tmp_path / "state.sqlite3"
    run_migrations(validation_db_path, clock=migration_clock)
    validation_store = SQLiteValidationStore(validation_db_path)

    report = ReplayRunner(
        code_commit="0123456789abcdef",
        config_digest="d" * 64,
        random_seed=7,
    ).run(
        corpus=corpus,
        state_path=state_path,
        validation_store=validation_store,
        clock=migration_clock,
    )

    assert report.total_cases == 300
    assert report.succeeded == 300
    assert report.deterministic_replays == 300
    assert report.success_rate == 1.0
    assert len(report.validation_ids) == 300

    # Finding 1 (P0.B acceptance review), end-to-end reviewer's exact reproduction: every
    # validation ID the frozen corpus produces must be retrievable through a real query
    # interface, not merely present in this in-memory report. Cross-reference the first
    # and last case's `run_id` through the now-migrated replay run repository to its
    # `decision_id`, then confirm the validation store returns a result carrying one of
    # `report.validation_ids` for it.
    repository = SQLiteRunRepository(state_path)
    for case in (corpus.cases[0], corpus.cases[-1]):
        decision = repository.get_decision_for_run(case.run_id)
        assert decision is not None
        by_decision = validation_store.list_by_decision(decision.decision_id)
        assert len(by_decision) == 1
        assert by_decision[0].validation_id in report.validation_ids

    # The replay database itself is migrated -- not permanently stuck at `user_version = 0`
    # (Finding 1's first half, verified by the acceptance reviewer against the pre-fix code).
    assert read_status(state_path).current_version != 0

    # `report.look_ahead_violations == 0` used to be asserted here, and it could not fail: a case
    # whose evidence is not yet visible at its own `as_of` is refused by
    # `ReplayCase.validate_point_in_time` while the corpus loads, before the runner sees it, so
    # the count is 0 for every corpus that reaches `run` (the field's docstring in
    # backtest/replay.py). What README.md:43 and docs/backtest/a-share-execution.zh-CN.md rely
    # on is that refusal, so this corpus is checked for the refusal instead: move one case's
    # clock to a minute before its evidence became visible, and the same corpus no longer loads.
    document = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    tampered = document["cases"][0]
    visible_from = datetime.fromisoformat(tampered["evidence"][0]["timeline"]["available_time"])
    tampered["as_of"] = (visible_from - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValidationError) as refused:
        ReplayCorpus.model_validate(document)
    assert any(
        isinstance(error.get("ctx", {}).get("error"), LookAheadViolationError)
        for error in refused.value.errors()
    )
