"""Tests for MutationQueue — serializes API mutations (resolution changes,
charge settings) to avoid race conditions."""

import asyncio

import pytest

from custom_components.frank_energie.mutation_queue import MutationQueue

pytestmark = pytest.mark.asyncio


async def test_add_executes_and_awaits_the_mutation() -> None:
    queue = MutationQueue()
    executed: list[str] = []

    async def mutation() -> None:
        executed.append("done")

    await queue.add(mutation)

    assert executed == ["done"]


async def test_concurrent_adds_are_fully_serialized_not_interleaved() -> None:
    """The entire point of MutationQueue: a second mutation submitted while
    the first is still in flight must wait for it to complete, not run
    concurrently — even though the first yields control mid-mutation.

    Uses explicit asyncio.Events rather than sleep(0) hop-counting: exactly
    how many scheduler round-trips a bare sleep(0) needs to fully drain a
    suspended coroutine isn't something to hardcode a test against, and
    doing so was flaky in practice (mutation_a ran to completion, and
    mutation_b along with it, well before the intended checkpoint).
    """
    queue = MutationQueue()
    order: list[str] = []
    a_started = asyncio.Event()
    a_may_finish = asyncio.Event()

    async def mutation_a() -> None:
        order.append("a-start")
        a_started.set()
        await a_may_finish.wait()
        order.append("a-end")

    async def mutation_b() -> None:
        order.append("b-start")
        order.append("b-end")

    task_a = asyncio.create_task(queue.add(mutation_a))
    await a_started.wait()  # deterministic: mutation_a has genuinely started

    task_b = asyncio.create_task(queue.add(mutation_b))
    await asyncio.sleep(0)  # give task_b a chance to attempt the lock

    # mutation_a is deliberately blocked mid-flight, and mutation_b must not
    # have started at all yet — proving the lock is actually blocking it.
    assert order == ["a-start"]

    a_may_finish.set()
    await asyncio.gather(task_a, task_b)

    assert order == ["a-start", "a-end", "b-start", "b-end"]


async def test_exception_in_mutation_propagates_to_caller() -> None:
    queue = MutationQueue()

    async def failing_mutation() -> None:
        raise ValueError("mutation boom")

    with pytest.raises(ValueError, match="mutation boom"):
        await queue.add(failing_mutation)


async def test_queue_remains_usable_after_a_failed_mutation() -> None:
    """A failed mutation must not leave the lock held or the queue broken
    for subsequent calls."""
    queue = MutationQueue()

    async def failing_mutation() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await queue.add(failing_mutation)

    executed: list[str] = []

    async def ok_mutation() -> None:
        executed.append("done")

    await queue.add(ok_mutation)

    assert executed == ["done"]
