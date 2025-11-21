import os
import re
import sys
import time
from datetime import datetime
import json

import streamlit as st

# Ensure the local `src` directory is on sys.path so that the
# running app uses the in-repo rv_agentic code (including fixes)
# instead of any previously installed wheel.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from rv_agentic.agents.company_researcher_agent import create_company_researcher_agent
from rv_agentic.agents.contact_researcher_agent import create_contact_researcher_agent
from rv_agentic.agents.lead_list_agent import create_lead_list_agent
from rv_agentic.agents.sequence_enroller_agent import create_sequence_enroller_agent
from rv_agentic.config.settings import get_settings
from rv_agentic.services import supabase_client as _sb
from rv_agentic.services.openai_provider import run_agent_sync, run_agent_with_streaming
from rv_agentic import orchestrator
from rv_agentic.services.hubspot_client import (
    HubSpotError as HS_E,
    associate_note_to_company as hs_assoc_note_company,
    associate_note_to_contact as hs_assoc_note_contact,
    create_company as hs_create_company,
    create_contact as hs_create_contact,
    create_note as hs_create_note,
    delete_note as hs_delete_note,
    pin_note_on_company as hs_pin_note_company,
    pin_note_on_contact as hs_pin_note_contact,
    search_company_by_domain as hs_search_company,
    search_contact as hs_search_contact,
)
from rv_agentic.services.utils import (
    extract_domain_from_url,
    normalize_domain,
    validate_domain,
)


def _load_env_files() -> None:
    """Load environment variables from .env.local and .env if present.
    Does not override variables already present in the environment.
    """
    import pathlib

    def _parse_and_set(path: str) -> None:
        p = pathlib.Path(path)
        if not p.exists():
            return
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # Support optional leading 'export '
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Strip surrounding quotes if present
                if (val.startswith("\"") and val.endswith("\"")) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                # Do not override existing env values
                if key and key not in os.environ:
                    os.environ[key] = val
        except Exception:
            # Fail open: ignore parse errors silently to avoid breaking app launch
            pass

    base_dir = os.path.dirname(os.path.abspath(__file__))
    _parse_and_set(os.path.join(base_dir, ".env.local"))
    _parse_and_set(os.path.join(base_dir, ".env"))


# Load env vars before creating any clients
_load_env_files()

# Initialize typed settings early so misconfig is visible
_settings = get_settings()

