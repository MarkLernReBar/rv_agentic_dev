#!/bin/bash
# Monitor E2E test every 5 minutes - check reasoning, kill if unexpected

RUN_ID="0915b268-d820-46a2-aa9b-aa1164701538"
LOG_FILE="e2e_5min_monitor.log"
CHECK_COUNT=0

while true; do
    CHECK_COUNT=$((CHECK_COUNT + 1))
    echo "========================================" | tee -a $LOG_FILE
    echo "🔍 Check #$CHECK_COUNT - $(date)" | tee -a $LOG_FILE
    echo "========================================" | tee -a $LOG_FILE
    
    # Check run status
    echo "📊 Run Status:" | tee -a $LOG_FILE
    .venv/bin/python -c "
from rv_agentic.services import supabase_client as sb
run = sb.get_pm_run('$RUN_ID')
print(f'Stage: {run[\"stage\"]}')
print(f'Status: {run[\"status\"]}')
print(f'Target: {run[\"target_quantity\"]}')
print(f'Discovery Target: {run.get(\"discovery_target\", \"N/A\")}')
" | tee -a $LOG_FILE
    
    # Check company count
    echo "" | tee -a $LOG_FILE
    echo "🏢 Companies Found:" | tee -a $LOG_FILE
    .venv/bin/python -c "
from rv_agentic.services import supabase_client as sb
conn = sb._pg_conn()
with conn.cursor() as cur:
    cur.execute(\"SELECT COUNT(*) FROM pm_pipeline.company_candidates WHERE run_id = '$RUN_ID'\")
    count = cur.fetchone()[0]
    print(f'Total: {count}')
    
    cur.execute(\"SELECT COUNT(*) FROM pm_pipeline.company_candidates WHERE run_id = '$RUN_ID' AND status = 'validated'\")
    validated = cur.fetchone()[0]
    print(f'Validated: {validated}')
" | tee -a $LOG_FILE
    
    # Check recent think calls and reasoning
    echo "" | tee -a $LOG_FILE
    echo "🧠 Recent Agent Reasoning (last 10 think calls):" | tee -a $LOG_FILE
    tail -1000 .lead_list_worker_retest3.log | grep -A 1 "tool=think" | tail -20 | tee -a $LOG_FILE
    
    # Check for errors
    echo "" | tee -a $LOG_FILE
    echo "⚠️  Recent Errors:" | tee -a $LOG_FILE
    tail -100 .lead_list_worker_retest3.log | grep -i "error\|exception\|failed" | tail -5 | tee -a $LOG_FILE || echo "No errors" | tee -a $LOG_FILE
    
    # Check if worker still running
    echo "" | tee -a $LOG_FILE
    if ps aux | grep -q "[l]ead_list_runner"; then
        echo "✅ Worker still running" | tee -a $LOG_FILE
    else
        echo "❌ Worker stopped!" | tee -a $LOG_FILE
        echo "Check #$CHECK_COUNT - Worker died unexpectedly" | tee -a $LOG_FILE
        exit 1
    fi
    
    echo "" | tee -a $LOG_FILE
    echo "========================================" | tee -a $LOG_FILE
    echo "Next check in 5 minutes..." | tee -a $LOG_FILE
    echo "" | tee -a $LOG_FILE
    
    # Wait 5 minutes
    sleep 300
done
