"""Heartbeat monitor daemon for cleaning up dead workers.

This process runs continuously in the background and:
1. Detects workers that have stopped sending heartbeats
2. Releases leases held by dead workers
3. Logs worker health statistics
4. Optionally sends alerts for dead workers

Usage:
    python -m rv_agentic.workers.heartbeat_monitor

Environment Variables:
    HEARTBEAT_MONITOR_INTERVAL: Cleanup check interval in seconds (default: 60)
    HEARTBEAT_MONITOR_ALERT_EMAIL: Email for dead worker alerts
    LOG_LEVEL: Logging level (default: INFO)
"""

import logging
import os
import time
from typing import List, Dict, Any

from rv_agentic.services import heartbeat
from rv_agentic.services.notifications import send_notification
from rv_agentic.workers.utils import load_env_files

logger = logging.getLogger(__name__)


def check_and_cleanup_dead_workers() -> Dict[str, Any]:
    """Check for dead workers and clean up their leases.

    Returns:
        Dict with cleanup results and statistics
    """
    try:
        # Get current worker health
        health = heartbeat.get_worker_health_summary()

        total_active = health.get("total_active_workers", 0)
        total_dead = health.get("total_dead_workers", 0)
        dead_workers = health.get("dead_workers", [])

        logger.info(
            "Worker health check: %d active, %d dead workers",
            total_active,
            total_dead
        )

        # Log details for each dead worker
        for worker in dead_workers:
            worker_id = worker.get("worker_id")
            worker_type = worker.get("worker_type")
            minutes_ago = worker.get("seconds_since_heartbeat", 0) / 60.0
            current_task = worker.get("current_task")

            logger.warning(
                "Dead worker detected: %s (type=%s, last_seen=%.1f min ago, task=%s)",
                worker_id,
                worker_type,
                minutes_ago,
                current_task or "none"
            )

        # Clean up leases from dead workers
        released = 0
        if total_dead > 0:
            released = heartbeat.cleanup_dead_workers()
            logger.info("Released %d leases from dead workers", released)

        return {
            "total_active": total_active,
            "total_dead": total_dead,
            "leases_released": released,
            "dead_workers": dead_workers,
            "health_status": health.get("health_status", "unknown"),
        }

    except Exception as e:
        logger.error("Failed to check/cleanup dead workers: %s", e, exc_info=True)
        return {
            "error": str(e),
            "health_status": "error",
        }


def send_dead_worker_alert(dead_workers: List[Dict[str, Any]], alert_email: str) -> None:
    """Send email alert for dead workers.

    Args:
        dead_workers: List of dead worker details
        alert_email: Email address to send alert to
    """
    if not dead_workers or not alert_email:
        return

    try:
        worker_details = []
        for worker in dead_workers:
            worker_id = worker.get("worker_id", "unknown")
            worker_type = worker.get("worker_type", "unknown")
            minutes_ago = worker.get("seconds_since_heartbeat", 0) / 60.0
            current_task = worker.get("current_task", "none")

            worker_details.append(
                f"- {worker_id} ({worker_type}): "
                f"last seen {minutes_ago:.1f} min ago, task: {current_task}"
            )

        subject = f"⚠️ Dead Workers Detected ({len(dead_workers)})"
        body = (
            f"The following workers have stopped sending heartbeats:\n\n"
            + "\n".join(worker_details)
            + "\n\nLeases from these workers have been automatically released. "
            + "Please check worker logs and restart if needed."
        )

        send_notification(
            to_email=alert_email,
            subject=subject,
            body=body
        )
        logger.info("Sent dead worker alert to %s", alert_email)

    except Exception as e:
        logger.error("Failed to send dead worker alert: %s", e)


def main() -> None:
    """Main loop for heartbeat monitor daemon."""
    load_env_files()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    interval = int(os.getenv("HEARTBEAT_MONITOR_INTERVAL", "60"))
    alert_email = os.getenv("HEARTBEAT_MONITOR_ALERT_EMAIL", "").strip()

    logger.info(
        "Heartbeat monitor starting (interval=%ds, alerts=%s)",
        interval,
        "enabled" if alert_email else "disabled"
    )

    # Track if we've already alerted for specific dead workers
    alerted_workers = set()

    try:
        while True:
            result = check_and_cleanup_dead_workers()

            # Send alerts for newly dead workers
            if alert_email and result.get("total_dead", 0) > 0:
                dead_workers = result.get("dead_workers", [])
                new_dead_workers = []

                for worker in dead_workers:
                    worker_id = worker.get("worker_id")
                    if worker_id and worker_id not in alerted_workers:
                        new_dead_workers.append(worker)
                        alerted_workers.add(worker_id)

                if new_dead_workers:
                    send_dead_worker_alert(new_dead_workers, alert_email)

            # Remove workers from alerted set if they're alive again
            if result.get("health_status") == "healthy":
                alerted_workers.clear()

            # Log statistics
            stats = result.get("stats_by_type", [])
            if isinstance(stats, list):
                for stat in stats:
                    worker_type = stat.get("worker_type")
                    active = stat.get("active_workers", 0)
                    dead = stat.get("dead_workers", 0)
                    logger.debug(
                        "Worker stats: type=%s, active=%d, dead=%d",
                        worker_type,
                        active,
                        dead
                    )

            # Sleep until next check
            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info("Heartbeat monitor shutting down (user interrupt)")
    except Exception as e:
        logger.error("Heartbeat monitor crashed: %s", e, exc_info=True)
        raise


if __name__ == "__main__":  # pragma: no cover
    main()
