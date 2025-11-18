"""OpenAI client and Agents SDK integration helpers."""

from __future__ import annotations

import asyncio
from typing import Any

from agents import Agent, Runner
from openai import OpenAI
from openai.types.responses import ResponseTextDeltaEvent

from rv_agentic.config.settings import get_settings


def get_openai_client() -> OpenAI:
    """Return a configured OpenAI client using Settings.

    Centralizing this makes it easy to rotate keys or inject
    tracing/observability later.
    """

    settings = get_settings()
    return OpenAI(api_key=settings.openai_api_key)


def _ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Ensure the current thread has an event loop and return it."""

    # Ensure the thread has an event loop policy (critical for Streamlit threads)
    try:
        asyncio.get_event_loop_policy()
    except RuntimeError:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

    # Explicitly create and set an event loop for this thread if one doesn't exist
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = None
    except RuntimeError:
        loop = None

    if loop is None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop


def run_agent_sync(agent: Agent, input_text: str, **kwargs: Any) -> Any:
    """Synchronous helper around ``Runner.run_sync``.

    The Streamlit UI uses this wrapper to talk to Agents.

    For MCP-backed tools we rely on HostedMCPTool instances that
    are already attached to the agent, so no extra wiring is
    required here.

    CRITICAL FIX for Streamlit: Ensure the thread has an event loop
    before calling Runner.run_sync(), which needs it to execute async tools.
    """

    _ensure_event_loop()

    return Runner.run_sync(agent, input_text, **kwargs)


def run_agent_with_streaming(
    agent: Agent,
    input_text: str,
    stream_callback: callable,
    **kwargs: Any
) -> Any:
    """Synchronous helper that streams agent output via ``Runner.run_streamed``.

    This runs entirely on the Streamlit script thread so that the provided
    ``stream_callback`` can safely update UI elements (e.g., ``st.status`` and
    markdown containers) as text deltas arrive from the model.
    """

    loop = _ensure_event_loop()

    async def _run_streamed() -> Any:
        """Async inner helper that performs the streamed run."""

        import time as _time

        # Initial status hint before the streamed run starts.
        if stream_callback:
            stream_callback("🔍 Starting research process...")

        # Start a streamed run; the returned object exposes an async stream_events() API.
        result = Runner.run_streamed(agent, input_text, **kwargs)

        # Configure gentle, agent-specific fallback status messages so the UI
        # shows progress even if the model does not emit emoji-prefixed lines.
        name = getattr(agent, "name", "") or ""
        is_company = "Company" in name
        is_contact = "Contact" in name

        if is_company:
            status_messages = [
                "🔍 Checking HubSpot for existing company record...",
                "🌐 Searching NEO Research Database...",
                "📊 Analyzing company profile and web presence...",
                "🔧 Running MCP tools for enrichment...",
                "👥 Discovering decision makers...",
                "📋 Analyzing ICP fit and signals...",
                "✍️ Compiling ICP brief and outreach notes...",
            ]
        elif is_contact:
            status_messages = [
                "🔍 Searching HubSpot for contact records...",
                "🌐 Checking NEO Research Database for prior enrichment...",
                "👤 Resolving contact identity and role...",
                "📧 Verifying email addresses and reachability...",
                "🔗 Finding LinkedIn profiles and web presence...",
                "📋 Gathering personalization data points...",
                "✍️ Creating contact research briefing...",
            ]
        else:
            status_messages = [
                "🔧 Processing request...",
                "📊 Analyzing data...",
                "✍️ Generating response...",
            ]

        status_index = 0
        last_status_time = _time.time()
        status_gap_seconds = 3.0

        # If no callback was provided, just drain the stream to completion.
        if not stream_callback:
            async for _ in result.stream_events():
                continue
            return result

        async for event in result.stream_events():
            # We only care about raw text deltas here; higher-level events are ignored.
            if getattr(event, "type", None) == "raw_response_event":
                data = getattr(event, "data", None)
                if isinstance(data, ResponseTextDeltaEvent):
                    delta = getattr(data, "delta", None)
                    if isinstance(delta, str) and delta:
                        stream_callback(delta)

            # Periodically emit fallback status messages while the agent runs.
            now = _time.time()
            if (
                status_index < len(status_messages)
                and (now - last_status_time) >= status_gap_seconds
            ):
                stream_callback(status_messages[status_index])
                status_index += 1
                last_status_time = now

        # Final completion status message
        if stream_callback:
            stream_callback("✅ Research complete!")

        return result

    # Run the async inner helper to completion in this thread.
    return loop.run_until_complete(_run_streamed())
