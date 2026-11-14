import { describe, expect, it } from 'vitest'

import { nextSort, sortGrid } from './grid'
import type { GridItem } from './types'

function item(index: number, extra: Partial<GridItem> = {}): GridItem {
  return {
    ok: true,
    index,
    smiles: `C${index}`,
    label: `#${index}`,
    svg: null,
    molfile: null,
    rationale: '',
    similarity: 0.5,
    formula: '',
    mw: 100,
    exact_mass: 100,
    clogp: 1,
    tpsa: 20,
    hbd: 1,
    hba: 1,
    rotatable_bonds: 1,
    fsp3: 0,
    stereocenters_defined: 0,
    stereocenters_undefined: 0,
    lipinski_violations: 0,
    veber_pass: true,
    qed: 0.5,
    sa_score: 2,
    pains_alerts: [],
    brenk_alerts: [],
    inchikey: null,
    ...extra,
  }
}

const order = (items: GridItem[]) => items.map((i) => i.index)

describe('sortGrid', () => {
  it('sorts by a property in both directions, ties by grid position', () => {
    const items = [item(1, { mw: 300 }), item(2, { mw: 200 }), item(3, { mw: 300 }), item(4, { mw: 250 })]
    expect(order(sortGrid(items, { key: 'mw', dir: 'asc' }))).toEqual([2, 4, 1, 3])
    expect(order(sortGrid(items, { key: 'mw', dir: 'desc' }))).toEqual([1, 3, 4, 2])
  })

  it('counts PAINS and Brenk alerts together', () => {
    const items = [item(1, { pains_alerts: ['a'], brenk_alerts: ['b'] }), item(2), item(3, { brenk_alerts: ['c'] })]
    expect(order(sortGrid(items, { key: 'alerts', dir: 'asc' }))).toEqual([2, 3, 1])
  })

  it('keeps missing similarity last', () => {
    const items = [item(1, { similarity: null }), item(2, { similarity: 0.9 }), item(3, { similarity: 0.4 })]
    expect(order(sortGrid(items, { key: 'similarity', dir: 'desc' }))).toEqual([2, 3, 1])
    expect(order(sortGrid(items, { key: 'similarity', dir: 'asc' }))).toEqual([3, 2, 1])
  })

  it('does not mutate its input', () => {
    const items = [item(2), item(1)]
    sortGrid(items, { key: 'index', dir: 'asc' })
    expect(order(items)).toEqual([2, 1])
  })
})

describe('nextSort', () => {
  it('starts ascending and flips on the same column', () => {
    const s1 = nextSort({ key: 'index', dir: 'asc' }, 'qed')
    expect(s1).toEqual({ key: 'qed', dir: 'asc' })
    expect(nextSort(s1, 'qed')).toEqual({ key: 'qed', dir: 'desc' })
  })
})
