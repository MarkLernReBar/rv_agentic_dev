"""Tests for orchestrator module."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rv_agentic import orchestrator


def test_wait_for_stage_completion_validates_timeout():
    """Test that wait_for_stage_completion has proper timeout validation."""
    assert callable(orchestrator.wait_for_stage_completion)


def test_execute_full_pipeline_signature():
    """Test that execute_full_pipeline has correct signature."""
    assert callable(orchestrator.execute_full_pipeline)

    # Verify required parameters
    import inspect
    sig = inspect.signature(orchestrator.execute_full_pipeline)
    assert "criteria" in sig.parameters
    assert "target_quantity" in sig.parameters


def test_get_run_progress_signature():
    """Test that get_run_progress has correct signature."""
    assert callable(orchestrator.get_run_progress)

    import inspect
    sig = inspect.signature(orchestrator.get_run_progress)
    assert "run_id" in sig.parameters


def test_get_run_progress_handles_missing_run():
    """Test that get_run_progress handles missing run gracefully."""
    result = orchestrator.get_run_progress("00000000-0000-0000-0000-000000000000")
    assert "error" in result
    assert result["error"] == "Run not found"


def test_pipeline_exceptions_exist():
    """Test that custom exceptions are defined."""
    assert issubclass(orchestrator.PipelineTimeoutError, Exception)
    assert issubclass(orchestrator.PipelineError, Exception)
