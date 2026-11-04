/** Minimal SSE parser for fetch() streams (EventSource cannot POST). */

export interface SSEMessage {
  event: string
  data: string
}

/** Incremental parser: feed text chunks, get complete messages back. */
export class SSEParser {
  private buffer = ''
  private event = 'message'
  private data: string[] = []

  feed(chunk: string): SSEMessage[] {
    this.buffer += chunk
    const out: SSEMessage[] = []
    let idx: number
    while ((idx = this.buffer.search(/\r\n|\r|\n/)) !== -1) {
      // a trailing \r may be the first half of \r\n: wait for the next chunk
      if (this.buffer[idx] === '\r' && idx === this.buffer.length - 1) break
      const line = this.buffer.slice(0, idx)
      const sepLen = this.buffer.startsWith('\r\n', idx) ? 2 : 1
      this.buffer = this.buffer.slice(idx + sepLen)
      if (line === '') {
        if (this.data.length) out.push({ event: this.event, data: this.data.join('\n') })
        this.event = 'message'
        this.data = []
      } else if (line.startsWith(':')) {
        // comment / keepalive ping
      } else {
        const colon = line.indexOf(':')
        const field = colon === -1 ? line : line.slice(0, colon)
        let value = colon === -1 ? '' : line.slice(colon + 1)
        if (value.startsWith(' ')) value = value.slice(1)
        if (field === 'event') this.event = value
        else if (field === 'data') this.data.push(value)
      }
    }
    return out
  }
}

export async function* readSSE(response: Response): AsyncGenerator<SSEMessage> {
  if (!response.body) return
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  const parser = new SSEParser()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    yield* parser.feed(value)
  }
}
