"""Async lead list worker that advances pm_pipeline runs.

This is a placeholder for a long-running process that:

- Polls the pm_pipeline.runs table for active lead list runs
- Calls the Lead List Agent with the run criteria
- Uses MCP-backed tools to discover companies/contacts
- Persists results into the company_candidates and contact_candidates tables

The concrete DB I/O is left to the existing Supabase client, but the
interface is shaped by ``pm_pipeline_tables.md``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple, Optional

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents import Runner
from rv_agentic.agents.lead_list_agent import (
    create_lead_list_agent,
    LeadListOutput,
)
from rv_agentic.config.settings import get_settings
from rv_agentic.services import supabase_client, retry, hubspot_client
from rv_agentic.services.heartbeat import WorkerHeartbeat
from rv_agentic.services.geography_decomposer import decompose_geography, format_region_for_prompt
from rv_agentic.workers.utils import load_env_files


class ActiveRunsFetcher(Protocol):
    def __call__(self) -> List[Dict[str, Any]]:
        ...


class RunMarker(Protocol):
    def __call__(self, run: Dict[str, Any], status: str, error: str | None = None) -> None:
        ...


logger = logging.getLogger(__name__)


load_env_files()

# Ensure OPENAI_API_KEY is set for the Agents SDK when running in the worker
_settings = get_settings()
if _settings.openai_api_key and "OPENAI_API_KEY" not in os.environ:
    os.environ["OPENAI_API_KEY"] = _settings.openai_api_key


def _maybe_advance_run_stage(run_id: str) -> None:
    if not run_id:
        return
    resume = supabase_client.get_run_resume_plan(run_id)
    if not resume:
        return
    stage = (resume.get("stage") or "").strip()
    companies_gap = int(resume.get("companies_gap") or 0)
    if stage == "company_discovery" and companies_gap <= 0:
        supabase_client.set_run_stage(run_id=run_id, stage="company_research")


def _deduplicate_companies_by_domain(companies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate companies by domain, keeping the one with highest quality score.

    Args:
        companies: List of company dicts with 'domain' and optional 'quality_score'

    Returns:
        Deduplicated list of companies
    """
    seen_domains = {}
    for company in companies:
        domain = company.get("domain", "").lower().strip()
        if not domain:
            continue

        # Get quality score (default to 0 if not present)
        quality = company.get("quality_score", 0)

        # Keep company with higher quality score
        if domain not in seen_domains or quality > seen_domains[domain].get("quality_score", 0):
            seen_domains[domain] = company

    return list(seen_domains.values())


