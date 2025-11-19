"""End-to-end orchestrator for lead list pipeline.

Coordinates the full pipeline: run creation → discovery → research →
contact discovery → CSV export.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from rv_agentic.services import supabase_client, export, notifications


logger = logging.getLogger(__name__)


class PipelineTimeoutError(Exception):
    """Raised when pipeline exceeds maximum allowed time."""
    pass


class PipelineError(Exception):
    """Raised when pipeline encounters an unrecoverable error."""
    pass


def wait_for_stage_completion(
    run_id: str,
    expected_stage: str,
    timeout_seconds: int = 3600,
    poll_interval: int = 10,
) -> Dict[str, Any]:
    """Poll a run until it advances past the expected stage or times out.

    Args:
        run_id: pm_pipeline.runs.id
        expected_stage: Stage we're waiting to complete
        timeout_seconds: Maximum time to wait
        poll_interval: Seconds between polls

    Returns:
        Run dict after stage completion

    Raises:
        PipelineTimeoutError if timeout exceeded
        PipelineError if run enters error state
    """
    start_time = time.time()
    last_log_time = start_time

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise PipelineTimeoutError(
                f"Run {run_id} did not complete stage '{expected_stage}' "
                f"within {timeout_seconds}s"
            )

        run = supabase_client.get_pm_run(run_id)
        if not run:
            raise PipelineError(f"Run {run_id} not found")

        status = run.get("status")
        stage = run.get("stage")

        # Log progress every minute
        if (time.time() - last_log_time) >= 60:
            logger.info(
                "Run %s: stage=%s status=%s (waiting for %s to complete, elapsed=%ds)",
                run_id,
                stage,
                status,
                expected_stage,
                int(elapsed),
            )
            last_log_time = time.time()

        # Check for error states
        if status == "error":
            notes = run.get("notes") or "Unknown error"
            raise PipelineError(f"Run {run_id} entered error state: {notes}")

        if status == "needs_user_decision":
            notes = run.get("notes") or "User decision required"
            raise PipelineError(
                f"Run {run_id} requires user decision: {notes}"
            )

        # Check if stage has advanced
        if stage != expected_stage:
            logger.info(
                "Run %s advanced from stage '%s' to '%s'",
                run_id,
                expected_stage,
                stage,
            )
            return run

        # Check if run is complete
        if stage == "done" or status == "completed":
            logger.info("Run %s completed", run_id)
            return run

        time.sleep(poll_interval)


def execute_full_pipeline(
    criteria: Dict[str, Any],
    target_quantity: int,
    contacts_min: int = 1,
    contacts_max: int = 3,
    output_dir: Optional[str] = None,
    timeout_per_stage: int = 3600,
    notify_email: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Execute the complete lead list pipeline from creation to CSV export.

    This function:
    1. Creates a pm_pipeline.run
    2. Waits for workers to complete company_discovery stage
    3. Waits for workers to complete company_research stage
    4. Waits for workers to complete contact_discovery stage
    5. Exports companies and contacts to CSV

    Args:
        criteria: Search criteria dict (pms, city, state, etc.)
        target_quantity: Number of companies to find
        contacts_min: Minimum contacts per company (default 1)
        contacts_max: Maximum contacts per company (default 3)
        output_dir: Directory for CSV files (defaults to current directory)
        timeout_per_stage: Max seconds to wait for each stage
        notify_email: Optional email for completion notification

    Returns:
        Tuple of (run_id, companies_csv_path, contacts_csv_path)

    Raises:
        PipelineTimeoutError if any stage times out
        PipelineError if pipeline encounters unrecoverable error
    """
    logger.info(
        "Starting full pipeline: criteria=%s target_qty=%d contacts=%d-%d",
        criteria,
        target_quantity,
        contacts_min,
        contacts_max,
    )

    # Step 1: Create run
    run = supabase_client.create_pm_run(
        criteria=criteria,
        target_quantity=target_quantity,
        contacts_min=contacts_min,
        contacts_max=contacts_max,
    )
    run_id = run.get("id")
    if not run_id:
        raise PipelineError("Failed to create run - no ID returned")

    logger.info("Created run %s", run_id)

    try:
        # Step 2: Wait for company discovery to complete
        logger.info("Waiting for company_discovery stage...")
        wait_for_stage_completion(
            run_id,
            expected_stage="company_discovery",
            timeout_seconds=timeout_per_stage,
        )

        # Step 3: Wait for company research to complete
        logger.info("Waiting for company_research stage...")
        wait_for_stage_completion(
            run_id,
            expected_stage="company_research",
            timeout_seconds=timeout_per_stage,
        )

        # Step 4: Wait for contact discovery to complete
        logger.info("Waiting for contact_discovery stage...")
        final_run = wait_for_stage_completion(
            run_id,
            expected_stage="contact_discovery",
            timeout_seconds=timeout_per_stage,
        )

        # Verify run reached completion
        if final_run.get("status") != "completed":
            raise PipelineError(
                f"Run {run_id} finished but status is '{final_run.get('status')}' "
                f"instead of 'completed'"
            )

        # Step 5: Export to CSV
        logger.info("Exporting run %s to CSV...", run_id)
        output_directory = output_dir or os.getcwd()
        companies_path, contacts_path = export.export_run_to_files(
            run_id, output_directory
        )

        logger.info(
            "Pipeline complete for run %s: companies=%s contacts=%s",
            run_id,
            companies_path,
            contacts_path,
        )

        # Send notification if email provided
        if notify_email:
            try:
                # Best-effort: attach CSV contents directly to email so the
                # recipient can download them without needing filesystem access.
                attachments = []
                try:
                    with open(companies_path, "rb") as f:
                        attachments.append(
                            (os.path.basename(companies_path), f.read(), "text/csv")
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to read companies CSV for email attachment (run %s): %s",
                        run_id,
                        exc,
                    )
                try:
                    with open(contacts_path, "rb") as f:
                        attachments.append(
                            (os.path.basename(contacts_path), f.read(), "text/csv")
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to read contacts CSV for email attachment (run %s): %s",
                        run_id,
                        exc,
                    )

                notifications.send_run_notification(
                    run_id=run_id,
                    subject=f"Lead list run {run_id[:8]} completed",
                    body=(
                        f"Your lead list request has completed successfully.\n\n"
                        f"Run ID: {run_id}\n"
                        f"Companies CSV: {companies_path}\n"
                        f"Contacts CSV: {contacts_path}\n"
                        "\nThe CSV files are attached to this email when possible."
                    ),
                    to_email=notify_email,
                    attachments=attachments or None,
                )
            except Exception as e:
                logger.warning("Failed to send completion notification: %s", e)

        return (run_id, companies_path, contacts_path)

    except (PipelineTimeoutError, PipelineError) as e:
        logger.error("Pipeline failed for run %s: %s", run_id, e)
        # Mark run as error if not already
        try:
            supabase_client.update_pm_run_status(
                run_id=run_id,
                status="error",
                error=f"Orchestrator error: {str(e)}",
            )
        except Exception:
            pass
        raise


