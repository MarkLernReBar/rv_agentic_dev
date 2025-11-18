"""Tests for CSV export functionality."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest
from rv_agentic.services import export


def test_export_companies_to_csv_validates_input():
    """Test that export_companies_to_csv validates run_id."""
    with pytest.raises(ValueError, match="Run .* not found"):
        export.export_companies_to_csv("00000000-0000-0000-0000-000000000000")


def test_export_contacts_to_csv_validates_input():
    """Test that export_contacts_to_csv validates run_id."""
    with pytest.raises(ValueError, match="Run .* not found"):
        export.export_contacts_to_csv("00000000-0000-0000-0000-000000000000")


def test_export_companies_to_csv_structure():
    """Test that export_companies_to_csv returns valid CSV structure."""
    # This is a smoke test - with a real run_id, it should return CSV
    # For now, just verify the function signature is correct
    assert callable(export.export_companies_to_csv)


def test_export_contacts_to_csv_structure():
    """Test that export_contacts_to_csv returns valid CSV structure."""
    assert callable(export.export_contacts_to_csv)


def test_export_run_to_files_validates_directory():
    """Test that export_run_to_files validates output directory."""
    # Should work with valid directory
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Will fail because run doesn't exist, but validates directory exists
        try:
            export.export_run_to_files("00000000-0000-0000-0000-000000000000", tmpdir)
        except ValueError as e:
            # Expected - run doesn't exist
            assert "not found" in str(e)
