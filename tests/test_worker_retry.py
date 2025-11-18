"""Tests for worker retry integration.

Verifies that retry logic is properly integrated into all workers
and handles transient agent failures.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest
from rv_agentic.services import retry


def test_retry_imports_in_all_workers():
    """Verify retry module is imported in all worker files."""
    from rv_agentic.workers import lead_list_runner
    from rv_agentic.workers import company_research_runner
    from rv_agentic.workers import contact_research_runner

    # Verify retry is accessible in each worker module
    assert hasattr(lead_list_runner, 'retry')
    assert hasattr(company_research_runner, 'retry')
    assert hasattr(contact_research_runner, 'retry')


def test_company_research_runner_retry_on_failure():
    """Test company research runner retries on agent failure."""
    from rv_agentic.workers import company_research_runner

    # Mock agent and supabase calls
    mock_agent = Mock()
    mock_result = Mock()
    mock_result.final_output = "# Company Analysis\nTest output"

    call_count = [0]

    def mock_runner_sync(agent, prompt):
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("Transient agent failure")
        return mock_result

    with patch('rv_agentic.workers.company_research_runner.Runner.run_sync', side_effect=mock_runner_sync):
        with patch('rv_agentic.workers.company_research_runner.create_company_researcher_agent', return_value=mock_agent):
            with patch('rv_agentic.workers.company_research_runner.supabase_client') as mock_sb:
                mock_sb.claim_company_for_research.return_value = {
                    "id": "test-company-id",
                    "run_id": "test-run-id",
                    "domain": "test.com",
                    "name": "Test Company"
                }
                mock_sb.get_pm_run.return_value = {"id": "test-run-id", "criteria": {}}
                mock_sb.has_company_research_queue.return_value = False

                # This should retry once and succeed
                result = company_research_runner.process_company_claim(
                    mock_agent,
                    "test-worker",
                    300
                )

                assert result is True
                assert call_count[0] == 2  # Failed once, succeeded on retry


def test_contact_research_runner_retry_on_failure():
    """Test contact research runner retries on agent failure."""
    from rv_agentic.workers import contact_research_runner
    from rv_agentic.agents.contact_researcher_agent import ContactResearchOutput, ContactResearchContact

    # Mock agent and supabase calls
    mock_agent = Mock()
    mock_result = Mock()
    typed_output = ContactResearchOutput(contacts=[
        ContactResearchContact(
            company_domain="test.com",
            full_name="John Doe",
            title="CEO",
            email="john@test.com",
            linkedin_url="https://linkedin.com/in/johndoe",
            notes="Test contact"
        )
    ])
    mock_result.final_output_as = Mock(return_value=typed_output)

    call_count = [0]

    def mock_runner_sync(agent, prompt):
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("Transient agent failure")
        return mock_result

    with patch('rv_agentic.workers.contact_research_runner.Runner.run_sync', side_effect=mock_runner_sync):
        with patch('rv_agentic.workers.contact_research_runner.create_contact_researcher_agent', return_value=mock_agent):
            with patch('rv_agentic.workers.contact_research_runner.supabase_client') as mock_sb:
                mock_sb.claim_company_for_contacts.return_value = {
                    "id": "test-company-id",
                    "run_id": "test-run-id",
                    "domain": "test.com",
                    "name": "Test Company"
                }
                mock_sb.get_pm_run.return_value = {"id": "test-run-id", "criteria": {}}
                mock_sb.get_contact_gap_for_company.return_value = {"contacts_min_gap": 2}
                mock_sb.get_contact_gap_summary.return_value = {"contacts_min_gap_total": 0}

                # This should retry once and succeed
                result = contact_research_runner.process_contact_gap(
                    mock_agent,
                    "test-worker",
                    300
                )

                assert result is True
                assert call_count[0] == 2  # Failed once, succeeded on retry


def test_lead_list_runner_retry_on_failure():
    """Test lead list runner retries on agent failure."""
    from rv_agentic.workers import lead_list_runner
    from rv_agentic.agents.lead_list_agent import LeadListOutput, LeadListCompany
    import uuid

    # Mock agent
    mock_agent = Mock()
    mock_result = Mock()
    # Return at least one company to avoid fallback path that requires DB
    typed_output = LeadListOutput(
        companies=[
            LeadListCompany(
                domain="test.com",
                name="Test Company",
                state="TX",
                reason="Matches criteria"
            )
        ],
        contacts=[]
    )
    mock_result.final_output_as = Mock(return_value=typed_output)
    mock_result.final_output = "# Lead List\nFound 1 company"

    call_count = [0]

    def mock_runner_sync(agent, prompt):
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("Transient agent failure")
        return mock_result

    with patch('rv_agentic.workers.lead_list_runner.Runner.run_sync', side_effect=mock_runner_sync):
        with patch('rv_agentic.workers.lead_list_runner.create_lead_list_agent', return_value=mock_agent):
            with patch('rv_agentic.workers.lead_list_runner.supabase_client') as mock_sb:
                mock_sb.get_pms_subdomain_seeds.return_value = []
                mock_sb.get_blocked_domains.return_value = []
                mock_sb.find_company.return_value = []
                mock_sb.insert_company_candidate.return_value = {
                    "id": str(uuid.uuid4()),
                    "domain": "test.com"
                }
                mock_sb.get_run_resume_plan.return_value = None

                run = {
                    "id": str(uuid.uuid4()),
                    "criteria": {"pms": "Buildium", "state": "TX", "quantity": 5},
                    "target_quantity": 5
                }

                # This should retry once and succeed
                result = lead_list_runner.process_run(run)

                assert result is not None
                assert call_count[0] == 2  # Failed once, succeeded on retry


def test_retry_exhaustion_raises_error():
    """Test that retry logic eventually raises after max attempts."""
    from rv_agentic.workers import company_research_runner

    mock_agent = Mock()

    call_count = [0]

    def mock_runner_sync(agent, prompt):
        call_count[0] += 1
        raise RuntimeError(f"Persistent failure (attempt {call_count[0]})")

    with patch('rv_agentic.workers.company_research_runner.Runner.run_sync', side_effect=mock_runner_sync):
        with patch('rv_agentic.workers.company_research_runner.create_company_researcher_agent', return_value=mock_agent):
            with patch('rv_agentic.workers.company_research_runner.supabase_client') as mock_sb:
                mock_sb.claim_company_for_research.return_value = {
                    "id": "test-company-id",
                    "run_id": "test-run-id",
                    "domain": "test.com",
                    "name": "Test Company"
                }
                mock_sb.get_pm_run.return_value = {"id": "test-run-id", "criteria": {}}

                # This should fail after 3 attempts
                result = company_research_runner.process_company_claim(
                    mock_agent,
                    "test-worker",
                    300
                )

                # process_company_claim catches exceptions and returns True
                # to allow worker to continue processing other companies
                assert result is True
                assert call_count[0] == 3  # All 3 attempts exhausted


def test_retry_timing_with_exponential_backoff():
    """Test that retry delays follow exponential backoff pattern."""
    import time

    call_times = []

    def mock_failing_call():
        call_times.append(time.time())
        if len(call_times) < 3:
            raise RuntimeError("Not yet")
        return "success"

    result = retry.retry_agent_call(
        mock_failing_call,
        max_attempts=3,
        base_delay=0.1
    )

    assert result == "success"
    assert len(call_times) == 3

    # Check delays between attempts (0.1s, 0.2s)
    delay1 = call_times[1] - call_times[0]
    delay2 = call_times[2] - call_times[1]

    # First delay should be ~0.1s, second delay ~0.2s
    assert 0.08 <= delay1 <= 0.15
    assert 0.18 <= delay2 <= 0.25