def get_run_progress(run_id: str) -> Dict[str, Any]:
    """Get detailed progress information for a run.

    Args:
        run_id: pm_pipeline.runs.id

    Returns:
        Dict with run metadata, stage, status, and gap information
    """
    run = supabase_client.get_pm_run(run_id)
    if not run:
        return {"error": "Run not found"}

    # Get gap views
    company_gap = supabase_client.get_pm_company_gap(run_id) or {}
    contact_gap = supabase_client.get_contact_gap_summary(run_id) or {}

    # Calculate progress percentages
    target_qty = int(run.get("target_quantity") or 0)
    companies_ready = int(company_gap.get("companies_ready") or 0)
    companies_gap = int(company_gap.get("companies_gap") or 0)

    company_progress_pct = 0
    if target_qty > 0:
        company_progress_pct = int((companies_ready / target_qty) * 100)

    contacts_min = int(run.get("contacts_min") or 1)
    contacts_ready_total = int(contact_gap.get("contacts_min_ready_total") or 0)
    contacts_min_gap_total = int(contact_gap.get("contacts_min_gap_total") or 0)
    contacts_target_total = companies_ready * contacts_min

    contact_progress_pct = 0
    if contacts_target_total > 0:
        contact_progress_pct = int((contacts_ready_total / contacts_target_total) * 100)

    return {
        "run_id": run_id,
        "stage": run.get("stage"),
        "status": run.get("status"),
        "criteria": run.get("criteria"),
        "target_quantity": target_qty,
        "companies": {
            "ready": companies_ready,
            "gap": companies_gap,
            "progress_pct": company_progress_pct,
        },
        "contacts": {
            "ready": contacts_ready_total,
            "gap": contacts_min_gap_total,
            "progress_pct": contact_progress_pct,
        },
        "created_at": run.get("created_at"),
        "notes": run.get("notes"),
    }


if __name__ == "__main__":
    """CLI entry point for orchestrator."""
    import argparse
    import json
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Execute full lead list pipeline")
    parser.add_argument("--criteria", required=True, help="JSON criteria string")
    parser.add_argument("--quantity", type=int, required=True, help="Target quantity")
    parser.add_argument("--contacts-min", type=int, default=1, help="Min contacts per company")
    parser.add_argument("--contacts-max", type=int, default=3, help="Max contacts per company")
    parser.add_argument("--output-dir", default=None, help="Output directory for CSV files")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout per stage (seconds)")
    parser.add_argument("--notify-email", default=None, help="Email for completion notification")

    args = parser.parse_args()

    try:
        criteria = json.loads(args.criteria)
    except json.JSONDecodeError as e:
        print(f"Error parsing criteria JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        run_id, companies_csv, contacts_csv = execute_full_pipeline(
            criteria=criteria,
            target_quantity=args.quantity,
            contacts_min=args.contacts_min,
            contacts_max=args.contacts_max,
            output_dir=args.output_dir,
            timeout_per_stage=args.timeout,
            notify_email=args.notify_email,
        )
        print(f"SUCCESS: Run {run_id}")
        print(f"Companies CSV: {companies_csv}")
        print(f"Contacts CSV: {contacts_csv}")
        sys.exit(0)
    except (PipelineTimeoutError, PipelineError) as e:
        print(f"PIPELINE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        logger.exception("Unexpected error in orchestrator")
        sys.exit(1)
