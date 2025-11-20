#!/usr/bin/env python3
"""Check results of the test run."""

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

print(f"Checking results for run: {run_id}")
print("=" * 80)

with _pg_conn() as conn:
    with conn.cursor() as cur:
        # Count companies
        cur.execute("""
            SELECT COUNT(*),
                   COUNT(DISTINCT discovery_source) as sources
            FROM pm_pipeline.company_candidates
            WHERE run_id = %s
        """, (run_id,))
        company_count, source_count = cur.fetchone()

        print(f"\n📊 Companies Found: {company_count}")
        print(f"   Discovery Sources: {source_count}")

        # Get sample companies
        cur.execute("""
            SELECT domain, discovery_source
            FROM pm_pipeline.company_candidates
            WHERE run_id = %s
            LIMIT 5
        """, (run_id,))
        companies = cur.fetchall()

        print(f"\n📋 Sample Companies:")
        for domain, source in companies:
            print(f"   - {domain} (source: {source})")

        # Check for agent_structured source (the fix we're testing)
        cur.execute("""
            SELECT COUNT(*)
            FROM pm_pipeline.company_candidates
            WHERE run_id = %s
            AND discovery_source LIKE 'agent_structured%'
        """, (run_id,))
        agent_count = cur.fetchone()[0]

        print(f"\n✅ FIX VALIDATION:")
        print(f"   Agent-discovered companies: {agent_count}")

        if agent_count > 0:
            print(f"   🎉 SUCCESS! Agent populated structured output correctly!")
        else:
            print(f"   ⚠️  No agent_structured companies - may have used seeding")

print("\n" + "=" * 80)
