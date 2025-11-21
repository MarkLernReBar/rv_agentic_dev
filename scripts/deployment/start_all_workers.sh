#!/bin/bash
# start_all_workers.sh - Launch all workers and monitoring
# Usage: ./start_all_workers.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment
if [ -f ".env.local" ]; then
    export $(grep -v '^#' .env.local | xargs)
fi

# Check if Python virtual environment exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Run 'uv sync' first."
    exit 1
fi

PYTHON=".venv/bin/python"

# Create logs directory
mkdir -p logs

echo "🚀 Starting RV Agentic Worker System"
echo "===================================="

# Function to check if a worker is already running
check_worker() {
    local worker_name=$1
    local count=$(pgrep -f "python.*${worker_name}" | wc -l)
    if [ "$count" -gt 0 ]; then
        echo "⚠️  ${worker_name} is already running (${count} processes)"
        return 0
    fi
    return 1
}

# Start heartbeat monitor
if ! check_worker "heartbeat_monitor"; then
    echo "🩺 Starting heartbeat monitor..."
    nohup $PYTHON -m rv_agentic.workers.heartbeat_monitor \
        > logs/heartbeat_monitor.log 2>&1 &
    echo "   PID: $! (logs/heartbeat_monitor.log)"
else
    echo "✅ Heartbeat monitor already running"
fi

# Start lead list runner
if ! check_worker "lead_list_runner"; then
    echo "📋 Starting lead list runner..."
    nohup $PYTHON -m rv_agentic.workers.lead_list_runner \
        > logs/lead_list_runner.log 2>&1 &
    echo "   PID: $! (logs/lead_list_runner.log)"
else
    echo "✅ Lead list runner already running"
fi

# Start company research runner
if ! check_worker "company_research_runner"; then
    echo "🏢 Starting company research runner..."
    nohup $PYTHON -m rv_agentic.workers.company_research_runner \
        > logs/company_research_runner.log 2>&1 &
    echo "   PID: $! (logs/company_research_runner.log)"
else
    echo "✅ Company research runner already running"
fi

# Start contact research runner
if ! check_worker "contact_research_runner"; then
    echo "👥 Starting contact research runner..."
    nohup $PYTHON -m rv_agentic.workers.contact_research_runner \
        > logs/contact_research_runner.log 2>&1 &
    echo "   PID: $! (logs/contact_research_runner.log)"
else
    echo "✅ Contact research runner already running"
fi

# Optional: Start staging promotion runner if needed
# Uncomment if you want this to run automatically
# if ! check_worker "staging_promotion_runner"; then
#     echo "📤 Starting staging promotion runner..."
#     nohup $PYTHON -m rv_agentic.workers.staging_promotion_runner \
#         > logs/staging_promotion_runner.log 2>&1 &
#     echo "   PID: $! (logs/staging_promotion_runner.log)"
# fi

sleep 2

echo ""
echo "===================================="
echo "✅ Worker system started"
echo ""
echo "📊 Current worker status:"
echo ""
$PYTHON -c "
import sys
sys.path.insert(0, 'src')
from rv_agentic.services import supabase_client

active = supabase_client.get_active_workers()
if active:
    print(f'✅ {len(active)} active workers:')
    for w in active:
        print(f\"   - {w.get('worker_type', 'unknown')}: {w.get('worker_id', 'N/A')[:20]}...\")
else:
    print('⚠️  No active workers detected yet (may take ~30s for heartbeats)')
"

echo ""
echo "📝 Commands:"
echo "   View logs: tail -f logs/lead_list_runner.log"
echo "   Check status: ./check_worker_status.sh"
echo "   Stop all: ./stop_all_workers.sh"
echo ""
