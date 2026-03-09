"""Handler registry: maps job_type strings to handler callables.

Registry is populated at import time. The worker loop looks up
handlers here via get_handler().
"""

from src.thinktank.handlers.base import JobHandler
from src.thinktank.handlers.fetch_podcast_feed import handle_fetch_podcast_feed
from src.thinktank.handlers.refresh_due_sources import handle_refresh_due_sources
from src.thinktank.handlers.tag_content_thinkers import handle_tag_content_thinkers

# Key: job_type string, Value: callable matching JobHandler protocol.
JOB_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    """Register a handler for a job type.

    Raises ValueError if a handler is already registered for the given type.
    This prevents accidental overwrites and makes registration conflicts
    visible immediately at startup.

    Args:
        job_type: The job type string (e.g., 'discover_thinker').
        handler: An async callable matching the JobHandler protocol.
    """
    if job_type in JOB_HANDLERS:
        raise ValueError(f"Handler already registered for job type: {job_type}")
    JOB_HANDLERS[job_type] = handler


def get_handler(job_type: str) -> JobHandler | None:
    """Get the handler for a job type.

    Returns None if no handler is registered for the given type.
    The worker loop uses this to dispatch jobs and handles the None
    case by failing the job with error_category='handler_not_found'.

    Args:
        job_type: The job type string to look up.

    Returns:
        The registered handler, or None if not registered.
    """
    return JOB_HANDLERS.get(job_type)


# --- Phase 3 handler registrations ---
register_handler("fetch_podcast_feed", handle_fetch_podcast_feed)
register_handler("refresh_due_sources", handle_refresh_due_sources)
register_handler("tag_content_thinkers", handle_tag_content_thinkers)
