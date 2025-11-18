"""CSV export utilities for pm_pipeline runs.

Exports companies and contacts from completed runs into CSV format
for delivery to users.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from rv_agentic.services import supabase_client


def export_companies_to_csv(run_id: str) -> str:
    """Export company candidates for a run to CSV format.

    Args:
        run_id: pm_pipeline.runs.id UUID

    Returns:
        CSV string with company data

    Raises:
        Exception if run not found or no companies exist
    """
    # Fetch run to get criteria context
    run = supabase_client.get_pm_run(run_id)
    if not run:
        raise ValueError(f"Run {run_id} not found")

    # Fetch all company candidates with status=validated or promoted
    companies = supabase_client._get_pm(  # type: ignore[attr-defined]
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {
            "run_id": f"eq.{run_id}",
            "status": "in.(validated,promoted)",
            "select": "*",
            "order": "created_at.asc",
        },
    )

    if not companies:
        raise ValueError(f"No validated/promoted companies found for run {run_id}")

    # Fetch company research for enrichment data
    research_map: Dict[str, Dict[str, Any]] = {}
    try:
        research_rows = supabase_client._get_pm(  # type: ignore[attr-defined]
            supabase_client.PM_COMPANY_RESEARCH_TABLE,
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

    # Define CSV columns
    fieldnames = [
        "company_name",
        "domain",
        "website",
        "state",
        "pms_detected",
        "units_estimate",
        "company_type",
        "description",
        "discovery_source",
        "icp_fit",
        "icp_tier",
        "icp_confidence",
        "disqualifiers",
        "created_at",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for company in companies:
        cid = str(company.get("id") or "")
        research = research_map.get(cid, {})
        facts = research.get("facts") or {}
        signals = research.get("signals") or {}

        # Extract ICP signals from research
        icp_fit = signals.get("icp_fit") or "Unknown"
        icp_tier = signals.get("icp_tier") or "Unknown"
        icp_confidence = signals.get("icp_confidence") or facts.get("icp_confidence") or ""
        disqualifiers = signals.get("disqualifiers") or facts.get("disqualifiers") or ""

        row = {
            "company_name": company.get("name") or "",
            "domain": company.get("domain") or "",
            "website": company.get("website") or "",
            "state": company.get("state") or "",
            "pms_detected": company.get("pms_detected") or "",
            "units_estimate": company.get("units_estimate") or "",
            "company_type": company.get("company_type") or "",
            "description": company.get("description") or "",
            "discovery_source": company.get("discovery_source") or "",
            "icp_fit": icp_fit,
            "icp_tier": icp_tier,
            "icp_confidence": icp_confidence,
            "disqualifiers": disqualifiers,
            "created_at": company.get("created_at") or "",
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

    # Define CSV columns
    fieldnames = [
        "full_name",
        "title",
        "email",
        "linkedin_url",
        "department",
        "seniority",
        "quality_score",
        "company_name",
        "company_domain",
        "company_website",
        "company_state",
        "personalization_notes",
        "created_at",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for contact in contacts:
        company_id = str(contact.get("company_id") or "")
        company = company_map.get(company_id, {})

        # Extract personalization from evidence if present
        evidence = contact.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}

        personalization = ""
        if isinstance(evidence, dict):
            personalization = (
                evidence.get("quality_notes")
                or evidence.get("personalization")
                or ""
            )

        row = {
            "full_name": contact.get("full_name") or "",
            "title": contact.get("title") or "",
            "email": contact.get("email") or "",
            "linkedin_url": contact.get("linkedin_url") or "",
            "department": contact.get("department") or "",
            "seniority": contact.get("seniority") or "",
            "quality_score": contact.get("quality_score") or "",
            "company_name": company.get("name") or "",
            "company_domain": company.get("domain") or "",
            "company_website": company.get("website") or "",
            "company_state": company.get("state") or "",
            "personalization_notes": personalization,
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
