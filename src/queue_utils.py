"""Small helpers for queue-oriented UI/event loops."""

from __future__ import annotations

from queue import Empty, Queue
from typing import Callable, TypeVar

T = TypeVar("T")


def drain_queue(q: Queue[T], handle_item: Callable[[T], None]) -> None:
    """Invoke ``handle_item`` for each pending item in FIFO order; swallow ``Empty``."""
    try:
        while True:
            handle_item(q.get_nowait())
    except Empty:
        pass
