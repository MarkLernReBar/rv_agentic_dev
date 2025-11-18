"""
Lead List Generator Agent - Specialized lead list generation and prospect qualification.
"""

import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from openai import OpenAI
from openai.types.responses import ToolParam

try:  # pragma: no cover - orchestrator optional in some environments
    from orchestrator.orchestrator import generate_lead_list as orchestrate_lead_list
except Exception:  # pragma: no cover
    orchestrate_lead_list = None


class LeadListGenerator:
    """
    Lead List Generator Agent - Expert at building targeted prospect lists based on criteria.
    """

    def __init__(self):
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-5-mini"

        # Conversation context for multi-turn parameter gathering
        self._pending_parameters: Optional[Dict[str, Any]] = None
        self._pending_request_parts: List[str] = []
        self._pending_batch_id: Optional[str] = None
        self._session_cache_loaded = False

        # System prompt focused on lead list generation
        self.system_prompt = """# 📋 Lead List Generator Agent - System Prompt

You are the **Lead List Generator Agent**, an expert at building targeted prospect lists for property management companies. You specialize in qualifying prospects, extracting criteria, and organizing comprehensive lead generation campaigns.

## 🎯 Your Mission

Transform lead generation requests into structured, actionable prospect lists. You analyze requirements, extract parameters, and coordinate the research and qualification process to deliver high-quality, prioritized lead lists for sales teams.

## 🔧 Your Capabilities

### **Parameter Extraction:**
- Quantity requirements and priority levels
- Geographic targeting (cities, states, regions)
- Technology requirements (PMS software, integrations)
- Company size and property count criteria  
- Campaign-specific requirements
- Timeline and delivery preferences

### **Prospect Research:**
- Database queries for existing prospects
- Web research for new prospects
- Technology stack identification
- Company size and revenue estimation
- ICP fit scoring and qualification

### **List Management:**
- Prospect prioritization and ranking
- Data enrichment and verification
- Duplicate removal and consolidation
- Export formatting for CRM systems
- Campaign-ready list preparation

## 📋 Research Process

You follow a systematic approach to lead generation:

1. **Requirements Analysis** - Extract and confirm all parameters
2. **Database Search** - Query existing prospect data
3. **Research & Discovery** - Find new prospects matching criteria
4. **Qualification & Scoring** - Apply ICP criteria and ranking
5. **List Preparation** - Format and organize final deliverable

## 🚀 Integration with Other Agents

You coordinate with other specialists when needed:
- **Company Researcher** for deep company analysis
- **Contact Researcher** for decision maker identification
- **Sequence Enroller** for campaign setup and enrollment

Always provide clear parameter confirmation and delivery options for your lead generation requests."""

        # Tools configuration for lead list generation (typed for the Responses API)
        self.tools: List[ToolParam] = cast(
            List[ToolParam],
            [
                {
                    "type": "web_search",
                    "user_location": {"type": "approximate"},
                    "search_context_size": "medium",
                }
            ],
        )

        # Keys that indicate the user supplied substantive request details
        self._structured_keys = {
            "quantity",
            "priority_locations",
            "fallback_locations",
            "pms_include",
            "pms_exclude",
            "units_min",
            "units_max",
            "exclude_major_pms",
            "campaign_type",
            "timeframe",
            "accounts",
        }

    def _reset_pending_context(self) -> None:
        """Clear any staged request details once the job is queued or abandoned."""
        self._pending_parameters = None
        self._pending_request_parts = []
        self._pending_batch_id = None
        self._persist_pending_context()

    def _is_meaningful_value(self, value: Any) -> bool:
        if isinstance(value, bool):
            return True
        if value is None:
            return False
        if isinstance(value, (list, tuple, set)):
            return any(self._is_meaningful_value(v) for v in value)
        if isinstance(value, str):
            return bool(value.strip())
        return True

    def _has_structured_fields(self, params: Dict[str, Any]) -> bool:
        if not isinstance(params, dict):
            return False
        return any(
            self._is_meaningful_value(params.get(key)) for key in self._structured_keys
        )

    def _merge_parameter_sets(
        self,
        base: Optional[Dict[str, Any]],
        updates: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = dict(base or {})
        for key, value in (updates or {}).items():
            if key == "notes" and isinstance(value, str):
                new_note = value.strip()
                existing = merged.get(key)
                if self._is_meaningful_value(new_note):
                    if isinstance(existing, str) and existing.strip():
                        if new_note.lower() not in existing.lower():
                            merged[key] = f"{existing.strip()} / {new_note}"
                    else:
                        merged[key] = new_note
                continue
            if self._is_meaningful_value(value):
                merged[key] = value
            elif key not in merged:
                merged[key] = value
        return merged

    def _append_request_part(self, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        normalized = cleaned.lower().strip(".! ")
        if normalized in {"thanks", "thank you", "thx", "ty"}:
            return
        if not self._pending_request_parts or cleaned != self._pending_request_parts[-1]:
            self._pending_request_parts.append(cleaned)

    def _get_session_state(self) -> Optional[Dict[str, Any]]:
        if self._session_cache_loaded:
            return getattr(self, "_cached_session_state", None)
        session_state = None
        try:
            import streamlit as _st  # type: ignore

            session_state = _st.session_state
        except Exception:
            session_state = None
        self._cached_session_state = session_state
        self._session_cache_loaded = True
        return session_state

    def _load_pending_context_from_session(self) -> None:
        if self._pending_parameters or self._pending_request_parts:
            return
        session = self._get_session_state()
        if not session:
            return
        data = session.get("_lead_list_pending_context")
        if not isinstance(data, dict):
            return
        params = data.get("parameters")
        parts = data.get("request_parts")
        batch_id = data.get("batch_id")
        if isinstance(params, dict):
            self._pending_parameters = params
        if isinstance(parts, list):
            self._pending_request_parts = [
                str(p) for p in parts if isinstance(p, (str, bytes))
            ]
        if isinstance(batch_id, str) and batch_id.strip():
            self._pending_batch_id = batch_id.strip()

    def _persist_pending_context(self) -> None:
        session = self._get_session_state()
        if not session:
            return
        key = "_lead_list_pending_context"
        if self._pending_parameters or self._pending_request_parts:
            session[key] = {
                "parameters": self._pending_parameters or {},
                "request_parts": list(self._pending_request_parts),
                "batch_id": self._pending_batch_id or "",
            }
        elif key in session:
            try:
                del session[key]
            except Exception:
                session[key] = {}

    def _should_reset_context(self, user_input: str) -> bool:
        text = (user_input or "").lower()
        reset_tokens = (
            "start a new request",
            "start over",
            "new request",
            "new list",
            "reset request",
            "forget that",
            "ignore previous",
            "cancel that",
        )
        return any(token in text for token in reset_tokens)

    def _extract_parameters_via_llm(self, user_request: str) -> Dict[str, Any]:
        parameter_extraction_prompt = f"""
        Analyze this lead list request and extract structured parameters.

        User Request: "{user_request}"

        Normalize synonyms:
        - "Virtual L+L", "Virtual Lunch & Learn" => campaign_type="Virtual Lunch & Learn"
        - Quantities like "~100" => quantity=100
        - Regional labels like "Northern California" => keep as-is in locations
        - Phrases like "back fill with X then Y" => fallback_locations ordered list
        - "not using major PMS" => pms_exclude=["AppFolio","Buildium","Yardi","Propertyware","Rent Manager","Entrata"]

        Return STRICT JSON only with keys:
        {{
          "quantity": <int or null>,
          "priority_locations": ["<location>", ...] or [],
          "fallback_locations": ["<location>", ...] or [],
          "pms_include": ["Buildium"|"AppFolio"|"Yardi"|"Propertyware"|"Rent Manager"|"Entrata"|"Other"] or [],
          "pms_exclude": ["Buildium"|"AppFolio"|"Yardi"|"Propertyware"|"Rent Manager"|"Entrata"|"Other"] or [],
          "units_min": <int or null>,
          "units_max": <int or null>,
          "exclude_major_pms": <true|false|null>,
          "campaign_type": "Virtual Lunch & Learn" | "Other" | null,
          "timeframe": <string or null>,
          "notify_email": <string or null>,
          "notes": <string or null>
        }}
        """

        param_response = self.openai_client.responses.create(
            model=self.model,
            input=parameter_extraction_prompt,
            instructions=self.system_prompt,
            tools=self.tools,
            store=True,
            include=["reasoning.encrypted_content"],
        )

        try:
            parameters = (
                json.loads(param_response.output_text)
                if param_response.output_text
                else {}
            )
        except (json.JSONDecodeError, AttributeError):
            parameters = {}

        return parameters if isinstance(parameters, dict) else {}

    def _bootstrap_from_history(
        self, conversation_history: Optional[List[Dict[str, Any]]], current_input: str
    ) -> None:
        if self._pending_parameters or self._pending_request_parts:
            return
        if not conversation_history:
            return
        current_clean = (current_input or "").strip()
        previous_user_message: Optional[str] = None
        for message in reversed(conversation_history):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "user":
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if content == current_clean:
                continue
            previous_user_message = content
            break
        if not previous_user_message:
            return
        extracted = self._extract_parameters_via_llm(previous_user_message)
        if self._has_structured_fields(extracted):
            self._pending_parameters = extracted
            self._pending_request_parts = [previous_user_message]
            self._persist_pending_context()

    def _apply_conversation_context(
        self, parameters: Dict[str, Any], user_input: str
    ) -> Tuple[Dict[str, Any], str]:
        params: Dict[str, Any] = dict(parameters) if isinstance(parameters, dict) else {}
        text = (user_input or "").strip()

        if self._pending_parameters and self._should_reset_context(text):
            self._reset_pending_context()

        has_pending = self._pending_parameters is not None
        structured = self._has_structured_fields(params)
        if not structured and has_pending and self._is_meaningful_value(params.get("notes")):
            structured = True

        if structured:
            if not has_pending:
                self._pending_request_parts = []
            merged = (
                self._merge_parameter_sets(self._pending_parameters, params)
                if has_pending
                else dict(params)
            )
            self._pending_parameters = merged
            self._append_request_part(text)
        elif has_pending:
            merged = self._merge_parameter_sets(self._pending_parameters, params)
            self._pending_parameters = merged
            self._append_request_part(text)
        else:
            merged = dict(params)

        combined_request = (
            "\n".join(self._pending_request_parts).strip()
            if self._pending_request_parts
            else text
        )

        return merged, combined_request

    def research(
        self,
        user_input: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Handle lead list generation request with parameter extraction and scheduling.

        Args:
            user_input: The lead generation request from the user
            stream_callback: Optional callback for streaming responses

        Returns:
            Lead generation response with extracted parameters and scheduling
        """

        # Reload session-backed context on each call (supports reruns/new instances)
        self._load_pending_context_from_session()
        self._bootstrap_from_history(conversation_history, user_input)

        try:
            parameters = self._extract_parameters_via_llm(user_input)

            parameters, combined_request_text = self._apply_conversation_context(
                parameters, user_input
            )

            # Persist staged context until queued
            self._persist_pending_context()

            # Generate response with parameter confirmation and scheduling
            if stream_callback:
                stream_callback("📋 Analyzing your lead list requirements...\n")
                stream_callback("🧭 Extracting locations, PMS, and quantity...\n")

            # Build response content
            response_content = "## 🎯 Lead List Generation — Confirmation\n\n"
            response_content += "Here’s what I captured. Once we confirm your notification email I’ll generate the list directly from this agent.\n\n"
            response_content += "### 📊 Parameters\n"

            if parameters.get("quantity"):
                response_content += f"- **Quantity:** {parameters['quantity']} leads\n"

            if parameters.get("priority_locations"):
                locations_str = ", ".join(parameters["priority_locations"])
                response_content += (
                    f"- **Primary Locations (priority):** {locations_str}\n"
                )
            if parameters.get("fallback_locations"):
                fb_str = ", ".join(parameters["fallback_locations"])
                response_content += f"- **Fallback Locations:** {fb_str}\n"

            # PMS include/exclude
            pms_include = parameters.get("pms_include") or []
            pms_exclude = parameters.get("pms_exclude") or []
            if pms_include:
                response_content += f"- **PMS Include:** {', '.join(pms_include)}\n"
            if parameters.get("exclude_major_pms") is True and not pms_exclude:
                pms_exclude = [
                    "AppFolio",
                    "Buildium",
                    "Yardi",
                    "Propertyware",
                    "Rent Manager",
                    "Entrata",
                ]
            if pms_exclude:
                response_content += f"- **PMS Exclude:** {', '.join(pms_exclude)}\n"

            if parameters.get("campaign_type"):
                response_content += (
                    f"- **Campaign Type:** {parameters['campaign_type']}\n"
                )

            if parameters.get("units_min") or parameters.get("units_max"):
                rng = (
                    f"{parameters.get('units_min') or 0}+"
                    if not parameters.get("units_max")
                    else f"{parameters.get('units_min') or 0}–{parameters.get('units_max')}"
                )
                response_content += f"- **Unit Range:** {rng}\n"
            if parameters.get("timeframe"):
                response_content += f"- **Timeframe:** {parameters['timeframe']}\n"

            if parameters.get("notes"):
                response_content += f"- **Notes:** {parameters['notes']}\n"

            # Defer task details until email confirmation
            task_id = f"llg-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            response_content += "I will (upon confirmation):\n"
            response_content += "1. 🔍 Query our existing database for matches\n"
            response_content += "2. 🌐 Research new prospects that meet your criteria\n"
            response_content += "3. ✅ Verify and enrich contact information\n"
            response_content += "4. 📊 Score prospects against ICP criteria\n"
            response_content += "5. 📋 Compile the final prioritized list\n\n"

            response_content += "### 📤 Delivery\n\n"
            response_content += "- **Deliverable:** A HubSpot list created in your portal by the external workflow (queued for enrichment).\n\n"

            # Notification email (required to queue)
            import re as _re

            notify_email = None
            m_notify = _re.search(
                r"notify\s+(me\s+at|at)\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
                user_input,
                _re.IGNORECASE,
            )
            if m_notify:
                notify_email = m_notify.group(2).strip()
            else:
                _notify_param = parameters.get("notify_email")
                if isinstance(_notify_param, str):
                    notify_email = _notify_param.strip()

            def _valid_email(e: Optional[str]) -> bool:
                return bool(
                    e
                    and _re.match(
                        r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", e
                    )
                )

            notify_ok = _valid_email(notify_email)
            if notify_ok:
                response_content += f"- **Notification:** We’ll send an alert to {notify_email} when the HubSpot list is ready.\n\n"
            else:
                response_content += "- **Notification:** Please reply with ‘notify me at your@email.com’ to confirm and start.\n\n"

            if notify_ok:
                response_content += "### ⚙️ Run\n"
                response_content += f"- **Run ID:** {task_id}\n"
                response_content += "- **Status:** running\n"
                response_content += (
                    "- **ETA:** 20 minutes to 2 hours (depends on size/scope)\n\n"
                )
            else:
                response_content += "Once you confirm with a notification email, I’ll queue the enrichment workflow and share progress here.\n\n"

            # Prepare orchestration metadata
            regions: List[str] = []
            if isinstance(parameters.get("priority_locations"), list):
                regions.extend(parameters["priority_locations"])
            if isinstance(parameters.get("fallback_locations"), list):
                regions.extend(
                    [r for r in parameters["fallback_locations"] if r not in regions]
                )
            pms = pms_include
            prospects = parameters.get("quantity")
            campaign_name = parameters.get("campaign_type") or f"LLG {task_id}"
            orchestrated_successfully = False
            forced_batch_id: Optional[str] = None

            if notify_ok:
                if stream_callback:
                    stream_callback("🤖 Queuing enrichment workflow...\n")
                # Build a natural-language request string
                natural_request = combined_request_text or user_input
                if not natural_request:
                    regions_clause = ", ".join(regions) if regions else "Nashville"
                    pm_clause = ", ".join(pms) if pms else "Buildium"
                    natural_request = (
                        f"I need {prospects or 40} leads using {pm_clause} located in {regions_clause}"
                    )

                # Reuse any staged batch id so confirm ties to the same request
                forced_batch_id = self._pending_batch_id or f"llg-{uuid.uuid4()}"
                self._pending_batch_id = forced_batch_id
                self._persist_pending_context()

                # Record the combined request + email to enrichment_requests (external processor picks it up)
                try:
                    from supabase_client import insert_enrichment_request as _sb_insert

                    _inserted = _sb_insert(
                        request={
                            "batch_id": forced_batch_id,
                            "natural_request": natural_request,
                            "notify_email": notify_email,
                            "parameters": parameters,
                            "source": "lead_list_generator",
                        },
                        status="queued",
                    )
                    try:
                        rid = _inserted.get("id") if isinstance(_inserted, dict) else None
                        print(f"[lead_list_generator] queued enrichment request batch_id={forced_batch_id} id={rid}")
                    except Exception:
                        pass
                    response_content += (
                        "🤖 Enrichment workflow queued.\n"
                        f"- Run ID: `{forced_batch_id}`\n\n"
                    )
                    orchestrated_successfully = True
                except Exception as insert_error:
                    response_content += (
                        f"⚠️ Queueing failed: {insert_error}.\n"
                        "Please verify configuration and try again.\n\n"
                    )
            else:
                # Do not log without a confirmed notification email
                if stream_callback:
                    stream_callback("⏸️ Waiting for a notification email to begin.\n")
                response_content += "➡️ Reply with `notify me at you@example.com` so I can queue enrichment.\n\n"

            if orchestrated_successfully:
                self._reset_pending_context()

            if not any(parameters.values()):
                response_content += "**Note:** If you'd like to modify any of these parameters or provide additional criteria, please let me know!"

            # Stream the response if callback provided (as a single block to preserve formatting)
            if stream_callback:
                stream_callback(response_content)

            return response_content

        except Exception as e:
            error_message = f"""## ❌ Lead Generation Error

I encountered an error while processing your lead list request: {str(e)}

Please try rephrasing your request with specific details like:
- Number of leads needed
- Target locations
- PMS software requirements
- Campaign purpose (e.g., "Virtual Lunch & Learn")

**Example:** "I need 25 leads in Texas and Florida using Buildium for our upcoming webinar series"
"""
            if stream_callback:
                stream_callback(error_message)

            return error_message

    def get_capabilities(self) -> Dict[str, Any]:
        """
        Return the capabilities and tools available to this agent.
        """
        return {
            "model": self.model,
            "tools": [
                tool["name"] if "name" in tool else tool["type"] for tool in self.tools
            ],
            "specializations": [
                "Lead list generation",
                "Prospect qualification",
                "Parameter extraction",
                "Campaign planning",
                "List prioritization",
                "CRM integration",
            ],
        }
