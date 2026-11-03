import { SendHorizontal } from 'lucide-react'

/** Chat shell. Streaming, tools and artifacts arrive in M1. */
export function ChatPanel() {
  return (
    <section className="flex h-full min-h-0 flex-col bg-white" aria-label="Chat">
      <header className="flex h-9 shrink-0 items-center border-b border-line px-3 text-[13px] font-medium text-ink">
        Assistant
      </header>
      <div className="flex min-h-0 flex-1 items-center justify-center p-6 text-center text-sm text-muted">
        Ask about the structure on the active tab — properties, edits, analogs. (Chat arrives in the next milestone.)
      </div>
      <form className="shrink-0 border-t border-line p-3" onSubmit={(e) => e.preventDefault()}>
        <div className="flex items-end gap-2 rounded-lg border border-line bg-white px-3 py-2 focus-within:border-accent">
          <textarea
            rows={2}
            disabled
            placeholder="Message the assistant…"
            className="min-h-10 flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-muted"
          />
          <button type="submit" disabled className="rounded p-1.5 text-muted" aria-label="Send">
            <SendHorizontal className="size-4" />
          </button>
        </div>
      </form>
    </section>
  )
}
