"""Error categorization for failed jobs.

Spec reference: Section 3.10 (error_category field).
STANDARDS.md: "Categories are a closed set, defined upfront, extended deliberately."
"""

from enum import StrEnum

import anthropic
import httpx
import pydantic


class ErrorCategory(StrEnum):
    """Closed set of error categories for failed jobs.

    Every job failure is classified into one of these categories.
    Adding a new category is a deliberate code change, not a runtime decision.
    """

    # Network / External
    RSS_PARSE = "rss_parse"
    HTTP_TIMEOUT = "http_timeout"
    HTTP_ERROR = "http_error"
    RATE_LIMITED = "rate_limited"
    YOUTUBE_RATE_LIMIT = "youtube_rate_limit"
    LISTENNOTES_RATE_LIMIT = "listennotes_rate_limit"
    PODCASTINDEX_ERROR = "podcastindex_error"
    API_ERROR = "api_error"

    # Transcription
    TRANSCRIPTION_FAILED = "transcription_failed"
    AUDIO_DOWNLOAD_FAILED = "audio_download_failed"
    AUDIO_CONVERSION_FAILED = "audio_conversion_failed"

    # LLM
    LLM_API_ERROR = "llm_api_error"
    LLM_TIMEOUT = "llm_timeout"
    LLM_PARSE_ERROR = "llm_parse_error"

    # System
    WORKER_TIMEOUT = "worker_timeout"
    DATABASE_ERROR = "database_error"
    PAYLOAD_INVALID = "payload_invalid"
    HANDLER_NOT_FOUND = "handler_not_found"
    UNKNOWN = "unknown"


def categorize_error(exc: Exception) -> ErrorCategory:
    """Map an exception to the appropriate ErrorCategory.

    Uses isinstance chains to classify common exception types.
    Unrecognized exceptions map to UNKNOWN.
    """
    # Anthropic SDK exceptions (check before generic Python exceptions
    # since some anthropic exceptions inherit from generic types)
    if isinstance(exc, anthropic.RateLimitError):
        return ErrorCategory.LLM_API_ERROR
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return ErrorCategory.LLM_TIMEOUT
    if isinstance(exc, anthropic.APIStatusError):
        return ErrorCategory.LLM_API_ERROR
    if isinstance(exc, pydantic.ValidationError):
        return ErrorCategory.LLM_PARSE_ERROR

    # httpx exceptions (check before generic Python exceptions)
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return ErrorCategory.RATE_LIMITED
        return ErrorCategory.HTTP_ERROR

    # Standard Python exceptions
    if isinstance(exc, TimeoutError):
        return ErrorCategory.HTTP_TIMEOUT
    if isinstance(exc, ConnectionError):
        return ErrorCategory.HTTP_ERROR
    if isinstance(exc, (ValueError, KeyError)):
        return ErrorCategory.PAYLOAD_INVALID
    if isinstance(exc, OSError):
        return ErrorCategory.HTTP_ERROR
    return ErrorCategory.UNKNOWN
