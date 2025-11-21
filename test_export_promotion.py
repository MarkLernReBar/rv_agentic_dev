#!/usr/bin/env python3
"""Test script for export promotion and persistence logic."""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from rv_agentic.services import export, supabase_client

def test_export_with_promotion():
    """Test the export function with promotion and persistence."""

    run_id = "0915b268-d820-46a2-aa9b-aa1164701538"

    print("=" * 80)
    print("🧪 Testing Export Promotion & Persistence Logic")
    print("=" * 80)
    print()

    # Check initial state
    print("📊 Initial State:")
    run = supabase_client.get_pm_run(run_id)
    target_qty = run.get("target_quantity", 0)
    print(f"  Run ID: {run_id}")
    print(f"  Target Quantity: {target_qty}")

    validated = supabase_client._get_pm(
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {"run_id": f"eq.{run_id}", "status": "eq.validated", "select": "id,name,domain,created_at"}
    )
    print(f"  Validated Companies: {len(validated)}")

    promoted = supabase_client._get_pm(
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {"run_id": f"eq.{run_id}", "status": "eq.promoted", "select": "id,name,domain"}
    )
    print(f"  Promoted Companies: {len(promoted)}")
    print()

    # Show top 5 validated companies (will be sorted by ICP tier in export)
    print("🏆 First 5 Validated Companies (sorted by created_at):")
    sorted_validated = sorted(validated, key=lambda x: x.get("created_at", ""))
    for i, co in enumerate(sorted_validated[:5], 1):
        print(f"  {i}. {co.get('name')} (domain: {co.get('domain')})")
    print()

    # Count current research_database entries
    print("📚 Checking research_database before export...")
    try:
        existing_companies = supabase_client._execute_query(
            "SELECT COUNT(*) as count FROM companies"
        )
        existing_count = existing_companies[0].get("count", 0) if existing_companies else 0
        print(f"  Existing companies in research_database: {existing_count}")
    except Exception as e:
        print(f"  ⚠️  Could not count research_database: {e}")
        existing_count = 0
    print()

    # Test the export
    print("🚀 Running export_companies_to_csv()...")
    print()

    try:
        csv_output = export.export_companies_to_csv(run_id)

        print("✅ Export completed successfully!")
        print()

        # Count CSV lines (excluding header)
        csv_lines = csv_output.strip().split("\n")
        csv_row_count = len(csv_lines) - 1  # Exclude header
        print(f"📄 CSV Output: {csv_row_count} companies exported")
        print()

        # Check post-export state
        print("📊 Post-Export State:")

        validated_after = supabase_client._get_pm(
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{run_id}", "status": "eq.validated", "select": "id,name,domain"}
        )
        promoted_after = supabase_client._get_pm(
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{run_id}", "status": "eq.promoted", "select": "id,name,domain"}
        )

        print(f"  Validated Companies: {len(validated_after)} (was {len(validated)})")
        print(f"  Promoted Companies: {len(promoted_after)} (was {len(promoted)})")
        print()

        # Show promoted companies
        print("✨ Promoted Companies:")
        for i, co in enumerate(promoted_after, 1):
            print(f"  {i}. {co.get('name')} (domain: {co.get('domain')})")
        print()

        # Check research_database after export
        print("📚 Checking research_database after export...")
        try:
            new_companies = supabase_client._execute_query(
                "SELECT COUNT(*) as count FROM companies"
            )
            new_count = new_companies[0].get("count", 0) if new_companies else 0
            persisted_count = new_count - existing_count
            print(f"  Total companies in research_database: {new_count} (was {existing_count})")
            print(f"  Newly persisted: {persisted_count}")
        except Exception as e:
            print(f"  ⚠️  Could not count research_database: {e}")
        print()

        # Validation checks
        print("🔍 Validation Checks:")
        checks = []

        # Check 1: CSV has exactly target_quantity rows
        if csv_row_count == target_qty:
            checks.append(("✅", f"CSV has exactly {target_qty} companies"))
        else:
            checks.append(("❌", f"CSV has {csv_row_count} companies, expected {target_qty}"))

        # Check 2: Promoted count equals target_quantity
        if len(promoted_after) == target_qty:
            checks.append(("✅", f"Promoted count equals target ({target_qty})"))
        else:
            checks.append(("❌", f"Promoted count is {len(promoted_after)}, expected {target_qty}"))

        # Check 3: Validated + Promoted = Original total
        total_after = len(validated_after) + len(promoted_after)
        if total_after == len(validated):
            checks.append(("✅", f"Total companies unchanged ({total_after})"))
        else:
            checks.append(("❌", f"Total companies changed: {total_after} vs {len(validated)}"))

        # Check 4: Excess companies persisted
        expected_persisted = len(validated) - target_qty
        if persisted_count >= expected_persisted:
            checks.append(("✅", f"At least {expected_persisted} companies persisted to research_database"))
        else:
            checks.append(("⚠️", f"Expected {expected_persisted} persisted, got {persisted_count}"))

        for status, message in checks:
            print(f"  {status} {message}")

        print()

        # Overall result
        if all(check[0] == "✅" for check in checks):
            print("=" * 80)
            print("✅ ALL TESTS PASSED!")
            print("=" * 80)
            return 0
        else:
            print("=" * 80)
            print("⚠️  Some checks failed - review above")
            print("=" * 80)
            return 1

    except Exception as e:
        print(f"❌ Export failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(test_export_with_promotion())
