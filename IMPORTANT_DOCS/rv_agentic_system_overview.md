# RentVine Agentic Lead List System – Canonical Overview

This document is the **single source of truth** for how the current
RentVine agentic system works: components, data flow, user expectations,
and how the codebase implements those expectations.

---

## 1. Purpose

The system provides a set of GPT‑5–powered agents and background workers
to:

- Research property management companies (ICP, PMS, units, etc.).
- Discover and enrich decision‑maker contacts.
- Build and maintain async lead‑list runs that honor strict constraints
  (PMS, geography, units, suppression).

It has two primary use modes:

1. **Interactive UI** (Streamlit app)
   - Company Researcher
   - Contact Researcher
   - Lead List Generator (status/monitoring + UX around async runs)

2. **Async pipeline** (Postgres `pm_pipeline.*` tables + workers)
   - Long‑running lead‑list runs that can be paused/resumed and scaled.

Sequence Enroller exists but is orthogonal to the lead‑list pipeline and
is not covered deeply here.

---

## 2. High‑Level Architecture

### 2.1 Components

- **UI / App**
  - `app.py` – Streamlit single‑page app with agent tabs and a Lead List
    run status panel.

- **Agents (OpenAI Agents SDK)**
  - `company_researcher_agent.py` – “Company Researcher”
  - `contact_researcher_agent.py` – “Contact Researcher”
  - `lead_list_agent.py` – “Lead List Agent” (used by workers)
  - `sequence_enroller_agent.py` – “Sequence Enroller”

- **Workers**
  - `workers/lead_list_runner.py` – seeds and discovers companies for
    async runs.
  - `workers/company_research_runner.py` – fills company ICP research.
  - `workers/contact_research_runner.py` – fills contact gaps.
  - `workers/staging_promotion_runner.py` – promotes PMS‑qualified
    staging rows into `company_candidates`.

- **Services**
  - `services/openai_provider.py` – OpenAI client + simple Agent runner.
  - `services/supabase_client.py` – Postgres/Supabase helpers for both
    legacy research DB and `pm_pipeline.*` tables.
  - `services/hubspot_client.py` – HubSpot CRM/sequence APIs.
  - `services/narpm_client.py` – Narpm membership lookup.
  - `services/utils.py` – shared helpers (domain parsing, etc.).
  - `services/notifications.py` – SMTP email notifications for runs.

- **MCP / external tools**
  - `tools/mcp_client.py` – MCP HTTP client to your n8n MCP server.
  - `tools/mcp_n8n_tools.py` – thin wrappers for tool names/usage.

- **Database**
  - Core research DB tables (companies, contacts, etc.) accessed via
    `find_company`, `find_contact`.
  - `pm_pipeline.*` tables and views for the async lead‑list pipeline
    (see `IMPORTANT_DOCS/pm_pipeline_tables.md`).
  - `public.pms_subdomains` for imported PMS subdomains (Buildium,
    AppFolio, etc.).

### 2.2 Models used

All production agents in this repo use GPT‑5 family models:

- Company / Contact / Lead List → `gpt-5-mini`
- Sequence Enroller → `gpt-5-nano`

Tests in `tests/test_agents_creation.py` assert these model IDs.

---

## 3. Data Model (pm_pipeline)

Key tables/views (see `pm_pipeline_tables.md` for full SQL):

- `pm_pipeline.runs`
  - One row per async lead‑list run.
  - Fields:
    - `criteria` (jsonb) – PMS, geo, units, etc.
    - `target_quantity` – target number of companies.
    - `contacts_min`, `contacts_max` – required contacts per company
      (default 1–3).
    - `stage` – `company_discovery` → `company_research`
      → `contact_discovery` → `done`.
    - `status` – `active`, `completed`, `error`,
      `needs_user_decision`.

- `pm_pipeline.company_candidates`
  - Discovered companies for a run.
  - Fields:
    - `run_id`, `name`, `domain`, `state`, `pms_detected`,
      `units_estimate`.
    - `status` – `candidate`, `validated`, `promoted`.
    - `meets_all_requirements` – boolean flag.
  - Unique per `(run_id, domain)` and `(run_id, idem_key)`; inserts are
    idempotent in code.

