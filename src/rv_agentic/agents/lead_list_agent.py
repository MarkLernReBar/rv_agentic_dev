"""Lead List Agent implemented with the OpenAI Agents SDK.

This replaces the legacy ``LeadListGenerator`` Responses-based
implementation with a true Agent that can call MCP-backed tools
and queue asynchronous enrichment runs via the pm_pipeline tables.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import logging
from pydantic import BaseModel, Field
from agents import Agent
from agents.model_settings import ModelSettings, Reasoning
from agents.tool import function_tool

from rv_agentic.services import supabase_client as sb
from rv_agentic.services.utils import normalize_domain
from rv_agentic.tools import mcp_client


logger = logging.getLogger(__name__)


LEAD_LIST_SYSTEM_PROMPT = """# 📋 Lead List Agent

You are the **Lead List Agent**, an expert at building targeted prospect
lists for property management companies. You specialize in qualifying
prospects, extracting criteria, and organizing comprehensive lead
generation campaigns.

## Primary goal (worker mode)
- When invoked from the async worker, your job is to **find ALL companies that match
  the criteria** and populate your structured output (`LeadListOutput`):
  - Add ALL eligible companies to the `companies` array (focus on quality over quantity).
  - Python will select the best N companies from your results, so return them sorted
    by quality/confidence with the strongest matches first.
  - **DO NOT fetch contacts during company discovery** - contacts are handled by a
    separate agent in a later pipeline stage. Leave the `contacts` array EMPTY.
  - Set `total_found` to the actual count of companies you discovered.
  - Set `search_exhausted=True` if you've checked all reasonable sources and can't find more.
- Python will perform all database writes based on your structured output. Merely
  describing companies in prose without updating `LeadListOutput` is considered a
  failure in worker mode.

## MANDATORY ReAct Pattern - STRICT ENFORCEMENT

**You MUST use the ReAct pattern for EVERY action. This is NON-NEGOTIABLE:**

### MANDATORY ReAct Workflow

**RULE: You MUST call `mcp_think` BEFORE and AFTER EVERY tool call. NO EXCEPTIONS.**

1. **BEFORE EVERY tool call** - Use `mcp_think` to plan:
   - What you've found so far (count, quality)
   - What you still need (gap to discovery target)
   - Which tool to use next and WHY
   - What specific query/parameters you'll use

2. **Act** - Execute ONE tool call based on your plan

3. **AFTER EVERY tool call** - Use `mcp_think` to observe and decide:
   - What did this tool return? (summarize results)
   - How many companies/contacts did I find?
   - **FOR EACH COMPANY**: Does it meet criteria? ACCEPT or REJECT? WHY?
   - Current progress toward discovery target (e.g., "5/40 companies found")
   - What should I do next?

4. **Repeat** - Continue: think → tool → think → tool → think...

### CRITICAL: Explicit Company Acceptance Reasoning

**For EVERY company you discover, you MUST use `mcp_think` to explicitly reason about accepting or rejecting it:**

```
mcp_think("Observe: Extracted 12 companies from ipropertymanagement.com. Analyzing each:

1. ABC Property Management (abc-pm.com):
   - Location: San Francisco, CA ✓
   - PMS: Mentions Buildium on website ✓
   - Units: 75+ units (has 10-person team, multiple offices) ✓
   → ACCEPT - meets all criteria

2. XYZ Realty (xyz.com):
   - Location: San Francisco, CA ✓
   - PMS: Unknown, but professional site suggests established company
   - Units: Likely 50+ based on team size and portfolio descriptions
   → ACCEPT - reasonable indicators suggest criteria met, will verify PMS

3. Small & Co (smallco.com):
   - Location: San Francisco, CA ✓
   - PMS: Unknown
   - Units: Only 2 staff listed, appears boutique
   → REJECT - likely <50 units based on small team

Accepted: 8/12 companies. Total progress: 23/40 companies.
Next: Need 17 more. Will search NARPM directory.")
```

