# CLAUDE.md
# Most Important RULES
1. YOU are the coding agent. YOU are proactive, resilient, and dont stop until the task is complete. You must be self-directed in all things, ensuring you adhere to the core requirements of the project. 
1a. DO NOT ask clarifying questions for things that are trivial; make reasonable assumptions and ACT. 
1b. DO NOT ask the user to run, install, or otherwise do a task that YOU the CODING agent should do. Its not the users job; it's YOURS.
2. DO NOT fake, mock, or otherwise make a test seem to show a passing grade when it in in fact does not. 
3. ALWAYS be OBJECTIVE and fearless in your self-critiques. 
4. DO NOT just tell the user what you think they want to hear. Always be objective and honest. 
5. This project ONLY uses gpt-5 family (gpt-5-mini or gpt-5-nano preferrably). Reference `/Users/marklerner/RV_Agentic_FrontEnd_Dev/IMPORTANT_DOCS/GPT-5_best_practices.md` for best practices when it comes to utilizing this family of model and prompting. 
6. This project uses the Open AI agents SDK. Reference `/Users/marklerner/RV_Agentic_FrontEnd_Dev/IMPORTANT_DOCS/Openai_AI_SDK.md` to answer all questions with regards to agentic functionality. 
7. ALWAYS use the `sequential_thinking` MCP to work through problems, bugs, or otherwise reason and observe. 

# Project-specific rules

