"""Structured JSON logging with correlation ID support.

Uses structlog with contextvars for per-request correlation ID propagation.
Every log line contains: timestamp (ISO), log_level, logger name, and any
bound context (service name, correlation_id).
"""

import logging
import sys
from typing import Any

import structlog


def _rename_level_to_log_level(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Rename 'level' key to 'log_level' for spec compliance.

    structlog's add_log_level produces 'level' in some processor chains.
    The spec requires 'log_level' on every log entry.
    """
    if "level" in event_dict and "log_level" not in event_dict:
        event_dict["log_level"] = event_dict.pop("level")
    return event_dict


def configure_logging(service_name: str, log_level: str = "INFO") -> None:
    """Configure structlog for JSON output with standard processors.

    Must be called once at application startup. The processor chain:
    1. merge_contextvars - picks up correlation_id, service from context
    2. add_log_level - adds log_level field
    3. add_logger_name - adds logger field
    4. TimeStamper - adds ISO timestamp
    5. StackInfoRenderer - renders stack info
    6. format_exc_info - formats exceptions
    7. UnicodeDecoder - ensures unicode
    8. JSONRenderer - outputs JSON
    """
    # Configure stdlib logging to route through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, log_level.upper(), logging.INFO),
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            _rename_level_to_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure the formatter for stdlib handler
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            _rename_level_to_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
        ],
    )

    # Apply formatter to root handler
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger with the given name.

    Usage:
        logger = get_logger(__name__)
        logger.info("something happened", key="value")
    """
    return structlog.get_logger(name)
