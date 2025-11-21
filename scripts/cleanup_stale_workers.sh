#!/bin/bash
# Cleanup Script: Kill Stale Background Workers
#
# This script identifies and kills workers that are targeting completed/archived runs.
# Can be run manually or as a cron job for automated cleanup.
#
# Usage:
#   ./scripts/cleanup_stale_workers.sh           # Interactive mode - shows what would be killed
#   ./scripts/cleanup_stale_workers.sh --force   # Actually kills the workers
#   ./scripts/cleanup_stale_workers.sh --help    # Show help

set -e

FORCE=false
DRY_RUN=true

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE=true
            DRY_RUN=false
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Cleanup stale background workers targeting completed/archived runs."
            echo ""
            echo "Options:"
            echo "  --force, -f    Actually kill the workers (default: dry-run)"
            echo "  --help, -h     Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0              # Show what would be killed"
            echo "  $0 --force      # Kill stale workers"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "🔍 Scanning for stale workers..."
echo ""

# Find all Python worker processes
WORKER_PIDS=$(ps aux | grep -E "python.*rv_agentic\.workers\.(lead_list_runner|company_research_runner|contact_research_runner)" | grep -v grep | awk '{print $2}' || true)

if [ -z "$WORKER_PIDS" ]; then
    echo "✅ No workers found running"
    exit 0
fi

echo "Found $(echo "$WORKER_PIDS" | wc -l | tr -d ' ') worker process(es)"
echo ""

# Check each worker's RUN_FILTER_ID against database
STALE_PIDS=()

for PID in $WORKER_PIDS; do
    # Get the command line for this process
    CMDLINE=$(ps -p $PID -o command= 2>/dev/null || true)

    if [ -z "$CMDLINE" ]; then
        continue
    fi

    # Extract RUN_FILTER_ID from environment
    RUN_ID=$(echo "$CMDLINE" | grep -o "RUN_FILTER_ID=[^ ]*" | cut -d= -f2 || true)

    if [ -z "$RUN_ID" ]; then
        echo "⚠️  Worker PID $PID: No RUN_FILTER_ID (production worker, skipping)"
        continue
    fi

    # Check run status in database
    if [ -z "$POSTGRES_URL" ]; then
        echo "❌ POSTGRES_URL not set - cannot check run status"
        exit 1
    fi

    RUN_STATUS=$(psql "$POSTGRES_URL" -t -c "SELECT status FROM pm_pipeline.runs WHERE id = '$RUN_ID'" 2>/dev/null | tr -d ' ' || echo "not_found")

    if [ "$RUN_STATUS" = "not_found" ] || [ -z "$RUN_STATUS" ]; then
        echo "⚠️  Worker PID $PID: Run $RUN_ID not found in database"
        STALE_PIDS+=("$PID")
    elif [ "$RUN_STATUS" = "completed" ] || [ "$RUN_STATUS" = "error" ] || [ "$RUN_STATUS" = "archived" ]; then
        echo "🔴 STALE Worker PID $PID: Targeting run $RUN_ID (status: $RUN_STATUS)"
        STALE_PIDS+=("$PID")
    else
        echo "✅ Active Worker PID $PID: Targeting run $RUN_ID (status: $RUN_STATUS)"
    fi
done

echo ""

if [ ${#STALE_PIDS[@]} -eq 0 ]; then
    echo "✅ No stale workers found"
    exit 0
fi

echo "Found ${#STALE_PIDS[@]} stale worker(s)"
echo ""

if [ "$DRY_RUN" = true ]; then
    echo "🔍 DRY RUN: Would kill the following PIDs:"
    for PID in "${STALE_PIDS[@]}"; do
        echo "  - PID $PID"
    done
    echo ""
    echo "To actually kill these workers, run with --force flag"
else
    echo "💀 Killing stale workers..."
    for PID in "${STALE_PIDS[@]}"; do
        echo "  Killing PID $PID..."
        kill -9 "$PID" 2>/dev/null || echo "    Failed to kill PID $PID (may already be dead)"
    done
    echo ""
    echo "✅ Cleanup complete"
fi
