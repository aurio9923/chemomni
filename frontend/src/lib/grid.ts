import type { GridItem } from './types'

export type GridSortKey =
  | 'index'
  | 'similarity'
  | 'mw'
  | 'clogp'
  | 'tpsa'
  | 'hbd'
  | 'hba'
  | 'rotatable_bonds'
  | 'qed'
  | 'sa_score'
  | 'lipinski_violations'
  | 'alerts'

export interface GridSort {
  key: GridSortKey
  dir: 'asc' | 'desc'
}

export const alertCount = (item: GridItem): number => item.pains_alerts.length + item.brenk_alerts.length

function value(item: GridItem, key: GridSortKey): number | null {
  if (key === 'alerts') return alertCount(item)
  const v = item[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** Sorted copy; missing values (e.g. no similarity without a parent) go last in both directions. */
export function sortGrid(items: GridItem[], sort: GridSort): GridItem[] {
  const sign = sort.dir === 'asc' ? 1 : -1
  return [...items].sort((a, b) => {
    const va = value(a, sort.key)
    const vb = value(b, sort.key)
    if (va === null && vb === null) return a.index - b.index
    if (va === null) return 1
    if (vb === null) return -1
    return va === vb ? a.index - b.index : (va - vb) * sign
  })
}

/** Clicking a column: a new column sorts ascending, the same column flips direction. */
export function nextSort(current: GridSort, key: GridSortKey): GridSort {
  if (current.key !== key) return { key, dir: 'asc' }
  return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
}
