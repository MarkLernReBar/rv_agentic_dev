# RentVine Agentic Lead List System – Overview

This repo contains the **RentVine AI Agent** Streamlit app and the
backing async lead‑list pipeline. All agents are implemented with the
OpenAI Agents SDK using GPT‑5 models.

For the canonical, detailed architecture and flow description, see:

- `IMPORTANT_DOCS/rv_agentic_system_overview.md`

## Agents (UI)

- **Company Researcher** – ICP company analysis, PMS/units, outreach
  suggestions. Can pin notes to HubSpot.
- **Contact Researcher** – decision‑maker discovery and enrichment. Can
  pin notes to HubSpot.
- **Lead List Generator** – primarily a **run status** and monitoring
  view for async `pm_pipeline.runs`, including progress and user
  decisions for partial results.
- **Sequence Enroller** – lists/searches HubSpot sequences and enrolls
  contacts with two‑step confirmations.

## Running the app

- Python 3.11+ / 3.12+
- Install dependencies:
  - `uv sync` **or**
  - `pip install -r requirements.txt`
- **Run database migrations** (first time setup):
  - `psql $POSTGRES_URL -f sql/migrations/001_pm_pipeline_schema.sql`
  - `psql $POSTGRES_URL -f sql/migrations/002_pm_pipeline_views.sql`
  - `psql $POSTGRES_URL -f sql/migrations/003_worker_heartbeats.sql`
- Start Streamlit:
  - `streamlit run app.py`

## Environment (.env.local)

Minimum for this repo:

- `OPENAI_API_KEY`
- `NEXT_PUBLIC_SUPABASE_URL`
- `SUPABASE_SERVICE_KEY` (or `NEXT_PUBLIC_SUPABASE_ANON_KEY`)
- `HUBSPOT_PRIVATE_APP_TOKEN`
- `POSTGRES_URL` or `SUPABASE_POSTGRES_URL`

Optional but recommended:

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
  `EMAIL_FROM`, `NOTIFICATION_EMAIL` – for run notifications.
- `N8N_MCP_SERVER_URL` (and related MCP settings) – for MCP tool calls
  to your n8n server.
- `HTTP_TIMEOUT` (default 20s).

## Code Map (high level)

- `app.py` – Streamlit UI, agent selection, Lead List run status.
- `src/rv_agentic/agents/..._agent.py` – Agents SDK definitions for:
  - Company, Contact, Lead List, Sequence Enroller.
- `src/rv_agentic/workers/*.py` – long‑running workers for the async
  lead‑list pipeline:
  - `lead_list_runner`, `company_research_runner`,
    `contact_research_runner`, `staging_promotion_runner`.
- `src/rv_agentic/services/supabase_client.py` – Postgres/Supabase
  helpers for research DB and `pm_pipeline.*` tables.
- `src/rv_agentic/services/hubspot_client.py` – HubSpot integration.
- `src/rv_agentic/tools/mcp_client.py` – MCP HTTP client for calling
  tools via n8n.

Refer to `IMPORTANT_DOCS/rv_agentic_system_overview.md` for full details
on the async pipeline, gap views, and how each worker and agent fits
into production user flows.
