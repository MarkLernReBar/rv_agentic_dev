#!/bin/bash
# Real-time pipeline monitoring with color coding

echo "🔍 PIPELINE MONITOR - Real-time view"
echo "====================================="
echo ""

# Check if worker is running
WORKER_PID=$(pgrep -f "lead_list_runner")
if [ -z "$WORKER_PID" ]; then
    echo "❌ NO WORKER RUNNING!"
    echo "   Start with: .venv/bin/python -m rv_agentic.workers.lead_list_runner > worker.log 2>&1 &"
    exit 1
fi

echo "✅ Worker running (PID: $WORKER_PID)"
echo ""

# Show recent database status
echo "📊 DATABASE STATUS:"
.venv/bin/python debug_runs.py | head -60

echo ""
echo "📝 WATCHING LOGS (Ctrl+C to stop)..."
echo "====================================="

# Tail logs with filtering
tail -f .lead_list_worker.log 2>/dev/null | grep --line-buffered -E "(Starting|Completed|Inserted|ERROR|companies found|parallel region|run_id|No active runs)"
