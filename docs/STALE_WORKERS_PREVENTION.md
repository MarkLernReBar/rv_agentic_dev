# Stale Workers Prevention Guide

**Date**: 2025-11-20
**Problem**: Background test workers continuing to poll and make MCP calls after runs complete
**Solution**: Multi-layered defense system

## The Problem

During testing on 2025-11-20, we discovered that background worker `8b00f1` was still running for Austin TX run `0a14f2a7` even after the run reached `status='completed'`. The worker continued polling every 2-3 seconds and making MCP calls, causing confusion about where the calls were coming from.

### Root Cause

```bash
# Test command that created the problem:
RUN_FILTER_ID=0a14f2a7... WORKER_MAX_LOOPS=10 python -m rv_agentic.workers.lead_list_runner &
```

**Issues:**
1. Worker ran in background (`&`)
2. Worker set `WORKER_MAX_LOOPS=10` (high limit)
3. Worker only checked `fetch_active_runs()` which filtered by status
4. Once run reached `completed`, `fetch_active_runs()` returned empty
5. Worker entered infinite sleep loop: "No active runs found; sleeping"
6. Worker never checked if the FILTERED run itself was terminal
7. Worker continued indefinitely until manually killed

## The Solution: 3-Layer Defense

### Layer 1: Worker Auto-Termination (PRIMARY DEFENSE)

