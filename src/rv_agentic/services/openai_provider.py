"""OpenAI client and Agents SDK integration helpers."""

from __future__ import annotations

from typing import Any

from agents import Agent, Runner
from openai import OpenAI

from rv_agentic.config.settings import get_settings


def get_openai_client() -> OpenAI:
    """Return a configured OpenAI client using Settings.

    Centralizing this makes it easy to rotate keys or inject
    tracing/observability later.
    """

    settings = get_settings()
    return OpenAI(api_key=settings.openai_api_key)


def run_agent_sync(agent: Agent, input_text: str, **kwargs: Any) -> Any:
    """Synchronous helper around ``Runner.run_sync``.

    The Streamlit UI uses this wrapper to talk to Agents.

    For MCP-backed tools we rely on HostedMCPTool instances that
    are already attached to the agent, so no extra wiring is
    required here.
    """

    return Runner.run_sync(agent, input_text, **kwargs)

