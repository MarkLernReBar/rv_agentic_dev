"""Lead List Agent implemented with the OpenAI Agents SDK.

This replaces the legacy ``LeadListGenerator`` Responses-based
implementation with a true Agent that can call MCP-backed tools
and queue asynchronous enrichment runs via the pm_pipeline tables.
"""

from __future__ import annotations

from typing import Any, Dict, List

import logging
from pydantic import BaseModel, Field
from agents import Agent
from agents.model_settings import ModelSettings, Reasoning
from agents.tool import function_tool

from rv_agentic.services import supabase_client as sb
from rv_agentic.tools import mcp_client


logger = logging.getLogger(__name__)


LEAD_LIST_SYSTEM_PROMPT = """# 📋 Lead List Agent

You are the **Lead List Agent**, an expert at building targeted prospect
lists for property management companies. You specialize in qualifying
prospects, extracting criteria, and organizing comprehensive lead
generation campaigns.

## Primary goal (worker mode)
- When invoked from the async worker, your job is to **populate your structured output**
  (`LeadListOutput`) for a specific `run_id`:
  - Add eligible companies to the `companies` array.
  - Add 1–3 decision maker contacts per company to the `contacts` array.
- Python will perform all database writes based on your structured output. Merely
  describing companies in prose without updating `LeadListOutput` is considered a
  failure in worker mode.

## Tooling
- Use **MCP tools via n8n** (wrapped as Python tools) for web search,
  company discovery, contact discovery, enrichment, and NARPM/LinkedIn
  lookups:
  - `search_web` / `LangSearch_API` for broad web discovery.
  - `fetch_page` and `extract_company_profile_url_` to turn URLs into
    structured company facts (name, domain, location, PMS).
  - `Run_PMS_Analyzer_Script` when you need PMS + confidence for a known
    domain.
  - `get_contacts` / `get_verified_emails` / `get_linkedin_profile_url`
    to move from companies → decision-makers with emails + profiles.
  - `Query_NARPM` to confirm NARPM membership and member details.
- Use `get_blocked_domains_tool` to understand which company domains are suppressed.
- Never attempt to synthesize SQL or write directly to the database; you only plan and
  populate `LeadListOutput`.

## Required behavior in worker mode
- Always begin by:
  - Reading the provided `run_id` and criteria JSON from the prompt.
  - Calling `get_blocked_domains_tool` once to get suppressed domains.
- For each company you determine is eligible:
  - Ensure its **domain is not blocked**.
  - Add a `LeadListCompany` entry to your `companies` array with:
    - A normalized root `domain` (lowercase, no protocol/path).
    - `state` and `city` when available.
    - A short `reason` describing why it meets the criteria.
- For each such company, use MCP contact tools to find 1–3 decision makers and
  add `LeadListContact` entries to your `contacts` array:
  - Link each contact to the company via `company_domain`.
  - Include name, title, email (verified or high-confidence), LinkedIn URL, and a
    short `quality_notes` field explaining why they are a good target.
- Do **not** end the run after only describing candidates in prose. You must
  actually populate `companies` and `contacts` with the entities you deem eligible.
- Treat PMS/vendor requirements (e.g. \"Buildium\") as **hard eligibility constraints**
  for your `companies` array in worker mode. Only add a company when you have strong
  evidence (from PMS analyzer, trusted profiles, or authoritative data) that it uses
  the required PMS. If you cannot find enough such companies after reasonable search,
  return fewer and clearly explain the gap in natural language.
- Only return an empty `companies` array when there are truly no reasonable
  candidates at all; this should be very rare. In typical cases you should
  always return your best-effort set of candidates, even when some criteria
  are partially met.

## Context-gathering style (medium reasoning effort)
- Start from the **user’s stated criteria** (quantity, geography, PMS,
  units, exclusions, campaign type, timing) and restate them clearly.
- When you need external data, **batch related tool calls** rather than
  calling tools repeatedly for tiny increments.
- Prefer at most **3–5 MCP tool calls per user request**; reuse the
  results instead of re-searching unless something is clearly missing.
- If you still cannot meet the criteria after a reasonable search,
  explain the gap and propose next-best options instead of over-searching.

## Output expectations (UI mode)
- When responding in the UI, prioritize:
  - A short “Agent Summary” section.
  - A structured list of the key parameters you inferred.
  - Clear next steps: what will be queued into the async pipeline, and
    any assumptions you made.
-- Markdown only. No JSON or code fences in the final answer.
"""


