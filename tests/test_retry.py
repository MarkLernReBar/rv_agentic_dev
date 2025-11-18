"""Tests for retry logic."""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest
from rv_agentic.services import retry


def test_with_exponential_backoff_success():
    """Test that decorator returns immediately on success."""
    call_count = [0]

    @retry.with_exponential_backoff(max_attempts=3)
    def succeeds_immediately():
        call_count[0] += 1
        return "success"

    result = succeeds_immediately()
    assert result == "success"
    assert call_count[0] == 1  # Only called once


def test_with_exponential_backoff_eventual_success():
    """Test that decorator retries until success."""
    call_count = [0]

    @retry.with_exponential_backoff(max_attempts=3, base_delay=0.1)
    def succeeds_on_third_try():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ValueError(f"Attempt {call_count[0]} failed")
        return "success"

    start = time.time()
    result = succeeds_on_third_try()
    elapsed = time.time() - start

    assert result == "success"
    assert call_count[0] == 3
    # Should have delays of 0.1s and 0.2s = 0.3s minimum
    assert elapsed >= 0.3


def test_with_exponential_backoff_all_failures():
    """Test that decorator raises after max attempts."""
    call_count = [0]

    @retry.with_exponential_backoff(max_attempts=3, base_delay=0.1)
    def always_fails():
        call_count[0] += 1
        raise ValueError(f"Attempt {call_count[0]} failed")

    with pytest.raises(ValueError, match="Attempt 3 failed"):
        always_fails()

    assert call_count[0] == 3  # All 3 attempts made


def test_retry_agent_call_success():
    """Test functional retry interface."""
    call_count = [0]

    def mock_agent_call(value):
        call_count[0] += 1
        return f"result: {value}"

    result = retry.retry_agent_call(
        mock_agent_call,
        "test",
        max_attempts=3
    )

    assert result == "result: test"
    assert call_count[0] == 1


def test_retry_agent_call_with_retries():
    """Test functional retry interface with failures."""
    call_count = [0]

    def mock_agent_call():
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("Temporary failure")
        return "success"

    result = retry.retry_agent_call(
        mock_agent_call,
        max_attempts=3,
        base_delay=0.1
    )

    assert result == "success"
    assert call_count[0] == 2


def test_retryable_agent_call_context_manager():
    """Test context manager interface."""
    call_count = [0]

    def mock_call():
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("Failure")
        return "success"

    with retry.RetryableAgentCall(max_attempts=3, base_delay=0.1) as retrier:
        result = retrier(mock_call)

    assert result == "success"
    assert call_count[0] == 2


def test_exponential_backoff_timing():
    """Test that backoff delays follow exponential pattern."""
    call_count = [0]
    call_times = []

    @retry.with_exponential_backoff(max_attempts=3, base_delay=0.1)
    def fails_twice():
        call_times.append(time.time())
        call_count[0] += 1
        if call_count[0] < 3:
            raise ValueError("Not yet")
        return "done"

    fails_twice()

    # Check delays between attempts
    assert len(call_times) == 3
    delay1 = call_times[1] - call_times[0]
    delay2 = call_times[2] - call_times[1]

    # First delay should be ~0.1s, second delay ~0.2s
    assert 0.08 <= delay1 <= 0.15
    assert 0.18 <= delay2 <= 0.25


def test_max_delay_cap():
    """Test that delay is capped at max_delay."""

    @retry.with_exponential_backoff(
        max_attempts=5,
        base_delay=10.0,
        max_delay=2.0,  # Cap at 2 seconds
    )
    def always_fails():
        raise ValueError("fail")

    start = time.time()
    with pytest.raises(ValueError):
        always_fails()
    elapsed = time.time() - start

    # 4 retries with max 2s each = 8s max (plus first attempt)
    # Should be less than if using uncapped exponential (10, 20, 40, 80s)
    assert elapsed < 10


def test_on_retry_callback():
    """Test that on_retry callback is called."""
    retry_info = []

    def track_retries(exception, attempt):
        retry_info.append((str(exception), attempt))

    @retry.with_exponential_backoff(
        max_attempts=3,
        base_delay=0.1,
        on_retry=track_retries
    )
    def fails_twice():
        if len(retry_info) < 2:
            raise ValueError(f"Attempt {len(retry_info) + 1}")
        return "success"

    result = fails_twice()

    assert result == "success"
    assert len(retry_info) == 2
    assert retry_info[0] == ("Attempt 1", 1)
    assert retry_info[1] == ("Attempt 2", 2)
