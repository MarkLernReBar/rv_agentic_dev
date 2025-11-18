#!/usr/bin/env python3
"""Test multi-region discovery with Denver, CO."""

import sys
sys.path.insert(0, "src")

from rv_agentic.services import supabase_client

# Create test run
criteria = {
    "quantity": 20,
    "cities": ["Denver"],
    "state": "CO",
    "geo_markets": ["CO"],
    "units_min": 99,
    "units_max": 50000,
    "pms": None,  # No PMS requirement
}

print(f"Criteria: {criteria}")
print("Creating test run...")

# Insert run
run = supabase_client.create_pm_run(
    criteria=criteria,
    target_quantity=20
)

run_id = run.get("id")
print(f"\n✅ Test run created: {run_id}")
print(f"\nRun the worker:")
print(f"RUN_FILTER_ID={run_id} WORKER_MAX_LOOPS=1 .venv/bin/python -m rv_agentic.workers.lead_list_runner 2>&1 | tee test_multi_region_denver.log")
print(f"\nExpected results:")
print(f"- 4 sequential region calls:")
print(f"  1. Downtown Denver & LoDo")
print(f"  2. North Denver")
print(f"  3. South Denver")
print(f"  4. West/East Metro")
print(f"- Each region: 8-12 companies")
print(f"- Total: 32-40 companies discovered")
print(f"- After dedup: ~32-40 companies")
