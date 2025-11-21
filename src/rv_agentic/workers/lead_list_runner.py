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

import asyncio
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
from rv_agentic.services import supabase_client, retry, hubspot_client, notifications
from rv_agentic.services.heartbeat import WorkerHeartbeat
from rv_agentic.services.geography_decomposer import decompose_geography, format_region_for_prompt
from rv_agentic.workers.utils import load_env_files
from rv_agentic.tools import mcp_client


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
    # Advance from discovery to research when companies are ready
    if stage == "company_discovery" and companies_gap <= 0:
        supabase_client.set_run_stage(run_id=run_id, stage="company_research")
        stage = "company_research"
        supabase_client.insert_audit_event(
            run_id=run_id,
            entity_type="run",
            entity_id=run_id,
            event="stage_advanced",
            meta={"to": "company_research"},
        )
    # Cascade to contact_discovery when no research queue remains
    if stage == "company_research" and companies_gap <= 0:
        try:
            has_research = supabase_client.has_company_research_queue(run_id)
        except Exception:
            has_research = True
        if not has_research:
            supabase_client.set_run_stage(run_id=run_id, stage="contact_discovery")
            supabase_client.insert_audit_event(
                run_id=run_id,
                entity_type="run",
                entity_id=run_id,
                event="stage_advanced",
                meta={"to": "contact_discovery"},
            )


def _log_run_event(run_id: str, stage: str, action: str, payload: dict[str, Any] | None = None) -> None:
    """Emit a structured log line for UI/ops consumption (no DB schema changes)."""
    data = payload or {}
    logger.info("EVENT|run_id=%s|stage=%s|action=%s|data=%s", run_id, stage, action, json.dumps(data, ensure_ascii=False))


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
    oversample_factor: float,
    region_query_budget: int,
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

        # Simplify prompt when running single region (the default)
        if total_regions == 1:
            prompt = (
                "You are running in **worker mode** for an async lead list run.\n\n"
                f"Run id (for your reasoning only): {run_id}\n"
                f"Run criteria JSON (authoritative requirements): {json.dumps(criteria, ensure_ascii=False)}\n\n"
                f"**Your goal**: Find {discovery_target} companies ({oversample_factor:.1f}x oversample of {target_qty} target).\n\n"
                "In this environment, plain text explanations are not enough – your primary job is to\n"
                "**populate your structured output (LeadListOutput)** with ALL high-quality companies\n"
                "that meet the criteria.\n\n"
            )
        else:
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
            )

        prompt += (
            "Your success criteria:\n"
            "- **PRIMARY STRATEGY**: Use `fetch_page` to extract companies from list pages\n"
            "  - Search for 'top property management [city]' to find list pages\n"
            "  - When you see a list URL (ipropertymanagement.com, expertise.com, etc.), IMMEDIATELY use `fetch_page`\n"
            "  - Extract ALL companies from the page into your `companies` array\n"
            "  - This is how you get 10-50 companies from one source\n"
            "- Add ALL matching companies to `companies` in LeadListOutput\n"
            "- **SORT** by quality/confidence - strongest matches FIRST\n"
            "- Skip companies whose domain is in the blocked domains list\n"
            "- For each company, try to find 1-3 decision makers in `contacts`\n"
            "- Set `search_exhausted=True` if you've checked all sources\n\n"
            "Tool sequence:\n"
            "1) Call get_blocked_domains_tool once\n"
            "2) Use `search_web` to find company list pages (3-5 searches)\n"
            "3) **CRITICAL**: Use `fetch_page` on EVERY list page URL you find\n"
            "4) Use `LangSearch_API` or `Run_PMS_Analyzer_Script` to verify PMS for each company\n"
            "5) Use contact tools to find 1-3 decision makers per company\n"
            "6) Return structured LeadListOutput with your findings\n\n"
        )

        # Call agent with increased turn limit
        # Default max_turns is 10, but we need ~20+ for the search rounds
        # Timeout is enforced at the ThreadPoolExecutor level (see as_completed with timeout)
        try:
            result = Runner.run_sync(
                agent,
                prompt,
                max_turns=100,  # Increased from default 10 to allow full search rounds
            )
        except TypeError as e:
            # Test doubles may not accept newer kwargs; fall back to default signature.
            if "max_turns" in str(e):
                result = Runner.run_sync(agent, prompt)
            else:
                raise

        # Extract structured output
        try:
            typed: LeadListOutput = result.final_output_as(LeadListOutput)  # type: ignore[assignment]
        except Exception as e:
            logger.warning(f"Region {region_num} ({region_name}) failed to parse structured output: {e}")
            typed = LeadListOutput()
        finally:
            # CRITICAL: Reset MCP counters after each agent run to prevent deluge
            from rv_agentic.tools import mcp_client
            mcp_client.reset_mcp_counters()

            # Force garbage collection to clean up any orphaned async tasks
            import gc
            gc.collect()

            # Extended pause for MCP session cleanup (1.0s instead of 0.3s)
            import time
            time.sleep(1.0)

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

    except BaseException as e:
        # Catch BaseException so asyncio.CancelledError does not crash the worker thread.
        error_msg = f"Region {region_num} ({region_name}) failed: {e}"
        logger.error(error_msg, exc_info=True)
        return (region_name, None, error_msg)


