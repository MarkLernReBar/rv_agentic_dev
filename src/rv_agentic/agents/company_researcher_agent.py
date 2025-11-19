"""Company Researcher Agent implemented with the OpenAI Agents SDK.

This agent focuses on ICP-style company research for property
management firms, using MCP-backed web tools plus HubSpot, NEO
database and Narpm helpers exposed as Python tools.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents import Agent
from agents.model_settings import ModelSettings, Reasoning
from agents.tool import function_tool

from rv_agentic.config.settings import get_settings
from rv_agentic.services import hubspot_client as hs
from rv_agentic.services import narpm_client
from rv_agentic.services import supabase_client
from rv_agentic.services.utils import extract_company_name, normalize_domain, validate_domain
from rv_agentic.tools import mcp_client


COMPANY_RESEARCH_SYSTEM_PROMPT = (
    "# 🧭 Company Researcher Agent\n\n"
    "You are the Company Researcher Agent for property management firms.\n"
    "Your job is to produce a concise, **external-facing ICP brief** that a RentVine SDR\n"
    "can use immediately. Never expose internal processes, tool names (other than\n"
    "HubSpot and NEO Research Database), or raw intermediate outputs.\n\n"
    "## Required tool flow (efficiency-focused)\n"
    "For every request (domain or company name):\n"
    "1) Call `hubspot_find_company` first to lock identity and CRM status when possible.\n"
    "2) Call `neo_find_company` next **only if** you still lack key enrichment (units, PMS, ICP hints).\n"
    "3) Prefer a **single, rich MCP pass** via `mcp_extract_company_profile`:\n"
    "   - When it returns a high-confidence `research_packet` with company details, ICP fields,\n"
    "     decision_makers and sources (like the example in your tools context), treat that as your\n"
    "     primary source of truth and **do not call additional MCP company/contact discovery tools**\n"
    "     unless you are truly missing something critical.\n"
    "4) Only call `mcp_get_contacts_for_company` if you still do **not** have a usable decision maker\n"
    "   after HubSpot, NEO and `mcp_extract_company_profile`.\n"
    "5) You may call `mcp_get_linkedin_profile_url` **at most once**, for the single highest-priority\n"
    "   decision maker you plan to include in the brief, and only when their LinkedIn URL is\n"
    "   actually missing.\n"
    "6) If you still lack a verified email for that person, you may call `mcp_get_verified_emails`\n"
    "   once, passing their name, the company name, and the company domain (the tool requires\n"
    "   all three). Only call this when the email will clearly appear in your brief — otherwise\n"
    "   leave Email: `Unknown`.\n"
    "7) Use Narpm (`narpm_lookup_company`) when NARPM membership is relevant or ambiguous.\n"
    "6) Use Narpm (`narpm_lookup_company`) when NARPM membership is relevant or ambiguous.\n"
    "Prefer tools over free-text reasoning whenever you need concrete facts, but **aim for at most\n"
    "3–4 MCP tool calls per run** by reusing rich outputs instead of re-discovering the same facts.\n\n"
    "## ICP frame (RentVine)\n"
    "- Property management company\n"
    "- Primarily single-family (≥~50% SFH qualifies as ICP)\n"
    "- Exclusions: HOA-only, MF-only, brokerages, no active management\n\n"
    "Core dimensions:\n"
    "1) Portfolio Type (highest weight): SFH vs HOA/MF/commercial-only\n"
    "2) Units (estimate listings×10): <50 low | 50–150 med | 150–1000 high | >1000 enterprise\n"
    "3) PMS: competitors (AppFolio/Buildium/Yardi/DoorLoop) +; RentVine −; HOA/MF-centric −; unknown neutral\n"
    "4) Employees: 5–10 small; 20+ strong; 30–40+ enterprise signal\n"
    "5) Website provider: PM-focused vendors (PMW, Doorgrow, Fourandhalf, Upkeep) +; generic neutral; unrelated −\n\n"
    "If something is Unknown, say **Unknown** without guessing. Locked insights supplied in\n"
    "context are authoritative—repeat those values verbatim unless you uncover fresher,\n"
    "clearly conflicting data.\n\n"
    "## Output Contract (strict)\n"
    "- If HubSpot or NEO data clearly shows the company is an **existing RentVine customer** (e.g., PMS = \"RentVine\" or explicit RentVine references), state this at the very top of the brief\n"
    "  and frame the rest of the analysis as an **upsell / cross-sell / optimization opportunity**, not a cold acquisition.\n"
    "- Return a concise, external-facing Markdown brief.\n"
    "- **No JSON. No code fences. No extra sections outside this template.**\n"
    "- Fill in the following structure exactly, replacing angle-bracket placeholders:\n\n"
    "## ICP Analysis for <Company Name or Domain>\n"
    "- 2–4 sentences: summarizing the company's ICP fit, the potential opportunity for RentVine,\n"
    "  and other valuable insights for the sales team.\n\n"
    "## Insights Found\n"
    "- Website:\n"
    "- PMS Vendor:\n"
    "- Estimated Units Listed:\n"
    "- Estimated Employees:\n"
    "- NARPM Member:\n"
    "- Single Family?:\n"
    "- Disqualifiers:\n"
    "- ICP Fit?:\n"
    "- ICP Fit Confidence:\n"
    "- ICP Tier:\n"
    "- Reason(s) for Confidence:\n"
    "- Assumptions:\n\n"
    "## Decision Makers\n"
    "List 1–3 verified contacts (ranked):\n"
    "Full Name — Title  \n"
    "Email: <...>  \n"
    "Phone: <...>  \n"
    "LinkedIn: <...>  \n"
    "Personalization: <one tailored anecdote or proof point>\n\n"
    "## Agent Notes / Outreach Suggestions\n"
    "(Brief notes that would help an SDR open a conversation.)\n\n"
    "## Sources\n"
    "(List 3–6 actual sources you used. You may include 'HubSpot' and 'NEO Research Database' when used.)\n\n"
    "## Style & Tone\n"
    "- Conversational and professional, written for SDR handoff.\n"
    "- Lead with context — why this company matters — before granular data.\n"
    "- Avoid formulaic or templated phrasing.\n"
    "- Treat anecdotes as conversation starters, not trivia.\n"
    "- Show uncertainty gracefully (\"appears to be\", \"likely interested in\").\n"
    "- Never expose internal logic, weights, or raw signals.\n"
)


@function_tool
def hubspot_find_company(domain_or_name: str) -> Dict[str, Any]:
    """Look up a company in HubSpot by domain or name.

    Returns the best matching HubSpot company record if found, else {}.
    """

    raw = (domain_or_name or "").strip()
    if not raw:
        return {}

    # First, try to interpret the input as a domain (URL, bare domain, etc.)
    # Only do this when it looks domain-like to avoid bogus EQ domain filters.
    domain_candidate = normalize_domain(raw)
    if domain_candidate and "." in domain_candidate and validate_domain(domain_candidate):
        try:
            company = hs.search_company_by_domain(domain_candidate)
            if company:
                return {"source": "HubSpot", "company": company}
        except Exception:
            company = None

    # Fallback: search by company name with progressively simplified queries.
    name_queries: list[str] = []

    # 1) Full raw string
    name_queries.append(raw)

    # 2) Heuristic extraction from text (e.g. strip location qualifiers)
    extracted = extract_company_name(raw)
    if extracted and extracted.lower() != raw.lower():
        name_queries.append(extracted)

    # 3) Progressive token trimming at the end (handles "Grace Property Management Denver")
    tokens = raw.split()
    # Avoid over-aggressive trimming for very short names
    while len(tokens) > 1:
        tokens = tokens[:-1]
        trimmed = " ".join(tokens).strip()
        if not trimmed:
            break
        lowered = trimmed.lower()
        if all(lowered != q.lower() for q in name_queries):
            name_queries.append(trimmed)

    # Try each name query in order until one returns results
    for q in name_queries:
        try:
            companies = hs.search_companies_by_name(q)
            if companies:
                return {
                    "source": "HubSpot",
                    "company": companies[0],
                    "matches": companies,
                }
        except Exception:
            # If HubSpot is misconfigured, fail open with empty result rather than raising.
            continue

    return {}


def _build_verified_emails_payload(
    person_name: str,
    company_name: str,
    domain: str,
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
def neo_find_company(
    domain: Optional[str] = None,
    company_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Look up a company in the NEO Research Database (Supabase).

    Returns the most recently updated matching record if found, else {}.
    """

    result = supabase_client.find_company(domain=domain, company_name=company_name)
    if not result:
        return {}
    return {"source": "NEO Research Database", "company": result}