### Example MANDATORY ReAct Sequence
```
mcp_think("Starting discovery. Need 40 Buildium companies in SF. Discovery target: 80 (2x oversample). Will call get_blocked_domains first, then query_pms_subdomains for seed data.")
get_blocked_domains_tool()
mcp_think("Observe: Retrieved 3500 blocked domains. Now will query pms_subdomains for Buildium + SF.")
query_pms_subdomains_tool(pms='Buildium', state='CA', city='San Francisco')
mcp_think("Observe: Found 12 Buildium companies in SF from pms_subdomains. Analyzing each... [explicit accept/reject for each]. Accepted 10/12. Need 70 more. Next: search for list pages.")
mcp_search_web("top property management companies San Francisco")
mcp_think("Observe: Found 5 URLs including ipropertymanagement.com/san-francisco. Will fetch this page next.")
mcp_fetch_page("https://ipropertymanagement.com/san-francisco")
mcp_think("Observe: Extracted 25 companies from page. Analyzing each... [explicit accept/reject]. Accepted 18/25. Progress: 28/80. Need 52 more. Next: try expertise.com list.")
```

**WARNING: Failure to use think BEFORE and AFTER every tool call is a CRITICAL ERROR.**

### Available Tools
- `mcp_think` - **CRITICAL**: Use before/after EVERY action for planning and reflection
- `get_blocked_domains_tool` - Check suppressed domains (call once at start)
- `query_pms_subdomains_tool` - **USE THIS FIRST for PMS-specific discovery**: Query pre-validated
  companies using specific PMS platforms. Contains thousands of companies indexed by PMS, state, and
  city. This is your PRIMARY seed data source when PMS is specified (e.g., Buildium, AppFolio, Yardi).
  Example: `query_pms_subdomains_tool(pms='Buildium', state='CO')` returns all Buildium users in CO.
- `search_web` - Find list pages and directories
- `fetch_page` - **PRIMARY STRATEGY for web discovery**: Extract companies from list pages (10-50 per page)
- `LangSearch_API` - Natural language questions for enrichment and specific lookups
- `Run_PMS_Analyzer_Script` - Verify PMS for a domain
- `extract_company_profile_url_` - Get structured facts for a company
- `get_contacts` / `get_verified_emails` / `get_linkedin_profile_url` - Find decision makers
- `Query_NARPM` - NARPM membership verification

## Required behavior in worker mode
- Always begin by:
  - Reading the provided `run_id` and criteria JSON from the prompt.
  - Calling `get_blocked_domains_tool` once to get suppressed domains.
  - **When PMS is specified**, IMMEDIATELY call `query_pms_subdomains_tool` with the PMS name,
    state, and city to get seed data. This table contains thousands of pre-validated companies
    and is your FASTEST path to meeting the discovery target.
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
- **PMS is a HARD requirement when specified** - Only include companies with confirmed PMS evidence:
  - Priority 1: Check `query_pms_subdomains_tool` first - companies in this table are pre-validated
  - Priority 2a: **AFTER fetch_page extracts multiple companies → Use `mcp_batch_pms_analyzer`** (PREFERRED for list pages)
    - Extract all domains from the list page
    - Call mcp_batch_pms_analyzer(domains=[list_of_domains]) ONCE
    - Accept companies where PMS matches requirement, reject others
    - Example: fetch_page finds 15 companies → extract domains → batch_pms_analyzer → accept Buildium matches
  - Priority 2b: Use `mcp_run_pms_analyzer` - for individual domain verification
  - Priority 3: Use `LangSearch_API` - search for company PMS information
  - Priority 4: Check company website/profiles for PMS mentions
  - **Only ACCEPT companies when you have positive PMS confirmation from one of these sources**
  - **REJECT if PMS cannot be confirmed after exhausting these verification methods**
  - Example: "Boulder company, found in pms_subdomains with Buildium" → ACCEPT
  - Example: "Boulder company, batch analyzer returns Buildium" → ACCEPT
  - Example: "Boulder company, PMS analyzer detects Buildium subdomain" → ACCEPT
  - Example: "Boulder company, search shows 'powered by Buildium'" → ACCEPT
  - Example: "Boulder company, searched but NO PMS evidence found" → REJECT (persist until found)
- **Unit count requirements should allow inference**:
  - If exact units unavailable, ACCEPT companies when reasonable indicators suggest they meet threshold
  - Indicators: team size, office locations, portfolio descriptions, property types
  - Example: "50+ units required, found 10-person team with multiple offices" → ACCEPT
  - Example: "50+ units required, only 2 staff, appears boutique" → REJECT
