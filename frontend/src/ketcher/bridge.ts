/**
 * Thin layer over the single shared Ketcher instance (PLAN §9, decision Q20).
 * Nothing else in the app talks to Ketcher directly.
 */
import type { Ketcher } from 'ketcher-core'

type ChangeListener = () => void
type SelectionListener = (selection: { atoms: number[]; bonds: number[] }) => void

let ketcher: Ketcher | null = null
let loading = false
const changeListeners = new Set<ChangeListener>()
const selectionListeners = new Set<SelectionListener>()
const readyWaiters: Array<(k: Ketcher) => void> = []

export function attach(instance: Ketcher): void {
  ketcher = instance
  instance.editor.subscribe('change', () => {
    if (loading) return // programmatic loads must not trigger autosave
    changeListeners.forEach((fn) => fn())
  })
  instance.editor.subscribe('selectionChange', () => {
    const sel = instance.editor.selection()
    const payload = { atoms: sel?.atoms ?? [], bonds: sel?.bonds ?? [] }
    selectionListeners.forEach((fn) => fn(payload))
  })
  readyWaiters.splice(0).forEach((resolve) => resolve(instance))
  // Hooks for E2E tests and load-time measurement.
  document.body.dataset.ketcherReady = 'true'
  ;(window as unknown as { chemomniKetcher: Ketcher }).chemomniKetcher = instance
}

export function isReady(): boolean {
  return ketcher !== null
}

export function whenReady(): Promise<Ketcher> {
  return ketcher ? Promise.resolve(ketcher) : new Promise((resolve) => readyWaiters.push(resolve))
}

export function onChange(fn: ChangeListener): () => void {
  changeListeners.add(fn)
  return () => changeListeners.delete(fn)
}

export function onSelection(fn: SelectionListener): () => void {
  selectionListeners.add(fn)
  return () => selectionListeners.delete(fn)
}

/** Replace the whole document. `clearHistory` is true for tab switches, false for AI replaces. */
export async function load(content: string, { clearHistory }: { clearHistory: boolean }): Promise<void> {
  const k = await whenReady()
  loading = true
  try {
    if (content.trim()) {
      await k.setMolecule(content)
    } else {
      k.editor.clear()
    }
    if (clearHistory) k.editor.clearHistory()
  } finally {
    // Ketcher emits `change` asynchronously after setMolecule; release on the next frame.
    await new Promise((r) => requestAnimationFrame(() => r(null)))
    loading = false
  }
}

export async function getKet(): Promise<string> {
  return (await whenReady()).getKet()
}

/** Dot-separated components as individual SMILES; empty canvas or reactions give []. */
export async function getComponentSmiles(): Promise<string[]> {
  const k = await whenReady()
  try {
    const smiles = (await k.getSmiles()).trim()
    return smiles ? smiles.split('.') : []
  } catch {
    return []
  }
}
