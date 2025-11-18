import os
import re
import time
from datetime import datetime

import streamlit as st

from rv_agentic.agents.company_researcher_agent import create_company_researcher_agent
from rv_agentic.agents.contact_researcher_agent import create_contact_researcher_agent
from rv_agentic.agents.lead_list_agent import create_lead_list_agent
from rv_agentic.agents.sequence_enroller_agent import create_sequence_enroller_agent
from rv_agentic.config.settings import get_settings
from rv_agentic.services import supabase_client as _sb
from rv_agentic.services.openai_provider import run_agent_sync
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

    # Navigation
    st.subheader("Navigation")
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "Agents"
    view_mode = st.radio(
        "View",
        ["Agents", "Dashboard"],
        index=0 if st.session_state.view_mode == "Agents" else 1,
        label_visibility="collapsed",
    )
    st.session_state.view_mode = view_mode

    st.markdown("---")

    # Agent selection only in Agents view
    agents = {
        "Company Researcher": "🔍",
        "Contact Researcher": "👤",
        "Lead List Generator": "📋",
        "Sequence Enroller": "📧",
    }

    if st.session_state.view_mode == "Agents":
        st.subheader("Select Agent")

        for agent_name, icon in agents.items():
            # Check if this is the active agent
            is_active = st.session_state.current_agent == agent_name

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
                    st.session_state.messages = []  # Clear messages when switching agents
                    st.rerun()

    st.markdown("---")

    # Quick Actions
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

    # (Removed sidebar pin buttons; actions live under the chat)

