# Real Production Testing Guide

**Purpose:** This guide explains how to run **real end-to-end tests** that prove the system works in production, not just mocked unit tests.

---

## Why Real Tests Matter

### Mocked Tests (51 passing) Show:
- ✅ Code doesn't crash
- ✅ Logic is correct in isolation
- ✅ Functions have right signatures

### Real Tests Show:
- ✅ **Agents actually find companies**
- ✅ **Database operations work**
- ✅ **Batching improves performance**
- ✅ **Heartbeat system tracks workers**
- ✅ **Retry logic catches real failures**
- ✅ **Full pipeline succeeds end-to-end**

---

## Test Suite Overview

### Test 1: Database Integration (Fast, ~10 seconds)
**File:** `tests/integration/test_real_25_company_run.py::test_database_heartbeat_integration`

**What it tests:**
- Real database connection
- Heartbeat upsert operations
- Active/dead worker detection
- Worker statistics
- Stop worker functionality

**Cost:** Free (no API calls)

### Test 2: Full 25-Company Run (Slow, ~12-15 minutes)
**File:** `tests/integration/test_real_25_company_run.py::test_real_25_company_run_with_batching`

**What it tests:**
- Real agent calls with OpenAI API
- Real MCP tool usage
- Batch processing (3 batches: 10+10+5)
- Checkpoint tracking
- Progress monitoring
- Success rate (target: ≥90%)

**Cost:** ~$0.50-$1.00 in OpenAI API credits

---

## Prerequisites

### 1. Database Access
```bash
# Required in .env.local or environment
POSTGRES_URL=postgresql://user:pass@host:5432/dbname
```

### 2. OpenAI API Key
```bash
# Required for agent tests
OPENAI_API_KEY=sk-...
```

### 3. Python Environment
```bash
# Ensure all dependencies installed
pip install -r requirements.txt
```

---

## Running the Tests

### Option 1: Automated Script (Recommended)

The script handles everything: permissions, database test, and optionally the full test.

```bash
./run_real_tests.sh
```

**What it does:**
1. Checks prerequisites (POSTGRES_URL, OPENAI_API_KEY)
2. Grants database permissions
3. Runs database integration test
4. Asks for confirmation before expensive full test
5. Runs 25-company test with real agents
6. Reports results

**Example output:**
```
==================================
REAL PRODUCTION TEST SUITE
==================================

Checking prerequisites...
✅ POSTGRES_URL is set
✅ OPENAI_API_KEY is set

==================================
STEP 1: Database Permissions
==================================

Current database user: postgres
✅ Permissions granted successfully

==================================
STEP 2: Database Integration Test
==================================

Testing heartbeat system with real database...

==================================
DATABASE INTEGRATION TEST: Heartbeat System
==================================
Worker ID: test-integration-a1b2c3d4

[Test 1] Upserting worker heartbeat...
✅ Heartbeat upserted successfully

[Test 2] Getting active workers...
✅ Worker found in active workers (3 total)

[Test 3] Getting worker stats...
✅ Worker stats retrieved (3 types)

[Test 4] Stopping worker...
✅ Worker stopped successfully

[Test 5] Verifying worker stopped...
✅ Worker removed from active workers

==================================
✅ ALL HEARTBEAT INTEGRATION TESTS PASSED
==================================

✅ Database integration test PASSED

==================================
STEP 3: Full 25-Company Test
==================================

⚠️  WARNING: The next test will:
   - Run real agents with OpenAI API
   - Take 12-15 minutes
   - Consume API credits (estimated $0.50-$1.00)
   - Create test data in your database

Do you want to proceed? (yes/no): yes

Starting full 25-company test...

==================================
REAL PRODUCTION TEST: 25-Company Run with Batching
==================================
Run ID: e5f6g7h8-i9j0-k1l2-m3n4-o5p6q7r8s9t0
Target: 25 companies
Batch Size: 10
Expected Batches: 3 (10 + 10 + 5)
Expected Time: 12-15 minutes
==================================

[10:30:15] Creating test run in database...
✅ Test run created successfully

[10:30:16] Initializing agent and worker...
✅ Worker started with heartbeat

==================================
BATCH PROCESSING START
==================================

[10:30:17] Batch 1:
  Progress: 0/25 companies
  Remaining: 25
  🔄 Processing batch...
  ✅ Batch complete: Found 10 companies in 245.3s
  Progress: 10/25 companies

[10:34:32] Batch 2:
  Progress: 10/25 companies
  Remaining: 15
  🔄 Processing batch...
  ✅ Batch complete: Found 10 companies in 238.1s
  Progress: 20/25 companies

[10:38:50] Batch 3:
  Progress: 20/25 companies
  Remaining: 5
  🔄 Processing batch...
  ✅ Batch complete: Found 5 companies in 142.7s
  Progress: 25/25 companies

  ✅ Target met!

==================================
BATCH PROCESSING COMPLETE
==================================
Total Batches: 3
Companies Found: 25/25
Success Rate: 100.0%
Total Time: 626.1s (10.4 min)
Avg Time/Batch: 208.7s
==================================

✅ All assertions passed!

Test Summary:
  - Batching: Working correctly (3 batches)
  - Checkpointing: Working (progress tracked after each batch)
  - Success Rate: 100.0% (target: ≥90%)
  - Performance: 10.4 min (target: 12-15 min)

[10:40:57] Cleaning up test run...
✅ Test data cleaned up

==================================
✅ FULL TEST PASSED
==================================
Total time: 640 seconds (10 minutes)

==================================
TEST SUITE COMPLETE
==================================

Results:
  ✅ Database permissions: OK
  ✅ Database integration: PASSED
  ✅ 25-company test: PASSED

Your system is production-ready! 🚀
```

