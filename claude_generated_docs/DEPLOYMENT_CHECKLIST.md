# Phase 1 Deployment Checklist

**Date:** 2025-01-17
**Target:** Production deployment for 5-10 company batches

---

## Pre-Deployment Verification

### 1. Environment Configuration ✓
- [x] `.env.local` exists with all required variables
- [x] `POSTGRES_URL` configured
- [x] `OPENAI_API_KEY` configured
- [x] `SUPABASE_SERVICE_KEY` configured
- [x] `HUBSPOT_PRIVATE_APP_TOKEN` configured
- [x] `N8N_MCP_SERVER_URL` configured

### 2. Database Connectivity ✓
- [ ] Can connect to PostgreSQL/Supabase
- [ ] `pm_pipeline.*` tables exist
- [ ] Gap views are accessible
- [ ] Can insert test company
- [ ] Can query runs table

### 3. Worker Startup ✓
- [ ] `lead_list_runner` starts without errors
- [ ] `company_research_runner` starts without errors
- [ ] `contact_research_runner` starts without errors
- [ ] All workers can poll database
- [ ] Workers can claim items with leases

### 4. UI Functionality ✓
- [ ] Streamlit app starts
- [ ] Can create test run
- [ ] Progress tracking displays correctly
- [ ] CSV download button appears for completed runs
- [ ] CSV files download successfully

---

## Deployment Steps

### Phase 1: Verification (10 minutes)

**Step 1.1: Test Database Connection**
```bash
python -c "
from dotenv import load_dotenv; load_dotenv('.env.local')
from rv_agentic.services import supabase_client
run = supabase_client.create_pm_run(criteria={'test': 'deployment'}, target_quantity=1)
print(f'✅ Database connected. Test run: {run[\"id\"]}')
"
```

**Step 1.2: Start Workers (in separate terminals)**
```bash
# Terminal 1
python -m rv_agentic.workers.lead_list_runner

# Terminal 2
python -m rv_agentic.workers.company_research_runner

# Terminal 3
python -m rv_agentic.workers.contact_research_runner
```

**Step 1.3: Start UI**
```bash
# Terminal 4
streamlit run app.py
```

---

### Phase 2: Smoke Test (5 minutes)

**Test Scenario:** Create 2-company run with mock simulator

```python
from dotenv import load_dotenv; load_dotenv('.env.local')
from rv_agentic.services import supabase_client
from tests.integration.test_phase1_pipeline import MockWorkerSimulator

# Create run
run = supabase_client.create_pm_run(
    criteria={'pms': 'Buildium', 'state': 'TX'},
    target_quantity=2
)
run_id = str(run['id'])
print(f'Created run: {run_id}')

# Simulate completion
simulator = MockWorkerSimulator(run_id)
simulator.simulate_company_discovery(num_companies=2)
simulator.simulate_company_research()
simulator.simulate_contact_discovery(contacts_per_company=2)

# Export CSVs
from rv_agentic.services import export
companies_csv = export.export_companies_to_csv(run_id)
contacts_csv = export.export_contacts_to_csv(run_id)

print(f'✅ Smoke test passed. {len(companies_csv)} chars in companies CSV')
print(f'✅ Run ID for UI test: {run_id}')
```

**UI Test:**
1. Open Streamlit at http://localhost:8501
2. Go to "Lead List Generator" tab
3. Paste run ID from smoke test
4. Click "Check Status"
5. Verify progress bars show 100%
6. Click "Download CSVs"
7. Verify both CSVs download correctly

---

### Phase 3: Production Readiness (2 minutes)

**Final Checks:**
- [ ] All 4 processes running (3 workers + UI)
- [ ] No error messages in worker logs
- [ ] UI accessible via browser
- [ ] Smoke test run shows as completed
- [ ] CSV export works

**Sign-Off:**
- [ ] Deployment verified by: _________
- [ ] Date/Time: _________
- [ ] Ready for production use: YES / NO

---

## Rollback Plan

If deployment fails:

1. **Stop all workers:**
   ```bash
   pkill -f lead_list_runner
   pkill -f company_research_runner
   pkill -f contact_research_runner
   ```

2. **Check logs for errors:**
   ```bash
   tail -100 worker_logs.txt
   ```

3. **Verify database state:**
   ```bash
   psql $POSTGRES_URL -c "SELECT id, status, stage FROM pm_pipeline.runs ORDER BY created_at DESC LIMIT 5;"
   ```

4. **Reset test run if needed:**
   ```python
   from rv_agentic.services import supabase_client
   supabase_client.update_pm_run_status(run_id='<run_id>', status='error')
   ```

---

## Post-Deployment Monitoring

**For First Production Run:**

1. Monitor worker logs in real-time:
   ```bash
   tail -f lead_list_runner.log
   tail -f company_research_runner.log
   tail -f contact_research_runner.log
   ```

2. Check run status every 30 seconds:
   ```bash
   watch -n 30 'python -c "from rv_agentic import orchestrator; print(orchestrator.get_run_progress(\"<run_id>\"))"'
   ```

3. Expected timeline for 5 companies:
   - 0-1 min: Company discovery
   - 1-3 min: Company research
   - 3-5 min: Contact discovery
   - 5 min: Run completed, CSVs available

---

## Known Issues & Workarounds

1. **Workers not claiming items:**
   - Check `worker_lease_until` timestamps
   - May need to manually release leases: `UPDATE pm_pipeline.company_candidates SET worker_lease_until = NULL`

2. **Progress query slow (>500ms):**
   - Expected for gap views
   - Don't poll more than once per 10 seconds

3. **CSV export fails:**
   - Check run status is `completed`
   - Verify companies have `status='validated'` or `status='promoted'`

---

## Success Criteria

✅ **Deployment Successful If:**
- All workers start without errors
- UI loads and displays correctly
- Smoke test completes in < 10 seconds
- CSV export produces valid files
- No database connection errors

🎯 **Ready for First Production Run**
