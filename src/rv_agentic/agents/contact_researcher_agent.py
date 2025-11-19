"""Contact Researcher Agent implemented with the OpenAI Agents SDK.

This agent focuses on deep contact research for property management
professionals, combining MCP-backed web tools with HubSpot and NEO
database lookups.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents import Agent
from agents.model_settings import ModelSettings, Reasoning
from agents.tool import function_tool

from pydantic import BaseModel, Field
from rv_agentic.config.settings import get_settings
from rv_agentic.services import hubspot_client as hs
from rv_agentic.services.utils import extract_company_name, extract_person_name, normalize_domain
from rv_agentic.services import supabase_client
from rv_agentic.tools import mcp_client


CONTACT_RESEARCH_SYSTEM_PROMPT = (
    "# 👤 Contact Researcher Agent\n\n"
    "You are the **Contact Research & Enrichment Specialist** for RentVine’s sales team.\n\n"
    "## Objective\n"
    "Given a partial prospect (name/company/title/email may be missing):\n"
    "1) Resolve identity, 2) Enrich thoroughly (company & person),\n"
    "3) Score fit with RentVine’s Contact ICP, 4) Return a concise Markdown briefing.\n\n"
    "## Tooling\n"
    "- Use **HubSpot tools** (`hubspot_find_contact`) first for identity + CRM context.\n"
    "- Use **NEO DB tools** (`neo_find_contacts`) to reuse existing enrichment.\n"
"- Use **MCP tools via n8n** when you need more:\n"
"  - `get_contacts` to discover decision makers for a company.\n"
"  - `get_linkedin_profile_url` to lock the most likely LinkedIn profile.\n"
"  - `get_verified_emails` to obtain verified email addresses (tool requires person name,\n"
"    company name, and domain, so only call it when the domain is known and you need the email\n"
"    in the final briefing).\n"
    "  - `search_web`, `LangSearch_API`, `fetch_page` for additional public context.\n\n"
    "## Ground rules\n"
    "- Truthfulness over coverage; mark gaps with confidence.\n"
    "- Aim for **1–3 targeted tool calls** per contact; avoid redundant searches.\n"
    "- Achieve an **identity lock** (Name + Company + Title) before deep research "
    "when possible; if not, explain uncertainty.\n\n"
"## Output contract\n"
"- Return a concise, external-facing **Markdown** brief only. No JSON, no code fences.\n"
"- Always include: Agent Summary, Contact Overview, Professional Summary,\n"
 "  Career Highlights, Relevance to RentVine, Personalization Data Points,\n"
 "  Assumptions & Data Gaps, and Sources.\n"
 "\n"
 "## Structured output (worker mode)\n"
"- When invoked by automation, you will also be evaluated on the structured\n"
"  `ContactResearchOutput`. Populate its `contacts` array with the best-fit\n"
"  decision makers you find (name, title, email, LinkedIn, notes).\n"
"- Link each contact to the relevant company_domain so the pipeline can\n"
"  persist them automatically.\n"
)


class ContactResearchContact(BaseModel):
    """Structured contact candidate used by the contact research worker."""

    company_domain: str = Field(..., description="Domain of the target company.")
    full_name: str = Field(..., description="Contact's full name.")
    title: Optional[str] = Field(default=None, description="Job title or role.")
    email: Optional[str] = Field(default=None, description="Verified or high-confidence email.")
    linkedin_url: Optional[str] = Field(default=None, description="LinkedIn profile URL.")
    notes: Optional[str] = Field(default=None, description="Personalization or relevance notes.")


class ContactResearchOutput(BaseModel):
    """Structured output used in worker mode."""

    contacts: List[ContactResearchContact] = Field(
        default_factory=list,
        description="List of contact candidates discovered for this company.",
    )


@function_tool
def hubspot_find_contact(query: str) -> Dict[str, Any]:
    """Search for a contact in HubSpot using a flexible query (email, name, or company+name)."""

    q = (query or "").strip()
    if not q:
        return {}

    # Try email search first
    try:
        if "@" in q:
            contact = hs.search_contact(q)
            if contact:
                return {"source": "HubSpot", "contact": contact}
    except Exception:
        contact = None

    # Fallback: search by fields using extracted name/company hints
    name = extract_person_name(q) or q
    company = extract_company_name(q)
    try:
        matches = hs.search_contacts_by_query(name=name, company_name=company)
        if matches:
            return {"source": "HubSpot", "matches": matches}
    except Exception:
        return {}

    return {}


@function_tool
def neo_find_contacts(
    company_name: Optional[str] = None,
    email: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Search the NEO Research Database for contacts at a company."""

    company_name = (company_name or "").strip()
    email = (email or "").strip()
    if not company_name and not email:
        return {}

    # Use the generic find_contact API which can filter by company and name/email
    contacts = []
    if company_name:
        contacts = supabase_client.find_contact(
            email=email or None,
            company_name=company_name or None,
            strict=False,
            limit=limit,
        ) or []
    elif email:
        c = supabase_client.find_contact(email=email)
        contacts = [c] if c else []

    if not contacts:
        return {}
    return {"source": "NEO Research Database", "contacts": contacts}