### Option 2: Manual Steps

#### Step 1: Grant Database Permissions

Find your database user:
```bash
psql $POSTGRES_URL -c "SELECT current_user;"
```

Grant permissions (replace `your_user`):
```bash
# Edit sql/migrations/004_grant_heartbeat_permissions.sql
# Replace 'postgres' with your actual user

# Run migration
psql $POSTGRES_URL -f sql/migrations/004_grant_heartbeat_permissions.sql
```

#### Step 2: Run Database Integration Test

```bash
pytest tests/integration/test_real_25_company_run.py::test_database_heartbeat_integration -v -s
```

**Expected:** Test passes, heartbeat operations work

#### Step 3: Run Full 25-Company Test

```bash
pytest tests/integration/test_real_25_company_run.py::test_real_25_company_run_with_batching -v -s
```

**Expected:**
- 3 batches complete
- 24-25 companies found
- 10-15 minutes runtime
- All assertions pass

---

## Interpreting Results

### Success Metrics

| Metric | Target | Good | Excellent |
|--------|--------|------|-----------|
| **Companies Found** | ≥90% (23/25) | 24/25 | 25/25 |
| **Batches** | 3 | 3 | 3 |
| **Time per Batch** | <5 min | 3-4 min | 2-3 min |
| **Total Time** | <15 min | 12-15 min | 10-12 min |
| **Success Rate** | ≥90% | 95% | 100% |

### What Good Results Look Like

```
Total Batches: 3                    ✅ Expected
Companies Found: 25/25              ✅ 100% success
Success Rate: 100.0%                ✅ Above 90% target
Total Time: 626.1s (10.4 min)       ✅ Under 15 min target
Avg Time/Batch: 208.7s              ✅ ~3.5 min per batch
```

### What Bad Results Look Like

```
Total Batches: 5                    ⚠️  Too many batches
Companies Found: 20/25              ⚠️  Only 80% success
Success Rate: 80.0%                 ❌ Below 90% target
Total Time: 1024.3s (17.1 min)      ⚠️  Over 15 min target
Avg Time/Batch: 341.4s              ⚠️  ~5.7 min per batch
```

**If you see bad results:**
- Check agent logs for errors
- Verify MCP tools are working
- Try smaller batch size (5-7)
- Check criteria aren't too restrictive
- Verify retry logic is working

---

## Troubleshooting

### Issue: Permission Denied Errors

**Error:**
```
psycopg.errors.InsufficientPrivilege: permission denied for table worker_heartbeats
```

**Fix:**
```bash
# Check your database user
psql $POSTGRES_URL -c "SELECT current_user;"

# Grant permissions manually
psql $POSTGRES_URL <<EOF
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE pm_pipeline.worker_heartbeats TO your_user;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pm_pipeline TO your_user;
EOF
```

### Issue: Test Times Out

**Error:**
```
Test exceeded max time limit
```

**Possible causes:**
1. Network issues with OpenAI API
2. MCP tools slow or failing
3. Agent getting stuck in reasoning loops
4. Database connection slow

**Fix:**
```bash
# Check API connectivity
curl -H "Authorization: Bearer $OPENAI_API_KEY" https://api.openai.com/v1/models

# Check database connectivity
psql $POSTGRES_URL -c "SELECT 1;"

# Increase timeout (edit test file)
# Or reduce batch size for faster iterations
export LEAD_LIST_BATCH_SIZE=5
```

### Issue: Low Success Rate

**Symptom:** Only finding 15-20 companies instead of 25

**Possible causes:**
1. Criteria too restrictive (rare PMS + small city)
2. Too many blocked domains
3. Agent being too picky with filters

