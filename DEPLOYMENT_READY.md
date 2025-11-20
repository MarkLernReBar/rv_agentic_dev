# Structured Output Fix - Ready for EC2 Deployment

## Status: ✅ READY FOR PRODUCTION TESTING

**Date:** 2025-11-19
**Fix Commit:** `3e71266` - "Fix: Add explicit structured output population instructions"

---

## Problem Fixed

**Root Cause:** Agent was calling MCP tools (fetch_page, extract_company_profile, get_contacts) successfully but returning **empty structured output** (LeadListOutput with 0 companies, 0 contacts).

**Why:** gpt-5-mini needs explicit step-by-step instructions on HOW to populate Pydantic structured outputs from tool responses.

---

## Solution Implemented

**File:** `src/rv_agentic/agents/lead_list_agent.py` (lines 161-204)

Added comprehensive section: **"CRITICAL: Populating Structured Output (Worker Mode)"**

Key additions:
1. Step-by-step extraction instructions for each tool type:
   - fetch_page → extract companies
   - extract_company_profile_url_ → extract company details
   - search_web → identify and extract companies
   - get_contacts → extract decision makers

2. Concrete code examples of LeadListCompany and LeadListContact objects

3. Clear warning: **"Empty companies array = FAILURE"**

4. Metadata guidance (total_found, search_exhausted)

---

## EC2 Deployment Steps

```bash
# 1. SSH to EC2
ssh -i ~/.ssh/your-key.pem ec2-user@your-ec2-instance

# 2. Navigate to project
cd /path/to/RV_Agentic_FrontEnd_Dev

# 3. Pull latest code
git pull origin main

# 4. Verify fix is present
grep -n "CRITICAL: Populating Structured Output" src/rv_agentic/agents/lead_list_agent.py
# Should return: 161:## CRITICAL: Populating Structured Output (Worker Mode)

# 5. Kill old workers
pkill -9 -f lead_list_runner

# 6. Start fresh worker
.venv/bin/python -m rv_agentic.workers.lead_list_runner > .lead_list_worker.log 2>&1 &
echo "Worker PID: $!"

# 7. Monitor worker startup
tail -f .lead_list_worker.log | grep -E "(Starting|worker_id)" | head -3
```

---

## Validation Test

### Option 1: Via Streamlit UI (Recommended)
```bash
# Start Streamlit on EC2
streamlit run app.py --server.port 8501 &

# Access via browser: http://your-ec2-ip:8501
# Submit minimal test:
#   - Location: San Francisco, CA
#   - PMS: Buildium
#   - Quantity: 3 companies
#   - Contacts: 1-2 per company

# Monitor worker
tail -f .lead_list_worker.log | grep -E "(Completed parallel region|companies found)"
```

### Option 2: Via Direct Database Insert
```bash
# Use the run_full_test.py pattern
.venv/bin/python run_full_test.py

# This creates a test run with criteria that forces agent MCP discovery
# (clears seeding to ensure agent is called)
```

---

## Success Criteria

### ✅ Pass Criteria:
1. Agent calls fetch_page tool (30+ times visible in logs)
2. Agent calls extract_company_profile, get_contacts
3. **LeadListOutput populated with companies.length > 0**
4. Companies inserted into `pm_pipeline.company_candidates` with `discovery_source='agent_structured'`
5. Run completes with status='completed' or advances to company_research stage

### ❌ Fail Criteria:
1. Agent calls tools but returns 0 companies (same as before fix)
2. Run fails with error
3. Worker times out (>15 min per region)

---

## Monitoring Commands

```bash
# Real-time progress
tail -f .lead_list_worker.log | grep -E "(Starting parallel region|Completed parallel region|companies found)"

# Check for fetch_page usage (should see 30+)
grep "fetch_page" .lead_list_worker.log | wc -l

# Check for agent_structured companies (this is the fix validation)
.venv/bin/python debug_runs.py | grep -A 20 "GAP ANALYSIS"

# Query database directly
.venv/bin/python -c "
from rv_agentic.services.supabase_client import _pg_conn
with _pg_conn() as conn:
    with conn.cursor() as cur:
        cur.execute('''
            SELECT run_id, COUNT(*) as companies,
                   COUNT(CASE WHEN discovery_source LIKE 'agent_structured%%' THEN 1 END) as agent_discovered
            FROM pm_pipeline.company_candidates
            WHERE created_at > NOW() - INTERVAL '1 hour'
            GROUP BY run_id
            ORDER BY MAX(created_at) DESC
            LIMIT 5
        ''')
        for row in cur.fetchall():
            print(f'Run: {row[0]}, Companies: {row[1]}, Agent-discovered: {row[2]}')
"
```

---

## Expected Timeline

- **Seeding fast-path:** <1 minute (if PMS matches database)
- **Agent MCP discovery:** 5-10 minutes per region × 4 regions = 20-40 minutes
- **With retries:** Up to 90 minutes max (if regions fail and retry)

---

## Rollback Plan

If the fix doesn't work:

```bash
# Revert to previous commit
git checkout 536d8ec  # (before fix)

# Restart worker
pkill -9 -f lead_list_runner
.venv/bin/python -m rv_agentic.workers.lead_list_runner > .lead_list_worker.log 2>&1 &
```

---

## Related Fixes Already Deployed

These fixes were deployed in previous commits and are working:

1. **fetch_page tool** (commit d952965) - Agent can now read company list pages ✅
2. **Turn limit increased to 100** (commit d952965) - Agent can complete full 20+ search strategy ✅
3. **Region timeouts (15 min)** (commit d952965) - Prevents hangs ✅
4. **Retry logic (3 attempts)** (commit d952965) - Recovers from transient failures ✅
5. **LangSearch_API enhancement** (commit 536d8ec) - Better company enrichment ✅

---

## Next Steps After Successful Validation

1. Monitor first production run end-to-end
2. Verify agent_structured companies have good quality (PMS detected, contact info, etc.)
3. Check contacts_min_gap closes (contacts are discovered for each company)
4. Update RESILIENCE_FIXES.md with validation results
5. Consider tuning discovery_target oversample factor if needed

---

## Key Files Changed

- `src/rv_agentic/agents/lead_list_agent.py` - Added structured output instructions (lines 161-204)

---

**Deployed By:** Claude Code
**Commit:** 3e71266
**GitHub:** https://github.com/MarkLernReBar/rv_agentic_dev/commit/3e71266