def _discover_companies_multi_region(
    run_id: str,
    criteria: Dict[str, Any],
    target_qty: int,
    discovery_target: int,
    companies_already_found: int,
    oversample_factor: float,
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
    # Default to 1 region (single agent call) for simpler, more effective search
    # Can override with LEAD_LIST_REGION_COUNT=4 for large geographies
    num_regions = int(os.getenv("LEAD_LIST_REGION_COUNT", "1"))
    region_workers = int(os.getenv("LEAD_LIST_REGION_WORKERS", "1"))
    region_workers = max(1, min(region_workers, num_regions))
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
    with ThreadPoolExecutor(max_workers=region_workers) as executor:
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
                oversample_factor,
                int(os.getenv("LEAD_LIST_REGION_QUERY_BUDGET", "10")),
            )
            futures[future] = (i, region)

        # Collect results as they complete with 15-minute timeout per region
        stop_early = False
        try:
            iterator = as_completed(futures, timeout=900)  # 900s = 15 minutes
            for future in iterator:
                region_index, region = futures[future]
                try:
                    # .result() should be immediate since the future is done
                    region_name, result, error = future.result(timeout=1)
                except TimeoutError:
                    region_name = futures[future][1]["name"]
                    error = f"Region {region_name} exceeded 15-minute timeout"
                    result = None
                    logger.error(error)
                except BaseException as e:
                    region_name = futures[future][1]["name"]
                    error = f"Region {region_name} raised {type(e).__name__}: {e}"
                    result = None
                    logger.error(error, exc_info=True)

                if error:
                    failed_regions.append((region_index, region, error))
                    logger.warning(f"Region {region_name} failed on first attempt: {error}")
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

                    # If we've already met or exceeded the discovery target, cancel outstanding regions
                    if discovery_target > 0:
                        current_total = len(_deduplicate_companies_by_domain(all_companies))
                        if current_total >= discovery_target:
                            logger.info(
                                "Discovery target satisfied mid-flight (%d/%d); cancelling remaining region tasks",
                                current_total,
                                discovery_target,
                            )
                            stop_early = True
                            break
        except TimeoutError:
            # as_completed timed out before all futures finished; cancel remaining and record failures.
            logger.error("Parallel region discovery exceeded overall timeout; cancelling outstanding regions")
            for future, (region_index, region) in futures.items():
                if future.done():
                    continue
                future.cancel()
                failed_regions.append((region_index, region, "Cancelled due to overall timeout"))

        if stop_early:
            for future in futures:
                if future.done():
                    continue
                future.cancel()

    # RETRY FAILED REGIONS (up to 2 additional attempts with backoff)
    if failed_regions and (discovery_target <= 0 or len(_deduplicate_companies_by_domain(all_companies)) < discovery_target):
        logger.info(f"Retrying {len(failed_regions)} failed regions...")
        import time

        for retry_attempt in range(1, 3):  # 2 more attempts
            if not failed_regions:
                break

            backoff_delay = 30 * retry_attempt  # 30s, 60s
            logger.info(f"Retry attempt {retry_attempt}: waiting {backoff_delay}s before retrying {len(failed_regions)} regions")
            time.sleep(backoff_delay)

            regions_to_retry = failed_regions[:]
            failed_regions = []
            retry_stop = False

            with ThreadPoolExecutor(max_workers=4) as executor:
                retry_futures = {}
                for region_index, region, prev_error in regions_to_retry:
                    logger.info(f"Retrying region {region['name']} (previous error: {prev_error})")
                    future = executor.submit(
                        _run_region_agent,
                        region,
                        region_index,
                        len(regions),
                        run_id,
                        criteria,
                        target_qty,
                        discovery_target,
                        oversample_factor,
                        int(os.getenv("LEAD_LIST_REGION_QUERY_BUDGET", "10")),
                    )
                    retry_futures[future] = (region_index, region, prev_error)

                try:
                    iterator = as_completed(retry_futures, timeout=900)  # 15-minute timeout
                    for future in iterator:
                        region_index, region, prev_error = retry_futures[future]
                        try:
                            region_name, result, error = future.result(timeout=1)
                        except TimeoutError:
                            region_name = retry_futures[future][1]["name"]
                            error = f"Region {region_name} exceeded 15-minute timeout on retry"
                            result = None
                            logger.error(error)
                        except BaseException as e:
                            region_name = retry_futures[future][1]["name"]
                            error = f"Region {region_name} raised {type(e).__name__}: {e}"
                            result = None
                            logger.error(error, exc_info=True)

                        if error:
                            failed_regions.append((region_index, region, error))
                            logger.warning(f"Region {region_name} failed on retry attempt {retry_attempt}: {error}")
                            continue

                        if result:
                            region_companies = [c.model_dump() for c in (result.companies or [])]
                            region_contacts = [c.model_dump() for c in (result.contacts or [])]

                            all_companies.extend(region_companies)
                            all_contacts.extend(region_contacts)
                            successful_regions.append(region_name)

                            logger.info(
                                "✅ Retry succeeded for %s: %d companies, %d contacts",
                                region_name, len(region_companies), len(region_contacts)
                            )
                            if discovery_target > 0:
                                current_total = len(_deduplicate_companies_by_domain(all_companies))
                                if current_total >= discovery_target:
                                    logger.info(
                                        "Discovery target satisfied during retries (%d/%d); cancelling remaining retries",
                                        current_total,
                                        discovery_target,
                                    )
                                    retry_stop = True
                                    break
                        if retry_stop:
                            break
                except TimeoutError:
                    # Give up on any retries still running after the global timeout.
                    logger.error("Retry window exceeded overall timeout; cancelling remaining retry regions")
                    for future, (region_index, region, _) in retry_futures.items():
                        if future.done():
                            continue
                        future.cancel()
                        failed_regions.append((region_index, region, "Cancelled due to overall retry timeout"))
                if retry_stop:
                    for future in retry_futures:
                        if future.done():
                            continue
                        future.cancel()
                    break

    # Deduplicate by domain
    all_companies_deduped = _deduplicate_companies_by_domain(all_companies)

    # Extract just region names from failed regions list (which now contains tuples)
    failed_region_names = [region[1]["name"] for region in failed_regions] if failed_regions else []

    logger.info(
        "Parallel multi-region discovery complete: %d successful regions, %d failed regions",
        len(successful_regions), len(failed_region_names)
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
    if failed_region_names:
        quality_notes += f" (failed after retries: {', '.join(failed_region_names)})"

    return LeadListOutput(
        companies=company_models,
        contacts=contact_models,
        total_found=len(all_companies_deduped),
        search_exhausted=len(successful_regions) >= 3,  # Consider exhausted if at least 3 regions succeeded
        quality_notes=quality_notes
    )


def _discover_companies_secondary(
    run_id: str,
    criteria: Dict[str, Any],
    target_qty: int,
    discovery_target: int,
    companies_already_found: int,
) -> LeadListOutput:
    """
    Secondary discovery strategy to satisfy the persistence rule with a UNIQUE, non-overlapping approach.

    This run uses a single broad prompt (no regional split) and instructs the agent to
    pivot strategies: industry directories, association member lists, long-tail search queries,
    and state/city combinations not previously attempted. Intended as a follow-up only when
    the parallel strategy fell short.
    """
    logger.info(
        "Starting secondary discovery strategy for run_id=%s (already_found=%s discovery_target=%s)",
        run_id,
        companies_already_found,
        discovery_target,
    )
    agent = create_lead_list_agent()
    prompt = (
        "You are executing a SECONDARY discovery sweep. The first pass already ran in parallel regions.\n"
        "Your job now is to uncover ADDITIONAL, NON-OVERLAPPING companies that meet the criteria.\n"
        "- Avoid repeating domains already found; focus on long-tail and alternative sources.\n"
        "- Use UNIQUE search strategies: industry associations, local chambers, state/city permutations,\n"
        "  niche directories, and unconventional keywords.\n"
        "- Stop early if you exceed the discovery target.\n\n"
        f"Run id: {run_id}\n"
        f"Run criteria JSON: {json.dumps(criteria, ensure_ascii=False)}\n"
        f"Target final companies: {target_qty}\n"
        f"Discovery target (with oversample): {discovery_target}\n"
        f"Already found: {companies_already_found}\n"
        "Return ONLY your structured LeadListOutput. Prioritize new, non-overlapping companies.\n"
        "Cap tool usage to <= 12 search queries in this sweep.\n"
    )
    try:
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=80,
        )
    except TypeError as e:
        if "max_turns" in str(e):
            result = Runner.run_sync(agent, prompt)
        else:
            raise

    try:
        typed: LeadListOutput = result.final_output_as(LeadListOutput)  # type: ignore[assignment]
    except Exception as e:
        logger.warning("Secondary strategy failed to parse structured output: %s", e)
        typed = LeadListOutput()
    finally:
        # CRITICAL: Reset MCP counters after agent run to prevent deluge
        from rv_agentic.tools import mcp_client
        mcp_client.reset_mcp_counters()

        # Force garbage collection to clean up any orphaned async tasks
        import gc
        gc.collect()

        # Extended pause for MCP session cleanup (1.0s instead of 0.3s)
        import time
        time.sleep(1.0)

    # Tag discovery source for traceability
    for ct in typed.contacts or []:
        ct.quality_notes = (ct.quality_notes or "") + " | secondary sweep"

    logger.info(
        "Secondary discovery complete: %d companies, %d contacts",
        len(typed.companies or []),
        len(typed.contacts or []),
    )
    return typed


