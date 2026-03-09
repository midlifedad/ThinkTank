"""ThinkTank worker module - async job processing.

Provides the worker loop that polls for jobs, dispatches them
to registered handlers, and manages graceful shutdown.

Usage:
    python -m thinktank.worker

Configuration via WORKER_ environment variables (see worker.config).
"""
