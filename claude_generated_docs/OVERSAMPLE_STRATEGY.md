# 🎯 Oversample Strategy for Multi-Stage Enrichment Pipeline

**Date:** 2025-01-17
**Status:** Implemented
**Replaces:** Batch-based discovery with prompt-controlled limits

---

## Problem Statement

The lead list generation system has a **multi-stage enrichment pipeline** where companies can be disqualified at each stage:

1. **Discovery** - Find companies matching criteria
2. **Company Enrichment** - ICP analysis, quality checks
3. **Contact Discovery** - Find 1-3 decision makers per company
4. **Contact Enrichment** - Email verification, anecdotes

**Challenge:** If we discover exactly 25 companies but 30% fail ICP analysis, we end up with only 17-18 fully enriched companies.

---

## Solution: Oversample at Discovery

### Strategy

**Discover 2x target companies upfront**, anticipating natural attrition through enrichment stages.

### Configuration

```bash
# Default: 2.0x oversample
export LEAD_LIST_OVERSAMPLE_FACTOR=2.0

# For 25 final companies
Target quantity: 25
Discovery target: 50 (25 * 2.0)
```

### Expected Attrition Rates

| Stage | Typical Attrition | Remaining |
|-------|-------------------|-----------|
| Discovery | 0% (all pass) | 50 companies |
| Company ICP Analysis | 30% | 35 companies |
| Contact Discovery | 20% | 28 companies |
| Contact Enrichment | 10% | ~25 companies |

---

## How It Works

### 1. Discovery Stage (lead_list_runner.py)

```python
target_qty = 25  # Final enriched companies needed
oversample_factor = 2.0
discovery_target = 50  # Oversample for attrition

# Agent finds ALL matching companies (~90)
# Worker selects best 50 (sorted by quality)
# Inserts into company_candidates table
```

### 2. Enrichment Stages (Existing Workers)

- `company_research_runner.py` - Enriches companies, marks failed ICP as rejected
- `contact_research_runner.py` - Discovers contacts, marks companies without contacts
- Each stage naturally filters companies

### 3. Final Result

After all stages, **~25 fully enriched companies** remain (within acceptable variance).

---

## Benefits

✅ **Works with existing stage-based architecture**
✅ **One comprehensive agent discovery pass** (no multiple batch loops)
✅ **Natural quality filtering** through enrichment stages
✅ **No complex backfill logic** needed
✅ **Predictable final counts** based on historical attrition rates

---

## Adjusting Oversample Factor

### When to Increase (2.5x - 3.0x)

- High PMS requirements (less common software)
- Strict ICP criteria
- Small geographic markets
- Historical attrition >40%

### When to Decrease (1.5x - 1.8x)

- Broad criteria (any PMS, large market)
- Historical attrition <20%
- Need to minimize enrichment costs

### Monitoring

Check actual attrition rates:

```sql
-- Discovery stage
SELECT COUNT(*) as discovered
FROM pm_pipeline.company_candidates
WHERE run_id = 'XXX';

-- After company research
SELECT
    COUNT(*) FILTER (WHERE status = 'validated') as passed_icp,
    COUNT(*) FILTER (WHERE status = 'rejected') as failed_icp
FROM pm_pipeline.company_research
WHERE run_id = 'XXX';

-- After contact discovery
SELECT
    COUNT(DISTINCT company_id) as companies_with_contacts
FROM pm_pipeline.contact_candidates
WHERE run_id = 'XXX';
```

---

## Comparison with Alternatives

### ❌ Batch Discovery with Backfill

```
Discover 10 → Enrich → 7 pass → Discover 10 more → Enrich → 7 pass...
```

**Problems:**
- LLM agents ignore prompt-based batch limits ("find exactly 10")
- Multiple expensive agent calls
- Complex checkpoint state management
- Higher MCP/n8n costs

### ❌ Exact Discovery (1.0x)

```
Discover 25 → Enrich → 17 pass → Manual intervention needed
```

**Problems:**
- Requires user decision when gap remains
- Delays pipeline completion
- Poor user experience

### ✅ Oversample Strategy (2.0x)

```
Discover 50 → Enrich all → ~25 pass naturally
```

**Benefits:**
- One agent call
- Natural filtering
- Predictable results
- No manual intervention

---

## Testing

### Clear seeding to test real agent MCP discovery:

```bash
# See what would be deleted
python clear_seeding_for_run.py RUN_UUID --dry-run

# Delete seeded companies
python clear_seeding_for_run.py RUN_UUID

# Re-run worker with oversample
export RUN_FILTER_ID=RUN_UUID
export WORKER_MAX_LOOPS=1
export LEAD_LIST_OVERSAMPLE_FACTOR=2.0
python -m rv_agentic.workers.lead_list_runner
```

### Expected Log Output

```
Progress check: target=25, discovery_target=50 (oversample=2.0x), existing=0, remaining=50
Processing agent output: agent returned 91 companies (total_found=91, search_exhausted=True)
Inserting up to 50 companies (final_target=25, discovery_target=50, companies_ready=0, remaining=50)
Inserted company 1/50: id=... domain=...
...
Inserted company 50/50: id=... domain=...
Target quantity reached: inserted=50 companies, stopping (agent had 41 more)
Run XXX discovery target met: 50/50 discovered (target: 25 final after enrichment)
```

---

## Future Improvements

### Dynamic Oversample Based on Historical Attrition

```python
# Calculate attrition from recent runs
recent_attrition = calculate_avg_attrition(last_10_runs)
dynamic_oversample = 1.0 / (1.0 - recent_attrition)

# Auto-adjust oversample factor
oversample_factor = max(1.5, min(3.0, dynamic_oversample))
```

### Per-Criteria Oversample Factors

```python
oversample_map = {
    ("Buildium", "TN"): 1.8,  # Low attrition (common combo)
    ("PropertyBoss", "WY"): 3.0,  # High attrition (rare combo)
}
```

---

## Conclusion

The **oversample strategy** elegantly solves the multi-stage enrichment pipeline problem by:
1. Accepting LLM agent natural behavior (returns all results)
2. Leveraging existing stage-based architecture
3. Using simple, predictable math (target × factor)
4. Eliminating complex batch coordination

This architectural decision prioritizes **simplicity, reliability, and cost-efficiency** over prompt engineering attempts to control agent output quantity.
