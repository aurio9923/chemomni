import { Copy, Download, FileUp, Hash, History, Plus, Sparkles, Split, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { IMPORT_ACCEPT } from '../lib/files'
import { useWorkspace } from '../store/workspace'

const EXPORTS = [
  { format: 'sdf', label: 'SDF with properties' },
  { format: 'csv', label: 'CSV with properties' },
  { format: 'smi', label: 'SMILES list' },
] as const

/** Export of the active tab with properties and numbers. (Ketcher's own "Save as" writes one structure format.) */
function ExportMenu() {
  const exportTab = useWorkspace((s) => s.exportTab)
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        title="Export this tab with properties and compound numbers"
        aria-label="Export"
        aria-haspopup="menu"
        aria-expanded={open}
        className="rounded p-1 hover:bg-line hover:text-ink"
        onClick={() => setOpen((v) => !v)}
      >
        <Download className="size-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute top-full right-0 z-50 mt-1 w-48 rounded-md border border-line bg-white py-1 text-[13px] text-ink shadow-lg"
        >
          {EXPORTS.map(({ format, label }) => (
            <button
              key={format}
              type="button"
              role="menuitem"
              data-testid={`export-${format}`}
              className="block w-full px-3 py-1.5 text-left hover:bg-panel"
              onClick={() => {
                setOpen(false)
                void exportTab(format)
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function TabBar() {
  const {
    tabs,
    activeTabId,
    activate,
    newTab,
    closeTab,
    duplicateTab,
    splitTab,
    numberCompounds,
    importFile,
    renameTab,
    restoreLastClosed,
    saving,
  } = useWorkspace()
  const [editing, setEditing] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

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
              {tab.smiles_cache.length > 1 && (
                <button
                  type="button"
                  aria-label={`Split ${tab.name} into one tab per molecule`}
                  title="Split into one tab per molecule"
                  className="ml-auto hidden rounded p-0.5 text-muted group-hover:block hover:bg-line hover:text-ink"
                  onClick={(e) => {
                    e.stopPropagation()
                    void splitTab(tab.id)
                  }}
                >
                  <Split className="size-3" />
                </button>
              )}
              <button
                type="button"
                aria-label={`Duplicate ${tab.name}`}
                title="Duplicate"
                className={`${tab.smiles_cache.length > 1 ? '' : 'ml-auto '}hidden rounded p-0.5 text-muted group-hover:block hover:bg-line hover:text-ink`}
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
          title="Number compounds: write 1, 2, 3 … under each structure, so you can say &quot;compound 1&quot; in the chat"
          aria-label="Number compounds"
          className="rounded p-1 hover:bg-line hover:text-ink"
          onClick={() => void numberCompounds()}
        >
          <Hash className="size-4" />
        </button>
        <button
          type="button"
          title="Import a file into a new tab: SDF, molfile, SMILES list, CSV, KET, CDXML. Several structures open on one numbered tab."
          aria-label="Import file"
          className="rounded p-1 hover:bg-line hover:text-ink"
          onClick={() => fileInput.current?.click()}
        >
          <FileUp className="size-4" />
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept={IMPORT_ACCEPT}
          data-testid="import-input"
          onChange={(e) => {
            const files = [...(e.target.files ?? [])]
            e.target.value = '' // the same file can be imported again
            void (async () => {
              for (const file of files) await importFile(file)
            })()
          }}
        />
        <ExportMenu />
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