- `pm_pipeline.company_research`
  - ICP research for a `(run_id, company_id)`.
  - `facts` (jsonb) – Markdown analysis, PMS, units, etc.
  - `signals` (jsonb) – structured metrics/tags.

- `pm_pipeline.contact_candidates`
  - Contacts for `(run_id, company_id)`.
  - Fields:
    - `full_name`, `title`, `email`, `linkedin_url`, `status`.
  - Unique per (run, company, email/LinkedIn/idempotency); code treats
    unique violations as no‑ops.
  - Contacts count toward gaps only when
    `status IN ('validated', 'promoted')`.

- `pm_pipeline.staging_companies`
  - High‑surface candidate staging pool for PMS/units‑enriched domains.
  - Fields:
    - `run_id` (search_run), `source`, `raw_*` and `normalized_*`
      identity fields.
    - `pms_detected`, `pms_confidence`, `units_estimate`,
      `units_confidence`.
    - `status` – `pending_pms`, `pms_checked`, `eligible`, `rejected`.
    - `rejection_reason`, `evidence`, `meta`.

- Suppression:
  - `pm_pipeline.hubspot_domain_suppression` +
    `pm_pipeline.suppression_domains`.
  - `pm_pipeline.v_blocked_domains` – all blocked/suppressed domains.

- Gap views:
  - `pm_pipeline.v_company_gap` – companies_ready/gap per run.
  - `pm_pipeline.v_contact_gap_per_company` – per‑company contact gaps.
  - `pm_pipeline.v_contact_gap` – aggregate contact gaps per run.
  - `pm_pipeline.v_run_resume_plan` – stage + company/contact gaps per
    run, used by workers.

---

## 4. Agents – Responsibilities & Tools

### 4.1 Company Researcher Agent

File: `agents/company_researcher_agent.py`

Goal:

- Produce an external‑facing ICP brief for a company:
  PMS, units, portfolio mix, ICP tier, and outreach notes.

Tools (via Agents SDK):

- `hubspot_find_company` – identity + CRM status.
- `neo_find_company` – existing enrichment from research DB
  (`find_company`).
- MCP tools via n8n (wrapped with `mcp_client`):
  - `mcp_search_web_for_company` → `search_web` / `LangSearch_API`.
  - `mcp_extract_company_profile` → `extract_company_profile_url_`.
  - `mcp_run_pms_analyzer` → `Run_PMS_Analyzer_Script`.
  - `mcp_get_contacts_for_company` → `get_contacts`.
  - `mcp_get_verified_emails` → `get_verified_emails`.
  - `mcp_get_linkedin_profile_url` → `get_linkedin_profile_url`.
  - `mcp_search_web_for_person` → `search_web`.
- `narpm_lookup_company` – Narpm membership queries.

Model:

- `Agent(..., model="gpt-5-mini", tool_choice="required")`

Usage:

- Interactive via UI (Company Researcher tab).
- Async via `company_research_runner`, which passes:
  - Run id, criteria, and company details in the prompt.

### 4.2 Contact Researcher Agent

File: `agents/contact_researcher_agent.py`

Goal:

- Deep contact research for property management professionals:
  identify decision makers, enrich profiles, and return a concise
  Markdown brief plus structured `ContactResearchOutput`.

Tools:

- `hubspot_find_contact` – search HubSpot by email/name/company.
- `neo_find_contacts` – pull existing contacts from research DB
  (`find_contact`).
- MCP tools:
  - `mcp_get_contacts_for_company` → `get_contacts`.
  - `mcp_get_verified_emails` → `get_verified_emails`.
  - `mcp_get_linkedin_profile_url` → `get_linkedin_profile_url`.
  - `mcp_search_web_for_person` → `search_web`.

Model:

- `Agent(..., model="gpt-5-mini", tool_choice="required", output_type=ContactResearchOutput)`

Usage:

- Interactive via UI (Contact Researcher tab).
- Async via `contact_research_runner`, which:
  - Computes how many contacts are needed from gap views.
  - Passes run id, criteria, company, and `needed` into the prompt.

### 4.3 Lead List Agent

File: `agents/lead_list_agent.py`

Goal:

- In worker mode, **populate structured `LeadListOutput`**:
  - `companies`: candidate companies meeting run criteria.
  - `contacts`: 1–3 decision makers per company.

