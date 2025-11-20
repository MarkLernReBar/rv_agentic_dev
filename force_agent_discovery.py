#!/usr/bin/env python3
"""Clear seeding and force agent MCP discovery for test run."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment
load_dotenv('.env.local')

# Add src to path
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from rv_agentic.services.supabase_client import _pg_conn

run_id = "5ddfb5dc-5bf8-42e7-a0e9-60310bc9563d"

print(f"Forcing agent discovery for run: {run_id}")
print("=" * 80)

with _pg_conn() as conn:
    with conn.cursor() as cur:
        # Clear seeded companies
        cur.execute("""
            DELETE FROM pm_pipeline.company_candidates
            WHERE run_id = %s
            AND (discovery_source LIKE 'pms_subdomains%%'
                 OR discovery_source LIKE 'neo_pms%%')
        """, (run_id,))
        deleted = cur.rowcount
        print(f"\n🗑️  Deleted {deleted} seeded companies")

        # Reset run to active/company_discovery
        cur.execute("""
            UPDATE pm_pipeline.runs
            SET status = 'active',
                stage = 'company_discovery'
            WHERE id = %s
        """, (run_id,))
        print(f"✅  Reset run to active/company_discovery")

        print(f"\n📝 Next steps:")
        print(f"   Worker will pick up this run and use agent MCP discovery")
        print(f"   Monitor: tail -f .lead_list_worker.log | grep -E '(fetch_page|Completed|companies found)'")

print("\n" + "=" * 80)