**Fix:**
```bash
# Check blocked domains
psql $POSTGRES_URL -c "SELECT COUNT(*) FROM pm_pipeline.blocked_domains;"

# Review criteria
# Try broader criteria: state instead of city, "any" PMS, etc.

# Check agent logs for rejection reasons
```

### Issue: Batches Not Checkpointing

**Symptom:** Run crashes and starts from scratch

**Possible causes:**
1. Database not committing transactions
2. `get_pm_company_gap` not working
3. Worker not checking existing companies

**Fix:**
```bash
# Verify company candidates are persisted
psql $POSTGRES_URL -c "
SELECT run_id, COUNT(*) as companies
FROM pm_pipeline.company_candidates
GROUP BY run_id;
"

# Check gap function works
psql $POSTGRES_URL -c "
SELECT * FROM pm_pipeline.get_pm_company_gap('<run_id>');
"

# Review worker logs for checkpoint messages
```

---

## Comparison: Mocked vs Real Tests

### Before Real Tests (Mocked Only)

```bash
$ pytest tests/ -v

51 tests passed ✅

Developer: "All tests pass! Ship it!"
```

**Problems:**
- Don't know if agents actually work
- Don't know if batching helps
- Don't know actual performance
- Don't know if database works
- False confidence

### After Real Tests

```bash
$ ./run_real_tests.sh

Database integration: PASSED ✅
25-company test: PASSED ✅
  - 3 batches completed
  - 25/25 companies found
  - 10.4 minutes total
  - 100% success rate

Developer: "Tests prove it works in production. Ship it!"
```

**Benefits:**
- **Proven**: Agents find real companies
- **Validated**: Batching works as designed
- **Measured**: Actual performance known
- **Verified**: Database operations correct
- **Confident**: Production-ready

---

## Next Steps After Passing

### 1. Document Baseline Performance

Save test results as your baseline:

```bash
# Create performance baseline file
cat > PERFORMANCE_BASELINE.md <<EOF
# Performance Baseline

**Date:** $(date +%Y-%m-%d)
**Test:** 25 companies, batch_size=10

## Results
- Batches: 3
- Companies: 25/25 (100%)
- Time: 10.4 min
- Avg/batch: 3.5 min

## System
- Database: PostgreSQL (Supabase)
- Agent: gpt-4-turbo
- Workers: 1 lead_list worker
- Retry: Enabled (3 attempts)
- Heartbeat: 30s interval
EOF
```

### 2. Run Larger Scale Tests

Test with 50 companies:

```bash
# Modify test file to use target_quantity=50
# Expected: 5 batches, 20-25 minutes, 95%+ success rate
```

### 3. Test Worker Crash Recovery

```bash
# Start worker
python -m rv_agentic.workers.lead_list_runner &
WORKER_PID=$!

# Let it run one batch, then kill it
sleep 300  # Wait for first batch
kill -9 $WORKER_PID

# Wait for heartbeat timeout (5 minutes)
sleep 300

# Start new worker
python -m rv_agentic.workers.lead_list_runner

# Verify: Run continues from checkpoint
```

### 4. Stress Test with Multiple Concurrent Runs

```bash
# Create 3 runs simultaneously
# Verify: All complete successfully
# Measure: Total throughput
```

---

## When to Run Real Tests

### Always Run:
- ✅ Before deploying to production
- ✅ After major changes to core logic
- ✅ When adding new features
- ✅ After database schema changes

### Consider Running:
- ⚠️  After dependency updates
- ⚠️  When performance issues reported
- ⚠️  Weekly/monthly health checks

### Don't Need to Run:
- ❌ Every commit (too slow, too expensive)
- ❌ For documentation changes
- ❌ For UI-only changes

---

## Cost Estimation

### Per Test Run:

**25-company test:**
- Agent calls: ~3 (one per batch)
- MCP tool calls: ~75-100 (contacts, company info, etc.)
- Estimated tokens: ~500k input, ~50k output
- Cost: ~$0.50-$1.00

**Database operations:**
- Free (using existing database)

**Total:** ~$0.50-$1.00 per full test run

### Monthly Budget (if running weekly):
- 4 tests/month × $1.00 = **$4.00/month**

Very reasonable for production validation.

---

## Summary

**Mocked tests are necessary but not sufficient.**

To truly validate your system works:

1. ✅ Run mocked tests for fast feedback (seconds)
2. ✅ Run real integration tests for database validation (seconds)
3. ✅ Run full end-to-end tests for production confidence (minutes)

**Use this guide to prove your system is production-ready, not just passing tests.**

---

**Ready to run real tests?**

```bash
./run_real_tests.sh
```

🚀 **Let's prove it works!**
