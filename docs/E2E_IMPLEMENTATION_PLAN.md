# End-to-End Pipeline Implementation Plan

**Date**: 2025-11-20
**Goal**: Ensure complete end-to-end functionality with all required fields, no MCP deluge, and proper delivery

## Current Status Analysis

### ✅ What's Working
1. **Company Discovery** - ReAct pattern, single-region, fetch_page strategy (40x faster)
2. **MCP Cleanup** - `reset_mcp_counters()` + 0.3s sleep in place
3. **Company Research** - Agent generates markdown "agent_summary"
4. **Email System** - `notifications.py` exists and functional
5. **CSV Export** - `export.py` framework exists

### ⚠️ What Needs Fixing

#### 1. Export Missing Required Fields

**Company CSV Missing:**
- ❌ `agent_summary` - Exists in company_research table but not exported
- ❌ `property_mix` - Likely in facts JSON
- ❌ `states_of_operations` - Likely in facts JSON

**Contact CSV Missing:**
- ❌ `personal_anecdotes` (3 required)
- ❌ `professional_anecdotes` (3 required)
- ❌ `agent_summary` - Need to verify if contact researcher generates this
- ❌ `data_sources`
- ❌ `additional_research_notes`
- ❌ `icp_score`

#### 2. MCP Deluge Prevention

**Current Implementation:**
```python
finally:
    mcp_client.reset_mcp_counters()
    time.sleep(0.3)
```

**Potential Issues:**
- OpenAI Agents SDK spawns background tasks
- `Runner.run_sync()` may not await all async operations
- Need more aggressive cleanup or timeout enforcement

**Required Actions:**
1. Verify all async tasks are properly awaited
2. Add explicit session termination
3. Consider adding global timeout
4. Monitor for orphaned tasks after completion

#### 3. Pipeline Completion Flow

**Current Gap:** No clear implementation of:
1. CSV export triggered on `stage='done'`
2. Email notification sent with CSV attachments
3. UI updated with download links

**Required Flow:**
```
stage='done' + status='completed'
  ↓
Export CSVs (companies.csv + contacts.csv)
  ↓
Send email notification with attachments
  ↓
Update run record with export_urls
  ↓
UI displays download links
```

#### 4. Contact Researcher Verification

**Need to verify:**
- Does contact researcher generate personal/professional anecdotes?
- Does it generate agent_summary?
- Are these stored in contact_candidates or separate table?

## Implementation Tasks

### Task 0: Add Email Input Validation to UI (CRITICAL)
**Priority**: CRITICAL - MUST BE FIRST
**Files**: `app.py`

**Requirement**: The task MUST NOT start until a notification email is provided.

**Actions:**
1. Add `notification_email` text input field in Lead List Generator section
2. Add email validation (regex pattern)
3. Prevent run creation if email is not provided
4. Store email in `pm_pipeline.runs.criteria` JSON
5. Display clear message: "Please provide notification email to receive results"

**Implementation:**
```python
# In Lead List Generator section (around line 1150)
notification_email = st.text_input(
    "📧 Notification Email (Required)",
    placeholder="your@email.com",
    help="You'll receive the final CSV files at this email when the run completes",
    key="lead_list_notification_email"
)

# Validate email format
import re
email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
email_valid = bool(notification_email and re.match(email_pattern, notification_email))

if not email_valid:
    st.warning("⚠️ Please provide a valid email address to receive your results")

# Disable submit button if no valid email
if st.button("Submit", disabled=not email_valid):
    # ... existing code ...

    # Add email to criteria
    criteria = {
        # ... existing criteria ...
        "notification_email": notification_email,
    }

    pm_run = _sb.create_pm_run(
        criteria=criteria,
        target_quantity=requested_qty,
    )
```

### Task 1: Verify Contact Researcher Output
**Priority**: HIGH
**Files**: `src/rv_agentic/agents/contact_researcher_agent.py`

**Actions:**
1. Read contact researcher system prompt
2. Identify what fields it currently generates
3. Determine where data is stored (contact_candidates.evidence?)
4. Identify gaps vs. requirements

**Requirements:**
- 3 personal anecdotes
- 3 professional anecdotes
- Agent summary (markdown)
- Data sources list
- All standard fields (name, email, title, etc.)

### Task 2: Update Company CSV Export
**Priority**: HIGH
**Files**: `src/rv_agentic/services/export.py`

**Actions:**
1. Add `agent_summary` field
   - Source: company_research table (full markdown output from agent)
2. Add `property_mix` field
   - Source: company_research.facts['property_mix']
3. Add `states_of_operations` field
   - Source: company_research.facts['states_of_operations']

**Implementation:**
```python
# Fetch company_research with full output
research_rows = supabase_client._get_pm(
    supabase_client.PM_COMPANY_RESEARCH_TABLE,
    {
        "run_id": f"eq.{run_id}",
        "select": "company_id,facts,signals,output",  # ADD output field
    },
)

# In CSV row construction:
row = {
    ...
    "agent_summary": research.get("output") or "",  # Full markdown from agent
    "property_mix": facts.get("property_mix") or "",
    "states_of_operations": facts.get("states_of_operations") or "",
}
```

