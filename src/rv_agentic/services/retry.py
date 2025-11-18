"""Retry logic with exponential backoff for agent calls.

Provides decorators and utilities for retrying operations that may fail
transiently (network issues, API rate limits, temporary service unavailability).
"""

import functools
import logging
import time
from typing import Any, Callable, Optional, Type, Tuple

logger = logging.getLogger(__name__)


def with_exponential_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None,
):
    """Decorator that retries a function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (default 3)
        base_delay: Initial delay in seconds (default 1.0)
        max_delay: Maximum delay in seconds (default 60.0)
        exponential_base: Base for exponential backoff (default 2.0)
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback(exception, attempt) called on each retry

    Example:
        @with_exponential_backoff(max_attempts=3)
        def call_api():
            return requests.get('https://api.example.com')

    Returns:
        Decorated function that retries on failure
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        # Final attempt failed - log and re-raise
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_attempts,
                            str(e),
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)

                    logger.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs...",
                        func.__name__,
                        attempt,
                        max_attempts,
                        str(e),
                        delay,
                    )

                    # Call retry callback if provided
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception as callback_error:
                            logger.error(
                                "on_retry callback failed: %s", callback_error
                            )

                    # Wait before retry
                    time.sleep(delay)

            # Should not reach here, but satisfy type checker
            if last_exception:
                raise last_exception
            raise RuntimeError(f"{func.__name__} failed without exception")

        return wrapper

    return decorator


def retry_agent_call(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    **kwargs,
) -> Any:
    """Retry an agent call with exponential backoff.

    This is a functional interface (not decorator) for retrying
    agent calls. Useful when you can't use decorators.

    Args:
        func: Function to call
        *args: Positional arguments to func
        max_attempts: Maximum number of attempts
        base_delay: Initial delay in seconds
        **kwargs: Keyword arguments to func

    Returns:
        Result of func(*args, **kwargs)

    Raises:
        Exception from the final failed attempt

    Example:
        from agents import Runner
        result = retry_agent_call(
            Runner.run_sync,
            agent,
            prompt,
            max_attempts=3
        )
    """
    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e

            if attempt == max_attempts:
                logger.error(
                    "Agent call failed after %d attempts: %s",
                    max_attempts,
                    str(e),
                )
                raise

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Agent call attempt %d/%d failed: %s. Retrying in %.1fs...",
                attempt,
                max_attempts,
                str(e),
                delay,
            )
            time.sleep(delay)

    if last_exception:
        raise last_exception
    raise RuntimeError("Agent call failed without exception")


class RetryableAgentCall:
    """Context manager for retryable agent calls with custom error handling.

    Example:
        with RetryableAgentCall(max_attempts=3) as retry:
            result = retry(Runner.run_sync, agent, prompt)
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        on_failure: Optional[Callable[[Exception], None]] = None,
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.on_failure = on_failure
        self.attempt_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Cleanup if needed
        return False  # Don't suppress exceptions

    def __call__(self, func: Callable, *args, **kwargs) -> Any:
        """Execute func with retry logic."""
        return retry_agent_call(
            func,
            *args,
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            **kwargs,
        )


# Pre-configured retry decorators for common use cases

agent_retry = with_exponential_backoff(
    max_attempts=3,
    base_delay=1.0,
    max_delay=10.0,
    exceptions=(Exception,),
)
"""Standard retry decorator for agent calls (3 attempts, 1s-2s-4s)."""


database_retry = with_exponential_backoff(
    max_attempts=5,
    base_delay=0.5,
    max_delay=5.0,
    exceptions=(Exception,),
)
"""Retry decorator for database operations (5 attempts, faster backoff)."""


mcp_retry = with_exponential_backoff(
    max_attempts=3,
    base_delay=2.0,
    max_delay=30.0,
    exceptions=(Exception,),
)
"""Retry decorator for MCP tool calls (3 attempts, longer delays)."""
