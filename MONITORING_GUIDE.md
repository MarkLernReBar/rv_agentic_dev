# Pipeline Monitoring Guide

## Current Worker Status

✅ **Worker Active**: PID 56718
📊 **Processing Run**: `f42a2c51-61b9-43b5-9caa-926bca70b7c6`
🎯 **Target**: 20 companies (discovering 40 with 2x oversample)

---

## Quick Monitoring Commands

### 1. Real-Time Log Monitoring (RECOMMENDED)

```bash
# Watch filtered logs (key events only)
tail -f .lead_list_worker.log | grep -E "(Starting|Completed|Inserted|ERROR|companies found|parallel region)"
```

**What to look for:**
- ✅ `Starting parallel region X/4: Region Y` - Region discovery starting
- ✅ `Completed parallel region X/4: found N companies` - Region completed successfully
- ✅ `Inserted company` - Company added to database
- ❌ `ERROR` - Problems that need attention
- ❌ `Max turns (10) exceeded` - Agent hit turn limit (CRITICAL ISSUE)

### 2. Database Status Check

```bash
# Check progress in database
.venv/bin/python debug_runs.py
```

**What to look for:**
- Companies discovered count (should increase over time)
- Gap analysis (discovery_gap should decrease)
- Worker status (should show active workers)

### 3. Full Real-Time Monitor (Auto-refresh)

```bash
# Watch everything with auto-refresh every 5 seconds
watch -n 5 '.venv/bin/python debug_runs.py'
```

### 4. Check Worker Process Health

```bash
# Verify worker is running
ps aux | grep lead_list_runner | grep -v grep

# Check worker PID and memory/CPU usage
ps aux | grep 56718
```

### 5. View All Logs (Unfiltered)

```bash
# See everything (lots of HTTP requests)
tail -f .lead_list_worker.log
```

---

## Pipeline Stages to Watch

### Stage 1: Company Discovery (Current Stage)

**Log patterns you'll see:**
```
[INFO] __main__: Starting parallel region 1/4: Region 1
[INFO] rv_agentic.tools.mcp_client: MCP call start: tool=search_web
[INFO] __main__: Completed parallel region 1/4: found 12 companies
```

**Success indicators:**
- All 4 regions complete without errors
- Total companies discovered >= discovery_target (40)
- No "Max turns exceeded" errors

**Database changes:**
- `pm_pipeline.company_candidates` gets new rows
- Run `stage` advances to `company_research`

### Stage 2: Company Research

**Log patterns:**
```
[INFO] __main__: Company research worker processing run...
[INFO] __main__: Enriching company: example.com
[INFO] __main__: Inserted company research for: example.com
```

**Success indicators:**
- All companies get research records
- Facts and signals populated
- Run advances to `contact_discovery`

### Stage 3: Contact Discovery

**Log patterns:**
```
[INFO] __main__: Contact research worker processing run...
[INFO] __main__: Found 2 contacts for company: example.com
[INFO] __main__: Inserted contact: john@example.com
```

**Success indicators:**
- Each company gets 1-3 contacts
- Contact gap reaches 0
- Run status becomes `done`

---

## Common Issues and What They Mean

### ❌ "Max turns (10) exceeded"

**Meaning:** The agent is stuck in a tool-calling loop and hit the conversation turn limit.

**Why it happens:**
- Agent calls a tool
- Tool returns results
- Agent calls another tool
- Repeats >10 times without finishing

**Impact:** Region discovery fails, no companies added from that region

**Fix:** Need to optimize agent prompt or increase turn limit in code

### ❌ "No active runs found; sleeping"

**Meaning:** Worker is idle, waiting for new tasks

**Why it happens:**
- All runs are completed/archived
- No new runs submitted
- Current run advanced to next stage (different worker handles it)

**Impact:** None - this is normal when idle

**Fix:** Submit a new lead list task from Streamlit UI

### ❌ Worker process not found

**Meaning:** Worker crashed or was stopped

**Why it happens:**
- Out of memory
- Unhandled exception
- Manual kill

**Impact:** No tasks will be processed

**Fix:** Restart worker:
```bash
.venv/bin/python -m rv_agentic.workers.lead_list_runner > .lead_list_worker.log 2>&1 &
```

### ⚠️ "OpenAI.agents: Error cleaning up server"

**Meaning:** Async cleanup issue in OpenAI Agents SDK

**Impact:** Cosmetic - doesn't affect functionality

**Fix:** None needed (SDK issue, not your code)

---

## Real-Time Monitoring Example Session

```bash
# Terminal 1: Watch logs
tail -f .lead_list_worker.log | grep -E "(Starting|Completed|Inserted|ERROR)"

# Terminal 2: Auto-refresh database status
watch -n 5 '.venv/bin/python debug_runs.py'

# Terminal 3: Check worker health periodically
ps aux | grep lead_list_runner | grep -v grep
```

---

## Key Metrics to Track

| Metric | Command | Good Value |
|--------|---------|------------|
| Companies discovered | `debug_runs.py` | Increasing toward target |
| Discovery gap | `debug_runs.py` | Decreasing toward 0 |
| Active workers | `debug_runs.py` | >= 1 |
| Dead workers | `debug_runs.py` | 0 |
| Error count | `grep ERROR .lead_list_worker.log \| wc -l` | Low/decreasing |
| Max turns errors | `grep "Max turns" .lead_list_worker.log \| wc -l` | 0 |

---

## Emergency Commands

```bash
# Stop worker
pkill -f lead_list_runner

# Force kill worker
pkill -9 -f lead_list_runner

# Restart worker
.venv/bin/python -m rv_agentic.workers.lead_list_runner > .lead_list_worker.log 2>&1 &

# Archive stuck run (replace RUN_ID)
psql $POSTGRES_URL -c "UPDATE pm_pipeline.runs SET status='archived' WHERE id='RUN_ID';"

# Check database connection
psql $POSTGRES_URL -c "SELECT 1;"
```

---

## Current Known Issue: Max Turns Limit

**Problem:** The Lead List Agent is hitting the 10-turn conversation limit when discovering companies.

**Symptoms:**
- Regions fail with "Max turns (10) exceeded"
- No companies inserted for failed regions
- Run gets stuck in `company_discovery` stage

**Temporary Workaround:**
- Restart worker to retry (it will attempt discovery again)
- Monitor closely and kill/restart if regions keep failing

**Permanent Fix Needed:**
- Increase agent turn limit in code (see `src/rv_agentic/agents/lead_list_agent.py`)
- Optimize agent prompt to use fewer tool calls
- Add turn count logging to identify which tools are causing loops

---

## Success Pattern Example

```
[INFO] __main__: Starting parallel region 1/4: Region 1
... [agent makes MCP calls] ...
[INFO] __main__: Completed parallel region 1/4: found 12 companies, 15 contacts
[INFO] __main__: Inserted 12 companies for region: Region 1

[INFO] __main__: Starting parallel region 2/4: Region 2
... [agent makes MCP calls] ...
[INFO] __main__: Completed parallel region 2/4: found 10 companies, 18 contacts
[INFO] __main__: Inserted 10 companies for region: Region 2

... [regions 3 and 4] ...

[INFO] __main__: All regions complete: total=45 companies (target=40, overage will be trimmed)
[INFO] __main__: Trimmed to best 40 companies by quality score
[INFO] __main__: Advancing run to stage: company_research
```
