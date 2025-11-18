# Phase 2.2: Worker Health Checks - IMPLEMENTATION COMPLETE ⏳

**Date:** 2025-01-17
**Status:** ⏳ **AWAITING DATABASE MIGRATION**

---

## Overview

Phase 2.2 adds comprehensive health monitoring for workers through a database-backed heartbeat system. This enables:
- Real-time worker status tracking
- Automatic dead worker detection
- Lease cleanup from crashed workers
- Worker monitoring in UI

---

## What's Been Built

### 1. Database Schema (`sql/migrations/003_worker_heartbeats.sql`)

**Table: `pm_pipeline.worker_heartbeats`**
```sql
worker_id TEXT PRIMARY KEY           -- Unique worker identifier
worker_type TEXT NOT NULL             -- 'lead_list', 'company_research', 'contact_research'
last_heartbeat_at TIMESTAMPTZ        -- When last heartbeat was sent
status TEXT                           -- 'active', 'idle', 'processing', 'stopped'
current_run_id UUID                   -- Run being processed
current_task TEXT                     -- Task description
lease_expires_at TIMESTAMPTZ         -- When current lease expires
started_at TIMESTAMPTZ               -- When worker started
metadata JSONB                        -- Additional metadata
```

**Views:**
- `v_active_workers` - Workers with heartbeat within last 5 minutes
- `v_dead_workers` - Workers that stopped sending heartbeats

**Functions:**
- `upsert_worker_heartbeat()` - Update/insert heartbeat with current timestamp
- `stop_worker()` - Mark worker as stopped (graceful shutdown)
- `cleanup_stale_workers()` - Remove old stopped workers
- `get_worker_stats()` - Get statistics by worker type

### 2. Supabase Client Functions (`src/rv_agentic/services/supabase_client.py`)

Added 8 new functions (lines 1443-1646):

```python
# Core heartbeat functions
upsert_worker_heartbeat()      # Send heartbeat
stop_worker()                   # Graceful shutdown
get_active_workers()            # Query active workers
get_dead_workers()              # Find crashed workers
get_worker_stats()              # Get statistics
cleanup_stale_workers()         # Remove old workers
release_dead_worker_leases()    # Clean up after crashes
```

### 3. Heartbeat Manager (`src/rv_agentic/services/heartbeat.py`)

**WorkerHeartbeat Class:** Background thread that sends periodic heartbeats

```python
heartbeat = WorkerHeartbeat(
    worker_id="company-research-12345",
    worker_type="company_research",
    interval_seconds=30
)
heartbeat.start()

# Update task info
heartbeat.update_task(
    run_id="abc123",
    task="Researching company example.com",
    status="processing"
)

# Mark as idle
heartbeat.mark_idle()

# Stop on shutdown
heartbeat.stop()
```

