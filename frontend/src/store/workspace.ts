import { create } from 'zustand'

import * as ketcher from '../ketcher/bridge'
import { ApiError, api } from '../lib/api'
import { basename, countKetMolecules, downloadText, importRoute, stem } from '../lib/files'
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
  /** One new tab per molecule; the original tab stays. */
  splitTab: (id: string) => Promise<void>
  /** Write a number under every structure of the active tab that has none. */
  numberCompounds: () => Promise<void>
  /** A new tab from a structure file: SDF, molfile, SMILES list, CSV (read by the server), KET, CDXML, RXN (by Ketcher). */
  importFile: (file: File) => Promise<void>
  /** Download the active tab's structures with properties and compound numbers. */
  exportTab: (format: 'sdf' | 'csv' | 'smi') => Promise<void>
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

  /**
   * Write a number under every structure of the active editor document that has none; returns the labels.
   * `expected` is the number of molecules a fresh load should show: Ketcher can still report an empty
   * document for a moment after setMolecule, so wait for them instead of numbering nothing.
   */
  const numberActive = async (expected = 0): Promise<string[]> => {
    let ket = await ketcher.getKet()
    for (let attempt = 0; attempt < 5 && countKetMolecules(ket) < expected; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 200))
      ket = await ketcher.getKet()
    }
    const { ket: numbered, added } = await api.numberCompounds(ket)
    if (added.length > 0) await ketcher.setKet(numbered)
    return added
  }

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

    splitTab: async (id) => {
      const active = id === get().activeTabId
      if (active) await get().flush()
      // the active tab may hold unsaved KET; the editor's molfile is the source of truth
      const molfile = active ? await ketcher.getMolfileV3000() : null
      try {
        const created = await api.splitTab(id, molfile)
        set((s) => ({ tabs: [...s.tabs, ...created] }))
        await get().activate(created[0].id)
        useToast.getState().show(`Split into ${created.length} tabs.`)
      } catch (e) {
        if (e instanceof ApiError && e.status === 400) useToast.getState().show('This tab holds a single molecule.')
        else throw e
      }
    },

    numberCompounds: async () => {
      const added = await numberActive()
      const toast = useToast.getState()
      if (added.length === 0) {
        toast.show('Every structure already has a number (or the editor is empty).')
        return
      }
      toast.show(`Numbered ${added.length} structure${added.length > 1 ? 's' : ''}: ${added.join(', ')}.`, {
        label: 'Undo',
        run: () => void ketcher.undo(),
      })
    },

    importFile: async (file) => {
      const toast = useToast.getState()
      const name = basename(file.name)
      const route = importRoute(file.name)
      if (route === 'unsupported') {
        toast.show(`${name}: this file type is not supported. Use .sdf, .mol, .smi, .csv, .ket or .cdxml.`)
        return
      }
      try {
        const text = await file.text()
        if (route === 'ketcher') {
          const tab = await api.createTab({ name: stem(file.name) })
          set((s) => ({ tabs: [...s.tabs, tab] }))
          await get().activate(tab.id)
          try {
            await ketcher.setStructure(text)
          } catch {
            await get().closeTab(tab.id)
            toast.show(`Ketcher could not read ${name}.`)
            return
          }
          toast.show(`Opened ${name} in a new tab.`)
          return
        }
        const result = await api.importFile(file.name, text)
        set((s) => ({ tabs: [...s.tabs, result.tab] }))
        await get().activate(result.tab.id)
        // several structures arrive on a grid: number them, so "compound 3" works right away
        const added = result.imported > 1 ? await numberActive(result.imported) : []
        let message = `Imported ${result.imported} structure${result.imported === 1 ? '' : 's'} from ${name}`
        if (added.length > 0) message += `, numbered ${added[0]}–${added[added.length - 1]}`
        message += '.'
        if (result.skipped.length > 0) {
          const more = result.skipped.length > 1 ? ` (+${result.skipped.length - 1} more)` : ''
          message += ` Note: ${result.skipped[0]}${more}.`
        }
        toast.show(
          message,
          added.length > 0 ? { label: 'Remove numbers', run: () => void ketcher.undo() } : undefined,
          result.skipped.length > 0 ? 15000 : undefined,
        )
      } catch (e) {
        const detail = e instanceof ApiError && typeof e.detail === 'string' ? `: ${e.detail}` : ''
        toast.show(`Could not import ${name}${detail}`)
      }
    },

    exportTab: async (format) => {
      const toast = useToast.getState()
      const tab = get().tabs.find((t) => t.id === get().activeTabId)
      if (!tab) return
      const molfile = await ketcher.getMolfileV3000()
      if (!molfile) {
        toast.show('Nothing to export: the editor is empty or holds a reaction.')
        return
      }
      const ket = await ketcher.getKet().catch(() => null)
      try {
        const result = await api.exportStructures({ format, molfile, ket, name: tab.name })
        downloadText(result.filename, result.mime, result.content)
        let message = `Exported ${result.count} structure${result.count === 1 ? '' : 's'} to ${result.filename}.`
        if (result.skipped.length > 0) message += ` Not exported: ${result.skipped.join('; ')}.`
        toast.show(message, undefined, result.skipped.length > 0 ? 15000 : undefined)
      } catch (e) {
        toast.show(e instanceof ApiError && typeof e.detail === 'string' ? e.detail : 'Export failed.')
      }
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
