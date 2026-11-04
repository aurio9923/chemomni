import { describe, expect, it } from 'vitest'

import { matchAtoms, parseMolfileAtoms } from './bridge'

const MOLFILE = `
  -INDIGO-

  0  0  0  0  0  0  0  0  0  0  0 V3000
M  V30 BEGIN CTAB
M  V30 COUNTS 3 2 0 0 0
M  V30 BEGIN ATOM
M  V30 1 O 5.0 -1.0 0.0 0
M  V30 2 C 1.0 -2.0 0.0 0
M  V30 3 C 2.0 -2.5 0.0 0
M  V30 END ATOM
M  V30 END CTAB
M  END
`

describe('atom numbering', () => {
  it('parses V3000 atoms', () => {
    expect(parseMolfileAtoms(MOLFILE)).toEqual([
      { el: 'O', x: 5, y: -1 },
      { el: 'C', x: 1, y: -2 },
      { el: 'C', x: 2, y: -2.5 },
    ])
  })

  it('matches by element and position with Ketcher y pointing down, independent of order', () => {
    const struct = [
      { id: 10, el: 'C', x: 1, y: 2 },
      { id: 11, el: 'C', x: 2, y: 2.5 },
      { id: 12, el: 'O', x: 5, y: 1 },
    ]
    expect([...matchAtoms(struct, parseMolfileAtoms(MOLFILE))]).toEqual([
      [10, 2],
      [11, 3],
      [12, 1],
    ])
  })

  it('falls back to pool order when positions do not match', () => {
    const struct = [
      { id: 0, el: 'C', x: 9, y: 9 },
      { id: 1, el: 'C', x: 8, y: 8 },
    ]
    expect([...matchAtoms(struct, parseMolfileAtoms(MOLFILE))]).toEqual([
      [0, 1],
      [1, 2],
    ])
  })
})
