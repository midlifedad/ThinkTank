"""Unit tests for the structured logging system.

Tests verify:
1. Log output is valid JSON
2. Required fields (timestamp, log_level, service, correlation_id) are present
3. Structured logging produces correctly formatted entries
"""

import io
import json
import logging

import structlog


class TestStructuredLogging:
    """Verify structlog produces JSON-formatted output with required fields."""

    def test_log_output_is_json(self, capsys):
        """Capture log output and parse as JSON to verify format."""
        from thinktank.logging import configure_logging, get_logger

        configure_logging("test-service", log_level="DEBUG")

        logger = get_logger("test")
        logger.info("test message")

        captured = capsys.readouterr()
        # Output should be valid JSON
        log_entry = json.loads(captured.err.strip().split("\n")[-1])
        assert isinstance(log_entry, dict)

    def test_log_contains_timestamp(self, capsys):
        """Log output JSON has 'timestamp' key in ISO format."""
        from thinktank.logging import configure_logging, get_logger

        configure_logging("test-service", log_level="DEBUG")

        logger = get_logger("test")
        logger.info("timestamp test")

        captured = capsys.readouterr()
        log_entry = json.loads(captured.err.strip().split("\n")[-1])
        assert "timestamp" in log_entry
        # ISO format includes T separator
        assert "T" in log_entry["timestamp"] or "-" in log_entry["timestamp"]

    def test_log_contains_level(self, capsys):
        """Log output JSON has 'log_level' key."""
        from thinktank.logging import configure_logging, get_logger

        configure_logging("test-service", log_level="DEBUG")

        logger = get_logger("test")
        logger.info("level test")

        captured = capsys.readouterr()
        log_entry = json.loads(captured.err.strip().split("\n")[-1])
        assert "log_level" in log_entry
        assert log_entry["log_level"] == "info"

    def test_log_contains_service(self, capsys):
        """After binding service contextvar, log output has 'service' key."""
        from thinktank.logging import configure_logging, get_logger

        configure_logging("test-service", log_level="DEBUG")

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(service="test-service")

        logger = get_logger("test")
        logger.info("service test")

        captured = capsys.readouterr()
        log_entry = json.loads(captured.err.strip().split("\n")[-1])
        assert "service" in log_entry
        assert log_entry["service"] == "test-service"

    def test_correlation_id_in_log(self, capsys):
        """After binding correlation_id contextvar, log output has 'correlation_id' key."""
        from thinktank.logging import configure_logging, get_logger

        configure_logging("test-service", log_level="DEBUG")

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id="test-uuid-123")

        logger = get_logger("test")
        logger.info("correlation test")

        captured = capsys.readouterr()
        log_entry = json.loads(captured.err.strip().split("\n")[-1])
        assert "correlation_id" in log_entry
        assert log_entry["correlation_id"] == "test-uuid-123"

    def test_log_contains_logger_name(self, capsys):
        """Log output includes the logger name."""
        from thinktank.logging import configure_logging, get_logger

        configure_logging("test-service", log_level="DEBUG")

        logger = get_logger("my_module")
        logger.info("logger name test")

        captured = capsys.readouterr()
        log_entry = json.loads(captured.err.strip().split("\n")[-1])
        assert "logger" in log_entry
