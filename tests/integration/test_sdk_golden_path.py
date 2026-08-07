import json
from datetime import UTC, datetime
from pathlib import Path

from openalpha_cn.agents.baseline import CapitalAgent
from openalpha_cn.providers.base import ProviderMetadata
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.sdk import OpenAlphaSDK

NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)


def test_sdk_imports_evidence_runs_research_and_queries_state(tmp_path: Path) -> None:
    source = tmp_path / "events.json"
    source.write_text(
        json.dumps(
            [
                {
                    "subject": "000001.SZ",
                    "kind": "limit_up",
                    "event_time": NOW.isoformat(),
                    "available_time": NOW.isoformat(),
                    "ingested_time": NOW.isoformat(),
                    "revision_time": NOW.isoformat(),
                    "summary": "Synthetic limit-up.",
                    "payload": {
                        "close": 10.5,
                        "pct_change": 9.99,
                        "board_count": 1,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    metadata = ProviderMetadata(
        provider_id="user.file",
        display_name="User file",
        source_license="user-supplied",
        redistribution="restricted",
        credential_env_vars=(),
        caching_policy="local-permitted",
        rate_limit="not-applicable",
        freshness="defined-by-input",
        failure_semantics="Invalid inputs fail explicitly.",
    )
    sdk = OpenAlphaSDK(runtime_dir=tmp_path / "runtime", clock=lambda: NOW)

    evidence = sdk.build_file_evidence(path=source, as_of=NOW, metadata=metadata)
    research = sdk.run_research(
        ResearchRunRequest(
            run_id="sdk-golden-run",
            mode="live",
            subject="000001.SZ",
            as_of=NOW,
            evidence=evidence,
            code_commit="0123456789abcdef",
            config_digest="f" * 64,
            random_seed=7,
        )
    )

    assert research.decision.final_action == "watch"
    assert sdk.query_evidence(as_of=NOW, subject="000001.SZ") == evidence
    assert sdk.health() == {"status": "ok", "version": "1.0.0"}

    reopened = OpenAlphaSDK(runtime_dir=tmp_path / "runtime", clock=lambda: NOW)
    memory = reopened.list_memory(subject="000001.SZ")
    recovery = reopened.get_recovery("sdk-golden-run")

    assert len(memory) == 1
    assert memory[0].decision_id == research.decision.decision_id
    assert recovery is not None
    assert recovery.status == "succeeded"
    assert recovery.next_agent_index == 1

    custom = OpenAlphaSDK(
        runtime_dir=tmp_path / "custom-runtime",
        clock=lambda: NOW,
        agents=(CapitalAgent(),),
    )
    custom_result = custom.run_research(
        ResearchRunRequest(
            run_id="sdk-custom-agent-run",
            mode="live",
            subject="000001.SZ",
            as_of=NOW,
            evidence=evidence,
            code_commit="0123456789abcdef",
            config_digest="a" * 64,
            random_seed=7,
        )
    )
    assert custom_result.signal.direction == "abstain"
    assert custom_result.agent_results == ()