def _run_region_agent(
    region: Dict[str, str],
    region_index: int,
    total_regions: int,
    run_id: str,
    criteria: Dict[str, Any],
    target_qty: int,
    discovery_target: int,
    oversample_factor: float
) -> Tuple[str, Optional[LeadListOutput], Optional[str]]:
    """
    Run lead list agent for a single region.

    This function is designed to be called in parallel by ThreadPoolExecutor.

    Args:
        region: Region specification with name, description, search_focus
        region_index: 0-indexed region number
        total_regions: Total number of regions
        run_id: Pipeline run ID
        criteria: Run criteria
        target_qty: Final target quantity
        discovery_target: Discovery target with oversample
        oversample_factor: Oversample multiplier

    Returns:
        Tuple of (region_name, LeadListOutput or None, error message or None)
    """
    region_name = region["name"]
    region_num = region_index + 1

    try:
        logger.info(
            "Starting parallel region %d/%d: %s",
            region_num, total_regions, region_name
        )

        # Build region-specific prompt
        region_prompt = format_region_for_prompt(region, criteria)

        agent = create_lead_list_agent()
        prompt = (
            "You are running in **worker mode** for an async lead list run.\n"
            "This is a **parallel multi-region discovery strategy**: you are assigned a specific geographic region.\n\n"
            f"{region_prompt}\n\n"
            "In this environment, plain text explanations are not enough – your primary job is to\n"
            "**populate your structured output (LeadListOutput)** with ALL high-quality companies\n"
            "in your assigned region that meet the criteria.\n\n"
            f"Run id (for your reasoning only): {run_id}\n"
            f"Run criteria JSON (authoritative requirements): {json.dumps(criteria, ensure_ascii=False)}\n\n"
            f"**Progress context**: This is region {region_num} of {total_regions} running IN PARALLEL.\n"
            f"Target for this run: {target_qty} final companies.\n"
            f"Discovery target: {discovery_target} companies ({oversample_factor:.1f}x oversample).\n\n"
            "**Your goal for this region**: Find 8-12 high-quality companies in your assigned region.\n"
            "Other regions are being covered SIMULTANEOUSLY by other agents, so focus ONLY on your assigned area.\n\n"
            "Your success criteria:\n"
            "- Search exhaustively within YOUR ASSIGNED REGION using MCP search tools\n"
            "- Add ALL matching companies to `companies` in LeadListOutput\n"
            "- **SORT** by quality/confidence - strongest matches FIRST\n"
            "- Skip companies whose domain is in the blocked domains list\n"
            "- For each company, try to find 1-3 decision makers in `contacts`\n"
            "- Set `search_exhausted=True` if you've checked all sources in your region\n\n"
            "Tool sequence:\n"
            "1) Call get_blocked_domains_tool once\n"
            "2) Use MCP search tools for your region (multiple searches with different strategies)\n"
            "3) Use company profile tools to enrich\n"
            "4) Use contact tools to find decision makers\n"
            "5) Return structured LeadListOutput with your findings\n\n"
            "Focus on your region only. Return your best 8-12 companies from this area.\n"
        )

        # Call agent with retry logic
        result = retry.retry_agent_call(
            Runner.run_sync,
            agent,
            prompt,
            max_attempts=3,
            base_delay=1.0,
            max_turns=30
        )

        # Extract structured output
        try:
            typed: LeadListOutput = result.final_output_as(LeadListOutput)  # type: ignore[assignment]
        except Exception as e:
            logger.warning(f"Region {region_num} ({region_name}) failed to parse structured output: {e}")
            typed = LeadListOutput()

        # Add discovery_source tracking with region name
        region_companies = [c.model_dump() for c in (typed.companies or [])]
        for company in region_companies:
            company["discovery_source"] = f"agent:multi_region:{region_name.replace(' ', '_')}"

        region_contacts = [c.model_dump() for c in (typed.contacts or [])]

        logger.info(
            "Completed parallel region %d/%d (%s): found %d companies, %d contacts",
            region_num, total_regions, region_name, len(region_companies), len(region_contacts)
        )

        return (region_name, typed, None)

    except Exception as e:
        error_msg = f"Region {region_num} ({region_name}) failed: {e}"
        logger.error(error_msg, exc_info=True)
        return (region_name, None, error_msg)


