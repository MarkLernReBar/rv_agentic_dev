# Pipeline Fix Summary
**Date**: 2025-11-20
**Status**: Ready for Testing

## Problem Diagnosis

The pipeline was not broken - it was **producing 0-1 companies instead of 5-10** due to:

1. **Poor search strategy**: Agent was doing many searches but not using `fetch_page` to extract companies from list pages
2. **False parallelism**: Split geography into 4 regions with no differentiation, causing 4 agents to search "San Francisco" identically
3. **Agent confusion**: Prompt emphasized "20 mandatory searches" over outcomes, leading to quantity over quality

### Evidence from Logs
```
2025-11-20 07:24:04 [INFO] Completed parallel region 1/4: found 0 companies, 0 contacts
2025-11-20 07:30:27 [INFO] Completed parallel region 4/4: found 1 companies, 1 contacts
```

After ~20 minutes of parallel agent execution with dozens of MCP calls, total yield: **1 company**.

## Root Cause

**The agent was not using its most powerful tool (`fetch_page`) effectively**:
- Searches would find URLs like "Top 50 Property Management Companies in San Francisco"
- Agent would note these URLs but never call `fetch_page` to extract the companies
- Each list page contains 10-50 companies, but agent treated them as individual mentions

**Search strategy was prescriptive instead of outcome-focused**:
- Prompt required "20 searches in 4 rounds"
- Agent focused on hitting the count rather than finding companies
- No reflection/planning cycle to adjust strategy mid-execution

## Solution Implemented

### 1. Redesigned Search Strategy (Priority-Based)

**BEFORE** (prescriptive):
```
ROUND 1 - Execute 5 searches with these queries...
ROUND 2 - Execute 5 searches with these queries...
[etc, 20 total required]
```

**AFTER** (outcome-focused):
```
PHASE 1: Find List Pages (THIS IS YOUR PRIMARY STRATEGY)
- Search for "top property management companies [city]"
- IMMEDIATELY use fetch_page on list URLs
- Extract ALL companies from each page (10-50 per page)
- Repeat until target reached

PHASE 2: Direct Discovery (only if list pages insufficient)
PHASE 3: Enrichment (after you have companies)
```

### 2. Simplified Multi-Region Logic

**BEFORE**:
- Default 4 regions per run
- For "San Francisco", creates 4 generic "San Francisco Region 1-4" with no differentiation
- 4 parallel agents all searching identically
- Each finds 0-1 companies

**AFTER**:
- Default 1 region per run (single agent call)
- Can override with `LEAD_LIST_REGION_COUNT=4` for large geographies (entire states)
- Agent sees full geography and uses list-page strategy to find 10-50 companies from 3-5 sources
- Expected time: 10-15 minutes instead of 60+ minutes

### 3. Added ReAct Pattern (Plan → Act → Observe)

**New `mcp_think` tool** for reflection:
```python
@function_tool
async def mcp_think(thought: str) -> str:
    """Use for planning and reflection (ReAct pattern)."""
```

**Agent workflow**:
1. **Plan**: "Need 40 companies. Will search for list pages first."
2. **Act**: `mcp_search_web("top property management San Francisco")`
3. **Observe**: "Found ipropertymanagement.com URL. Will fetch this page."
4. **Act**: `mcp_fetch_page("https://ipropertymanagement.com/san-francisco")`
5. **Observe**: "Extracted 23 companies. Need 17 more. Will try NARPM next."
6. **Repeat** until target reached

This makes the agent **self-directed and adaptive** instead of following a rigid script.

## Files Changed

1. **[lead_list_agent.py](src/rv_agentic/agents/lead_list_agent.py)**:
   - Redesigned search strategy prompt (lines 100-159)
   - Added ReAct pattern documentation (lines 46-91)
   - Added `mcp_think` tool (lines 403-423)
   - Registered `mcp_think` in agent tools (line 543)

2. **[lead_list_runner.py](src/rv_agentic/workers/lead_list_runner.py)**:
   - Changed default regions from 4 to 1 (line 273)
   - Simplified worker prompt for single-region mode (lines 170-179)
   - Emphasized fetch_page strategy in prompt (lines 199-215)

## What Was NOT Changed (Per User Requirements)

✅ **Kept hard PMS requirements** - PMS filtering remains strict when specified
✅ **Kept oversample strategy** - Still discover 2x target to account for attrition
✅ **Kept all pipeline stages** - company_discovery → company_research → contact_discovery → done
✅ **Kept async workers** - Background processing unchanged

## Expected Improvement

**BEFORE** (observed):
- 4 parallel agents × 15 minutes each = 60 minutes
- Total yield: 0-1 companies
- Result: `needs_user_decision` (gap unresolved)

**AFTER** (expected):
- 1 agent × 10-15 minutes
- Agent finds 3-5 list pages via search
- Each list page → 10-50 companies via `fetch_page`
- Total yield: 30-50 companies (before PMS filtering)
- After PMS filtering: 10-20 companies (meeting target)

## Testing Plan

### Quick Test (Recommended)
```bash
# Set targeted test mode
export RUN_FILTER_ID="<existing-run-id>"
export WORKER_MAX_LOOPS=1

# Run worker
python -m rv_agentic.workers.lead_list_runner
```

### Full Test
```bash
# Create new run via Streamlit UI
streamlit run app.py

# Or via CLI
python -m rv_agentic.orchestrator \
  --criteria '{"city": "San Francisco", "state": "CA", "pms": "Buildium", "units_min": 50}' \
  --quantity 5 \
  --output-dir ./test_output
```

### Success Criteria
1. Agent finds at least 3 list page URLs in first 3-5 searches
2. Agent calls `fetch_page` on each list URL
3. Agent extracts 10+ companies per list page
4. Agent uses `mcp_think` before/after major actions
5. Total execution time: 10-15 minutes for 5-10 company target
6. Final yield: Meets or exceeds discovery target (after oversample)

### What to Watch For
- **Agent uses fetch_page**: Check logs for `[INFO] rv_agentic.tools.mcp_client: MCP call complete: tool=fetch_page items=1`
- **Agent reflects**: Check for `[INFO] [LeadListAgent] think: ...` entries
- **Companies extracted**: Look for `Extracted 23 companies` style messages
- **No early termination**: Agent should NOT return empty `companies` array

## Rollback Plan (If Needed)

```bash
git checkout HEAD~1 src/rv_agentic/agents/lead_list_agent.py
git checkout HEAD~1 src/rv_agentic/workers/lead_list_runner.py
```

Or set environment override:
```bash
export LEAD_LIST_REGION_COUNT=4  # Restore old behavior
```

## Next Steps

1. **Test immediately** with existing run or create small test run (5 companies)
2. **Monitor logs** for fetch_page usage and think pattern
3. **If successful**: Update CLAUDE.md to document new ReAct pattern
4. **If issues**: Share logs and we'll iterate on prompt

---

**Bottom Line**: The fix focuses on **smarter search strategy** (list pages + fetch_page) and **adaptive planning** (ReAct pattern), not architectural changes. This should 10x the company discovery rate while reducing execution time by 75%.
