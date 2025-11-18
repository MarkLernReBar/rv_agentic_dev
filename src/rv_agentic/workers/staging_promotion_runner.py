"""Worker/CLI helper to promote PMS-qualified staging companies into pm_pipeline runs.

This is intentionally simple and driven entirely by environment variables so it can
be used for targeted testing or wired into external orchestrators:

- STAGING_SEARCH_RUN_ID: uuid from pm_pipeline.search_runs.id
- STAGING_PM_RUN_ID: uuid from pm_pipeline.runs.id
- STAGING_PMS_REQUIRED: optional PMS filter (e.g. "Buildium")
- STAGING_MIN_PMS_CONFIDENCE: optional float threshold (default 0.7)
- STAGING_MAX_COMPANIES: optional int cap on companies to promote
"""

from __future__ import annotations

import logging
import os

from rv_agentic.config.settings import get_settings
from rv_agentic.services import supabase_client
from rv_agentic.workers.utils import load_env_files

logger = logging.getLogger(__name__)


def _ensure_env_loaded() -> None:
    load_env_files()
    # Ensure OPENAI_API_KEY is available if anything upstream ever needs it,
    # though this worker itself does not call the model.
    settings = get_settings()
    if settings.openai_api_key and "OPENAI_API_KEY" not in os.environ:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _ensure_env_loaded()

    search_run_id = os.getenv("STAGING_SEARCH_RUN_ID", "").strip()
    pm_run_id = os.getenv("STAGING_PM_RUN_ID", "").strip()
    pms_required = os.getenv("STAGING_PMS_REQUIRED", "").strip() or None

    min_conf_raw = os.getenv("STAGING_MIN_PMS_CONFIDENCE", "").strip()
    max_companies_raw = os.getenv("STAGING_MAX_COMPANIES", "").strip()
    min_conf = None
    max_companies = None
    try:
        if min_conf_raw:
            min_conf = float(min_conf_raw)
    except ValueError:
        logger.warning("Invalid STAGING_MIN_PMS_CONFIDENCE=%s; ignoring", min_conf_raw)
        min_conf = None
    try:
        if max_companies_raw:
            max_companies = int(max_companies_raw)
    except ValueError:
        logger.warning("Invalid STAGING_MAX_COMPANIES=%s; ignoring", max_companies_raw)
        max_companies = None

    if not search_run_id or not pm_run_id:
        logger.error(
            "Both STAGING_SEARCH_RUN_ID and STAGING_PM_RUN_ID must be set. "
            "Got search_run_id=%r pm_run_id=%r",
            search_run_id,
            pm_run_id,
        )
        raise SystemExit(1)

    logger.info(
        "Promoting staging companies search_run_id=%s -> pm_run_id=%s pms_required=%s "
        "min_confidence=%s max_companies=%s",
        search_run_id,
        pm_run_id,
        pms_required,
        min_conf,
        max_companies,
    )

    inserted = supabase_client.promote_staging_companies_to_run(
        search_run_id=search_run_id,
        pm_run_id=pm_run_id,
        pms_required=pms_required,
        min_pms_confidence=min_conf if min_conf is not None else 0.7,
        max_companies=max_companies,
    )
    logger.info("Promoted %s staging company(ies) into pm_pipeline.company_candidates", inserted)

    # Optional: log the updated company gap for visibility.
    gap = supabase_client.get_pm_company_gap(pm_run_id)
    if gap:
        logger.info(
            "Run %s company gap: target=%s ready=%s gap=%s",
            pm_run_id,
            gap.get("target_quantity"),
            gap.get("companies_ready"),
            gap.get("companies_gap"),
        )
        try:
            gap_val = int(gap.get("companies_gap") or 0)
        except (TypeError, ValueError):
            gap_val = 0
        if gap_val <= 0:
            run = supabase_client.get_pm_run(pm_run_id) or {}
            stage = (run.get("stage") or "").strip()
            if stage == "company_discovery":
                supabase_client.set_run_stage(run_id=pm_run_id, stage="company_research")
                logger.info(
                    "Advanced run %s stage company_discovery -> company_research "
                    "after closing company gap via staging promotion",
                    pm_run_id,
                )


if __name__ == "__main__":  # pragma: no cover
    main()
