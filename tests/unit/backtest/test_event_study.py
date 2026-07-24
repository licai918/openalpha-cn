from openalpha_cn.backtest.event_study import (
    EventReturnWindow,
    EventStudy,
    EventStudyRequest,
)


def test_event_study_reports_car_t_stat_and_deterministic_bootstrap_interval() -> None:
    request = EventStudyRequest(
        windows=(
            EventReturnWindow(
                event_id="e1",
                asset_returns=(0.03, 0.02),
                benchmark_returns=(0.01, 0.01),
            ),
            EventReturnWindow(
                event_id="e2",
                asset_returns=(0.02, 0.01),
                benchmark_returns=(0.01, 0.0),
            ),
            EventReturnWindow(
                event_id="e3",
                asset_returns=(0.04, 0.02),
                benchmark_returns=(0.01, 0.01),
            ),
        ),
        bootstrap_samples=500,
        confidence_level=0.95,
        random_seed=7,
    )

    first = EventStudy().analyze(request)
    second = EventStudy().analyze(request)

    assert first == second
    assert first.sample_size == 3
    assert first.mean_car > 0
    assert first.t_statistic > 0
    assert first.bootstrap_lower <= first.mean_car <= first.bootstrap_upper
    assert sum(item.car for item in first.events) / 3 == first.mean_car
