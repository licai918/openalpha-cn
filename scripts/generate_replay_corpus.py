"""Generate the deterministic 60-day / 300-event synthetic replay corpus."""

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

KINDS = (
    "limit_up",
    "broken_board",
    "consecutive_board",
    "disclosure",
    "theme",
    "catalyst",
    "capital",
)


def trading_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def facts(kind: str, index: int) -> dict[str, Any]:
    if kind == "limit_up":
        return {"close": 10.0 + index / 100, "pct_change": 10.0, "board_count": 1}
    if kind == "broken_board":
        return {"high": 11.0, "close": 10.2, "open_count": 2}
    if kind == "consecutive_board":
        return {"close": 12.0, "pct_change": 10.0, "board_count": 2 + index % 3}
    if kind == "disclosure":
        return {
            "announcement_id": f"ann-{index:03d}",
            "title": "Synthetic disclosure",
            "category": "periodic",
        }
    if kind == "theme":
        return {"theme": f"synthetic-theme-{index % 5}", "score": 0.65 + index % 3 / 10}
    if kind == "catalyst":
        return {"headline": "Synthetic catalyst", "catalyst_type": "policy"}
    return {"net_inflow": 1_000_000 + index * 1000, "unit": "CNY"}


def family(kind: str) -> str:
    if kind in {"limit_up", "broken_board", "consecutive_board"}:
        return "market_event"
    return kind


def build() -> dict[str, Any]:
    days = trading_days(date(2026, 1, 5), 60)
    cases: list[dict[str, Any]] = []
    for day_index, trading_day in enumerate(days):
        for slot in range(5):
            index = day_index * 5 + slot
            kind = KINDS[index % len(KINDS)]
            subject = f"{index + 1:06d}.{'SZ' if index % 2 == 0 else 'SH'}"
            event_time = datetime.combine(trading_day, time(7, 0), tzinfo=UTC)
            available_time = datetime.combine(trading_day, time(8, 0), tzinfo=UTC)
            as_of = datetime.combine(trading_day, time(8, 30), tzinfo=UTC)
            outcome_end = as_of + timedelta(days=5)
            cases.append(
                {
                    "run_id": f"replay-{trading_day:%Y%m%d}-{slot}",
                    "trading_day": trading_day.isoformat(),
                    "subject": subject,
                    "as_of": as_of.isoformat(),
                    "evidence": [
                        {
                            "subject": subject,
                            "kind": kind,
                            "timeline": {
                                "event_time": event_time.isoformat(),
                                "available_time": available_time.isoformat(),
                                "ingested_time": (
                                    available_time + timedelta(minutes=1)
                                ).isoformat(),
                                "revision_time": available_time.isoformat(),
                            },
                            "source_id": "synthetic.replay",
                            "source_uri": f"fixture://replay/{index:03d}",
                            "source_license": "CC0-1.0",
                            "redistribution": "allowed",
                            "summary": f"Synthetic {kind} replay event {index}.",
                            "payload": {
                                "schema": "a-share-evidence/v1",
                                "family": family(kind),
                                "facts": facts(kind, index),
                                "quality_flags": [],
                            },
                        }
                    ],
                    "outcome": {
                        "observation_start": as_of.isoformat(),
                        "observation_end": outcome_end.isoformat(),
                        "start_price": 10.0,
                        "end_price": 9.8 if kind == "broken_board" else 10.2,
                        "benchmark_return": 0.001,
                        "transaction_cost": 0.001,
                        "data_quality_notes": ["Synthetic frozen replay outcome."],
                    },
                }
            )
    return {
        "schema_version": "openalpha-replay-corpus/v1",
        "trading_days": [item.isoformat() for item in days],
        "cases": cases,
    }


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    target = repository_root / "tests" / "fixtures" / "replay" / "a-share-v1-corpus.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
