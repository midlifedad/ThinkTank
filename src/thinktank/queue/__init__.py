"""Job queue operations: claim, retry, error categorization, coordination.

Re-exports all public functions from submodules for convenient access:
    from thinktank.queue import claim_job, complete_job, fail_job
"""

from thinktank.queue.backpressure import get_effective_priority, get_queue_depth
from thinktank.queue.claim import claim_job, complete_job, fail_job
from thinktank.queue.errors import ErrorCategory, categorize_error
from thinktank.queue.kill_switch import is_workers_active
from thinktank.queue.rate_limiter import check_and_acquire_rate_limit
from thinktank.queue.reclaim import reclaim_stale_jobs
from thinktank.queue.retry import calculate_backoff, get_max_attempts, should_retry

__all__ = [
    # claim.py
    "claim_job",
    "complete_job",
    "fail_job",
    # errors.py
    "ErrorCategory",
    "categorize_error",
    # retry.py
    "calculate_backoff",
    "get_max_attempts",
    "should_retry",
    # kill_switch.py
    "is_workers_active",
    # reclaim.py
    "reclaim_stale_jobs",
    # rate_limiter.py
    "check_and_acquire_rate_limit",
    # backpressure.py
    "get_effective_priority",
    "get_queue_depth",
]
