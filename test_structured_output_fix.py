#!/usr/bin/env python3
"""Minimal test to validate structured output fix.

Tests that the agent populates LeadListOutput correctly after calling tools.
"""

import sys
from agents import Runner

from rv_agentic.agents.lead_list_agent import create_lead_list_agent, LeadListOutput


def test_minimal_discovery():
    """Test with minimal discovery target to validate structured output."""

    print("Creating Lead List Agent...")
    agent = create_lead_list_agent()

    # Minimal test prompt - just ask for 3 companies in San Francisco with Buildium
    prompt = """
DISCOVERY MODE - Find property management companies matching these criteria:

**Location:** San Francisco, CA
**PMS Required:** Buildium
**Discovery Target:** 3 companies (find at least 3)
**Contacts per company:** 1-2

You are in WORKER MODE - you MUST populate the LeadListOutput structure with companies and contacts.

Execute a minimal discovery:
1. Search for "San Francisco Buildium property management"
2. Use fetch_page on any company list URLs you find
3. Extract company profiles for discovered companies
4. Populate LeadListOutput with ALL companies found
5. Return the structured output

Remember: Empty companies array = FAILURE.
"""

    print("\nRunning agent...")
    print("=" * 80)

    try:
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=50,  # Limit turns for fast testing
        )

        # Extract structured output
        typed: LeadListOutput = result.final_output_as(LeadListOutput)

        print("\n" + "=" * 80)
        print("RESULT:")
        print(f"  Companies found: {len(typed.companies)}")
        print(f"  Contacts found: {len(typed.contacts)}")
        print(f"  Total found (metadata): {typed.total_found}")
        print(f"  Search exhausted: {typed.search_exhausted}")

        if typed.companies:
            print("\n  First company:")
            company = typed.companies[0]
            print(f"    Name: {company.name}")
            print(f"    Domain: {company.domain}")
            print(f"    City: {company.city}")
            print(f"    PMS: {company.pms_detected}")
            print(f"    Source: {company.discovery_source}")

        # Validate
        if len(typed.companies) == 0:
            print("\n❌ FAILURE: No companies in structured output!")
            return False
        elif len(typed.companies) < 3:
            print(f"\n⚠️  WARNING: Only {len(typed.companies)} companies found (target: 3)")
            print("   But at least output is populated - FIX IS WORKING!")
            return True
        else:
            print(f"\n✅ SUCCESS: Found {len(typed.companies)} companies!")
            return True

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_minimal_discovery()
    sys.exit(0 if success else 1)