# Configure page
st.set_page_config(
    page_title="RentVine AI Agent",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agents" not in st.session_state:
    # All agents are now backed by the OpenAI Agents SDK.
    st.session_state.agents = {
        "Company Researcher": create_company_researcher_agent(),
        "Contact Researcher": create_contact_researcher_agent(),
        "Lead List Generator": create_lead_list_agent(),
        "Sequence Enroller": create_sequence_enroller_agent(),
    }
if "current_agent" not in st.session_state:
    st.session_state.current_agent = "Company Researcher"
if "company_create_if_missing" not in st.session_state:
    st.session_state.company_create_if_missing = True
if "contact_create_if_missing" not in st.session_state:
    st.session_state.contact_create_if_missing = True

# Support agent switching via URL: /?agent=Lead%20List%20Generator&prompt=...
try:
    qp = st.query_params  # modern API
    qp_dict = dict(qp)
except Exception:
    qp_dict = st.experimental_get_query_params()
agent_param = None
prompt_param = None
if qp_dict:
    agent_param = (
        qp_dict.get("agent") if isinstance(qp_dict.get("agent"), str) else (qp_dict.get("agent", [None])[0])
    )
    prompt_param = (
        qp_dict.get("prompt") if isinstance(qp_dict.get("prompt"), str) else (qp_dict.get("prompt", [None])[0])
    )
if agent_param and agent_param in ["Company Researcher", "Contact Researcher", "Lead List Generator", "Sequence Enroller"]:
    st.session_state.current_agent = agent_param
    if prompt_param:
        st.session_state.quick_prompt = prompt_param
    try:
        # Clear query params
        st.experimental_set_query_params()
    except Exception:
        pass
    st.rerun()

# Sidebar
with st.sidebar:
    # Add RentVine logo at the top
    try:
        # Use the standalone RentVine logo (no laptop)
        st.image("rentvine_logo.svg", width=200)
        st.markdown("### AI Agents")
    except Exception:
        # Fallback to text if logo fails to load
        st.title("🏠 RentVine AI Agents")

    st.markdown("---")

    # Initialize view_mode if not present
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "Agents"

    # Dashboard button (shown first for quick access)
    is_dashboard_active = st.session_state.view_mode == "Dashboard"
    if is_dashboard_active:
        st.markdown(
            """
            <div style="
                background-color: #d4edda;
                border: 2px solid #28a745;
                border-radius: 8px;
                padding: 8px;
                margin-bottom: 8px;
                text-align: center;
                font-weight: bold;
                color: #155724;
            ">
                📊 Dashboard ✓ (Active)
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        if st.button("📊 Dashboard", use_container_width=True, key="btn_dashboard"):
            st.session_state.view_mode = "Dashboard"
            st.rerun()

    # Agent buttons
    agents = {
        "Company Researcher": "🔍",
        "Contact Researcher": "👤",
        "Lead List Generator": "📋",
        "Sequence Enroller": "📧",
    }

    # Agent selection buttons
    for agent_name, icon in agents.items():
        # Check if this is the active agent
        is_active = st.session_state.current_agent == agent_name and st.session_state.view_mode == "Agents"

        # Create button with conditional styling
        if is_active:
            # Active agent with green background and checkmark
            st.markdown(
                f"""
                <div style="
                    background-color: #d4edda;
                    border: 2px solid #28a745;
                    border-radius: 8px;
                    padding: 8px;
                    margin-bottom: 8px;
                    text-align: center;
                    font-weight: bold;
                    color: #155724;
                ">
                    {icon} {agent_name} ✓ (Active)
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            # Regular button for inactive agents
            if st.button(
                f"{icon} {agent_name}",
                use_container_width=True,
                key=f"btn_{agent_name}",
            ):
                st.session_state.current_agent = agent_name
                st.session_state.view_mode = "Agents"
                st.session_state.messages = []  # Clear messages when switching agents
                st.rerun()

    st.markdown("---")

    # Quick Actions (only show in Agents view)
    if st.session_state.view_mode == "Agents":
        st.subheader("Quick Actions")
        if st.button("🔄 New Session", use_container_width=True):
            st.session_state.messages = []
            # Agents persist across sessions - no need to recreate
            st.rerun()
        if st.button("🔁 Reload Agents", use_container_width=True, help="Recreate agent objects to pick up latest code changes"):
            try:
                del st.session_state["agents"]
            except Exception:
                pass
            st.session_state.messages = []
            st.rerun()
        # Removed: HubSpot Sequences quick actions and owner input.
        # Natural language requests to view sequences are handled by the Sequence Enroller agent.
        st.markdown("---")

        # Current agent info
        st.subheader("Current Agent")
        current_icon = agents.get(st.session_state.current_agent, "🤖")
        st.write(f"**Active:** {current_icon} {st.session_state.current_agent}")

    # Display agent configuration
    with st.expander("🔧 System Status", expanded=False):
        st.write("**Model family:** GPT-5")
        # HubSpot owners fallback hint
        owners_fallback = bool(os.getenv("HUBSPOT_OWNER_USER_IDS"))
        st.write(
            f"**HubSpot Owner Aggregation Fallback:** {'configured' if owners_fallback else 'not configured'}"
        )
        # Service/config diagnostics (no secrets)
        serper_ok = bool(os.getenv("SERPER_API_KEY"))
        supabase_ok = bool(os.getenv("NEXT_PUBLIC_SUPABASE_URL") and (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")))
        openai_ok = bool(os.getenv("OPENAI_API_KEY"))
        hubspot_ok = bool(os.getenv("HUBSPOT_PRIVATE_APP_TOKEN") or os.getenv("HUBSPOT_TOKEN"))
        mcp_ok = bool(os.getenv("N8N_MCP_SERVER_URL"))
        st.write(f"**OpenAI:** {'configured' if openai_ok else 'not configured'}")
        st.write(f"**HubSpot:** {'configured' if hubspot_ok else 'not configured'}")
        st.write(f"**NEO Research Database:** {'configured' if supabase_ok else 'not configured'}")
        st.write(f"**Serper (Precision):** {'configured' if serper_ok else 'not configured'}")
        st.write(f"**MCP (n8n):** {'configured' if mcp_ok else 'not configured'}")
        # Removed: Default Owner Email display (not relevant to this app)
        # HubSpot Note round-trip test (create -> delete)
        if st.button("🧪 Test HubSpot Note (create + delete)"):
            with st.status(
                "Testing HubSpot note create/delete...", state="running", expanded=True
            ) as stat:
                try:
                    test_html = (
                        f"<p>RV test note at {datetime.utcnow().isoformat()}.</p>"
                    )
                    note = hs_create_note(test_html)
                    nid = note.get("id")
                    st.write(f"Created note id={nid}")
                    if nid:
                        hs_delete_note(nid)
                        st.write("Deleted test note.")
                    stat.update(label="✅ HubSpot note test passed", state="complete")
                except HS_E as e:
                    stat.update(label="❌ HubSpot note test failed", state="error")
                    st.error(str(e))

        # Supabase insert smoke test for pm_pipeline.runs
        if st.button("🧪 Test pm_pipeline runs insert"):
            with st.status("Testing pm_pipeline.runs insert...", state="running", expanded=True) as stat:
                try:
                    test_criteria = {
                        "natural_request": "TEST: please ignore",
                        "source": "ui_smoke_test",
                    }
                    row = _sb.create_pm_run(
                        criteria=test_criteria,
                        target_quantity=1,
                        notes="smoke test from Streamlit UI",
                    )
                    rid = row.get("id") if isinstance(row, dict) else None
                    stat.update(label="✅ pm_pipeline.runs insert ok", state="complete")
                    st.success(f"Inserted run id={rid}")
                except Exception as e:
                    stat.update(label="❌ pm_pipeline.runs insert failed", state="error")
                    st.error(str(e))

        # Worker Health Monitoring (for ops/admin)
        st.markdown("---")
        st.markdown("#### Worker Health")

        if st.button("🔄 Refresh Worker Status", key="sidebar_refresh_workers"):
            st.rerun()

        try:
            from rv_agentic.services import heartbeat
            health = heartbeat.get_worker_health_summary()

            # Overall health metrics
            col_active, col_dead, col_status = st.columns(3)
            with col_active:
                st.metric("Active", health.get("total_active_workers", 0))
            with col_dead:
                st.metric("Dead", health.get("total_dead_workers", 0))
            with col_status:
                health_status = health.get("health_status", "unknown")
                status_display = {
                    "healthy": "✅",
                    "no_workers": "🔵",
                    "unknown": "⚪"
                }
                st.metric("Status", status_display.get(health_status, "⚪"))

            # Worker stats by type
            stats_by_type = health.get("stats_by_type", [])
            if stats_by_type:
                import pandas as pd
                df = pd.DataFrame(stats_by_type)
                if not df.empty:
                    df_display = df.rename(columns={
                        "worker_type": "Type",
                        "active_workers": "Active",
                        "dead_workers": "Dead"
                    })
                    cols_to_show = ["Type", "Active", "Dead"]
                    df_display = df_display[[col for col in cols_to_show if col in df_display.columns]]
                    st.dataframe(df_display, use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Failed to load worker health: {e}")

    # (Removed sidebar pin buttons; actions live under the chat)

# Dashboard view
if st.session_state.get("view_mode") == "Dashboard":
    st.title("📊 Dashboard")

    # Check for runs needing user attention
    try:
        recent_runs = _sb.get_active_and_recent_runs(limit=20)
        runs_needing_attention = [
            r for r in recent_runs
            if str(r.get("status")) == "needs_user_decision"
        ]
        completed_runs = [
            r for r in recent_runs
            if str(r.get("status")) == "completed" and str(r.get("stage")) == "done"
        ]

        if runs_needing_attention:
            st.markdown("### ⚠️ Attention Required")
            st.markdown(f"**{len(runs_needing_attention)} lead list run(s) need your decision**")

            for run in runs_needing_attention[:5]:  # Show max 5 on dashboard
                rid = str(run.get("id"))
                created_at = run.get("created_at", "")
                criteria = run.get("criteria", {})
                target_qty = run.get("target_quantity", "?")

                # Extract criteria for display
                cities = criteria.get("cities", [])
                state = criteria.get("state", "")
                pms = criteria.get("pms", [])

                location_str = ", ".join(cities) if cities else state if state else "Various"
                pms_str = ", ".join(pms) if pms else "Any"

                with st.container():
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**Run:** `{rid[:13]}...`")
                        st.markdown(f"*{target_qty} companies • {location_str} • PMS: {pms_str}*")
                        st.caption(f"Created: {created_at}")
                    with col2:
                        if st.button("View & Decide", key=f"dashboard_goto_{rid}", use_container_width=True):
                            # Navigate to Lead List Generator
                            st.session_state.current_agent = "Lead List Generator"
                            st.session_state.view_mode = "Agents"
                            st.rerun()
                    st.markdown("---")

        # Highlight completed runs with downloadable CSVs
        if completed_runs:
            st.markdown("### ✅ New Lead Lists Ready to Download")
            st.markdown(
                "The following lead list run(s) have completed and are ready for CSV download "
                "from the **Lead List Generator → Active & Recent Runs** section."
            )

            for run in completed_runs[:5]:  # Show max 5 on dashboard
                rid = str(run.get("id"))
                created_at = run.get("created_at", "")
                criteria = run.get("criteria", {})
                target_qty = run.get("target_quantity", "?")

                cities = criteria.get("cities", [])
                state = criteria.get("state", "")
                pms = criteria.get("pms") or criteria.get("pms_required") or ""

                location_str = ", ".join(cities) if cities else state if state else "Various"
                pms_str = pms if isinstance(pms, str) and pms else ", ".join(pms) if isinstance(pms, list) and pms else "Any"

                with st.container():
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.markdown(f"**Run:** `{rid[:13]}...` (completed)")
                        st.markdown(f"*{target_qty} companies • {location_str} • PMS: {pms_str}*")
                        st.caption(f"Completed: {created_at}")
                    with col2:
                        if st.button("View Downloads", key=f"dashboard_download_{rid}", use_container_width=True):
                            # Navigate to Lead List Generator and focus this run's downloads
                            st.session_state.current_agent = "Lead List Generator"
                            st.session_state.view_mode = "Agents"
                            st.session_state["focus_run_id"] = rid
                            st.rerun()
                    st.markdown("---")
    except Exception as e:
        st.warning(f"Could not load run alerts: {e}")

    # Focus Account Metrics section
    try:
        metrics = _sb.get_focus_account_metrics()
        if not metrics:
            st.info("No focus account metrics found.")
        else:
            # Ensure only the desired columns are shown and visible
            cols = ["owner_email", "role", "target", "current", "gap", "status"]
            # Normalize rows to only these keys
            cleaned = [{k: row.get(k) for k in cols} for row in metrics]

            # Lightweight summary strip across the top
            owners = {row.get("owner_email") for row in cleaned if row.get("owner_email")}

            def _safe_int(v: object) -> int:
                try:
                    return int(v) if v is not None else 0
                except (ValueError, TypeError):
                    return 0

            total_target = sum(_safe_int(row.get("target")) for row in cleaned)
            total_current = sum(_safe_int(row.get("current")) for row in cleaned)
            total_gap = sum(_safe_int(row.get("gap")) for row in cleaned)

            st.subheader("Focus Account Metrics")
            st.markdown("Overall pipeline targets by owner and role.")

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Owners", len(owners))
            with c2:
                st.metric("Total Target", total_target)
            with c3:
                st.metric("Total Current", total_current)
            with c4:
                st.metric("Total Gap", total_gap)

            # Main table: lightly styled, readable summary with no scroll
            try:
                import pandas as pd

                df = pd.DataFrame(cleaned)
                # Rename columns for readability
                df = df.rename(
                    columns={
                        "owner_email": "Owner",
                        "role": "Role",
                        "target": "Target",
                        "current": "Current",
                        "gap": "Gap",
                        "status": "Status",
                    }
                )
                # Sort by largest gap first to surface priorities
                if "Gap" in df.columns:
                    df = df.sort_values(by="Gap", ascending=False)

                # Convert to records and render a simple HTML table so we can
                # avoid the numeric index column and automatic email links.
                records = df.to_dict(orient="records")
                headers = ["Owner", "Role", "Target", "Current", "Gap", "Status"]

                def _esc(val: object) -> str:
                    import html as _html

                    s = "" if val is None else str(val)
                    # Insert a zero-width space before '@' to prevent email
                    # auto-linking while keeping the visual text unchanged.
                    s = s.replace("@", "@\u200b")
                    return _html.escape(s)

                rows_html = []
                for row in records:
                    cells = "".join(f"<td>{_esc(row.get(h))}</td>" for h in headers)
                    rows_html.append(f"<tr>{cells}</tr>")

                header_html = "".join(f"<th>{h}</th>" for h in headers)
                table_html = f"""
                <table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
                  <thead>
                    <tr>{header_html}</tr>
                  </thead>
                  <tbody>
                    {''.join(rows_html)}
                  </tbody>
                </table>
                """
                st.markdown(table_html, unsafe_allow_html=True)
            except Exception:
                # Fallback to simple table if pandas styling fails
                st.table(cleaned)
    except Exception as e:
        st.error(f"Error loading dashboard metrics: {e}")
    # Skip the rest of the chat UI
    st.stop()

# Main chat interface
current_icon = agents.get(st.session_state.current_agent, "🤖")
st.title(f"{current_icon} {st.session_state.current_agent}")

# Agent descriptions
agent_descriptions = {
    "Company Researcher": "*Specialized in deep company analysis, ICP qualification, and technology stack research*",
    "Contact Researcher": "*Expert at finding key contacts, decision makers, and contact information*",
    "Lead List Generator": "*Focused on building targeted prospect lists based on your criteria*",
    "Sequence Enroller": "*Handles automated enrollment and outreach sequence management*",
}

st.markdown(agent_descriptions.get(st.session_state.current_agent, "*AI Assistant*"))

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and message.get("type") == "analysis":
            # Special formatting for ICP analysis
            st.markdown(message["content"])
        else:
            st.markdown(message["content"])

# Lead list run status helper (for pm_pipeline runs)
if st.session_state.current_agent == "Lead List Generator":
    st.markdown("---")
    st.subheader("Active & Recent Runs")

    # Auto-fetch active and recent runs
    try:
        # Fetch runs from Supabase and hide internal/test runs from the UI.
        raw_runs = _sb.get_active_and_recent_runs(limit=20)
        runs: list[dict[str, object]] = []
        for r in raw_runs:
            criteria = r.get("criteria") or {}
            try:
                is_test = bool(criteria.get("test_run")) if isinstance(criteria, dict) else False
            except Exception:
                is_test = False
            if not is_test:
                runs.append(r)

        if not runs:
            st.info("No active or recent runs found. Submit a new lead list request to get started!")
        else:
            # Partition runs: attention, active, and completed.
            runs_needing_attention = []
            active_runs = []
            completed_runs = []
            for r in runs:
                status = str(r.get("status") or "")
                stage = str(r.get("stage") or "")
                # Hide archived runs from the UI completely; these are
                # typically old test runs or manually retired tasks.
                if status == "archived":
                    continue
                if status == "needs_user_decision":
                    runs_needing_attention.append(r)
                elif status == "completed":
                    completed_runs.append(r)
                elif stage != "done":
                    active_runs.append(r)

            # Only surface the single most recent completed run to avoid
            # filling the screen with historical lists.
            completed_runs = sorted(
                completed_runs,
                key=lambda r: str(r.get("created_at") or ""),
                reverse=True,
            )[:1]

            ordered_runs = runs_needing_attention + active_runs + completed_runs

            # Alert for runs needing attention
            if runs_needing_attention:
                st.warning(f"⚠️ {len(runs_needing_attention)} run(s) need your attention")

            # Display each run
            for run in ordered_runs:
                rid = str(run.get("id"))
                run_status = str(run.get("status", "unknown"))
                run_stage = str(run.get("stage", "unknown"))
                created_at = run.get("created_at", "")

                # Determine emoji and title based on status
                if run_status == "needs_user_decision":
                    emoji = "⚠️"
                    title_suffix = "(Action Required)"
                elif run_status == "completed":
                    emoji = "✅"
                    title_suffix = "(Completed)"
                elif run_status == "error":
                    emoji = "❌"
                    title_suffix = "(Error)"
                else:
                    emoji = "🔄"
                    title_suffix = "(In Progress)"

                # Create expandable section for each run
                expanded = (
                    run_status == "needs_user_decision"
                    or rid == str(st.session_state.get("focus_run_id") or "")
                )
                with st.expander(f"{emoji} Run {rid[:8]}... {title_suffix}", expanded=expanded):
                    try:
                        # Use orchestrator.get_run_progress for enhanced progress display
                        progress = orchestrator.get_run_progress(rid)

                        st.markdown("#### Run Info")
                        st.markdown(
                            f"- **Status:** `{progress.get('status')}`\n"
                            f"- **Stage:** `{progress.get('stage')}`\n"
                            f"- **Target Quantity:** `{progress.get('target_quantity')}`\n"
                        )

                        # Surface errors/notes for quicker diagnosis
                        error_msg = progress.get("error") or run.get("error")
                        notes_msg = progress.get("notes") or run.get("notes")
                        if error_msg:
                            st.error(error_msg)
                        elif notes_msg:
                            st.info(notes_msg)

                        st.markdown("#### Progress")

                        # Company progress bar
                        companies_info = progress.get("companies", {})
                        companies_ready = companies_info.get("ready", 0)
                        companies_gap = companies_info.get("gap", 0)
                        company_progress_pct = companies_info.get("progress_pct", 0)

                        st.markdown(f"**Companies: {companies_ready} / {progress.get('target_quantity')} ({company_progress_pct}%)**")
                        st.progress(min(company_progress_pct, 100) / 100.0)
                        st.caption(f"Gap: {companies_gap} companies remaining")

                        # Contact progress bar
                        contacts_info = progress.get("contacts", {})
                        contacts_ready = contacts_info.get("ready", 0)
                        contacts_gap = contacts_info.get("gap", 0)
                        contact_progress_pct = contacts_info.get("progress_pct", 0)
                        # Derive ready if not provided but gap and target are known
                        if not contacts_ready and companies_ready and progress.get("contacts", {}):
                            try:
                                contacts_ready = max(0, companies_ready * int(progress.get("contacts_min", 1)) - int(contacts_gap or 0))
                            except Exception:
                                pass

                        st.markdown(f"**Contacts: {contacts_ready} total ({contact_progress_pct}% of minimum)**")
                        st.progress(min(contact_progress_pct, 100) / 100.0)
                        st.caption(f"Gap: {contacts_gap} contacts remaining to meet minimum")

                        # Recent audit events for visibility (rich view)
                        try:
                            events = supabase_client.fetch_audit_events(rid, limit=50)
                        except Exception:
                            events = []
                        if events:
                            st.markdown("#### Run Events (latest)")
                            # Normalize for table display
                            data = []
                            for ev in events:
                                data.append(
                                    {
                                        "time": ev.get("created_at"),
                                        "event": ev.get("event"),
                                        "entity": ev.get("entity_type"),
                                        "meta": json.dumps(ev.get("meta") or {}, ensure_ascii=False),
                                    }
                                )
                            st.dataframe(data, use_container_width=True, hide_index=True, height=min(320, 24 * (len(data) + 1)))

                        # Export CSV button when run is completed
                        if str(progress.get("status")) == "completed":
                            st.markdown("#### Export")
                            if st.button("📥 Download CSVs", use_container_width=True, key=f"btn_export_csv_{rid}"):
                                try:
                                    from rv_agentic.services import export
                                    import tempfile

                                    with st.status("Generating CSV files...", state="running") as export_stat:
                                        with tempfile.TemporaryDirectory() as tmpdir:
                                            companies_path, contacts_path = export.export_run_to_files(rid, tmpdir)

                                            # Read files for download
                                            with open(companies_path, "r") as f:
                                                companies_csv = f.read()
                                            with open(contacts_path, "r") as f:
                                                contacts_csv = f.read()

                                            export_stat.update(label="✅ CSVs generated", state="complete")

                                            # Provide download buttons
                                            col_csv1, col_csv2 = st.columns(2)
                                            with col_csv1:
                                                st.download_button(
                                                    label="📊 Download Companies CSV",
                                                    data=companies_csv,
                                                    file_name=f"companies_{rid[:8]}.csv",
                                                    mime="text/csv",
                                                    use_container_width=True,
                                                )
                                            with col_csv2:
                                                st.download_button(
                                                    label="👥 Download Contacts CSV",
                                                    data=contacts_csv,
                                                    file_name=f"contacts_{rid[:8]}.csv",
                                                    mime="text/csv",
                                                    use_container_width=True,
                                                )
                                except Exception as e:
                                    st.error(f"CSV export failed: {e}")

                        # When a run needs user decision (e.g. contact gap could not be closed),
                        # surface guidance and options.
                        if str(run.get("status")) == "needs_user_decision":
                            st.markdown("#### Action Required")
                            notes = (run.get("notes") or "").strip()
                            if notes:
                                st.info(notes)
                            st.markdown(
                                "The system could not fully satisfy the requirements for this run. "
                                "Choose how you would like to proceed:"
                            )
                            col1, col2, col3 = st.columns(3)
                            with col1:
                                expand_loc = st.button("Expand Location", key=f"btn_expand_location_{rid}")
                            with col2:
                                loosen_pms = st.button("Loosen PMS", key=f"btn_loosen_pms_{rid}")
                            with col3:
                                accept_partial = st.button("Accept Partial Results", key=f"btn_accept_partial_{rid}")

                            chosen = None
                            if expand_loc:
                                chosen = "expand_location"
                            elif loosen_pms:
                                chosen = "loosen_pms"
                            elif accept_partial:
                                chosen = "accept_partial"

                            if chosen:
                                # For now we record the choice in notes and, for the
                                # accept-partial path, mark the run as fully completed.
                                base_notes = notes or ""
                                choice_note = (
                                    f"[User decision] {chosen.replace('_', ' ')} at UI time."
                                )
                                new_notes = (base_notes + "\n\n" + choice_note).strip()
                                if chosen == "accept_partial":
                                    _sb.set_run_stage(run_id=rid, stage="done", status="completed")
                                    _sb.update_pm_run_status(run_id=rid, status="completed", error=new_notes)
                                    st.success("Marked run as completed with partial results.")
                                    st.rerun()  # Refresh to show updated status
                                else:
                                    # Keep status as needs_user_decision but capture the choice;
                                    # a follow-up process or operator can adjust criteria and resume.
                                    _sb.update_pm_run_status(
                                        run_id=rid,
                                        status="needs_user_decision",
                                        error=new_notes,
                                    )
                                    st.success("Recorded your preference; please adjust criteria and resume the run.")

                    except Exception as e:
                        st.error(f"Failed to display run details: {e}")

    except Exception as e:
        st.error(f"Failed to load runs: {e}")

# Contextual HubSpot pin/create actions under the most recent assistant summary
last_assistant_msg = next(
    (m for m in reversed(st.session_state.messages) if m.get("role") == "assistant"),
    None,
)
last_user_msg = next(
    (m for m in reversed(st.session_state.messages) if m.get("role") == "user"), None
)
assistant_content_raw = last_assistant_msg.get("content") if last_assistant_msg else ""

# Try to unwrap assistant content if it is JSON-shaped (Company/Contact agents sometimes return structured data)
def _unwrap_assistant_content_for_actions(text: str) -> str:
    import json as _json
    s = text or ""
    try:
        data = _json.loads(s)
        # Array format: [{"output": "..."}]
        if isinstance(data, list) and data and isinstance(data[0], dict) and "output" in data[0]:
            return "\n\n".join(str(item.get("output", "")) for item in data)
        # Dict format: {"markdown": "..."}
        if isinstance(data, dict):
            if "markdown" in data:
                return str(data.get("markdown", ""))
            if "content" in data:
                return str(data.get("content", ""))
            if "output" in data:
                return str(data.get("output", ""))
    except Exception:
        pass
    return s

assistant_content = _unwrap_assistant_content_for_actions(assistant_content_raw)
has_assistant_content = bool(assistant_content and str(assistant_content).strip())
# Quick jump link to HubSpot actions for researchers
if has_assistant_content and st.session_state.current_agent in [
    "Company Researcher",
    "Contact Researcher",
]:
    st.markdown("[Jump to HubSpot Actions](#hubspot-actions)")
if has_assistant_content and st.session_state.current_agent in [
    "Company Researcher",
    "Contact Researcher",
]:
    st.markdown("---")
    # Anchor target for jump link
    st.markdown('<a id="hubspot-actions"></a>', unsafe_allow_html=True)
    st.subheader("HubSpot Actions")
    last_user_text = last_user_msg.get("content") if last_user_msg else ""
    default_identifier = last_user_text.strip()
    if st.session_state.current_agent == "Company Researcher":
        # Derive a sensible default domain
        candidate = normalize_domain(default_identifier)
        if not validate_domain(candidate):
            # Try to extract from last assistant content (e.g., Website: https://...)
            content = assistant_content or ""
            m_url = re.search(r"https?://[^\s)]+", content)
            if m_url:
                candidate = extract_domain_from_url(m_url.group(0))
        pin_id = st.text_input(
            "Company domain", value=candidate, key="hs_pin_company_domain"
        )
        # Check existence in HubSpot
        company_id = None
        try:
            if pin_id:
                hs = hs_search_company(pin_id)
                company_id = hs and hs.get("id")
        except HS_E:
            st.warning("HubSpot search failed. Check token/scopes.")
        if company_id:
            st.success(f"Found company in HubSpot (id: {company_id})")
        else:
            st.info("No HubSpot company found for this domain.")
        create_if_missing = st.checkbox(
            "Create if missing",
            value=st.session_state.company_create_if_missing,
            key="company_create_if_missing",
            help="If not found by domain, create a minimal HubSpot company and pin the note",
        )
        # Target badge
        if company_id:
            st.caption(f"Target company: `{pin_id}` — HubSpot id: `{company_id}`")
        else:
            st.caption(
                f"Target company: `{pin_id}` — "
                + ("will create" if create_if_missing else "not found; will not create")
            )
        if st.button(
            "📌 Pin Note to Company",
            use_container_width=True,
            key="btn_append_company",
            disabled=not has_assistant_content,
        ):
            with st.status(
                "⚠️ Pinning note to HubSpot company...", state="running", expanded=True
            ) as stat:
                try:
                    cid = company_id
                    if not cid and create_if_missing:
                        props = {"domain": pin_id}
                        if validate_domain(pin_id):
                            props["name"] = pin_id
                        created = hs_create_company(props)
                        cid = created.get("id")
                    if not cid:
                        raise Exception(
                            "Company not found. Enable 'Create if missing' to create and append."
                        )
                    content_md = assistant_content or ""
                    note_html = "<div>" + content_md.replace("\n", "<br/>") + "</div>"
                    note = hs_create_note(note_html)
                    nid = note.get("id")
                    if nid:
                        hs_assoc_note_company(str(nid), str(cid))
                        try:
                            hs_pin_note_company(str(cid), str(nid))
                        except HS_E:
                            pass
                    stat.update(label="✅ Complete", state="complete")
                    st.success(f"Pinned note id={nid} to company {cid}")
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": f"Pinned note id={nid} to company {cid}",
                        }
                    )
                except Exception as e:
                    stat.update(label="❌ Error", state="error")
                    st.error(str(e))
    else:
        # Derive sensible default email
        email_candidate = None
        # First from last user message
        m = re.search(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", last_user_text or ""
        )
        if m:
            email_candidate = m.group(0)
        else:
            # Try assistant content
            m2 = re.search(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                assistant_content or "",
            )
            email_candidate = m2.group(0) if m2 else ""
        pin_id = st.text_input(
            "Contact email", value=email_candidate or "", key="hs_pin_contact_email"
        )
        contact_id = None
        try:
            if pin_id and "@" in pin_id:
                hs = hs_search_contact(email=pin_id)
                contact_id = hs and hs.get("id")
        except HS_E:
            st.warning("HubSpot search failed. Check token/scopes.")
        if contact_id:
            st.success(f"Found contact in HubSpot (id: {contact_id})")
        else:
            st.info("No HubSpot contact found for this email.")
        create_if_missing = st.checkbox(
            "Create if missing",
            value=st.session_state.contact_create_if_missing,
            key="contact_create_if_missing",
            help="If not found by email, create a minimal HubSpot contact and pin the note",
        )
        # Target badge
        if contact_id:
            st.caption(f"Target contact: `{pin_id}` — HubSpot id: `{contact_id}`")
        else:
            st.caption(
                f"Target contact: `{pin_id}` — "
                + ("will create" if create_if_missing else "not found; will not create")
            )
        if st.button(
            "📌 Pin Note to Contact",
            use_container_width=True,
            key="btn_append_contact",
            disabled=not has_assistant_content,
        ):
            with st.status(
                "⚠️ Pinning note to HubSpot contact...", state="running", expanded=True
            ) as stat:
                try:
                    cid = contact_id
                    if not cid and create_if_missing:
                        created = hs_create_contact({"email": pin_id})
                        cid = created.get("id")
                    if not cid:
                        raise Exception(
                            "Contact not found. Enable 'Create if missing' to create and append."
                        )
                    content_md = assistant_content or ""
                    note_html = "<div>" + content_md.replace("\n", "<br/>") + "</div>"
                    note = hs_create_note(note_html)
                    nid = note.get("id")
                    if nid:
                        hs_assoc_note_contact(str(nid), str(cid))
                        try:
                            hs_pin_note_contact(str(cid), str(nid))
                        except HS_E:
                            pass
                    stat.update(label="✅ Complete", state="complete")
                    st.success(f"Pinned note id={nid} to contact {cid}")
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": f"Pinned note id={nid} to contact {cid}",
                        }
                    )
                except Exception as e:
                    stat.update(label="❌ Error", state="error")
                    st.error(str(e))


# Add a convenient jump link under the latest assistant message (Sequence Enroller)
if has_assistant_content and st.session_state.current_agent == "Sequence Enroller":
    st.markdown("[Jump to Sequence Actions](#sequence-actions)")

# Sequence actions (Email Copy) when using Sequence Enroller — show only after an assistant result
if has_assistant_content and st.session_state.current_agent == "Sequence Enroller":
    st.markdown("---")
    # Anchor target for jump link
    st.markdown('<a id="sequence-actions"></a>', unsafe_allow_html=True)
    st.subheader("Sequence Actions")
    # Try to extract sequenceId from last assistant content or last user prompt
    seq_id_candidate = None
    owner_email_candidate = None
    if assistant_content:
        m_sid = re.search(r"\bid[:\s]*([0-9]{3,})\b", assistant_content)
        if m_sid:
            seq_id_candidate = m_sid.group(1)
    if last_user_msg and last_user_msg.get("content"):
        m_sid2 = re.search(r"sequence[_\s-]?id[:\s]*([0-9]+)", last_user_msg["content"], re.IGNORECASE)
        if m_sid2:
            seq_id_candidate = seq_id_candidate or m_sid2.group(1)
        m_owner = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", last_user_msg["content"]) 
        if m_owner:
            owner_email_candidate = m_owner.group(1)
    col_a, col_b, col_c = st.columns([2,2,2])
    with col_a:
        seq_input = st.text_input("Sequence ID", value=seq_id_candidate or "", key="seq_actions_seqid")
    with col_b:
        owner_input = st.text_input("Owner Email", value=owner_email_candidate or "", key="seq_actions_owner")
    with col_c:
        step_input = st.number_input("Step (optional)", min_value=0, max_value=100, value=0, step=1, help="0 = all steps")
    col_opts = st.columns([1,1,1,1])
    with col_opts[0]:
        mask_tokens = st.checkbox("Mask tokens", value=True, help="Replace {{...}}/[[...]]/%...% with [[TOKEN]]")
    with col_opts[1]:
        include_html = st.checkbox("Include HTML", value=False)
    with col_opts[2]:
        full_body = st.checkbox("Full body", value=False, help="Show entire body; otherwise show a truncated preview")
    with col_opts[3]:
        export_ready = bool(assistant_content and assistant_content.strip().startswith("## Email Copy —"))
        if export_ready:
            st.download_button("Export .md", (assistant_content or "").encode("utf-8"), file_name="sequence_email_copy.md")
        else:
            st.write("\n")
    if st.button("📧 Show Email Copy", use_container_width=True, disabled=not (seq_input)):
        cmd = f"show email copy sequenceId: {seq_input} owner_email: {owner_input}"
        if mask_tokens:
            cmd += " tokens: masked"
        if include_html:
            cmd += " include_html: true"
        if full_body:
            cmd += " full: true"
        if step_input and int(step_input) > 0:
            cmd += f" step: {int(step_input)}"
        st.session_state.quick_prompt = cmd
        st.rerun()

def _process_prompt(prompt: str):
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Display user message
    with st.chat_message("user"):
        st.markdown(prompt)

    # Process with agent
    with st.chat_message("assistant"):
        try:
            # Place the loader first so it stays above the streamed response
            status_anchor = st.container()
            content_placeholder = st.empty()
            content_buffer = ""

            with status_anchor:
                with st.status(
                    "🤖 Working...", state="running", expanded=True
                ) as status:
                    status_container = st.container()
                    last_status_time = 0.0
                    last_content_time = 0.0

                    def stream_callback(content: str):
                        # Route emoji-prefixed status lines to st.status; send everything else to the main content buffer.
                        status_prefixes = ("🔍", "🌐", "📋", "🧭", "🧩", "✍️", "🔎", "📤", "⚠️", "✅", "👤", "🚚", "🗄️", "•", "📊", "🔧")
                        nonlocal content_buffer, last_content_time, last_status_time
                        text = str(content)
                        # Process chunk line-by-line so multi-line status chunks are routed correctly
                        for line in text.splitlines(True):
                            stripped = line.strip("\n")
                            if not stripped:
                                content_buffer += line
                                continue
                            # Allow leading whitespace before emoji
                            lstripped = stripped.lstrip()
                            # Treat both plain emoji-prefixed lines and bullet-emoji lines as status updates
                            starts_with_status = any(lstripped.startswith(p) for p in status_prefixes)
                            bullet_emoji_status = any(
                                re.match(rf"^\s*[-*]\s*{re.escape(p)}", stripped) for p in status_prefixes
                            )
                            is_heading = bool(re.match(r"^\s*#{1,6}\s+", stripped))
                            is_status = (starts_with_status or bullet_emoji_status) and not is_heading
                            if is_status:
                                gap = time.time() - last_status_time
                                if gap < 0.08:
                                    time.sleep(0.08 - gap)
                                # Strip any leading bullet when displaying inside the status container
                                display_text = re.sub(r"^\s*[-*]\s*", "", lstripped)
                                status_container.markdown(display_text)
                                last_status_time = time.time()
                            else:
                                content_buffer += line
                                if (time.time() - last_content_time) >= 0.03:
                                    content_placeholder.markdown(content_buffer)
                                    last_content_time = time.time()

                    # Initial line
                    status_container.markdown(
                        f"🧭 Processing request with {st.session_state.current_agent}…"
                    )

                    current_agent_name = st.session_state.current_agent
                    current_agent = st.session_state.agents[current_agent_name]

                    # Lead List Generator: use Agents SDK + pm_pipeline.runs
                    if current_agent_name == "Lead List Generator":
                        # CRITICAL: Validate email is provided before creating run
                        notification_email = st.session_state.get("lead_list_notification_email", "")
                        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
                        email_valid = bool(notification_email and re.match(email_pattern, notification_email))

                        if not email_valid:
                            error_msg = "❌ **Error:** Please provide a valid email address before submitting a lead list request"
                            status_container.markdown(error_msg)
                            status.update(label="⚠️ Email Required", state="error")
                            content_placeholder.markdown(error_msg)
                            content_buffer = error_msg
                            st.session_state.messages.append({"role": "assistant", "content": error_msg})
                            return

                        # Create a pm_pipeline run immediately so downstream workers can process it.
                        # Extract quantity
                        requested_qty = 10  # default
                        try:
                            qty_match = re.search(r"(\d+)\s*(?:companies|accounts|leads|properties)", prompt, re.I)
                            if qty_match:
                                requested_qty = int(qty_match.group(1))
                        except Exception:
                            requested_qty = 10

                        # Extract location (city and/or state)
                        location_parts = []
                        city_match = re.search(r"in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),?\s+([A-Z]{2})", prompt)
                        if city_match:
                            location_parts.append(f"{city_match.group(1)}, {city_match.group(2)}")
                        else:
                            state_match = re.search(r"in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", prompt)
                            if state_match:
                                location_parts.append(state_match.group(1))

                        # Extract units requirement
                        units_req = None
                        units_match = re.search(r"(\d+)\+?\s*units", prompt, re.I)
                        if units_match:
                            units_req = f"{units_match.group(1)}+ units"

                        # Extract PMS requirement
                        pms_req = None
                        pms_keywords = ["Buildium", "AppFolio", "Yardi", "RealPage", "Entrata", "ResMan"]
                        for pms in pms_keywords:
                            if pms.lower() in prompt.lower():
                                pms_req = pms
                                break

                        criteria = {
                            "natural_request": prompt,
                            "source": "lead_list_generator_ui",
                            "created_via": "streamlit",
                            "notification_email": notification_email,  # Store email for completion notification
                        }

                        pm_run = _sb.create_pm_run(
                            criteria=criteria,
                            target_quantity=requested_qty,
                        )
                        run_id = pm_run.get("id")

                        # Build criteria summary for display
                        criteria_items = []
                        if location_parts:
                            criteria_items.append(f"**Location:** {', '.join(location_parts)}")
                        if units_req:
                            criteria_items.append(f"**Units:** {units_req}")
                        if pms_req:
                            criteria_items.append(f"**PMS:** {pms_req}")

                        criteria_summary = "\n- ".join(criteria_items) if criteria_items else "No specific criteria detected"

                        # Let the Lead List Agent generate a traceable confirmation,
                        # but always show a deterministic, human-friendly summary in the UI.
                        fallback_response = (
                            "### ✅ Lead List Request Queued\n\n"
                            f"- **Run ID:** `{run_id}`\n"
                            f"- **Requested Quantity:** {requested_qty} companies\n\n"
                            "**Criteria:**\n"
                            f"- {criteria_summary}\n\n"
                            "Your lead list will be generated asynchronously. Use the **Run ID** above to check status."
                        )
                        try:
                            agent_prompt = (
                                f"User lead list request: {prompt}\n\n"
                                f"A backend worker will fulfill run id '{run_id}'. "
                                f"Criteria: {criteria_summary}\n"
                                "Summarize the request parameters clearly for the user and confirm "
                                "that the list will be generated asynchronously."
                            )
                            # We run the agent mainly for traces/observability; UI uses fallback text.
                            _ = run_agent_sync(current_agent, agent_prompt)
                            response = fallback_response
                        except Exception:
                            response = fallback_response

                        # Add backend run metadata to the status stream
                        if run_id:
                            stream_callback(f"✅ Queued pm_pipeline run `{run_id}` for processing.")
                    else:
                        # Legacy agents with .research()
                        if hasattr(current_agent, "research"):
                            response = current_agent.research(prompt, stream_callback)
                        else:
                            # Agents SDK-based agents with streaming support
                            agent_name = st.session_state.current_agent
                            if agent_name in ["Company Researcher", "Contact Researcher"]:
                                # Use streaming to capture tool preambles for real-time progress updates
                                result = run_agent_with_streaming(current_agent, prompt, stream_callback)
                                response = getattr(result, "final_output", "") or ""
                            else:
                                # Other agents (Lead List, Sequence Enroller) without streaming
                                result = run_agent_sync(current_agent, prompt)
                                response = getattr(result, "final_output", "") or ""

                    # Normalize non-string responses (e.g., Pydantic models) into text
                    # so downstream markdown/JSON handling is consistent.
                    if not isinstance(response, str):
                        try:
                            # Prefer Pydantic-style JSON when available
                            if hasattr(response, "model_dump_json"):
                                response = response.model_dump_json()
                            else:
                                response = str(response)
                        except Exception:
                            response = str(response)

                    # Final render with minimal newline-after-headings pass outside code fences.
                    # If nothing was streamed as content, fall back to the agent's return value
                    final_render = content_buffer or (response or "")
                    # Unwrap optional JSON outputs: [{"output": "..."}] or {"markdown": "...", "search_details": {...}}
                    def _unwrap_json_output(text: str) -> str:
                        import json as _json
                        try:
                            data = _json.loads(text)
                            # Company researcher array format
                            if isinstance(data, list) and data and isinstance(data[0], dict) and "output" in data[0]:
                                return "\n\n".join(str(item.get("output", "")) for item in data)
                            # Dict formats
                            if isinstance(data, dict):
                                # Store search details in session if present
                                if data.get("search_details"):
                                    st.session_state["last_search_details"] = data.get("search_details")
                                if "markdown" in data:
                                    return str(data.get("markdown", ""))
                                if "content" in data:
                                    return str(data.get("content", ""))
                                if "output" in data:
                                    return str(data.get("output", ""))
                        except Exception:
                            pass
                        return text
                    final_render = _unwrap_json_output(final_render)
                    import re as _re
                    parts = final_render.split("```")
                    # Build a new list to avoid in-place assignment type issues with static typing
                    new_parts = []
                    for idx, part in enumerate(parts):
                        if idx % 2 == 0:  # outside code fences
                            seg = _re.sub(r"(?m)^(#{1,6}\s[^\n]+)\n(?!\n)", r"\1\n\n", part)
                            new_parts.append(seg)
                        else:
                            new_parts.append(part)
                    final_render = "```".join(new_parts)

                    # Strip any leading status-style lines (emoji-prefixed) that the
                    # model may have echoed into the main content. These belong in
                    # the status widget only.
                    def _strip_leading_status_lines(text: str) -> str:
                        status_prefixes = (
                            "🔍",
                            "🌐",
                            "📋",
                            "🧭",
                            "🧩",
                            "✍️",
                            "🔎",
                            "📤",
                            "⚠️",
                            "✅",
                            "👤",
                            "🚚",
                            "🗄️",
                            "•",
                            "📊",
                            "🔧",
                            "👥",
                        )
                        lines = text.splitlines()
                        idx = 0
                        while idx < len(lines):
                            stripped = lines[idx].lstrip()
                            if stripped and any(stripped.startswith(p) for p in status_prefixes):
                                idx += 1
                            else:
                                break
                        return "\n".join(lines[idx:]) if idx else text

                    final_render = _strip_leading_status_lines(final_render)

                    content_placeholder.markdown(final_render)
                    status.update(label="✅ Complete", state="complete")

            def _clean_markdown(md: str) -> str:
                try:
                    import re as _re

                    s = md.replace("\r\n", "\n").replace("\r", "\n")
                    # Insert a newline before any heading marker not already at line start
                    s = _re.sub(r"(?<!\n)(#{1,6}\s)", r"\n\1", s)
                    # Ensure a blank line after heading lines
                    s = _re.sub(
                        r"^(#{1,6}\s[^\n]+)\n(?!\n)", r"\1\n\n", s, flags=_re.MULTILINE
                    )
                    # Normalize horizontal rules with surrounding blank lines
                    s = _re.sub(r"(?m)^(---+)\s*$", r"\n\1\n", s)
                    # Collapse 3+ blank lines to 2
                    s = _re.sub(r"\n{3,}", "\n\n", s)
                    # Trim trailing spaces
                    s = _re.sub(r"[ \t]+$", "", s, flags=_re.MULTILINE)
                    return s.strip()
                except Exception:
                    return md

            final_response = final_render

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": final_response,
                    "type": (
                        "analysis"
                        if "## ICP Analysis" in final_response
                        else "response"
                    ),
                }
            )

            # Optional: Render Search Details panel for Contact Researcher
            if st.session_state.current_agent == "Contact Researcher":
                sd = st.session_state.get("last_search_details")
                if isinstance(sd, dict):
                    with st.expander("🔎 Search Details", expanded=False):
                        ids = sd.get("identity_queries") or []
                        pers = sd.get("personalization_queries") or []
                        tops = sd.get("top_results") or []
                        leads = sd.get("personal_leads") or []
                        if ids:
                            st.markdown("**Identity Queries**")
                            st.markdown("\n".join(f"- {q}" for q in ids if isinstance(q, str)))
                        if pers:
                            st.markdown("**Personalization Queries**")
                            st.markdown("\n".join(f"- {q}" for q in pers if isinstance(q, str)))
                        if tops:
                            st.markdown("**Top Results**")
                            st.markdown("\n".join(f"- {u}" for u in tops if isinstance(u, str)))
                        if leads:
                            st.markdown("**Personal Leads**")
                            for pl in leads[:5]:
                                if isinstance(pl, dict):
                                    txt = str(pl.get("text") or "")
                                    url = str(pl.get("url") or "")
                                    if txt and url:
                                        st.markdown(f"- {txt} — {url}")

        except Exception as e:
            error_msg = f"❌ **Error:** {str(e)}"
            st.error(error_msg)
            st.session_state.messages.append(
                {"role": "assistant", "content": error_msg}
            )
        # Trigger a rerun so downstream sections (e.g., HubSpot Actions) render using the latest assistant output
        st.rerun()


