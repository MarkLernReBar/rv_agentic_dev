# GTMAgenticAI — App Overview

Single‑page Streamlit app with four agents. Lead List Generator now only logs requests to Supabase; external orchestration is handled elsewhere.

Agents
- Company Researcher — ICP analysis with public sources; can create/pin notes in HubSpot.
- Contact Researcher — contact discovery + brief personalization; can create/pin notes in HubSpot.
- Lead List Generator — collects requirements and writes the request to Supabase (`enrichment_requests`). No in‑app orchestration.
- Sequence Enroller — lists/searches sequences and enrolls contacts with two‑step confirmation.

Run
- Python 3.11+
- `uv sync` (or `pip install -r requirements.txt`)
- `streamlit run app.py`

Environment (.env.local)
- Required: `OPENAI_API_KEY`, `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (or `NEXT_PUBLIC_SUPABASE_ANON_KEY`), `HUBSPOT_PRIVATE_APP_TOKEN`
- Optional: `AGENTS_READ_ONLY=1`, `HTTP_TIMEOUT=20`

Lead List Generator → Supabase
- Inserts a row into `enrichment_requests` with:
  - `request` (jsonb) including: `batch_id`, `natural_request`, `notify_email`, `parameters`, `source`
  - `request_status`: `staged` (no email yet) or `queued` (email provided)
- Table/path can be customized with:
  - `SUPABASE_ENRICHMENT_REQUESTS_TABLE` (default `enrichment_requests`)
  - `SUPABASE_ENRICHMENT_REQUESTS_PATH` (custom PostgREST path)
- A “Test Supabase Logging” button is available in the sidebar System Status.

Code Map
- `app.py` — Streamlit UI and agent switcher; sidebar actions.
- `company_researcher.py` — company analysis; HubSpot note create/pin.
- `contact_researcher.py` — contact research; HubSpot note create/pin.
- `lead_list_generator.py` — parameter parsing; Supabase logging (no orchestration).
- `sequence_enroller.py` — sequences list/search/enroll (two‑step confirmations).
- `supabase_client.py` — REST helpers (`insert_enrichment_request`, `find_company`, `find_contact`, etc.).
- `hubspot_client.py` — HubSpot CRM + sequences helpers.
- `utils.py` — small helpers (domain extraction/validation, etc.).
- `narpm_client.py` — NARPM membership helper (used by Company Researcher).

Notes
- Orchestrator code was removed from this repo. Lead generation execution now happens in a separate codebase. This app’s Lead List Generator only records the request for external processing.
- Keep `.env.local` out of version control.
