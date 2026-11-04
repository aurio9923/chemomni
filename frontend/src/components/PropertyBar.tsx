import { useEffect, useRef, useState } from 'react'

import * as ketcher from '../ketcher/bridge'
import { api } from '../lib/api'
import type { LightRow } from '../lib/types'

const DEBOUNCE_MS = 300

export function PropertyBar() {
  const [rows, setRows] = useState<LightRow[]>([])
  const [index, setIndex] = useState(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inflight = useRef<AbortController | null>(null)

  useEffect(() => {
    const refresh = () => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(async () => {
        const smiles = await ketcher.getComponentSmiles()
        inflight.current?.abort()
        if (smiles.length === 0) {
          setRows([])
          return
        }
        const ctrl = new AbortController()
        inflight.current = ctrl
        try {
          const res = await api.lightProperties(smiles.slice(0, 50), ctrl.signal)
          setRows(res.rows)
          setIndex((i) => Math.min(i, res.rows.length - 1))
        } catch {
          /* aborted or offline: keep last values */
        }
      }, DEBOUNCE_MS)
    }
    const offChange = ketcher.onChange(refresh)
    const offLoad = ketcher.onLoad(refresh)
    return () => {
      offChange()
      offLoad()
      if (timer.current) clearTimeout(timer.current)
    }
  }, [])

  const row = rows[index]
  return (
    <div
      className="flex h-8 shrink-0 items-center gap-4 border-t border-line bg-panel px-3 text-xs text-muted tabular-nums"
      data-testid="property-bar"
    >
      {rows.length > 1 && (
        <span className="flex items-center gap-1">
          <button type="button" className="px-1 hover:text-ink" onClick={() => setIndex((i) => Math.max(0, i - 1))}>
            ‹
          </button>
          {index + 1}/{rows.length}
          <button
            type="button"
            className="px-1 hover:text-ink"
            onClick={() => setIndex((i) => Math.min(rows.length - 1, i + 1))}
          >
            ›
          </button>
        </span>
      )}
      {!row && <span>Draw a structure to see its properties</span>}
      {row && !row.ok && <span className="text-danger">{row.error}</span>}
      {row?.ok && (
        <>
          <Stat label="Formula" value={row.formula} />
          <Stat label="MW" value={row.mw.toFixed(2)} />
          <Stat label="cLogP" value={row.clogp.toFixed(2)} />
          <Stat label="TPSA" value={row.tpsa.toFixed(1)} />
          <Stat label="HBD" value={row.hbd} />
          <Stat label="HBA" value={row.hba} />
          <Stat label="RotB" value={row.rotatable_bonds} />
          <Stat
            label="Ro5"
            value={row.lipinski_violations === 0 ? 'pass' : `${row.lipinski_violations} viol.`}
            warn={row.lipinski_violations > 1}
          />
        </>
      )}
    </div>
  )
}

function Stat({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <span className="whitespace-nowrap">
      {label} <span className={warn ? 'text-danger' : 'text-ink'}>{value}</span>
    </span>
  )
}
