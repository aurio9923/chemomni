import { create } from 'zustand'

import * as ketcher from '../ketcher/bridge'
import { ApiError, api } from '../lib/api'
import type { Tab } from '../lib/types'
import { useToast } from './toast'

const AUTOSAVE_MS = 500

interface WorkspaceState {
  ready: boolean
  error: string | null
  conversationId: string | null
  tabs: Tab[]
  activeTabId: string | null
  switching: boolean
  saving: boolean
  selection: { atoms: number[]; bonds: number[] }

  init: () => Promise<void>
  activate: (id: string) => Promise<void>
  newTab: () => Promise<void>
  closeTab: (id: string) => Promise<void>
  restoreLastClosed: () => Promise<void>
  duplicateTab: (id: string) => Promise<void>
  renameTab: (id: string, name: string) => Promise<void>
  openStructures: (name: string, items: { molfile: string | null; smiles: string }[]) => Promise<void>
  setConversation: (id: string) => void
  /** A tab created by the AI (SSE `tab.create`). */
  addTab: (tab: Tab, activate: boolean) => Promise<void>
  /** The AI replaced a tab's document (SSE `tab.replace`). */
  applyReplace: (r: { tab_id: string; content: string; content_format: 'ket' | 'mol'; revision: number; smiles: string[] }) => Promise<void>
  markDirty: () => void
  flush: () => Promise<void>
}

let saveTimer: ReturnType<typeof setTimeout> | null = null
let initialized = false
let saveChain: Promise<void> = Promise.resolve()

export const useWorkspace = create<WorkspaceState>((set, get) => {
  const patchTab = (tab: Tab) => set((s) => ({ tabs: s.tabs.map((t) => (t.id === tab.id ? tab : t)) }))

  /** Save the editor's current document into the active tab (serialized per app). */
  const saveActive = (): Promise<void> => {
    const id = get().activeTabId
    if (!id) return saveChain
    saveChain = saveChain.then(async () => {
      const [content, smiles] = await Promise.all([ketcher.getKet(), ketcher.getComponentSmiles()])
      const tab = get().tabs.find((t) => t.id === id)
      if (!tab || tab.content === content) return
      // optimistic: keep the local copy current so switching back never waits for the network
      patchTab({ ...tab, content, content_format: 'ket', smiles_cache: smiles })
      set({ saving: true })
      try {
        const saved = await api.updateTab(id, { revision: tab.revision, content, content_format: 'ket', smiles })
        patchTab(saved)
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          // M0: last writer wins from the editor. M2 refines this for AI replaces.
          const fresh = await api.getTab(id)
          patchTab(
            await api.updateTab(id, { revision: fresh.revision, content, content_format: 'ket', smiles }),
          )
        } else {
          set({ error: 'Autosave failed' })
        }
      } finally {
        set({ saving: false })
      }
    })
    return saveChain
  }

  return {
    ready: false,
    error: null,
    conversationId: null,
    tabs: [],
    activeTabId: null,
    switching: false,
    saving: false,
    selection: { atoms: [], bonds: [] },

    init: async () => {
      if (initialized) return
      initialized = true
      const ws = await api.workspace()
      const active = ws.tabs[ws.tabs.length - 1] ?? null
      set({ ready: true, conversationId: ws.conversation_id, tabs: ws.tabs, activeTabId: active?.id ?? null })
      ketcher.onChange(() => get().markDirty())
      ketcher.onSelection((selection) => set({ selection }))
      if (active) await ketcher.load(active.content, { clearHistory: true })
    },

    activate: async (id) => {
      const { activeTabId, tabs } = get()
      if (id === activeTabId) return
      set({ switching: true })
      await get().flush()
      set({ activeTabId: id, selection: { atoms: [], bonds: [] } })
      const next = tabs.find((t) => t.id === id) ?? get().tabs.find((t) => t.id === id)
      await ketcher.load(next?.content ?? '', { clearHistory: true })
      set({ switching: false })
    },

    newTab: async () => {
      const tab = await api.createTab()
      set((s) => ({ tabs: [...s.tabs, tab] }))
      await get().activate(tab.id)
    },

    closeTab: async (id) => {
      const { tabs, activeTabId } = get()
      if (id === activeTabId) await get().flush()
      const idx = tabs.findIndex((t) => t.id === id)
      const remaining = tabs.filter((t) => t.id !== id)
      set({ tabs: remaining })
      void api.deleteTab(id)
      if (remaining.length === 0) {
        await get().newTab()
      } else if (id === activeTabId) {
        const next = remaining[Math.min(idx, remaining.length - 1)]
        set({ activeTabId: null })
        await get().activate(next.id)
      }
    },

    restoreLastClosed: async () => {
      const [last] = await api.closedTabs()
      if (!last) return
      const tab = await api.restoreTab(last.id)
      set((s) => ({ tabs: [...s.tabs, tab] }))
      await get().activate(tab.id)
    },

    duplicateTab: async (id) => {
      if (id === get().activeTabId) await get().flush()
      const tab = await api.duplicateTab(id)
      set((s) => ({ tabs: [...s.tabs, tab] }))
      await get().activate(tab.id)
    },

    renameTab: async (id, name) => {
      const tab = get().tabs.find((t) => t.id === id)
      if (!tab || !name.trim() || name === tab.name) return
      patchTab({ ...tab, name })
      await saveChain
      const current = get().tabs.find((t) => t.id === id) ?? tab
      patchTab(await api.updateTab(id, { revision: current.revision, name }))
    },

    openStructures: async (name, items) => {
      // M1: one structure per call from chat cards. M3 adds server-side grid layout for many.
      const [first] = items
      if (!first) return
      const tab = await api.createTab({
        name,
        content_format: 'mol',
        content: first.molfile ?? first.smiles,
        smiles: [first.smiles],
      })
      set((s) => ({ tabs: [...s.tabs, tab] }))
      await get().activate(tab.id)
    },

    setConversation: (id) => set({ conversationId: id }),

    addTab: async (tab, activate) => {
      if (get().tabs.some((t) => t.id === tab.id)) return
      set((s) => ({ tabs: [...s.tabs, tab] }))
      if (activate) await get().activate(tab.id)
    },

    applyReplace: async (r) => {
      const tab = get().tabs.find((t) => t.id === r.tab_id)
      if (!tab) return
      const updated: Tab = {
        ...tab,
        content: r.content,
        content_format: r.content_format,
        revision: r.revision,
        smiles_cache: r.smiles,
      }
      if (r.tab_id !== get().activeTabId) {
        patchTab(updated)
        useToast.getState().show(`“${tab.name}” was updated by the assistant.`)
        return
      }
      // drop any pending autosave of the old document, then load without clearing history so Undo works
      if (saveTimer) {
        clearTimeout(saveTimer)
        saveTimer = null
      }
      await saveChain
      patchTab(updated)
      await ketcher.load(r.content, { clearHistory: false })
      useToast.getState().show(`Replaced “${tab.name}”.`, {
        label: 'Undo',
        run: () => {
          void ketcher.undo()
        },
      })
    },

    markDirty: () => {
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => {
        saveTimer = null
        void saveActive()
      }, AUTOSAVE_MS)
    },

    flush: async () => {
      if (saveTimer) {
        clearTimeout(saveTimer)
        saveTimer = null
        await saveActive()
      }
      await saveChain
    },
  }
})
