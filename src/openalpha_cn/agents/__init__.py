"""Research agent contracts and built-in roles."""

from openalpha_cn.agents.base import AgentContext, AgentResult, ResearchAgent
from openalpha_cn.agents.baseline import CapitalAgent, MarketAgent, ThemeAgent, baseline_agents

__all__ = [
    "AgentContext",
    "AgentResult",
    "CapitalAgent",
    "MarketAgent",
    "ResearchAgent",
    "ThemeAgent",
    "baseline_agents",
]