Constraints:

- PMS/vendor requirements in worker mode are treated as **hard
  constraints**:
  - Only include companies with strong PMS evidence matching the
    requested PMS.
  - If not enough companies exist, return fewer and explain the gap.
- Must consult `get_blocked_domains_tool` once and never add blocked
  domains.

Tools:

- MCP discovery:
  - `mcp_search_web`, `mcp_lang_search`.
  - `mcp_extract_company_profile`, `mcp_run_pms_analyzer`.
  - `mcp_get_contacts_for_company`, `mcp_get_verified_emails`,
    `mcp_get_linkedin_profile_url`, `mcp_query_narpm`.
- DB helper:
  - `get_blocked_domains_tool` → `v_blocked_domains`.

Model:

- `Agent(..., model="gpt-5-mini", tool_choice="required", output_type=LeadListOutput)`

Usage:

- Primarily via `lead_list_runner` in worker mode, which:
  - Seeds from internal data (NEO + `pms_subdomains`).
  - Calls the Agent once per run.
  - Inserts `LeadListOutput.companies` and `.contacts` into
    `company_candidates` and `contact_candidates`.
  - Advances stage when `v_company_gap.companies_gap == 0`.

---

## 5. Workers & Async Flow

### 5.1 lead_list_runner – Company discovery

File: `workers/lead_list_runner.py`

Responsibilities:

- For `pm_pipeline.runs` with `stage='company_discovery'`:
  - Normalize `criteria`.
  - **Seed candidate companies**:
    - From `public.pms_subdomains` when PMS is specified.
    - From NEO via `find_company(pms=..., city=...)`.
    - Insert seeds as `status='validated'`, `pms_detected` set.
  - Run Lead List Agent to produce `LeadListOutput`.
  - Insert any additional companies & contacts from structured output.
  - If still zero companies:
    - Attempt a text fallback parser (`CANDIDATE_COMPANIES:` section).
  - If after all attempts there are still no companies:
    - Mark run `completed` with a clear error/notes.
  - When `v_company_gap.companies_gap == 0`:
    - Advance stage `company_discovery → company_research`.

Controls:

- `RUN_FILTER_ID` – if set, only processes that run (and will re‑activate
  it if needed).
- `WORKER_MAX_LOOPS` – optional; in targeted mode (RUN_FILTER_ID set)
  defaults to 3 loops so tests don’t run indefinitely.

### 5.2 staging_promotion_runner – Staging → company_candidates

File: `workers/staging_promotion_runner.py`

Responsibilities:

- Promote PMS‑qualified staging companies into a specific run.

Config via env:

- `STAGING_SEARCH_RUN_ID` – `pm_pipeline.search_runs.id`
- `STAGING_PM_RUN_ID` – `pm_pipeline.runs.id`
- `STAGING_PMS_REQUIRED` – optional PMS filter.
- `STAGING_MIN_PMS_CONFIDENCE` – PMS confidence threshold (default 0.7).
- `STAGING_MAX_COMPANIES` – optional cap.

Behavior:

- Calls `promote_staging_companies_to_run(...)`:
  - Fetches `staging_companies.status='eligible'` rows for the
    `search_run`.
  - Filters by PMS + confidence and blocked domains.
  - Inserts into `company_candidates` as `status='validated'`.
- Logs updated `v_company_gap`.
- When `companies_gap == 0` and stage is `company_discovery`, advances to
  `company_research`.

### 5.3 company_research_runner – Company enrichment

File: `workers/company_research_runner.py`

Responsibilities:

- Lease companies needing research for runs in `stage='company_research'`
and `status='validated'`.
- Build prompts from run `criteria` and company fields.
- Run Company Researcher Agent via `Runner.run_sync`.
- Insert/update `company_research` rows with:
  - `facts["analysis_markdown"]`
  - `signals`, `confidence`, `status='complete'`.
- When no more companies remain in `v_company_research_queue`:
  - Advance run to `stage='contact_discovery'`.

Controls:

- `RUN_FILTER_ID` – optional targeting of a single run.
- `WORKER_MAX_LOOPS` – optional; in targeted mode defaults to 3 loops.

### 5.4 contact_research_runner – Contact enrichment