### Task 3: Update Contact CSV Export
**Priority**: HIGH
**Files**: `src/rv_agentic/services/export.py`

**Dependencies**: Task 1 (verify where data is stored)

**Actions:**
1. Identify where anecdotes are stored
2. Add personal_anecdotes field (comma-separated or JSON)
3. Add professional_anecdotes field
4. Add agent_summary field
5. Add data_sources field
6. Add additional_research_notes field
7. Add icp_score field

### Task 4: Strengthen MCP Cleanup
**Priority**: HIGH
**Files**: `src/rv_agentic/workers/lead_list_runner.py`, `company_research_runner.py`, `contact_research_runner.py`

**Actions:**
1. Add explicit timeout to all `Runner.run_sync()` calls
2. Add try/finally to ensure cleanup even on timeout
3. Consider adding global session manager
4. Add logging to track active MCP sessions

**Implementation:**
```python
try:
    result = Runner.run_sync(
        agent,
        prompt=prompt,
        max_turns=30,
        timeout=600,  # 10 minute hard limit
    )
finally:
    # CRITICAL: Always cleanup
    from rv_agentic.tools import mcp_client
    mcp_client.reset_mcp_counters()

    # Force garbage collection
    import gc
    gc.collect()

    # Longer pause for cleanup
    import time
    time.sleep(1.0)
```

### Task 5: Implement Completion Flow
**Priority**: HIGH
**Files**: `src/rv_agentic/workers/lead_list_runner.py` or new `completion_handler.py`

**Actions:**
1. Detect when run reaches `stage='done'` + `status='completed'`
2. Call `export.export_run_to_files(run_id, output_dir)`
3. Read CSV files as bytes
4. Extract notification_email from `run.criteria['notification_email']`
5. Call `notifications.send_run_notification()` with:
   - `to_email=notification_email` (from criteria)
   - `subject=f"Lead List Complete: {quantity} companies ready"`
   - `body=<summary of results>`
   - `attachments=[(companies_csv, contacts_csv)]`
6. Update run record with export metadata
7. Log completion for UI to display

**Implementation Location:**
- Option A: Add to end of `lead_list_runner.py` main loop
- Option B: Create new `completion_handler.py` called by all workers
- Option C: Add to `_supabase_mark_run_complete()` function

**Preferred**: Option A (simplest, already in the right place)

### Task 6: Update UI to Show Download Links
**Priority**: MEDIUM
**Files**: `app.py`

**Actions:**
1. Check for export_urls in run record
2. Display download buttons when available
3. Generate presigned URLs if using cloud storage
4. Or serve files directly from output_dir

## Testing Plan

### Test 1: 5-Company Full Pipeline Test
**Purpose**: Verify all components work together

**Setup:**
```bash
# Create test run via UI or CLI
city: "Boulder"
state: "CO"
pms: "Buildium"
units_min: 50
quantity: 5
```

**Expected Results:**
1. Discovery finds 10 companies (2x oversample)
2. Research enriches all fields including agent_summary
3. Contact discovery finds 1-3 per company
4. CSVs generated with ALL required fields
5. Email sent with attachments
6. UI shows download links
7. NO MCP deluge after completion

**Monitor:**
- MCP session count in n8n
- Agent execution time
- CSV field completeness
- Email delivery
- Total cost/tokens

### Test 2: MCP Cleanup Verification
**Purpose**: Ensure no session leakage

**Actions:**
1. Note MCP session count before test
2. Run 5-company test
3. Wait 5 minutes after completion
4. Check MCP session count
5. Should be back to baseline (or very close)

### Test 3: CSV Field Completeness
**Purpose**: Verify all required fields present

**Actions:**
1. Download both CSVs
2. Check all required company fields present
3. Check all required contact fields present
4. Verify agent_summary is full markdown (not truncated)
5. Verify anecdotes are properly formatted

## Success Criteria

✅ All required company fields in CSV
✅ All required contact fields in CSV
✅ Agent summaries complete and properly formatted
✅ Email sent with both CSV attachments
✅ UI shows download links
✅ No MCP session deluge (< 10 orphaned sessions)
✅ Total execution time < 30 minutes for 5 companies
✅ All agents complete without errors

## Risk Mitigation

**Risk 1**: MCP deluge still occurs
- **Mitigation**: Add more aggressive timeout + cleanup
- **Fallback**: Manual n8n restart procedure

**Risk 2**: Required fields not in database
- **Mitigation**: Update agent prompts to generate missing fields
- **Fallback**: Mark as "Unknown" in CSV, document gaps

**Risk 3**: Export fails silently
- **Mitigation**: Add comprehensive logging
- **Fallback**: Manual export via scripts/monitoring/status_report.py

## Next Steps

1. ✅ Create this implementation plan
2. ⏳ Task 1: Verify contact researcher output
3. ⏳ Task 2: Update company CSV export
4. ⏳ Task 3: Update contact CSV export
5. ⏳ Task 4: Strengthen MCP cleanup
6. ⏳ Task 5: Implement completion flow
7. ⏳ Task 6: Update UI
8. ⏳ Test 1: 5-company full pipeline test

## Notes

- All changes should be backwards compatible
- Add comprehensive logging at each step
- Document any assumptions or limitations
- Keep original functionality intact while adding new features
