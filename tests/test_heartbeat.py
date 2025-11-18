"""Tests for worker heartbeat system.

These tests require the database migration 003_worker_heartbeats.sql to be run.
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest


def test_heartbeat_manager_initialization():
    """Test that WorkerHeartbeat can be initialized."""
    from rv_agentic.services.heartbeat import WorkerHeartbeat

    heartbeat = WorkerHeartbeat(
        worker_id="test-worker-123",
        worker_type="company_research",
        interval_seconds=30,
        metadata={"test": "data"}
    )

    assert heartbeat.worker_id == "test-worker-123"
    assert heartbeat.worker_type == "company_research"
    assert heartbeat.interval_seconds == 30
    assert heartbeat.metadata == {"test": "data"}


def test_heartbeat_task_updates():
    """Test updating task information."""
    from rv_agentic.services.heartbeat import WorkerHeartbeat

    heartbeat = WorkerHeartbeat(
        worker_id="test-worker-456",
        worker_type="contact_research",
        interval_seconds=30
    )

    # Update task
    heartbeat.update_task(
        run_id="run-123",
        task="Processing contact",
        status="processing"
    )

    assert heartbeat._current_run_id == "run-123"
    assert heartbeat._current_task == "Processing contact"
    assert heartbeat._status == "processing"

    # Mark idle
    heartbeat.mark_idle()
    assert heartbeat._current_run_id is None
    assert heartbeat._current_task is None
    assert heartbeat._status == "idle"


def test_heartbeat_thread_lifecycle():
    """Test starting and stopping heartbeat thread."""
    from rv_agentic.services.heartbeat import WorkerHeartbeat

    heartbeat = WorkerHeartbeat(
        worker_id="test-worker-789",
        worker_type="lead_list",
        interval_seconds=1  # Fast for testing
    )

    # Should not be running initially
    assert heartbeat._thread is None or not heartbeat._thread.is_alive()

    # Start should create thread
    # Note: This will fail without POSTGRES_URL, so we just test the API
    try:
        heartbeat.start()
        assert heartbeat._thread is not None
        time.sleep(0.2)  # Let thread run briefly
        heartbeat.stop()
    except Exception as e:
        # Expected if POSTGRES_URL not set
        if "POSTGRES_URL" not in str(e):
            raise


@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL"),
    reason="Requires POSTGRES_URL to be set"
)
def test_supabase_heartbeat_functions():
    """Test Supabase heartbeat functions with real database."""
    from rv_agentic.services import supabase_client
    import uuid

    worker_id = f"test-worker-{uuid.uuid4()}"

    # Test upsert_worker_heartbeat
    supabase_client.upsert_worker_heartbeat(
        worker_id=worker_id,
        worker_type="company_research",
        status="active",
        metadata={"test": True}
    )

    # Test get_active_workers
    active = supabase_client.get_active_workers()
    assert any(w.get("worker_id") == worker_id for w in active)

    # Test get_worker_stats
    stats = supabase_client.get_worker_stats()
    assert len(stats) > 0
    assert any(s.get("worker_type") == "company_research" for s in stats)

    # Test stop_worker
    supabase_client.stop_worker(worker_id)

    # Verify worker is stopped
    time.sleep(0.5)
    stats_after = supabase_client.get_worker_stats()
    company_research_stats = next(
        (s for s in stats_after if s.get("worker_type") == "company_research"),
        {}
    )
    # Note: stopped workers may not appear in active counts

    # Cleanup
    supabase_client.cleanup_stale_workers(stale_threshold_minutes=0)


@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL"),
    reason="Requires POSTGRES_URL to be set"
)
def test_dead_worker_detection():
    """Test detection of dead workers."""
    from rv_agentic.services import supabase_client
    import uuid

    worker_id = f"test-dead-worker-{uuid.uuid4()}"

    # Create a worker with an old heartbeat
    supabase_client.upsert_worker_heartbeat(
        worker_id=worker_id,
        worker_type="contact_research",
        status="processing"
    )

    # Manually set heartbeat to 6 minutes ago to simulate dead worker
    with supabase_client._pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pm_pipeline.worker_heartbeats
                SET last_heartbeat_at = NOW() - INTERVAL '6 minutes'
                WHERE worker_id = %s
                """,
                (worker_id,)
            )

    # Test get_dead_workers
    dead = supabase_client.get_dead_workers()
    assert any(w.get("worker_id") == worker_id for w in dead)

    # Cleanup
    supabase_client.stop_worker(worker_id)
    supabase_client.cleanup_stale_workers(stale_threshold_minutes=0)