File: `workers/contact_research_runner.py`

Responsibilities:

- Lease companies needing contacts for runs in
  `stage='contact_discovery'`, based on `v_contact_gap_per_company`.
- For each leased company:
  - Fetch run criteria.
  - Compute `needed = contacts_min_gap` for that company.
  - Build prompt with run id, criteria, company details, and `needed`.
  - Run Contact Researcher Agent via `Runner.run_sync`.
  - Parse `result.final_output_as(ContactResearchOutput)`.
  - Insert up to `needed` contacts into `contact_candidates` with:
    - `status="validated"` and idempotent `idem_key`.
- After each insertion:
  - If `v_contact_gap.contacts_min_gap_total == 0`:
    - Advance run `stage='done', status='completed'`.

Controls & resilience:

- `RUN_FILTER_ID` – optional single‑run target.
- `WORKER_MAX_LOOPS`:
  - If set, used as is.
  - If not set and `RUN_FILTER_ID` is present, defaults to 3 loops.

- **Needs user decision gate**:
  - When the worker exits due to `WORKER_MAX_LOOPS` and a specific run is
    targeted:
    - If `contacts_min_gap_total > 0`:
      - Builds a message summarizing:
        - Criteria (city, state, PMS, quantity).
        - Remaining contact gap.
        - Options:
          1. Expand location requirements.
          2. Loosen PMS requirements.
          3. Accept partial results.
      - Sets `runs.status='needs_user_decision'` and writes the message
        into `notes` via `update_pm_run_status`.
      - Calls `send_run_notification(...)` to email the operator.

---

## 6. UI Flows & Expectations

### 6.1 Company Researcher (UI)

- User selects **Company Researcher** in the sidebar.
- Inputs a company description or domain into the chat box.
- Expectations:
  - The agent:
    - Tries HubSpot/NEO first to lock identity.
    - Uses MCP tools for PMS, units, and public info.
    - Returns a Markdown ICP brief.
  - The UI:
    - Shows user and assistant messages.
    - Allows pinning/saving to HubSpot via the HubSpot actions panel.

Mapping to code:

- `app.py`:
  - `create_company_researcher_agent()` → `run_agent_sync(...)`.
  - Renders Markdown output.
- `company_researcher_agent.py`:
  - Implements the system prompt and required tool flow.

### 6.2 Contact Researcher (UI)

- User selects **Contact Researcher**.
- Inputs something like “Find decision makers at San Diego Premier
  Property Management”.
- Expectations:
  - The agent:
    - Uses HubSpot/NEO to find existing contacts first.
    - Then uses MCP contact tools for new discovery and verification.
    - Returns a Markdown brief + structured `ContactResearchOutput`.
  - The UI:
    - Shows the analysis.
    - Enables HubSpot pin/create actions for contact notes.

Mapping to code:

- `app.py`:
  - `create_contact_researcher_agent()` → `run_agent_sync(...)`.
- `contact_researcher_agent.py`:
  - Implements the system prompt, tools, and structured output.

### 6.3 Lead List Generator (UI)

- User selects **Lead List Generator**.
- The main UI currently focuses on **run status**, not on creating runs.
- Expectations:
  - User can paste a `pm_pipeline.runs.id` and see:
    - Run `status`, `stage`, `target_quantity`.
    - Companies ready/gap.
    - Contact min/capacity/gap totals.
  - If the run is `needs_user_decision`:
    - See a detailed explanation.
    - Choose one of three options:
      1. Expand location.
      2. Loosen PMS.
      3. Accept partial results.
    - For “Accept partial results”, the run should be marked completed.

Mapping to code:

- `app.py` → “Lead List Run Status” section:
  - Uses `_sb.get_pm_run`, `_sb.get_pm_company_gap`,
    `_sb.get_contact_gap_summary`.
  - Renders progress.
  - If `status == 'needs_user_decision'`:
    - Shows `run['notes']` message.
    - Renders 3 buttons.
    - On “Accept Partial Results”:
      - Sets run stage `done`, status `completed` via `set_run_stage` +
        `update_pm_run_status`.
    - On “Expand Location” / “Loosen PMS”:
      - Keeps `status='needs_user_decision'` but appends a `[User decision] ...`
        marker to notes, so downstream logic (or an operator) knows how to
        adjust and resume.

