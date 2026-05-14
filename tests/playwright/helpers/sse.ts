export interface SSEEvent {
  [key: string]: unknown;
  _seq?: number;
  _done?: boolean;
}

interface FetchSSEOptions {
  method?: string;
  body?: unknown;
  maxEvents?: number;
  timeoutMs?: number;
}

async function fetchSSE(url: string, opts: FetchSSEOptions = {}): Promise<SSEEvent[]> {
  const { method = 'GET', body, maxEvents = 50, timeoutMs = 60000 } = opts;
  const events: SSEEvent[] = [];
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const fetchOpts: RequestInit = {
      method,
      signal: controller.signal,
      headers: {
        Accept: 'text/event-stream',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    };
    const res = await fetch(url, fetchOpts);
    if (!res.body) throw new Error('No body in SSE response');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (events.length < maxEvents) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const ev = JSON.parse(line.slice(6)) as SSEEvent;
            events.push(ev);
            if (ev._done) return events;
          } catch {}
        }
      }
    }
  } finally {
    clearTimeout(timeout);
  }
  return events;
}

export async function collectSSE(url: string, maxEvents = 50, timeoutMs = 60000): Promise<SSEEvent[]> {
  return fetchSSE(url, { maxEvents, timeoutMs });
}

export async function collectSSEPost(url: string, body: unknown, maxEvents = 50, timeoutMs = 60000): Promise<SSEEvent[]> {
  return fetchSSE(url, { method: 'POST', body, maxEvents, timeoutMs });
}
