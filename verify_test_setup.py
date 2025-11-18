#!/usr/bin/env python3
"""Verify test environment is ready."""

import os
import sys
from pathlib import Path

# Load environment
env_file = Path(__file__).parent / ".env.local"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip().strip('"').strip("'")

# Add src to path
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

print("=" * 80)
print("PRE-TEST VERIFICATION")
print("=" * 80)

# Check required env vars
print("\n1. Checking Environment Variables:")
print("-" * 80)

required = {
    "OPENAI_API_KEY": "OpenAI Agent execution",
    "POSTGRES_URL": "Database access",
    "N8N_MCP_SERVER_URL": "MCP tool discovery",
}

optional = {
    "LEAD_LIST_OVERSAMPLE_FACTOR": "Oversample configuration (default: 2.0)",
    "RUN_FILTER_ID": "Targeted test run (not set = will process all active)",
    "WORKER_MAX_LOOPS": "Loop limit (not set = unlimited)",
}

all_good = True
for key, purpose in required.items():
    val = os.getenv(key)
    if val:
        masked = val[:8] + "..." if len(val) > 8 else "***"
        print(f"  ✅ {key}: {masked} ({purpose})")
    else:
        print(f"  ❌ {key}: NOT SET ({purpose})")
        all_good = False

print()
for key, purpose in optional.items():
    val = os.getenv(key)
    if val:
        print(f"  ℹ️  {key}: {val} ({purpose})")
    else:
        print(f"  ⚪ {key}: not set ({purpose})")

if not all_good:
    print("\n❌ Required environment variables missing!")
    sys.exit(1)

# Check database connectivity
print("\n2. Checking Database Connection:")
print("-" * 80)

try:
    from rv_agentic.services.supabase_client import _pg_conn

    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pm_pipeline.runs WHERE status = 'active'")
            active_runs = cur.fetchone()[0]
            print(f"  ✅ Database connected")
            print(f"  ℹ️  Active runs: {active_runs}")
except Exception as e:
    print(f"  ❌ Database connection failed: {e}")
    sys.exit(1)

# Check MCP server
print("\n3. Checking MCP Server:")
print("-" * 80)

mcp_url = os.getenv("N8N_MCP_SERVER_URL")
if mcp_url:
    print(f"  ℹ️  MCP URL: {mcp_url}")
    print(f"  ⚠️  Cannot test connectivity without making actual call")
    print(f"  ⚠️  Will verify during worker run")
else:
    print(f"  ⚪ MCP server not configured (seeding only)")

# Check oversample factor
print("\n4. Checking Oversample Configuration:")
print("-" * 80)

factor = float(os.getenv("LEAD_LIST_OVERSAMPLE_FACTOR", "2.0"))
print(f"  ℹ️  Oversample factor: {factor}x")
print(f"  ℹ️  For target=25, will discover {int(25 * factor)} companies")

print("\n" + "=" * 80)
print("✅ PRE-TEST VERIFICATION COMPLETE")
print("=" * 80)
print("\nReady to:")
print("  1. Create test run (or use existing)")
print("  2. Clear seeding: python clear_seeding_for_run.py RUN_UUID")
print("  3. Run worker: python -m rv_agentic.workers.lead_list_runner")
