import asyncio
import json
from typing import AsyncGenerator, AsyncIterator, Optional

from .errors import error_envelope

# How long the upstream may stay silent before we emit an SSE comment line so
# intermediaries (reverse proxies, browsers) don't drop the idle connection.
KEEPALIVE_INTERVAL = 15.0


async def proxy_sse(
    upstream_gen: AsyncIterator[str],
    keepalive_interval: float = KEEPALIVE_INTERVAL,
    request_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Takes an async generator of raw JSON strings (already stripped of 'data: '
    prefix) and yields properly formatted SSE chunks.

    - While the upstream is idle for longer than `keepalive_interval` seconds,
      a `: keepalive` comment line is emitted to hold the connection open.
    - If the upstream fails mid-stream, an `event: error` message carrying the
      canonical error envelope is emitted instead of silently ending the
      stream (the `_done` sentinel is only sent on clean completion).
    """
    seq = 0
    iterator = upstream_gen.__aiter__()
    pending: Optional[asyncio.Task] = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=keepalive_interval)
            if not done:
                # Upstream still working — don't cancel it, just reassure the
                # client. Comment lines are ignored by SSE parsers.
                yield ": keepalive\n\n"
                continue
            task, pending = pending, None
            try:
                raw = task.result()
            except StopAsyncIteration:
                break
            except Exception as exc:
                yield "event: error\ndata: " + json.dumps(error_envelope(
                    "upstream_error",
                    f"Upstream stream failed: {type(exc).__name__}: {exc}",
                    hint="The backing service may have stopped mid-task. Check that it is running, then retry.",
                    request_id=request_id,
                )) + "\n\n"
                return
            try:
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw}
            if not isinstance(data, dict):
                data = {"raw": data}
            data["_seq"] = seq
            seq += 1
            yield f"data: {json.dumps(data)}\n\n"
        yield 'data: {"_done": true}\n\n'
    finally:
        if pending is not None:
            pending.cancel()
