"""Tests for lead list batching functionality.

Verifies that batching logic properly handles large company requests
by breaking them into smaller batches with checkpointing.
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


def test_batch_size_configuration():
    """Test that batch size can be configured via environment variable."""
    from rv_agentic.workers import lead_list_runner

    # Default batch size should be 10
    with patch.dict(os.environ, {}, clear=False):
        # The batch size is read in process_run, so we need to mock the agent call
        mock_agent = Mock()
        mock_run = {
            "id": "test-run-123",
            "target_quantity": 25,
            "criteria": {"pms": "Buildium", "state": "TX", "quantity": 25},
            "stage": "company_discovery"
        }

        with patch('rv_agentic.workers.lead_list_runner._sb') as mock_sb:
            # Mock returning that we have 0 companies so far
            mock_sb.get_pm_company_gap.return_value = {
                "companies_ready": 0,
                "companies_validated": 0
            }
            mock_sb.get_blocked_domains.return_value = []

            with patch('rv_agentic.workers.lead_list_runner.retry.retry_agent_call') as mock_retry:
                mock_result = Mock()
                mock_typed = Mock()
                mock_typed.companies = []
                mock_typed.contacts = []
                mock_result.final_output_as.return_value = mock_typed
                mock_retry.return_value = mock_result

                # Process the run
                lead_list_runner.process_run(mock_run)

                # Verify the prompt contains batch information
                call_args = mock_retry.call_args
                prompt = call_args[0][2]  # Third positional arg is the prompt

                # Should mention batch mode and target of 10 (default batch size)
                assert "BATCH MODE" in prompt
                assert "10 more companies" in prompt


def test_batch_progress_tracking():
    """Test that batching correctly tracks progress across multiple batches."""
    from rv_agentic.workers import lead_list_runner

    mock_agent = Mock()
    mock_run = {
        "id": "test-run-456",
        "target_quantity": 25,
        "criteria": {"pms": "AppFolio", "state": "CA", "quantity": 25},
        "stage": "company_discovery"
    }

    with patch('rv_agentic.workers.lead_list_runner._sb') as mock_sb:
        # Simulate that we already have 10 companies
        mock_sb.get_pm_company_gap.return_value = {
            "companies_ready": 10,
            "companies_validated": 10
        }
        mock_sb.get_blocked_domains.return_value = []

        with patch('rv_agentic.workers.lead_list_runner.retry.retry_agent_call') as mock_retry:
            mock_result = Mock()
            mock_typed = Mock()
            mock_typed.companies = []
            mock_typed.contacts = []
            mock_result.final_output_as.return_value = mock_typed
            mock_retry.return_value = mock_result

            # Process the run
            lead_list_runner.process_run(mock_run)

            # Verify the prompt shows we already have 10 companies
            call_args = mock_retry.call_args
            prompt = call_args[0][2]

            assert "We already have 10" in prompt
            assert "remaining: 15" in prompt


def test_batch_early_exit_when_target_met():
    """Test that batching exits early when target quantity is already met."""
    from rv_agentic.workers import lead_list_runner

    mock_agent = Mock()
    mock_run = {
        "id": "test-run-789",
        "target_quantity": 15,
        "criteria": {"pms": "Yardi", "state": "FL", "quantity": 15},
        "stage": "company_discovery"
    }

    with patch('rv_agentic.workers.lead_list_runner._sb') as mock_sb:
        # Simulate that we already have 15 companies (target met)
        mock_sb.get_pm_company_gap.return_value = {
            "companies_ready": 15,
            "companies_validated": 15
        }
        mock_sb.get_blocked_domains.return_value = []

        with patch('rv_agentic.workers.lead_list_runner.retry.retry_agent_call') as mock_retry:
            # Process the run
            result = lead_list_runner.process_run(mock_run)

            # Should not call the agent since target is already met
            mock_retry.assert_not_called()

            # Should return None indicating no processing needed
            assert result is None


def test_batch_calculates_correct_batch_target():
    """Test that batch target is minimum of batch_size and remaining companies."""
    from rv_agentic.workers import lead_list_runner

    test_cases = [
        # (target, existing, batch_size, expected_batch_target)
        (25, 0, 10, 10),   # First batch: 10
        (25, 10, 10, 10),  # Second batch: 10
        (25, 20, 10, 5),   # Last batch: only 5 remaining
        (5, 0, 10, 5),     # Small request: only need 5
    ]

    for target, existing, batch_size, expected_target in test_cases:
        mock_run = {
            "id": f"test-run-{target}-{existing}",
            "target_quantity": target,
            "criteria": {"pms": "RealPage", "state": "TX", "quantity": target},
            "stage": "company_discovery"
        }

        with patch.dict(os.environ, {"LEAD_LIST_BATCH_SIZE": str(batch_size)}, clear=False):
            with patch('rv_agentic.workers.lead_list_runner._sb') as mock_sb:
                mock_sb.get_pm_company_gap.return_value = {
                    "companies_ready": existing,
                    "companies_validated": existing
                }
                mock_sb.get_blocked_domains.return_value = []

                with patch('rv_agentic.workers.lead_list_runner.retry.retry_agent_call') as mock_retry:
                    mock_result = Mock()
                    mock_typed = Mock()
                    mock_typed.companies = []
                    mock_typed.contacts = []
                    mock_result.final_output_as.return_value = mock_typed
                    mock_retry.return_value = mock_result

                    # Process the run
                    lead_list_runner.process_run(mock_run)

                    # Verify the prompt has correct batch target
                    call_args = mock_retry.call_args
                    prompt = call_args[0][2]

                    assert f"{expected_target} more companies" in prompt


def test_heartbeat_shows_batch_progress():
    """Test that heartbeat updates show current batch progress."""
    from rv_agentic.workers import lead_list_runner

    mock_agent = Mock()
    mock_heartbeat = Mock()
    mock_run = {
        "id": "test-run-hb",
        "target_quantity": 30,
        "criteria": {"pms": "Entrata", "state": "AZ", "quantity": 30},
        "stage": "company_discovery"
    }

    with patch('rv_agentic.workers.lead_list_runner._sb') as mock_sb:
        # Simulate 12 companies already found
        mock_sb.get_pm_company_gap.return_value = {
            "companies_ready": 12,
            "companies_validated": 12
        }
        mock_sb.get_blocked_domains.return_value = []

        with patch('rv_agentic.workers.lead_list_runner.retry.retry_agent_call') as mock_retry:
            mock_result = Mock()
            mock_typed = Mock()
            mock_typed.companies = []
            mock_typed.contacts = []
            mock_result.final_output_as.return_value = mock_typed
            mock_retry.return_value = mock_result

            # Process the run with heartbeat
            lead_list_runner.process_run(mock_run, mock_heartbeat)

            # Verify heartbeat was updated with progress
            mock_heartbeat.update_task.assert_called()
            call_args = mock_heartbeat.update_task.call_args

            # Should show "12/30 companies" in task description
            task_desc = call_args[1]["task"]
            assert "12/30" in task_desc
            assert "PMS=Entrata" in task_desc
            assert "State=AZ" in task_desc


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
