import { AlertTriangle, Check, Loader2, MessageSquarePlus, SendHorizontal, Square, Wrench, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { Part, Turn } from '../lib/types'
import { useChat } from '../store/chat'
import { useWorkspace } from '../store/workspace'
import { ArtifactView } from './Artifacts'

const TOOL_LABELS: Record<string, string> = {
  compute_properties: 'Computing properties',
  validate_structures: 'Validating structures',
  similarity: 'Comparing similarity',
  substructure_search: 'Searching substructure',
}

function ToolChip({ part }: { part: Extract<Part, { kind: 'tool' }> }) {
  const label = TOOL_LABELS[part.name] ?? part.name
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-2.5 py-0.5 text-xs text-muted">
      {part.status === 'running' && <Loader2 className="size-3 animate-spin" />}
      {part.status === 'ok' && <Check className="size-3 text-emerald-600" />}
      {part.status === 'error' && <X className="size-3 text-danger" />}
      <Wrench className="size-3" />
      <span>
        {label}
        {part.status === 'running' ? '…' : ''}
      </span>
      {part.summary && <span className="text-ink">· {part.summary}</span>}
    </div>
  )
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === 'user') {
    const text = turn.parts.map((p) => (p.kind === 'text' ? p.text : '')).join('')
    return (
      <div className="flex justify-end" data-testid="user-message">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-sm whitespace-pre-wrap text-white">
          {text}
        </div>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2" data-testid="assistant-message">
      {turn.parts.map((p, i) => {
        if (p.kind === 'text')
          return (
            <div key={i} className="md text-sm leading-relaxed text-ink">
              <Markdown remarkPlugins={[remarkGfm]}>{p.text}</Markdown>
            </div>
          )
        if (p.kind === 'tool')
          return (
            <div key={i}>
              <ToolChip part={p} />
            </div>
          )
        return <ArtifactView key={i} artifact={p.artifact} />
      })}
      {turn.streaming && turn.parts.length === 0 && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="size-3 animate-spin" /> Thinking…
        </div>
      )}
      {turn.error && (
        <div role="alert" className="flex items-start gap-2 rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          {turn.error}
        </div>
      )}
      {turn.stopReason === 'max_tools' && (
        <div className="text-xs text-muted">Stopped after the tool-call limit for one turn.</div>
      )}
      {turn.model && !turn.streaming && (
        <div className="text-[11px] text-muted/80" data-testid="answered-by">
          {turn.model}
        </div>
      )}
    </div>
  )
}

function Composer() {
  const { send, stop, streaming } = useChat()
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    const value = text.trim()
    if (!value || streaming) return
    setText('')
    void send(value)
  }

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])

  return (
    <form
      className="shrink-0 border-t border-line p-3"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <div className="flex items-end gap-2 rounded-lg border border-line bg-white px-3 py-2 focus-within:border-accent">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          aria-label="Message"
          placeholder="Ask about the active tab…  (Enter to send, Shift+Enter for a new line)"
          className="max-h-[200px] min-h-6 flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            // never send while an IME (e.g. Japanese input) is composing
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
              e.preventDefault()
              submit()
            }
          }}
        />
        {streaming ? (
          <button type="button" onClick={stop} className="rounded p-1.5 text-ink hover:bg-panel" aria-label="Stop">
            <Square className="size-4 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!text.trim()}
            className="rounded p-1.5 text-accent hover:bg-accent/10 disabled:text-muted disabled:hover:bg-transparent"
            aria-label="Send"
          >
            <SendHorizontal className="size-4" />
          </button>
        )}
      </div>
    </form>
  )
}

export function ChatPanel() {
  const conversationId = useWorkspace((s) => s.conversationId)
  const { turns, load, config, newChat, streaming, model, setModel } = useChat()
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (conversationId) void load(conversationId) // no-op for a conversation already loaded or just created
  }, [conversationId, load])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [turns])

  return (
    <section className="flex h-full min-h-0 flex-col bg-white" aria-label="Chat">
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-line px-3 text-[13px]">
        <span className="font-medium text-ink">Assistant</span>
        {config && (
          <select
            aria-label="Model"
            title="Model used for the next message"
            value={model ?? config.model}
            disabled={streaming}
            onChange={(e) => setModel(e.target.value)}
            className="rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-muted hover:border-line hover:text-ink focus:border-accent focus:outline-none disabled:opacity-50"
          >
            {config.models.map((m) => (
              <option key={m} value={m}>
                {m}
                {m === config.model ? ' (default)' : ''}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          disabled={streaming}
          onClick={() => void newChat()}
          className="ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-muted hover:bg-panel hover:text-ink disabled:opacity-50"
        >
          <MessageSquarePlus className="size-3.5" /> New chat
        </button>
      </header>
      {config && config.provider === 'openai' && (() => {
        const selected = model ?? config.model
        const claude = selected.startsWith('claude-')
        const missing = claude ? !config.anthropic_key_set : !config.api_key_set
        if (!missing) return null
        return (
          <div className="border-b border-line bg-amber-50 px-3 py-2 text-xs text-amber-800" role="status">
            <code>{claude ? 'ANTHROPIC_API_KEY' : 'OPENAI_API_KEY'}</code> is not set. Add it to <code>.env</code> at
            the repository root and restart the backend.
          </div>
        )
      })()}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center text-sm text-muted">
            <p>Ask about the structure on the active tab.</p>
            <p className="text-xs">e.g. “Compute the properties”, “Any structural alerts?”, “Compare with salicylic acid”</p>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {turns.map((t) => (
              <TurnView key={t.id} turn={t} />
            ))}
            <div ref={bottom} />
          </div>
        )}
      </div>
      <Composer />
    </section>
  )
}
