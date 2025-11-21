"""Worker that fills contact gaps for company candidates.

Consumes ``pm_pipeline.v_contact_gap_per_company``, runs the Contact
Researcher Agent for each company needing contacts, and inserts the
resulting contacts into ``pm_pipeline.contact_candidates`` until the
run's contact gap is closed.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, List

from agents import Runner

from rv_agentic.agents.contact_researcher_agent import (
    create_contact_researcher_agent,
    ContactResearchOutput,
)
from rv_agentic.config.settings import get_settings
from rv_agentic.services import supabase_client, retry, research_backfill
from rv_agentic.services.heartbeat import WorkerHeartbeat
from rv_agentic.services.notifications import send_run_notification
from rv_agentic.workers.utils import load_env_files

logger = logging.getLogger(__name__)


def _ensure_openai_api_key() -> None:
    settings = get_settings()
    if settings.openai_api_key and "OPENAI_API_KEY" not in os.environ:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key


def _build_prompt(company: Dict[str, Any], run: Dict[str, Any], needed: int) -> str:
    criteria = run.get("criteria") or {}
    run_id = run.get("id")
    name = company.get("name") or company.get("domain") or "Unknown Company"
    domain = company.get("domain") or ""
    state = company.get("state") or ""
    query_budget = int(os.getenv("CONTACT_MCP_QUERY_BUDGET", "12"))

    prompt = (
        "You are assisting the async lead list pipeline by enriching contacts for a target company.\n"
        "Return your standard Markdown brief **and** populate the ContactResearchOutput with the best-fit\n"
        "decision makers you find. Focus on operators who match RentVine's ICP.\n\n"
        f"Run id: {run_id}\n"
        f"Run criteria JSON: {criteria}\n\n"
        "Company details:\n"
        f"- Name: {name}\n"
        f"- Domain: {domain}\n"
        f"- State: {state}\n\n"
        f"Contact requirement: Need at least {needed} high-quality decision makers with verified or high-confidence emails.\n"
        f"Skip anyone who would obviously be outside RentVine's ICP. Use MCP tools for verified emails and LinkedIn URLs. "
        f"Keep MCP tool usage efficient: aim for <= {query_budget} MCP calls for this company and stop early once you meet the requirement.\n"
    )
    return prompt


def _contacts_to_insert(
    typed_output: ContactResearchOutput,
    needed: int,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for contact in typed_output.contacts or []:
        if needed <= 0:
            break
        entry = {
            "company_domain": (contact.company_domain or "").lower().strip(),
            "full_name": contact.full_name,
            "title": contact.title,
            "email": contact.email,
            "linkedin_url": contact.linkedin_url,
            "notes": contact.notes,
        }
        results.append(entry)
        needed -= 1
    return results


def _insert_contacts(
    run_id: str,
    company_id: str,
    contacts: List[Dict[str, Any]],
    agent_markdown: str = "",
) -> None:
    for contact in contacts:
        email = contact.get("email")
        linkedin = contact.get("linkedin_url")
        notes = contact.get("notes")
        idem = (email or linkedin or contact.get("full_name") or "").lower().strip()

        # Store both the agent markdown output and contact-specific notes
        evidence = []
        if agent_markdown:
            evidence.append({"agent_output": agent_markdown})
        if notes:
            evidence.append({"notes": notes})

        try:
            supabase_client.insert_contact_candidate(
                run_id=run_id,
                company_id=company_id,
                full_name=contact["full_name"],
                title=contact.get("title"),
                email=email,
                linkedin_url=linkedin,
                evidence=evidence if evidence else None,
                status="validated",
                idem_key=idem or None,
            )
            logger.info(
                "Inserted contact run_id=%s company_id=%s name=%s email=%s linkedin=%s",
                run_id,
                company_id,
                contact.get("full_name"),
                email,
                linkedin,
            )
            supabase_client.insert_audit_event(
                run_id=run_id,
                entity_type="contact",
                entity_id=None,
                event="contact_inserted",
                meta={"company_id": company_id, "name": contact.get("full_name"), "email": email, "linkedin": linkedin},
            )
            logger.info(
                "EVENT|run_id=%s|stage=contact_discovery|action=contact_inserted|data=%s",
                run_id,
                {
                    "company_id": company_id,
                    "name": contact.get("full_name"),
                    "email": email,
                    "linkedin": linkedin,
                },
            )
        except Exception:
            logger.exception(
                "Failed to insert contact for run_id=%s company_id=%s name=%s",
                run_id,
                company_id,
                contact["full_name"],
            )


def _advance_stage_if_ready(run_id: str) -> None:
    resume = supabase_client.get_run_resume_plan(run_id)
    if not resume:
        return
    stage = (resume.get("stage") or "").strip()
    original_stage = stage
    if stage == "company_discovery":
        companies_gap = int(resume.get("companies_gap") or 0)
        if companies_gap <= 0:
            supabase_client.set_run_stage(run_id=run_id, stage="company_research")
            stage = "company_research"
    if stage == "company_research":
        if not supabase_client.has_company_research_queue(run_id):
            supabase_client.set_run_stage(run_id=run_id, stage="contact_discovery")
            stage = "contact_discovery"
    if stage == "contact_discovery":
        target_qty = int((resume.get("target_quantity") or 0))
        gap_top = supabase_client.get_contact_gap_for_top_companies(run_id, target_qty) if target_qty else None
        if gap_top is not None:
            total_gap = int(gap_top.get("gap_total") or 0)
            ready_companies = int(gap_top.get("ready_companies") or 0)
        else:
            gap = supabase_client.get_contact_gap_summary(run_id)
            total_gap = int(gap.get("contacts_min_gap_total") or 0) if gap else 0
            ready_companies = 0
        if total_gap <= 0 and (target_qty == 0 or ready_companies >= target_qty):
            # Mark run as completed and backfill researched data into the shared
            # research_database so no fully researched company/contact goes to waste.
            supabase_client.set_run_stage(run_id=run_id, stage="done", status="completed")
            try:
                companies_summary = research_backfill.backfill_run_companies(run_id)
                contacts_summary = research_backfill.backfill_run_contacts(run_id)
                logger.info(
                    "Backfill complete for run_id=%s companies=%s contacts=%s",
                    run_id,
                    companies_summary,
                    contacts_summary,
                )
            except Exception:
                # Backfill is best-effort and must not break the main pipeline.
                logger.exception("Backfill failed for run_id=%s", run_id)

            # COMPLETION FLOW: Export CSVs and send email notification
            try:
                from rv_agentic.services import export
                import os
                import tempfile

                # Create temp directory for CSV exports
                output_dir = tempfile.mkdtemp(prefix=f"leadlist_{run_id[:8]}_")
                logger.info("Exporting run_id=%s to %s", run_id, output_dir)

                # Export both CSVs
                companies_path, contacts_path = export.export_run_to_files(run_id, output_dir)
                logger.info("Exported CSVs: companies=%s contacts=%s", companies_path, contacts_path)

                # Read CSV files as bytes for email attachments
                with open(companies_path, "rb") as f:
                    companies_bytes = f.read()
                with open(contacts_path, "rb") as f:
                    contacts_bytes = f.read()

                # Get notification email from run criteria
                criteria = run.get("criteria") or {}
                notification_email = criteria.get("notification_email")

                if notification_email:
                    # Get company/contact counts for email body
                    from rv_agentic.services.notifications import send_run_notification
                    companies_count = len(companies_summary.get("backfilled", [])) if companies_summary else target_qty
                    contacts_count = len(contacts_summary.get("backfilled", [])) if contacts_summary else 0

                    email_body = f"""Lead List Complete!

