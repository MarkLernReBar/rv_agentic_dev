#!/usr/bin/env python3
"""Simple test to verify export promotion logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from rv_agentic.services import export, supabase_client

def main():
    run_id = "0915b268-d820-46a2-aa9b-aa1164701538"

    print("=" * 80)
    print("Testing Export with Promotion")
    print("=" * 80)

    # Check initial state
    run = supabase_client.get_pm_run(run_id)
    target_qty = run.get("target_quantity", 0)
    print(f"\nTarget Quantity: {target_qty}")

    validated = supabase_client._get_pm(
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {"run_id": f"eq.{run_id}", "status": "eq.validated", "select": "id,name"}
    )
    print(f"Validated Companies BEFORE export: {len(validated)}")

    promoted = supabase_client._get_pm(
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {"run_id": f"eq.{run_id}", "status": "eq.promoted", "select": "id,name"}
    )
    print(f"Promoted Companies BEFORE export: {len(promoted)}")

    # Run export
    print(f"\nCalling export_companies_to_csv('{run_id}')...")
    try:
        csv_output = export.export_companies_to_csv(run_id)

        # Count lines
        lines = csv_output.strip().split("\n")
        row_count = len(lines) - 1  # Exclude header
        print(f"\nCSV Lines (including header): {len(lines)}")
        print(f"CSV Rows (excluding header): {row_count}")

        # Show first few lines
        print(f"\nFirst 3 lines of CSV:")
        for i, line in enumerate(lines[:3]):
            print(f"  Line {i}: {line[:100]}...")

        # Check final state
        validated_after = supabase_client._get_pm(
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{run_id}", "status": "eq.validated", "select": "id,name"}
        )
        promoted_after = supabase_client._get_pm(
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{run_id}", "status": "eq.promoted", "select": "id,name"}
        )

        print(f"\nValidated Companies AFTER export: {len(validated_after)}")
        print(f"Promoted Companies AFTER export: {len(promoted_after)}")

        # Validation
        print(f"\n{'=' * 80}")
        if row_count == target_qty:
            print(f"✅ PASS: CSV has exactly {target_qty} rows")
        else:
            print(f"❌ FAIL: CSV has {row_count} rows, expected {target_qty}")

        if len(promoted_after) == target_qty:
            print(f"✅ PASS: {target_qty} companies promoted")
        else:
            print(f"❌ FAIL: {len(promoted_after)} companies promoted, expected {target_qty}")

        print("=" * 80)

    except Exception as e:
        print(f"\n❌ Export failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