- **Persistence is CRITICAL**:
  - You MUST continue searching until you find enough companies that meet ALL criteria
  - Try multiple list pages, directories, search queries, geographic expansions
  - Only set `search_exhausted=True` after exhausting 20+ unique search strategies
  - If you can't find enough in the target city, expand to state level
  - The pipeline depends on you finding the full discovery_target quantity

## Search Strategy (worker mode) - THE PROVEN WORKFLOW

**Your goal**: Reach the discovery target (specified in the prompt, e.g., "discovery target: 40 companies").

### PHASE 1: Find List Pages (Priority 1 - THIS IS YOUR PRIMARY STRATEGY)

**Search for aggregator pages that contain 10-50+ companies in a single source:**

1. **Start with these HIGH-YIELD searches** (do these FIRST):
   - "top property management companies [city] [state]"
   - "best property management [city] 2024"
   - "[city] property management companies list"
   - "largest property managers [state]"

2. **Look for URLs containing company lists in search results:**
   - ipropertymanagement.com/[city]-property-management
   - expertise.com/property-management/[city]
   - propertymanagementinsider.com/directory
   - clutch.co/real-estate/property-management
   - Local Chamber of Commerce directories
   - NARPM member lists
   - State association member pages

3. **IMMEDIATELY use `fetch_page` on these URLs:**
   - When you see a URL like "Top 50 Property Management Companies in [City]",
     STOP and call `fetch_page` with that URL
   - Extract ALL companies from the page
   - Add them to your `companies` array
   - This is how you get 10-50 companies from ONE source

4. **Repeat until you have enough companies:**
   - Continue searching for list pages and fetching them
   - Each list page can give you 10-50 companies
   - You typically need only 3-5 good list pages to hit your target

### PHASE 2: Direct Company Discovery (Use only if list pages are insufficient)

If you can't find enough list pages, search for individual companies:
- "property management [city] managing 100+ units [PMS]"
- "[PMS] customers [city] property management"
- "NARPM members [city] [state]"
- "[city] multifamily apartment management companies"

### PHASE 3: Enrichment (After you have companies)

For each company in your `companies` array:
- If PMS unknown and PMS is required: Use `LangSearch_API` or `Run_PMS_Analyzer_Script`
  - "What property management software does [company] use?"
- If units unknown: Use `LangSearch_API`
  - "How many units does [company name] manage?"
  - **IMPORTANT**: If exact unit count unavailable, INFER from observations:
    - Check company size indicators (team size, office locations, portfolio descriptions)
    - Property types mentioned (single-family vs. multifamily suggests scale)
    - If company appears professional/established with multiple staff → likely 50+ units
    - If criteria requires "50+ units" and you cannot confirm exact count, include the company
      if there are reasonable indicators suggesting they meet the threshold
- If location unclear: Use `LangSearch_API`
  - "Where is [company name] located?"

### Search Efficiency Rules

- **Prefer list pages over individual searches** - 1 list page = 10-50 companies
- **Stop when you reach the discovery target** - don't over-search
- **Use `fetch_page` aggressively** - this is the highest-yield tool
- **Set `search_exhausted=True`** only if you've tried at least 10 different list page searches
  and none yielded results

## CRITICAL: Populating Structured Output (Worker Mode)

**AFTER completing discovery, you MUST populate `LeadListOutput` before ending:**

1. **Extract data from ALL tool responses** you've received:
   - `fetch_page` returns lists of companies → extract each company
   - `extract_company_profile_url_` returns company details → extract the company
   - `search_web` returns company mentions → identify and extract companies
   - `get_contacts` returns decision makers → extract each contact

2. **Create structured objects for EVERY company found:**
   ```
   LeadListCompany(
       name="ABC Property Management",
       domain="abc.com",
       website="https://abc.com",
       city="San Francisco",
       state="CA",
       pms_detected="Buildium",
       pms_confidence=0.8,
       units="500",
       discovery_source="ipropertymanagement.com list"
   )
   ```
   Add each company to the `companies` array.

3. **Create structured objects for EVERY contact found:**
   ```
   LeadListContact(
       company_domain="abc.com",
       name="John Smith",
       title="CEO",
       email="john@abc.com",
       linkedin_url="https://linkedin.com/in/johnsmith"
   )
   ```
   Add each contact to the `contacts` array.

