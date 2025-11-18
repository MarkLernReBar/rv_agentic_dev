"""MCP-backed tools surfaced via n8n for the Agents SDK.

These tools are simple Python callables that an Agent can invoke.
They delegate to n8n HTTP workflows, which in turn talk to MCP
servers to perform web search, fetching, or other external actions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from rv_agentic.config.settings import get_settings


class CompanySearchParams(BaseModel):
    """Parameters for company search via MCP/n8n."""

    query: str = Field(..., description="Natural language query describing target companies")
    limit: int = Field(20, description="Maximum number of companies to return")


class ContactSearchParams(BaseModel):
    """Parameters for contact search via MCP/n8n."""

    company_domain: Optional[str] = Field(
        None,
        description="Domain of the company whose contacts to search",
    )
    role_keywords: Optional[str] = Field(
        None,
        description="Role/title keywords (e.g. 'owner, principal, director of operations')",
    )
    limit: int = Field(10, description="Maximum number of contacts to return")


def _post_to_n8n(path: str, payload: Dict[str, Any]) -> Any:
    """POST helper to call the configured n8n MCP proxy.

    Assumes n8n exposes MCP-like tools via HTTP JSON endpoints.
    """

    settings = get_settings()
    if not settings.n8n_mcp_base_url:
        raise RuntimeError("N8N_MCP_BASE_URL is not configured")

    url = settings.n8n_mcp_base_url.rstrip("/") + "/" + path.lstrip("/")
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def mcp_search_companies(params: CompanySearchParams) -> List[Dict[str, Any]]:
    """Search for companies via MCP tools orchestrated by n8n.

    Agents can call this to perform web/data discovery without
    relying on the built-in OpenAI web_search tool.
    """

    data = _post_to_n8n("mcp/search_companies", payload=params.dict())
    results = data.get("results") if isinstance(data, dict) else data
    return results or []


def mcp_search_contacts(params: ContactSearchParams) -> List[Dict[str, Any]]:
    """Search for contacts / decision makers via MCP tools."""

    data = _post_to_n8n("mcp/search_contacts", payload=params.dict())
    results = data.get("results") if isinstance(data, dict) else data
    return results or []