**Features:**
- Background thread sends heartbeats every N seconds (default: 30s)
- Automatic shutdown handling (SIGTERM, SIGINT, atexit)
- Thread-safe task updates
- Graceful degradation (heartbeat failures don't stop work)

**Helper Functions:**
- `cleanup_dead_workers()` - Release leases from crashed workers
- `get_worker_health_summary()` - Get overall system health

### 4. Worker Integration (Example: `company_research_runner.py`)

Integrated heartbeats into company_research_runner:

**Changes:**
1. Import `WorkerHeartbeat`
2. Start heartbeat on worker startup
3. Update heartbeat when claiming tasks
4. Mark idle between tasks
5. Stop heartbeat on shutdown

**Pattern:**
```python
from rv_agentic.services.heartbeat import WorkerHeartbeat

def main():
    # Initialize heartbeat
    heartbeat = WorkerHeartbeat(
        worker_id=worker_id,
        worker_type="company_research",
        interval_seconds=30
    )
    heartbeat.start()

    try:
        while True:
            # Process work
            claimed = process_company_claim(agent, worker_id, lease_seconds, heartbeat)
            if not claimed:
                heartbeat.mark_idle()
                time.sleep(idle_sleep)
    finally:
        heartbeat.stop()

def process_company_claim(..., heartbeat):
    claim = supabase_client.claim_company_for_research(...)

    # Update heartbeat with current task
    if heartbeat:
        heartbeat.update_task(
            run_id=run_id,
            task=f"Researching company: {domain}",
            status="processing"
        )

    try:
        # Do work...
        pass
    finally:
        # Mark idle after processing
        if heartbeat:
            heartbeat.mark_idle()
```

---

## What's NOT Done Yet

### 1. Database Migration (Required)

**Action Needed:** Run the SQL migration:

```bash
psql $POSTGRES_URL -f sql/migrations/003_worker_heartbeats.sql
```

Or through Supabase dashboard:
1. Go to SQL Editor
2. Paste contents of `sql/migrations/003_worker_heartbeats.sql`
3. Run

**Until this is done:**
- Workers with heartbeats will crash on startup
- Heartbeat functions will fail with "relation does not exist"
- No monitoring data will be collected

### 2. Integrate Heartbeats into Other Workers

**Status:** Only `company_research_runner.py` has heartbeats integrated

**Remaining:**
- `lead_list_runner.py` - needs heartbeat integration
- `contact_research_runner.py` - needs heartbeat integration

**Pattern to apply:**
Same pattern as company_research_runner (see lines 22, 72-90, 138-139, 164-170, 184, 187, 199)

### 3. Dead Worker Cleanup Daemon

**Need:** Background process that periodically cleans up dead workers

**Options:**

**Option A: Separate Python script (recommended)**
```python
# src/rv_agentic/workers/heartbeat_monitor.py
import time
from rv_agentic.services import heartbeat

while True:
    released = heartbeat.cleanup_dead_workers()
    if released > 0:
        print(f"Released {released} leases from dead workers")
    time.sleep(60)  # Run every minute
```

**Option B: Cron job**
```bash
# Run every minute via cron
* * * * * python -c "from rv_agentic.services.heartbeat import cleanup_dead_workers; cleanup_dead_workers()"
```

**Option C: Integrate into existing workers**
- Each worker checks for dead workers before processing
- Pro: No additional process needed
- Con: Cleanup happens less frequently

### 4. UI Integration

**Add to Streamlit UI (`app.py`):**

```python
from rv_agentic.services import heartbeat

# Worker Status Section
st.header("Worker Health")
health = heartbeat.get_worker_health_summary()

st.metric("Active Workers", health["total_active_workers"])
st.metric("Dead Workers", health["total_dead_workers"],
          delta_color="inverse")

if health["dead_workers"]:
    st.warning(f"{len(health['dead_workers'])} worker(s) appear to be dead")
    st.dataframe(health["dead_workers"])

# Worker Stats Table
st.subheader("Workers by Type")
st.dataframe(health["stats_by_type"])
```

### 5. Testing

**Need tests for:**
- WorkerHeartbeat class
- Database functions
- Integration with workers
- Dead worker cleanup

---

## Files Changed

### Created
- ✅ `sql/migrations/003_worker_heartbeats.sql` (200 lines)
- ✅ `src/rv_agentic/services/heartbeat.py` (261 lines)

### Modified
- ✅ `src/rv_agentic/services/supabase_client.py` (+213 lines, heartbeat functions)
- ✅ `src/rv_agentic/workers/company_research_runner.py` (+32 lines, heartbeat integration)

**Total Lines Added:** ~706 lines
**Code Lines:** ~474 lines
**SQL Lines:** ~200 lines
**Documentation:** ~32 lines

---

## Configuration

### New Environment Variables

```bash
# Heartbeat interval (how often to send heartbeats)
WORKER_HEARTBEAT_INTERVAL=30  # seconds (default: 30)

# Worker identification (optional, auto-generated if not set)
COMPANY_RESEARCH_WORKER_ID=company-research-prod-1
CONTACT_RESEARCH_WORKER_ID=contact-research-prod-1
LEAD_LIST_WORKER_ID=lead-list-prod-1
```

---

## Next Steps (Priority Order)

### 1. ⚠️ CRITICAL: Run Database Migration

**Command:**
```bash
psql $POSTGRES_URL -f sql/migrations/003_worker_heartbeats.sql
```

**Verify:**
```sql
-- Check table exists
SELECT * FROM pm_pipeline.worker_heartbeats LIMIT 1;

-- Check views exist
SELECT * FROM pm_pipeline.v_active_workers;
SELECT * FROM pm_pipeline.v_dead_workers;

-- Check functions exist
SELECT pm_pipeline.get_worker_stats();
```

### 2. Test Company Research Worker with Heartbeats

**Command:**
```bash
# Set env for testing
export WORKER_MAX_LOOPS=2
export LOG_LEVEL=DEBUG

# Run worker
python -m rv_agentic.workers.company_research_runner
```

**Expected Output:**
```
INFO: Company research worker starting up worker_id=company-research-xxx
INFO: Started heartbeat thread for worker company-research-xxx (interval: 30s)
DEBUG: Heartbeat sent for worker company-research-xxx (status=idle)
```

**Verify in Database:**
```sql
SELECT * FROM pm_pipeline.worker_heartbeats
WHERE worker_type = 'company_research'
ORDER BY last_heartbeat_at DESC;
```

### 3. Integrate Heartbeats into Other Workers

Apply same pattern to:
- `lead_list_runner.py`
- `contact_research_runner.py`

### 4. Create Dead Worker Cleanup Process

Create `src/rv_agentic/workers/heartbeat_monitor.py`:
```python
import logging
import time
from rv_agentic.services import heartbeat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting heartbeat monitor")
    while True:
        try:
            released = heartbeat.cleanup_dead_workers()
            if released > 0:
                logger.info("Released %d leases from dead workers", released)
        except Exception as e:
            logger.error("Failed to cleanup dead workers: %s", e)
        time.sleep(60)

if __name__ == "__main__":
    main()
```

### 5. Add Worker Status to UI

Add worker health section to `app.py`

### 6. Create Tests

Write tests for heartbeat system

---

## Expected Impact

### Before Phase 2.2

| Scenario | Outcome | Recovery Time |
|----------|---------|---------------|
| Worker crash during processing | Lease held until expiry | 5 minutes (lease timeout) |
| Worker crash with no monitoring | Silent failure | Unknown until manual check |
| Multiple workers crash | Leases accumulate | 5-30 minutes per lease |

### After Phase 2.2

| Scenario | Outcome | Recovery Time |
|----------|---------|---------------|
| Worker crash during processing | Detected within 5 minutes | 1-2 minutes (cleanup cycle) |
| Worker crash with monitoring | Immediate visibility in UI | Real-time alert |
| Multiple workers crash | Auto-cleanup within 1 minute | 1-2 minutes total |

**Key Improvements:**
- **Detection Time:** Unknown → 5 minutes
- **Recovery Time:** 5-30 minutes → 1-2 minutes
- **Visibility:** None → Real-time dashboard
- **Manual Intervention:** Required → Optional

---

## Monitoring Queries

### Check Active Workers
```sql
SELECT
    worker_id,
    worker_type,
    status,
    current_run_id,
    current_task,
    ROUND(seconds_since_heartbeat::numeric, 1) as seconds_ago
FROM pm_pipeline.v_active_workers
ORDER BY worker_type, worker_id;
```

### Find Dead Workers
```sql
SELECT
    worker_id,
    worker_type,
    last_heartbeat_at,
    current_run_id,
    current_task,
    ROUND(seconds_since_heartbeat::numeric / 60, 1) as minutes_ago
FROM pm_pipeline.v_dead_workers;
```

### Worker Statistics
```sql
SELECT * FROM pm_pipeline.get_worker_stats();
```

### Cleanup Old Workers
```sql
SELECT * FROM pm_pipeline.cleanup_stale_workers(60);  -- 60 minutes threshold
```

---

## Phase 2.2 Sign-Off

**Implementation Status:**
- ✅ Database schema designed (200 lines SQL)
- ✅ Supabase client functions added (213 lines)
- ✅ Heartbeat manager created (261 lines)
- ✅ Example integration complete (company_research_runner)

**Blocked On:**
- ⏳ Database migration needs to be run
- ⏳ Other workers need heartbeat integration
- ⏳ Dead worker cleanup daemon needed
- ⏳ UI integration needed
- ⏳ Tests needed

**Ready to Proceed:**
Once database migration is run, we can:
1. Test company_research_worker with heartbeats
2. Integrate into other workers
3. Add cleanup daemon
4. Move to Phase 2.3 (Lead List Batching)

**Estimated Time to Complete Phase 2.2:**
- Migration: 5 minutes
- Testing: 15 minutes
- Integrate other workers: 30 minutes
- Cleanup daemon: 15 minutes
- UI integration: 20 minutes
- Tests: 1 hour
- **Total: ~2-3 hours**

---

**Next Action:** Run database migration, then test company_research_worker