Your lead list request has been successfully completed.

Results:
- Companies: {companies_count}
- Contacts: {contacts_count}
- Run ID: {run_id}

The attached CSV files contain all enriched company and contact data including:
- Company agent summaries, PMS info, ICP scores
- Contact details with personal/professional anecdotes and agent summaries

Please review the attached files and reach out if you have any questions.
"""

                    # Send email with CSV attachments
                    attachments = [
                        (os.path.basename(companies_path), companies_bytes, "text/csv"),
                        (os.path.basename(contacts_path), contacts_bytes, "text/csv"),
                    ]

                    send_run_notification(
                        run_id=run_id,
                        subject=f"Lead List Complete: {companies_count} companies ready",
                        body=email_body,
                        to_email=notification_email,
                        attachments=attachments,
                    )
                    logger.info("Sent completion email to %s for run_id=%s", notification_email, run_id)
                else:
                    logger.warning("No notification_email in criteria for run_id=%s - skipping email", run_id)

                # Clean up temp files
                try:
                    import shutil
                    shutil.rmtree(output_dir)
                except Exception:
                    pass

            except Exception:
                # Completion flow is best-effort - don't break the pipeline
                logger.exception("Completion flow failed for run_id=%s", run_id)


def process_contact_gap(agent, worker_id: str, lease_seconds: int, heartbeat: WorkerHeartbeat | None = None) -> bool:
    claim = supabase_client.claim_company_for_contacts(worker_id, lease_seconds)
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
            task=f"Finding contacts for: {domain}",
            lease_expires_at=lease_expires,
            status="processing"
        )

    try:
        run = supabase_client.get_pm_run(run_id)
        if not run:
            logger.warning("Run not found for run_id=%s", run_id)
            return True
        gap_info = supabase_client.get_contact_gap_for_company(run_id, company_id)
        if not gap_info or int(gap_info.get("contacts_min_gap") or 0) <= 0:
            return True

        needed = int(gap_info.get("contacts_min_gap") or 0)
        prompt = _build_prompt(claim, run, needed)
        logger.info(
            "Running contact research for run_id=%s company_id=%s need=%s",
            run_id,
            company_id,
            needed,
        )
        # Use retry logic for agent calls (3 attempts with exponential backoff)
        result = retry.retry_agent_call(
            Runner.run_sync,
            agent,
            prompt,
            max_attempts=3,
            base_delay=1.0
        )
        typed = result.final_output_as(ContactResearchOutput)
        agent_markdown = result.final_output  # Capture full markdown output
        structured_contacts = _contacts_to_insert(typed, needed)
        logger.info(
            "Contact researcher returned %d structured contact(s) (needed %d) for run_id=%s company_id=%s",
            len(typed.contacts or []),
            needed,
            run_id,
            company_id,
        )
        if structured_contacts:
            _insert_contacts(run_id, company_id, structured_contacts, agent_markdown=agent_markdown)
        else:
            logger.warning(
                "No insertable contacts produced for run_id=%s company_id=%s (gap remains=%s)",
                run_id,
                company_id,
                needed,
            )
        _advance_stage_if_ready(run_id)
    except Exception:
        logger.exception(
            "Error during contact research for run_id=%s company_id=%s",
            run_id,
            company_id,
        )
    finally:
        # CRITICAL: Reset MCP counters after each agent run to prevent deluge
        try:
            from rv_agentic.tools import mcp_client
            mcp_client.reset_mcp_counters()
            logger.debug("Reset MCP counters after contact research for run_id=%s company_id=%s", run_id, company_id)
        except Exception as mcp_err:
            logger.warning("Failed to reset MCP counters: %s", mcp_err)

        # Force garbage collection to clean up any orphaned async tasks
        import gc
        gc.collect()

        # Extended pause for MCP session cleanup (1.0s instead of 0.3s)
        import time
        time.sleep(1.0)

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

    worker_id = os.getenv("CONTACT_RESEARCH_WORKER_ID") or f"contact-research-{uuid.uuid4()}"
    lease_seconds = int(os.getenv("CONTACT_RESEARCH_LEASE_SECONDS", "300"))
    idle_sleep = int(os.getenv("CONTACT_RESEARCH_IDLE_SLEEP", "5"))
    heartbeat_interval = int(os.getenv("WORKER_HEARTBEAT_INTERVAL", "30"))

    agent = create_contact_researcher_agent()
    logger.info(
        "Contact research worker starting worker_id=%s lease_seconds=%s",
        worker_id,
        lease_seconds,
    )

    # Start heartbeat monitoring
    heartbeat = WorkerHeartbeat(
        worker_id=worker_id,
        worker_type="contact_research",
        interval_seconds=heartbeat_interval,
        metadata={"lease_seconds": lease_seconds}
    )
    heartbeat.start()

    # Optional guardrail: after a bounded number of loops, if a specific run
    # (RUN_FILTER_ID) still has a contact gap, surface a message for the user
    # instead of silently looping forever in automated environments.
    run_filter_id = os.getenv("RUN_FILTER_ID", "").strip()

    max_loops_env = os.getenv("WORKER_MAX_LOOPS")
    try:
        max_loops = int(max_loops_env) if max_loops_env is not None else None
    except ValueError:
        max_loops = None
    if max_loops is None and run_filter_id:
        max_loops = 3

    loops = 0
    try:
        while True:
            claimed = process_contact_gap(agent, worker_id, lease_seconds, heartbeat)
            if not claimed:
                logger.info("No contact gaps available; sleeping %s seconds", idle_sleep)
                heartbeat.mark_idle()
                time.sleep(idle_sleep)
            loops += 1
            if max_loops is not None and loops >= max_loops:
                logger.info(
                    "WORKER_MAX_LOOPS=%s reached in contact_research_runner; exiting after %s loop(s)",
                    max_loops,
                    loops,
                )
                # Before exiting, if there is still a gap, mark it as needs_user_decision
                target_run_id = run_filter_id or (claimed and claimed.get("run_id")) or ""
                if target_run_id:
                    gap = supabase_client.get_contact_gap_summary(target_run_id)
                    total_gap = int(gap.get("contacts_min_gap_total") or 0) if gap else 0
                    if total_gap > 0:
                        run = supabase_client.get_pm_run(target_run_id) or {}
                        criteria = run.get("criteria") or {}
                        city = criteria.get("city") or ""
                        state = criteria.get("state") or ""
                        pms = criteria.get("pms") or ""
                        quantity = criteria.get("quantity") or run.get("target_quantity")

                        msg = (
                            "Good news: we were able to find some matching accounts, "
                            "but we could not fully satisfy your contact requirements "
                            "after the allotted attempts.\n\n"
                            f"- Run id: {target_run_id}\n"
                            f"- Criteria: city={city!r} state={state!r} pms={pms!r} quantity={quantity}\n"
                            f"- Remaining contact gap (min contacts not yet filled): {total_gap}\n\n"
                            "To proceed, please choose one of the following options:\n"
                            "1) Expand the location requirements (broaden city/region).\n"
                            "2) Loosen the PMS requirements (allow additional PMS vendors).\n"
                            "3) Accept the partial results and complete the task with the accounts found so far.\n"
                        )

                        supabase_client.update_pm_run_status(
                            run_id=target_run_id,
                            status="needs_user_decision",
                            error=msg,
                        )
                        send_run_notification(
                            run_id=target_run_id,
                            subject="Lead list run needs your decision",
                            body=msg,
                        )
                        logger.info(
                            "Run %s still has contact gap=%s after %s loop(s); "
                            "marked as needs_user_decision and sent notification",
                            target_run_id,
                            total_gap,
                            loops,
                        )
                break
    finally:
        # Ensure heartbeat is stopped on exit
        heartbeat.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
