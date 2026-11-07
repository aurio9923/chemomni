/** File helpers for import and export (kept free of React and Ketcher so they are easy to test). */

/** Read by the server (molfile, SDF, SMILES list, CSV), by Ketcher itself (KET, CDXML, RXN) or not at all. */
export type ImportRoute = 'server' | 'ketcher' | 'unsupported'

const SERVER_EXTENSIONS = new Set(['sdf', 'sd', 'mol', 'smi', 'smiles', 'txt', 'csv', 'tsv'])
const KETCHER_EXTENSIONS = new Set(['ket', 'cdxml', 'rxn'])

export const IMPORT_ACCEPT = [...SERVER_EXTENSIONS, ...KETCHER_EXTENSIONS].map((e) => `.${e}`).join(',')

/** The file name without folders (a Windows path counts too). */
export function basename(filename: string): string {
  return filename.split(/[\\/]/).pop() ?? filename
}

export function extension(filename: string): string {
  const name = basename(filename)
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** The tab name for an imported file. */
export function stem(filename: string): string {
  const name = basename(filename)
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(0, dot) : name
}

/**
 * Ketcher reads one structure per file well but stacks the records of an SDF on top of each other
 * and its dialog hangs on a multi-line SMILES list, so those go through the server.
 */
export function importRoute(filename: string): ImportRoute {
  const ext = extension(filename)
  if (SERVER_EXTENSIONS.has(ext)) return 'server'
  if (KETCHER_EXTENSIONS.has(ext)) return 'ketcher'
  return 'unsupported'
}

/** Number of molecules in a KET document (0 for anything that is not KET). */
export function countKetMolecules(ket: string): number {
  try {
    const doc = JSON.parse(ket) as Record<string, unknown>
    return Object.keys(doc).filter((key) => /^mol\d+$/.test(key)).length
  } catch {
    return 0
  }
}

export function downloadText(filename: string, mime: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
