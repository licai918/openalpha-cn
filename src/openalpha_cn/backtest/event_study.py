"""Event-study CAR, t-statistic, and deterministic bootstrap inference."""

import math
import random
import statistics

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventReturnWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    asset_returns: tuple[float, ...] = Field(min_length=1)
    benchmark_returns: tuple[float, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_lengths(self) -> "EventReturnWindow":
        if len(self.asset_returns) != len(self.benchmark_returns):
            raise ValueError("asset and benchmark windows must have equal length")
        return self


class EventStudyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    windows: tuple[EventReturnWindow, ...] = Field(min_length=2)
    bootstrap_samples: int = Field(default=1000, ge=100, le=100_000)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)
    random_seed: int = 0


class EventCAR(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    car: float


class EventStudyReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_size: int
    events: tuple[EventCAR, ...]
    mean_car: float
    standard_error: float
    t_statistic: float
    bootstrap_lower: float
    bootstrap_upper: float
    confidence_level: float
    bootstrap_samples: int
    random_seed: int


class EventStudy:
    """Compute transparent inference without a heavyweight statistics dependency."""

    def analyze(self, request: EventStudyRequest) -> EventStudyReport:
        cars = [
            sum(
                asset - benchmark
                for asset, benchmark in zip(
                    window.asset_returns, window.benchmark_returns, strict=True
                )
            )
            for window in request.windows
        ]
        mean_car = statistics.fmean(cars)
        standard_error = statistics.stdev(cars) / math.sqrt(len(cars))
        t_statistic = math.inf if standard_error == 0 else mean_car / standard_error
        generator = random.Random(request.random_seed)
        means = sorted(
            statistics.fmean(generator.choice(cars) for _item in cars)
            for _sample in range(request.bootstrap_samples)
        )
        alpha = 1 - request.confidence_level
        lower_index = int((alpha / 2) * (len(means) - 1))
        upper_index = int((1 - alpha / 2) * (len(means) - 1))
        return EventStudyReport(
            sample_size=len(cars),
            events=tuple(
                EventCAR(event_id=window.event_id, car=car)
                for window, car in zip(request.windows, cars, strict=True)
            ),
            mean_car=mean_car,
            standard_error=standard_error,
            t_statistic=t_statistic,
            bootstrap_lower=means[lower_index],
            bootstrap_upper=means[upper_index],
            confidence_level=request.confidence_level,
            bootstrap_samples=request.bootstrap_samples,
            random_seed=request.random_seed,
        )