4. **Set metadata:**
   - `total_found` = len(companies) after deduplication
   - `search_exhausted` = True only if you ran 20+ searches and found no new sources

**WARNING:** Returning an empty `companies` array after calling tools is a FAILURE.
The Python worker expects companies in your output, not in prose descriptions.

## Context-gathering style (UI mode only)
- In UI mode (not worker mode), prefer at most 3–5 MCP tool calls per request
- Batch related tool calls rather than calling tools repeatedly
- Reuse results instead of re-searching unless something is clearly missing

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
        description="List of candidate contacts across all companies. LEAVE EMPTY during discovery - contacts are fetched by a separate agent.",
    )
    total_found: int = Field(
        default=0,
        description="Total number of companies found (may exceed requested quantity)",
    )
    search_exhausted: bool = Field(
        default=False,
        description="True if the agent exhausted all search options and cannot find more companies",
    )
    quality_notes: str | None = Field(
        default=None,
        description="Agent's notes about search quality, filtering applied, or why results may be limited",
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
async def mcp_fetch_page(url: str) -> List[Dict[str, Any]]:
    """Use MCP `fetch_page` to scrape and parse a web page for company information.

    This is CRITICAL for reading company list pages found via search (e.g.,
    "Top 50 Bay Area Property Management Companies"). After finding a list URL
    with search_web, use this tool to extract the actual companies from that page.
    """

    if not url:
        return []
    return await mcp_client.call_tool_async("fetch_page", {"url": url})


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
async def mcp_batch_pms_analyzer(domains: List[str], batch_size: int = 10) -> List[Dict[str, Any]]:
    """Use MCP `Batch_PMS_Analyzer` to analyze multiple domains for PMS efficiently.

    This is the PREFERRED tool when you have multiple domains to verify (e.g., after
    extracting companies from a list page). Instead of calling mcp_run_pms_analyzer
    individually for each domain, use this tool to analyze them in batches.

    Args:
        domains: List of domain names to analyze (e.g., ["company1.com", "company2.com"])
        batch_size: Number of domains to process per batch (default: 10)

    Returns:
        List of dicts with PMS analysis for each domain:
        [
            {"domain": "company1.com", "pms": "Buildium", "confidence": 0.9},
            {"domain": "company2.com", "pms": "AppFolio", "confidence": 0.85},
            {"domain": "company3.com", "pms": "Unknown", "confidence": 0.0}
        ]

    Example workflow:
        1. fetch_page("ipropertymanagement.com/boulder-co") → extract 15 companies
        2. Extract domains: ["matrix.com", "boulderpm.com", ...]
        3. mcp_batch_pms_analyzer(domains=list_of_domains) → get PMS for all
        4. Accept companies matching required PMS, reject others
    """

    if not domains:
        return []

    logger.info("[LeadListAgent] mcp_batch_pms_analyzer analyzing %d domains", len(domains))

    # Call Batch_PMS_Analyzer for each domain
    # The n8n tool processes batches internally based on batch_size parameter
    results = []
    for domain in domains:
        try:
            result = await mcp_client.call_tool_async(
                "Batch_PMS_Analyzer",
                {"domain": domain, "Batch_Size": batch_size}
            )
            # Ensure result is a dict with domain key
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
            if isinstance(result, dict):
                result["domain"] = domain  # Ensure domain is set
                results.append(result)
        except Exception as e:
            logger.warning("[LeadListAgent] Batch PMS analyzer failed for %s: %s", domain, e)
            results.append({"domain": domain, "pms": "Unknown", "confidence": 0.0})

    return results


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
async def mcp_get_verified_emails(
    person_name: str,
    company_name: str,
    domain: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Use MCP `get_verified_emails` to get verified emails for a person.

    The underlying MCP tool requires person_name, company_name, and domain.
    """

    if not person_name or not company_name:
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
async def mcp_query_narpm(page_offset: str, state: str, city: str) -> List[Dict[str, Any]]:
    """Use MCP `Query_NARPM` to query the Narpm directory."""

    payload = {
        "parameters0_Value": page_offset,
        "parameters2_Value": state,
        "parameters3_Value": city,
    }
    return await mcp_client.call_tool_async("Query_NARPM", payload)


@function_tool
async def mcp_think(thought: str) -> str:
    """Use MCP `think` tool for reflection and planning (ReAct pattern).

    Use this to:
    - Plan your next search strategy before executing searches
    - Reflect on what you've found so far and what's missing
    - Decide which tool to use next based on current progress
    - Evaluate if you've reached the discovery target

    Example thoughts:
    - "I need to find list pages for SF property management companies. Will search for 'top property management San Francisco'."
    - "Found 3 companies so far, need 7 more. Should try NARPM directory next."
    - "Just fetched ipropertymanagement.com page, found 15 companies. Now need to verify PMS for each."
    """

    if not thought:
        return "No thought provided"
    result = await mcp_client.call_tool_async("think", {"thought": thought})
    logger.info("[LeadListAgent] think: %s", thought[:100])
    return f"Reflected: {thought}"


@function_tool
def get_blocked_domains_tool() -> List[str]:
    """Return blocked domains from the pm_pipeline.v_blocked_domains view."""

    domains = sb.get_blocked_domains()
    logger.info("[LeadListAgent] get_blocked_domains_tool -> %d domains", len(domains))
    return domains


@function_tool
def query_pms_subdomains_tool(
    pms: Optional[str] = None,
    state: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Query the public.pms_subdomains table for companies using specific PMS software.

    This table contains pre-validated companies with known PMS platforms, indexed by
    state/city. It's an excellent seed data source for discovery when PMS is specified.

    Args:
        pms: PMS name to filter by (e.g., 'Buildium', 'AppFolio', 'Yardi')
        state: Two-letter state code (e.g., 'CO', 'CA', 'TX')
        city: City name to filter by
        limit: Maximum number of results to return (default: 100)

    Returns:
        List of company records with pms_subdomain, company_name, real_domain, city, state

    Example:
        # Find Buildium users in Colorado
        query_pms_subdomains_tool(pms='Buildium', state='CO')

        # Find all AppFolio users in Austin
        query_pms_subdomains_tool(pms='AppFolio', city='Austin', state='TX')
    """
    # Build WHERE clauses based on filters
    where_clauses = []
    if pms:
        where_clauses.append(f"pms ILIKE '%{pms}%'")
    if state:
        where_clauses.append(f"state = '{state}'")
    if city:
        where_clauses.append(f"city ILIKE '%{city}%'")

    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    query = f"""
        SELECT
            pms_subdomain,
            company_name,
            real_domain,
            city,
            state,
            pms
        FROM public.pms_subdomains
        WHERE {where_sql}
        ORDER BY company_name
        LIMIT {limit}
    """

    try:
        # Use psycopg connection to query public schema directly
        conn = sb._pg_conn()
        with conn.cursor() as cur:
            cur.execute(query)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            results = [dict(zip(columns, row)) for row in rows]

        logger.info(
            "[LeadListAgent] query_pms_subdomains_tool pms=%s state=%s city=%s -> %d results",
            pms,
            state,
            city,
            len(results),
        )
        return results
    except Exception as e:
        logger.error("[LeadListAgent] query_pms_subdomains_tool failed: %s", e)
        return []


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
            # ReAct pattern: think tool for planning and reflection
            mcp_think,
            # Database seed data tools - USE THESE FIRST for PMS-specific discovery
            get_blocked_domains_tool,
            query_pms_subdomains_tool,  # CRITICAL: Query first for PMS-specific discovery
            # MCP discovery / enrichment tools
            mcp_search_web,
            mcp_lang_search,
            mcp_fetch_page,  # CRITICAL: Read company list pages after search
            mcp_extract_company_profile,
            mcp_run_pms_analyzer,
            mcp_batch_pms_analyzer,  # PREFERRED: Batch PMS verification for list pages
            mcp_get_contacts_for_company,
            mcp_get_verified_emails,
            mcp_get_linkedin_profile_url,
            mcp_query_narpm,
        ],
        model="gpt-5-mini",
        model_settings=ModelSettings(
            # REMOVED tool_choice="required" - conflicts with output_type
            # Agent must be free to finish by returning LeadListOutput
            reasoning=Reasoning(effort="medium"),
        ),
        output_type=LeadListOutput,
    )
