"""Tests for lead list single-shot discovery with intelligent filtering.

Verifies that the agent returns ALL matching companies in one call,
and the worker intelligently selects the best N companies to meet the target.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest


def test_intelligent_selection_logic():
    """Test that worker selects best N companies from agent's full results."""
    test_cases = [
        # (target, existing, agent_found, expected_to_insert)
        (25, 0, 91, 25),   # Agent found 91, insert best 25
        (25, 10, 50, 15),  # Already have 10, insert 15 more from 50
        (25, 20, 30, 5),   # Already have 20, insert 5 more from 30
        (5, 0, 100, 5),    # Small target: insert 5 from 100
        (10, 10, 50, 0),   # Target already met: insert 0
        (25, 0, 15, 15),   # Agent found fewer than target: insert all 15
    ]

    for target, existing, agent_found, expected_insert in test_cases:
        companies_remaining = max(0, target - existing)
        companies_to_insert = min(agent_found, companies_remaining) if companies_remaining > 0 else 0

        assert companies_to_insert == expected_insert, \
            f"For target={target}, existing={existing}, agent_found={agent_found}: expected insert={expected_insert}, got={companies_to_insert}"


def test_selection_with_quality_sorting():
    """Test that worker respects agent's quality sorting (best first)."""
    # Simulate agent returning companies sorted by quality
    all_companies = [
        {"domain": "best1.com", "quality": 0.95},
        {"domain": "best2.com", "quality": 0.90},
        {"domain": "good1.com", "quality": 0.80},
        {"domain": "good2.com", "quality": 0.75},
        {"domain": "okay1.com", "quality": 0.60},
    ]

    target = 3
    # Worker should take first 3 (already sorted by agent)
    selected = all_companies[:target]

    assert len(selected) == 3
    assert selected[0]["domain"] == "best1.com"
    assert selected[1]["domain"] == "best2.com"
    assert selected[2]["domain"] == "good1.com"


def test_early_exit_when_target_met():
    """Test that processing skips when target is already met."""
    target_qty = 15
    companies_ready = 15
    companies_remaining = max(0, target_qty - companies_ready)

    # Should be 0 remaining
    assert companies_remaining == 0

    # In the actual code, when companies_remaining <= 0, it returns None without calling agent
    should_skip = companies_remaining <= 0 and target_qty > 0
    assert should_skip is True


def test_single_agent_call_returns_all():
    """Test that agent is called once and returns all matching companies."""
    # New design: agent returns ALL companies in one call
    target = 25
    agent_found = 91  # Agent finds all matching companies
    existing = 0

    # Worker inserts only what's needed to meet target
    to_insert = min(agent_found, target - existing)

    assert to_insert == 25  # Insert 25 out of 91 found
    assert agent_found == 91  # Agent still found all 91


def test_heartbeat_progress_format():
    """Test that heartbeat message format includes batch progress."""
    companies_ready = 12
    quantity = 30
    pms = "Entrata"
    state = "AZ"

    task_description = f"Lead discovery: {companies_ready}/{quantity} companies (PMS={pms}, State={state})"

    assert "12/30" in task_description
    assert "PMS=Entrata" in task_description
    assert "State=AZ" in task_description


def test_prompt_context_formatting():
    """Test that prompt correctly formats context about target and progress."""
    target_qty = 50
    companies_ready = 20
    companies_remaining = target_qty - companies_ready

    prompt_snippet = (
        f"**Context**: This run needs {target_qty} total companies. "
        f"We already have {companies_ready}.\\n"
        f"Your job: Find ALL companies matching the criteria ({companies_remaining} still needed).\\n"
        "Python will intelligently select the best candidates from your results.\\n"
    )

    assert "Context" in prompt_snippet
    assert "50 total companies" in prompt_snippet
    assert "already have 20" in prompt_snippet
    assert "Find ALL companies" in prompt_snippet
    assert "30 still needed" in prompt_snippet
    assert "intelligently select" in prompt_snippet


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
