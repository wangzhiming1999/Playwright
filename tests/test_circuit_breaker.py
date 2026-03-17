"""
Tests for agent/circuit_breaker.py — CircuitBreaker state machine.
"""

from unittest.mock import patch

from agent.circuit_breaker import CircuitBreaker, CircuitState


class TestInitialState:
    def test_starts_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    def test_check_returns_true_when_closed(self):
        cb = CircuitBreaker("test")
        assert cb.check() is True


class TestFailureToOpen:
    def test_opens_after_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_check_returns_false_when_open(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.check() is False


class TestCooldownToHalfOpen:
    def test_transitions_to_half_open_after_cooldown(self):
        cb = CircuitBreaker("test", failure_threshold=2, cooldown=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate time passing beyond cooldown
        with patch("agent.circuit_breaker.time.monotonic", return_value=cb._open_time + 11):
            assert cb.state == CircuitState.HALF_OPEN

    def test_check_returns_true_when_half_open(self):
        cb = CircuitBreaker("test", failure_threshold=2, cooldown=5.0)
        cb.record_failure()
        cb.record_failure()

        with patch("agent.circuit_breaker.time.monotonic", return_value=cb._open_time + 6):
            assert cb.check() is True

    def test_stays_open_before_cooldown(self):
        cb = CircuitBreaker("test", failure_threshold=2, cooldown=30.0)
        cb.record_failure()
        cb.record_failure()

        with patch("agent.circuit_breaker.time.monotonic", return_value=cb._open_time + 10):
            assert cb.state == CircuitState.OPEN


class TestRecovery:
    def test_success_in_half_open_closes(self):
        cb = CircuitBreaker("test", failure_threshold=2, cooldown=5.0)
        cb.record_failure()
        cb.record_failure()

        with patch("agent.circuit_breaker.time.monotonic", return_value=cb._open_time + 6):
            _ = cb.state  # trigger transition to HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestReset:
    def test_reset_returns_to_closed(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0


class TestLogFn:
    def test_log_fn_called_on_open(self):
        logs = []
        cb = CircuitBreaker("test_api", failure_threshold=2, log_fn=logs.append)
        cb.record_failure()
        cb.record_failure()
        assert any("熔断" in msg for msg in logs)

    def test_log_fn_called_on_check_while_open(self):
        logs = []
        cb = CircuitBreaker("test_api", failure_threshold=1, cooldown=999, log_fn=logs.append)
        cb.record_failure()
        logs.clear()
        cb.check()
        assert any("熔断中" in msg for msg in logs)
