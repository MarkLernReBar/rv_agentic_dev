#!/usr/bin/env python3
"""Debug script to check recent lead list runs and their status."""

import os
import sys
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment
load_dotenv('.env.local')

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from rv_agentic.services import supabase_client

def main():
    print("=" * 80)
    print("RECENT LEAD LIST RUNS")
    print("=" * 80)

    # Get recent runs using get_active_and_recent_runs
    recent_runs = supabase_client.get_active_and_recent_runs(limit=10)

    if not recent_runs:
        print("\n❌ No runs found in the database")
        return

    print(f"\n🔍 Found {len(recent_runs)} runs")
    print(f"🔍 First run keys: {list(recent_runs[0].keys()) if recent_runs else 'None'}")

    for run in recent_runs:
        run_id = run['id']
        print(f"\n📋 Run ID: {run_id}")
        print(f"   Stage: {run.get('stage', 'N/A')}")
        print(f"   Status: {run.get('status', 'N/A')}")
        print(f"   Created: {run.get('created_at', 'N/A')}")

        # Parse criteria if present
        criteria = run.get('criteria', {})
        if isinstance(criteria, str):
            import json
            try:
                criteria = json.loads(criteria)
            except:
                criteria = {}

        print(f"   Target Quantity: {run.get('target_quantity', 'N/A')}")
        print(f"   PMS: {criteria.get('pms', 'N/A')}")
        print(f"   Geographic Regions: {criteria.get('geographic_regions', 'N/A')}")
        print(f"   Contacts Min/Max: {run.get('contacts_min', 'N/A')}/{run.get('contacts_max', 'N/A')}")

    # Check gap analysis for the most recent run
    most_recent_run_id = recent_runs[0]['id']
    print("\n" + "=" * 80)
    print(f"GAP ANALYSIS FOR MOST RECENT RUN: {most_recent_run_id}")
    print("=" * 80)

    # Company gap
    company_gap = supabase_client.get_pm_company_gap(most_recent_run_id)
    if company_gap:
        print(f"\n📊 Company Gap:")
        print(f"   Discovery Target: {company_gap.get('discovery_target', 'N/A')}")
        print(f"   Companies Discovered: {company_gap.get('companies_discovered', 'N/A')}")
        print(f"   Discovery Gap: {company_gap.get('discovery_gap', 'N/A')}")
        print(f"   Companies Ready: {company_gap.get('companies_ready', 'N/A')}")
        print(f"   Companies Gap: {company_gap.get('companies_gap', 'N/A')}")
    else:
        print("\n❌ No company gap data found")

    # Contact gap
    contact_gap = supabase_client.get_contact_gap_summary(most_recent_run_id)
    if contact_gap:
        print(f"\n👥 Contact Gap:")
        print(f"   Companies Ready: {contact_gap.get('companies_ready', 'N/A')}")
        print(f"   Contacts Min Gap Total: {contact_gap.get('contacts_min_gap_total', 'N/A')}")
        print(f"   Contacts Capacity Gap Total: {contact_gap.get('contacts_capacity_gap_total', 'N/A')}")
    else:
        print("\n❌ No contact gap data found")

    # Resume plan
    resume_plan = supabase_client.get_run_resume_plan(most_recent_run_id)
    if resume_plan:
        print(f"\n🔄 Resume Plan:")
        print(f"   Next Stage: {resume_plan.get('stage', 'N/A')}")
        print(f"   Discovery Gap: {resume_plan.get('discovery_gap', 'N/A')}")
        print(f"   Companies Gap: {resume_plan.get('companies_gap', 'N/A')}")
        print(f"   Contacts Min Gap: {resume_plan.get('contacts_min_gap_total', 'N/A')}")

    # Check worker status
    print("\n" + "=" * 80)
    print("WORKER STATUS")
    print("=" * 80)

    active_workers = supabase_client.get_active_workers()
    if active_workers:
        print(f"\n✅ Active Workers: {len(active_workers)}")
        for worker in active_workers:
            print(f"   - {worker.get('worker_id', 'N/A')} (Stage: {worker.get('stage', 'N/A')})")
    else:
        print("\n⚠️  No active workers found")

    dead_workers = supabase_client.get_dead_workers()
    if dead_workers:
        print(f"\n❌ Dead Workers: {len(dead_workers)}")
        for worker in dead_workers:
            print(f"   - {worker.get('worker_id', 'N/A')} (Stage: {worker.get('stage', 'N/A')})")

if __name__ == '__main__':
    main()