class LeadListCompany(BaseModel):
    """Structured representation of a candidate company."""

    name: str = Field(..., description="Company name")
    domain: str = Field(..., description="Root domain, e.g. examplepm.com")
    state: str | None = Field(
        default=None,
        description="Two-letter state code when known (e.g. TN, NV)",
    )
    city: str | None = Field(default=None, description="City when known")
    meets_requirements: bool = Field(
        default=True,
        description="True when the company meets the run criteria.",
    )
    reason: str | None = Field(
        default=None,
        description="Short explanation of why this company was selected.",
    )


class LeadListContact(BaseModel):
    """Structured representation of a candidate contact."""

    company_domain: str = Field(..., description="Domain of the associated company")
    full_name: str = Field(..., description="Contact's full name")
    title: str | None = Field(default=None, description="Job title")
    email: str | None = Field(default=None, description="Verified or high-confidence email")
    linkedin_url: str | None = Field(default=None, description="Public LinkedIn profile URL")
    quality_notes: str | None = Field(
        default=None,
        description="Why this person is a good decision-maker target.",
    )


class LeadListOutput(BaseModel):
    """Structured output for the Lead List Agent.

    The worker uses this to drive reliable, scalable DB inserts.
    """

    companies: List[LeadListCompany] = Field(
        default_factory=list,
        description="List of candidate companies that meet the run criteria.",
    )
    contacts: List[LeadListContact] = Field(
        default_factory=list,
        description="List of candidate contacts across all companies.",
    )


@function_tool
async def mcp_search_web(query: str) -> List[Dict[str, Any]]:
    """Use MCP `search_web` for broad web discovery about companies."""

    if not query:
        return []
    return await mcp_client.call_tool_async("search_web", {"query": query})


@function_tool
async def mcp_lang_search(query: str) -> List[Dict[str, Any]]:
    """Use MCP `LangSearch_API` for web search via LangSearch."""

    if not query:
        return []
    return await mcp_client.call_tool_async(
        "LangSearch_API", {"parameters0_Value": query}
    )


@function_tool
async def mcp_extract_company_profile(company_name: str, domain: str, other_details: str = "") -> List[Dict[str, Any]]:
    """Use MCP `extract_company_profile_url_` to get structured company facts."""

    if not company_name and not domain:
        return []
    payload: Dict[str, Any] = {
        "company_name": company_name,
        "domain": domain,
        "other_details": other_details,
    }
    return await mcp_client.call_tool_async("extract_company_profile_url_", payload)


@function_tool
async def mcp_run_pms_analyzer(domain: str) -> List[Dict[str, Any]]:
    """Use MCP `Run_PMS_Analyzer_Script` to infer PMS and confidence for a domain."""

    if not domain:
        return []
    return await mcp_client.call_tool_async(
        "Run_PMS_Analyzer_Script", {"domain": domain}
    )


@function_tool
async def mcp_get_contacts_for_company(
    company_name: str,
    company_domain: str,
    company_city: str,
    company_state: str,
) -> List[Dict[str, Any]]:
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
async def mcp_get_verified_emails(person_name: str, company_name: str) -> List[Dict[str, Any]]:
    """Use MCP `get_verified_emails` to get verified emails for a person."""

    if not person_name or not company_name:
        return []
    payload = {"person_name": person_name, "company_name": company_name}
    return await mcp_client.call_tool_async("get_verified_emails", payload)


