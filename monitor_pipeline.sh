#!/bin/bash
# Quick pipeline monitoring commands

echo "=== PIPELINE MONITORING COMMANDS ==="
echo ""
echo "1. REAL-TIME WORKER LOGS (filtered for key events):"
echo "   tail -f .lead_list_worker.log | grep -E '(Starting|Completed|Inserted|ERROR|companies found)'"
echo ""
echo "2. CHECK DATABASE STATUS:"
echo "   .venv/bin/python debug_runs.py"
echo ""
echo "3. VIEW ALL WORKER LOGS (unfiltered):"
echo "   tail -f .lead_list_worker.log"
echo ""
echo "4. CHECK LAST 50 ERRORS:"
echo "   tail -100 .lead_list_worker.log | grep ERROR"
echo ""
echo "5. CHECK SPECIFIC RUN STATUS (replace RUN_ID):"
echo "   psql \$POSTGRES_URL -c \"SELECT run_id, stage, status, created_at FROM pm_pipeline.runs WHERE run_id = 'YOUR_RUN_ID';\""
echo ""
echo "6. CHECK COMPANY DISCOVERY PROGRESS:"
echo "   psql \$POSTGRES_URL -c \"SELECT run_id, COUNT(*) as companies FROM pm_pipeline.company_candidates GROUP BY run_id;\""
echo ""
echo "7. MONITOR CONTINUOUS (auto-refresh every 5s):"
cat << 'EOF'
   watch -n 5 '.venv/bin/python debug_runs.py'
EOF
echo ""
