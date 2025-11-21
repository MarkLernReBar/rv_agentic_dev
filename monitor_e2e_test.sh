#!/bin/bash
# E2E Test Monitoring Script
# Monitors run 0915b268-d820-46a2-aa9b-aa1164701538 every 5 minutes

RUN_ID="0915b268-d820-46a2-aa9b-aa1164701538"
LOG_FILE="e2e_test_monitor.log"

echo "========================================" | tee -a $LOG_FILE
echo "E2E Test Monitor - $(date)" | tee -a $LOG_FILE
echo "Run ID: $RUN_ID" | tee -a $LOG_FILE
echo "========================================" | tee -a $LOG_FILE

# Check run status in database
echo "" | tee -a $LOG_FILE
echo "📊 Run Status:" | tee -a $LOG_FILE
psql $POSTGRES_URL -c "
SELECT
    id,
    stage,
    status,
    target_quantity,
    criteria->>'notification_email' as email,
    created_at
FROM pm_pipeline.runs
WHERE id = '$RUN_ID'
" | tee -a $LOG_FILE

# Check company discovery progress
echo "" | tee -a $LOG_FILE
echo "🏢 Company Discovery Progress:" | tee -a $LOG_FILE
psql $POSTGRES_URL -c "
SELECT
    COUNT(*) as total_companies,
    COUNT(*) FILTER (WHERE status = 'validated') as validated,
    COUNT(*) FILTER (WHERE status = 'promoted') as promoted
FROM pm_pipeline.company_candidates
WHERE run_id = '$RUN_ID'
" | tee -a $LOG_FILE

# Check company research progress
echo "" | tee -a $LOG_FILE
echo "🔬 Company Research Progress:" | tee -a $LOG_FILE
psql $POSTGRES_URL -c "
SELECT
    COUNT(*) as researched_companies
FROM pm_pipeline.company_research
WHERE run_id = '$RUN_ID'
" | tee -a $LOG_FILE

# Check contact discovery progress
echo "" | tee -a $LOG_FILE
echo "👥 Contact Discovery Progress:" | tee -a $LOG_FILE
psql $POSTGRES_URL -c "
SELECT
    COUNT(*) as total_contacts,
    COUNT(*) FILTER (WHERE status = 'validated') as validated,
    COUNT(*) FILTER (WHERE status = 'promoted') as promoted
FROM pm_pipeline.contact_candidates
WHERE run_id = '$RUN_ID'
" | tee -a $LOG_FILE

# Check worker status
echo "" | tee -a $LOG_FILE
echo "⚙️  Worker Status:" | tee -a $LOG_FILE
echo "Lead List Worker:" | tee -a $LOG_FILE
tail -5 .lead_list_worker.log | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo "Company Research Worker:" | tee -a $LOG_FILE
tail -3 .company_research_worker.log | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo "Contact Research Worker:" | tee -a $LOG_FILE
tail -3 .contact_research_worker.log | tee -a $LOG_FILE

# Check for errors
echo "" | tee -a $LOG_FILE
echo "⚠️  Recent Errors:" | tee -a $LOG_FILE
echo "Lead List Worker Errors:" | tee -a $LOG_FILE
grep -i "error\|exception\|failed" .lead_list_worker.log | tail -3 | tee -a $LOG_FILE || echo "No errors" | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo "Company Research Worker Errors:" | tee -a $LOG_FILE
grep -i "error\|exception\|failed" .company_research_worker.log | tail -3 | tee -a $LOG_FILE || echo "No errors" | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo "Contact Research Worker Errors:" | tee -a $LOG_FILE
grep -i "error\|exception\|failed" .contact_research_worker.log | tail -3 | tee -a $LOG_FILE || echo "No errors" | tee -a $LOG_FILE

echo "" | tee -a $LOG_FILE
echo "========================================" | tee -a $LOG_FILE
echo "Monitor check complete at $(date)" | tee -a $LOG_FILE
echo "========================================" | tee -a $LOG_FILE
echo "" | tee -a $LOG_FILE
