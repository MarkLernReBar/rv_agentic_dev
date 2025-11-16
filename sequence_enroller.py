"""
Sequence Enroller Agent - Specialized outreach sequence management and enrollment.
"""

import json
import os
import re
import time
from functools import lru_cache
from typing import Any, Callable, Dict, Optional, List

from openai import OpenAI

from hubspot_client import HubSpotError
from hubspot_client import enroll_contact_in_sequence as hs_enroll
from hubspot_client import get_sequence as hs_get_sequence
from hubspot_client import list_all_owner_user_ids as hs_list_all_owner_user_ids
from hubspot_client import list_all_sequences as hs_list_all_sequences
from hubspot_client import list_sequences as hs_list_sequences
from hubspot_client import search_contact as hs_search_contact
from hubspot_client import search_contact_by_fields as hs_search_contact_by_fields
from hubspot_client import search_contacts_by_query as hs_search_contacts_by_query


class SequenceEnroller:
    """
    Sequence Enroller Agent - Expert at managing outreach sequences and enrollment automation.
    """

    def __init__(self):
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-5-mini"
        # Keep last preview state for confirmation flow within the session
        self._last_preview: Optional[Dict[str, Any]] = None
        # Lightweight in-memory audit of recent enroll attempts
        self._last_audit: List[Dict[str, Any]] = []
        # Pending single-enroll confirmation cache for two-step safeguard
        self._last_pending_enroll: Optional[Dict[str, Any]] = None
        # Remember resolved owner userIds per sequenceId across turns
        self._sequence_owner_cache: Dict[str, str] = {}

        # System prompt focused on sequence enrollment
        self.system_prompt = """# 📧 Sequence Enroller Agent - System Prompt

You are the **Sequence Enroller Agent**, an expert at managing automated outreach sequences, campaign enrollment, and follow-up optimization. You specialize in coordinating prospect enrollment into sequences and managing ongoing campaign workflows.

## 🎯 Your Mission

Transform prospect lists into enrolled, active outreach campaigns. You handle the technical and strategic aspects of sequence management, from initial enrollment through completion tracking and optimization.

## 🔧 Your Capabilities

### **Sequence Management:**
- Automated sequence enrollment and scheduling
- Campaign workflow design and optimization  
- Follow-up timing and cadence management
- Multi-channel sequence coordination (email, calls, social)
- A/B testing and performance optimization

### **Enrollment Operations:**
- Bulk prospect enrollment into sequences
- Individual contact sequence assignment
- Enrollment status tracking and reporting
- Sequence progression monitoring
- Completion and response tracking

### **Campaign Integration:**
- CRM sequence integration (HubSpot, Salesforce)
- Email platform coordination (Outreach, SalesLoft)
- Calendar and task scheduling
- Response handling and routing
- Performance analytics and reporting

## 📋 Enrollment Process

You follow a systematic approach to sequence enrollment:

1. **Sequence Selection** - Identify appropriate sequences for prospects
2. **Enrollment Preparation** - Verify contact data and personalization
3. **Batch Processing** - Coordinate bulk enrollment operations
4. **Schedule Management** - Set timing and cadence parameters
5. **Monitoring Setup** - Configure tracking and reporting

## 🚀 Integration with Other Agents

You coordinate with other specialists for complete workflow management:
- **Lead List Generator** for prospect list preparation
- **Contact Researcher** for decision maker verification
- **Company Researcher** for personalization data

Always ensure sequence enrollment follows best practices for deliverability and compliance."""

        # No web/MCP tools. Interacts directly with HubSpot Automation Sequences v4 API.

    def research(  # type: ignore
        self, user_input: str, stream_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """
        Handle sequence enrollment and management requests.
        Tries to perform the requested HubSpot action when possible, else explains next steps.
        """
        try:
            # Try to parse a JSON command first
            json_cmd: Optional[Dict[str, Any]] = None
            try:
                json_cmd = json.loads(user_input)
            except Exception:
                json_cmd = None

            text = user_input.lower()

            # --- Suggest a sequence for a contact (by email) ---
            if (
                re.search(r"\b(suggest|recommend|choose|pick|best)\b", text, re.IGNORECASE)
                and re.search(r"\bsequence\b", text, re.IGNORECASE)
            ):
                # Extract contact email and optional owner scope
                contact_email = (
                    (json_cmd or {}).get("contact_email")
                    or self._extract_kv_email(user_input, "contact_email")
                    or self._extract_value(user_input, r"for\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
                    or self._extract_value(user_input, r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
                )
                owner_email = (json_cmd or {}).get("owner_email") or self._extract_kv_email(user_input, "owner_email")
                # Attempt to resolve by name/company if no email provided
                resolved_contact = None
                if not contact_email:
                    # Extract phrase after 'for' as best-effort name/company
                    phrase = self._extract_value(user_input, r"\bfor\s+([^\n\r]+)") or ""
                    phrase = phrase.strip()
                    fn = ln = comp = None
                    # Try to split patterns: 'Name at Company', 'Name, Company', 'Name — Company'
                    name_part = phrase
                    m_at = re.search(r"^(.*?)\s+(?:at|@)\s+(.*)$", phrase, re.IGNORECASE)
                    if m_at:
                        name_part = m_at.group(1).strip()
                        comp = m_at.group(2).strip()
                    else:
                        m_comma = re.search(r"^(.*?),(.*)$", phrase)
                        if m_comma:
                            name_part = m_comma.group(1).strip()
                            comp = m_comma.group(2).strip()
                        else:
                            m_dash = re.search(r"^(.*?)\s+[\-–—]\s+(.*)$", phrase)
                            if m_dash:
                                name_part = m_dash.group(1).strip()
                                comp = m_dash.group(2).strip()
                    if name_part:
                        parts = [re.sub(r"[,.;]$", "", p) for p in re.split(r"\s+", name_part) if p]
                        if len(parts) >= 2:
                            fn, ln = parts[0], parts[-1]
                    # Try fielded search first
                    candidates = []
                    try:
                        if fn or ln or comp:
                            candidates = hs_search_contact_by_fields(
                                firstname=fn, lastname=ln, company=comp, limit=5
                            ) or []
                    except HubSpotError:
                        candidates = []
                    # Fallback to free-text query
                    if not candidates and phrase:
                        try:
                            candidates = hs_search_contacts_by_query(phrase, limit=5) or []
                        except HubSpotError:
                            candidates = []
                    # Score candidates to avoid cross-company mismatches
                    def _score_cand(c: Dict[str, Any]) -> int:
                        pr = c.get("properties", {}) if isinstance(c, dict) else {}
                        pf = (pr.get("firstname") or "").strip().lower()
                        pl = (pr.get("lastname") or "").strip().lower()
                        pc = (pr.get("company") or "").strip().lower()
                        s = 0
                        if fn and ln and pf == fn.lower() and pl == ln.lower():
                            s += 4
                        else:
                            if ln and pl == ln.lower():
                                s += 2
                            if fn and pf == fn.lower():
                                s += 1
                        if comp:
                            cn = comp.strip().lower()
                            # company token containment
                            tokens = [t for t in re.split(r"\W+", cn) if t and t not in {"inc","llc","ltd","co","company","property","properties","management","pm"}]
                            if any(t in pc for t in tokens):
                                s += 3
                            else:
                                s -= 3
                        if pr.get("email"):
                            s += 1
                        return s
                    if candidates:
                        ranked = sorted(candidates, key=_score_cand, reverse=True)
                        if _score_cand(ranked[0]) >= 3:
                            resolved_contact = ranked[0]
                            props = resolved_contact.get("properties", {}) if isinstance(resolved_contact, dict) else {}
                            contact_email = props.get("email") or contact_email
                if not contact_email and not resolved_contact:
                    return "Provide a contact email or include a name (optionally with 'at Company'). Example: 'suggest a sequence for Eric Keith at Rent Now'"
                if stream_callback:
                    stream_callback("🔎 Looking up contact in HubSpot by email...\n")
                try:
                    contact = (
                        hs_search_contact(email=contact_email) if contact_email else resolved_contact
                    )
                except HubSpotError as ex:
                    return f"HubSpot error while searching contact: {str(ex)}"
                if not contact:
                    return f"No HubSpot contact found for {contact_email}."
                # Extract fields for scoring context
                props = contact.get("properties", {}) if isinstance(contact, dict) else {}
                contact_ctx = {
                    "email": contact_email,
                    "name": (" ".join([p for p in [props.get("firstname"), props.get("lastname")] if p]) if props else None) or (name_part if 'name_part' in locals() else None) or None,
                    "job_title": (props.get("jobtitle") if props else None),
                    "company": (props.get("company") if props else None) or (comp if 'comp' in locals() else None),
                }
                # Gather sequences (owner-scoped if provided; else across users) with details for content scoring
                if owner_email:
                    if stream_callback:
                        stream_callback("🔎 Resolving owner email to userId...\n")
                    user_id = self._resolve_user_id_from_email(owner_email, stream_callback)
                    if not user_id:
                        return f"Could not resolve owner_email to userId: {owner_email}"
                    if stream_callback:
                        stream_callback("🔍 Fetching sequences for owner...\n")
                    seqs_base = hs_list_all_sequences(user_id=str(user_id), page_size=100)
                    seqs = self._normalize_sequence_list({"results": seqs_base})
                    seqs = self._maybe_enrich_with_details(seqs, str(user_id), True, stream_callback, max_details=60)
                else:
                    if stream_callback:
                        stream_callback("🔍 Aggregating sequences across users (with details)...\n")
                    seqs = self._aggregate_sequences_all_users({}, True, stream_callback)
                if not seqs:
                    return "No sequences available to suggest from. Ensure owners scope is configured or use HUBSPOT_OWNER_USER_IDS fallback."
                # Build compact summaries for LLM ranking
                def _seq_summary(s: Dict[str, Any]) -> Dict[str, Any]:
                    name = (
                        s.get("name")
                        or (s.get("metadata", {}).get("name") if isinstance(s.get("metadata"), dict) else None)
                        or "(no name)"
                    )
                    steps = s.get("details", {}).get("steps") if isinstance(s.get("details"), dict) else (s.get("steps") or s.get("sequenceSteps") or [])
                    if not isinstance(steps, list):
                        steps = []
                    subjects = []
                    for st in steps[:5]:
                        subj = self._extract_step_subject(st) or st.get("title") or ""
                        if subj:
                            subjects.append(str(subj))
                    channels = []
                    for st in steps[:5]:
                        ch = self._channel_match(st)
                        if ch and ch not in channels:
                            channels.append(ch)
                    return {
                        "id": str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or ""),
                        "name": name,
                        "subjects": subjects,
                        "channels": channels,
                        "step_count": self._count_steps(s),
                    }
                summaries = [_seq_summary(s) for s in seqs if (s.get("id") or s.get("sequenceId") or s.get("sequence_id"))]
                # Keep top N by basic heuristics: prefer active and email-first sequences if available
                def _active(s: Dict[str, Any]) -> bool:
                    return bool(
                        self._extract_bool(s, ["active", "isActive", "enabled", "isEnabled"]) or \
                        self._extract_bool(s.get("details", {}) if isinstance(s.get("details"), dict) else {}, ["active", "isActive", "enabled", "isEnabled"]) or False
                    )
                ranked = sorted(seqs, key=lambda s: (not _active(s), -self._count_steps(s)))
                top = ranked[:40]
                top_summaries = [_seq_summary(s) for s in top]
                # Ask the model to pick best match
                if stream_callback:
                    stream_callback("🧠 Scoring sequences against contact profile...\n")
                prompt = (
                    "Contact Profile:\n" + json.dumps(contact_ctx, ensure_ascii=False) + "\n\n" +
                    "Candidate Sequences (summaries):\n" + json.dumps(top_summaries, ensure_ascii=False)
                )
                instr = (
                    "You are a CRM sequence selector. Choose the single best outreach sequence for the contact based on title, company context, and email-first suitability.\n"
                    "Return STRICT JSON with keys: {\"sequence_id\": <string>, \"name\": <string>, \"reason\": <string>}."
                )
                try:
                    resp = self.openai_client.responses.create(
                        model=self.model,
                        input=prompt,
                        instructions=instr,
                        store=False,
                    )
                    data = {}
                    try:
                        data = json.loads(getattr(resp, "output_text", "") or "{}")
                    except Exception:
                        data = {}
                    seq_id = (data or {}).get("sequence_id")
                    name = (data or {}).get("name")
                    reason = (data or {}).get("reason")
                except Exception:
                    # Fallback: simple heuristic — choose the first active with 'email' in channels
                    best = next((s for s in top_summaries if "email" in s.get("channels", [])), None) or (top_summaries[0] if top_summaries else None)
                    seq_id = (best or {}).get("id")
                    name = (best or {}).get("name")
                    reason = "Heuristic fallback: active/email-first and longer sequence preferred."

                if not seq_id:
                    return "Could not determine a suitable sequence. Try 'search sequences across all users query: <topic>' to shortlist."

                # Remember owner userId for this sequence to avoid re-resolving next turn
                chosen_uid: Optional[str] = None
                if owner_email:
                    # We already scoped to an owner; user_id was resolved above
                    try:
                        chosen_uid = str(user_id)  # type: ignore[name-defined]
                    except Exception:
                        chosen_uid = None
                if not chosen_uid:
                    # Find in aggregated results to capture first source_user_id
                    try:
                        sid = str(seq_id)
                        match = next((s for s in seqs if str(s.get("id") or s.get("sequenceId") or s.get("sequence_id")) == sid), None)
                        sources = (match or {}).get("source_user_ids") or []
                        if sources:
                            chosen_uid = str(sources[0])
                    except Exception:
                        chosen_uid = None
                if chosen_uid:
                    self._sequence_owner_cache[str(seq_id)] = chosen_uid

                md = [
                    "## Suggested Sequence",
                    f"- Contact: {contact_ctx.get('name') or contact_email} — {contact_ctx.get('company') or ''}",
                    f"- Sequence: `{seq_id}` — {name or '(no name)'}",
                ]
                if reason:
                    md.append(f"- Why: {reason}")
                md.append("")
                md.append("### Next Actions")
                md.append(f"- Preview email copy: `show email copy sequenceId: {seq_id}` (owner optional; will auto-resolve)")
                md.append(f"- Enroll: `enroll sequenceId: {seq_id} contact_email: {contact_email} sender_email: <your@company>`")
                out = "\n".join(md)
                if stream_callback:
                    for ch in out:
                        stream_callback(ch)
                return out

            # --- Show email copy for a sequence ---
            if (
                re.search(r"\b(show|render|display)\b", text, re.IGNORECASE)
                and re.search(r"\b(email\s+copy|email\s+content)\b", text, re.IGNORECASE)
                and re.search(r"\bsequence(id)?\b", text, re.IGNORECASE)
            ):
                sequence_id = self._extract_value(user_input, r"sequence[_\s-]?id[:\s]*([0-9]+)")
                user_id = self._extract_value(user_input, r"user[_\s-]?id[:\s]*([0-9]+)")
                owner_email = self._extract_kv_email(user_input, "owner_email")
                if not user_id and owner_email:
                    if stream_callback:
                        stream_callback("🔎 Resolving owner email to userId...\n")
                    user_id = self._resolve_user_id_from_email(owner_email, stream_callback)
                if not sequence_id or not user_id:
                    return (
                        "Provide sequenceId and an owner_email or userId. Example: `show email copy sequenceId: 279644275 owner_email: ae@yourco.com`"
                    )
                if stream_callback:
                    stream_callback("🔍 Fetching sequence details...\n")
                # Parse flags
                step_only_val = self._extract_value(user_input, r"step[:\s]*([0-9]+)")
                step_only = int(step_only_val) if step_only_val and step_only_val.isdigit() else None
                mask = bool(re.search(r"\btokens\s*:\s*masked\b", user_input, re.IGNORECASE))
                include_html = bool(re.search(r"\binclude[_\s-]?html[:\s]*(true|yes|1)\b", user_input, re.IGNORECASE))
                include_full = bool(re.search(r"\bfull[:\s]*(true|yes|1)\b", user_input, re.IGNORECASE))
                data = _cached_get_sequence_impl(str(sequence_id), str(user_id))
                md = self._render_sequence_email_copy(
                    data, step_only=step_only, mask_tokens=mask, include_html=include_html, include_full=include_full
                )
                if stream_callback:
                    for ch in md:
                        stream_callback(ch)
                return md

            # --- Search sequences by text (two-stage: name, then steps with capped detail fetch) ---
            if (
                (re.search(r"\bsearch\b", text, re.IGNORECASE) and re.search(r"\bsequence(s)?\b", text, re.IGNORECASE))
                or (json_cmd and json_cmd.get("action") == "search_sequences")
            ):
                # Prefer quoted phrase; else capture until next known keyword
                query = (json_cmd or {}).get("query") or (
                    self._extract_value(user_input, r"query[:\s]*\"([^\"]+)\"")
                    or self._extract_value(user_input, r"query[:\s]*([^\n\r]+?)(?=\s+(channel:|min\s+steps|owner_email:|user[_\s-]?id:)|$)")
                    or ""
                )
                user_id = (json_cmd or {}).get("user_id") or self._extract_value(user_input, r"user[_\s-]?id[:\s]*([0-9]+)")
                owner_email = (json_cmd or {}).get("owner_email") or self._extract_kv_email(user_input, "owner_email")
                channel = self._extract_value(user_input, r"channel[:\s]*([A-Za-z]+)")
                channel_lc = (channel or "").strip().lower()
                # Bias toward action: if no owner scope provided, default to across-all-users search
                across_all_users = (not (user_id or owner_email)) or bool(
                    re.search(r"\bacross\s+all\s+users\b|\ball\s+users\b|\ball\s+owners\b", text, re.IGNORECASE)
                )

                DISPLAY_CAP = 50
                MAX_STEP_SEARCH = int(os.getenv("MAX_STEP_SEARCH", "60"))

                def build_output(matches: List[Dict[str, Any]], total: int, name_hits: int, step_hits: int) -> str:
                    out = ["## Sequence Search Results", f"- Query: `{query or '(none)'}`", ""]
                    if not matches:
                        out.append("No sequences matched query.")
                        out.append("")
                        out.append(f"- Matched: 0 of {total} (name: {name_hits}, steps: {step_hits})")
                        return "\n".join(out)
                    for s in matches[:DISPLAY_CAP]:
                        sid = str(s.get("id") or s.get("sequenceId") or "?")
                        name = (
                            s.get("name")
                            or (
                                s.get("metadata", {}).get("name") if isinstance(s.get("metadata"), dict) else None
                            )
                            or "(no name)"
                        )
                        steps = self._count_steps(s) or self._count_steps(s.get("details", {}) if isinstance(s.get("details"), dict) else {})
                        out.append(f"- `{sid}` — {name} — Steps: {steps if steps else 'Unknown'}")
                    if len(matches) > DISPLAY_CAP:
                        out.append(f"\n(+{len(matches)-DISPLAY_CAP} more truncated)")
                    out.append("")
                    out.append(f"- Matched: {min(len(matches), DISPLAY_CAP)} of {total} (name: {name_hits}, steps: {step_hits}; cap: {DISPLAY_CAP})")
                    return "\n".join(out)

                def enrich_subset_for_steps(seq_list: List[Dict[str, Any]], uid: str, cap: int) -> List[Dict[str, Any]]:
                    if not seq_list:
                        return []
                    subset = seq_list[:cap]
                    if stream_callback:
                        stream_callback(f"• Fetching details for {len(subset)} sequences (for step search)...\n")
                    return self._maybe_enrich_with_details(subset, uid, True, None, max_details=len(subset))

                if not across_all_users:
                    if not user_id and owner_email:
                        if stream_callback:
                            stream_callback("🔎 Resolving owner email to userId...\n")
                        user_id = self._resolve_user_id_from_email(owner_email, stream_callback)
                    if not user_id:
                        # Fallback to org-wide search when owner/user is not provided
                        if stream_callback:
                            stream_callback("🔍 Aggregating sequences across users...\n")
                        seqs = self._aggregate_sequences_all_users({}, False, stream_callback)
                        total = len(seqs)
                        name_hits_list = [s for s in seqs if self._sequence_matches_query(s, query)]
                        remaining = [s for s in seqs if s not in name_hits_list]
                        step_hits_list: List[Dict[str, Any]] = []
                        cap = min(MAX_STEP_SEARCH, len(remaining))
                        if cap > 0 and stream_callback:
                            stream_callback(f"• Fetching details across users for {cap} sequences (for step search)...\n")
                        for i in range(cap):
                            s = remaining[i]
                            uid_list = s.get("source_user_ids") or []
                            uid = str(uid_list[0]) if uid_list else None
                            if not uid:
                                continue
                            try:
                                detail = _cached_get_sequence_impl(str(s.get("id") or s.get("sequenceId") or ""), uid)
                                s = dict(s)
                                s["details"] = detail
                                if self._sequence_matches_query_with_channel(s, query, channel_lc):
                                    step_hits_list.append(s)
                            except Exception:
                                continue
                        matches = name_hits_list + step_hits_list
                        md = build_output(matches, total, len(name_hits_list), len(step_hits_list))
                    else:
                        if stream_callback:
                            stream_callback("🔍 Fetching sequences...\n")
                        base = hs_list_all_sequences(user_id=str(user_id), page_size=100)
                        seqs = self._normalize_sequence_list({"results": base})
                        total = len(seqs)
                        name_hits_list = [s for s in seqs if self._sequence_matches_query(s, query)]
                        remaining = [s for s in seqs if s not in name_hits_list]
                        enriched = enrich_subset_for_steps(remaining, str(user_id), min(MAX_STEP_SEARCH, len(remaining)))
                        step_hits_list = self._search_sequences_text_with_channel(enriched, query, channel_lc)
                        matches = name_hits_list + step_hits_list
                        md = build_output(matches, total, len(name_hits_list), len(step_hits_list))
                else:
                    if stream_callback:
                        stream_callback("🔍 Aggregating sequences across users...\n")
                    seqs = self._aggregate_sequences_all_users({}, False, stream_callback)
                    total = len(seqs)
                    name_hits_list = [s for s in seqs if self._sequence_matches_query(s, query)]
                    remaining = [s for s in seqs if s not in name_hits_list]
                    step_hits_list: List[Dict[str, Any]] = []
                    cap = min(MAX_STEP_SEARCH, len(remaining))
                    if cap > 0 and stream_callback:
                        stream_callback(f"• Fetching details across users for {cap} sequences (for step search)...\n")
                    for i in range(cap):
                        s = remaining[i]
                        uid_list = s.get("source_user_ids") or []
                        uid = str(uid_list[0]) if uid_list else None
                        if not uid:
                            continue
                        try:
                            detail = _cached_get_sequence_impl(str(s.get("id") or s.get("sequenceId") or ""), uid)
                            s = dict(s)
                            s["details"] = detail
                            if self._sequence_matches_query_with_channel(s, query, channel_lc):
                                step_hits_list.append(s)
                        except Exception:
                            continue
                    matches = name_hits_list + step_hits_list
                    md = build_output(matches, total, len(name_hits_list), len(step_hits_list))

                if stream_callback:
                    for ch in md:
                        stream_callback(ch)
                return md

            # --- Auto enroll (recommend best sequence by query, then preview) ---
            if re.search(r"\benroll\b", text, re.IGNORECASE) and not re.search(r"\bpreview\b", text, re.IGNORECASE):
                # If a sequenceId is already provided, let the explicit enroll handler below take over
                if not self._extract_value(user_input, r"sequence(?:[_\s-]?id)?[:#\s]*([0-9]+)"):
                    # Parse senderEmail and contact emails
                    sender_email = (
                        self._extract_kv_email(user_input, "senderEmail")
                        or self._extract_value(user_input, r"senderEmail[:\s]*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
                        or self._extract_value(user_input, r"\bfrom\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
                    )
                    raw_emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_input)
                    emails: List[str] = []
                    seen_e = set()
                    for e in raw_emails:
                        el = e.strip().lower()
                        if re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", el) and el not in seen_e:
                            seen_e.add(el)
                            emails.append(el)
                    # Remove the sender from target emails if present
                    if sender_email and sender_email in emails:
                        emails.remove(sender_email)
                    # Extract a natural language query/topic for sequence recommendation
                    query = (
                        (json_cmd or {}).get("query")
                        or self._extract_value(user_input, r"query[:\s]*\"([^\"]+)\"")
                        or self._extract_value(user_input, r"(?:for|about|around|regarding)\s+([^\n\r]+?)(?=\s+(with|using|sender|emails?:|$))")
                        or ""
                    )
                    # Require minimum inputs
                    if not sender_email or not emails:
                        hint = (
                            "To auto-enroll, include: senderEmail (connected inbox) and one or more contact emails.\n\n"
                            "Example: enroll contacts for onboarding senderEmail: agent@yourco.com emails: a@ex.com, b@ex.com\n"
                        )
                        return hint
                    if stream_callback:
                        stream_callback("🔍 Finding best-matching sequences across users...\n")
                    # Aggregate sequences across users and score by name+step content
                    try:
                        seqs_all = self._aggregate_sequences_all_users({}, False, stream_callback)
                    except HubSpotError as ex:
                        return (
                            "Missing scope crm.objects.owners.read or token invalid. "
                            "Add the scope to your private app, re-install the app, and retry. Details: " + str(ex)
                        )
                    # Coarse name match first
                    name_hits_list = [s for s in seqs_all if self._sequence_matches_query(s, query)] if query else list(seqs_all)
                    remaining = [s for s in seqs_all if s not in name_hits_list]
                    # Enrich a capped subset for step-body search
                    MAX_STEP_SEARCH = int(os.getenv("MAX_STEP_SEARCH", "60"))
                    step_hits_list: List[Dict[str, Any]] = []
                    cap = min(MAX_STEP_SEARCH, len(remaining))
                    if cap > 0 and stream_callback:
                        stream_callback(f"• Fetching details across users for {cap} sequences (ranking)...\n")
                    for i in range(cap):
                        s = remaining[i]
                        uid_list = s.get("source_user_ids") or []
                        uid = str(uid_list[0]) if uid_list else None
                        if not uid:
                            continue
                        try:
                            detail = _cached_get_sequence_impl(str(s.get("id") or s.get("sequenceId") or ""), uid)
                            s = dict(s)
                            s["details"] = detail
                            if not query or self._sequence_matches_query_with_channel(s, query, "email"):
                                step_hits_list.append(s)
                        except Exception:
                            continue
                    ranked = name_hits_list + step_hits_list
                    if not ranked:
                        return "No sequences found to recommend. Try providing a brief topic, e.g., 'for onboarding'."
                    best = ranked[0]
                    best_id = str(best.get("id") or best.get("sequenceId") or "")
                    best_name = best.get("name") or (
                        best.get("metadata", {}).get("name") if isinstance(best.get("metadata"), dict) else None
                    ) or "(no name)"
                    # Resolve contacts to contactIds
                    if stream_callback:
                        stream_callback("🔎 Resolving contacts in HubSpot...\n")
                    rows: List[Dict[str, Any]] = []
                    for em in emails:
                        cid = None
                        err = None
                        try:
                            c = hs_search_contact(email=em)
                            cid = self._extract_contact_id(c)
                        except HubSpotError as ex:
                            err = str(ex)
                        rows.append({"email": em, "contact_id": cid, "error": err})
                    ready = [r for r in rows if r.get("contact_id")]
                    not_found = [r for r in rows if not r.get("contact_id")]
                    self._last_preview = {
                        "mode": "auto",
                        "sequence_id": best_id,
                        "sequence_name": best_name,
                        "sender_email": sender_email,
                        "items": rows,
                        "ts": time.time(),
                    }
                    md = "## Auto Enroll — Preview\n\n"
                    md += f"- Recommended Sequence: `{best_id}` — {best_name}\n"
                    md += f"- SenderEmail: `{sender_email}`\n"
                    md += f"- Contacts: {len(emails)} (ready: {len(ready)}, unresolved: {len(not_found)})\n\n"
                    if ready:
                        md += "### Ready\n"
                        for r in ready:
                            md += f"- {r['email']} → contactId `{r['contact_id']}`\n"
                        md += "\n"
                    if not_found:
                        md += "### Needs Attention\n"
                        for r in not_found:
                            note = f" (error: {r['error']})" if r.get("error") else ""
                            md += f"- {r['email']} — not found{note}\n"
                        md += "\n"
                    md += (
                        "To proceed, reply: `CONFIRM AUTO ENROLL` (this enrolls only 'Ready' contacts, paced).\n"
                    )
                    if stream_callback:
                        for ch in md:
                            stream_callback(ch)
                    return md

            # --- Preview bulk enroll: "preview enroll ..." ---
            if re.search(r"\bpreview\b", text) and re.search(r"\benroll\b", text):
                # Extract sequenceId and senderEmail
                seq_id = self._extract_value(user_input, r"sequence(?:[_\s-]?id)?[:#\s]*([0-9]+)")
                sender_email = (
                    self._extract_kv_email(user_input, "sender_email")
                    or self._extract_value(user_input, r"\bfrom\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
                )
                # Collect candidate emails from text (dedupe, lowercase, validate)
                raw_emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_input)
                emails = []
                seen = set()
                for e in raw_emails:
                    el = e.strip().lower()
                    if re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", el) and el not in seen:
                        seen.add(el)
                        emails.append(el)
                # Remove the sender if present
                if sender_email in emails:
                    emails.remove(sender_email)
                # Require minimum inputs
                if not seq_id or not sender_email or not emails:
                    msg = "## Preview Bulk Enroll\n\n"
                    msg += "To preview, include: sequenceId, senderEmail (or 'from <email>'), and paste contact emails.\n\n"
                    msg += "Example: preview enroll sequenceId: 279644275 from inbox@yourco.com emails: a@ex.com, b@ex.com\n"
                    return msg
                # Resolve contacts and prepare preview
                if stream_callback:
                    stream_callback("🔎 Resolving contacts in HubSpot...\n")
                rows: List[Dict[str, Any]] = []
                for em in emails:
                    cid = None
                    err = None
                    try:
                        c = hs_search_contact(email=em)
                        cid = self._extract_contact_id(c)
                    except HubSpotError as ex:
                        err = str(ex)
                    rows.append({"email": em, "contact_id": cid, "error": err})
                ready = [r for r in rows if r.get("contact_id")]
                not_found = [r for r in rows if not r.get("contact_id")]
                # Cache preview for confirmation
                self._last_preview = {
                    "sequence_id": str(seq_id),
                    "sender_email": sender_email,
                    "items": rows,
                    "ts": time.time(),
                }
                # Render preview
                md = "## Preview — Bulk Enroll\n\n"
                md += f"- SequenceId: `{seq_id}`\n"
                md += f"- SenderEmail: `{sender_email}`\n"
                md += f"- Contacts: {len(emails)} (ready: {len(ready)}, unresolved: {len(not_found)})\n\n"
                if ready:
                    md += "### Ready\n"
                    for r in ready:
                        md += f"- {r['email']} → contactId `{r['contact_id']}`\n"
                    md += "\n"
                if not_found:
                    md += "### Needs Attention\n"
                    for r in not_found:
                        note = f" (error: {r['error']})" if r.get("error") else ""
                        md += f"- {r['email']} — not found{note}\n"
                    md += "\n"
                md += (
                    "To proceed, reply: `CONFIRM BULK ENROLL` (this will enroll only the 'Ready' contacts, paced).\n"
                )
                if stream_callback:
                    for ch in md:
                        stream_callback(ch)
                return md

            # --- Confirm bulk enroll (two-step safeguard) ---
            if re.search(r"\bconfirm\b", text) and re.search(r"\benroll\b", text) and re.search(r"\bbulk\b", text):
                if not self._last_preview:
                    return "No pending preview found. First run a preview enroll with sequenceId, senderEmail, and contact emails."
                seq_id = self._last_preview.get("sequence_id")
                sender_email = self._last_preview.get("sender_email")
                items = [i for i in self._last_preview.get("items", []) if i.get("contact_id")]
                if not items:
                    return "Nothing to enroll: no resolved contacts in the last preview."
                # Validate cached sender email type
                if not isinstance(sender_email, str) or not sender_email:
                    return "Invalid cached senderEmail. Please rerun the preview with a valid sender email."
                # First confirmation => require final confirmation before proceeding
                if not re.search(r"\bfinal\b", text, re.IGNORECASE):
                    self._last_preview["confirm_state"] = "armed"
                    return (
                        "⚠️ Final safeguard: This will enroll multiple contacts into a live sequence, which may send emails.\n"
                        + f"Planned action: sequenceId={seq_id}, senderEmail={sender_email}, contacts={len(items)}.\n"
                        + "To proceed, reply: FINAL CONFIRM BULK ENROLL\n"
                    )
                # Pacing caps
                rate_per_min = int(os.getenv("BULK_ENROLL_RATE_PER_MIN", "15"))
                rate_per_min = max(1, min(rate_per_min, 600))
                max_run = int(os.getenv("BULK_ENROLL_MAX", "200"))
                to_enroll = items[:max_run]
                md_head = (
                    "## Enrolling — Bulk\n\n"
                    f"- SequenceId: `{seq_id}`\n- SenderEmail: `{sender_email}`\n- Contacts: {len(to_enroll)} (cap: {max_run})\n\n"
                )
                if stream_callback:
                    for ch in md_head:
                        stream_callback(ch)
                results: List[str] = []
                self._last_audit = []
                interval = 60.0 / max(1, rate_per_min)
                chunk_size = 50
                for idx, r in enumerate(to_enroll, start=1):
                    em = r.get("email")
                    cid = r.get("contact_id")
                    try:
                        hs_enroll(sequence_id=str(seq_id), contact_id=str(cid), sender_email=str(sender_email))
                        results.append(f"✅ {em} → enrolled (contactId {cid})")
                        self._last_audit.append({
                            "ts": time.time(),
                            "sequenceId": str(seq_id),
                            "contactId": str(cid),
                            "senderEmail": str(sender_email),
                            "outcome": "success",
                            "message": "enrolled",
                        })
                    except HubSpotError as ex:
                        results.append(f"❌ {em} → {str(ex)}")
                        self._last_audit.append({
                            "ts": time.time(),
                            "sequenceId": str(seq_id),
                            "contactId": str(cid),
                            "senderEmail": str(sender_email),
                            "outcome": "error",
                            "message": str(ex),
                        })
                    if stream_callback:
                        stream_callback(f"• {results[-1]}\n")
                    # Per-item pacing
                    time.sleep(max(0.1, min(interval, 2.0)))
                    # Inter-chunk smoothing
                    if idx % chunk_size == 0:
                        time.sleep(1.0)
                return "\n".join([md_head] + results)

            # List sequences: supports a specific userId, owner email, or across all users
            if (json_cmd and json_cmd.get("action") == "list_sequences") or (
                re.search(r"\b(list|show|display|get|see|what\s+are)\b", text, re.IGNORECASE)
                and re.search(r"\bsequence(s)?\b", text, re.IGNORECASE)
            ):
                user_id = (json_cmd or {}).get("user_id") or self._extract_value(
                    user_input, r"user[_\s-]?id[:\s]*([0-9]+)"
                )
                owner_email = (json_cmd or {}).get("owner_email") or self._extract_kv_email(user_input, "owner_email")
                if not owner_email:
                    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_input)
                    has_other = bool(self._extract_kv_email(user_input, "contact_email") or self._extract_kv_email(user_input, "sender_email"))
                    if len(emails) == 1 and not has_other:
                        owner_email = emails[0]
                        if stream_callback:
                            stream_callback(f"👤 Using owner email: {owner_email}\n")
                limit_raw = (json_cmd or {}).get("limit") or self._extract_value(
                    user_input, r"limit[:\s]*([0-9]+|all)"
                )
                list_all_flag = str(limit_raw).lower() == "all" if limit_raw else False
                filters = (json_cmd or {}).get("filters") or {}
                details_flag = bool((json_cmd or {}).get("details")) or bool(
                    re.search(
                        r"\bwith details\b|\band details\b|\binclude details\b",
                        text,
                        re.IGNORECASE,
                    )
                )
                # Final fallback: capture any email in the text to treat as owner email
                if not owner_email:
                    m_any_email = re.search(
                        r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", user_input
                    )
                    if m_any_email:
                        owner_email = m_any_email.group(1)
                        if stream_callback:
                            stream_callback(
                                f"👤 Using owner email (fallback): {owner_email}\n"
                            )
                # Determine if the user intends to view organization-wide sequences
                all_seq_pattern = re.search(r"\ball(?:\s+\w+){0,3}\s+sequences\b", text, re.IGNORECASE)
                # Bias toward action: default to across-all-users if no owner/user is provided
                across_all_users = (not user_id and not owner_email) or bool(
                    all_seq_pattern
                    or re.search(r"\ball\s+users\b|\bacross\s+all\s+users\b|\ball\s+owners\b", text, re.IGNORECASE)
                )

                # If not aggregating across users, fall back to configured default owner email
                if not across_all_users and not user_id and not owner_email:
                    # With bias toward action, we'll aggregate across all users rather than require a default owner
                    across_all_users = True

                if across_all_users:
                    if stream_callback:
                        stream_callback(
                            "🔍 Fetching all owners to aggregate sequences across users...\n"
                        )
                    try:
                        # Fetch details if requested or needed for step count filtering
                        needs_steps_filter = bool(
                            ("min_steps" in filters)
                            or ("max_steps" in filters)
                            or re.search(r"min\s*steps\s*[:=]?\s*\d+", text, re.IGNORECASE)
                            or re.search(r"\b(more than|greater than|over)\s*\d+\s*steps?\b", text, re.IGNORECASE)
                            or re.search(r"\bat least\s*\d+\s*steps?\b", text, re.IGNORECASE)
                            or re.search(r"steps\s*[><=]", text, re.IGNORECASE)
                        )
                        sequences = self._aggregate_sequences_all_users(
                            filters, details_flag or needs_steps_filter, stream_callback
                        )
                    except HubSpotError as ex:
                        return (
                            "Missing scope crm.objects.owners.read or token invalid. "
                            "Add the scope to your private app, re-install the app, and retry. Details: " + str(ex)
                        )
                else:
                    # Resolve userId via owner email if provided (with fallback mapping if owners scope is missing)
                    if not user_id and owner_email:
                        user_id = self._resolve_user_id_from_email(
                            owner_email, stream_callback
                        )
                    if not user_id:
                        # Fall back to org-wide aggregation instead of returning an instruction
                        across_all_users = True
                        if stream_callback:
                            stream_callback("🔍 Aggregating sequences across users...\n")
                        try:
                            sequences = self._aggregate_sequences_all_users(
                                filters, details_flag, stream_callback
                            )
                        except HubSpotError as ex:
                            return (
                                "Missing scope crm.objects.owners.read or token invalid. "
                                "Add the scope to your private app, re-install the app, and retry. Details: "
                                + str(ex)
                            )
                        # Render output
                        md_lines = [f"## Sequences — Aggregated (all users)", ""]
                        for s in sequences:
                            sid = str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or "?")
                            name = (
                                s.get("name")
                                or (
                                    s.get("metadata", {}).get("name")
                                    if isinstance(s.get("metadata"), dict)
                                    else None
                                )
                                or "(no name)"
                            )
                            steps = self._count_steps(s) or self._count_steps(s.get("details", {}) if isinstance(s.get("details"), dict) else {})
                            active_val = self._extract_bool(s, ["active","isActive","enabled","isEnabled"]) or self._extract_bool(s.get("details", {}) if isinstance(s.get("details"), dict) else {}, ["active","isActive","enabled","isEnabled"]) or None
                            active_str = "Yes" if active_val is True else ("No" if active_val is False else "Unknown")
                            owners = s.get("source_user_ids")
                            o = f" — Sources: {', '.join(map(str, owners))}" if owners else ""
                            md_lines.append(f"- `{sid}` — {name} — Active: {active_str} — Steps: {steps}{o}")
                        md = "\n".join(md_lines)
                        if stream_callback:
                            for ch in md:
                                stream_callback(ch)
                        return md
                    if stream_callback:
                        stream_callback("🔍 Fetching sequences from HubSpot...\n")
                    if list_all_flag:
                        items = hs_list_all_sequences(user_id=str(user_id), page_size=100)
                        data = {"results": items}
                    else:
                        data = hs_list_sequences(
                            user_id=str(user_id), limit=int(limit_raw or 10)
                        )
                    sequences = self._normalize_sequence_list(data)
                    # Decide if we need step details based on filters or natural-language cues in the text
                    needs_steps_filter = bool(
                        re.search(r"min\s*steps\s*[:=]?\s*\d+", text, re.IGNORECASE)
                        or re.search(r"\b(more than|greater than|over)\s*\d+\s*steps?\b", text, re.IGNORECASE)
                        or re.search(r"\bat least\s*\d+\s*steps?\b", text, re.IGNORECASE)
                        or re.search(r"steps\s*[><=]", text, re.IGNORECASE)
                    )
                    # Optionally enrich with details for better filtering/formatting
                    sequences = self._maybe_enrich_with_details(
                        sequences=sequences,
                        user_id=str(user_id),
                        require_details=details_flag or ("active" in filters) or ("min_steps" in filters) or ("max_steps" in filters) or needs_steps_filter,
                        stream_callback=stream_callback,
                        max_details=200 if list_all_flag else 50,
                    )
                    sequences = self._filter_sequences(sequences, filters, text)

                # Deterministic markdown formatting for readability
                md_lines = []
                header = "## Sequences"
                if owner_email and not across_all_users:
                    header += f" — Owner: {owner_email} (userId {user_id})"
                if across_all_users:
                    header += " — Aggregated across users"
                md_lines.append(header)
                md_lines.append("")
                if not sequences:
                    md_lines.append("No sequences found.")
                else:
                    for s in sequences:
                        sid = str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or "?")
                        name = (
                            s.get("name")
                            or (
                                s.get("metadata", {}).get("name")
                                if isinstance(s.get("metadata"), dict)
                                else None
                            )
                            or "(no name)"
                        )
                        # Compute active and steps by looking at either top-level or details
                        active_val = self._extract_bool(s, ["active", "isActive", "enabled", "isEnabled"]) or \
                                     self._extract_bool(s.get("details", {}) if isinstance(s.get("details"), dict) else {}, ["active", "isActive", "enabled", "isEnabled"]) or None
                        steps_count = self._count_steps(s)
                        if steps_count == 0 and isinstance(s.get("details"), dict):
                            steps_count = self._count_steps(s.get("details", {}))
                        active_str = "Yes" if active_val is True else ("No" if active_val is False else "Unknown")
                        sources = s.get("source_user_ids")
                        src_str = f" — Sources: {', '.join(map(str, sources))}" if isinstance(sources, list) and sources else ""
                        md_lines.append(f"- `{sid}` — {name} — Active: {active_str} — Steps: {steps_count if steps_count else 'Unknown'}{src_str}")
                md = "\n".join(md_lines)
                if stream_callback:
                    for ch in md:
                        stream_callback(ch)
                return md

            # Get sequence details: allow owner_email or userId; auto-resolve owner_email -> userId
            if (
                (json_cmd and json_cmd.get("action") == "get_sequence" and json_cmd.get("sequence_id") and (json_cmd.get("user_id") or json_cmd.get("owner_email")))
                or ("get" in text and "sequence" in text and bool(self._extract_value(user_input, r"sequence[_\s-]?id[:\s]*([0-9]+)")))
            ):
                sequence_id = (json_cmd or {}).get(
                    "sequence_id"
                ) or self._extract_value(user_input, r"sequence[_\s-]?id[:\s]*([0-9]+)")
                user_id = (json_cmd or {}).get("user_id") or self._extract_value(
                    user_input, r"user[_\s-]?id[:\s]*([0-9]+)"
                )
                if not user_id:
                    owner_email = (json_cmd or {}).get("owner_email") or self._extract_value(
                        user_input, r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
                    )
                    if owner_email:
                        if stream_callback:
                            stream_callback("🔎 Resolving owner email to userId...\n")
                        user_id = self._resolve_user_id_from_email(owner_email, stream_callback)
                # Use cached owner mapping from recent suggestion if available
                if not user_id and sequence_id and isinstance(self._sequence_owner_cache, dict):
                    cached_uid = self._sequence_owner_cache.get(str(sequence_id))
                    if cached_uid:
                        user_id = cached_uid
                        if stream_callback:
                            stream_callback(f"• Using cached userId={user_id} for sequence {sequence_id}.\n")
                # Auto-resolve userId by scanning across owners when omitted
                if not user_id and sequence_id:
                    try:
                        if stream_callback:
                            stream_callback("🔍 Locating sequence owner across users...\n")
                        agg = self._aggregate_sequences_all_users({}, False, stream_callback)
                        sid = str(sequence_id)
                        match = next((s for s in agg if str(s.get("id") or s.get("sequenceId") or s.get("sequence_id")) == sid), None)
                        if match:
                            sources = match.get("source_user_ids") or []
                            if sources:
                                user_id = str(sources[0])
                                if stream_callback:
                                    stream_callback(f"• Using userId={user_id} for sequence {sid}.\n")
                                # Cache for future quick access
                                self._sequence_owner_cache[str(sid)] = str(user_id)
                    except Exception:
                        pass
                # Normalize types to strings for API call
                if sequence_id is not None:
                    sequence_id = str(sequence_id)
                if user_id is not None:
                    user_id = str(user_id)
                if not sequence_id or not user_id:
                    return "Provide a resolvable owner email (owner_email: ...) or omit it and I will auto-resolve by scanning owners (requires owners scope)."
                if stream_callback:
                    stream_callback("🔍 Fetching sequence details from HubSpot...\n")
                data = hs_get_sequence(sequence_id=str(sequence_id), user_id=str(user_id))
                md = self._format_sequence_detail_markdown(data)
                if stream_callback:
                    for ch in md:
                        stream_callback(ch)
                return md

            # Enroll contact in sequence (guarded by explicit confirmation)
            if (
                json_cmd
                and json_cmd.get("action") == "enroll"
                and json_cmd.get("sequence_id")
                and (json_cmd.get("contact_id") or json_cmd.get("contact_email"))
                and json_cmd.get("sender_email")
            ) or ("enroll" in text and "sequence" in text):
                sequence_id = (json_cmd or {}).get(
                    "sequence_id"
                ) or self._extract_value(user_input, r"sequence[_\s-]?id[:\s]*([0-9]+)")
                contact_id = (json_cmd or {}).get("contact_id") or self._extract_value(
                    user_input, r"contact[_\s-]?id[:\s]*([0-9]+)"
                )
                contact_email = (json_cmd or {}).get(
                    "contact_email"
                ) or self._extract_value(
                    user_input, r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
                )
                sender_email = (json_cmd or {}).get(
                    "sender_email"
                ) or self._extract_value(
                    user_input,
                    r"sender[_\s-]?email[:\s]*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
                )
                is_confirmed = bool(json_cmd and json_cmd.get("confirm")) or bool(
                    re.search(r"\bconfirm\s+enroll\b", user_input, re.IGNORECASE)
                )

                if not sequence_id:
                    return "Please provide a sequenceId to enroll into (e.g., 'enroll contact ... sequenceId: 123456')."
                if not sender_email:
                    return "Please provide a senderEmail with a connected inbox (e.g., 'senderEmail: agent@yourco.com')."
                # Preflight: validate IDs and sender email shape
                if not str(sequence_id).isdigit():
                    return "sequenceId must be a numeric string."
                if contact_id and not str(contact_id).isdigit():
                    return "contactId must be a numeric string."
                if not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", str(sender_email)):
                    return f"Invalid senderEmail: {sender_email}"

                # Resolve contactId from email if needed
                if not contact_id and contact_email:
                    if stream_callback:
                        stream_callback(
                            "🔍 Looking up contact in HubSpot by email...\n"
                        )
                    contact = hs_search_contact(email=contact_email)
                    contact_id = self._extract_contact_id(contact)
                if not contact_id:
                    return "Please provide contactId or a valid contact email to resolve it."

                # Guardrail: require explicit confirmation before performing enrollment
                if not is_confirmed:
                    warn = (
                        "⚠️ Enrollment safeguard: This will enroll the HubSpot contact into a live sequence, which may send emails.\n"
                        + f"Planned action: sequenceId={sequence_id}, contactId={contact_id}, senderEmail={sender_email}.\n"
                        + "To proceed, reply: \n"
                        + f"- CONFIRM ENROLL sequenceId: {sequence_id} contactId: {contact_id} senderEmail: {sender_email}\n"
                    )
                    return warn

                # Two-step safeguard: on first confirm, require final confirm before enrolling
                if not re.search(r"\bfinal\b", text, re.IGNORECASE):
                    self._last_pending_enroll = {
                        "sequence_id": str(sequence_id),
                        "contact_id": str(contact_id),
                        "sender_email": str(sender_email),
                    }
                    return (
                        "⚠️ Final safeguard: This will enroll the contact into a live sequence, which may send emails.\n"
                        + f"Planned action: sequenceId={sequence_id}, contactId={contact_id}, senderEmail={sender_email}.\n"
                        + "To proceed, reply: FINAL CONFIRM ENROLL\n"
                    )
                # Final confirm present: proceed
                if stream_callback:
                    stream_callback(
                        "⚠️ Proceeding with enrollment. Sequences may send emails.\n"
                    )
                    stream_callback("📤 Enrolling contact into HubSpot sequence...\n")
                result = hs_enroll(
                    sequence_id=str(sequence_id),
                    contact_id=str(contact_id),
                    sender_email=str(sender_email),
                )
                try:
                    self._last_audit.append({
                        "ts": time.time(),
                        "sequenceId": str(sequence_id),
                        "contactId": str(contact_id),
                        "senderEmail": str(sender_email),
                        "outcome": "success",
                        "message": "enrolled",
                    })
                except Exception:
                    pass
                return json.dumps(result, indent=2)

            # --- Final confirm single contact enroll ---
            if re.search(r"\bfinal\b", text, re.IGNORECASE) and re.search(r"\bconfirm\b", text, re.IGNORECASE) and re.search(r"\benroll\b", text, re.IGNORECASE) and not re.search(r"\bauto\b|\bbulk\b", text, re.IGNORECASE):
                pend = self._last_pending_enroll
                if not pend:
                    return "No pending enroll found. Please send the enroll request again with CONFIRM ENROLL first."
                sequence_id = pend.get("sequence_id")
                contact_id = pend.get("contact_id")
                sender_email = pend.get("sender_email")
                if stream_callback:
                    stream_callback("⚠️ Proceeding with enrollment. Sequences may send emails.\n")
                    stream_callback("📤 Enrolling contact into HubSpot sequence...\n")
                result = hs_enroll(
                    sequence_id=str(sequence_id),
                    contact_id=str(contact_id),
                    sender_email=str(sender_email),
                )
                self._last_pending_enroll = None
                return json.dumps(result, indent=2)

            # --- Confirm auto enroll (two-step safeguard) ---
            if re.search(r"\bconfirm\b", text, re.IGNORECASE) and re.search(r"\bauto\b", text, re.IGNORECASE) and re.search(r"\benroll\b", text, re.IGNORECASE):
                if not self._last_preview or self._last_preview.get("mode") != "auto":
                    return "No pending auto-enroll preview found. First run an enroll request with emails and a topic."
                seq_id = self._last_preview.get("sequence_id")
                sender_email = self._last_preview.get("sender_email")
                items = [i for i in self._last_preview.get("items", []) if i.get("contact_id")]
                if not items:
                    return "Nothing to enroll: no resolved contacts in the last auto-enroll preview."
                if not isinstance(sender_email, str) or not sender_email:
                    return "Invalid senderEmail. Please rerun the auto-enroll preview with a valid sender email."
                if not re.search(r"\bfinal\b", text, re.IGNORECASE):
                    self._last_preview["confirm_state"] = "armed"
                    return (
                        "⚠️ Final safeguard: This will enroll contacts into a live sequence, which may send emails.\n"
                        + f"Planned action: sequenceId={seq_id}, senderEmail={sender_email}, contacts={len(items)}.\n"
                        + "To proceed, reply: FINAL CONFIRM AUTO ENROLL\n"
                    )
                if stream_callback:
                    stream_callback("⚠️ Proceeding with auto enrollment. Sequences may send emails.\n")
                    stream_callback("📤 Enrolling contacts into HubSpot sequence...\n")
                results = []
                for it in items:
                    try:
                        res = hs_enroll(
                            sequence_id=str(seq_id),
                            contact_id=str(it["contact_id"]),
                            sender_email=str(sender_email),
                        )
                        results.append({"email": it["email"], "result": res})
                    except Exception as ex:
                        results.append({"email": it["email"], "error": str(ex)})
                return json.dumps({"sequenceId": seq_id, "results": results}, indent=2)

            # Generic "tell me about our sequences" only when explicitly asking across org
            if re.search(r"\b(all\s+sequences|across\s+all\s+users|across\s+all\s+owners)\b", text, re.IGNORECASE):
                if stream_callback:
                    stream_callback(
                        "🔍 Aggregating sequences across all owners (with details)...\n"
                    )
                sequences = self._aggregate_sequences_all_users(
                    filters={}, details=True, stream_callback=stream_callback
                )
                md_lines = ["## Sequences — Aggregated (all users)", ""]
                for s in sequences:
                    sid = str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or "?")
                    name = (
                        s.get("name")
                        or (
                            s.get("metadata", {}).get("name")
                            if isinstance(s.get("metadata"), dict)
                            else None
                        )
                        or "(no name)"
                    )
                    steps = self._count_steps(s) or self._count_steps(s.get("details", {}) if isinstance(s.get("details"), dict) else {})
                    active_val = self._extract_bool(s, ["active","isActive","enabled","isEnabled"]) or self._extract_bool(s.get("details", {}) if isinstance(s.get("details"), dict) else {}, ["active","isActive","enabled","isEnabled"]) or None
                    active_str = "Yes" if active_val is True else ("No" if active_val is False else "Unknown")
                    owners = s.get("source_user_ids")
                    o = f" — Sources: {', '.join(map(str, owners))}" if owners else ""
                    md_lines.append(f"- `{sid}` — {name} — Active: {active_str} — Steps: {steps}{o}")
                md = "\n".join(md_lines)
                if stream_callback:
                    for ch in md:
                        stream_callback(ch)
                return md

            # Default: no verbose help; emit a brief status and concise note
            if stream_callback:
                stream_callback("⚠️ No recognized sequence request. Include an owner email or say 'all sequences'.\n")
            return "No recognized sequence request. Try 'list sequences owner_email: you@yourco.com' or 'show all active sequences'."

        except Exception as e:
            error_msg = f"❌ **Error during sequence operation:** {str(e)}\n\nPlease try again or contact support if the issue persists."
            if stream_callback:
                stream_callback(error_msg)
            return error_msg

    @staticmethod
    def _extract_value(text: str, pattern: str) -> Optional[str]:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None
    
    @staticmethod
    def _extract_kv_email(text: str, key: str) -> Optional[str]:
        m = re.search(rf"\b{re.escape(key)}[:\s]*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{{2,}})", text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_contact_id(res: Any) -> Optional[str]:
        try:
            if not res:
                return None
            if isinstance(res, dict):
                if res.get("id"):
                    return str(res.get("id"))
                results = res.get("results") or []
                if results and isinstance(results, list):
                    first = results[0]
                    if isinstance(first, dict) and first.get("id"):
                        return str(first.get("id"))
            return None
        except Exception:
            return None

    @staticmethod
    def _normalize_sequence_list(data: Any) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results") or data.get("items") or []
        return []

    @staticmethod
    def _extract_bool(obj: Dict[str, Any], keys: list) -> Optional[bool]:
        for k in keys:
            if k in obj:
                v = obj[k]
                if isinstance(v, bool):
                    return v
                if isinstance(v, str):
                    if v.lower() in ("true", "false"):
                        return v.lower() == "true"
        return None

    @staticmethod
    def _count_steps(obj: Dict[str, Any]) -> int:
        if isinstance(obj.get("steps"), list):
            return len(obj["steps"])
        if isinstance(obj.get("sequenceSteps"), list):
            return len(obj["sequenceSteps"])
        return 0

    @staticmethod
    def _extract_owner(obj: Dict[str, Any]) -> Optional[str]:
        v = obj.get("owner") or obj.get("ownerEmail") or obj.get("createdBy")
        if isinstance(v, dict):
            return v.get("email") or v.get("id")
        return str(v) if v else None

    def _format_sequence_detail_markdown(self, data: Dict[str, Any]) -> str:
        seq = data if isinstance(data, dict) else {}
        sid = str(seq.get("id") or seq.get("sequenceId") or seq.get("sequence_id") or "?")
        name = (
            seq.get("name")
            or (seq.get("metadata", {}).get("name") if isinstance(seq.get("metadata"), dict) else None)
            or "(no name)"
        )
        active = self._extract_bool(seq, ["active", "isActive", "enabled", "isEnabled"]) or False
        owner = self._extract_owner(seq) or "unknown"
        steps = seq.get("steps") or seq.get("sequenceSteps") or []
        if not isinstance(steps, list) and isinstance(seq.get("data"), dict):
            d = seq.get("data", {})
            steps = d.get("steps") or d.get("sequenceSteps") or []
        step_lines = []
        total_days = 0
        for idx, stp in enumerate(steps, start=1):
            if not isinstance(stp, dict):
                continue
            stype = stp.get("type") or stp.get("stepType") or stp.get("channel") or "Step"
            delay = stp.get("delayDays") or stp.get("delay") or 0
            try:
                d_int = int(delay)
            except Exception:
                d_int = 0
            total_days = max(total_days, d_int)
            subj = stp.get("subject") or stp.get("title") or ""
            note = stp.get("notes") or stp.get("description") or ""
            line = f"{idx}) Day {d_int} — {stype}"
            if subj:
                line += f" — Subject: {subj}"
            if note and not subj:
                line += f" — {note}"
            step_lines.append(line)
        md_lines = [
            f"## Sequence: {name} (id: {sid})",
            f"- Owner: {owner}",
            f"- Active: {'Yes' if active else 'No'}",
            f"- Steps: {len(step_lines)} (~{total_days} days)",
            "",
        ]
        if step_lines:
            md_lines.extend(step_lines)
        else:
            md_lines.append("(No step details available)")
        return "\n".join(md_lines)

    def _maybe_enrich_with_details(
        self,
        sequences: list,
        user_id: str,
        require_details: bool,
        stream_callback: Optional[Callable[[str], None]],
        max_details: int = 50,
    ) -> list:
        """Optionally fetch per-sequence details for better filtering and display.
        Adds a `details` key on each sequence dict when fetched.
        """
        if not require_details or not sequences:
            return sequences
        limit = min(len(sequences), max_details)
        for i in range(limit):
            s = sequences[i]
            sid = str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or "")
            if not sid:
                continue
            try:
                if stream_callback:
                    stream_callback(f"• Fetching details for sequence {sid}...\n")
                detail = _cached_get_sequence_impl(sequence_id=sid, user_id=user_id)
                s["details"] = detail
            except Exception:
                s["details_error"] = True
        if len(sequences) > limit and stream_callback:
            stream_callback(f"⚠️ Details truncated to first {limit} sequences for performance.\n")
        return sequences

    def _filter_sequences(
        self, sequences: list, filters: Dict[str, Any], text: str
    ) -> list:
        try:
            if re.search(r"\b(active only|only active)\b", text, re.IGNORECASE) or (
                re.search(r"\bactive\b", text, re.IGNORECASE)
                and not re.search(r"\binactive\b|\bnot\s+active\b", text, re.IGNORECASE)
            ):
                filters.setdefault("active", True)
            m = re.search(r"name\s*contains\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
            if m:
                filters.setdefault("name_contains", m.group(1))
            m2 = re.search(r"min\s*steps\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
            if m2:
                filters.setdefault("min_steps", int(m2.group(1)))
            m_gt = re.search(r"\b(more than|greater than|over)\s*(\d+)\s*steps?\b", text, re.IGNORECASE)
            if m_gt:
                try:
                    val = int(m_gt.group(2)) + 1
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            m_ge = re.search(r"\b(at least)\s*(\d+)\s*steps?\b", text, re.IGNORECASE)
            if m_ge:
                try:
                    val = int(m_ge.group(2))
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            m_sym_gt = re.search(r"steps\s*>\s*(\d+)", text, re.IGNORECASE)
            if m_sym_gt:
                try:
                    val = int(m_sym_gt.group(1)) + 1
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            m_sym_gte = re.search(r"steps\s*>?=\s*(\d+)", text, re.IGNORECASE)
            if m_sym_gte:
                try:
                    val = int(m_sym_gte.group(1))
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            m_sym_lt = re.search(r"steps\s*<\s*(\d+)", text, re.IGNORECASE)
            if m_sym_lt:
                try:
                    val = int(m_sym_lt.group(1)) - 1
                    if val >= 0:
                        filters.setdefault("max_steps", val)
                except Exception:
                    pass
            m_sym_lte = re.search(r"steps\s*<=\s*(\d+)", text, re.IGNORECASE)
            if m_sym_lte:
                try:
                    val = int(m_sym_lte.group(1))
                    filters.setdefault("max_steps", val)
                except Exception:
                    pass
        except Exception:
            pass

        def matches(seq: Dict[str, Any]) -> bool:
            name = (
                seq.get("name")
                or (
                    seq.get("metadata", {}).get("name")
                    if isinstance(seq.get("metadata"), dict)
                    else None
                )
                or ""
            )
            if "name_contains" in filters:
                if filters["name_contains"].lower() not in str(name).lower():
                    return False
            if "active" in filters:
                active = (
                    self._extract_bool(
                        seq, ["active", "isActive", "enabled", "isEnabled"]
                    )
                    or False
                )
                if bool(filters["active"]) != bool(active):
                    return False
            if "min_steps" in filters:
                if self._count_steps(seq) < int(filters["min_steps"]):
                    return False
            if "max_steps" in filters:
                if self._count_steps(seq) > int(filters["max_steps"]):
                    return False
            return True

        if not filters:
            return sequences
        return [s for s in sequences if matches(s)]

    def _aggregate_sequences_all_users(
        self,
        filters: Dict[str, Any],
        details: bool,
        stream_callback: Optional[Callable[[str], None]],
    ) -> list:
        owners = []
        try:
            owners = hs_list_all_owner_user_ids(active_only=True)
        except HubSpotError:
            owners = []
        if not owners:
            env_ids = os.getenv("HUBSPOT_OWNER_USER_IDS", "").strip()
            if env_ids:
                if stream_callback:
                    stream_callback(
                        "⚠️ Owners scope missing; aggregating via HUBSPOT_OWNER_USER_IDS fallback...\n"
                    )
                user_ids = [uid.strip() for uid in env_ids.split(",") if uid.strip()]
                return self._aggregate_from_user_ids(user_ids, filters, details, stream_callback)
            return []
        agg: Dict[str, Dict[str, Any]] = {}
        for idx, o in enumerate(owners, start=1):
            uid = o.get("userId")
            if not uid:
                continue
            if stream_callback:
                stream_callback(
                    f"• Collecting sequences for userId={uid} ({idx}/{len(owners)})...\n"
                )
            items = hs_list_all_sequences(user_id=uid, page_size=100)
            seqs = self._normalize_sequence_list({"results": items})
            for s in seqs:
                sid = str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or "")
                if not sid:
                    continue
                entry = agg.get(sid)
                if not entry:
                    entry = {"id": sid, "sources": [uid], "raw": s}
                    agg[sid] = entry
                else:
                    if uid not in entry["sources"]:
                        entry["sources"].append(uid)
                entry["raw"] = s
        result = []
        for i, (sid, entry) in enumerate(agg.items(), start=1):
            rec = dict(entry["raw"])
            rec["id"] = sid
            rec["source_user_ids"] = entry["sources"]
            if details and entry["sources"]:
                uid = entry["sources"][0]
                try:
                    if stream_callback:
                        stream_callback(
                            f"• Fetching details for sequence {sid} via userId={uid}...\n"
                        )
                    detail = hs_get_sequence(sequence_id=sid, user_id=uid)
                    rec["details"] = detail
                except Exception:
                    rec["details_error"] = "Failed to fetch details"
            result.append(rec)
        return self._filter_sequences(result, filters, "")

    def _aggregate_from_user_ids(
        self,
        user_ids: list,
        filters: Dict[str, Any],
        details: bool,
        stream_callback: Optional[Callable[[str], None]],
    ) -> list:
        agg: Dict[str, Dict[str, Any]] = {}
        for idx, uid in enumerate(user_ids, start=1):
            if not uid:
                continue
            if stream_callback:
                stream_callback(
                    f"• Collecting sequences for userId={uid} ({idx}/{len(user_ids)})...\n"
                )
            items = hs_list_all_sequences(user_id=str(uid), page_size=100)
            seqs = self._normalize_sequence_list({"results": items})
            for s in seqs:
                sid = str(s.get("id") or s.get("sequenceId") or s.get("sequence_id") or "")
                if not sid:
                    continue
                entry = agg.get(sid)
                if not entry:
                    entry = {"id": sid, "sources": [uid], "raw": s}
                    agg[sid] = entry
                else:
                    if uid not in entry["sources"]:
                        entry["sources"].append(uid)
                entry["raw"] = s
        result = []
        for sid, entry in agg.items():
            rec = dict(entry["raw"])
            rec["id"] = sid
            rec["source_user_ids"] = entry["sources"]
            if details and entry["sources"]:
                uid = entry["sources"][0]
                try:
                    if stream_callback:
                        stream_callback(
                            f"• Fetching details for sequence {sid} via userId={uid}...\n"
                        )
                    detail = hs_get_sequence(sequence_id=sid, user_id=uid)
                    rec["details"] = detail
                except Exception:
                    rec["details_error"] = "Failed to fetch details"
            result.append(rec)
        return self._filter_sequences(result, filters, "")
    # ---- New helpers for email copy rendering and search ----
    def _email_step_predicate(self, step: Dict[str, Any]) -> bool:
        if not isinstance(step, dict):
            return False
        stype = (step.get("type") or step.get("stepType") or step.get("channel") or "").lower()
        return ("email" in stype) or (step.get("email") is not None)

    def _extract_step_subject(self, step: Dict[str, Any]) -> str:
        if not isinstance(step, dict):
            return ""
        email = step.get("email") or {}
        return email.get("subject") or step.get("subject") or step.get("title") or ""

    def _extract_step_body(self, step: Dict[str, Any]) -> str:
        if not isinstance(step, dict):
            return ""
        email = step.get("email") or {}
        body = email.get("body") or step.get("body") or step.get("notes") or step.get("description") or ""
        return str(body)

    def _extract_delay_days(self, step: Dict[str, Any]) -> int:
        delay = step.get("delayDays") or step.get("delay") or 0
        try:
            return int(delay)
        except Exception:
            return 0

    def _render_sequence_email_copy(
        self,
        sequence: Dict[str, Any],
        *,
        step_only: Optional[int] = None,
        mask_tokens: bool = False,
        include_html: bool = False,
        include_full: bool = False,
    ) -> str:
        seq = sequence or {}
        sid = str(seq.get("id") or seq.get("sequenceId") or "?")
        name = (
            seq.get("name")
            or (seq.get("metadata", {}).get("name") if isinstance(seq.get("metadata"), dict) else None)
            or "(no name)"
        )
        steps = seq.get("steps") or seq.get("sequenceSteps") or []
        if not isinstance(steps, list) and isinstance(seq.get("data"), dict):
            steps = seq.get("data", {}).get("steps") or []
        email_steps = [s for s in (steps or []) if self._email_step_predicate(s)]
        if not email_steps:
            return f"## Email Copy — {name} (id: {sid})\n\n(No email steps or copy available)\n"

        # Step targeting (1-based index)
        if step_only is not None:
            if 1 <= step_only <= len(email_steps):
                email_steps = [email_steps[step_only - 1]]
            else:
                return (
                    f"## Email Copy — {name} (id: {sid})\n\nRequested step {step_only} not found (email steps: {len(email_steps)})."
                )

        out = [f"## Email Copy — {name} (id: {sid})", ""]
        start_index = 1 if step_only is None else step_only
        for idx_offset, stp in enumerate(email_steps, start=0):
            idx = start_index + idx_offset
            day = self._extract_delay_days(stp)
            subj = self._extract_step_subject(stp) or "(no subject)"
            body = self._extract_step_body(stp) or "(body unavailable)"
            body_out = self._mask_tokens(body) if mask_tokens else body
            # Optional truncation guard
            if not include_full:
                lines = body_out.splitlines()
                if len(lines) > 60:
                    body_out = "\n".join(lines[:60]) + "\n… (truncated; add 'full: true' to view all)"
                if len(body_out) > 2000:
                    body_out = body_out[:2000] + "\n… (truncated; add 'full: true' to view all)"
            out.append(f"### Step {idx} — Day {day} — Subject: {subj}")
            out.append("```text")
            out.append(body_out.strip())
            out.append("```")
            if include_html and ("<" in body and ">" in body):
                out.append("```html")
                out.append(body.strip())
                out.append("```")
            out.append("")
        return "\n".join(out)

    def _sequence_matches_query(self, seq: Dict[str, Any], q: str) -> bool:
        ql = (q or "").strip().lower()
        if not ql:
            return True
        name = (
            seq.get("name")
            or (seq.get("metadata", {}).get("name") if isinstance(seq.get("metadata"), dict) else "")
            or ""
        )
        if ql in str(name).lower():
            return True
        details = seq.get("details", {}) if isinstance(seq.get("details"), dict) else {}
        steps = details.get("steps") or seq.get("steps") or seq.get("sequenceSteps") or []
        if not isinstance(steps, list) and isinstance(details.get("data"), dict):
            steps = details.get("data", {}).get("steps") or []
        for s in steps or []:
            subject = (self._extract_step_subject(s) or "").lower()
            body = (self._extract_step_body(s) or "").lower()
            notes = str(s.get("notes") or s.get("description") or "").lower()
            title = str(s.get("title") or "").lower()
            if any(ql in part for part in (subject, body, notes, title)):
                return True
        return False

    def _search_sequences_text(self, sequences: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        if not query:
            return sequences
        return [s for s in sequences if self._sequence_matches_query(s, query)]

    def _channel_match(self, step: Dict[str, Any]) -> str:
        stype = (step.get("type") or step.get("stepType") or step.get("channel") or "").lower()
        if "email" in stype:
            return "email"
        if "task" in stype:
            return "task"
        if "call" in stype:
            return "call"
        return stype

    def _sequence_matches_query_with_channel(self, seq: Dict[str, Any], q: str, channel: str) -> bool:
        if not self._sequence_matches_query(seq, q):
            return False
        if not channel:
            return True
        details = seq.get("details", {}) if isinstance(seq.get("details"), dict) else {}
        steps = details.get("steps") or seq.get("steps") or seq.get("sequenceSteps") or []
        if not isinstance(steps, list) and isinstance(details.get("data"), dict):
            steps = details.get("data", {}).get("steps") or []
        return any(self._channel_match(s) == channel for s in steps or [])

    def _search_sequences_text_with_channel(self, sequences: List[Dict[str, Any]], query: str, channel: str) -> List[Dict[str, Any]]:
        if not query and not channel:
            return sequences
        return [s for s in sequences if self._sequence_matches_query_with_channel(s, query, channel)]

    def _mask_tokens(self, text: str) -> str:
        if not text:
            return text or ""
        return re.sub(r"(\{\{.*?\}\}|\[\[.*?\]\]|%.*?%)", "[[TOKEN]]", text, flags=re.DOTALL)

    # Owner email -> userId resolution (class-local to avoid missing attribute issues)
    def _resolve_user_id_from_email(
        self, owner_email: str, stream_callback: Optional[Callable[[str], None]]
    ) -> Optional[str]:
        if not owner_email:
            return None
        try:
            if stream_callback:
                stream_callback("🔎 Resolving owner email to userId via HubSpot owners API...\n")
            owners = hs_list_all_owner_user_ids(active_only=True)
            email_lc = owner_email.strip().lower()
            for o in owners:
                em = (o.get("email") or "").strip().lower()
                if not em and isinstance(o.get("user"), dict):
                    em = (o["user"].get("email") or "").strip().lower()
                if em == email_lc:
                    uid = o.get("userId")
                    if uid:
                        return str(uid)
        except HubSpotError:
            # Fallback to env var mapping JSON
            mapping_raw = os.getenv("HUBSPOT_OWNER_EMAIL_MAP", "").strip()
            if mapping_raw:
                try:
                    import json as _json
                    mapping = _json.loads(mapping_raw)
                    uid = mapping.get(owner_email.lower()) or mapping.get(owner_email)
                    if uid:
                        return str(uid)
                except Exception:
                    pass
        return None


# Module-level LRU cache for sequence details
@lru_cache(maxsize=512)
def _cached_get_sequence_impl(sequence_id: str, user_id: str) -> Dict[str, Any]:
    return hs_get_sequence(sequence_id=sequence_id, user_id=user_id)

    def _filter_sequences(
        self, sequences: list, filters: Dict[str, Any], text: str
    ) -> list:
        # Build filters from natural text if present
        try:
            if re.search(r"\b(active only|only active)\b", text, re.IGNORECASE) or (
                re.search(r"\bactive\b", text, re.IGNORECASE)
                and not re.search(r"\binactive\b|\bnot\s+active\b", text, re.IGNORECASE)
            ):
                filters.setdefault("active", True)
            m = re.search(r"name\s*contains\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
            if m:
                filters.setdefault("name_contains", m.group(1))
            # Explicit min steps
            m2 = re.search(r"min\s*steps\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
            if m2:
                filters.setdefault("min_steps", int(m2.group(1)))
            # Natural phrases: more than / greater than / over N steps
            m_gt = re.search(r"\b(more than|greater than|over)\s*(\d+)\s*steps?\b", text, re.IGNORECASE)
            if m_gt:
                try:
                    val = int(m_gt.group(2)) + 1
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            # Natural phrases: at least N steps
            m_ge = re.search(r"\b(at least)\s*(\d+)\s*steps?\b", text, re.IGNORECASE)
            if m_ge:
                try:
                    val = int(m_ge.group(2))
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            # Symbolic: steps > N / steps >= N / steps < N / steps <= N
            m_sym_gt = re.search(r"steps\s*>\s*(\d+)", text, re.IGNORECASE)
            if m_sym_gt:
                try:
                    val = int(m_sym_gt.group(1)) + 1
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            m_sym_gte = re.search(r"steps\s*>?=\s*(\d+)", text, re.IGNORECASE)
            if m_sym_gte:
                try:
                    val = int(m_sym_gte.group(1))
                    filters.setdefault("min_steps", val)
                except Exception:
                    pass
            m_sym_lt = re.search(r"steps\s*<\s*(\d+)", text, re.IGNORECASE)
            if m_sym_lt:
                try:
                    val = int(m_sym_lt.group(1)) - 1
                    if val >= 0:
                        filters.setdefault("max_steps", val)
                except Exception:
                    pass
            m_sym_lte = re.search(r"steps\s*<=\s*(\d+)", text, re.IGNORECASE)
            if m_sym_lte:
                try:
                    val = int(m_sym_lte.group(1))
                    filters.setdefault("max_steps", val)
                except Exception:
                    pass
        except Exception:
            pass

        def matches(seq: Dict[str, Any]) -> bool:
            name = (
                seq.get("name")
                or (
                    seq.get("metadata", {}).get("name")
                    if isinstance(seq.get("metadata"), dict)
                    else None
                )
                or ""
            )
            if "name_contains" in filters:
                if filters["name_contains"].lower() not in str(name).lower():
                    return False
            if "active" in filters:
                active = (
                    self._extract_bool(
                        seq, ["active", "isActive", "enabled", "isEnabled"]
                    )
                    or False
                )
                if bool(filters["active"]) != bool(active):
                    return False
            if "min_steps" in filters:
                if self._count_steps(seq) < int(filters["min_steps"]):
                    return False
            if "max_steps" in filters:
                if self._count_steps(seq) > int(filters["max_steps"]):
                    return False
            return True

        if not filters:
            return sequences
        return [s for s in sequences if matches(s)]

    def _aggregate_sequences_all_users(
        self,
        filters: Dict[str, Any],
        details: bool,
        stream_callback: Optional[Callable[[str], None]],
    ) -> list:
        """Aggregate sequences across all owners/users; optionally fetch details for each unique sequence."""
        owners = []
        try:
            owners = hs_list_all_owner_user_ids(active_only=True)
        except HubSpotError:
            owners = []
        # Fallback: use HUBSPOT_OWNER_USER_IDS if owners scope is missing or returned empty
        if not owners:
            env_ids = os.getenv("HUBSPOT_OWNER_USER_IDS", "").strip()
            if env_ids:
                if stream_callback:
                    stream_callback("⚠️ Owners scope missing; aggregating via HUBSPOT_OWNER_USER_IDS fallback...\n")
                user_ids = [uid.strip() for uid in env_ids.split(',') if uid.strip()]
                return self._aggregate_from_user_ids(user_ids, filters, details, stream_callback)
            # No fallback configured
            return []
        # Map of sequenceId -> aggregated record with primary_user_id and optional details
        agg: Dict[str, Dict[str, Any]] = {}
        for idx, o in enumerate(owners, start=1):
            uid = o.get("userId")
            if not uid:
                continue
            if stream_callback:
                stream_callback(
                    f"• Collecting sequences for userId={uid} ({idx}/{len(owners)})...\n"
                )
            items = hs_list_all_sequences(user_id=uid, page_size=100)
            seqs = self._normalize_sequence_list({"results": items})
            for s in seqs:
                # Normalize id field
                sid = str(
                    s.get("id") or s.get("sequenceId") or s.get("sequence_id") or ""
                )
                if not sid:
                    continue
                entry = agg.get(sid)
                if not entry:
                    entry = {"id": sid, "sources": [uid], "raw": s}
                    agg[sid] = entry
                else:
                    if uid not in entry["sources"]:
                        entry["sources"].append(uid)
                # keep a canonical raw snapshot
                entry["raw"] = s
        # Optionally fetch details for each unique sequence using first source userId
        result = []
        for i, (sid, entry) in enumerate(agg.items(), start=1):
            rec = dict(entry["raw"])
            rec["id"] = sid
            rec["source_user_ids"] = entry["sources"]
            if details and entry["sources"]:
                uid = entry["sources"][0]
                try:
                    if stream_callback:
                        stream_callback(
                            f"• Fetching details for sequence {sid} via userId={uid}...\n"
                        )
                    detail = hs_get_sequence(sequence_id=sid, user_id=uid)
                    rec["details"] = detail
                except Exception as _:
                    rec["details_error"] = "Failed to fetch details"
            result.append(rec)
        # Apply filters after aggregation for name/active/steps
        return self._filter_sequences(result, filters, "")

    def _resolve_user_id_from_email(
        self, owner_email: str, stream_callback: Optional[Callable[[str], None]]
    ) -> Optional[str]:
        if not owner_email:
            return None
        try:
            if stream_callback:
                stream_callback(
                    "🔎 Resolving owner email to userId via HubSpot owners API...\n"
                )
            owners = hs_list_all_owner_user_ids(active_only=True)
            match = next(
                (
                    o
                    for o in owners
                    if str(o.get("email", "")).lower() == owner_email.lower()
                ),
                None,
            )
            if match and match.get("userId"):
                return str(match.get("userId"))
        except HubSpotError:
            # Fallback to env var mapping
            mapping_raw = os.getenv("HUBSPOT_OWNER_EMAIL_MAP", "").strip()
            if mapping_raw:
                if stream_callback:
                    stream_callback(
                        "⚠️ Owners scope missing; using HUBSPOT_OWNER_EMAIL_MAP fallback...\n"
                    )
                try:
                    import json as _json

                    mapping = _json.loads(mapping_raw)
                    uid = mapping.get(owner_email.lower()) or mapping.get(owner_email)
                    if uid:
                        return str(uid)
                except Exception:
                    pass
        return None

    def _aggregate_from_user_ids(
        self,
        user_ids: list,
        filters: Dict[str, Any],
        details: bool,
        stream_callback: Optional[Callable[[str], None]],
    ) -> list:
        agg: Dict[str, Dict[str, Any]] = {}
        for idx, uid in enumerate(user_ids, start=1):
            if not uid:
                continue
            if stream_callback:
                stream_callback(
                    f"• Collecting sequences for userId={uid} ({idx}/{len(user_ids)})...\n"
                )
            items = hs_list_all_sequences(user_id=str(uid), page_size=100)
            seqs = self._normalize_sequence_list({"results": items})
            for s in seqs:
                sid = str(
                    s.get("id") or s.get("sequenceId") or s.get("sequence_id") or ""
                )
                if not sid:
                    continue
                entry = agg.get(sid)
                if not entry:
                    entry = {"id": sid, "sources": [uid], "raw": s}
                    agg[sid] = entry
                else:
                    if uid not in entry["sources"]:
                        entry["sources"].append(uid)
                entry["raw"] = s
        result = []
        for sid, entry in agg.items():
            rec = dict(entry["raw"])
            rec["id"] = sid
            rec["source_user_ids"] = entry["sources"]
            if details and entry["sources"]:
                uid = entry["sources"][0]
                try:
                    if stream_callback:
                        stream_callback(
                            f"• Fetching details for sequence {sid} via userId={uid}...\n"
                        )
                    detail = hs_get_sequence(sequence_id=sid, user_id=uid)
                    rec["details"] = detail
                except Exception:
                    rec["details_error"] = "Failed to fetch details"
            result.append(rec)
        return self._filter_sequences(result, filters, "")

    def get_capabilities(self) -> Dict[str, Any]:
        """
        Return the capabilities and tools available to this agent.
        """
        return {
            "model": self.model,
            "specializations": [
                "Sequence enrollment automation",
                "Campaign workflow management",
                "Follow-up scheduling",
                "Multi-channel sequence coordination",
                "Performance tracking and optimization",
                "CRM integration management",
            ],
        }