def _discover_companies_multi_region(
    run_id: str,
    criteria: Dict[str, Any],
    target_qty: int,
    discovery_target: int,
    companies_already_found: int,
    oversample_factor: float
) -> LeadListOutput:
    """
    Discover companies using parallel multi-region strategy.

    Decomposes geography into 4 regions and calls agents IN PARALLEL for each region.
    This solves the "agent stops early" problem while minimizing latency through
    concurrent execution. Expected time: ~20 minutes (vs 80 minutes sequential).

    Args:
        run_id: Pipeline run ID
        criteria: Run criteria with geography, units, PMS, etc.
        target_qty: Final target quantity of companies
        discovery_target: Number of companies to discover (with oversample)
        companies_already_found: Companies already discovered (from seeding, etc.)
        oversample_factor: Oversample multiplier

    Returns:
        LeadListOutput with aggregated companies and contacts from all regions
    """

    # Decompose geography into regions
    num_regions = 4
    regions = decompose_geography(criteria, num_regions=num_regions)

    logger.info(
        "Parallel multi-region discovery: %d regions, discovery_target=%d, already_found=%d",
        len(regions), discovery_target, companies_already_found
    )

    all_companies = []
    all_contacts = []
    successful_regions = []
    failed_regions = []

    # Launch all 4 regions in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as executor:
        # Submit all region tasks
        futures = {}
        for i, region in enumerate(regions):
            future = executor.submit(
                _run_region_agent,
                region,
                i,
                len(regions),
                run_id,
                criteria,
                target_qty,
                discovery_target,
                oversample_factor
            )
            futures[future] = (i, region)

        # Collect results as they complete
        for future in as_completed(futures):
            region_index, region = futures[future]
            region_name, result, error = future.result()

            if error:
                failed_regions.append(region_name)
                logger.warning(f"Region {region_name} failed: {error}")
                continue

            if result:
                # Extract companies and contacts
                region_companies = [c.model_dump() for c in (result.companies or [])]
                region_contacts = [c.model_dump() for c in (result.contacts or [])]

                all_companies.extend(region_companies)
                all_contacts.extend(region_contacts)
                successful_regions.append(region_name)

                logger.info(
                    "Collected results from %s: %d companies, %d contacts",
                    region_name, len(region_companies), len(region_contacts)
                )

    # Deduplicate by domain
    all_companies_deduped = _deduplicate_companies_by_domain(all_companies)

    logger.info(
        "Parallel multi-region discovery complete: %d successful regions, %d failed regions",
        len(successful_regions), len(failed_regions)
    )
    logger.info(
        "Results: %d total companies, %d after dedup, %d contacts",
        len(all_companies), len(all_companies_deduped), len(all_contacts)
    )

    # Convert back to Pydantic models for LeadListOutput
    from rv_agentic.agents.lead_list_agent import LeadListCompany, LeadListContact

    company_models = [LeadListCompany(**c) for c in all_companies_deduped]
    contact_models = [LeadListContact(**c) for c in all_contacts]

    quality_notes = (
        f"Parallel multi-region discovery: {len(successful_regions)}/{len(regions)} regions succeeded"
    )
    if failed_regions:
        quality_notes += f" (failed: {', '.join(failed_regions)})"

    return LeadListOutput(
        companies=company_models,
        contacts=contact_models,
        total_found=len(all_companies_deduped),
        search_exhausted=len(successful_regions) >= 3,  # Consider exhausted if at least 3 regions succeeded
        quality_notes=quality_notes
    )


