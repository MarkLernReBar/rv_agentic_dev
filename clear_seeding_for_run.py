#!/usr/bin/env python3
"""Clear seeded companies for a specific run to test agent MCP discovery.

Usage:
    python clear_seeding_for_run.py RUN_UUID
    python clear_seeding_for_run.py RUN_UUID --dry-run
"""

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

from rv_agentic.services.supabase_client import _pg_conn


def main():
    if len(sys.argv) < 2:
        print("Usage: python clear_seeding_for_run.py RUN_UUID [--dry-run]")
        sys.exit(1)

    run_id = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Clearing seeded companies for run: {run_id}\n")

    try:
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                # Get current breakdown
                print("Current company breakdown by discovery_source:")
                print("-" * 70)
                cur.execute("""
                    SELECT discovery_source, COUNT(*) as count
                    FROM pm_pipeline.company_candidates
                    WHERE run_id = %s
                    GROUP BY discovery_source
                    ORDER BY count DESC
                """, (run_id,))

                sources = {}
                total = 0
                for row in cur.fetchall():
                    source, count = row
                    sources[source] = count
                    total += count
                    print(f"  {source}: {count}")

                if not sources:
                    print("  (No companies found for this run)")
                    return

                print("-" * 70)
                print(f"Total: {total} companies\n")

                # Count seeded companies
                seeded = [(s, c) for s, c in sources.items()
                         if s.startswith("pms_subdomains:") or s.startswith("neo_pms:")]

                if not seeded:
                    print("✓ No seeded companies found (all from agent discovery)")
                    return

                to_delete = sum(c for _, c in seeded)
                print(f"Will {'[DRY RUN]' if dry_run else 'DELETE'} {to_delete} seeded companies:")
                for source, count in seeded:
                    print(f"  - {source}: {count}")

                if dry_run:
                    print("\n[DRY RUN] No changes made. Run without --dry-run to delete.")
                    return

                # Confirm
                print()
                confirm = input("Type 'DELETE' to confirm deletion: ")
                if confirm != "DELETE":
                    print("Aborted.")
                    return

                # Delete seeded companies
                cur.execute("""
                    DELETE FROM pm_pipeline.company_candidates
                    WHERE run_id = %s
                    AND (discovery_source LIKE 'pms_subdomains:%'
                         OR discovery_source LIKE 'neo_pms:%')
                """, (run_id,))

                deleted = cur.rowcount
                print(f"\n✓ Deleted {deleted} seeded companies")

                # Get remaining count
                cur.execute("""
                    SELECT COUNT(*) FROM pm_pipeline.company_candidates
                    WHERE run_id = %s
                """, (run_id,))
                remaining = cur.fetchone()[0]

                print(f"✓ Remaining companies: {remaining}")
                print("\nNow re-run the worker to test agent MCP discovery:")
                print(f"  export RUN_FILTER_ID={run_id}")
                print(f"  export WORKER_MAX_LOOPS=1")
                print(f"  python -m rv_agentic.workers.lead_list_runner")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
