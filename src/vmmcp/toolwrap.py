"""toolwrap.py — wraps EVERY tool function before FastMCP sees it.

Why this exists (the "SSE drops / client stalls" bug):
  FastMCP runs plain `def` tools directly on the asyncio event loop. Any tool that blocks (mouse glides,
  waiting for the desktop agent, shell commands ...) freezes the loop, so the SSE keep-alive pings stop,
  the client decides the server is dead, drops the stream, and never reconnects.
So each tool is run in a worker thread, with a hard timeout, and every call is recorded for the dashboard.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
import typing
from typing import Callable

from fastmcp.exceptions import ToolError

from .activity import store
from .config import TOOL_TIMEOUT
from .logs import log


def wrap(fn: Callable) -> Callable:
    try:
        sig = inspect.signature(fn, eval_str=True)           # resolve `from __future__ import annotations`
        hints = typing.get_type_hints(fn)
    except Exception:
        sig, hints = inspect.signature(fn), dict(getattr(fn, "__annotations__", {}))

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        event_id = store.begin(fn.__name__, kwargs)
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            msg = f"{fn.__name__} did not finish within {TOOL_TIMEOUT}s and was abandoned."
            store.end(event_id, False, error=msg)
            log.error(msg)
            raise ToolError(msg) from None
        except asyncio.CancelledError:
            store.end(event_id, False, error="cancelled (client disconnected or gave up)")
            raise
        except Exception as exc:
            store.end(event_id, False, error=f"{type(exc).__name__}: {exc}")
            log.warning("%s failed after %.2fs: %s", fn.__name__, time.perf_counter() - t0, exc)
            raise
        store.end(event_id, True, result=result)
        return result

    wrapper.__signature__ = sig
    wrapper.__annotations__ = hints
    wrapper.__dict__.pop("__wrapped__", None)               # make sure nobody unwraps back to the sync fn
    return wrapper
