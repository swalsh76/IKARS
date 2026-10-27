"""Blocking work the engine must never wait on at exit (a NAS that stops answering, a GPU driver
that hangs): run in a daemon thread and awaited. Unlike ``asyncio.to_thread``, a call that never
returns does not hold up the process's exit."""

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any


async def in_daemon_thread[T](work: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    done: asyncio.Future[T] = loop.create_future()

    def settle(result: T | None, error: BaseException | None) -> None:
        if done.cancelled():
            return
        if error is not None:
            done.set_exception(error)
        else:
            done.set_result(result)  # type: ignore[arg-type]

    def run() -> None:
        try:
            result, error = work(*args, **kwargs), None
        except BaseException as exc:  # handed to the awaiting coroutine
            result, error = None, exc
        with contextlib.suppress(RuntimeError):  # the loop is gone: the process exited meanwhile
            loop.call_soon_threadsafe(settle, result, error)

    threading.Thread(target=run, name="ikars-blocking", daemon=True).start()
    return await done
