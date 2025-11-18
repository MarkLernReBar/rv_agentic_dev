"""Worker heartbeat management for health monitoring.

This module provides utilities for workers to send periodic heartbeats,
enabling monitoring and automatic cleanup of crashed workers.
"""

import atexit
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from rv_agentic.services import supabase_client

logger = logging.getLogger(__name__)


class WorkerHeartbeat:
    """Manages periodic heartbeats for a worker.

    This class handles:
    - Sending periodic heartbeats in a background thread
    - Tracking current task/run information
    - Graceful shutdown handling
    - Automatic worker registration/cleanup

    Usage:
        ```python
        heartbeat = WorkerHeartbeat(
            worker_id="lead-list-12345",
            worker_type="lead_list",
            interval_seconds=30
        )
        heartbeat.start()

        try:
            # Do work
            heartbeat.update_task(run_id="abc", task="Processing company XYZ")
            process_something()
        finally:
            heartbeat.stop()
        ```
    """

    def __init__(
        self,
        worker_id: str,
        worker_type: str,
        interval_seconds: int = 30,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Initialize worker heartbeat manager.

        Args:
            worker_id: Unique identifier for this worker
            worker_type: Type of worker ('lead_list', 'company_research', 'contact_research')
            interval_seconds: How often to send heartbeats (default: 30s)
            metadata: Additional metadata to include in heartbeats
        """
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.interval_seconds = interval_seconds
        self.metadata = metadata or {}

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Current task information
        self._current_run_id: Optional[str] = None
        self._current_task: Optional[str] = None
        self._lease_expires_at: Optional[datetime] = None
        self._status = "idle"

        # Register shutdown handlers
        atexit.register(self.stop)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info("Worker %s received signal %s, shutting down", self.worker_id, signum)
        self.stop()

    def start(self) -> None:
        """Start sending periodic heartbeats in background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Heartbeat thread already running for worker %s", self.worker_id)
            return

        self._stop_event.clear()
        self._status = "active"

        # Send initial heartbeat immediately
        self._send_heartbeat()

        # Start background thread
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"heartbeat-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Started heartbeat thread for worker %s (interval: %ds)",
            self.worker_id,
            self.interval_seconds,
        )

    def stop(self) -> None:
        """Stop sending heartbeats and mark worker as stopped."""
        if not self._thread or not self._thread.is_alive():
            return

        logger.info("Stopping heartbeat thread for worker %s", self.worker_id)
        self._stop_event.set()

        # Wait for thread to finish (with timeout)
        self._thread.join(timeout=5.0)

        # Send final heartbeat marking worker as stopped
        try:
            supabase_client.stop_worker(self.worker_id)
            logger.info("Worker %s marked as stopped", self.worker_id)
        except Exception as e:
            logger.error("Failed to mark worker %s as stopped: %s", self.worker_id, e)

    def update_task(
        self,
        run_id: Optional[str] = None,
        task: Optional[str] = None,
        lease_expires_at: Optional[datetime] = None,
        status: str = "processing",
    ) -> None:
        """Update current task information.

        This should be called whenever the worker starts processing a new task.
        The next heartbeat will include this information.

        Args:
            run_id: ID of run being processed
            task: Description of current task
            lease_expires_at: When the current lease expires
            status: Worker status ('processing', 'idle', 'active')
        """
        with self._lock:
            self._current_run_id = run_id
            self._current_task = task
            self._lease_expires_at = lease_expires_at
            self._status = status

        # Send heartbeat immediately to update task info
        self._send_heartbeat()

    def mark_idle(self) -> None:
        """Mark worker as idle (no current task)."""
        self.update_task(run_id=None, task=None, status="idle")

    def _heartbeat_loop(self) -> None:
        """Background thread that sends periodic heartbeats."""
        while not self._stop_event.is_set():
            try:
                self._send_heartbeat()
            except Exception as e:
                logger.error(
                    "Failed to send heartbeat for worker %s: %s",
                    self.worker_id,
                    e,
                    exc_info=True,
                )

            # Sleep in small increments so we can respond quickly to stop event
            for _ in range(self.interval_seconds * 10):
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)

    def _send_heartbeat(self) -> None:
        """Send a single heartbeat to the database."""
        with self._lock:
            run_id = self._current_run_id
            task = self._current_task
            lease_expires_at = self._lease_expires_at
            status = self._status

        try:
            supabase_client.upsert_worker_heartbeat(
                worker_id=self.worker_id,
                worker_type=self.worker_type,
                status=status,
                current_run_id=run_id,
                current_task=task,
                lease_expires_at=lease_expires_at,
                metadata=self.metadata,
            )
            logger.debug(
                "Heartbeat sent for worker %s (status=%s, run_id=%s)",
                self.worker_id,
                status,
                run_id,
            )
        except Exception as e:
            # Log but don't crash - heartbeat failures shouldn't stop work
            logger.warning(
                "Failed to send heartbeat for worker %s: %s",
                self.worker_id,
                e,
            )


def cleanup_dead_workers() -> int:
    """Clean up leases held by dead workers.

    This should be called periodically (e.g., every minute) by a monitoring
    process to release leases from crashed workers.

    Returns:
        Number of leases released
    """
    try:
        released = supabase_client.release_dead_worker_leases()
        if released > 0:
            logger.info("Released %d leases from dead workers", released)
        return released
    except Exception as e:
        logger.error("Failed to cleanup dead workers: %s", e)
        return 0


def get_worker_health_summary() -> Dict[str, Any]:
    """Get summary of worker health across all types.

    Returns:
        Dict with worker statistics and health status
    """
    try:
        stats = supabase_client.get_worker_stats()
        active_workers = supabase_client.get_active_workers()
        dead_workers = supabase_client.get_dead_workers()

        total_active = sum(s.get("active_workers", 0) for s in stats)
        total_dead = sum(s.get("dead_workers", 0) for s in stats)

        # Determine health status based on active workers
        # Only show degraded if there are NO active workers (system cannot process)
        if total_active > 0:
            health_status = "healthy"
        elif total_dead > 0:
            health_status = "no_workers"  # Workers existed but all died
        else:
            health_status = "unknown"  # No workers ever registered

        return {
            "stats_by_type": stats,
            "total_active_workers": total_active,
            "total_dead_workers": total_dead,
            "active_workers": active_workers,
            "dead_workers": dead_workers,
            "health_status": health_status,
        }
    except Exception as e:
        logger.error("Failed to get worker health summary: %s", e)
        return {
            "error": str(e),
            "health_status": "unknown",
        }
