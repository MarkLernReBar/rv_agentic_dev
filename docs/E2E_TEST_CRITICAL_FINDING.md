# E2E Test Critical Finding - PMS Verification Bottleneck

**Date**: 2025-11-20
**Run ID**: `0915b268-d820-46a2-aa9b-aa1164701538`
**Test**: 5 companies in Boulder, CO using Buildium with 50+ units

## Problem Summary

The agent is giving up after finding only 1 company despite:
- ✅ Using strict ReAct pattern correctly
- ✅ Calling pms_subdomains tool (found 1 company)
- ✅ Searching web for list pages
- ✅ Fetching list pages (ipropertymanagement.com/companies/boulder-co)
- ❌ **Rejecting ALL companies from list pages due to lack of PMS evidence**

## Root Cause

**Fundamental contradiction between requirements:**

1. **PMS is a HARD requirement** - Only accept companies with confirmed Buildium evidence
2. **List pages don't include PMS info** - ipropertymanagement.com lists companies but doesn't say what PMS they use
3. **PMS verification tools are limited**:
   - `pms_subdomains` only has 1 Boulder company
   - `Run_PMS_Analyzer_Script` requires a domain (which the agent has from list pages)
   - But the agent isn't calling PMS analyzer for each domain found on list pages

## Evidence from Logs

```
17:46:13 - search_web('top property management Boulder CO')
17:46:19 - Found ipropertymanagement.com URL
17:46:23 - fetch_page('https://ipropertymanagement.com/companies/boulder-co')
17:46:24 - Page fetched successfully
17:51:00 - search_web with Buildium-specific query
... but only 1 company in database
```

**Agent behavior**: Fetched list page containing 10-20 companies, but didn't add them to structured output because it couldn't verify PMS.

## The Real Issue

The agent is following instructions TOO literally:
> "Only ACCEPT companies when you have positive PMS confirmation from one of these sources"

When it fetches a list page with 15 Boulder companies:
1. ✅ It extracts company names and domains
2. ❌ It doesn't have PMS info for any of them
3. ❌ It doesn't call `Run_PMS_Analyzer_Script` for each domain
4. ❌ It rejects all 15 companies
5. ✅ It returns only the 1 company from pms_subdomains

## Proposed Solutions

### Option 1: Agent Must Verify PMS for Each Discovered Company (RECOMMENDED)

**Fix the agent workflow:**

```
AFTER fetch_page returns companies:
1. Extract ALL companies from page
2. FOR EACH company:
   a. Call Run_PMS_Analyzer_Script(domain) to check PMS
   b. IF Buildium detected → ACCEPT
   c. IF no PMS detected → REJECT
3. Return accepted companies in structured output
```

**Implementation**: Update system prompt to enforce this verification loop.

### Option 2: Geographic Expansion Strategy

When Boulder alone doesn't yield enough companies:
1. Expand to "Boulder County, CO"
2. Expand to "Denver Metro Area"
3. Expand to "Colorado"

But this doesn't solve the core PMS verification problem.

### Option 3: Relax PMS Requirement (NOT RECOMMENDED per user feedback)

User explicitly said: "PMS is a hard requirement when its part of the request."

## Recommended Fix

**Update the agent system prompt to require PMS verification IN THE LOOP:**

```markdown
### PHASE 3: PMS Verification Loop (MANDATORY)

After extracting companies from list pages, you MUST verify PMS for each:

1. Extract company name and domain from fetch_page results
2. FOR EACH company:
   - Call `Run_PMS_Analyzer_Script(domain=company_domain)`
   - IF analyzer returns Buildium → ACCEPT company
   - IF analyzer returns different PMS → REJECT company
   - IF analyzer returns no PMS → Try LangSearch, then REJECT if still unknown
3. Only add VERIFIED companies to your structured output

**CRITICAL**: You cannot reject companies just because the list page doesn't show PMS.
You MUST actively verify PMS using the analyzer tool for every discovered company.
```

