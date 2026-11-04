/**
 * Thin layer over the single shared Ketcher instance (PLAN §9, decision Q20).
 * Nothing else in the app talks to Ketcher directly.
 *
 * All operations are serialized: Ketcher 3.18 can leave a read (getSmiles/getKet) pending forever
 * when it is issued while a document is still being loaded, so reads wait for loads to finish and
 * are retried with a timeout as a second line of defence.
 */
import type { Ketcher } from 'ketcher-core'

type ChangeListener = () => void
type SelectionListener = (selection: { atoms: number[]; bonds: number[] }) => void

const READ_TIMEOUT_MS = 1500
const READ_ATTEMPTS = 3

let ketcher: Ketcher | null = null
let loading = false
let queue: Promise<unknown> = Promise.resolve()
const changeListeners = new Set<ChangeListener>()
const selectionListeners = new Set<SelectionListener>()
const loadListeners = new Set<ChangeListener>()
const readyWaiters: Array<(k: Ketcher) => void> = []

class ReadTimeout extends Error {}

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
  Object.assign(window, {
    chemomniKetcher: instance,
    chemomniBridge: { getSmiles: async () => (await getComponentSmiles()).join('.') },
  })
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

/** Fires after every programmatic load (tab switch, AI replace, initial restore). */
export function onLoad(fn: ChangeListener): () => void {
  loadListeners.add(fn)
  return () => loadListeners.delete(fn)
}

export function onSelection(fn: SelectionListener): () => void {
  selectionListeners.add(fn)
  return () => selectionListeners.delete(fn)
}

/** Run `op` after every previously queued operation has settled. */
function enqueue<T>(op: (k: Ketcher) => Promise<T>): Promise<T> {
  const run = queue.then(async () => op(await whenReady()))
  queue = run.catch(() => undefined)
  return run
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new ReadTimeout()), ms)
    p.then(
      (v) => {
        clearTimeout(timer)
        resolve(v)
      },
      (e) => {
        clearTimeout(timer)
        reject(e)
      },
    )
  })
}

/** A serialized read that is retried if Ketcher never answers. */
function read<T>(op: (k: Ketcher) => Promise<T>): Promise<T> {
  return enqueue(async (k) => {
    for (let attempt = 1; ; attempt++) {
      try {
        return await withTimeout(op(k), READ_TIMEOUT_MS)
      } catch (e) {
        if (!(e instanceof ReadTimeout)) throw e
        console.warn(`[bridge] Ketcher read timed out (attempt ${attempt}/${READ_ATTEMPTS})`)
        if (attempt >= READ_ATTEMPTS) throw e
      }
    }
  })
}

/** Replace the whole document. `clearHistory` is true for tab switches, false for AI replaces. */
export function load(content: string, { clearHistory }: { clearHistory: boolean }): Promise<void> {
  return enqueue(async (k) => {
    loading = true
    try {
      if (content.trim()) {
        await k.setMolecule(content)
      } else {
        k.editor.clear()
      }
      if (clearHistory) k.editor.clearHistory()
    } finally {
      // Ketcher emits `change` asynchronously after setMolecule; release after the next frame.
      await new Promise((r) => requestAnimationFrame(() => r(null)))
      loading = false
    }
  }).then(() => loadListeners.forEach((fn) => fn()))
}

export function getKet(): Promise<string> {
  return read((k) => k.getKet())
}

/** Dot-separated components as individual SMILES; empty canvas or reactions give []. */
export async function getComponentSmiles(): Promise<string[]> {
  try {
    const smiles = (await read((k) => k.getSmiles())).trim()
    return smiles ? smiles.split('.') : []
  } catch {
    return []
  }
}

/** V3000 molfile of the whole canvas, or null when empty or not representable (e.g. reactions). */
export async function getMolfileV3000(): Promise<string | null> {
  try {
    return await read((k) => k.getMolfile('v3000'))
  } catch {
    return null
  }
}
