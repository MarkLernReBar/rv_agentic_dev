#!/bin/bash
# Monitor multi-region discovery progress

RUN_ID="5ad7aaaf-3c46-4a86-8801-56c671d03555"
LOG_FILE="test_multi_region_denver.log"

echo "=== Multi-Region Discovery Monitor ==="
echo "Run ID: $RUN_ID"
echo ""

while true; do
  clear
  echo "=== Multi-Region Discovery Monitor ==="
  echo "Run ID: $RUN_ID"
  echo ""

  # Count regions started
  REGIONS_STARTED=$(grep -c "Region [0-9]/4:" "$LOG_FILE" 2>/dev/null || echo "0")
  echo "Regions Started: $REGIONS_STARTED/4"

  # Count regions completed
  REGIONS_COMPLETE=$(grep -c "Region [0-9]/4 complete:" "$LOG_FILE" 2>/dev/null || echo "0")
  echo "Regions Complete: $REGIONS_COMPLETE/4"

  # Count total searches
  SEARCHES=$(grep -c "MCP call start: tool=search_web" "$LOG_FILE" 2>/dev/null || echo "0")
  echo "Total Searches: $SEARCHES"

  # Count companies in DB
  COMPANIES=$(.venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from rv_agentic.services.supabase_client import _pg_conn; conn = _pg_conn(); cur = conn.cursor(); cur.execute('SELECT COUNT(*) FROM pm_pipeline.company_candidates WHERE run_id=%s', ('$RUN_ID',)); print(cur.fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
  echo "Companies in DB: $COMPANIES"

  echo ""
  echo "Last 5 region events:"
  grep -E "Region [0-9]/4|Multi-region" "$LOG_FILE" | tail -5

  echo ""
  echo "Press Ctrl+C to exit"

  # Check if worker completed
  if ! ps aux | grep -q "[l]ead_list_runner.*$RUN_ID"; then
    echo ""
    echo "✅ Worker completed!"
    break
  fi

  sleep 10
done
