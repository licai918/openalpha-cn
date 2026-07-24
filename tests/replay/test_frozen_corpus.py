from pathlib import Path

from openalpha_cn.backtest.replay import ReplayCorpus, ReplayRunner

ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "replay" / "a-share-v1-corpus.json"


def test_frozen_corpus_runs_300_events_across_60_trading_days(tmp_path: Path) -> None:
    corpus = ReplayCorpus.load(CORPUS_PATH)

    assert corpus.schema_version == "openalpha-replay-corpus/v1"
    assert len(corpus.trading_days) == 60
    assert len(corpus.cases) == 300

    report = ReplayRunner(
        code_commit="0123456789abcdef",
        config_digest="d" * 64,
        random_seed=7,
    ).run(corpus=corpus, state_path=tmp_path / "replay.sqlite3")

    assert report.total_cases == 300
    assert report.succeeded == 300
    assert report.deterministic_replays == 300
    assert report.look_ahead_violations == 0
    assert report.success_rate == 1.0
    assert len(report.validation_ids) == 300
