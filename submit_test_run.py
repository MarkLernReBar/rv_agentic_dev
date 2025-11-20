#!/usr/bin/env python3
"""Submit a minimal test run to validate structured output fix."""

import os
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load environment
env_file = Path(__file__).parent / ".env.local"
if env_file.exists():
    load_dotenv(env_file)

# Add src to path
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from rv_agentic.services.supabase_client import _pg_conn
import json


def submit_test_run():
    """Submit minimal test run for structured output validation."""

    print("Submitting minimal test run...")
    print("Criteria: San Francisco, Buildium, 3 companies")

    # Create minimal test run
    run_id = str(uuid.uuid4())

    target_qty = 3
    criteria = {
        "geo_markets": ["CA"],
        "cities": ["San Francisco"],
        "pms": "Buildium",
    }

    try:
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pm_pipeline.runs (
                        id, status, stage, created_at,
                        criteria, target_quantity, contacts_min, contacts_max,
                        notes
                    )
                    VALUES (
                        %s, 'active', 'company_discovery', NOW(),
                        %s::jsonb, %s, 1, 2,
                        'STRUCTURED_OUTPUT_FIX_TEST: San Francisco, Buildium, 3 companies'
                    )
                """, (run_id, json.dumps(criteria), target_qty))

        print(f"\n✅ Test run submitted successfully!")
        print(f"   Run ID: {run_id}")
        print(f"   Target: {target_qty} companies")
        print(f"   Location: San Francisco, CA")
        print(f"   PMS: Buildium")
        print(f"\nWorker will process this run and we'll validate structured output.")
        print(f"\nMonitor with:")
        print(f"   tail -f .lead_list_worker.log | grep -E '(Completed|companies found|ERROR)'")

        return run_id

    except Exception as e:
        print(f"\n❌ Failed to submit run: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    run_id = submit_test_run()
    sys.exit(0 if run_id else 1)
