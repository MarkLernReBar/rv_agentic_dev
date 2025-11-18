"""Sequence Enroller Agent implemented with the OpenAI Agents SDK.

This version delegates HubSpot sequence operations to Python tools
and lets the model orchestrate enrollment flows using natural
language, instead of hand-written parsing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents import Agent
from agents.model_settings import ModelSettings, Reasoning
from agents.tool import function_tool

from rv_agentic.services.hubspot_client import (
    HubSpotError,
    enroll_contact_in_sequence as hs_enroll,
    get_sequence as hs_get_sequence,
    list_all_sequences as hs_list_all_sequences,
    list_sequences as hs_list_sequences,
    search_contact as hs_search_contact,
)


SEQUENCE_ENROLLER_SYSTEM_PROMPT = """# 📧 Sequence Enroller Agent

You are the **Sequence Enroller Agent**, an expert at managing automated outreach
sequences, campaign enrollment, and follow-up optimization in HubSpot.

## Your responsibilities

- Recommend appropriate sequences for prospects based on their ICP, role, and intent.
- Preview email copy and steps when the user asks to inspect a sequence.
- Enroll contacts into sequences (single or bulk) *only when the user gives clear consent*.
- Respect two-step confirm flows when the user uses phrases like
  `CONFIRM ENROLL`, `FINAL CONFIRM ENROLL`, `CONFIRM BULK ENROLL`, or
  `FINAL CONFIRM BULK ENROLL`.

## Tools

- `list_hubspot_sequences` to list sequences (optionally by owner_email).
- `get_hubspot_sequence` to inspect a specific sequence’s steps and content.
- `search_hubspot_contact` to resolve contacts from email or name+company.
- `Enroll_contacts_in_sequence` to actually enroll contacts in a sequence
  once the user has clearly confirmed.

## Guardrails

- Never enroll contacts without either explicit natural-language confirmation
  or a confirm phrase (e.g. `FINAL CONFIRM ENROLL`).
- When in doubt, ask for clarification instead of guessing.
- Keep responses concise, external-facing, and Markdown-only (no JSON, no code fences).
"""


@function_tool
def list_hubspot_sequences(owner_email: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """List HubSpot sequences, optionally filtered by owner email."""

    try:
        if owner_email:
            return hs_list_sequences(owner_email=owner_email, limit=limit) or []
        return hs_list_all_sequences(limit=limit) or []
    except HubSpotError:
        return []


@function_tool
def get_hubspot_sequence(sequence_id: int, owner_email: Optional[str] = None) -> Dict[str, Any]:
    """Fetch a single HubSpot sequence by ID (and optional owner scope)."""

    try:
        seq = hs_get_sequence(sequence_id=sequence_id, owner_email=owner_email)
        return seq or {}
    except HubSpotError:
        return {}


@function_tool
def search_hubspot_contact(query: str) -> Dict[str, Any]:
    """Search HubSpot for a contact using email or name+company."""

    q = (query or "").strip()
    if not q:
        return {}
    try:
        contact = hs_search_contact(q)
        return contact or {}
    except HubSpotError:
        return {}


@function_tool
def enroll_contacts_in_sequence(
    sequence_id: int,
    owner_email: str,
    contact_emails: List[str],
) -> Dict[str, Any]:
    """Enroll one or more contacts (by email) into a HubSpot sequence."""

    results: List[Dict[str, Any]] = []
    for email in contact_emails:
        e = (email or "").strip()
        if not e:
            continue
        try:
            res = hs_enroll(sequence_id=sequence_id, owner_email=owner_email, email=e)
            results.append({"email": e, "status": "enrolled", "result": res})
        except HubSpotError as exc:
            results.append({"email": e, "status": "error", "error": str(exc)})
    return {"sequence_id": sequence_id, "owner_email": owner_email, "results": results}


def create_sequence_enroller_agent(name: str = "Sequence Enroller") -> Agent:
    """Factory for the Sequence Enroller agent."""

    return Agent(
        name=name,
        instructions=SEQUENCE_ENROLLER_SYSTEM_PROMPT,
        tools=[
            list_hubspot_sequences,
            get_hubspot_sequence,
            search_hubspot_contact,
            enroll_contacts_in_sequence,
        ],
        model="gpt-5-nano",
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="medium"),
        ),
    )
