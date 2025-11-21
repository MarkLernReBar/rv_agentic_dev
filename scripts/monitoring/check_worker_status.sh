#!/bin/bash
# check_worker_status.sh - Check status of all workers
# Usage: ./check_worker_status.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment
if [ -f ".env.local" ]; then
    export $(grep -v '^#' .env.local | xargs 2>/dev/null)
fi

echo "📊 RV Agentic Worker System Status"
echo "===================================="
echo ""

# Function to check process status
check_process() {
    local worker_name=$1
    local display_name=$2
    local pids=$(pgrep -f "python.*${worker_name}")

    if [ -z "$pids" ]; then
        echo "❌ ${display_name}: NOT RUNNING"
        return 1
    else
        local count=$(echo "$pids" | wc -w)
        echo "✅ ${display_name}: RUNNING (${count} process(es))"
        for pid in $pids; do
            local uptime=$(ps -p $pid -o etime= | tr -d ' ')
            local mem=$(ps -p $pid -o rss= | awk '{printf "%.1f MB", $1/1024}')
            echo "   PID: $pid, Uptime: $uptime, Memory: $mem"
        done
        return 0
    fi
}

# Check all workers
echo "Process Status:"
echo "---------------"
check_process "heartbeat_monitor" "Heartbeat Monitor"
check_process "lead_list_runner" "Lead List Runner"
check_process "company_research_runner" "Company Research Runner"
check_process "contact_research_runner" "Contact Research Runner"
check_process "staging_promotion_runner" "Staging Promotion Runner"

echo ""
echo "Database Worker Status:"
echo "----------------------"

# Query database for active workers
.venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
from rv_agentic.services import supabase_client

# Active workers
active = supabase_client.get_active_workers()
if active:
    print(f'✅ Active Workers in DB: {len(active)}')
    for w in active:
        worker_type = w.get('worker_type', 'unknown')
        worker_id = w.get('worker_id', 'N/A')[:30]
        last_heartbeat = w.get('last_heartbeat_at', 'N/A')
        print(f'   [{worker_type}] {worker_id}...')
        print(f'      Last heartbeat: {last_heartbeat}')
else:
    print('⚠️  No active workers found in database')

print()

# Dead workers
dead = supabase_client.get_dead_workers()
if dead:
    print(f'❌ Dead Workers in DB: {len(dead)}')
    for w in dead:
        worker_type = w.get('worker_type', 'unknown')
        worker_id = w.get('worker_id', 'N/A')[:30]
        print(f'   [{worker_type}] {worker_id}...')
else:
    print('✅ No dead workers')

print()

# Recent runs
runs = supabase_client.get_active_and_recent_runs(limit=5)
if runs:
    print(f'📋 Recent Runs: {len(runs)}')
    for run in runs:
        run_id = str(run.get('id', 'N/A'))[:20]
        stage = run.get('stage', 'N/A')
        status = run.get('status', 'N/A')
        print(f'   {run_id}... - Stage: {stage}, Status: {status}')
"

echo ""
echo "===================================="
echo "💡 Commands:"
echo "   Start workers: ./start_all_workers.sh"
echo "   Stop workers: ./stop_all_workers.sh"
echo "   View logs: tail -f logs/*.log"
echo ""
