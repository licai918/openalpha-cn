"""Research agent contracts and built-in roles."""

from openalpha_cn.agents.base import AgentContext, AgentResult, ResearchAgent
from openalpha_cn.agents.baseline import CapitalAgent, MarketAgent, ThemeAgent, baseline_agents
from openalpha_cn.agents.model import ModelProviderFailure, StructuredSignalAgent

__all__ = [
    "AgentContext",
    "AgentResult",
    "CapitalAgent",
    "MarketAgent",
    "ModelProviderFailure",
    "ResearchAgent",
    "StructuredSignalAgent",
    "ThemeAgent",
    "baseline_agents",
]