@function_tool
async def mcp_get_linkedin_profile_url(name: str, company: str, jobtitle: str) -> List[Dict[str, Any]]:
    """Use MCP `get_linkedin_profile_url` to find the most likely LinkedIn URL."""

    if not name or not company:
        return []
    payload = {"name": name, "company": company, "jobtitle": jobtitle}
    return await mcp_client.call_tool_async("get_linkedin_profile_url", payload)


@function_tool
async def mcp_query_narpm(page_offset: str, state: str, city: str) -> List[Dict[str, Any]]:
    """Use MCP `Query_NARPM` to query the Narpm directory."""

    payload = {
        "parameters0_Value": page_offset,
        "parameters2_Value": state,
        "parameters3_Value": city,
    }
    return await mcp_client.call_tool_async("Query_NARPM", payload)


@function_tool
def get_blocked_domains_tool() -> List[str]:
    """Return blocked domains from the pm_pipeline.v_blocked_domains view."""

    domains = sb.get_blocked_domains()
    logger.info("[LeadListAgent] get_blocked_domains_tool -> %d domains", len(domains))
    return domains


@function_tool
def insert_company_candidate_tool(
    run_id: str,
    name: str,
    website: str,
    domain: str,
    state: str,
    discovery_source: str = "mcp",
) -> Dict[str, Any]:
    """Insert a company candidate for the given run."""

    logger.info(
        "[LeadListAgent] insert_company_candidate_tool run_id=%s name=%s domain=%s state=%s",
        run_id,
        name,
        domain,
        state,
    )
    row = sb.insert_company_candidate(
        run_id=run_id,
        name=name,
        website=website,
        domain=domain,
        state=state,
        discovery_source=discovery_source,
    )
    if row:
        logger.info(
            "[LeadListAgent] insert_company_candidate_tool inserted id=%s",
            row.get("id"),
        )
    else:
        logger.info(
            "[LeadListAgent] insert_company_candidate_tool no-op (likely duplicate)"
        )
    return row or {}


@function_tool
def insert_contact_candidate_tool(
    run_id: str,
    company_id: str,
    full_name: str,
    title: str = "",
    email: str | None = None,
    linkedin_url: str | None = None,
) -> Dict[str, Any]:
    """Insert a contact candidate for a run and company."""

    logger.info(
        "[LeadListAgent] insert_contact_candidate_tool run_id=%s company_id=%s name=%s email=%s linkedin=%s",
        run_id,
        company_id,
        full_name,
        email,
        linkedin_url,
    )
    row = sb.insert_contact_candidate(
        run_id=run_id,
        company_id=company_id,
        full_name=full_name,
        title=title or None,
        email=email,
        linkedin_url=linkedin_url,
    )
    if row:
        logger.info(
            "[LeadListAgent] insert_contact_candidate_tool inserted id=%s",
            row.get("id"),
        )
    else:
        logger.info(
            "[LeadListAgent] insert_contact_candidate_tool no-op (likely duplicate)"
        )
    return row or {}


def create_lead_list_agent(name: str = "Lead List Agent") -> Agent:
    """Factory for the Lead List Agent.

    The resulting Agent can be run via ``Runner`` helpers and invoked
    from the Streamlit UI or async worker.
    """

    return Agent(
        name=name,
        instructions=LEAD_LIST_SYSTEM_PROMPT,
        tools=[
            # MCP discovery / enrichment tools
            mcp_search_web,
            mcp_lang_search,
            mcp_extract_company_profile,
            mcp_run_pms_analyzer,
            mcp_get_contacts_for_company,
            mcp_get_verified_emails,
            mcp_get_linkedin_profile_url,
            mcp_query_narpm,
            # DB-facing tools are exposed for UI / other flows,
            # but the async worker relies on structured output only.
            get_blocked_domains_tool,
        ],
        model="gpt-5-mini",
        model_settings=ModelSettings(
            tool_choice="required",
            reasoning=Reasoning(effort="medium"),
        ),
        output_type=LeadListOutput,
    )
