export interface SSEEvent {
  [key: string]: unknown;
  _seq?: number;
  _done?: boolean;
}

export async function collectSSE(url: string, maxEvents = 50, timeoutMs = 60000): Promise<SSEEvent[]> {
  const events: SSEEvent[] = [];
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal, headers: { Accept: 'text/event-stream' } });
    if (!res.body) throw new Error('No body');
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
