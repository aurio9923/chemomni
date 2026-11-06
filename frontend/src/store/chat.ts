import { create } from 'zustand'

import * as ketcher from '../ketcher/bridge'
import { api } from '../lib/api'
import { readSSE } from '../lib/sse'
import type { AppConfig, Artifact, Part, StoredMessage, Turn } from '../lib/types'
import { useToast } from './toast'
import { useWorkspace } from './workspace'

const MODEL_KEY = 'chemomni.model'

function readStoredModel(): string | null {
  try {
    return localStorage.getItem(MODEL_KEY)
  } catch {
    return null
  }
}

interface ChatState {
  config: AppConfig | null
  /** Model chosen in the picker; null means the server default. */
  model: string | null
  setModel: (model: string) => void
  loadedConversationId: string | null
  turns: Turn[]
  streaming: boolean
  load: (conversationId: string) => Promise<void>
  send: (text: string) => Promise<void>
  stop: () => void
  newChat: () => Promise<void>
}

let controller: AbortController | null = null

async function buildSnapshot() {
  // save pending edits first so the snapshot's revision matches the stored document
  await useWorkspace.getState().flush()
  const ws = useWorkspace.getState()
  const active = ws.tabs.find((t) => t.id === ws.activeTabId)
  const [read, molfile, ket] = await Promise.all([
    ketcher.getComponentSmiles(),
    ketcher.getMolfileV3000(),
    ketcher.getKet().catch(() => null), // the numbers written next to structures live in the KET
  ])
  const selection = await ketcher.selectionAsMolfileAtoms(molfile)
  // If Ketcher could not be read (see bridge.ts), fall back to the last autosaved SMILES of the tab.
  const smiles = read.length > 0 ? read : (active?.smiles_cache ?? [])
  return {
    active_tab: active
      ? { id: active.id, name: active.name, revision: active.revision, smiles, molfile, ket, selection_atoms: selection }
      : null,
    tabs: ws.tabs.map((t) => ({
      id: t.id,
      name: t.name,
      smiles: t.id === active?.id ? smiles : t.smiles_cache,
    })),
  }
}

/** Rebuild UI parts from a stored assistant message (blocks + artifacts linked by call_id). */
export function partsFromStored(msg: StoredMessage): Part[] {
  const parts: Part[] = []
  const results = new Map(
    msg.blocks.filter((b) => b.type === 'tool_result').map((b) => [b.tool_call_id!, b.content ?? '']),
  )
  for (const b of msg.blocks) {
    if (b.type === 'text' && b.text) {
      parts.push({ kind: 'text', text: b.text })
    } else if (b.type === 'tool_use' && b.id) {
      let ok = false
      try {
        ok = Boolean(JSON.parse(results.get(b.id) ?? '{}').ok)
      } catch {
        /* keep false */
      }
      parts.push({ kind: 'tool', callId: b.id, name: b.name ?? '?', status: ok ? 'ok' : 'error' })
      for (const a of msg.artifacts.filter((x) => x.payload.call_id === b.id)) {
        parts.push({ kind: 'artifact', artifact: a })
      }
    }
  }
  return parts
}

