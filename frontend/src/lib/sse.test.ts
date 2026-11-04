import { describe, expect, it } from 'vitest'

import { SSEParser } from './sse'

describe('SSEParser', () => {
  it('parses events split across chunks', () => {
    const p = new SSEParser()
    expect(p.feed('event: text.delta\r\ndata: {"delta":"he')).toEqual([])
    expect(p.feed('llo"}\r\n\r\nevent: message.done\r\ndata: {}\r\n\r\n')).toEqual([
      { event: 'text.delta', data: '{"delta":"hello"}' },
      { event: 'message.done', data: '{}' },
    ])
  })

  it('ignores comments and joins multi-line data', () => {
    const p = new SSEParser()
    expect(p.feed(': ping\n\ndata: a\ndata: b\n\n')).toEqual([{ event: 'message', data: 'a\nb' }])
  })

  it('handles CRLF split between chunks', () => {
    const p = new SSEParser()
    expect(p.feed('event: x\r')).toEqual([])
    expect(p.feed('\ndata: 1\r\n\r')).toEqual([])
    expect(p.feed('\n')).toEqual([{ event: 'x', data: '1' }])
  })

  it('handles a separator split between chunks', () => {
    const p = new SSEParser()
    expect(p.feed('event: x\ndata: 1\n')).toEqual([])
    expect(p.feed('\n')).toEqual([{ event: 'x', data: '1' }])
  })
})