def process_run(run: Dict[str, Any], heartbeat: WorkerHeartbeat | None = None) -> None:
    """Process a single lead list run.

    ``run`` is expected to match the ``pm_pipeline.runs`` schema: it
    should contain criteria, target_quantity, stage, etc. This function
    delegates reasoning and tool calls to the Lead List Agent.
    """

    raw_criteria = run.get("criteria")
    criteria: Dict[str, Any]
    if isinstance(raw_criteria, dict):
        criteria = raw_criteria
    elif isinstance(raw_criteria, str):
        # Support legacy runs where criteria was stored as a free-text string.
        try:
            parsed = json.loads(raw_criteria)
            criteria = parsed if isinstance(parsed, dict) else {"natural_request": raw_criteria}
        except Exception:
            criteria = {"natural_request": raw_criteria}
    else:
        criteria = {}
    run_id = str(run.get("id") or "")

    # Update heartbeat with current task including batch progress
    if heartbeat:
        quantity = int(criteria.get("quantity") or run.get("target_quantity") or 0)
        pms = criteria.get("pms") or "any"
        state = criteria.get("state") or "any"

        # Check current progress for batch status
        existing_companies = supabase_client.get_pm_company_gap(run_id)
        companies_ready = int(existing_companies.get("companies_ready") or 0) if existing_companies else 0

        task_description = f"Lead discovery: {companies_ready}/{quantity} companies (PMS={pms}, State={state})"

        heartbeat.update_task(
            run_id=run_id,
            task=task_description,
            status="processing"
        )

    # Seed candidates from PMS subdomains when PMS is a hard requirement and we
    # have a known PMS vendor (e.g. Buildium/AppFolio). This gives us a fast,
    # high-quality starting pool that already satisfies PMS requirements.
    pms_required = (criteria.get("pms") or "").strip()
    city = (criteria.get("city") or "").strip()
    state = (criteria.get("state") or "").strip().upper()
    target_qty = 0
    try:
        target_qty = int(criteria.get("quantity") or run.get("target_quantity") or 0)
    except Exception:
        target_qty = 0

    if pms_required:
        oversample = float(os.getenv("PM_PMS_SEED_OVERSAMPLE", "2.0"))
        seed_limit = int(max((target_qty or 10) * oversample, 10))

        # 1) Seed from the imported PMS subdomains table, which gives us
        #    a fast, PMS-confirmed pool of candidates.
        try:
            seeds = supabase_client.get_pms_subdomain_seeds(
                pms=pms_required,
                city=city or None,
                state=state or None,
                status="alive",
                limit=seed_limit,
            )
        except Exception:
            seeds = []

        # Reuse the same blocked-domain list we apply after the agent run so
        # that seeds never introduce suppressed companies into the pipeline.
        try:
            blocked = set(
                (d or "").strip().lower() for d in supabase_client.get_blocked_domains()
            )
        except Exception:
            blocked = set()

        for row in seeds:
            subdomain = (row.get("pms_subdomain") or "").strip()
            real_domain = (row.get("real_domain") or "").strip()
            company_name = (row.get("company_name") or "").strip()
            if not subdomain and not real_domain:
                continue
            domain = (real_domain or subdomain).lower()
            # Avoid inserting obviously empty/invalid domains.
            if not domain or "." not in domain:
                continue
            if domain in blocked:
                continue
            name = company_name or domain
            website = f"https://{domain}"
            try:
                # PMS subdomain seeds are already known to use the required PMS,
                # so they can be treated as validated discovery-stage companies.
                supabase_client.insert_company_candidate(
                    run_id=run_id,
                    name=name,
                    website=website,
                    domain=domain,
                    state=state or "NA",
                    discovery_source=f"pms_subdomains:{pms_required}",
                    pms_detected=pms_required,
                    status="validated",
                )
            except Exception:
                # Ignore duplicate/conflict errors; idempotent per run/domain.
                logger.debug(
                    "Seed insert_company_candidate duplicate/failed for domain=%s run_id=%s",
                    domain,
                    run_id,
                )

        # 2) Seed from the NEO research database when available. This
        #    gives us PMS-qualified companies even when the subdomain
        #    import is incomplete for a region.
        try:
            neo_rows = supabase_client.find_company(
                pms=pms_required,
                city=city or None,
                limit=seed_limit,
            )
        except Exception:
            neo_rows = []

        for row in neo_rows or []:
            domain = (row.get("domain") or "").strip().lower()
            if not domain or "." not in domain:
                continue
            if domain in blocked:
                continue
            name = (row.get("company_name") or row.get("domain") or "").strip() or domain
            website = (row.get("company_url") or "").strip() or f"https://{domain}"
            neo_state = (row.get("hq_state") or "").strip().upper()
            neo_city = (row.get("target_city") or row.get("hq_city") or "").strip()
            try:
                supabase_client.insert_company_candidate(
                    run_id=run_id,
                    name=name,
                    website=website,
                    domain=domain,
                    state=(neo_state or state or "NA"),
                    discovery_source=f"neo_pms:{pms_required}",
                    pms_detected=pms_required,
                    status="validated",
                )
            except Exception:
                logger.debug(
                    "NEO seed insert_company_candidate duplicate/failed for domain=%s run_id=%s",
                    domain,
                    run_id,
                )

    # Oversample to account for attrition in enrichment stages
    # (company ICP filtering, contact discovery failures, etc.)
    oversample_factor = float(os.getenv("LEAD_LIST_OVERSAMPLE_FACTOR", "2.0"))
    discovery_target = int(target_qty * oversample_factor) if target_qty > 0 else 0

    # Check how many companies we already have
    existing_companies = supabase_client.get_pm_company_gap(run_id)
    companies_ready = int(existing_companies.get("companies_ready") or 0) if existing_companies else 0
    companies_remaining = max(0, discovery_target - companies_ready)

    logger.info(
        "Progress check: target=%d, discovery_target=%d (oversample=%.1fx), existing=%d, remaining=%d",
        target_qty, discovery_target, oversample_factor, companies_ready, companies_remaining
    )

    # If we already have enough companies, skip agent call
    if companies_remaining <= 0 and target_qty > 0:
        logger.info(
            "Run %s already has %d companies (target: %d), skipping lead list agent",
            run_id,
            companies_ready,
            target_qty
        )
        return None

    # Call multi-region discovery function
    logger.info("Starting multi-region discovery for run id=%s", run_id)
    typed = _discover_companies_multi_region(
        run_id=run_id,
        criteria=criteria,
        target_qty=target_qty,
        discovery_target=discovery_target,
        companies_already_found=companies_ready,
        oversample_factor=oversample_factor
    )

    # Post-run primary path: use structured output for deterministic inserts.
    companies: List[Dict[str, Any]] = []
    contacts: List[Dict[str, Any]] = []

    blocked = set(d.lower().strip() for d in supabase_client.get_blocked_domains())

    target_quantity = 0
    try:
        target_quantity = int(criteria.get("quantity") or criteria.get("target_quantity") or 0)
    except Exception:
        target_quantity = 0

    inserted = 0
    state = (criteria.get("state") or "").strip().upper() or ""

    logger.info(
        "Processing agent output: agent returned %d companies (total_found=%d, search_exhausted=%s)",
        len(typed.companies),
        typed.total_found,
        typed.search_exhausted
    )

    if typed.quality_notes:
        logger.info("Agent quality notes: %s", typed.quality_notes)

    # Agent returns companies sorted by quality (best first).
    # Insert up to discovery_target (oversampled) to account for enrichment attrition.
    companies_to_insert = min(len(typed.companies), companies_remaining) if companies_remaining > 0 else len(typed.companies)

    logger.info(
        "Inserting up to %d companies (final_target=%d, discovery_target=%d, companies_ready=%d, remaining=%d)",
        companies_to_insert, target_qty, discovery_target, companies_ready, companies_remaining
    )

    for idx, cc in enumerate(typed.companies):
        # Stop when we've reached the target quantity
        if target_qty > 0 and inserted >= companies_remaining:
            logger.info(
                "Target quantity reached: inserted=%d companies, stopping (agent had %d more)",
                inserted,
                len(typed.companies) - idx
            )
            break

        domain = (cc.domain or "").lower().strip()
        if not domain or domain in blocked:
            logger.debug("Skipping blocked/empty domain: %s", domain)
            continue

        # Check HubSpot suppression (existing customers and recently contacted)
        try:
            suppression_check = hubspot_client.check_company_suppression(domain, days=90)
            if suppression_check.get('should_suppress'):
                reason = suppression_check.get('reason')
                details = suppression_check.get('details', {})
                logger.info(
                    "Suppressing company %s (reason: %s) - %s",
                    domain,
                    reason,
                    details.get('company_name', 'Unknown')
                )

                # Insert into suppression table for tracking
                supabase_client.insert_hubspot_suppression(
                    domain=domain,
                    company_name=details.get('company_name'),
                    hubspot_company_id=details.get('company_id'),
                    suppression_reason=reason,
                    lifecycle_stage=details.get('lifecycle_stage'),
                    last_contact_date=details.get('last_contact_date'),
                    last_contact_type=details.get('last_contact_type'),
                    engagement_count=details.get('engagement_count'),
                )
                continue
        except Exception as e:
            # Best-effort suppression check - don't fail if HubSpot is unavailable
            logger.warning("HubSpot suppression check failed for domain=%s: %s", domain, e)

        name = cc.name or domain
        website = f"https://{domain}"
        try:
            row = supabase_client.insert_company_candidate(
                run_id=run_id,
                name=name,
                website=website,
                domain=domain,
                state=(cc.state or state or "NA"),
                discovery_source="agent_structured",
                status="validated",
            )
            if row:
                inserted += 1
                companies.append(row)
                logger.info(
                    "Inserted company %d/%d: id=%s domain=%s run_id=%s",
                    inserted,
                    companies_to_insert,
                    row.get("id"),
                    domain,
                    run_id,
                )
        except Exception:
            logger.exception(
                "Structured insert_company_candidate failed for domain=%s run_id=%s",
                domain,
                run_id,
            )

    if not companies:
        # Fallback path: parse from final text (CANDIDATE_COMPANIES).
        logger.warning(
            "No company_candidates inserted for run %s after primary agent run; "
            "attempting fallback extraction from final output.",
            run_id,
        )
        raw_output = getattr(result, "final_output", "") or ""
        if isinstance(raw_output, LeadListOutput):
            # When output_type is active, final_output may already be the model.
            # Convert to a plain string representation for fallback parsing.
            final_text = raw_output.model_dump_json()
        else:
            final_text = str(raw_output)
        try:
            _fallback_insert_companies_from_output(
                run_id=run_id,
                criteria=criteria,
                final_output=final_text,
                supabase_client=supabase_client,
            )
        except Exception:  # pragma: no cover
            logger.exception(
                "Fallback insertion failed for run id=%s; will surface as error", run_id
            )

        companies = supabase_client._get_pm(  # type: ignore[attr-defined]
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{run_id}"},
        )

    if not companies:
        # No companies were inserted even after seeding + agent run. In a strict
        # PMS/location scenario this likely means there are no eligible companies
        # at all, not a pipeline failure. Mark the run as completed-without-matches
        # so it can be inspected but does not block the async system.
        msg = (
            f"Lead List Agent completed without inserting any companies for run {run_id}. "
            "This most likely indicates that no companies match the given PMS/location "
            "constraints after reasonable search."
        )
        logger.warning(msg)
        try:
            supabase_client.update_pm_run_status(run_id=run_id, status="completed", error=msg)
        except Exception:
            logger.exception("Failed to update run status after empty company set for run %s", run_id)
        return result

    # Insert contacts based on structured output, mapping by company domain.
    domain_to_company_id: Dict[str, str] = {}
    for row in companies:
        dom = (row.get("domain") or "").lower().strip()
        cid = row.get("id")
        if dom and cid:
            domain_to_company_id[dom] = str(cid)

    # Build per-company contact limits (1–3 per company).
    per_company_counts: Dict[str, int] = {}

    for ct in typed.contacts:
        domain = (ct.company_domain or "").lower().strip()
        if not domain:
            continue
        company_id = domain_to_company_id.get(domain)
        if not company_id:
            continue

        # Enforce max 3 contacts per company.
        count = per_company_counts.get(company_id, 0)
        if count >= 3:
            continue

        try:
            row = supabase_client.insert_contact_candidate(
                run_id=run_id,
                company_id=company_id,
                full_name=ct.full_name,
                title=ct.title or None,
                email=ct.email or None,
                linkedin_url=ct.linkedin_url or None,
                evidence={"quality_notes": ct.quality_notes} if ct.quality_notes else None,
                status="validated",
            )
            if row:
                contacts.append(row)
                per_company_counts[company_id] = count + 1
                logger.info(
                    "Structured insert_contact_candidate id=%s company_id=%s email=%s",
                    row.get("id"),
                    company_id,
                    row.get("email"),
                )
        except Exception:
            logger.exception(
                "Structured insert_contact_candidate failed for company_id=%s domain=%s",
                company_id,
                domain,
            )

    logger.info(
        "Completed pm_pipeline run id=%s with %d company candidate(s) and %d contact(s)",
        run.get("id"),
        len(companies),
        len(contacts),
    )
    _maybe_advance_run_stage(str(run.get("id") or ""))
    return result


