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
    """Synchronously call an MCP tool; use only outside Agents SDK.

    Each item is a dict with:
    - type: "text" | "structured" | "unknown"
    - text: present when type == "text"
    - data: present when type == "structured"
    """

    try:
        return asyncio.run(call_tool_async(tool_name, arguments))
    except RuntimeError as exc:
        # If there's already a running loop, this function should not be used.
        raise RuntimeError(
            f"call_tool({tool_name}) cannot run inside an existing event loop; "
            "use call_tool_async from within Agents SDK tools."
        ) from exc
