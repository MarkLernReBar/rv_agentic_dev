"""Timing and performance tests for Phase 1.

These tests measure actual performance with mock data to identify bottlenecks.
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from dotenv import load_dotenv
env_file = ROOT / ".env.local"
if env_file.exists():
    load_dotenv(env_file)

from rv_agentic.services import supabase_client
from integration.test_phase1_pipeline import MockWorkerSimulator


def test_timing_5_companies():
    """Measure timing for 5 company pipeline."""
    # Create run
    run = supabase_client.create_pm_run(
        criteria={"pms": "Buildium", "state": "TX"},
        target_quantity=5,
    )
    run_id = run["id"]
    print(f"\n=== TIMING TEST: 5 Companies ===")
    print(f"Run ID: {run_id}")

    simulator = MockWorkerSimulator(run_id)

    # Time company discovery
    start = time.time()
    simulator.simulate_company_discovery(num_companies=5)
    discovery_time = time.time() - start
    print(f"✅ Company Discovery: {discovery_time:.2f}s")

    # Time company research
    start = time.time()
    simulator.simulate_company_research()
    research_time = time.time() - start
    print(f"✅ Company Research: {research_time:.2f}s")

    # Time contact discovery
    start = time.time()
    simulator.simulate_contact_discovery(contacts_per_company=2)
    contact_time = time.time() - start
    print(f"✅ Contact Discovery: {contact_time:.2f}s")

    total_time = discovery_time + research_time + contact_time
    print(f"\n📊 Total Time: {total_time:.2f}s")
    print(f"   - Discovery: {discovery_time:.2f}s ({discovery_time/total_time*100:.1f}%)")
    print(f"   - Research:  {research_time:.2f}s ({research_time/total_time*100:.1f}%)")
    print(f"   - Contacts:  {contact_time:.2f}s ({contact_time/total_time*100:.1f}%)")

    # Verify completion
    run_check = supabase_client.get_pm_run(run_id)
    assert run_check["status"] == "completed"
    assert run_check["stage"] == "done"

    # Performance assertions (these are baseline expectations for mock)
    assert total_time < 30, f"Total time {total_time:.2f}s exceeded 30s threshold"
    assert discovery_time < 10, f"Discovery {discovery_time:.2f}s exceeded 10s threshold"
    assert research_time < 10, f"Research {research_time:.2f}s exceeded 10s threshold"
    assert contact_time < 15, f"Contacts {contact_time:.2f}s exceeded 15s threshold"

    print("\n✅ All timing thresholds met!")


def test_timing_10_companies():
    """Measure timing for 10 company pipeline."""
    run = supabase_client.create_pm_run(
        criteria={"pms": "Buildium", "state": "TX"},
        target_quantity=10,
    )
    run_id = run["id"]
    print(f"\n=== TIMING TEST: 10 Companies ===")
    print(f"Run ID: {run_id}")

    simulator = MockWorkerSimulator(run_id)

    start_total = time.time()

    # Discovery
    start = time.time()
    simulator.simulate_company_discovery(num_companies=10)
    discovery_time = time.time() - start

    # Research
    start = time.time()
    simulator.simulate_company_research()
    research_time = time.time() - start

    # Contacts
    start = time.time()
    simulator.simulate_contact_discovery(contacts_per_company=2)
    contact_time = time.time() - start

    total_time = time.time() - start_total

    print(f"\n📊 Total Time: {total_time:.2f}s")
    print(f"   - Discovery: {discovery_time:.2f}s")
    print(f"   - Research:  {research_time:.2f}s")
    print(f"   - Contacts:  {contact_time:.2f}s")

    # Calculate rate
    companies_per_second = 10 / total_time
    print(f"\n⚡ Rate: {companies_per_second:.2f} companies/second")
    print(f"   Estimated 50 companies: {50/companies_per_second:.1f}s ({50/companies_per_second/60:.1f} min)")

    # Verify completion
    run_check = supabase_client.get_pm_run(run_id)
    assert run_check["status"] == "completed"


def test_db_operation_timing():
    """Measure individual DB operation timing."""
    run = supabase_client.create_pm_run(
        criteria={"pms": "Test"},
        target_quantity=1,
    )
    run_id = str(run["id"])

    print(f"\n=== DB OPERATION TIMING ===")

    # Measure company insert
    start = time.time()
    supabase_client.insert_company_candidate(
        run_id=run_id,
        name="Timing Test Co",
        website="https://timing-test.com",
        domain=f"timing-test-{run_id[:8]}.com",
        state="TX",
    )
    company_insert_time = time.time() - start
    print(f"Company Insert: {company_insert_time*1000:.1f}ms")

    # Get company
    companies = supabase_client._get_pm(  # type: ignore
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {"run_id": f"eq.{run_id}", "select": "*"},
    )
    company_id = companies[0]["id"]

    # Measure contact insert
    start = time.time()
    supabase_client.insert_contact_candidate(
        run_id=run_id,
        company_id=company_id,
        full_name="Timing Test",
        email="timing@test.com",
        idem_key=f"timing-{run_id[:8]}",
    )
    contact_insert_time = time.time() - start
    print(f"Contact Insert: {contact_insert_time*1000:.1f}ms")

    # Measure progress query
    from rv_agentic import orchestrator
    start = time.time()
    progress = orchestrator.get_run_progress(run_id)
    progress_time = time.time() - start
    print(f"Progress Query: {progress_time*1000:.1f}ms")

    # Extrapolate for 50 companies
    print(f"\n📊 Extrapolation for 50 companies:")
    print(f"   - Company inserts: {50 * company_insert_time:.2f}s")
    print(f"   - Contact inserts (100): {100 * contact_insert_time:.2f}s")
    print(f"   - Total DB overhead: {(50 * company_insert_time + 100 * contact_insert_time):.2f}s")


if __name__ == "__main__":
    """Run timing tests directly."""
    import pytest
    pytest.main([__file__, "-v", "-s"])