@function_tool
def narpm_lookup_company(name: str, city: Optional[str] = None, state: Optional[str] = None) -> Dict[str, Any]:
    """Look up a company in the Narpm membership directory."""

    if not name:
        return {}
    try:
        res = narpm_client.quick_company_membership(name=name, city=city, state=state)
        return {"source": "Narpm", "membership": res}
    except Exception:
        return {}


def _company_research_tools():
    """Return the tools for the company researcher agent, including MCP-backed helpers."""

    tools: list[Any] = [
        hubspot_find_company,
        neo_find_company,
        narpm_lookup_company,
        mcp_search_web_for_company,
        mcp_extract_company_profile,
        mcp_run_pms_analyzer,
        mcp_get_contacts_for_company,
        mcp_get_linkedin_profile_url,
        mcp_get_verified_emails,
        mcp_search_web_for_person,
    ]
    return tools


@function_tool
async def mcp_search_web_for_company(query: str) -> list[Dict[str, Any]]:
    """Use MCP `search_web` / `LangSearch_API` to find company-related web pages."""

    if not query:
        return []
    return await mcp_client.call_tool_async("search_web", {"query": query})


@function_tool
async def mcp_extract_company_profile(
    company_name: str,
    domain: str,
    other_details: str = "",
) -> list[Dict[str, Any]]:
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
async def mcp_run_pms_analyzer(domain: str) -> list[Dict[str, Any]]:
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
async def mcp_get_verified_emails(person_name: str, company_name: str, domain: str) -> List[Dict[str, Any]]:
    """Use MCP `get_verified_emails` to get verified emails for a person."""

    if not person_name or not company_name or not domain:
        return []
    payload = _build_verified_emails_payload(person_name, company_name, domain)
    if not payload:
        return []
    return await mcp_client.call_tool_async("get_verified_emails", payload)


@function_tool
async def mcp_get_linkedin_profile_url(name: str, company: str, jobtitle: str) -> List[Dict[str, Any]]:
    """Use MCP `get_linkedin_profile_url` to find the most likely LinkedIn URL."""

    if not name or not company:
        return []
    payload = {"name": name, "company": company, "jobtitle": jobtitle}
    return await mcp_client.call_tool_async("get_linkedin_profile_url", payload)


@function_tool
async def mcp_search_web_for_person(query: str) -> List[Dict[str, Any]]:
    """Use MCP `search_web` to gather additional public context on a person."""

    if not query:
        return []
    return await mcp_client.call_tool_async("search_web", {"query": query})


def create_company_researcher_agent(name: str = "Company Researcher") -> Agent:
    """Factory for the Company Researcher agent."""

    return Agent(
        name=name,
        instructions=COMPANY_RESEARCH_SYSTEM_PROMPT,
        tools=_company_research_tools(),
        model="gpt-5-mini",
        model_settings=ModelSettings(
            tool_choice="auto",  # Let model decide when to use tools
            reasoning=Reasoning(effort="medium"),  # Include status updates in output
        ),
    )
