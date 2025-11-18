"""Integration tests for Phase 1 pipeline.

Tests the complete pipeline with real database interactions and mock workers.
"""

import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Load environment variables from .env.local
from dotenv import load_dotenv
env_file = ROOT / ".env.local"
if env_file.exists():
    load_dotenv(env_file)

import pytest
from rv_agentic.services import supabase_client, export
from rv_agentic import orchestrator


class MockWorkerSimulator:
    """Simulates worker behavior for testing without actual agent calls."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        # Use unique suffix to avoid conflicts with other test runs
        self.suffix = str(run_id)[:8]

    def simulate_company_discovery(self, num_companies: int = 5):
        """Simulate discovering N companies."""
        print(f"[MOCK] Simulating discovery of {num_companies} companies...")
        inserted = 0
        for i in range(num_companies):
            domain = f"testco{i+1}-{self.suffix}.com"
            result = supabase_client.insert_company_candidate(
                run_id=self.run_id,
                name=f"Test Company {i+1}",
                website=f"https://{domain}",
                domain=domain,
                state="TX",
                pms_detected="Buildium",
                discovery_source="test_mock",
                status="validated",
            )
            if result:
                inserted += 1
                print(f"[MOCK] Inserted company {i+1}: {result.get('id')}")
            else:
                print(f"[MOCK] Company {i+1} was duplicate (None returned)")

        # Advance to company_research stage
        supabase_client.set_run_stage(run_id=self.run_id, stage="company_research")
        print(f"[MOCK] Advanced to company_research stage")

    def simulate_company_research(self):
        """Simulate researching all companies."""
        companies = supabase_client._get_pm(  # type: ignore
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{self.run_id}", "select": "*"},
        )
        print(f"[MOCK] Researching {len(companies)} companies...")

        for company in companies:
            company_id = company.get("id")
            try:
                supabase_client.insert_company_research(
                    run_id=self.run_id,
                    company_id=company_id,
                    facts={"analysis_markdown": f"Mock research for {company.get('name')}"},
                    signals={"icp_fit": "Strong", "icp_tier": "Tier 1"},
                )
            except Exception as e:
                print(f"[MOCK] Insert research for {company_id} failed: {e}")

        # Advance to contact_discovery stage
        supabase_client.set_run_stage(run_id=self.run_id, stage="contact_discovery")
        print(f"[MOCK] Advanced to contact_discovery stage")

    def simulate_contact_discovery(self, contacts_per_company: int = 2):
        """Simulate discovering contacts for all companies."""
        companies = supabase_client._get_pm(  # type: ignore
            supabase_client.PM_COMPANY_CANDIDATES_TABLE,
            {"run_id": f"eq.{self.run_id}", "select": "*"},
        )
        print(f"[MOCK] Discovering {contacts_per_company} contacts per company...")

        for company in companies:
            company_id = company.get("id")
            company_name = company.get("name")
            domain = company.get("domain")

            for j in range(contacts_per_company):
                # Use company_id in linkedin to make it unique per company
                linkedin = f"https://linkedin.com/in/johndoe{j+1}-{str(company_id)[:8]}"
                # Provide unique idem_key to avoid empty string collision
                idem_key = f"{domain}-contact{j+1}"
                result = supabase_client.insert_contact_candidate(
                    run_id=self.run_id,
                    company_id=company_id,
                    full_name=f"John Doe {j+1}",
                    title="Property Manager",
                    email=f"john.doe{j+1}@{domain}",
                    linkedin_url=linkedin,
                    status="validated",
                    idem_key=idem_key,
                )
                if result:
                    print(f"[MOCK] Inserted contact {j+1} for {company_name}")
                else:
                    print(f"[MOCK] Contact {j+1} for {company_name} was duplicate")

        # Mark run as completed
        supabase_client.update_pm_run_status(run_id=self.run_id, status="completed")
        supabase_client.set_run_stage(run_id=self.run_id, stage="done")
        print(f"[MOCK] Run completed")


@pytest.fixture
def test_run():
    """Create a test run and clean up after."""
    run = supabase_client.create_pm_run(
        criteria={"pms": "Buildium", "state": "TX", "city": "Austin"},
        target_quantity=5,
        contacts_min=1,
        contacts_max=3,
    )
    run_id = run.get("id")

    yield run_id

    # Cleanup (optional - can keep for debugging)
    # try:
    #     supabase_client.delete_pm_run(run_id)
    # except:
    #     pass


def test_mock_worker_simulation(test_run):
    """Test that mock worker simulation works."""
    simulator = MockWorkerSimulator(test_run)

    # Simulate all stages
    simulator.simulate_company_discovery(num_companies=3)
    time.sleep(1)  # Small delay between stages

    simulator.simulate_company_research()
    time.sleep(1)

    simulator.simulate_contact_discovery(contacts_per_company=2)

    # Verify completion
    run = supabase_client.get_pm_run(test_run)
    assert run.get("status") == "completed"
    assert run.get("stage") == "done"

    # Verify companies
    companies = supabase_client._get_pm(  # type: ignore
        supabase_client.PM_COMPANY_CANDIDATES_TABLE,
        {"run_id": f"eq.{test_run}", "status": "eq.validated"},
    )
    assert len(companies) == 3

    # Verify contacts
    contacts = supabase_client._get_pm(  # type: ignore
        supabase_client.PM_CONTACT_CANDIDATES_TABLE,
        {"run_id": f"eq.{test_run}", "status": "eq.validated"},
    )
    assert len(contacts) == 6  # 3 companies * 2 contacts


def test_csv_export_after_mock_run(test_run):
    """Test CSV export works after mock run completion."""
    # Run mock simulation
    simulator = MockWorkerSimulator(test_run)
    simulator.simulate_company_discovery(num_companies=2)
    simulator.simulate_company_research()
    simulator.simulate_contact_discovery(contacts_per_company=2)

    # Test CSV export
    companies_csv = export.export_companies_to_csv(test_run)
    assert len(companies_csv) > 0
    # Check for unique domain with run suffix
    assert f"testco1-{simulator.suffix}.com" in companies_csv
    assert f"testco2-{simulator.suffix}.com" in companies_csv

    contacts_csv = export.export_contacts_to_csv(test_run)
    assert len(contacts_csv) > 0
    assert "John Doe" in contacts_csv
    assert f"@testco1-{simulator.suffix}.com" in contacts_csv


def test_progress_tracking(test_run):
    """Test progress tracking during mock run."""
    simulator = MockWorkerSimulator(test_run)

    # Initial progress
    progress = orchestrator.get_run_progress(test_run)
    assert progress["stage"] == "company_discovery"
    assert progress["companies"]["ready"] == 0

    # After discovery
    simulator.simulate_company_discovery(num_companies=5)
    progress = orchestrator.get_run_progress(test_run)
    assert progress["stage"] == "company_research"
    assert progress["companies"]["ready"] == 5
    assert progress["companies"]["progress_pct"] == 100

    # After research
    simulator.simulate_company_research()
    progress = orchestrator.get_run_progress(test_run)
    assert progress["stage"] == "contact_discovery"

    # After contacts
    simulator.simulate_contact_discovery(contacts_per_company=1)
    progress = orchestrator.get_run_progress(test_run)
    assert progress["status"] == "completed"
    # Note: contacts may show as 0 if gap view hasn't refreshed, but run is completed
    assert progress["stage"] == "done"


if __name__ == "__main__":
    """Allow running tests directly for debugging."""
    pytest.main([__file__, "-v", "-s"])
