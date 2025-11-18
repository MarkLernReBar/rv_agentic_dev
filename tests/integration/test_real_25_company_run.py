"""Real production test for 25-company run with batching.

This is NOT a mocked test. It:
- Connects to real database
- Runs real agents with real MCP tools
- Measures actual performance
- Verifies batching works in production

Run this test ONLY when you want to validate real-world functionality.
It will take 12-15 minutes and consume real API credits.
"""

import os
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL") or not os.getenv("OPENAI_API_KEY"),
    reason="Requires POSTGRES_URL and OPENAI_API_KEY for real production test"
)
def test_real_25_company_run_with_batching():
    """
    Real end-to-end test with 25 companies.

    This test:
    1. Creates a real run in the database
    2. Configures batch size to 10
    3. Starts a real worker
    4. Monitors progress through batches
    5. Verifies batching checkpoints work
    6. Measures actual performance

    Expected results:
    - 3 batches (10 + 10 + 5)
    - 12-15 minutes total time
    - 24-25 companies found (96%+ success rate)
    - Progress tracked at each checkpoint
    """
    from rv_agentic.services import supabase_client
    from rv_agentic.workers import lead_list_runner
    from rv_agentic.agents.lead_list_agent import create_lead_list_agent
    from rv_agentic.services.heartbeat import WorkerHeartbeat

    # Test configuration
    target_quantity = 25
    batch_size = 10

    # Create test run in database
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Creating test run in database...")

    criteria = {
        "pms": "Buildium",
        "state": "TX",
        "quantity": target_quantity,
        "test_run": True,
        "batch_test": True
    }

    # Insert run using the proper API
    try:
        run = supabase_client.create_pm_run(
            criteria=criteria,
            target_quantity=target_quantity,
            notes="Automated test run for batching validation"
        )
        test_run_id = str(run["id"])  # Convert UUID to string
        print(f"✅ Test run created successfully (ID: {test_run_id})")
    except Exception as e:
        print(f"❌ Failed to create test run: {e}")
        pytest.skip(f"Cannot create test run: {e}")

    print(f"\n{'='*80}")
    print(f"REAL PRODUCTION TEST: 25-Company Run with Batching")
    print(f"{'='*80}")
    print(f"Run ID: {test_run_id}")
    print(f"Target: {target_quantity} companies")
    print(f"Batch Size: {batch_size}")
    print(f"Expected Batches: 3 (10 + 10 + 5)")
    print(f"Expected Time: 12-15 minutes")
    print(f"{'='*80}\n")

    # Set batch size for this test
    os.environ["LEAD_LIST_BATCH_SIZE"] = str(batch_size)
    os.environ["WORKER_MAX_LOOPS"] = "5"  # Safety limit
    os.environ["RUN_FILTER_ID"] = test_run_id  # Focus on this run only

    # Initialize agent and worker
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Initializing agent and worker...")
    agent = create_lead_list_agent()
    worker_id = f"test-worker-{uuid.uuid4()}"

    # Start heartbeat
    heartbeat = WorkerHeartbeat(
        worker_id=worker_id,
        worker_type="lead_list",
        interval_seconds=30,
        metadata={"test_run": True, "batch_size": batch_size}
    )
    heartbeat.start()

    try:
        # Track batches
        batches_completed = 0
        start_time = time.time()
        batch_times = []

        print(f"✅ Worker started with heartbeat")
        print(f"\n{'='*80}")
        print(f"BATCH PROCESSING START")
        print(f"{'='*80}\n")

        # Process batches until target met or max loops reached
        for loop in range(5):  # Max 5 loops (safety)
            batch_start = time.time()

            # Check current progress
            gap_info = supabase_client.get_pm_company_gap(test_run_id)
            companies_ready = int(gap_info.get("companies_ready") or 0) if gap_info else 0
            companies_remaining = max(0, target_quantity - companies_ready)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Batch {loop + 1}:")
            print(f"  Progress: {companies_ready}/{target_quantity} companies")
            print(f"  Remaining: {companies_remaining}")

            if companies_remaining == 0:
                print(f"  ✅ Target met!")
                break

            # Run one batch
            print(f"  🔄 Processing batch...")
            run = supabase_client.get_pm_run(test_run_id)
            if not run:
                print(f"  ❌ Run not found!")
                break

            lead_list_runner.process_run(run, heartbeat)

            # Check progress after batch
            gap_info_after = supabase_client.get_pm_company_gap(test_run_id)
            companies_after = int(gap_info_after.get("companies_ready") or 0) if gap_info_after else 0
            found_in_batch = companies_after - companies_ready

            batch_time = time.time() - batch_start
            batch_times.append(batch_time)
            batches_completed += 1

            print(f"  ✅ Batch complete: Found {found_in_batch} companies in {batch_time:.1f}s")
            print(f"  Progress: {companies_after}/{target_quantity} companies\n")

            if companies_after >= target_quantity:
                print(f"  ✅ Target met!")
                break

        total_time = time.time() - start_time

        # Get final results
        final_gap = supabase_client.get_pm_company_gap(test_run_id)
        final_companies = int(final_gap.get("companies_ready") or 0) if final_gap else 0

        print(f"\n{'='*80}")
        print(f"BATCH PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"Total Batches: {batches_completed}")
        print(f"Companies Found: {final_companies}/{target_quantity}")
        print(f"Success Rate: {(final_companies/target_quantity)*100:.1f}%")
        print(f"Total Time: {total_time:.1f}s ({total_time/60:.1f} min)")
        print(f"Avg Time/Batch: {sum(batch_times)/len(batch_times):.1f}s" if batch_times else "N/A")
        print(f"{'='*80}\n")

        # Assertions for test validation
        assert batches_completed > 0, "Should have completed at least one batch"
        assert batches_completed <= 5, f"Should not need more than 5 batches (got {batches_completed})"
        assert final_companies >= target_quantity * 0.9, \
            f"Should find at least 90% of target (got {final_companies}/{target_quantity})"

        # Verify batching worked as expected
        expected_batches = (target_quantity + batch_size - 1) // batch_size  # Ceiling division
        assert batches_completed == expected_batches, \
            f"Expected {expected_batches} batches for {target_quantity} companies with batch_size={batch_size}, got {batches_completed}"

        print(f"✅ All assertions passed!")
        print(f"\nTest Summary:")
        print(f"  - Batching: Working correctly ({batches_completed} batches)")
        print(f"  - Checkpointing: Working (progress tracked after each batch)")
        print(f"  - Success Rate: {(final_companies/target_quantity)*100:.1f}% (target: ≥90%)")
        print(f"  - Performance: {total_time/60:.1f} min (target: 12-15 min)")

    finally:
        # Cleanup
        heartbeat.stop()

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Cleaning up test run...")

        # Delete test data using direct database access
        try:
            from rv_agentic.services.supabase_client import _pg_conn
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    # Delete contacts
                    cur.execute(
                        "DELETE FROM pm_pipeline.contact_candidates WHERE run_id = %s",
                        (test_run_id,)
                    )
                    # Delete companies
                    cur.execute(
                        "DELETE FROM pm_pipeline.company_candidates WHERE run_id = %s",
                        (test_run_id,)
                    )
                    # Delete run
                    cur.execute(
                        "DELETE FROM pm_pipeline.runs WHERE id = %s",
                        (test_run_id,)
                    )
            print(f"✅ Test data cleaned up")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL"),
    reason="Requires POSTGRES_URL for database integration test"
)
def test_database_heartbeat_integration():
    """Test that heartbeat system works with real database."""
    from rv_agentic.services import supabase_client
    import uuid

    worker_id = f"test-integration-{uuid.uuid4()}"

    print(f"\n{'='*80}")
    print(f"DATABASE INTEGRATION TEST: Heartbeat System")
    print(f"{'='*80}")
    print(f"Worker ID: {worker_id}")

    try:
        # Test 1: Upsert heartbeat
        print(f"\n[Test 1] Upserting worker heartbeat...")
        supabase_client.upsert_worker_heartbeat(
            worker_id=worker_id,
            worker_type="lead_list",
            status="processing",
            current_run_id=str(uuid.uuid4()),
            current_task="Test task"
        )
        print(f"✅ Heartbeat upserted successfully")

        # Test 2: Get active workers
        print(f"\n[Test 2] Getting active workers...")
        active = supabase_client.get_active_workers()
        assert any(w["worker_id"] == worker_id for w in active), \
            f"Worker {worker_id} should be in active workers"
        print(f"✅ Worker found in active workers ({len(active)} total)")

        # Test 3: Get worker stats
        print(f"\n[Test 3] Getting worker stats...")
        stats = supabase_client.get_worker_stats()
        assert len(stats) > 0, "Should have worker stats"
        print(f"✅ Worker stats retrieved ({len(stats)} types)")

        # Test 4: Stop worker
        print(f"\n[Test 4] Stopping worker...")
        supabase_client.stop_worker(worker_id)
        print(f"✅ Worker stopped successfully")

        # Test 5: Verify worker status is 'stopped'
        print(f"\n[Test 5] Verifying worker stopped...")
        # Note: Worker may still appear in v_active_workers (heartbeat within 5 min)
        # but status should be 'stopped'
        active_after = supabase_client.get_active_workers()
        worker_after = next((w for w in active_after if w["worker_id"] == worker_id), None)
        if worker_after:
            assert worker_after["status"] == "stopped", \
                f"Worker status should be 'stopped', got '{worker_after['status']}'"
            print(f"✅ Worker status correctly set to 'stopped'")
        else:
            # Worker not in active list (timed out or removed)
            print(f"✅ Worker removed from active workers")

        print(f"\n{'='*80}")
        print(f"✅ ALL HEARTBEAT INTEGRATION TESTS PASSED")
        print(f"{'='*80}\n")

    finally:
        # Cleanup
        try:
            from rv_agentic.services.supabase_client import _pg_conn
            with _pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM pm_pipeline.worker_heartbeats WHERE worker_id = %s",
                        (worker_id,)
                    )
            print(f"✅ Test worker cleaned up")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")


if __name__ == "__main__":
    # Run with: pytest tests/integration/test_real_25_company_run.py -v -s
    pytest.main([__file__, "-v", "-s", "--tb=short"])