def process_run(run: Dict[str, Any], heartbeat: WorkerHeartbeat | None = None) -> None:
    """Process a single lead list run.

    ``run`` is expected to match the ``pm_pipeline.runs`` schema: it
    should contain criteria, target_quantity, stage, etc. This function
    delegates reasoning and tool calls to the Lead List Agent.
    """

    # Reset MCP counters per run to avoid cross-run leakage and runaway calls.
    mcp_client.reset_mcp_counters()

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

    def _promote_from_staging_until_gap_closed(
        *,
        max_passes: int = 3,
        promotion_batch: int = 25,
    ) -> int:
        """Continuously promote staging companies until discovery gap closes or passes exhausted."""

        promoted_total = 0
        goal = discovery_target or target_qty or 0
        for _ in range(max_passes):
            try:
                gap_info = supabase_client.get_pm_company_gap(run_id)
                ready_now = int((gap_info or {}).get("companies_ready") or 0)
            except Exception:
                ready_now = 0
            if goal and ready_now >= goal:
                break
            to_fetch = max(0, goal - ready_now)
            if to_fetch <= 0:
                break
            promoted = supabase_client.promote_staging_companies_to_run(
                search_run_id=run_id,
                pm_run_id=run_id,
                pms_required=pms_required or None,
                max_companies=min(promotion_batch, to_fetch),
            )
            if promoted:
                promoted_total += promoted
                supabase_client.insert_audit_event(
                    run_id=run_id,
                    entity_type="run",
                    entity_id=run_id,
                    event="staging_promoted",
                    meta={"count": promoted, "pass": promoted_total},
                )
                _log_run_event(
                    run_id,
                    "company_discovery",
                    "staging_promoted",
                    {"count": promoted, "ready_after": ready_now + promoted},
                )
            if promoted == 0:
                break
        return promoted_total

    def _insert_from_structured_output(
        typed: LeadListOutput,
        *,
        target_qty: int,
        discovery_target: int,
        existing_ready: int,
    ) -> Tuple[int, int]:
        """Insert companies/contacts from structured output; returns (companies_inserted, contacts_inserted)."""
        companies: List[Dict[str, Any]] = []
        contacts: List[Dict[str, Any]] = []

        blocked = set(d.lower().strip() for d in supabase_client.get_blocked_domains())

        target_quantity = target_qty
        inserted = 0
        state = (criteria.get("state") or "").strip().upper() or ""

        logger.info(
            "Processing agent output: agent returned %d companies (total_found=%d, search_exhausted=%s)",
            len(typed.companies or []),
            typed.total_found,
            typed.search_exhausted
        )

        if typed.quality_notes:
            logger.info("Agent quality notes: %s", typed.quality_notes)

        companies_remaining = max(0, discovery_target - existing_ready)
        companies_to_insert = min(len(typed.companies or []), companies_remaining) if companies_remaining > 0 else len(typed.companies or [])

        logger.info(
            "Inserting up to %d companies (final_target=%d, discovery_target=%d, already_ready=%d)",
            companies_to_insert, target_qty, discovery_target, existing_ready
        )

        for idx, cc in enumerate(typed.companies or []):
            if target_quantity > 0 and inserted >= companies_remaining and companies_remaining > 0:
                logger.info(
                    "Target quantity reached for this pass: inserted=%d companies, stopping (agent had %d more)",
                    inserted,
                    len(typed.companies) - idx
                )
                break

            domain = (cc.domain or "").lower().strip()
            if not domain or domain in blocked:
                logger.debug("Skipping blocked/empty domain: %s", domain)
                continue

            try:
                supabase_client.insert_staging_company(
                    search_run_id=run_id,
                    name=cc.name or domain,
                    domain=domain,
                    website=f"https://{domain}",
                    state=(cc.state or state or "NA"),
                    pms_detected=getattr(cc, "pms_detected", None),
                    pms_confidence=1.0,
                    raw={"source": getattr(cc, "discovery_source", None) or "agent_structured"},
                )
            except Exception:
                pass

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
                    supabase_client.insert_audit_event(
                        run_id=run_id,
                        entity_type="company",
                        entity_id=None,
                        event="company_suppressed",
                        meta={"domain": domain, "reason": reason, "details": details},
                    )
                    _log_run_event(run_id, "company_discovery", "company_suppressed", {"domain": domain, "reason": reason})
                    continue
            except Exception as e:
                logger.warning("HubSpot suppression check failed for domain=%s: %s", domain, e)

            name = cc.name or domain
            website = f"https://{domain}"
            try:
                supabase_client.insert_staging_company(
                    search_run_id=run_id,
                    name=name,
                    domain=domain,
                    website=website,
                    state=(cc.state or state or "NA"),
                    pms_detected=getattr(cc, "pms_detected", None),
                    pms_confidence=1.0,
                    raw={"source": getattr(cc, "discovery_source", None) or "agent_structured"},
                )
            except Exception:
                pass
            try:
                row = supabase_client.insert_company_candidate(
                    run_id=run_id,
                    name=name,
                    website=website,
                    domain=domain,
                    state=(cc.state or state or "NA"),
                    discovery_source=getattr(cc, "discovery_source", None) or "agent_structured",
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
                    supabase_client.insert_audit_event(
                        run_id=run_id,
                        entity_type="company",
                        entity_id=row.get("id"),
                        event="company_inserted",
                        meta={"domain": domain, "source": getattr(cc, "discovery_source", None) or "agent_structured"},
                    )
                    _log_run_event(
                        run_id,
                        "company_discovery",
                        "company_inserted",
                        {"company_id": row.get("id"), "domain": domain, "source": getattr(cc, "discovery_source", None) or "agent_structured"},
                    )
                else:
                    logger.info(
                        "Duplicate/blocked company skipped domain=%s run_id=%s (likely already present)",
                        domain,
                        run_id,
                    )
            except Exception:
                logger.exception(
                    "Structured insert_company_candidate failed for domain=%s run_id=%s",
                    domain,
                    run_id,
                )

        # Check if companies were actually inserted (not just in THIS run of the worker)
        # The 'companies' list only tracks NEW inserts in this worker run, but companies
        # may already exist from previous runs. Check the actual database count.
        if not companies:
            # Verify there truly are no companies before sending error notification
            existing_count = supabase_client.count_company_candidates(run_id)
            if existing_count == 0:
                msg = (
                    f"Lead List Agent completed without discovering any companies for run {run_id}. "
                    "This indicates that no companies match the given PMS/location "
                    "constraints after reasonable search."
                )
                logger.warning(msg)
                try:
                    supabase_client.update_pm_run_status(run_id=run_id, status="needs_user_decision", error=msg)
                    notifications.send_run_notification(
                        run_id=run_id,
                        subject="Lead list run needs review (no companies discovered)",
                        body=msg,
                        to_email="mlerner@rebarhq.ai",
                    )
                except Exception:
                    logger.exception("Failed to update status/notify after empty company set for run %s", run_id)
                return (0, 0)
            else:
                # Companies exist from previous worker run - this is normal after restart
                logger.info(
                    "No NEW companies inserted in this worker run for %s, but %d companies already exist. "
                    "This is expected when rerunning/restarting the worker.",
                    run_id,
                    existing_count,
                )
                # Return early since contact handling below requires the companies list
                return (existing_count, 0)

        domain_to_company_id: Dict[str, str] = {}
        for row in companies:
            dom = (row.get("domain") or "").lower().strip()
            cid = row.get("id")
            if dom and cid:
                domain_to_company_id[dom] = str(cid)

        per_company_counts: Dict[str, int] = {}
        for ct in (typed.contacts or []):
            domain = (ct.company_domain or "").lower().strip()
            if not domain:
                continue
            company_id = domain_to_company_id.get(domain)
            if not company_id:
                continue

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
                    supabase_client.insert_audit_event(
                        run_id=run_id,
                        entity_type="contact",
                        entity_id=row.get("id"),
                        event="contact_inserted",
                        meta={"company_id": company_id, "email": row.get("email"), "name": row.get("full_name")},
                    )
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
            "Structured pass complete for run id=%s with %d company candidate(s) and %d contact(s)",
            run_id,
            len(companies),
            len(contacts),
        )
        # Promote staged rows again now that structured inserts are done.
        try:
            _promote_from_staging_until_gap_closed(max_passes=2, promotion_batch=discovery_target or 50)
        except Exception:
            logger.exception("Staging promotion failed for run_id=%s", run_id)

        # Final fallback: if we still have a gap, pull from research_database by PMS/geo.
        try:
            gap_info = supabase_client.get_pm_company_gap(run_id)
            ready_now = int((gap_info or {}).get("companies_ready") or 0)
        except Exception:
            ready_now = len(companies)
        gap_remaining = max(0, target_qty - ready_now) if target_qty else 0
        if gap_remaining > 0 and pms_required:
            try:
                fallback_rows = supabase_client.find_company(
                    pms=pms_required,
                    city=city or None,
                    limit=gap_remaining * 2 or 10,
                ) or []
            except Exception:
                fallback_rows = []
            for row in fallback_rows:
                if gap_remaining <= 0:
                    break
                domain = (row.get("domain") or "").strip().lower()
                if not domain or "." not in domain:
                    continue
                name = (row.get("company_name") or domain).strip() or domain
                website = (row.get("company_url") or "").strip() or f"https://{domain}"
                try:
                    supabase_client.insert_staging_company(
                        search_run_id=run_id,
                        name=name,
                        domain=domain,
                        website=website,
                        state=(row.get("hq_state") or state or "NA"),
                        pms_detected=pms_required,
                        pms_confidence=1.0,
                        raw={"source": "research_database_fallback"},
                    )
                    res = supabase_client.insert_company_candidate(
                        run_id=run_id,
                        name=name,
                        website=website,
                        domain=domain,
                        state=(row.get("hq_state") or state or "NA"),
                        discovery_source="research_database_fallback",
                        status="validated",
                    )
                    if res:
                        gap_remaining -= 1
                        supabase_client.insert_audit_event(
                            run_id=run_id,
                            entity_type="company",
                            entity_id=res.get("id"),
                            event="company_inserted",
                            meta={"domain": domain, "source": "research_database_fallback"},
                        )
                except Exception:
                    continue

        return (len(companies), len(contacts))

    # Update heartbeat with current task including batch progress
    if heartbeat:
        quantity = int(criteria.get("quantity") or run.get("target_quantity") or 0)
        pms = criteria.get("pms") or "any"
        state = criteria.get("state") or "any"

        # Check current progress for batch status; treat failures as 0-ready.
        try:
            existing_companies = supabase_client.get_pm_company_gap(run_id)
        except Exception:
            logger.exception(
                "get_pm_company_gap failed while updating heartbeat for run_id=%s; treating as 0-ready",
                run_id,
            )
            existing_companies = None
        companies_ready = 0
        if isinstance(existing_companies, dict):
            try:
                companies_ready = int(existing_companies.get("companies_ready") or 0)
            except Exception:
                companies_ready = 0

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

    # Oversample to account for attrition in enrichment stages (suppression, ICP filters, contact failures).
    oversample_factor = float(os.getenv("LEAD_LIST_OVERSAMPLE_FACTOR", "2.0"))
    discovery_target = int(target_qty * oversample_factor) if target_qty > 0 else 0

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
                supabase_client.insert_staging_company(
                    search_run_id=run_id,
                    name=name,
                    domain=domain,
                    website=website,
                    state=state or "NA",
                    pms_detected=pms_required,
                    pms_confidence=1.0,
                    raw={"source": "pms_subdomains"},
                )
            except Exception:
                pass
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
                supabase_client.insert_staging_company(
                    search_run_id=run_id,
                    name=name,
                    domain=domain,
                    website=website,
                    state=(neo_state or state or "NA"),
                    pms_detected=pms_required,
                    pms_confidence=1.0,
                    raw={"source": "neo_pms", "city": neo_city},
                )
            except Exception:
                pass
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

        # Immediately promote any eligible staged seeds to reduce later MCP load.
        try:
            _promote_from_staging_until_gap_closed(max_passes=2, promotion_batch=seed_limit)
        except Exception:
            logger.exception("Staging promotion failed after seeding for run_id=%s", run_id)

    # Check how many companies we already have; treat errors as 0-ready.
    try:
        existing_companies = supabase_client.get_pm_company_gap(run_id)
    except Exception:
        logger.exception(
            "get_pm_company_gap failed before agent call for run_id=%s; treating as 0-ready",
            run_id,
        )
        existing_companies = None
    companies_ready = 0
    if isinstance(existing_companies, dict):
        try:
            companies_ready = int(existing_companies.get("companies_ready") or 0)
        except Exception:
            companies_ready = 0
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
        # Ensure stage advances when gaps are already closed (e.g., seeding filled the pool).
        _maybe_advance_run_stage(run_id)
        return None

    # If seeds already satisfy discovery_target, skip agent to avoid MCP load.
    if companies_remaining <= 0 and discovery_target > 0:
        logger.info(
            "Discovery target already satisfied after seeding (ready=%d target=%d); skipping MCP agent",
            companies_ready,
            discovery_target,
        )
        _maybe_advance_run_stage(run_id)
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

    companies_inserted, contacts_inserted = _insert_from_structured_output(
        typed,
        target_qty=target_qty,
        discovery_target=discovery_target,
        existing_ready=companies_ready,
    )

    # If discovery target still not satisfied, trigger a secondary unique strategy sweep.
    if discovery_target > 0:
        try:
            gap_info = supabase_client.get_pm_company_gap(run_id)
            ready_after_first = int((gap_info or {}).get("companies_ready") or companies_ready)
        except Exception:
            ready_after_first = companies_ready + companies_inserted

        discovery_remaining = max(0, discovery_target - ready_after_first)
        if discovery_remaining > 0:
            logger.info(
                "Discovery target still short after primary sweep: ready=%d target=%d gap=%d. Running secondary sweep.",
                ready_after_first,
                discovery_target,
                discovery_remaining,
            )
            secondary = _discover_companies_secondary(
                run_id=run_id,
                criteria=criteria,
                target_qty=target_qty,
                discovery_target=discovery_target,
                companies_already_found=ready_after_first,
            )
            _insert_from_structured_output(
                secondary,
                target_qty=target_qty,
                discovery_target=discovery_target,
                existing_ready=ready_after_first,
            )
            try:
                _promote_from_staging_until_gap_closed(max_passes=2, promotion_batch=discovery_target or 50)
            except Exception:
                logger.exception("Post-secondary staging promotion failed for run_id=%s", run_id)

    # Final check: if we are still below the final target quantity after all strategies,
    # surface a decision + alert to keep the workflow visible.
    try:
        gap_info = supabase_client.get_pm_company_gap(run_id)
        final_ready = int((gap_info or {}).get("companies_ready") or 0)
    except Exception:
        final_ready = 0
    if target_qty and final_ready < target_qty:
        try:
            # Last chance: promote any remaining staging rows to cover gap before surfacing decision.
            _promote_from_staging_until_gap_closed(max_passes=2, promotion_batch=discovery_target or 50)
            gap_info = supabase_client.get_pm_company_gap(run_id)
            final_ready = int((gap_info or {}).get("companies_ready") or final_ready)
        except Exception:
            pass

    if target_qty and final_ready < target_qty:
        gap = target_qty - final_ready
        msg = (
            f"Discovery still short after multi-pass search. Run {run_id} has {final_ready}/{target_qty} companies "
            f"(gap={gap}). Consider broadening geography/PMS or rerunning with relaxed filters."
        )
        logger.warning(msg)
        try:
            existing_run = supabase_client.get_pm_run(run_id) or {}
            prior_status = str(existing_run.get("status") or "")
            prior_error = (existing_run.get("error") or "").strip()
            # Avoid spamming notifications if we've already raised this identical gap.
            already_notified = prior_status == "needs_user_decision" and prior_error == msg
            if not already_notified:
                supabase_client.update_pm_run_status(run_id=run_id, status="needs_user_decision", error=msg)
                supabase_client.insert_audit_event(
                    run_id=run_id,
                    entity_type="run",
                    entity_id=run_id,
                    event="gap_unresolved",
                    meta={"ready": final_ready, "target": target_qty, "gap": gap},
                )
                notifications.send_run_notification(
                    run_id=run_id,
                    subject="Lead list run needs your decision (discovery gap)",
                    body=msg,
                    to_email="mlerner@rebarhq.ai",
                )
        except Exception:
            logger.exception("Failed to update status/notify after discovery gap for run %s", run_id)
    _maybe_advance_run_stage(str(run.get("id") or ""))
    return typed


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
        # CRITICAL FIX: When running in targeted test mode (RUN_FILTER_ID set),
        # exit immediately if that specific run has reached a terminal state.
        # This prevents stale workers from continuing to poll after test completion.
        if run_filter_id:
            import uuid
            try:
                uuid.UUID(run_filter_id)  # Validate UUID format
                # Fetch the specific run to check its status
                filtered_run = supabase_client.get_run_by_id(run_filter_id)
                if filtered_run:
                    run_status = (filtered_run.get("status") or "").strip()
                    # Terminal states: completed, error, archived
                    if run_status in ("completed", "error", "archived"):
                        logger.info(
                            "RUN_FILTER_ID %s has reached terminal status '%s'; exiting worker to prevent stale polling",
                            run_filter_id,
                            run_status
                        )
                        break
            except Exception as e:
                logger.warning("Failed to check RUN_FILTER_ID %s status: %s", run_filter_id, e)

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
                # Process run
                # Timeout protection is handled at region level (15min per region)
                # With 4 regions + retries, worst case is ~90 minutes
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
                    final_gap = max(0, target_qty - companies_ready)

                if final_gap > 0:
                    # Agent couldn't find enough companies to meet the FINAL target.
                    # Only fail if we don't have enough for the final requirement,
                    # not the discovery target (which includes oversample buffer).
                    logger.warning(
                        "Run %s discovery incomplete: %d discovered (target: %d final, discovery_target: %d), %d gap",
                        run_id, companies_ready, target_qty, discovery_target, final_gap
                    )
                    msg = (
                        f"Discovery shortfall for run {run_id}: {companies_ready} discovered "
                        f"(final target {target_qty}, gap {final_gap}). Unique passes exhausted."
                    )
                    mark_run_complete(run, status="needs_user_decision", error=msg)
                    try:
                        notifications.send_run_notification(
                            run_id=run_id,
                            subject="Lead list run needs your decision (discovery shortfall)",
                            body=msg,
                            to_email="mlerner@rebarhq.ai",
                        )
                    except Exception:
                        logger.warning("Notification failed for run %s discovery shortfall", run_id)
                    continue
                else:
                    logger.info(
                        "Run %s discovery sufficient: %d discovered (target: %d final, discovery_target: %d with oversample)",
                        run_id, companies_ready, target_qty, discovery_target
                    )

                # Target met or no target specified - mark as complete
                # On success, mark the discovery stage complete. Downstream
                # workers use ``stage`` to drive additional work; the `status`
                # flag is used only for queueing active vs. finished runs.
                mark_run_complete(run, status="completed")
            except Exception as exc:  # pragma: no cover
                logger.exception("Error processing run id=%s", run.get("id"))
                mark_run_complete(run, status="error", error=str(exc))
                try:
                    notifications.send_run_notification(
                        run_id=str(run.get("id")),
                        subject="Lead list worker error",
                        body=f"Run {run.get('id')} failed in lead_list_runner: {exc}",
                        to_email="mlerner@rebarhq.ai",
                    )
                except Exception:
                    logger.warning("Failed to send error notification for run %s", run.get("id"))

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
        # Ignore non-UUID RUN_FILTER_ID to avoid crashes in testing.
        import uuid
        try:
            uuid.UUID(run_filter_id)
        except Exception:
            logger.warning("RUN_FILTER_ID is not a valid UUID; ignoring filter: %s", run_filter_id)
            return []
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