## Solution Implemented: Batch PMS Analyzer Tool

**Date**: 2025-11-20 (after critical finding)
**Approach**: Created `mcp_batch_pms_analyzer` tool instead of prompt-based loop

### Why Batch Tool vs Prompt Update?

**Problem with Prompt Approach:**
- Agent already has `mcp_run_pms_analyzer` but ISN'T using it
- Expecting agent to loop 15 times for each list page is complex
- High risk of agent skipping verification (already demonstrated)
- Higher token/API costs with 15 individual calls

**Batch Tool Advantages:**
1. Single tool call per list page (more efficient)
2. Simpler workflow: fetch_page → extract domains → batch_analyze → accept/reject
3. Harder for agent to skip - one clear step
4. Lower token usage and costs
5. More reliable

### Implementation Details

**1. New Tool: `mcp_batch_pms_analyzer` (lines 421-458)**
```python
@function_tool
async def mcp_batch_pms_analyzer(domains: List[str]) -> List[Dict[str, Any]]:
    """Analyze multiple domains for PMS in one call.

    Args:
        domains: ["company1.com", "company2.com", ...]

    Returns:
        [{"domain": "company1.com", "pms": "Buildium", "confidence": 0.9}, ...]
    """
    fields = [{"domain": d} for d in domains]
    return await mcp_client.call_tool_async("batch_pms_analyzer", {"fields": fields, "query": "batch"})
```

**2. Added to Tools List** (line 747)
```python
tools=[
    ...
    mcp_batch_pms_analyzer,  # PREFERRED: Batch PMS verification for list pages
    ...
]
```

**3. Updated System Prompt** (lines 152-156)
```markdown
- Priority 2a: **AFTER fetch_page extracts multiple companies → Use `mcp_batch_pms_analyzer`** (PREFERRED)
  - Extract all domains from the list page
  - Call mcp_batch_pms_analyzer(domains=[list_of_domains]) ONCE
  - Accept companies where PMS matches requirement, reject others
  - Example: fetch_page finds 15 companies → extract domains → batch_pms_analyzer → accept Buildium matches
```

### Expected Workflow with Batch Tool

1. Agent calls query_pms_subdomains_tool(pms="Buildium", state="CO", city="Boulder") → 1 pre-validated company
2. Agent calls search_web("property management Boulder CO") → finds ipropertymanagement.com URL
3. Agent calls fetch_page("ipropertymanagement.com/boulder-co") → extracts 15 companies with domains
4. **Agent calls mcp_batch_pms_analyzer(domains=["company1.com", "company2.com", ...])** → gets PMS for all 15
5. Agent accepts companies with Buildium, rejects others
6. Continues until discovery_target (10 companies) reached

### Files Modified

- [src/rv_agentic/agents/lead_list_agent.py:421-458](../src/rv_agentic/agents/lead_list_agent.py) - New batch tool function
- [src/rv_agentic/agents/lead_list_agent.py:747](../src/rv_agentic/agents/lead_list_agent.py) - Added to tools list
- [src/rv_agentic/agents/lead_list_agent.py:152-156](../src/rv_agentic/agents/lead_list_agent.py) - Updated prompt

### Prerequisites

**n8n Workflow Required:**
- Tool name: `batch_pms_analyzer`
- Input: `{fields: [{domain: string}], query: "batch"}`
- Logic: Loop through fields array, call existing PMS analyzer for each domain
- Output: Array of `{domain: string, pms: string, confidence: number}`

**This n8n workflow must be deployed BEFORE testing can proceed.**

## Next Steps

1. ✅ Document the finding
2. ✅ Implement batch_pms_analyzer tool in lead_list_agent.py
3. ⏳ **Create batch_pms_analyzer workflow in n8n** (BLOCKING - must be done first)
4. ⏳ Reset test run and clear company_candidates
5. ⏳ Restart worker with new code
6. ⏳ Monitor logs - verify agent uses batch_pms_analyzer after fetch_page
7. ⏳ Verify discovery completes with 10 companies
