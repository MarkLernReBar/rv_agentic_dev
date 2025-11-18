# Sequential Multi-Region Discovery Implementation Plan

## Overview
Replace single agent call with sequential multi-region approach to guarantee discovery target is reached.

## Architecture

```
Worker process_run():
├── Decompose geography into 4 regions
├── Loop through regions (with early exit):
│   ├── Call agent for region N
│   ├── Aggregate results
│   └── Check if discovery_target reached
└── Dedupe & insert top N companies
```

## Implementation Steps

### 1. ✅ Geography Decomposer (COMPLETE)
- Created: `src/rv_agentic/services/geography_decomposer.py`
- Functions: `decompose_geography()`, `format_region_for_prompt()`

### 2. Multi-Region Discovery Function (TO DO)
Create new function in lead_list_runner.py:
```python
def discover_companies_multi_region(
    run_id: str,
    criteria: Dict[str, Any],
    target_qty: int,
    discovery_target: int,
    companies_ready: int
) -> Tuple[List[Dict], List[Dict], Dict[str, Any]]:
    """
    Discover companies using sequential multi-region strategy.

    Returns: (companies, contacts, metadata)
    """
```

### 3. Modify process_run() (TO DO)
Replace lines 246-398 (single agent call + processing) with:
```python
companies, contacts, metadata = discover_companies_multi_region(
    run_id, criteria, target_qty, discovery_target, companies_ready
)
# Then proceed with existing insertion logic
```

### 4. Update Agent Prompt (TO DO)
Simplify agent prompt - remove ROUND 1/2/3/4 instructions, add region focus:
- Agent receives: "Your region: Downtown Denver"
- Agent searches only that region
- Returns ~8-10 companies per region

### 5. Deduplication Logic (TO DO)
Add helper function:
```python
def deduplicate_companies_by_domain(companies: List[Dict]) -> List[Dict]:
    """Dedupe by domain, keep highest quality."""
```

## Expected Behavior

**Before (Single Agent):**
- 1 agent call
- 10-15 searches
- Returns 8-10 companies
- ❌ Falls short of discovery_target (40)

**After (Multi-Region):**
- 4 sequential agent calls
- 10-15 searches each (40-60 total)
- Each returns 8-10 companies
- Aggregate: 32-40 companies
- ✅ Meets discovery_target

## Testing Plan

Test case: Denver, target=20, discovery_target=40
- Expected regions: Downtown, North, South, West/East
- Expected: 4 agent calls, ~36 companies discovered
- Success criteria: companies_discovered >= 32

## Files to Modify

1. `src/rv_agentic/services/geography_decomposer.py` - ✅ DONE
2. `src/rv_agentic/workers/lead_list_runner.py` - IN PROGRESS
3. `src/rv_agentic/agents/lead_list_agent.py` - TODO (simplify prompt)

## Rollback Plan

If this approach fails, we can:
1. Revert lead_list_runner.py changes
2. Fall back to current single-agent approach
3. Accept lower yield and increase oversample factor to 5x