# Email validation for Lead List Generator (CRITICAL: Required before submission)
if st.session_state.current_agent == "Lead List Generator":
    st.markdown("---")
    st.markdown("### 📧 Notification Email")

    # Initialize email in session state if not present
    if "lead_list_notification_email" not in st.session_state:
        st.session_state.lead_list_notification_email = ""

    notification_email = st.text_input(
        "Email Address (Required)",
        value=st.session_state.lead_list_notification_email,
        placeholder="your@email.com",
        help="You'll receive the final CSV files at this email when the lead list completes",
        key="lead_list_email_input"
    )

    # Store email in session state
    if notification_email:
        st.session_state.lead_list_notification_email = notification_email

    # Validate email format
    import re
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    email_valid = bool(notification_email and re.match(email_pattern, notification_email))

    if not email_valid and notification_email:
        st.warning("⚠️ Please provide a valid email address")
    elif not notification_email:
        st.info("ℹ️ Please provide your email address to receive the completed lead list CSVs")

# Chat input (agent-specific placeholder)
placeholder_map = {
    "Company Researcher": "Grace Property Management, Denver Colorado",
    "Contact Researcher": "Eric Keith, Rent Now",
    "Lead List Generator": "I need 20 accounts in Texas that use Buildium for an upcoming lunch and learn",
    "Sequence Enroller": "Find the best sequence for Eric Keith, Rent Now",
}
ph = placeholder_map.get(st.session_state.current_agent, "Enter a request…")
prompt = st.chat_input(ph)
if prompt:
    _process_prompt(prompt)