def _fallback_insert_companies_from_output(
    *,
    run_id: str,
    criteria: Dict[str, Any],
    final_output: str,
    supabase_client: Any,
) -> None:
    """Best-effort fallback when the agent didn't call insert_company_candidate_tool.

    This parses the agent's final markdown/text for likely company names + domains
    and inserts them directly via supabase_client.insert_company_candidate, while
    respecting blocked domains and the run's target quantity.
    """

    if not final_output or not run_id:
        return

    logger.info(
        "Running fallback company extraction for run id=%s on output length=%d",
        run_id,
        len(final_output),
    )

    # Collect blocked domains so we don't re-insert suppressed companies.
    try:
        blocked = set(d.lower().strip() for d in supabase_client.get_blocked_domains())
    except Exception:
        blocked = set()

    candidates: Dict[str, Dict[str, str]] = {}

    # First, try to parse the explicit CANDIDATE_COMPANIES section if present.
    lines = final_output.splitlines()
    start_idx = None
    for idx, line in enumerate(lines):
        if line.strip().upper().startswith("CANDIDATE_COMPANIES:"):
            start_idx = idx + 1
            break

    if start_idx is not None:
        for raw in lines[start_idx:]:
            if not raw.strip():
                # Stop at first blank line after the section.
                break
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) < 2:
                continue
            name = parts[0] or ""
            domain = (parts[1] or "").lower()
            if not domain:
                continue
            if domain in blocked:
                continue
            if domain in {
                "rentvine.com",
                "appfolio.com",
                "buildium.com",
                "yardi.com",
                "doorloop.com",
            }:
                continue
            candidates.setdefault(domain, {"domain": domain, "name": name or domain})

    # If the explicit section was missing or empty, fall back to heuristic parsing.
    if not candidates:
        domain_pattern = re.compile(r"\b([a-z0-9-]+\.[a-z]{2,})\b", re.IGNORECASE)
        for line in lines:
            if not line.strip():
                continue
            domains = domain_pattern.findall(line)
            if not domains:
                continue
            for dom in domains:
                domain = dom.lower()
                if domain in blocked:
                    continue
                if domain in {
                    "rentvine.com",
                    "appfolio.com",
                    "buildium.com",
                    "yardi.com",
                    "doorloop.com",
                }:
                    continue
                name = line.strip()
                if len(name) > 200:
                    name = name[:200]
                candidates.setdefault(domain, {"domain": domain, "name": name})

    if not candidates:
        logger.info(
            "Fallback extraction found no candidate domains for run id=%s", run_id
        )
        return

    target_quantity = 0
    try:
        target_quantity = int(criteria.get("quantity") or criteria.get("target_quantity") or 0)
    except Exception:
        target_quantity = 0

    # Derive a state from criteria if present (fallback to empty string).
    state = (criteria.get("state") or "").strip().upper() or ""

    inserted = 0
    for domain, info in candidates.items():
        if target_quantity and inserted >= target_quantity:
            break
        name = info.get("name") or domain
        website = f"https://{domain}"
        try:
            row = supabase_client.insert_company_candidate(
                run_id=run_id,
                name=name,
                website=website,
                domain=domain,
                state=state or "NA",
                discovery_source="agent_fallback",
            )
            if row:
                inserted += 1
                logger.info(
                    "Fallback inserted company_candidate id=%s domain=%s run_id=%s",
                    row.get("id"),
                    domain,
                    run_id,
                )
        except Exception:
            logger.exception(
                "Fallback insert_company_candidate failed for domain=%s run_id=%s",
                domain,
                run_id,
            )

    logger.info(
        "Fallback insertion completed for run id=%s; inserted=%d candidates",
        run_id,
        inserted,
    )


