"""Job handler infrastructure: protocol, registry, and dispatch."""

from thinktank.handlers.base import JobHandler
from thinktank.handlers.registry import JOB_HANDLERS, get_handler, register_handler

__all__ = ["JobHandler", "JOB_HANDLERS", "register_handler", "get_handler"]
