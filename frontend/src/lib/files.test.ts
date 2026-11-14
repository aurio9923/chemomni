import { describe, expect, it } from 'vitest'

import { basename, countKetMolecules, extension, importRoute, stem } from './files'

describe('import routing', () => {
  it('sends multi-structure formats to the server and Ketcher-native ones to Ketcher', () => {
    expect(importRoute('series.SDF')).toBe('server')
    expect(importRoute('list.smi')).toBe('server')
    expect(importRoute('table.csv')).toBe('server')
    expect(importRoute('drawing.ket')).toBe('ketcher')
    expect(importRoute('scheme.cdxml')).toBe('ketcher')
    expect(importRoute('photo.png')).toBe('unsupported')
    expect(importRoute('README')).toBe('unsupported')
  })
})

describe('file names', () => {
  it('handles Windows and POSIX paths', () => {
    expect(basename('C:\\data\\series.sdf')).toBe('series.sdf')
    expect(basename('/home/me/series.sdf')).toBe('series.sdf')
    expect(stem('C:\\data\\my.series.sdf')).toBe('my.series')
    expect(extension('a/b/series.SDF')).toBe('sdf')
    expect(stem('.hidden')).toBe('.hidden')
    expect(extension('noext')).toBe('')
  })
})

describe('KET molecule count', () => {
  it('counts molN nodes only', () => {
    const ket = JSON.stringify({ root: { nodes: [{ $ref: 'mol0' }, { $ref: 'mol1' }] }, mol0: {}, mol1: {}, other: {} })
    expect(countKetMolecules(ket)).toBe(2)
    expect(countKetMolecules('not json')).toBe(0)
    expect(countKetMolecules(JSON.stringify({ root: { nodes: [] } }))).toBe(0)
  })
})