1. The Agent(s) MUST persist until the required quantity is met. Each round of discovery should utilize UNIQUE and NON-OVERLAPPING discovery strategies until the quantity requirement is met. 
2. If any of the steps filters out companies such that the toital quantity dips below the total required quantity, discovery must be re-done with additional UNIQUE and NON OVERLAPPING strategies until the threshold is met again.
3. We need to ensure that no fully researched company or contact goes to waste. If a net new (not in the database or hubspot) record is generated for a test or a task, but not used in the final output (for tests this would mean any net new record that's not in the db or hubspot), it should be added to research_database or contacts table for future use. An example might be if we find a company, add it to the candidates, only to find that later in the task it doesnt meet the unit requirements. If we've done the work to research it, and it doesnt already exist in the db or hubspot, it should be added to research_database with the researched insights in their correct fields. 
4. Company Researcher MUST generate a robust, useful, and insightful response. If the given company being researched is fond to be an existing customer in Hubspot, the agent MUST include this at the top of its response and then approach the summary/icp overview from the perspective of a potential upsell or cross-sell. 
5. If a company being researched is found in hubspot, always check the hubspot contact records associated with the company FIRST when it comes to the decision maker section. Then check supabase and web search tools to fill in whatever is missing.
6. We need detailed logs that will enable us to QUICKLY triangulate where in the pipeline things are breaking and make targeted fixes. We need perfect visibility. 
---

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🎯 Phase 1 Status: COMPLETE ✅

**Date Completed:** 2025-01-17
**Status:** Production-ready for 5-10 company batches
**Test Coverage:** 20/20 tests passing
**Documentation:** See `PHASE1_FINAL_REPORT.md` for comprehensive results

## Project Overview

RentVine Agentic Lead List System - A Streamlit-based UI with GPT-5 powered agents and async background workers for property management lead generation and research. Built with OpenAI Agents SDK and integrated with MCP tools via n8n.

**Canonical documentation:** See `IMPORTANT_DOCS/rv_agentic_system_overview.md` for full architecture details.

## Development Commands

### Setup and Installation

```bash
# Install dependencies (preferred)
uv sync

# Or use pip
pip install -r requirements.txt

# Install with dev dependencies
pip install -e ".[dev]"
```

### Running the Application

```bash
# Start the Streamlit UI
streamlit run app.py
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_agents_creation.py

# Run with verbose output
pytest -v
```

### Worker Processes

Workers are long-running background processes that advance async pipeline runs:

```bash
# Company discovery worker
python -m rv_agentic.workers.lead_list_runner

# Company research worker
python -m rv_agentic.workers.company_research_runner

# Contact research worker
python -m rv_agentic.workers.contact_research_runner

# Staging promotion worker
python -m rv_agentic.workers.staging_promotion_runner
```

**Worker Configuration via Environment Variables:**

- `RUN_FILTER_ID` - Target a specific run UUID (for testing/debugging)
- `WORKER_MAX_LOOPS` - Limit worker loop iterations (defaults to 3 when RUN_FILTER_ID is set)
- `STAGING_SEARCH_RUN_ID` - Search run ID for staging promotion
- `STAGING_PM_RUN_ID` - PM run ID for staging promotion
- `STAGING_PMS_REQUIRED` - PMS filter for staging promotion
- `STAGING_MIN_PMS_CONFIDENCE` - PMS confidence threshold (default: 0.7)

## Architecture

### Core Components

1. **Streamlit UI (`app.py`)**
   - Single-page app with 4 specialized agents
   - Lead List run status/monitoring panel
   - HubSpot integration actions (pin notes, create records)

2. **Agents (`src/rv_agentic/agents/`)**
   - All agents use OpenAI Agents SDK with GPT-5 models
   - `company_researcher_agent.py` - ICP analysis (gpt-5-mini)
   - `contact_researcher_agent.py` - Decision maker discovery (gpt-5-mini)
   - `lead_list_agent.py` - Lead list generation (gpt-5-mini)
   - `sequence_enroller_agent.py` - HubSpot sequence management (gpt-5-nano)

3. **Workers (`src/rv_agentic/workers/`)**
   - Async background processes for pipeline stages
   - Use leases and idempotent inserts for reliability
   - Advance runs through stages: company_discovery → company_research → contact_discovery → done

4. **Services (`src/rv_agentic/services/`)**
   - `supabase_client.py` - Postgres/Supabase for research DB and pm_pipeline tables
   - `hubspot_client.py` - HubSpot CRM/sequence integration
   - `narpm_client.py` - NARPM membership lookup
   - `openai_provider.py` - OpenAI client and Agent runner
   - `notifications.py` - SMTP email notifications

5. **MCP Integration (`src/rv_agentic/tools/`)**
   - `mcp_client.py` - HTTP client for n8n MCP server
   - `mcp_n8n_tools.py` - Tool wrappers for Agents SDK
   - Tools: web search, company profiles, PMS detection, contact discovery, email verification

### Database Schema

**Research Database Tables:**
- `companies` - Company master records
- `contacts` - Contact master records

**Pipeline Tables (`pm_pipeline.*`):**
- `runs` - Async lead list runs with criteria, stage, status
- `company_candidates` - Discovered companies per run
- `company_research` - ICP research facts/signals per company
- `contact_candidates` - Contacts per company/run
- `staging_companies` - High-surface candidate staging pool
- `suppression_domains` + `hubspot_domain_suppression` - Domain blocklists

**Key Views:**
- `v_company_gap` - Companies ready/gap per run
- `v_contact_gap` - Contact min/capacity/gap per run
- `v_contact_gap_per_company` - Per-company contact gaps
- `v_run_resume_plan` - Stage + gaps for worker resume logic
- `v_blocked_domains` - All suppressed domains

See `IMPORTANT_DOCS/pm_pipeline_tables.md` for full SQL schema.

### Pipeline Flow

1. **Run Creation** - Insert into `pm_pipeline.runs` with criteria (PMS, geo, units, quantity)
2. **Company Discovery** (`stage='company_discovery'`)
   - **Oversample Strategy**: Discover 2x target quantity to account for enrichment attrition
   - Seed from NEO research DB and `pms_subdomains` table (fast, PMS-validated)
   - Lead List Agent discovers ALL matching companies via MCP tools
   - Worker selects best N companies (sorted by quality) up to discovery_target
   - Insert into `company_candidates` with `discovery_source` tracking
   - Advance when discovery_target met (see `OVERSAMPLE_STRATEGY.md`)
3. **Company Research** (`stage='company_research'`)
   - Company Researcher Agent enriches each company
   - Write ICP analysis to `company_research`
   - Advance when research queue is empty
4. **Contact Discovery** (`stage='contact_discovery'`)
   - Contact Researcher Agent finds 1-3 decision makers per company
   - Insert into `contact_candidates`
   - Advance to `done` when `v_contact_gap.contacts_min_gap_total == 0`
   - If gap remains after `WORKER_MAX_LOOPS`, set `status='needs_user_decision'`

### Environment Configuration

Required variables (`.env.local`):

```bash
OPENAI_API_KEY=...
NEXT_PUBLIC_SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...  # or NEXT_PUBLIC_SUPABASE_ANON_KEY
HUBSPOT_PRIVATE_APP_TOKEN=...
POSTGRES_URL=...  # or SUPABASE_POSTGRES_URL
```

Optional but recommended:

```bash
# Email notifications
SMTP_HOST=...
SMTP_PORT=...
SMTP_USER=...
SMTP_PASSWORD=...
EMAIL_FROM=...
NOTIFICATION_EMAIL=...

# MCP/n8n integration
N8N_MCP_SERVER_URL=...
N8N_MCP_SERVER_LABEL=default-server
N8N_MCP_AUTH_TOKEN=...
N8N_MCP_BASE_URL=...

# HubSpot owner fallback
HUBSPOT_OWNER_USER_IDS=...

# Serper API
SERPER_API_KEY=...

# HTTP timeout (default: 20s)
HTTP_TIMEOUT=20

# Lead list discovery oversample factor (default: 2.0)
# Discover N×target companies to account for enrichment attrition
LEAD_LIST_OVERSAMPLE_FACTOR=2.0
```

**See `OVERSAMPLE_STRATEGY.md` for details on multi-stage enrichment pipeline architecture.**

## Key Patterns and Conventions

### Agent Development

- All agents must use `Agent(...)` from OpenAI Agents SDK
- Model pinning: Company/Contact/Lead List use `gpt-5-mini`, Sequence Enroller uses `gpt-5-nano`
- Tests in `tests/test_agents_creation.py` enforce model IDs
- Tools are exposed via `@function_tool` decorator
- System prompts should include status update guidance (use emojis: 🔍 🌐 📋 🧭 ✍️ ✅)
- Structured outputs use Pydantic models with `output_type=` parameter

### Database Patterns

- Use `supabase_client` helpers for all DB operations
- Inserts are idempotent - duplicate key errors are swallowed gracefully
- Workers use leases (`worker_id`, `worker_lease_until`) to prevent double-processing
- Unique constraints: `(run_id, domain)` for companies, `(run_id, company_id, email)` for contacts
- Idempotency keys (`idem_key`) prevent duplicate inserts

### MCP Tool Integration

- All MCP calls go through `mcp_client.call_mcp_tool(tool_name, params)`
- Tool wrappers in `mcp_n8n_tools.py` expose them to Agents SDK
- MCP server URL configured via `N8N_MCP_SERVER_URL` env var
- Available tools: search_web, extract_company_profile_url_, Run_PMS_Analyzer_Script, get_contacts, get_verified_emails, get_linkedin_profile_url

### Hard Constraints (Production Requirements)

- **PMS requirements are hard constraints** in worker mode - only include companies with strong PMS evidence
- **Suppression is absolute** - always check `v_blocked_domains` and never add blocked domains
- **Contacts: 1-3 per company** - enforced via `contacts_min`/`contacts_max` in runs table
- **No guessing** - if data is Unknown, mark it as such and cite sources

### Worker Resilience

- Workers poll continuously with configurable loop limits
- Use `RUN_FILTER_ID` for targeted testing of specific runs
- Contact research worker enters `needs_user_decision` state when gap cannot be closed
- Email notifications alert operators when manual intervention needed
- UI provides three options: expand location, loosen PMS, accept partial results

## Common Development Workflows

### Adding a New Agent

1. Create agent file in `src/rv_agentic/agents/`
2. Define system prompt with required tool flow and status updates
3. Implement `@function_tool` wrappers for Python services
4. Use `Agent(..., model="gpt-5-mini", tool_choice="required")`
5. Add `create_<agent_name>_agent()` factory function
6. Add test to `tests/test_agents_creation.py` with model assertion
7. Register in `app.py` session state

### Adding a New MCP Tool

1. Add tool wrapper in `src/rv_agentic/tools/mcp_n8n_tools.py`
2. Use `@function_tool` decorator with clear docstring
3. Call `mcp_client.call_mcp_tool(tool_name, params)` inside wrapper
4. Register tool in agent's `Agent(..., tools=[...])` list
5. Update agent system prompt to document when/how to use the tool

### Testing Workers Locally

1. Set `RUN_FILTER_ID` to target specific run UUID
2. Set `WORKER_MAX_LOOPS=3` to prevent infinite loops
3. Run worker module: `python -m rv_agentic.workers.<worker_name>`
4. Check logs for processing steps and status updates
5. Query gap views to verify progress: `SELECT * FROM pm_pipeline.v_company_gap WHERE run_id = '...'`

### Debugging Agent Behavior

1. Check agent system prompt for required tool flow
2. Verify model is correct in tests: `assert agent.model == "gpt-5-mini"`
3. Run agent interactively via Streamlit UI for visual feedback
4. Review status emoji lines (🔍 🌐 📋) in streaming output
5. For workers, check `facts` and `signals` JSON in research tables

## Important Notes

- Legacy `*_legacy.py` agents have been removed - only Agents SDK implementations are supported
- Do not commit `.env.local` - it is gitignored and contains secrets
- HubSpot operations require appropriate token scopes (contacts, companies, notes, sequences)
- Gap views are the source of truth for worker stage transitions
- Sequence Enroller is orthogonal to the lead list pipeline
- All agent outputs should be external-facing - never expose internal tool names or processes
