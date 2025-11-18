"""Utility to backfill researched companies/contacts to research_database tables.

This ensures no researched data goes to waste per CLAUDE.md rule #3:
- If a company/contact is fully researched but doesn't make final output
- And it's net new (not already in companies/contacts or HubSpot)
- Copy it to research_database for future reuse
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from rv_agentic.services import supabase_client

logger = logging.getLogger(__name__)


def backfill_company_to_research_db(
    domain: str,
    name: str,
    company_data: Dict[str, Any],
    research_data: Dict[str, Any] | None = None
) -> bool:
    """Backfill a researched company to companies table if it's net new.

    Args:
        domain: Company domain
        name: Company name
        company_data: Basic company info (website, state, city, etc.)
        research_data: Research insights (facts, signals, icp_summary)

    Returns:
        True if backfilled, False if already exists or error
    """
    try:
        # Check if already in research_database.companies
        existing = supabase_client.get_company_by_domain(domain)
        if existing:
            logger.debug(f"Company {domain} already in research_database.companies")
            return False

        # TODO: Check if in HubSpot (requires hubspot_client integration)
        # For now, skip HubSpot check and just ensure not in our DB

        # Prepare insert data
        insert_data = {
            "domain": domain,
            "name": name,
            "website": company_data.get("website"),
            "state": company_data.get("state"),
            "city": company_data.get("city"),
            "source": "backfill_from_pipeline",
        }

        # Add research insights if available
        if research_data:
            insert_data.update({
                "icp_summary": research_data.get("icp_summary"),
                "facts": research_data.get("facts"),  # JSONB
                "signals": research_data.get("signals"),  # JSONB
            })

        # Insert to companies table
        result = supabase_client.upsert_company(insert_data)

        if result:
            logger.info(f"Backfilled company {domain} to research_database.companies")
            return True
        else:
            logger.warning(f"Failed to backfill company {domain}")
            return False

    except Exception as e:
        logger.error(f"Error backfilling company {domain}: {e}")
        return False


def backfill_contact_to_research_db(
    email: str,
    contact_data: Dict[str, Any],
    company_domain: str | None = None
) -> bool:
    """Backfill a researched contact to contacts table if it's net new.

    Args:
        email: Contact email
        contact_data: Contact info (name, title, linkedin, etc.)
        company_domain: Associated company domain

    Returns:
        True if backfilled, False if already exists or error
    """
    try:
        # Check if already in research_database.contacts
        existing = supabase_client.get_contact_by_email(email)
        if existing:
            logger.debug(f"Contact {email} already in research_database.contacts")
            return False

        # TODO: Check if in HubSpot contacts

        # Prepare insert data
        insert_data = {
            "email": email,
            "name": contact_data.get("name"),
            "first_name": contact_data.get("first_name"),
            "last_name": contact_data.get("last_name"),
            "title": contact_data.get("title"),
            "linkedin_url": contact_data.get("linkedin_url"),
            "company_domain": company_domain,
            "source": "backfill_from_pipeline",
        }

        # Insert to contacts table
        result = supabase_client.upsert_contact(insert_data)

        if result:
            logger.info(f"Backfilled contact {email} to research_database.contacts")
            return True
        else:
            logger.warning(f"Failed to backfill contact {email}")
            return False

    except Exception as e:
        logger.error(f"Error backfilling contact {email}: {e}")
        return False


def backfill_run_companies(run_id: str) -> Dict[str, int]:
    """Backfill all researched companies from a run that didn't make final output.

    This should be called after company research stage completes.

    Args:
        run_id: The pm_pipeline run ID

    Returns:
        Dict with counts: {"total": N, "backfilled": M, "skipped": K}
    """
    try:
        # Get all company_candidates with research data for this run
        conn = supabase_client._pg_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                cc.domain, cc.name, cc.website, cc.state, cc.city,
                cr.icp_summary, cr.facts, cr.signals
            FROM pm_pipeline.company_candidates cc
            LEFT JOIN pm_pipeline.company_research cr
                ON cc.run_id = cr.run_id AND cc.company_id = cr.company_id
            WHERE cc.run_id = %s
                AND cr.icp_summary IS NOT NULL  -- Only backfill researched companies
        """, (run_id,))

        companies = cur.fetchall()
        conn.close()

        total = len(companies)
        backfilled = 0

        for row in companies:
            domain, name, website, state, city, icp_summary, facts, signals = row

            company_data = {
                "website": website,
                "state": state,
                "city": city,
            }

            research_data = {
                "icp_summary": icp_summary,
                "facts": facts,
                "signals": signals,
            }

            if backfill_company_to_research_db(domain, name, company_data, research_data):
                backfilled += 1

        skipped = total - backfilled
        logger.info(f"Run {run_id} backfill: {backfilled}/{total} companies backfilled, {skipped} skipped")

        return {"total": total, "backfilled": backfilled, "skipped": skipped}

    except Exception as e:
        logger.error(f"Error backfilling run {run_id} companies: {e}")
        return {"total": 0, "backfilled": 0, "skipped": 0}


def backfill_run_contacts(run_id: str) -> Dict[str, int]:
    """Backfill all researched contacts from a run.

    This should be called after contact discovery stage completes.

    Args:
        run_id: The pm_pipeline run ID

    Returns:
        Dict with counts: {"total": N, "backfilled": M, "skipped": K}
    """
    try:
        # Get all contact_candidates for this run
        conn = supabase_client._pg_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT
                cc.email, cc.name, cc.first_name, cc.last_name,
                cc.title, cc.linkedin_url,
                cand.domain as company_domain
            FROM pm_pipeline.contact_candidates cc
            JOIN pm_pipeline.company_candidates cand
                ON cc.run_id = cand.run_id AND cc.company_id = cand.company_id
            WHERE cc.run_id = %s
                AND cc.email IS NOT NULL
        """, (run_id,))

        contacts = cur.fetchall()
        conn.close()

        total = len(contacts)
        backfilled = 0

        for row in contacts:
            email, name, first_name, last_name, title, linkedin_url, company_domain = row

            contact_data = {
                "name": name,
                "first_name": first_name,
                "last_name": last_name,
                "title": title,
                "linkedin_url": linkedin_url,
            }

            if backfill_contact_to_research_db(email, contact_data, company_domain):
                backfilled += 1

        skipped = total - backfilled
        logger.info(f"Run {run_id} backfill: {backfilled}/{total} contacts backfilled, {skipped} skipped")

        return {"total": total, "backfilled": backfilled, "skipped": skipped}

    except Exception as e:
        logger.error(f"Error backfilling run {run_id} contacts: {e}")
        return {"total": 0, "backfilled": 0, "skipped": 0}