def _contact_research_tools():
    """Return tools for the contact researcher agent, including MCP-backed helpers."""

    tools: list[Any] = [
        hubspot_find_contact,
        neo_find_contacts,
        mcp_get_contacts_for_company,
        mcp_get_verified_emails,
        mcp_get_linkedin_profile_url,
        mcp_search_web_for_person,
    ]
    return tools


def _build_verified_emails_payload(
    person_name: str,
    company_name: str,
    domain: Optional[str],
) -> Dict[str, Any] | None:
    """Helper to build payload for get_verified_emails MCP tool."""

    person = (person_name or "").strip()
    company = (company_name or "").strip()
    dom = normalize_domain(domain or "")
    if not person or not company or not dom:
        return None
    return {
        "person_name": person,
        "company_name": company,
        "domain": dom,
    }


@function_tool
async def mcp_get_contacts_for_company(
    company_name: str,
    company_domain: str,
    company_city: str,
    company_state: str,
) -> list[Dict[str, Any]]:
    """Use MCP `get_contacts` to fetch decision makers for a company."""

    if not company_name or not company_domain:
        return []
    payload: Dict[str, Any] = {
        "company_name": company_name,
        "company_domain": company_domain,
        "company_city": company_city,
        "company_state": company_state,
    }
    return await mcp_client.call_tool_async("get_contacts", payload)


@function_tool
async def mcp_get_verified_emails(
    person_name: str,
    company_name: str,
    domain: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Use MCP `get_verified_emails` to obtain verified emails for a person."""

    if not person_name or not company_name:
        return []
    payload = _build_verified_emails_payload(person_name, company_name, domain)
    if not payload:
        return []
    return await mcp_client.call_tool_async("get_verified_emails", payload)


@function_tool
async def mcp_get_linkedin_profile_url(name: str, company: str, jobtitle: str) -> list[Dict[str, Any]]:
    """Use MCP `get_linkedin_profile_url` to find the most likely LinkedIn profile URL."""

    if not name or not company:
        return []
    payload = {"name": name, "company": company, "jobtitle": jobtitle}
    return await mcp_client.call_tool_async("get_linkedin_profile_url", payload)


@function_tool
async def mcp_search_web_for_person(query: str) -> list[Dict[str, Any]]:
    """Use MCP `search_web` to gather additional public context on a person."""

    if not query:
        return []
    return await mcp_client.call_tool_async("search_web", {"query": query})


def create_contact_researcher_agent(name: str = "Contact Researcher") -> Agent:
    """Factory for the Contact Researcher agent."""

    return Agent(
        name=name,
        instructions=CONTACT_RESEARCH_SYSTEM_PROMPT,
        tools=_contact_research_tools(),
        model="gpt-5-mini",
        model_settings=ModelSettings(
            tool_choice="auto",  # Let model decide when to use tools
            reasoning=Reasoning(effort="medium"),  # Include status updates in output
        ),
        output_type=ContactResearchOutput,
    )
