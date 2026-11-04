import { ArrowRight, ExternalLink } from 'lucide-react'

import type { Artifact, DiffPayload, DiffSide, PropertyRow, StructureItem } from '../lib/types'
import { useWorkspace } from '../store/workspace'

function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

function Thumb({ item, size = 'md' }: { item: StructureItem; size?: 'sm' | 'md' }) {
  const cls = size === 'sm' ? 'h-20 w-28' : 'h-28 w-40'
  return item.svg ? (
    <img src={svgUrl(item.svg)} alt={item.smiles} title={item.smiles} className={`${cls} object-contain`} />
  ) : (
    <div className={`${cls} flex items-center justify-center text-xs text-muted`}>no depiction</div>
  )
}

function OpenButton({ item, name }: { item: StructureItem; name: string }) {
  const openStructures = useWorkspace((s) => s.openStructures)
  return (
    <button
      type="button"
      title="Open in a new tab"
      aria-label={`Open ${name} in a new tab`}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-accent hover:bg-accent/10"
      onClick={() => void openStructures(name, [item])}
    >
      <ExternalLink className="size-3" />
      Open
    </button>
  )
}

const fmt = (n: number, d = 2) => (Number.isFinite(n) ? n.toFixed(d) : '–')

function PropertyTable({ title, rows }: { title: string; rows: PropertyRow[] }) {
  return (
    <figure className="overflow-hidden rounded-lg border border-line" data-testid="artifact-table">
      <figcaption className="border-b border-line bg-panel px-3 py-1.5 text-xs font-medium text-muted">
        {title}
      </figcaption>
      <div className="overflow-x-auto">
        <table className="w-full text-xs tabular-nums">
          <thead className="text-left text-muted">
            <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:font-medium [&>th]:whitespace-nowrap">
              <th>Structure</th>
              <th>Formula</th>
              <th>MW</th>
              <th>cLogP</th>
              <th>TPSA</th>
              <th>HBD</th>
              <th>HBA</th>
              <th>RotB</th>
              <th>Fsp3</th>
              <th>QED</th>
              <th>SA</th>
              <th>Ro5</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const alerts = [...r.pains_alerts.map((a) => `PAINS: ${a}`), ...r.brenk_alerts.map((a) => `Brenk: ${a}`)]
              return (
                <tr key={i} className="border-t border-line align-middle [&>td]:px-2 [&>td]:py-1">
                  <td>
                    <div className="flex flex-col items-start">
                      <Thumb item={r} size="sm" />
                      <OpenButton item={r} name={r.formula} />
                    </div>
                  </td>
                  <td className="whitespace-nowrap">{r.formula}</td>
                  <td>{fmt(r.mw)}</td>
                  <td>{fmt(r.clogp)}</td>
                  <td>{fmt(r.tpsa, 1)}</td>
                  <td>{r.hbd}</td>
                  <td>{r.hba}</td>
                  <td>{r.rotatable_bonds}</td>
                  <td>{fmt(r.fsp3)}</td>
                  <td>{fmt(r.qed)}</td>
                  <td>{fmt(r.sa_score, 1)}</td>
                  <td className={r.lipinski_violations > 1 ? 'text-danger' : ''}>
                    {r.lipinski_violations === 0 ? 'pass' : r.lipinski_violations}
                  </td>
                  <td className={alerts.length ? 'text-danger' : 'text-muted'} title={alerts.join('\n')}>
                    {alerts.length ? alerts.length : 'none'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </figure>
  )
}

function StructureSet({ title, items }: { title: string; items: StructureItem[] }) {
  return (
    <figure className="rounded-lg border border-line" data-testid="artifact-structures">
      <figcaption className="border-b border-line bg-panel px-3 py-1.5 text-xs font-medium text-muted">
        {title}
      </figcaption>
      <div className="flex flex-wrap gap-2 p-2">
        {items.map((item, i) => (
          <div key={i} className="flex flex-col items-center rounded-md border border-line p-1">
            <Thumb item={item} />
            <div className="flex w-full items-center justify-between px-1">
              <span className="text-xs text-muted">{item.label ?? ''}</span>
              <OpenButton item={item} name={item.label ?? 'Structure'} />
            </div>
          </div>
        ))}
      </div>
    </figure>
  )
}

function Delta({ label, before, after, digits = 2 }: { label: string; before: number; after: number; digits?: number }) {
  const d = after - before
  const sign = d > 0 ? '+' : ''
  return (
    <span className="whitespace-nowrap">
      {label} {after.toFixed(digits)}{' '}
      <span className={Math.abs(d) < 10 ** -digits ? 'text-muted' : 'text-ink'}>
        ({sign}
        {d.toFixed(digits)})
      </span>
    </span>
  )
}

function DiffView({ p }: { p: DiffPayload }) {
  const activate = useWorkspace((s) => s.activate)
  const tabExists = useWorkspace((s) => s.tabs.some((t) => t.id === p.tab_id))
  const where =
    p.applied === 'replaced'
      ? `Replaced on “${p.tab_name}”`
      : p.applied === 'new_tab_fallback'
        ? `Opened in “${p.tab_name}” (the active tab changed meanwhile)`
        : `Opened in “${p.tab_name}”`
  const side = (s: DiffSide, label: string) => (
    <div className="flex flex-col items-center">
      <Thumb item={{ smiles: s.smiles, svg: s.svg, label, molfile: null }} />
      <span className="text-[11px] text-muted">{label}</span>
    </div>
  )
  return (
    <figure className="rounded-lg border border-line" data-testid="artifact-diff">
      <figcaption className="flex items-center justify-between border-b border-line bg-panel px-3 py-1.5 text-xs">
        <span className="font-medium text-muted">{p.title}</span>
        {tabExists && (
          <button type="button" className="text-accent hover:underline" onClick={() => void activate(p.tab_id)}>
            {where}
          </button>
        )}
      </figcaption>
      <div className="flex items-center justify-center gap-2 p-2">
        {side(p.before, 'before')}
        <ArrowRight className="size-4 shrink-0 text-muted" />
        {side(p.after, 'after')}
      </div>
      {p.before.ok && p.after.ok && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-3 py-1.5 text-xs text-muted tabular-nums">
          <span className="text-ink">{p.after.formula}</span>
          <Delta label="MW" before={p.before.mw} after={p.after.mw} />
          <Delta label="cLogP" before={p.before.clogp} after={p.after.clogp} />
          <Delta label="TPSA" before={p.before.tpsa} after={p.after.tpsa} digits={1} />
          <Delta label="HBD" before={p.before.hbd} after={p.after.hbd} digits={0} />
          <Delta label="HBA" before={p.before.hba} after={p.after.hba} digits={0} />
        </div>
      )}
    </figure>
  )
}

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  if (artifact.type === 'diff') return <DiffView p={artifact.payload} />
  if (artifact.type === 'table') return <PropertyTable title={artifact.payload.title} rows={artifact.payload.rows} />
  if (artifact.type === 'structure_set')
    return <StructureSet title={artifact.payload.title} items={artifact.payload.items} />
  return null
}
