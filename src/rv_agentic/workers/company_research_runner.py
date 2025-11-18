"""Worker that enriches company candidates with detailed research notes.

This worker pulls companies that reached the ``company_research`` stage,
runs the Company Researcher Agent, and stores the results in
``pm_pipeline.company_research`` for downstream reuse.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict

from agents import Runner

from rv_agentic.agents.company_researcher_agent import create_company_researcher_agent
from rv_agentic.config.settings import get_settings
from rv_agentic.services import supabase_client, retry
from rv_agentic.services.heartbeat import WorkerHeartbeat
from rv_agentic.workers.utils import load_env_files

logger = logging.getLogger(__name__)


def _ensure_openai_api_key() -> None:
    settings = get_settings()
    if settings.openai_api_key and "OPENAI_API_KEY" not in os.environ:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key


def _build_prompt(company: Dict[str, Any], criteria: Dict[str, Any], run_id: str) -> str:
    name = company.get("name") or company.get("domain") or "Unknown Company"
    domain = company.get("domain") or ""
    website = company.get("website") or ""
    state = company.get("state") or ""

    prompt = (
        "You are assisting the async lead list pipeline.\n"
        "Research the following company and produce the strict ICP brief requested in your system instructions.\n\n"
        f"Run id: {run_id}\n"
        f"Run criteria JSON: {json.dumps(criteria, ensure_ascii=False)}\n\n"
        "Company details:\n"
        f"- Name: {name}\n"
        f"- Domain: {domain}\n"
        f"- Website: {website}\n"
        f"- State: {state}\n\n"
        "Focus on whether this company matches the run criteria, highlight ICP-relevant insights, "
        "and include 1–3 decision makers with verified contact info when possible.\n"
    )
    return prompt


def _maybe_advance_run_stage(run_id: str) -> None:
    resume = supabase_client.get_run_resume_plan(run_id)
    if not resume:
        return

    stage = (resume.get("stage") or "").strip()
    companies_gap = int(resume.get("companies_gap") or 0)
    if stage == "company_discovery" and companies_gap <= 0:
        supabase_client.set_run_stage(run_id=run_id, stage="company_research")
        stage = "company_research"

    if stage == "company_research":
        if not supabase_client.has_company_research_queue(run_id):
            supabase_client.set_run_stage(run_id=run_id, stage="contact_discovery")


def process_company_claim(agent, worker_id: str, lease_seconds: int, heartbeat: WorkerHeartbeat | None = None) -> bool:
    claim = supabase_client.claim_company_for_research(worker_id, lease_seconds)
    if not claim:
        return False

    company_id = claim.get("id")
    run_id = claim.get("run_id")
    domain = claim.get("domain", "unknown")

    # Update heartbeat with current task
    if heartbeat:
        from datetime import datetime, timedelta, timezone
        lease_expires = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        heartbeat.update_task(
            run_id=run_id,
            task=f"Researching company: {domain}",
            lease_expires_at=lease_expires,
            status="processing"
        )

    try:
        run = supabase_client.get_pm_run(run_id)
        if not run:
            logger.warning("No pm_pipeline run found for run_id=%s", run_id)
            return True
        criteria = run.get("criteria") or {}
        prompt = _build_prompt(claim, criteria, run_id)

        logger.info(
            "Running company research for run_id=%s company_id=%s domain=%s",
            run_id,
            company_id,
            claim.get("domain"),
        )
        # Use retry logic for agent calls (3 attempts with exponential backoff)
        result = retry.retry_agent_call(
            Runner.run_sync,
            agent,
            prompt,
            max_attempts=3,
            base_delay=1.0
        )

        facts = {
            "analysis_markdown": result.final_output,
            "prompt": prompt,
        }
        supabase_client.insert_company_research(
            run_id=run_id,
            company_id=company_id,
            facts=facts,
        )
        logger.info(
            "Stored company research for run_id=%s company_id=%s", run_id, company_id
        )
        _maybe_advance_run_stage(run_id)
    except Exception:
        logger.exception(
            "Error while processing company research for run_id=%s company_id=%s",
            run_id,
            company_id,
        )
    finally:
        if company_id:
            supabase_client.release_company_lease(company_id)
        # Mark worker as idle after processing
        if heartbeat:
            heartbeat.mark_idle()
    return True


def main() -> None:
    load_env_files()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _ensure_openai_api_key()

    worker_id = os.getenv("COMPANY_RESEARCH_WORKER_ID") or f"company-research-{uuid.uuid4()}"
    lease_seconds = int(os.getenv("COMPANY_RESEARCH_LEASE_SECONDS", "300"))
    idle_sleep = int(os.getenv("COMPANY_RESEARCH_IDLE_SLEEP", "5"))
    heartbeat_interval = int(os.getenv("WORKER_HEARTBEAT_INTERVAL", "30"))

    agent = create_company_researcher_agent()
    logger.info(
        "Company research worker starting up worker_id=%s lease_seconds=%s",
        worker_id,
        lease_seconds,
    )

    # Start heartbeat monitoring
    heartbeat = WorkerHeartbeat(
        worker_id=worker_id,
        worker_type="company_research",
        interval_seconds=heartbeat_interval,
        metadata={"lease_seconds": lease_seconds}
    )
    heartbeat.start()

    max_loops_env = os.getenv("WORKER_MAX_LOOPS")
    run_filter_id = os.getenv("RUN_FILTER_ID", "").strip()
    try:
        max_loops = int(max_loops_env) if max_loops_env is not None else None
    except ValueError:
        max_loops = None
    if max_loops is None and run_filter_id:
        max_loops = 3

    loops = 0
    try:
        while True:
            claimed = process_company_claim(agent, worker_id, lease_seconds, heartbeat)
            if not claimed:
                logger.info("No companies ready for research; sleeping %s seconds", idle_sleep)
                heartbeat.mark_idle()
                time.sleep(idle_sleep)
            loops += 1
            if max_loops is not None and loops >= max_loops:
                logger.info(
                    "WORKER_MAX_LOOPS=%s reached in company_research_runner; exiting after %s loop(s)",
                    max_loops,
                    loops,
                )
                break
    finally:
        # Ensure heartbeat is stopped on exit
        heartbeat.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