# Dashboard view
if st.session_state.get("view_mode") == "Dashboard":
    st.title("📊 Dashboard")
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

            # Main table (kept simple and non-scrollable)
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
    st.subheader("Lead List Run Status")
    col_run, col_btn = st.columns([3, 1])
    with col_run:
        run_id_input = st.text_input(
            "Run ID",
            value="",
            placeholder="Paste a pm_pipeline run id (UUID)",
            key="lead_list_run_id",
        )
    with col_btn:
        check_clicked = st.button("Check Status", use_container_width=True)
    if check_clicked and run_id_input.strip():
        with st.status("Fetching run status from pm_pipeline…", state="running") as stat:
            try:
                rid = run_id_input.strip()
                run = _sb.get_pm_run(rid)
                company_gap = _sb.get_pm_company_gap(rid)
                contact_gap = _sb.get_contact_gap_summary(rid)
                if not run:
                    stat.update(label="Run not found", state="error")
                    st.error("No run found with that id.")
                else:
                    stat.update(label="Run loaded", state="complete")

                    # Use orchestrator.get_run_progress for enhanced progress display
                    progress = orchestrator.get_run_progress(rid)

                    st.markdown("#### Run")
                    st.markdown(
                        f"- **Status:** `{progress.get('status')}`\n"
                        f"- **Stage:** `{progress.get('stage')}`\n"
                        f"- **Target Quantity:** `{progress.get('target_quantity')}`\n"
                    )

                    st.markdown("#### Progress")

                    # Company progress bar
                    companies_info = progress.get("companies", {})
                    companies_ready = companies_info.get("ready", 0)
                    companies_gap = companies_info.get("gap", 0)
                    company_progress_pct = companies_info.get("progress_pct", 0)

                    st.markdown(f"**Companies: {companies_ready} / {progress.get('target_quantity')} ({company_progress_pct}%)**")
                    st.progress(company_progress_pct / 100.0)
                    st.caption(f"Gap: {companies_gap} companies remaining")

                    # Contact progress bar
                    contacts_info = progress.get("contacts", {})
                    contacts_ready = contacts_info.get("ready", 0)
                    contacts_gap = contacts_info.get("gap", 0)
                    contact_progress_pct = contacts_info.get("progress_pct", 0)

                    st.markdown(f"**Contacts: {contacts_ready} total ({contact_progress_pct}% of minimum)**")
                    st.progress(contact_progress_pct / 100.0)
                    st.caption(f"Gap: {contacts_gap} contacts remaining to meet minimum")

                    # Export CSV button when run is completed
                    if str(progress.get("status")) == "completed":
                        st.markdown("#### Export")
                        if st.button("📥 Download CSVs", use_container_width=True, key="btn_export_csv"):
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
                            expand_loc = st.button("Expand Location", key="btn_expand_location")
                        with col2:
                            loosen_pms = st.button("Loosen PMS", key="btn_loosen_pms")
                        with col3:
                            accept_partial = st.button("Accept Partial Results", key="btn_accept_partial")

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
                stat.update(label="Error loading run", state="error")
                st.error(str(e))

    # Worker Health Monitoring
    st.markdown("---")
    st.subheader("Worker Health")

    if st.button("🔄 Refresh Worker Status", use_container_width=False):
        st.rerun()

    try:
        from rv_agentic.services import heartbeat
        health = heartbeat.get_worker_health_summary()

        # Overall health metrics
        col_active, col_dead, col_status = st.columns(3)
        with col_active:
            st.metric("Active Workers", health.get("total_active_workers", 0))
        with col_dead:
            st.metric(
                "Dead Workers",
                health.get("total_dead_workers", 0),
                delta=None,
                delta_color="inverse"
            )
        with col_status:
            health_status = health.get("health_status", "unknown")
            status_emoji = "✅" if health_status == "healthy" else "⚠️"
            st.metric("System Health", f"{status_emoji} {health_status.title()}")

        # Worker stats by type
        stats_by_type = health.get("stats_by_type", [])
        if stats_by_type:
            st.markdown("#### Workers by Type")

            # Create a nice table
            import pandas as pd
            df = pd.DataFrame(stats_by_type)
            if not df.empty:
                # Rename columns for display
                df_display = df.rename(columns={
                    "worker_type": "Type",
                    "total_workers": "Total",
                    "active_workers": "Active",
                    "idle_workers": "Idle",
                    "processing_workers": "Processing",
                    "dead_workers": "Dead"
                })
                # Select only relevant columns
                cols_to_show = ["Type", "Total", "Active", "Idle", "Processing", "Dead"]
                df_display = df_display[[col for col in cols_to_show if col in df_display.columns]]
                st.dataframe(df_display, use_container_width=True, hide_index=True)

        # Show dead workers if any
        dead_workers = health.get("dead_workers", [])
        if dead_workers:
            st.warning(f"⚠️ {len(dead_workers)} worker(s) appear to be dead")
            with st.expander("View Dead Workers"):
                for worker in dead_workers:
                    worker_id = worker.get("worker_id", "unknown")
                    worker_type = worker.get("worker_type", "unknown")
                    minutes_ago = worker.get("seconds_since_heartbeat", 0) / 60.0
                    current_task = worker.get("current_task", "none")

                    st.markdown(
                        f"**{worker_id}** ({worker_type})  \n"
                        f"Last seen: {minutes_ago:.1f} minutes ago  \n"
                        f"Task: {current_task}"
                    )
                    st.markdown("---")

        # Show active workers
        active_workers = health.get("active_workers", [])
        if active_workers:
            with st.expander(f"View Active Workers ({len(active_workers)})"):
                for worker in active_workers:
                    worker_id = worker.get("worker_id", "unknown")
                    worker_type = worker.get("worker_type", "unknown")
                    status = worker.get("status", "unknown")
                    seconds_ago = worker.get("seconds_since_heartbeat", 0)
                    current_task = worker.get("current_task", "idle")
                    current_run_id = worker.get("current_run_id")

                    status_emoji = {"active": "🟢", "idle": "🟡", "processing": "🔵"}.get(status, "⚪")

                    st.markdown(
                        f"{status_emoji} **{worker_id}** ({worker_type})  \n"
                        f"Status: {status} | Heartbeat: {seconds_ago:.0f}s ago  \n"
                        f"Task: {current_task or 'idle'}"
                    )
                    if current_run_id:
                        st.caption(f"Run ID: {current_run_id}")
                    st.markdown("---")

    except Exception as e:
        st.error(f"Failed to load worker health: {e}")

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
                        status_prefixes = ("🔍", "🌐", "📋", "🧭", "🧩", "✍️", "🔎", "📤", "⚠️", "✅", "👤", "🚚", "🗄️", "•")
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
                            starts_with_status = any(lstripped.startswith(p) for p in status_prefixes)
                            is_list_line = bool(re.match(r"^\s*(\d+\.|[-*])\s+", stripped))
                            is_heading = bool(re.match(r"^\s*#{1,6}\s+", stripped))
                            is_status = starts_with_status and not is_list_line and not is_heading
                            if is_status:
                                gap = time.time() - last_status_time
                                if gap < 0.08:
                                    time.sleep(0.08 - gap)
                                status_container.markdown(lstripped)
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
                        # Create a pm_pipeline run immediately so downstream workers can process it.
                        try:
                            inferred_qty = 40
                            qty_match = re.search(r"(?:^|\\D)(\\d{1,4})(?:\\s+leads|\\s+accounts|\\b)", prompt)
                            if qty_match:
                                inferred_qty = int(qty_match.group(1))
                        except Exception:
                            inferred_qty = 40

                        criteria = {
                            "natural_request": prompt,
                            "source": "lead_list_generator_ui",
                            "created_via": "streamlit",
                        }
                        pm_run = _sb.create_pm_run(
                            criteria=criteria,
                            target_quantity=inferred_qty,
                        )
                        run_id = pm_run.get("id")

                        # Let the Lead List Agent generate a human-friendly confirmation / summary.
                        try:
                            agent_prompt = (
                                f"User lead list request: {prompt}\n\n"
                                f"A backend worker will fulfill run id '{run_id}'. "
                                "Summarize the request parameters clearly for the user and confirm "
                                "that the list will be generated asynchronously."
                            )
                            result = run_agent_sync(current_agent, agent_prompt)
                            response = getattr(result, "final_output", "") or ""
                        except Exception:
                            response = (
                                "### ✅ Lead List Request Queued\n\n"
                                f"- **Run ID:** `{run_id}`\n"
                                f"- **Requested Quantity (approx.):** {inferred_qty}\n\n"
                                "Your lead list will be generated asynchronously based on this request."
                            )

                        # Add backend run metadata to the status stream
                        if run_id:
                            stream_callback(f"✅ Queued pm_pipeline run `{run_id}` for processing.")
                    else:
                        # Legacy agents with .research()
                        if hasattr(current_agent, "research"):
                            response = current_agent.research(prompt, stream_callback)
                        else:
                            # Fallback for future Agents SDK-based agents
                            result = run_agent_sync(current_agent, prompt)
                            response = getattr(result, "final_output", "") or ""

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