export const useChat = create<ChatState>((set, get) => {
  const updateLast = (fn: (turn: Turn) => Turn) =>
    set((s) => ({ turns: [...s.turns.slice(0, -1), fn(s.turns[s.turns.length - 1])] }))

  const appendText = (delta: string) =>
    updateLast((t) => {
      const parts = [...t.parts]
      const last = parts[parts.length - 1]
      if (last?.kind === 'text') parts[parts.length - 1] = { ...last, text: last.text + delta }
      else parts.push({ kind: 'text', text: delta })
      return { ...t, parts }
    })

  return {
    config: null,
    model: null,
    loadedConversationId: null,
    turns: [],
    streaming: false,

    load: async (conversationId) => {
      if (get().loadedConversationId === conversationId) return
      set({ loadedConversationId: conversationId })
      const [config, { messages }] = await Promise.all([api.config(), api.messages(conversationId)])
      if (get().model === null) {
        const stored = readStoredModel()
        set({ model: stored && config.models.includes(stored) ? stored : config.model })
      }
      // a newer conversation was selected, or the user already started chatting: keep local state
      if (get().loadedConversationId !== conversationId || get().turns.length > 0) {
        set({ config })
        return
      }
      set({
        config,
        turns: messages.map((m) =>
          m.role === 'user'
            ? {
                id: m.id,
                role: 'user',
                parts: [{ kind: 'text', text: m.blocks.map((b) => b.text ?? '').join('') }],
              }
            : { id: m.id, role: 'assistant', parts: partsFromStored(m), stopReason: m.stop_reason, model: m.model },
        ),
      })
    },

    send: async (text) => {
      const conversationId = useWorkspace.getState().conversationId
      if (!conversationId || get().streaming || !text.trim()) return
      set((s) => ({
        streaming: true,
        turns: [
          ...s.turns,
          { id: `local-user-${Date.now()}`, role: 'user', parts: [{ kind: 'text', text }] },
          { id: `local-assistant-${Date.now()}`, role: 'assistant', parts: [], streaming: true },
        ],
      }))
      controller = new AbortController()
      try {
        const snapshot = await buildSnapshot()
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify({ conversation_id: conversationId, content: text, snapshot, model: get().model }),
          signal: controller.signal,
        })
        if (!res.ok) throw new Error(`Chat request failed (${res.status})`)
        for await (const msg of readSSE(res)) {
          const data = JSON.parse(msg.data)
          switch (msg.event) {
            case 'message.start':
              updateLast((t) => ({ ...t, id: data.message_id, model: data.model }))
              break
            case 'text.delta':
              appendText(data.delta)
              break
            case 'tool.start':
              updateLast((t) => ({
                ...t,
                parts: [...t.parts, { kind: 'tool', callId: data.call_id, name: data.name, status: 'running' }],
              }))
              break
            case 'tool.result':
              updateLast((t) => ({
                ...t,
                parts: t.parts.map((p) =>
                  p.kind === 'tool' && p.callId === data.call_id
                    ? { ...p, status: data.ok ? 'ok' : 'error', summary: data.summary, durationMs: data.duration_ms }
                    : p,
                ),
              }))
              break
            case 'artifact':
              updateLast((t) => ({
                ...t,
                parts: [...t.parts, { kind: 'artifact', artifact: data.artifact as Artifact }],
              }))
              break
            case 'tab.create':
              await useWorkspace.getState().addTab(data.tab, data.activate)
              break
            case 'tab.replace':
              await useWorkspace.getState().applyReplace(data)
              break
            case 'tab.replace_rejected':
              useToast.getState().show('You edited the tab while the assistant was working, so the result opened in a new tab.')
              break
            case 'highlight':
              if (data.tab_id === useWorkspace.getState().activeTabId) await ketcher.selectMolfileAtoms(data.atoms)
              break
            case 'error':
              updateLast((t) => ({ ...t, error: data.message }))
              break
            case 'message.done':
              updateLast((t) => ({ ...t, streaming: false, stopReason: data.stop_reason }))
              break
          }
        }
      } catch (e) {
        if (!(e instanceof DOMException && e.name === 'AbortError')) {
          updateLast((t) => ({ ...t, error: e instanceof Error ? e.message : String(e) }))
        }
      } finally {
        controller = null
        updateLast((t) => ({ ...t, streaming: false }))
        set({ streaming: false })
      }
    },

    stop: () => controller?.abort(),

    setModel: (model) => {
      set({ model })
      try {
        localStorage.setItem(MODEL_KEY, model)
      } catch {
        /* private mode etc.: the choice just won't be remembered */
      }
    },

    newChat: async () => {
      get().stop()
      const conv = await api.newConversation()
      set({ turns: [], loadedConversationId: conv.id })
      useWorkspace.getState().setConversation(conv.id)
    },
  }
})
