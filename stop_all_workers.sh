#!/bin/bash
# stop_all_workers.sh - Stop all running workers
# Usage: ./stop_all_workers.sh

echo "🛑 Stopping RV Agentic Worker System"
echo "===================================="

# Function to stop workers by name pattern
stop_worker() {
    local worker_name=$1
    local pids=$(pgrep -f "python.*${worker_name}")

    if [ -z "$pids" ]; then
        echo "✅ ${worker_name}: not running"
        return 0
    fi

    echo "🛑 Stopping ${worker_name}..."
    for pid in $pids; do
        echo "   Killing PID $pid"
        kill $pid 2>/dev/null || kill -9 $pid 2>/dev/null
    done

    sleep 1

    # Verify stopped
    local remaining=$(pgrep -f "python.*${worker_name}")
    if [ -z "$remaining" ]; then
        echo "✅ ${worker_name}: stopped"
    else
        echo "⚠️  ${worker_name}: some processes still running: $remaining"
    fi
}

# Stop all workers
stop_worker "heartbeat_monitor"
stop_worker "lead_list_runner"
stop_worker "company_research_runner"
stop_worker "contact_research_runner"
stop_worker "staging_promotion_runner"

echo ""
echo "===================================="
echo "✅ All workers stopped"
echo ""
