"""Thin MCP client helpers for calling n8n MCP tools from Python.

These helpers use MCPServerStreamableHttp so that the app container
can talk to the n8n MCP Server Trigger endpoint inside the VPN. The
agents see these as regular Python tools.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from agents.mcp.server import MCPServerStreamableHttp, MCPServerStreamableHttpParams

from rv_agentic.config.settings import get_settings


logger = logging.getLogger(__name__)

# CRITICAL FIX for Streamlit: Ensure all threads can create event loops
# Streamlit's ScriptRunner threads don't have event loop policies by default
try:
    asyncio.get_event_loop_policy()
except RuntimeError:
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())


def _get_mcp_url() -> str:
    settings = get_settings()
    if not settings.n8n_mcp_server_url:
        raise RuntimeError("N8N_MCP_SERVER_URL is not configured")
    return settings.n8n_mcp_server_url


async def call_tool_async(tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Async helper to call a single MCP tool and normalize the result.

    This is intended for use inside Agents SDK tools, which already run
    inside an event loop.
    """

    url = _get_mcp_url()
    # Allow long-running tools (1–2 minutes) to complete.
    params: MCPServerStreamableHttpParams = {
        "url": url,
        # Individual HTTP calls may take up to ~2 minutes when crawling
        # or enriching; keep the client-side timeout aligned with that.
        "timeout": 120.0,
        # Keep the SSE stream open long enough for multi-step tools.
        "sse_read_timeout": 600.0,
    }
    logger.info("MCP call start: tool=%s url=%s args=%s", tool_name, url, arguments)
    items: List[Dict[str, Any]] = []
    async with MCPServerStreamableHttp(
        name="n8n",
        params=params,
        cache_tools_list=True,
        # Critical: increase client session timeout beyond the 5s default
        # so that long-running MCP workflows are not cut off by the client.
        client_session_timeout_seconds=600.0,
    ) as server:
        result = await server.call_tool(tool_name, arguments=arguments)
        for content in result.content:
            t = getattr(content, "type", None)
            if t == "text":
                items.append({"type": "text", "text": content.text})
            elif t == "structured":
                items.append({"type": "structured", "data": content.data})
            else:
                items.append({"type": t or "unknown"})
    logger.info("MCP call complete: tool=%s items=%d", tool_name, len(items))
    return items


def call_tool(tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Synchronously call an MCP tool; works both inside and outside Agents SDK.

    Each item is a dict with:
    - type: "text" | "structured" | "unknown"
    - text: present when type == "text"
    - data: present when type == "structured"
    """

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

    # Now run the async function
    try:
        return loop.run_until_complete(call_tool_async(tool_name, arguments))
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"call_tool({tool_name}) failed with traceback:\n{tb}")
        raise RuntimeError(
            f"call_tool({tool_name}) failed to execute: {exc}"
        ) from exc
