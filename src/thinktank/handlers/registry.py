"""Handler registry: maps job_type strings to handler callables.

Registry is populated at import time. The worker loop looks up
handlers here via get_handler().
"""

from src.thinktank.handlers.base import JobHandler
from src.thinktank.handlers.discover_guests_podcastindex import handle_discover_guests_podcastindex
from src.thinktank.handlers.discover_thinker import handle_discover_thinker
from src.thinktank.handlers.fetch_podcast_feed import handle_fetch_podcast_feed
from src.thinktank.handlers.fetch_youtube_channel import handle_fetch_youtube_channel
from src.thinktank.handlers.llm_approval_check import handle_llm_approval_check
from src.thinktank.handlers.process_content import handle_process_content
from src.thinktank.handlers.refresh_due_sources import handle_refresh_due_sources
from src.thinktank.handlers.rescan_cataloged_for_thinker import handle_rescan_cataloged_for_thinker
from src.thinktank.handlers.rollup_api_usage import handle_rollup_api_usage
from src.thinktank.handlers.scan_episodes_for_thinkers import handle_scan_episodes_for_thinkers
from src.thinktank.handlers.scan_for_candidates import handle_scan_for_candidates
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

# --- Phase 4 handler registrations ---
register_handler("process_content", handle_process_content)

# --- Phase 5 handler registrations ---
register_handler("llm_approval_check", handle_llm_approval_check)

# --- Phase 6 handler registrations ---
register_handler("scan_for_candidates", handle_scan_for_candidates)
register_handler("discover_guests_podcastindex", handle_discover_guests_podcastindex)
register_handler("discover_thinker", handle_discover_thinker)

# --- Phase 7 handler registrations ---
register_handler("rollup_api_usage", handle_rollup_api_usage)

# --- Phase 13 handler registrations ---
register_handler("scan_episodes_for_thinkers", handle_scan_episodes_for_thinkers)
register_handler("fetch_youtube_channel", handle_fetch_youtube_channel)
register_handler("rescan_cataloged_for_thinker", handle_rescan_cataloged_for_thinker)
