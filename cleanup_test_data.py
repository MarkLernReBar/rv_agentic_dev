"""Clean up all test data from database."""
import os
import sys
from pathlib import Path

# Load environment
env_file = Path(__file__).parent / ".env.local"
if env_file.exists():
    print("Loading .env.local...")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip().strip('"').strip("'")
    print("✅ Environment loaded\n")

# Add src to path
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from rv_agentic.services.supabase_client import _pg_conn

print("=" * 80)
print("CLEANING UP TEST DATA")
print("=" * 80)

try:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            # Delete all test run data
            print("\n1. Deleting contacts from test runs...")
            cur.execute("""
                DELETE FROM pm_pipeline.contact_candidates
                WHERE run_id IN (
                    SELECT id FROM pm_pipeline.runs WHERE notes LIKE '%test%'
                )
            """)
            contact_count = cur.rowcount
            print(f"   Deleted {contact_count} test contacts")

            print("\n2. Deleting companies from test runs...")
            cur.execute("""
                DELETE FROM pm_pipeline.company_candidates
                WHERE run_id IN (
                    SELECT id FROM pm_pipeline.runs WHERE notes LIKE '%test%'
                )
            """)
            company_count = cur.rowcount
            print(f"   Deleted {company_count} test companies")

            print("\n3. Deleting test runs...")
            cur.execute("""
                DELETE FROM pm_pipeline.runs
                WHERE notes LIKE '%test%'
            """)
            run_count = cur.rowcount
            print(f"   Deleted {run_count} test runs")

            print("\n4. Deleting test worker heartbeats...")
            cur.execute("""
                DELETE FROM pm_pipeline.worker_heartbeats
                WHERE worker_id LIKE 'test-worker-%'
            """)
            worker_count = cur.rowcount
            print(f"   Deleted {worker_count} test workers")

    print("\n" + "=" * 80)
    print("✅ CLEANUP COMPLETE")
    print("=" * 80)
    print(f"\nSummary:")
    print(f"  - {run_count} test runs deleted")
    print(f"  - {company_count} test companies deleted")
    print(f"  - {contact_count} test contacts deleted")
    print(f"  - {worker_count} test workers deleted")

except Exception as e:
    print(f"\n❌ Cleanup failed: {e}")
    sys.exit(1)
