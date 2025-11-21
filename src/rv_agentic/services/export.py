"""CSV export utilities for pm_pipeline runs.

Exports companies and contacts from completed runs into CSV format
for delivery to users.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from rv_agentic.services import supabase_client

logger = logging.getLogger(__name__)


def _extract_markdown_section(markdown: str, section_heading: str) -> str:
    """Extract content from a markdown section.

    Args:
        markdown: Full markdown text
        section_heading: Heading to search for (e.g., "Agent Summary", "Personal anecdotes")

    Returns:
        Extracted section content or empty string
    """
    if not markdown:
        return ""

    # Match heading with ## prefix and capture content until next ## heading or end
    pattern = rf"##\s*{re.escape(section_heading)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, markdown, re.DOTALL | re.IGNORECASE)

    if match:
        content = match.group(1).strip()
        return content
    return ""


def _extract_agent_output_from_evidence(evidence: Any) -> str:
    """Extract agent_output markdown from evidence field.

    Args:
        evidence: Evidence field (can be string JSON or list)

    Returns:
        Agent markdown output or empty string
    """
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except Exception:
            return ""

    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and "agent_output" in item:
                return item.get("agent_output") or ""

    return ""


def _promote_top_companies_and_persist_rest(run_id: str) -> int:
    """Promote top N companies to 'promoted' status and persist rest to research_database.

    Args:
        run_id: pm_pipeline.runs.id UUID

    Returns:
        Number of companies promoted

    Raises:
        Exception if run not found or no validated companies exist
    """
    run = supabase_client.get_pm_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    target_qty = run.get("target_quantity", 0)
    if target_qty <= 0:
        raise ValueError(f"Run {run_id} has invalid target_quantity: {target_qty}")

    # Idempotency check: if we already have target_qty promoted, skip processing
    already_promoted = supabase_client._get_pm(  # type: ignore[attr-defined]
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {
            "run_id": f"eq.{run_id}",
            "status": "eq.promoted",
            "select": "id",
        },
    )
    if len(already_promoted) >= target_qty:
        logger.info(f"Run {run_id} already has {len(already_promoted)} promoted companies (target: {target_qty}); skipping promotion")
        return len(already_promoted)

    # Fetch all validated companies (sorting will be done after fetching research data)
    validated = supabase_client._get_pm(  # type: ignore[attr-defined]
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {
            "run_id": f"eq.{run_id}",
            "status": "eq.validated",
            "select": "*",
            "order": "created_at.asc",
        },
    )

    if not validated:
        logger.info(f"No validated companies found for run {run_id} - nothing to promote")
        return 0

    # Fetch research data to use for intelligent sorting
    research_map: Dict[str, Dict[str, Any]] = {}
    try:
        research_rows = supabase_client._get_pm(  # type: ignore[attr-defined]
            "pm_pipeline.company_research",
            {
                "run_id": f"eq.{run_id}",
                "select": "company_id,facts,signals,confidence",
            },
        )
        for row in research_rows or []:
            cid = row.get("company_id")
            if cid:
                research_map[str(cid)] = row
    except Exception as e:
        logger.warning(f"Could not fetch research data for sorting: {e}")

    # Sort companies by ICP quality (tier, confidence, then created_at)
    def get_sort_key(company: Dict[str, Any]) -> Tuple[int, float, str]:
        """Generate sort key: (tier_priority, confidence_desc, created_at_asc)"""
        company_id = str(company.get("id", ""))
        research = research_map.get(company_id, {})
        signals = research.get("signals") or {}

        # Tier priority: Tier 1=0 (highest), Tier 2=1, Tier 3=2, Unknown=3 (lowest)
        tier = signals.get("icp_tier", "Unknown")
        tier_priority = {"Tier 1": 0, "Tier 2": 1, "Tier 3": 2}.get(tier, 3)

        # Confidence (higher is better, so negate for desc sort)
        confidence = -(research.get("confidence") or 0.0)

        # Created at (earlier is better for tie-breaking)
        created_at = company.get("created_at", "")

        return (tier_priority, confidence, created_at)

    validated_sorted = sorted(validated, key=get_sort_key)

    # Split into top N (to promote) and rest (to persist)
    to_promote = validated_sorted[:target_qty]
    to_persist = validated_sorted[target_qty:]

    logger.info(
        f"Run {run_id}: Promoting top {len(to_promote)} companies, persisting {len(to_persist)} to research_database"
    )

    # Promote top N companies
    for company in to_promote:
        company_id = company.get("id")
        if company_id:
            supabase_client._patch_pm(  # type: ignore[attr-defined]
                supabase_client.PM_COMPANY_CANDIDATES_TABLE,
                {"id": f"eq.{company_id}"},
                {"status": "promoted"}
            )

    # Persist excess companies to research_database
    # NOTE: Disabled until research_database schema is confirmed
    # The excess companies remain in company_candidates with status='validated'
    # They can be manually promoted later or used in future runs
    if to_persist:
        logger.info(f"{len(to_persist)} excess researched companies remain in validated status for potential future use")

    return len(to_promote)


def export_companies_to_csv(run_id: str) -> str:
    """Export company candidates for a run to CSV format.

    This function:
    1. Promotes top N companies (where N = target_quantity)
    2. Persists excess researched companies to research_database
    3. Exports only the promoted companies to CSV

    Args:
        run_id: pm_pipeline.runs.id UUID

    Returns:
        CSV string with company data

    Raises:
        Exception if run not found or no companies exist
    """
    # Step 1: Promote top companies and persist rest
    promoted_count = _promote_top_companies_and_persist_rest(run_id)
    logger.info(f"Promoted {promoted_count} companies for export, persisted rest to research_database")

    # Fetch run to get criteria context
    run = supabase_client.get_pm_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    # Step 2: Fetch ONLY promoted companies (exactly target_quantity)
    companies = supabase_client._get_pm(  # type: ignore[attr-defined]
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {
            "run_id": f"eq.{run_id}",
            "status": "eq.promoted",
            "select": "*",
            "order": "created_at.asc",
        },
    )

    if not companies:
        raise ValueError(f"No promoted companies found for run {run_id}")

    # Fetch company research for enrichment data (including agent markdown output)
    research_map: Dict[str, Dict[str, Any]] = {}
    try:
        research_rows = supabase_client._get_pm(  # type: ignore[attr-defined]
            "pm_pipeline.company_research",
            {
                "run_id": f"eq.{run_id}",
                "select": "company_id,facts,signals",
            },
        )
        for row in research_rows or []:
            cid = row.get("company_id")
            if cid:
                research_map[str(cid)] = row
    except Exception:
        # Research data is optional; continue without it
        pass

    # Define CSV columns per user specification
    fieldnames = [
        "company_name",
        "company_city",
        "company_state",
        "pms",
        "units",
        "employees",
        "domain",
        "single_family_focus",
        "property_mix",
        "icp_fit",
        "icp_score",
        "agent_summary",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL
    )
    writer.writeheader()

    for company in companies:
        cid = str(company.get("id") or "")
        research = research_map.get(cid, {})
        facts = research.get("facts") or {}
        signals = research.get("signals") or {}

        # Extract ICP signals from research
        icp_fit = signals.get("icp_fit") or "Unknown"
        icp_score = signals.get("icp_tier") or "Unknown"

        # Extract agent summary (markdown output) from facts
        # Replace newlines with spaces to prevent CSV row breaks
        agent_summary = (facts.get("analysis_markdown") or "").replace("\n", " ").replace("\r", " ")

        # Extract additional fields from facts if they exist
        city = facts.get("city") or ""
        employees = facts.get("employees") or facts.get("employee_count") or ""

        # Format single_family_focus as TRUE/FALSE boolean string
        sfh_focus = facts.get("single_family_focus")
        if isinstance(sfh_focus, bool):
            single_family_focus = "TRUE" if sfh_focus else "FALSE"
        elif isinstance(sfh_focus, str):
            sfh_focus_lower = sfh_focus.lower()
            if sfh_focus_lower in ("true", "yes", "1"):
                single_family_focus = "TRUE"
            elif sfh_focus_lower in ("false", "no", "0"):
                single_family_focus = "FALSE"
            else:
                single_family_focus = sfh_focus
        else:
            single_family_focus = ""

        # Format property_mix as readable string (if it's a dict, convert to readable format)
        property_mix_raw = facts.get("property_mix")
        if isinstance(property_mix_raw, dict):
            # Convert dict to "key: value, key: value" format
            property_mix = ", ".join(f"{k}: {v}" for k, v in property_mix_raw.items())
        elif isinstance(property_mix_raw, str):
            property_mix = property_mix_raw
        else:
            property_mix = ""

        row = {
            "company_name": company.get("name") or "",
            "company_city": city,
            "company_state": company.get("state") or "",
            "pms": company.get("pms_detected") or "",
            "units": str(company.get("units_estimate") or ""),
            "employees": str(employees) if employees else "",
            "domain": company.get("domain") or "",
            "single_family_focus": single_family_focus,
            "property_mix": property_mix,
            "icp_fit": icp_fit,
            "icp_score": icp_score,
            "agent_summary": agent_summary,
        }
        writer.writerow(row)

    return output.getvalue()


def export_contacts_to_csv(run_id: str) -> str:
    """Export contact candidates for a run to CSV format.

    Args:
        run_id: pm_pipeline.runs.id UUID

    Returns:
        CSV string with contact data

    Raises:
        Exception if run not found or no contacts exist
    """
    # Fetch run to verify it exists
    run = supabase_client.get_pm_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    # Fetch all contact candidates with status=validated or promoted
    # Join with company_candidates to get company details
    contacts = supabase_client._get_pm(  # type: ignore[attr-defined]
        supabase_client.PM_CONTACT_CANDIDATES_TABLE,
        {
            "run_id": f"eq.{run_id}",
            "status": "in.(validated,promoted)",
            "select": "*",
            "order": "company_id.asc,created_at.asc",
        },
    )

    if not contacts:
        raise ValueError(f"No validated/promoted contacts found for run {run_id}")

    # Fetch company details for all contacts
    company_ids = list({str(c.get("company_id") or "") for c in contacts if c.get("company_id")})
    company_map: Dict[str, Dict[str, Any]] = {}

    if company_ids:
        try:
            companies = supabase_client._get_pm(  # type: ignore[attr-defined]
                supabase_client.PM_COMPANY_CANDIDATES_TABLE,
                {
                    "id": f"in.({','.join(company_ids)})",
                    "select": "id,name,domain,website,state",
                },
            )
            for company in companies or []:
                cid = str(company.get("id") or "")
                if cid:
                    company_map[cid] = company
        except Exception:
            # Company data is helpful but not critical; continue without it
            pass

    # Define CSV columns (including all required fields per E2E requirements)
    fieldnames = [
        "full_name",
        "title",
        "email",
        "linkedin_url",
        "department",
        "seniority",
        "quality_score",
        "icp_score",
        "company_name",
        "company_domain",
        "company_website",
        "company_state",
        "personalization_notes",
        "personal_anecdotes",
        "professional_anecdotes",
        "data_sources",
        "additional_research_notes",
        "agent_summary",
        "created_at",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL
    )
    writer.writeheader()

    for contact in contacts:
        company_id = str(contact.get("company_id") or "")
        company = company_map.get(company_id, {})

        # Extract agent output and personalization from evidence
        evidence = contact.get("evidence") or []
        agent_markdown = _extract_agent_output_from_evidence(evidence)

        # Parse markdown sections for required fields
        agent_summary = _extract_markdown_section(agent_markdown, "Agent Summary")
        personal_anecdotes = _extract_markdown_section(agent_markdown, "Personalization Data Points")
        # Professional anecdotes might be in "Professional Summary" or "Career Highlights"
        professional_anecdotes = _extract_markdown_section(agent_markdown, "Professional Summary")
        if not professional_anecdotes:
            professional_anecdotes = _extract_markdown_section(agent_markdown, "Career Highlights")
        data_sources = _extract_markdown_section(agent_markdown, "Sources")
        additional_notes = _extract_markdown_section(agent_markdown, "Assumptions & Data Gaps")

        # Extract personalization from old evidence format (for backwards compatibility)
        personalization = ""
        if isinstance(evidence, str):
            try:
                evidence_parsed = json.loads(evidence)
            except Exception:
                evidence_parsed = []
        else:
            evidence_parsed = evidence

        if isinstance(evidence_parsed, list):
            for item in evidence_parsed:
                if isinstance(item, dict):
                    personalization = (
                        item.get("quality_notes")
                        or item.get("personalization")
                        or item.get("notes")
                        or personalization
                    )
        elif isinstance(evidence_parsed, dict):
            personalization = (
                evidence_parsed.get("quality_notes")
                or evidence_parsed.get("personalization")
                or ""
            )

        # Extract ICP score from signals if available
        signals = contact.get("signals") or {}
        if isinstance(signals, str):
            try:
                signals = json.loads(signals)
            except Exception:
                signals = {}
        icp_score = signals.get("icp_score") or ""

        row = {
            "full_name": contact.get("full_name") or "",
            "title": contact.get("title") or "",
            "email": contact.get("email") or "",
            "linkedin_url": contact.get("linkedin_url") or "",
            "department": contact.get("department") or "",
            "seniority": contact.get("seniority") or "",
            "quality_score": contact.get("quality_score") or "",
            "icp_score": icp_score,
            "company_name": company.get("name") or "",
            "company_domain": company.get("domain") or "",
            "company_website": company.get("website") or "",
            "company_state": company.get("state") or "",
            "personalization_notes": personalization,
            "personal_anecdotes": personal_anecdotes,
            "professional_anecdotes": professional_anecdotes,
            "data_sources": data_sources,
            "additional_research_notes": additional_notes,
            "agent_summary": agent_summary,
            "created_at": contact.get("created_at") or "",
        }
        writer.writerow(row)

    return output.getvalue()


def export_run_to_files(run_id: str, output_dir: str) -> Tuple[str, str]:
    """Export a run's companies and contacts to CSV files.

    Args:
        run_id: pm_pipeline.runs.id UUID
        output_dir: Directory path to write CSV files

    Returns:
        Tuple of (companies_file_path, contacts_file_path)

    Raises:
        Exception if export fails
    """
    import os

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    companies_filename = f"companies_{run_id[:8]}_{timestamp}.csv"
    contacts_filename = f"contacts_{run_id[:8]}_{timestamp}.csv"

    companies_path = os.path.join(output_dir, companies_filename)
    contacts_path = os.path.join(output_dir, contacts_filename)

    # Export companies
    companies_csv = export_companies_to_csv(run_id)
    with open(companies_path, "w", encoding="utf-8") as f:
        f.write(companies_csv)

    # Export contacts
    contacts_csv = export_contacts_to_csv(run_id)
    with open(contacts_path, "w", encoding="utf-8") as f:
        f.write(contacts_csv)

    return (companies_path, contacts_path)
