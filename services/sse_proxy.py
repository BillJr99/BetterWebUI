import json
from typing import AsyncGenerator, AsyncIterator


async def proxy_sse(upstream_gen: AsyncIterator[str]) -> AsyncGenerator[str, None]:
    """
    Takes an async generator of raw JSON strings (already stripped of 'data: ' prefix)
    and yields properly formatted SSE chunks.
    """
    seq = 0
    async for raw in upstream_gen:
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
        data["_seq"] = seq
        seq += 1
        yield f"data: {json.dumps(data)}\n\n"
    yield 'data: {"_done": true}\n\n'