def run_forever(fetch_active_runs: ActiveRunsFetcher, mark_run_complete: RunMarker, heartbeat: WorkerHeartbeat | None = None) -> None:
    """Main loop for the worker.

    ``fetch_active_runs`` should be a callable returning a list of
    active run dicts. ``mark_run_complete`` should mark a run as
    completed or failed. These are injected so this worker can be
    used with Supabase, direct Postgres, or mocks for testing.
    """

    max_loops_env = os.getenv("WORKER_MAX_LOOPS")
    run_filter_id = os.getenv("RUN_FILTER_ID", "").strip()
    try:
        max_loops = int(max_loops_env) if max_loops_env is not None else None
    except ValueError:
        max_loops = None
    # In targeted testing mode (RUN_FILTER_ID set) but no explicit limit
    # provided, use a small default so the worker does not run forever.
    if max_loops is None and run_filter_id:
        max_loops = 3

    loops = 0
    while True:
        runs: List[Dict[str, Any]] = fetch_active_runs()
        if not runs:
            # Avoid busy looping when there is no work.
            import time as _time

            logger.info("No active runs found; sleeping")
            if heartbeat:
                heartbeat.mark_idle()
            _time.sleep(2.0)
            continue
        logger.info("Found %d active run(s) to process", len(runs))
        for run in runs:
            try:
                process_run(run, heartbeat)

                # Check if we've met the discovery target for this run
                run_id = run.get("id")
                target_qty = int(run.get("target_quantity") or 0)
                oversample_factor = float(os.getenv("LEAD_LIST_OVERSAMPLE_FACTOR", "2.0"))
                discovery_target = int(target_qty * oversample_factor) if target_qty > 0 else 0

                if discovery_target > 0:
                    # Check how many companies we have now
                    gap_info = supabase_client.get_pm_company_gap(run_id)
                    companies_ready = int(gap_info.get("companies_ready") or 0) if gap_info else 0
                    discovery_remaining = max(0, discovery_target - companies_ready)

                    if discovery_remaining > 0:
                        # Agent couldn't find enough companies to meet discovery target.
                        # With the oversample strategy, if we can't meet the discovery target,
                        # we may not have enough after enrichment attrition.
                        logger.warning(
                            "Run %s discovery incomplete: %d/%d discovered (target: %d final), %d gap",
                            run_id, companies_ready, discovery_target, target_qty, discovery_remaining
                        )
                        # Mark as completed with partial results
                        mark_run_complete(run, status="completed",
                                        error=f"Discovery found {companies_ready}/{discovery_target} companies (target: {target_qty} final). Agent exhausted search.")
                        continue
                    else:
                        logger.info(
                            "Run %s discovery target met: %d/%d discovered (target: %d final after enrichment)",
                            run_id, companies_ready, discovery_target, target_qty
                        )

                # Target met or no target specified - mark as complete
                # On success, mark the discovery stage complete. Downstream
                # workers use ``stage`` to drive additional work; the `status`
                # flag is used only for queueing active vs. finished runs.
                mark_run_complete(run, status="completed")
            except Exception as exc:  # pragma: no cover
                logger.exception("Error processing run id=%s", run.get("id"))
                mark_run_complete(run, status="error", error=str(exc))

        loops += 1
        if max_loops is not None and loops >= max_loops:
            logger.info(
                "WORKER_MAX_LOOPS=%s reached in lead_list_runner; exiting after %s loop(s)",
                max_loops,
                loops,
            )
            break