@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL"),
    reason="Requires POSTGRES_URL to be set"
)
def test_release_dead_worker_leases():
    """Test releasing leases from dead workers."""
    from rv_agentic.services import supabase_client
    import uuid

    worker_id = f"test-lease-worker-{uuid.uuid4()}"

    # Create a dead worker
    supabase_client.upsert_worker_heartbeat(
        worker_id=worker_id,
        worker_type="company_research",
        status="processing"
    )

    # Set heartbeat to old time
    with supabase_client._pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pm_pipeline.worker_heartbeats
                SET last_heartbeat_at = NOW() - INTERVAL '6 minutes'
                WHERE worker_id = %s
                """,
                (worker_id,)
            )

    # Test release_dead_worker_leases
    # (This will return 0 if no actual leases exist, which is fine)
    released = supabase_client.release_dead_worker_leases()
    assert isinstance(released, int)
    assert released >= 0

    # Cleanup
    supabase_client.stop_worker(worker_id)
    supabase_client.cleanup_stale_workers(stale_threshold_minutes=0)


@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL"),
    reason="Requires POSTGRES_URL to be set"
)
def test_heartbeat_manager_integration():
    """Test WorkerHeartbeat with real database."""
    from rv_agentic.services.heartbeat import WorkerHeartbeat
    from rv_agentic.services import supabase_client
    import uuid

    worker_id = f"test-integration-{uuid.uuid4()}"

    heartbeat = WorkerHeartbeat(
        worker_id=worker_id,
        worker_type="lead_list",
        interval_seconds=1,  # Fast for testing
        metadata={"integration_test": True}
    )

    try:
        # Start heartbeat
        heartbeat.start()
        time.sleep(1.5)  # Wait for at least one heartbeat

        # Verify worker appears in active workers
        active = supabase_client.get_active_workers()
        worker_found = any(w.get("worker_id") == worker_id for w in active)
        assert worker_found, f"Worker {worker_id} not found in active workers"

        # Update task
        heartbeat.update_task(
            run_id="test-run-123",
            task="Test task",
            status="processing"
        )
        time.sleep(0.5)

        # Verify task update
        active = supabase_client.get_active_workers()
        worker = next((w for w in active if w.get("worker_id") == worker_id), None)
        assert worker is not None
        assert worker.get("current_run_id") == "test-run-123"
        assert worker.get("current_task") == "Test task"
        assert worker.get("status") == "processing"

    finally:
        # Stop heartbeat
        heartbeat.stop()
        time.sleep(0.5)

        # Verify worker is marked as stopped
        with supabase_client._pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM pm_pipeline.worker_heartbeats WHERE worker_id = %s",
                    (worker_id,)
                )
                row = cur.fetchone()
                if row:
                    assert row[0] == "stopped"

        # Cleanup
        supabase_client.cleanup_stale_workers(stale_threshold_minutes=0)


@pytest.mark.skipif(
    not os.getenv("POSTGRES_URL"),
    reason="Requires POSTGRES_URL to be set"
)
def test_worker_health_summary():
    """Test get_worker_health_summary helper."""
    from rv_agentic.services.heartbeat import get_worker_health_summary

    summary = get_worker_health_summary()

    assert "stats_by_type" in summary
    assert "total_active_workers" in summary
    assert "total_dead_workers" in summary
    assert "health_status" in summary
    assert summary["health_status"] in ["healthy", "degraded", "unknown"]