**File**: [src/rv_agentic/workers/lead_list_runner.py:1491-1511](../src/rv_agentic/workers/lead_list_runner.py#L1491-L1511)

Workers now check if their `RUN_FILTER_ID` target has reached a terminal state and exit immediately:

```python
while True:
    # CRITICAL FIX: When running in targeted test mode (RUN_FILTER_ID set),
    # exit immediately if that specific run has reached a terminal state.
    if run_filter_id:
        filtered_run = supabase_client.get_run_by_id(run_filter_id)
        if filtered_run:
            run_status = (filtered_run.get("status") or "").strip()
            # Terminal states: completed, error, archived
            if run_status in ("completed", "error", "archived"):
                logger.info(
                    "RUN_FILTER_ID %s has reached terminal status '%s'; exiting worker",
                    run_filter_id,
                    run_status
                )
                break
```

**Terminal States:**
- `completed` - Run finished successfully
- `error` - Run encountered an error
- `archived` - Run was archived by user

**When It Triggers:**
- At the start of EVERY loop iteration
- Before `fetch_active_runs()` is called
- Worker exits cleanly via `break`

**Impact:**
- Test workers exit immediately when target run completes
- No more indefinite polling on finished runs
- Clean shutdown without manual intervention

### Layer 2: Cleanup Script (SAFETY NET)

**File**: [scripts/cleanup_stale_workers.sh](../scripts/cleanup_stale_workers.sh)

Manual/automated script to kill orphaned workers:

```bash
# Dry run - show what would be killed
./scripts/cleanup_stale_workers.sh

# Actually kill stale workers
./scripts/cleanup_stale_workers.sh --force
```

**How It Works:**
1. Finds all Python worker processes
2. Extracts `RUN_FILTER_ID` from command line
3. Queries database for run status
4. Identifies workers targeting completed/error/archived runs
5. Kills those workers (with `--force` flag)

**Use Cases:**
- Emergency cleanup of orphaned workers
- Pre-deployment cleanup
- Cron job for automated hygiene
- Recovery from worker crashes

**Example Output:**
```
🔍 Scanning for stale workers...

Found 3 worker process(es)

⚠️  Worker PID 12345: No RUN_FILTER_ID (production worker, skipping)
🔴 STALE Worker PID 67890: Targeting run 0a14f2a7... (status: completed)
✅ Active Worker PID 11111: Targeting run 0915b268... (status: in_progress)

Found 1 stale worker(s)

💀 Killing stale workers...
  Killing PID 67890...

✅ Cleanup complete
```

### Layer 3: Testing Best Practices (PREVENTION)

**Updated**: [CLAUDE.md](../CLAUDE.md)

#### DO:
```bash
# ✅ CORRECT: Foreground execution with low loop limit
RUN_FILTER_ID=xxx WORKER_MAX_LOOPS=1 python -m rv_agentic.workers.lead_list_runner

# ✅ CORRECT: Explicit timeout
timeout 300 RUN_FILTER_ID=xxx python -m rv_agentic.workers.lead_list_runner
```

#### DON'T:
```bash
# ❌ WRONG: Background execution
RUN_FILTER_ID=xxx python -m rv_agentic.workers.lead_list_runner &

# ❌ WRONG: High loop limits in background
RUN_FILTER_ID=xxx WORKER_MAX_LOOPS=10 python -m rv_agentic.workers.lead_list_runner &
```

## Testing the Fix

### Step 1: Verify Auto-Termination

```bash
# Create a test run
RUN_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
psql $POSTGRES_URL -c "INSERT INTO pm_pipeline.runs (id, criteria, target_quantity, stage, status)
VALUES ('$RUN_ID', '{\"pms\": \"Buildium\"}', 5, 'company_discovery', 'active');"

# Start worker
RUN_FILTER_ID=$RUN_ID WORKER_MAX_LOOPS=100 python -m rv_agentic.workers.lead_list_runner &
WORKER_PID=$!

# Wait for first loop
sleep 5

# Mark run as completed
psql $POSTGRES_URL -c "UPDATE pm_pipeline.runs SET status='completed' WHERE id='$RUN_ID';"

# Worker should exit within 2-3 seconds
sleep 5

# Check if worker is still running (should be dead)
if ps -p $WORKER_PID > /dev/null; then
    echo "❌ FAIL: Worker still running"
    kill -9 $WORKER_PID
else
    echo "✅ PASS: Worker exited cleanly"
fi
```

### Step 2: Verify Cleanup Script

```bash
# Start a test worker targeting completed run
RUN_FILTER_ID=0a14f2a7-ed6e-434a-9160-dd547bbab3a3 python -m rv_agentic.workers.lead_list_runner &

# Wait for it to poll
sleep 3

# Dry run
./scripts/cleanup_stale_workers.sh
# Should show: "🔴 STALE Worker PID xxx: Targeting run 0a14f2a7... (status: completed)"

# Kill it
./scripts/cleanup_stale_workers.sh --force
# Should show: "✅ Cleanup complete"
```

## Deployment Checklist

Before deploying to production:

- [ ] Layer 1: Worker auto-termination code deployed
- [ ] Layer 2: Cleanup script installed in `scripts/`
- [ ] Layer 3: Documentation updated in CLAUDE.md
- [ ] Cleanup script added to pre-deployment checklist
- [ ] Consider adding cleanup script to cron (optional)

## Operational Procedures

### Pre-Deployment

```bash
# Always clean up stale workers before deploying
./scripts/cleanup_stale_workers.sh --force
```

### During Testing

```bash
# Use foreground execution
RUN_FILTER_ID=xxx WORKER_MAX_LOOPS=1 python -m rv_agentic.workers.lead_list_runner

# Or use explicit timeout
timeout 300 RUN_FILTER_ID=xxx python -m rv_agentic.workers.lead_list_runner
```

### Post-Incident

```bash
# Check for stale workers
./scripts/cleanup_stale_workers.sh

# Kill if found
./scripts/cleanup_stale_workers.sh --force

# Verify all workers stopped
ps aux | grep "python.*rv_agentic.workers" | grep -v grep
```

## Future Enhancements

### Optional: Worker Registry Table

For production monitoring, consider adding:

```sql
CREATE TABLE pm_pipeline.worker_registry (
    worker_id TEXT PRIMARY KEY,
    worker_type TEXT NOT NULL,
    run_filter_id UUID,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'active'
);
```

This would enable:
- Real-time worker monitoring
- Automated detection of crashed workers
- Better operational visibility
- Worker load balancing

### Optional: Cron Job

Add to crontab for automated cleanup:

```cron
# Clean up stale workers every hour
0 * * * * cd /path/to/project && ./scripts/cleanup_stale_workers.sh --force >> /var/log/worker_cleanup.log 2>&1
```

## Summary

**Three Layers of Defense:**

1. **Prevention**: Workers auto-exit when target run reaches terminal state
2. **Detection**: Cleanup script identifies orphaned workers
3. **Education**: Best practices prevent background worker pollution

**Result**: Stale workers can no longer continue indefinitely after test completion.

---

**Related Documentation:**
- [SESSION_CONTINUATION_SUMMARY.md](SESSION_CONTINUATION_SUMMARY.md) - Discovery of the stale worker problem
- [CLAUDE.md](../CLAUDE.md) - Updated testing best practices
- [WORKER_MANAGEMENT.md](../WORKER_MANAGEMENT.md) - Worker lifecycle management
