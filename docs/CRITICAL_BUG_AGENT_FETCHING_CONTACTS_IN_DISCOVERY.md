# CRITICAL BUG: Lead List Agent Fetching Contacts During Discovery

**Date**: 2025-11-20
**Severity**: CRITICAL - Blocks E2E testing
**Status**: BLOCKING ALL TESTS

## Summary

The Lead List Agent is instructed to fetch contacts during the company discovery phase, violating the pipeline's stage separation and causing massive performance degradation and test failures.

## Root Cause

**File**: [src/rv_agentic/agents/lead_list_agent.py](../src/rv_agentic/agents/lead_list_agent.py#L26-L44)

**Lines 39-40 of LEAD_LIST_SYSTEM_PROMPT**:
```python
- Add ALL eligible companies to the `companies` array (focus on quality over quantity).
- Python will select the best N companies from your results, so return them sorted
  by quality/confidence with the strongest matches first.
- Add 1–3 decision maker contacts per company to the `contacts` array.  # ❌ WRONG!
- Set `total_found` to the actual count of companies you discovered.
```

## Pipeline Design (CORRECT)

The pm_pipeline is designed with stage separation:

```
Stage 1: company_discovery
├─ Lead List Agent finds companies
├─ Writes to company_candidates table
└─ NO contact fetching

Stage 2: company_research
├─ Company Researcher Agent enriches companies
├─ Writes to company_research table
└─ Still NO contact fetching

Stage 3: contact_discovery
├─ Contact Researcher Agent finds contacts
├─ Writes to contact_candidates table
└─ ONLY NOW do we fetch contacts
```

## Current Behavior (WRONG)

1. Worker invokes Lead List Agent for company discovery
2. Agent discovers companies from pms_subdomains (e.g., 10 companies)
3. **Agent immediately starts fetching contacts for each company** (get_contacts tool)
4. **Agent calls get_verified_emails and get_linkedin_profile_url for each contact**
5. Agent takes 10+ minutes to complete discovery phase
6. Worker evaluates discovery completion
7. Run proceeds to company_research stage

**Problem**: The agent is doing Stage 3 work (contact fetching) during Stage 1 (discovery)!

## Evidence from Logs

### [.lead_list_worker_FIXED.log](../.lead_list_worker_FIXED.log)

```
2025-11-20 20:26:55,286 [INFO] rv_agentic.tools.mcp_client: MCP call start: tool=get_contacts
    args={'company_name': 'Matrix Real Estate, LLC', 'company_domain': 'matrixrealestate.managebuilding.com', ...}

2025-11-20 20:29:50,508 [INFO] rv_agentic.tools.mcp_client: MCP call start: tool=get_verified_emails
    args={'person_name': 'Amy Alexander', 'company_name': 'Matrix Real Estate, LLC', ...}

2025-11-20 20:31:09,571 [INFO] rv_agentic.tools.mcp_client: MCP call start: tool=get_contacts
    args={'company_name': 'Brass Key Property Management', ...}
```

The agent is calling contact tools **DURING company_discovery stage**.

## Impact

### Performance Impact

- **Discovery should take**: ~30-60 seconds (just finding companies)
- **Discovery actually takes**: 10+ minutes (fetching contacts for all companies)
- **16x-20x slowdown** in discovery phase

### Test Failures

1. **Previous test run** (`0915b268-d820-46a2-aa9b-aa1164701538`):
   - Expected: Agent finds 5-10 companies, returns in 1 minute
   - Actual: Agent found 10 companies, spent 10+ minutes fetching contacts
   - Result: Test killed after 15 minutes due to apparent hang

2. **FIXED worker test**:
   - Same behavior observed
   - Agent fetching contacts instead of just discovering companies
   - Test killed to investigate

### Resource Waste

- Fetching 1-3 contacts per company = 30-90 MCP tool calls
- Each contact fetch: get_contacts + get_verified_emails + get_linkedin_profile_url
- Total: 90-270 API calls during discovery
- **These contacts are NEVER USED** - they're discarded when worker advances to company_research

### Pipeline Correctness

- Contact discovery is a separate stage with its own worker
- Contact Researcher Agent should handle contact fetching
- Lead List Agent fetching contacts violates separation of concerns
- Makes it impossible to resume/retry individual stages

## The Fix

### Option 1: Remove Contact Fetching from Lead List Agent (RECOMMENDED)

**Change lines 39-44** from:
```python
- Add ALL eligible companies to the `companies` array (focus on quality over quantity).
- Python will select the best N companies from your results, so return them sorted
  by quality/confidence with the strongest matches first.
- Add 1–3 decision maker contacts per company to the `contacts` array.  # ❌ REMOVE THIS
- Set `total_found` to the actual count of companies you discovered.
```

To:
```python
- Add ALL eligible companies to the `companies` array (focus on quality over quantity).
- Python will select the best N companies from your results, so return them sorted
  by quality/confidence with the strongest matches first.
- Do NOT fetch contacts during discovery - contacts are fetched by a separate agent in a later stage.
- Set `total_found` to the actual count of companies you discovered.
```

**Also update LeadListOutput schema** to remove or make `contacts` optional with default empty list.

### Option 2: Make Contact Fetching Conditional

Add a parameter to control whether agent fetches contacts:

```python
def invoke_lead_list_agent(..., fetch_contacts: bool = False):
    prompt = LEAD_LIST_SYSTEM_PROMPT
    if not fetch_contacts:
        prompt += "\n\n**IMPORTANT**: Do NOT fetch contacts. Only discover companies."
```

Then in worker:
```python
result = invoke_lead_list_agent(criteria, fetch_contacts=False)
```

## Verification Steps After Fix

1. Update agent prompt to remove contact fetching instruction
2. Update LeadListOutput schema if needed
3. Reset test run:
   ```sql
   UPDATE pm_pipeline.runs
   SET stage='company_discovery', status='in_progress'
   WHERE id='0915b268-d820-46a2-aa9b-aa1164701538';

   DELETE FROM pm_pipeline.company_candidates
   WHERE run_id='0915b268-d820-46a2-aa9b-aa1164701538';
   ```
4. Restart worker with `WORKER_MAX_LOOPS=1`
5. Monitor logs - verify agent does NOT call get_contacts tools
6. Verify discovery completes in ~1 minute (not 10+ minutes)
7. Verify worker evaluates discovery completion correctly
8. Verify run advances to company_research stage

## Related Issues

- **Discovery Shortfall Bug**: Fixed in previous session, but couldn't verify because agent was hanging on contact fetching
- **fetch_page Blocker**: [FETCH_PAGE_BLOCKER.md](FETCH_PAGE_BLOCKER.md) - Separate issue preventing batch_pms_analyzer testing

## Timeline

- **20:25:17**: Worker started with FIXED code for discovery shortfall
- **20:26:55**: Agent finished discovering 10 companies
- **20:26:55-20:35:54**: Agent wasted 9 minutes fetching contacts (should take 0 seconds)
- **20:35:54**: Test killed to investigate

## Recommendation

**IMMEDIATELY fix Option 1** - remove contact fetching from discovery phase entirely. This is a fundamental architectural violation that blocks all E2E testing and wastes massive resources.

The contact_discovery stage exists for a reason - use it!