### 6.4 Async Lead‑List Runs (Full Pipeline)

Typical flow for a production run:

1. **Run creation**
   - A separate orchestrator (UI or enrichment_requests pipeline) inserts
     into `pm_pipeline.runs`:
     - `criteria` JSON (PMS, city, state, quantity, units_min, etc.).
     - `target_quantity`, `contacts_min` (implicit 1–3), `contacts_max`.
     - `stage='company_discovery'`, `status='active'`.

2. **Company discovery**
   - `staging_promotion_runner` and/or `lead_list_runner`:
     - Seed from NEO + `pms_subdomains`.
     - Optionally use `Lead List Agent` to discover additional companies.
     - Insert seeds and discovered companies into `company_candidates` as
       `status='validated'`.
     - Update `v_company_gap`.
     - When `companies_gap == 0`, set `stage='company_research'`.

3. **Company research**
   - `company_research_runner`:
     - For each `company_candidate`:
       - Runs Company Researcher Agent.
       - Writes `company_research` facts/signals.
     - When `v_company_research_queue` is empty, set
       `stage='contact_discovery'`.

4. **Contact research**
   - `contact_research_runner`:
     - For each company with a contact gap:
       - Runs Contact Researcher Agent.
       - Inserts validated contacts into `contact_candidates`.
     - When `v_contact_gap.contacts_min_gap_total == 0`:
       - Set `stage='done'`, `status='completed'`.
     - If `WORKER_MAX_LOOPS` is used for a specific run and a gap
       remains:
       - Set `status='needs_user_decision'`.
       - Email + UI present options to expand location, loosen PMS, or
         accept partial results.

---

## 7. Expectations & How Code Meets Them

### 7.1 Hard PMS / Units / Geo constraints

- PMS:
  - Lead List Agent treats PMS as a hard constraint in worker mode.
  - Staging and NEO seeding use `pms_detected` to only promote matching
    companies.
- Geo:
  - `criteria.city`/`state` are used in search/seed queries and passed to
    MCP tools.
- Units:
  - `units_min` can be enforced via staging (`units_estimate`) and
    company research; not all logic is fully automated yet but the data
    pipe is in place.

### 7.2 Suppression

- `v_blocked_domains` aggregates customer and recently contacted domains.
- Lead List Agent and promotion/seeding skip any domain in this view.

### 7.3 Contacts 1–3 per company

- `pm_pipeline.runs.contacts_min`/`contacts_max` default to 1–3.
- `v_contact_gap` / `v_contact_gap_per_company` compute gaps based on
  these.
- `contact_research_runner` uses `contacts_min_gap` per company to
  decide how many contacts to ask the agent for.
- Contacts are inserted as `status='validated'`, so they count toward
  closing gaps.

### 7.4 Reliability & Resilience

- Workers use leases (`worker_id`, `worker_lease_until`) and unique
  constraints to avoid double‑processing.
- Insert helpers swallow duplicate key errors gracefully (idempotent).
- `RUN_FILTER_ID` + `WORKER_MAX_LOOPS` allow bounded, targeted tests.
- Contact research has a **“needs_user_decision”** stop‑condition rather
  than looping forever when requirements can’t be met.
- Email notifications and UI action panel ensure operators are informed
  and can choose next steps.

### 7.5 Scalability

- Staging + promotion allows:
  - Fan‑out search (via Agents and/or n8n) to fill a large candidate
    pool once.
  - Fast promotion of PMS‑qualified candidates into multiple runs.
- Gap‑driven stages allow workers to process many runs in parallel while
  maintaining clear run state.
- Leases and idempotent inserts make it safe to run multiple worker
  processes for higher throughput.

---

## 8. Notes & Non‑Goals

- Legacy, Responses‑based agents (`*_legacy.py`) have been removed from
  this repo; the Agents SDK–based implementations described here are
  the **canonical** and only supported versions.
- Orchestration for creating `pm_pipeline.runs` and `search_runs` (e.g.,
  from `enrichment_requests`) lives outside this repo and should treat
  this system as the async execution engine.
- Sequence Enroller is left as is; it interacts with HubSpot sequences
  but does not participate in the lead‑list pipeline described above.

