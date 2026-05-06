"""Tests for ``queue_utils.drain_queue``."""

from __future__ import annotations

from queue import Queue

from queue_utils import drain_queue


def test_drain_queue_empty_no_callbacks() -> None:
    q: Queue[int] = Queue()
    seen: list[int] = []

    def h(x: int) -> None:
        seen.append(x)

    drain_queue(q, h)
    assert seen == []


def test_drain_queue_fifo_order() -> None:
    q: Queue[str] = Queue()
    for s in ("a", "b", "c"):
        q.put(s)
    seen: list[str] = []
    drain_queue(q, seen.append)
    assert seen == ["a", "b", "c"]
    assert q.empty()


def test_drain_queue_drains_all_pending() -> None:
    q: Queue[int] = Queue()
    q.put(1)
    q.put(2)
    total = 0

    def acc(n: int) -> None:
        nonlocal total
        total += n

    drain_queue(q, acc)
    assert total == 3
