"""Configuration consulted by pure oracle comparisons."""

from dataclasses import dataclass

from sieve_tool.common.config import CommonConfig, ControllerConfig


@dataclass(frozen=True)
class ComparisonPolicy:
    workload: str
    common: CommonConfig
    controller: ControllerConfig
