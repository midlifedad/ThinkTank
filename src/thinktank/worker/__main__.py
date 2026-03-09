"""Entry point: python -m thinktank.worker

Starts the async worker loop using the default session factory
and worker settings from environment variables.
"""

import asyncio

from src.thinktank.database import async_session_factory
from src.thinktank.worker.loop import worker_loop

if __name__ == "__main__":
    asyncio.run(worker_loop(async_session_factory))
