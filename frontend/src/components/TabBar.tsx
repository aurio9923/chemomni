import { Copy, History, Plus, Sparkles, X } from 'lucide-react'
import { useState } from 'react'

import { useWorkspace } from '../store/workspace'

export function TabBar() {
  const { tabs, activeTabId, activate, newTab, closeTab, duplicateTab, renameTab, restoreLastClosed, saving } =
    useWorkspace()
  const [editing, setEditing] = useState<string | null>(null)

  return (
    <div className="flex h-9 shrink-0 items-end gap-0.5 border-b border-line bg-panel px-1.5" role="tablist">
      <div className="flex min-w-0 flex-1 items-end gap-0.5 overflow-x-auto">
        {tabs.map((tab) => {
          const active = tab.id === activeTabId
          return (
            <div
              key={tab.id}
              role="tab"
              aria-selected={active}
              data-testid="tab"
              onClick={() => void activate(tab.id)}
              onDoubleClick={() => setEditing(tab.id)}
              onAuxClick={(e) => e.button === 1 && void closeTab(tab.id)}
              className={`group flex h-8 max-w-48 min-w-24 shrink-0 cursor-default items-center gap-1.5 rounded-t-md border border-b-0 px-2.5 text-[13px] select-none ${
                active
                  ? 'border-line bg-white text-ink'
                  : 'border-transparent text-muted hover:bg-white/60 hover:text-ink'
              }`}
            >
              {tab.source === 'ai' && <Sparkles className="size-3.5 shrink-0 text-accent" aria-label="AI" />}
              {editing === tab.id ? (
                <input
                  autoFocus
                  defaultValue={tab.name}
                  aria-label="Tab name"
                  className="w-full min-w-0 bg-transparent outline-none"
                  onClick={(e) => e.stopPropagation()}
                  onBlur={(e) => {
                    setEditing(null)
                    void renameTab(tab.id, e.currentTarget.value)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') e.currentTarget.blur()
                    if (e.key === 'Escape') setEditing(null)
                  }}
                />
              ) : (
                <span className="truncate" title={tab.name}>
                  {tab.name}
                </span>
              )}
              <button
                type="button"
                aria-label={`Duplicate ${tab.name}`}
                title="Duplicate"
                className="ml-auto hidden rounded p-0.5 text-muted group-hover:block hover:bg-line hover:text-ink"
                onClick={(e) => {
                  e.stopPropagation()
                  void duplicateTab(tab.id)
                }}
              >
                <Copy className="size-3" />
              </button>
              <button
                type="button"
                aria-label={`Close ${tab.name}`}
                title="Close"
                className={`rounded p-0.5 text-muted hover:bg-line hover:text-ink ${active ? '' : 'invisible group-hover:visible'}`}
                onClick={(e) => {
                  e.stopPropagation()
                  void closeTab(tab.id)
                }}
              >
                <X className="size-3.5" />
              </button>
            </div>
          )
        })}
        <button
          type="button"
          aria-label="New tab"
          title="New tab"
          className="mb-1 ml-1 rounded p-1 text-muted hover:bg-line hover:text-ink"
          onClick={() => void newTab()}
        >
          <Plus className="size-4" />
        </button>
      </div>
      <div className="mb-1.5 flex items-center gap-2 pl-2 text-xs text-muted">
        <span aria-live="polite" className="w-12 text-right">
          {saving ? 'Saving…' : ''}
        </span>
        <button
          type="button"
          title="Reopen closed tab"
          aria-label="Reopen closed tab"
          className="rounded p-1 hover:bg-line hover:text-ink"
          onClick={() => void restoreLastClosed()}
        >
          <History className="size-4" />
        </button>
      </div>
    </div>
  )
}
