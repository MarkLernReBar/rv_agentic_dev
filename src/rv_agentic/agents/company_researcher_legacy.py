"""
Company Researcher Agent - Specialized comprehensive company research for property management firms.
"""

import json
import os
import time
from typing import Any, Callable, Dict, Optional, cast

import requests
from openai import OpenAI

from hubspot_client import (
    HubSpotError,
    search_companies_by_name,
    search_company_by_domain,
)
from hubspot_client import associate_note_to_company as hs_assoc_note_company
from hubspot_client import create_company as hs_create_company
from hubspot_client import create_note as hs_create_note
from hubspot_client import pin_note_on_company as hs_pin_note_company
from narpm_client import quick_company_membership
from supabase_client import find_company
from utils import (
    COMPANY_STALE_DAYS,
    extract_company_name,
    is_stale,
    normalize_domain,
    validate_domain,
)


class CompanyResearcher:
    """
    Company Researcher Agent - Expert business intelligence analyst specializing in comprehensive
    company research for property management firms.
    """

    def __init__(self):
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-5-mini"
        # Built-in tools: enable web search for gap-filling
        self.tools = [
            {
                "type": "web_search",
                "user_location": {"type": "approximate"},
                "search_context_size": "medium",
            }
        ]

        # System prompt updated to align with Contact Researcher: prioritize HubSpot, include NEO DB, style & tone
        self.system_prompt = (
            "You are the Company Researcher Agent for property management firms.\n"
            "Return a concise, sales-ready ICP analysis strictly for external consumption.\n"
            "Never expose internal processes, tools, or raw outputs (no 'Phase' language; no internal system names besides 'HubSpot' and the 'NEO Research Database').\n"
            "Include a Sources section with public sources you actually used; you MAY also list 'HubSpot' and 'NEO Research Database' when they were used.\n"
            "If something is Unknown, state 'Unknown' without guessing.\n"
            "Locked insights supplied in context are authoritative—repeat those values verbatim unless you uncover fresher conflicting data.\n\n"
            "# Company ICP (RentVine)\n\n"
            "Baseline:\n\n"
            "- Property management company\n"
            "- Primarily single-family (≥~50% SFH qualifies as ICP)\n"
            "Core dimensions:\n\n"
            "1) Portfolio Type (highest weight): + SFH-dominant; − HOA-only/commercial-only\n"
            "2) Units (estimate listings×10): <50 low | 50–150 med | 150–1000 high | >1000 enterprise motion\n"
            "3) PMS: competitors (AppFolio/Buildium/Yardi/DoorLoop) +; RentVine −; HOA/MF-centric −; unknown neutral\n"
            "4) Employee count: 5–10 small; 20+ strong; 30–40+ enterprise\n"
            "5) Website provider: PM-focused vendors (PMW, Doorgrow, Fourandhalf, Upkeep) +; generic neutral; unrelated −\n"
            "6) Exclusions: HOA-only, MF-only, brokerages, no active mgmt\n\n"
            "## ICP Analysis for <Company Name or Domain>\n\n"
            "### Agent Summary\n\n"
            "## Insights Found\n"
            "- **Website**: <url>\n"
            "- **PMS Vendor**: <vendor | Unknown>\n"
            "- **Estimated Units Listed**: <number | Unknown>\n"
            "- **Estimated Number of Employees**: <number | Unknown>\n"
            "- **NARPM Member**: <Yes — <member names> | No | Unknown>\n"
            "- **Single Family?**: <Yes | No | Mixed | Unknown>\n"
            "- **Disqualifiers**: <None | list>\n"
            "- **ICP Fit?**: <Yes | No | Uncertain>\n"
            "- **ICP Fit Confidence**: <Low | Medium | High>\n"
            "- **ICP Tier**: <A+ | A | B | C | D>\n\n"
            "### Reason(s) for Confidence\n\n"
            "### Assumptions\n\n"
            "---\n\n"
            "## Decision Makers\n"
            "List 1–3 priority contacts in rank order. For each, use this layout:\n"
            "1. **Full Name** — Title  \\\n"
            "   - **Email**: <email | Unknown>\\n"
            "   - **Phone**: <phone | Unknown>\\n"
            "   - **LinkedIn**: <url | Unknown>\\n"
            "   - **Personalization**: <one tailored anecdote or proof point>\\n\n"
            "If fewer than three qualified contacts are found, only list the ones you can verify.\n\n"
            "---\n\n"
            "Agent notes / outreach suggestions\n\n"
            "### Sources\n\n(List 3–6 sources you actually used. Include public sources and, if used, 'HubSpot' and 'NEO Research Database'.)\n\n"
            "Markdown only. Blank line after each heading. No code fences. No JSON.\n\n"
            "If current PMS is 'RentVine' or the website indicates a 'RentVine portal', treat the company as an existing RentVine customer.\n"
            "Do NOT describe RentVine as a competitor or a product mismatch in that case; instead, suggest upsell opportunities (modules, workflows, seats, services).\n\n"
            "## STYLE & TONE\n\n"
            "* Professional but conversational — written like an SDR handoff.\n"
            "* Summary-first: the Agent Summary should give the SDR immediate context without scrolling.\n"
            "* Avoid robotic phrasing; use natural language.\n"
            "* Show transparency about uncertainties.\n"
            "* Never reveal internal formulas or raw tool outputs.\n"
        )

    def research(
        self, user_input: str, stream_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """
        Conduct comprehensive company research with DB-first, gap-filling via HubSpot.
        """
        try:
            status_interval = float(os.getenv("COMPANY_STATUS_MIN_GAP", "1.5"))
            last_status_time = [0.0]

            def emit_status(message: str) -> None:
                if not stream_callback:
                    return
                now = time.time()
                if last_status_time[0] > 0:
                    wait = status_interval - (now - last_status_time[0])
                    if wait > 0:
                        time.sleep(wait)
                stream_callback(message)
                last_status_time[0] = time.time()

            # Guarded HubSpot write operations (require explicit JSON confirm)
            try:
                maybe_json = json.loads(user_input)
            except Exception:
                maybe_json = None

            if isinstance(maybe_json, dict) and maybe_json.get("action") in {
                "create_company",
                "pin_company_summary",
            }:
                if not maybe_json.get("confirm"):
                    return (
                        '⚠️ HubSpot update requires explicit confirmation. Include "confirm": true in your JSON payload.\n'
                        'Examples: {"action":"create_company","confirm":true,"name":"Acme PM","domain":"acmepm.com"} or {"action":"pin_company_summary","confirm":true,"domain":"acmepm.com"}'
                    )

                action = maybe_json.get("action")
                if action == "create_company":
                    emit_status("⚠️ Creating company in HubSpot (confirmed)...\n")
                    name = maybe_json.get("name") or extract_company_name(
                        maybe_json.get("domain", "") or user_input
                    )
                    req_domain = normalize_domain(
                        maybe_json.get("domain") or user_input
                    )
                    if not name and not req_domain:
                        return (
                            "Cannot create company: provide at least a name or domain."
                        )
                    # Avoid duplicates
                    hs_existing = None
                    if req_domain and validate_domain(req_domain):
                        try:
                            hs_existing = search_company_by_domain(req_domain)
                        except HubSpotError:
                            hs_existing = None
                    if hs_existing:
                        return f"Company already exists in HubSpot with id={hs_existing.get('id')} for domain {req_domain}."
                    props = {}
                    if name:
                        props["name"] = name
                    if req_domain and validate_domain(req_domain):
                        props["domain"] = req_domain
                    result = hs_create_company(props)
                    return f"✅ Created HubSpot company id={result.get('id')} (name={props.get('name')}, domain={props.get('domain') or 'n/a'})."

                if action == "pin_company_summary":
                    emit_status("⚠️ Pinning company summary in HubSpot (confirmed)...\n")
                    req_domain = normalize_domain(
                        maybe_json.get("domain") or user_input
                    )
                    hs_existing = None
                    if req_domain and validate_domain(req_domain):
                        hs_existing = search_company_by_domain(req_domain)
                    company_id = maybe_json.get("company_id") or (
                        hs_existing and hs_existing.get("id")
                    )
                    if not company_id:
                        return "Cannot pin: provide company_id or a resolvable domain."
                    # Use provided full summary markdown
                    note_md = maybe_json.get("note_markdown")
                    if not note_md or not str(note_md).strip():
                        return "Please provide 'note_markdown' (the full summary) or use the sidebar Pin button right after generating the summary."
                    note_md_str = str(note_md)
                    note_html = "<div>" + note_md_str.replace("\n", "<br/>") + "</div>"
                    note = hs_create_note(note_html)
                    note_id = note.get("id")
                    if note_id:
                        try:
                            hs_assoc_note_company(note_id, company_id)
                        except HubSpotError:
                            pass
                        try:
                            hs_pin_note_company(company_id, note_id)
                        except HubSpotError:
                            pass
                    return f"✅ Added and pinned summary note (id={note_id}) to company {company_id}."
            # Derive query terms
            domain_candidate = normalize_domain(user_input)
            domain: Optional[str] = (
                domain_candidate if validate_domain(domain_candidate) else None
            )
            company_name = extract_company_name(user_input)

            emit_status(
                "🔍 Checking database (NEO Research Database) for existing company record...\n"
            )

            db_record = find_company(domain=domain, company_name=company_name)

            # Determine staleness (6 months)
            stale, _ = (True, None)
            if db_record:
                stale, _ = is_stale(db_record.get("updated_at"), COMPANY_STALE_DAYS)

            if db_record:
                emit_status(
                    "📋 Found existing record; assessing freshness and missing fields...\n"
                )
            else:
                emit_status("📋 No existing record found.\n")

            # Identify gaps
            record = db_record or {}
            sources_used: list[str] = []
            if db_record:
                # Externally refer to our internal DB as the NEO Research Database
                sources_used.append("NEO Research Database")
            gaps = []
            important_fields = [
                "company_name",
                "domain",
                "pms",
                "unit_count",
                "employee_count",
                "icp_score",
                "icp_tier",
                "meets_basic_icp",
            ]
            for f in important_fields:
                if record.get(f) in (None, "", [], {}):
                    gaps.append(f)
            if gaps:
                emit_status(
                    "🧭 Missing fields to enrich: " + ", ".join(gaps) + "\n"
                )
            else:
                emit_status(
                    "🧭 No critical gaps detected; verifying freshness and preparing analysis...\n"
                )

            # Try HubSpot first; if known RentVine customer, prioritize HubSpot values over NEO DB
            hubspot_data: Optional[Dict[str, Any]] = None
            should_refresh_hubspot = bool(domain or record.get("domain") or company_name)

            if should_refresh_hubspot:
                msg = (
                    "🌐 Querying HubSpot to refresh company record...\n"
                    if not (stale or gaps)
                    else "🌐 Querying HubSpot to fill missing fields...\n"
                )
                emit_status(msg)
                domain_for_lookup = str(domain or record.get("domain") or "").strip()
                if domain_for_lookup:
                    hs_props = [
                        "name",
                        "domain",
                        "numberofemployees",
                        "annualrevenue",
                        "hs_lastmodifieddate",
                        # lifecycle/customer signals
                        "lifecyclestage",
                        "hs_lifecyclestage_customer_date",
                        "is_customer",
                        "hs_is_customer",
                        # custom fields expected by this portal (safe if missing)
                        "units_managed",
                        "pm_software",
                    ]
                    try:
                        hs = search_company_by_domain(domain_for_lookup, properties=hs_props)
                    except HubSpotError:
                        hs = None
                    hubspot_data = hs or None
                elif company_name:
                    emit_status("🌐 Searching HubSpot by company name...\n")
                    candidates = []
                    try:
                        candidates = search_companies_by_name(company_name, limit=3)
                    except HubSpotError:
                        candidates = []
                    if candidates:
                        hubspot_data = candidates[0]
                        props = (
                            hubspot_data.get("properties", {})
                            if isinstance(hubspot_data, dict)
                            else {}
                        )
                        # Backfill domain/name if missing
                        if not record.get("domain") and props.get("domain"):
                            record["domain"] = props.get("domain")
                        if not record.get("company_name") and props.get("name"):
                            record["company_name"] = props.get("name")
                        nm = props.get("name") or "(no name)"
                        dm = props.get("domain") or "(no domain)"
                        emit_status(f"🧩 Found HubSpot match: {nm} — {dm}\n")

                # Map HS properties to our schema if available
                if hubspot_data:
                    before = {k: record.get(k) for k in important_fields}
                    props = (
                        hubspot_data.get("properties", {})
                        if isinstance(hubspot_data, dict)
                        else {}
                    )
                    # Flag existing RentVine customer via lifecycle/custom flags (best-effort)
                    lifecycle = str(props.get("lifecyclestage") or "").lower()
                    rv_flags = [
                        str(props.get(k) or "").lower()
                        for k in ("is_customer", "hs_is_customer")
                    ]
                    is_rv_customer = (
                        ("customer" in lifecycle)
                        or any(v in ("true", "1", "yes", "y") for v in rv_flags)
                        or str(record.get("pms") or props.get("pm_software") or "")
                        .strip()
                        .lower()
                        == "rentvine"
                    )
                    if is_rv_customer:
                        record["existing_rentvine_customer"] = True
                    if not record.get("employee_count") and props.get(
                        "numberofemployees"
                    ):
                        record["employee_count"] = (
                            int(props.get("numberofemployees"))
                            if str(props.get("numberofemployees")).isdigit()
                            else props.get("numberofemployees")
                        )
                    if not record.get("company_name") and props.get("name"):
                        record["company_name"] = props.get("name")
                    if not record.get("domain") and props.get("domain"):
                        record["domain"] = props.get("domain")
                    if not record.get("revenue") and props.get("annualrevenue"):
                        record["revenue"] = props.get("annualrevenue")
                    # Custom fields mapping
                    if not record.get("unit_count") and props.get("units_managed"):
                        try:
                            record["unit_count"] = int(props.get("units_managed"))
                        except Exception:
                            record["unit_count"] = props.get("units_managed")
                    if not record.get("pms") and props.get("pm_software"):
                        record["pms"] = props.get("pm_software")
                    after = {k: record.get(k) for k in important_fields}
                    filled = [
                        k
                        for k in important_fields
                        if (before.get(k) in (None, "", [], {}))
                        and (after.get(k) not in (None, "", [], {}))
                    ]
                    if filled:
                        emit_status(
                            "🧩 Filled via HubSpot: " + ", ".join(filled) + "\n"
                        )

            # Optional NARPM company membership check
            try:
                comp_for_narpm = (
                    record.get("company_name") or record.get("domain") or company_name
                )
                if comp_for_narpm:
                    narpm_company = quick_company_membership(comp_for_narpm)
                    if narpm_company:
                        record["narpm_member_company"] = True
                        record["narpm_company_display"] = (
                            narpm_company.get("company")
                            or narpm_company.get("full_name")
                            or narpm_company.get("name")
                        )
                        narpm_people: list[str] = []
                        for key in (
                            "member_name",
                            "full_name",
                            "name",
                            "primary_contact",
                        ):
                            value = narpm_company.get(key)
                            if value:
                                narpm_people.append(str(value))
                        members = narpm_company.get("members")
                        if isinstance(members, list):
                            for person in members:
                                name = (
                                    person.get("full_name")
                                    or person.get("name")
                                    or person.get("member_name")
                                ) if isinstance(person, dict) else None
                                if name:
                                    narpm_people.append(str(name))
                        if narpm_people:
                            record["narpm_member_people"] = sorted(
                                {p.strip() for p in narpm_people if p and p.strip()}
                            )
                        # Record source hint for output
                        try:
                            if "NARPM" not in sources_used:
                                sources_used.append("NARPM")
                        except Exception:
                            pass
            except Exception:
                pass

            # Lightweight tech fingerprint: fetch homepage and look for PMS markers if still unknown
            if hubspot_data:
                sources_used.append("HubSpot")
            try:
                if not record.get("pms") and (record.get("domain") or domain):
                    target_domain = record.get("domain") or domain
                    url_candidates = [
                        f"https://{target_domain}",
                        f"http://{target_domain}",
                    ]
                    for url in url_candidates:
                        try:
                            resp = requests.get(
                                url, timeout=float(os.getenv("HTTP_TIMEOUT", "10"))
                            )
                            if resp.ok and resp.text:
                                html = resp.text.lower()
                                detected = None
                                if "appfolio" in html:
                                    detected = "AppFolio"
                                elif "buildium" in html:
                                    detected = "Buildium"
                                elif "yardi" in html or "rentcafe" in html:
                                    detected = "Yardi"
                                elif "propertyware" in html:
                                    detected = "Propertyware"
                                elif "rentmanager" in html:
                                    detected = "Rent Manager"
                                elif "entrata" in html:
                                    detected = "Entrata"
                                if detected:
                                    record["pms"] = record.get("pms") or detected
                                    sources_used.append(f"Website: {url}")
                                    break
                        except Exception:
                            continue
            except Exception:
                pass

            # Prepare authoritative insights for consistent downstream output
            locked_insights: Dict[str, str] = {}

            def _stringify(value: Any) -> Optional[str]:
                if value in (None, "", [], {}):
                    return None
                if isinstance(value, (int, float)):
                    return f"{value}"
                return str(value)

            def _bool_label(value: Any) -> Optional[str]:
                if isinstance(value, bool):
                    return "Yes" if value else "No"
                lowered = str(value).strip().lower()
                if lowered in {"true", "1", "yes", "y"}:
                    return "Yes"
                if lowered in {"false", "0", "no", "n"}:
                    return "No"
                return None

            icp_fit_label = _bool_label(record.get("meets_basic_icp"))
            if not icp_fit_label:
                icp_fit_label = _stringify(record.get("icp_fit"))
            if icp_fit_label:
                locked_insights["ICP Fit"] = icp_fit_label

            icp_confidence = record.get("icp_confidence") or record.get(
                "icp_fit_confidence"
            )
            confidence_label = _stringify(icp_confidence)
            if confidence_label:
                locked_insights["ICP Fit Confidence"] = confidence_label

            tier_label = _stringify(record.get("icp_tier"))
            if tier_label:
                locked_insights["ICP Tier"] = tier_label

            pms_label = _stringify(record.get("pms"))
            if pms_label:
                locked_insights["PMS Vendor"] = pms_label

            units_value = record.get("unit_count") or record.get("units")
            units_label = _stringify(units_value)
            if units_label:
                locked_insights["Estimated Units Listed"] = units_label

            employees_value = record.get("employee_count")
            employees_label = _stringify(employees_value)
            if employees_label:
                locked_insights["Estimated Number of Employees"] = employees_label

            single_family_value = record.get("single_family_focus") or record.get(
                "sfh_focus"
            )
            sfh_label = _bool_label(single_family_value) or _stringify(
                record.get("portfolio_mix")
            )
            if sfh_label:
                locked_insights["Single Family?"] = sfh_label

            rentvine_label = _bool_label(record.get("existing_rentvine_customer"))
            if rentvine_label == "Yes":
                locked_insights["Existing RentVine Customer"] = "Yes"

            narpm_people = record.get("narpm_member_people")
            if narpm_people:
                locked_insights["NARPM Member"] = "Yes — " + ", ".join(narpm_people)
            elif record.get("narpm_member_company"):
                display = record.get("narpm_company_display")
                if display:
                    locked_insights["NARPM Member"] = "Yes — " + str(display)

            website_value = record.get("website")
            if not website_value and record.get("domain"):
                website_value = f"https://{record.get('domain')}"
            website_label = _stringify(website_value)
            if website_label:
                locked_insights["Website"] = website_label

            # Prepare input for model
            analysis_input = {
                "query": user_input,
                "source": {
                    "database": bool(db_record),
                    "hubspot": bool(hubspot_data),
                    "stale_db_record": bool(db_record) and stale,
                },
                "company": record,
                "locked_insights": locked_insights,
                "unfilled_fields": [
                    f for f in important_fields if record.get(f) in (None, "", [], {})
                ],
            }

            prompt = (
                "Using the JSON data below (database record merged with any HubSpot data), produce a concise, structured report.\n"
                "- Treat values in 'locked_insights' as canonical and repeat them exactly in the 'Insights Found' bullets unless you verify fresher conflicting data.\n"
                "- If you supersede a locked value, state the change and evidence in 'Assumptions' or 'Agent notes / outreach suggestions'.\n"
                "- Prefer HubSpot and the NEO Research Database; call web_search only when critical gaps remain.\n"
                "- Never fabricate unknowns.\n"
                "\nRender the final output with this structure:\n"
                "## ICP Analysis for {company_name}\n\n"
                "### Agent Summary\n"
                "One to three paragraphs summarizing the business and ICP fit. Include a bold line like: ***Bottom Line -> <ICP Fit>.***\n\n"
                "---\n\n"
                "### Insights Found\n"
                "- **Website**: <url>\n"
                "- **PMS Vendor**: <name + confirmation method>\n"
                "- **Estimated Units Listed**: <number or Unknown>\n"
                "- **Estimated Number of Employees**: <number or Unknown>\n"
                "- **NARPM Member**: <Yes — names | No | Unknown>\n"
                "- **Single Family?**: <Yes | No | Mixed | Unknown>\n"
                "- **Disqualifiers**: <None | list>\n"
                "- **ICP Fit?**: <Yes | No | Uncertain>\n"
                "- **ICP Fit Confidence**: <High | Medium | Low>\n"
                "- **ICP Tier**: <A+ | A | B | C | D>\n\n"
                "---\n\n"
                "### Reason(s) for Confidence\n"
                "- <bullet reason 1>\n"
                "- <bullet reason 2>\n"
                "- <bullet reason 3>\n\n"
                "### Assumptions\n"
                "- <bullet assumption or 'None'>\n\n"
                "---\n\n"
                "## Decision Makers\n"
                "1. **Full Name** — Title  \\n"
                "   - **Email**: <email or Unknown>\\n"
                "   - **Phone**: <phone or Unknown>\\n"
                "   - **LinkedIn**: <url or Unknown>\\n"
                "   - **Personalization**: <one tailored anecdote or proof point>\\n\n"
                "(Repeat the block for up to three verified contacts.)\n\n"
                "---\n\n"
                "Agent notes / outreach suggestions\n\n"
                "### Sources\n\n(List 3–6 sources you actually used. Include public sources and, if used, 'HubSpot' and 'NEO Research Database'.)\n"
                f"\n```json\n{json.dumps(analysis_input, default=str)}\n```\n"
                "\n### Enrichment Offer\n"
                "If useful new company details were found via web_search (or verified from HubSpot), offer to pin this summary to HubSpot.\n"
                "Provide a ready-to-run JSON block the user can submit back to this agent to perform the action (note: confirm required):\n"
                '```json\n{\n  "action": "pin_company_summary",\n  "confirm": true,\n  "domain": "<company_domain>",\n  "note_markdown": "<paste the full summary above>"\n}\n```\n'
            )

            # If still missing fields, inform that web search will be attempted by the model
            still_missing = [
                f for f in important_fields if record.get(f) in (None, "", [], {})
            ]
            if still_missing:
                emit_status(
                    "🌐 Will attempt web search for: "
                    + ", ".join(still_missing)
                    + "\n"
                )
            emit_status("✍️ NEO agent generating summary...\n")

            # Stream model output using system instructions; keep user input minimal
            output_requirements = (
                "Follow the section outline above exactly.\n"
                "- Markdown only. Blank line after headings. No code fences. No JSON.\n"
                "- Include a '### Sources' section listing public sources; you may also include 'HubSpot' and 'NEO Research Database' if actually used.\n"
                "- If 'sources_hint' is present in context, include those exact labels verbatim in Sources.\n"
                "- Do NOT mention other internal data sources, tools, or processes (avoid 'Phase' language or system names besides 'HubSpot' and 'NEO Research Database').\n"
                "- Do not add tracking params to links; use canonical URLs.\n"
                "- Repeat each value provided in 'locked_insights' exactly (unless you surface fresher data and note the change).\n"
                "- When filling each bullet, present only the value after the colon (e.g., '**ICP Fit?**: Yes' — do not echo 'ICP Fit=Yes').\n"
                "- Present up to three 'Decision Makers' entries with the specified sub-bullets and personalization line.\n"
                "- If the context indicates 'Existing RentVine Customer: Yes', immediately after '### Agent Summary' add '> Note: HubSpot indicates this company is an existing RentVine customer. Consider an upsell conversation (additional modules, seats, workflows, or services).'\n"
                "- Keep sentences tight; one idea per bullet.\n"
            )
            # Provide a compact context string instead of raw JSON to avoid echoing
            ctx_parts = []
            comp = record or {}
            for k in [
                "company_name",
                "domain",
                "pms",
                "unit_count",
                "employee_count",
                "icp_score",
                "icp_tier",
            ]:
                if k in comp and comp.get(k) not in (None, ""):
                    ctx_parts.append(f"{k}: {comp.get(k)}")
            missing = ", ".join(
                [f for f in important_fields if record.get(f) in (None, "", [], {})]
            )
            sources_hint = "; ".join(sources_used) if sources_used else ""
            locked_hint = (
                "; ".join(f"{k}: {v}" for k, v in locked_insights.items())
                if locked_insights
                else ""
            )
            context_text = (
                "Context (do not echo): "
                + "; ".join(ctx_parts)
                + (f"; missing: {missing}" if missing else "")
                + (f"; sources_hint: {sources_hint}" if sources_hint else "")
                + (f"; locked_insights: {locked_hint}" if locked_hint else "")
            )
            full_instructions = (
                f"{self.system_prompt}\n\n"
                + output_requirements
                + "\n"
                + context_text
                + "\nDo not include any JSON, code fences, or the literal words 'JSON', 'DATA', or 'payload' in the final answer."
            )
            stream = self.openai_client.responses.create(
                model=self.model,
                input=user_input,
                instructions=full_instructions,
                tools=cast(Any, self.tools),
                store=True,
                include=["reasoning.encrypted_content"],
                stream=True,
            )

            def _sanitize_chunk(text: str) -> str:
                try:
                    import re as _re

                    s = text
                    # Strip tracking params the model may inject
                    s = s.replace("?utm_source=openai", "").replace(
                        "&utm_source=openai", ""
                    )
                    # Remove leading preambles like "Short answer" / "Quick answer" / "Answer"
                    s = _re.sub(
                        r"(?im)^(short answer|quick answer|answer)\s*\:?-?\s*", "", s
                    )
                    return s
                except Exception:
                    return text

            # Collect without streaming raw chunks; we will stream polished content once
            full_content = ""
            for event in stream:
                if event.type == "response.output_text.delta":
                    delta = event.delta
                    full_content += delta
                    # Stream live to UI for true streaming
                    try:
                        if stream_callback:
                            stream_callback(delta)
                    except Exception:
                        pass
            # Minimal post-processing to remove tracking params and any internal mentions if they slip through
            try:
                import re as _re

                # Strip utm_source=openai (and similar) query params from markdown links
                full_content = _re.sub(r"\?utm_source=openai\b", "", full_content)
                full_content = _re.sub(r"&utm_source=openai\b", "", full_content)
                # Strip any utm_* query params conservatively
                full_content = _re.sub(
                    r"[?&]utm_[a-z0-9_]+=\S+", "", full_content, flags=_re.IGNORECASE
                )
                # Ensure blank line after headings
                full_content = _re.sub(
                    r"(?m)^(#{1,6}\s[^\n]+)\n(?!\n)", r"\1\n\n", full_content
                )
                # Remove stray 'pick one' placeholders and redundant double spaces
                full_content = full_content.replace("(pick one)", "").replace("  ", " ")
                # Drop internal headings/phrases that should never appear externally
                full_content = _re.sub(
                    r"(?im)^##?\s*Phase\s*\d+.*$\n?", "", full_content
                )
                # Keep 'Sources' but remove other internal-only headings if they slip in
                full_content = _re.sub(
                    r"(?im)^###\s*(Next recommended action).*$\n?",
                    "",
                    full_content,
                )
                # Replace internal product name with external label; allow 'HubSpot'
                full_content = _re.sub(
                    r"(?i)\bSupabase\b", "NEO Research Database", full_content
                )
                full_content = _re.sub(
                    r"(?im)\b(internal research_database|Operational constraint)\b",
                    "",
                    full_content,
                )
            except Exception:
                pass
            # Final polish using a higher-quality model; do not alter facts
            try:
                polish_instructions = (
                    "You are a professional editor. Rewrite the following Markdown to be clear, grammatical, and well-structured.\n"
                    "Preserve all facts, numbers, names, and links. Do not invent details.\n"
                    "Fix casing, punctuation, spacing, and remove odd characters.\n"
                    "Keep headings and lists; output only the polished Markdown."
                )
                polish_resp = self.openai_client.responses.create(
                    model=getattr(self, "polish_model", "gpt-5"),
                    input=full_content,
                    instructions=polish_instructions,
                    store=False,
                )
                polished_candidate = getattr(polish_resp, "output_text", None)
                if (
                    not polished_candidate
                    or len(str(polished_candidate).strip()) < 10
                    or str(polished_candidate).strip() in ("{}", "[]")
                ):
                    polished = full_content
                else:
                    polished = str(polished_candidate)
            except Exception:
                polished = full_content

            # Normalize Unicode punctuation/zero-width characters
            def _normalize_text(s: str) -> str:
                try:
                    import re as _re

                    s = (
                        s.replace("\u200b", "")
                        .replace("\u200c", "")
                        .replace("\u00ad", "")
                    )
                    trans = {
                        "\u2013": "-",
                        "\u2014": "-",
                        "\u2018": "'",
                        "\u2019": "'",
                        "\u201c": '"',
                        "\u201d": '"',
                    }
                    for k, v in trans.items():
                        s = s.replace(k, v)
                    s = _re.sub(r"[ \t]{2,}", " ", s)
                    return s
                except Exception:
                    return s

            def _dedupe_sections(md: str) -> str:
                try:
                    lines = md.splitlines()
                    out = []
                    seen = set()
                    i = 0
                    while i < len(lines):
                        line = lines[i]
                        m2 = None
                        # Match H2/H3 headings
                        if line.startswith("## ") or line.startswith("### "):
                            key = line.strip().lower()
                            if key in seen:
                                # skip until next heading of same or higher level
                                level = 3 if line.startswith("### ") else 2
                                i += 1
                                while i < len(lines):
                                    nxt = lines[i]
                                    if (
                                        (level == 2 and nxt.startswith("## "))
                                        or nxt.startswith("## ")
                                        or nxt.startswith("### ")
                                    ):
                                        break
                                    i += 1
                                continue
                            seen.add(key)
                        out.append(line)
                        i += 1
                    return "\n".join(out)
                except Exception:
                    return md

            final_text = _dedupe_sections(_normalize_text(polished))
            # Guard against duplicated full reports (occasionally the model re-renders the entire template).
            if final_text.count("## ICP Analysis") > 1:
                first_idx = final_text.find("## ICP Analysis")
                second_idx = final_text.find("## ICP Analysis", first_idx + 1)
                if second_idx != -1:
                    final_text = final_text[:second_idx].rstrip()
            # If HubSpot flagged an existing RentVine customer, insert upsell note near the top
            try:
                if record.get("existing_rentvine_customer"):
                    upsell_note = (
                        "> Note: HubSpot indicates this company is an existing RentVine customer. "
                        "Consider an upsell conversation (additional modules, seats, workflows, or services)."
                    )
                    if upsell_note not in final_text:
                        anchor = "### Agent Summary"
                        if anchor in final_text:
                            final_text = final_text.replace(
                                anchor,
                                anchor + "\n\n" + upsell_note,
                                1,
                            )
                        else:
                            final_text = upsell_note + "\n\n" + final_text
            except Exception:
                pass
            emit_status("✅ Summary ready. Rendering results...\n")
            # Also return wrapped JSON array so non-UI clients can parse the expected shape
            try:
                wrapped = json.dumps([{"output": final_text}], ensure_ascii=False)
            except Exception:
                wrapped = final_text
            return wrapped

        except Exception as e:
            error_msg = f"❌ **Error during company research:** {str(e)}\n\nPlease try again or contact support if the issue persists."
            if stream_callback:
                stream_callback(error_msg)
            return error_msg

    def get_capabilities(self) -> Dict[str, Any]:
        """
        Return the capabilities and tools available to this agent.
        """
        return {
            "model": self.model,
            "specializations": [
                "DB-first company research",
                "Business intelligence analysis",
                "ICP fit assessment",
                "Competitive landscape analysis",
                "Strategic partnership evaluation",
                "Technology stack identification",
            ],
        }