def _supabase_fetch_active_runs() -> List[Dict[str, Any]]:
    """Default fetcher that pulls active runs from pm_pipeline.runs via Supabase.

    When RUN_FILTER_ID is set, only that run (if active) is returned. This
    is useful for targeted testing and limits blast radius while iterating.
    """

    run_filter_id = os.getenv("RUN_FILTER_ID")
    if run_filter_id:
        # When a specific run id is provided, treat it as an explicit
        # override for debugging / targeted processing. We still only
        # allow runs in the company_discovery stage, but we will
        # revive runs that previously hit an error.
        run = supabase_client.get_pm_run(run_filter_id)
        if not run:
            return []
        if str(run.get("stage")) != "company_discovery":
            return []
        # If the run is not active (e.g. status='error'), flip it back
        # to active so it can be reprocessed.
        if str(run.get("status")) != "active":
            try:
                supabase_client.update_pm_run_status(
                    run_id=run_filter_id,
                    status="active",
                )
                run["status"] = "active"
            except Exception:
                # If we cannot update status, still return the run so that
                # local testing is possible.
                pass
        return [run]

    # By default, only process runs that are still in the company_discovery
    # stage so that downstream workers can safely own later stages.
    all_active = supabase_client.fetch_active_pm_runs()
    return [r for r in all_active if str(r.get("stage")) == "company_discovery"]


def _supabase_mark_run_complete(run: Dict[str, Any], status: str, error: str | None = None) -> None:
    """Default marker that updates pm_pipeline.runs via Supabase."""

    run_id = str(run.get("id"))
    if not run_id:
        return
    supabase_client.update_pm_run_status(run_id=run_id, status=status, error=error)


def main() -> None:
    """Entry point for running the lead list worker with Supabase-backed pm_pipeline tables."""
    import uuid

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    worker_id = os.getenv("LEAD_LIST_WORKER_ID") or f"lead-list-{uuid.uuid4()}"
    heartbeat_interval = int(os.getenv("WORKER_HEARTBEAT_INTERVAL", "30"))

    logger.info("Lead list worker starting up worker_id=%s", worker_id)

    # Start heartbeat monitoring
    heartbeat = WorkerHeartbeat(
        worker_id=worker_id,
        worker_type="lead_list",
        interval_seconds=heartbeat_interval
    )
    heartbeat.start()

    try:
        run_forever(_supabase_fetch_active_runs, _supabase_mark_run_complete, heartbeat)
    finally:
        # Ensure heartbeat is stopped on exit
        heartbeat.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