elif st.session_state.get("quick_prompt"):
    qp = st.session_state.quick_prompt
    st.session_state.quick_prompt = None
    _process_prompt(qp)

# Footer
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: #666;'>"
    "RentVine AI Agents - Specialized Sales Development Powered by GPT-5"
    "</div>",
    unsafe_allow_html=True,
)

# Display example queries based on current agent
if not st.session_state.messages:
    st.markdown("### 💡 Example Queries")

    if st.session_state.current_agent == "Company Researcher":
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(
                """
            **Domain Analysis:**
            - `rentmanagerservice.com`
            - `Analyze mypropertymanagement.com`
            - `Research pmccompany.net for ICP fit`
                """
            )

        with col2:
            st.markdown(
                """
            **Company Intelligence:**
            - `What PMS does ABC Property Management use?`
            - `How many units does XYZ Rentals manage?`
            - `Analyze technology stack for domain.com`
            
            _Note: If data isn’t public, I’ll mark it **Unknown** and cite sources._
                """
            )

    elif st.session_state.current_agent == "Contact Researcher":
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(
                """
            **Contact Discovery:**
            - `List decision makers at domain.com`
            - `Find contacts at ABC Property Management`
            - `Who is the CEO of XYZ Rentals?`
            
            For more than 3 contacts: [Open Lead List Generator](/?agent=Lead%20List%20Generator)
                """
            )

        with col2:
            st.markdown(
                """
            **Contact Details:**
            - `Find email for John Smith at ABC PM`
            - `Get LinkedIn profiles for XYZ Rentals team`
            - `Research contact information for key prospects`
                """
            )

    elif st.session_state.current_agent == "Lead List Generator":
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(
                """
            **Geographic Lists:**
            - `Property management companies in Texas`
            - `Generate 100 leads in California with 50+ units`
            - `Build list of companies in Austin, TX`
                """
            )

        with col2:
            st.markdown(
                """
            **Technology-Based Lists:**
            - `AppFolio users with 100+ units`
            - `Companies using Yardi but under 200 units`
            - `Find prospects not using major PMS in Florida (100–300 units)`
                """
            )

    elif st.session_state.current_agent == "Sequence Enroller":
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(
                """
            **Enrollment & Preview**
            - Auto-enroll (recommend + preview):  
              `enroll contacts for onboarding senderEmail: ae@yourco.com emails: a@ex.com, b@ex.com`
            - Confirm auto-enroll (two-step):  
              `CONFIRM AUTO ENROLL` → `FINAL CONFIRM AUTO ENROLL`
            - Manual preview:  
              `preview enroll sequenceId: 279644275 from ae@yourco.com emails: a@ex.com, b@ex.com`  
              `CONFIRM BULK ENROLL` → `FINAL CONFIRM BULK ENROLL`
            - Single contact manual enroll (two-step):  
              `enroll sequenceId: 279644275 contactId: 123456 senderEmail: ae@yourco.com`  
              `CONFIRM ENROLL` → `FINAL CONFIRM ENROLL`
                """
            )

        with col2:
            st.markdown(
                """
            **List & Filter Sequences**
            - `list sequences owner_email: ae@yourco.com limit: 20 with details`
            - `list sequences owner_email: ae@yourco.com active only min steps: 3`
            - `list all sequences` (aggregates across owners)
                """
            )

        with col3:
            st.markdown(
                """
            **Search & Email Copy**
            - `search sequences owner_email: ae@yourco.com query: onboarding`
            - `search sequences across all users query: renewal min steps: 2`
            - `show email copy sequenceId: 279644275 owner_email: ae@yourco.com`
            
            _Tip: You can skip searching and use auto-enroll — the agent will recommend a sequence from across owners and show a preview before requiring a two-step confirmation._
                """
            )
