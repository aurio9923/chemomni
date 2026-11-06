import { ArrowDown, ArrowRight, ArrowUp, ExternalLink } from 'lucide-react'
import { type ReactNode, useState } from 'react'

import { type GridSort, type GridSortKey, alertCount, nextSort, sortGrid } from '../lib/grid'
import type { Artifact, DiffPayload, DiffSide, GridPayload, PropertyRow, StructureItem } from '../lib/types'
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

function TabLink({ tabId, children }: { tabId: string; children: ReactNode }) {
  const activate = useWorkspace((s) => s.activate)
  const tabExists = useWorkspace((s) => s.tabs.some((t) => t.id === tabId))
  if (!tabExists) return null
  return (
    <button type="button" className="text-accent hover:underline" onClick={() => void activate(tabId)}>
      {children}
    </button>
  )
}

const GRID_COLUMNS: { key: GridSortKey; label: string; title: string }[] = [
  { key: 'index', label: '#', title: 'Position on the grid tab' },
  { key: 'similarity', label: 'Sim', title: 'Tanimoto similarity to the parent (Morgan r=2, 2048 bits)' },
  { key: 'mw', label: 'MW', title: 'Molecular weight (g/mol)' },
  { key: 'clogp', label: 'cLogP', title: 'Crippen cLogP' },
  { key: 'tpsa', label: 'TPSA', title: 'Topological polar surface area (Å²)' },
  { key: 'hbd', label: 'HBD', title: 'H-bond donors' },
  { key: 'hba', label: 'HBA', title: 'H-bond acceptors' },
  { key: 'rotatable_bonds', label: 'RotB', title: 'Rotatable bonds' },
  { key: 'qed', label: 'QED', title: 'Quantitative estimate of drug-likeness' },
  { key: 'sa_score', label: 'SA', title: 'Synthetic accessibility (1 easy – 10 hard)' },
  { key: 'lipinski_violations', label: 'Ro5', title: 'Lipinski rule-of-five violations' },
  { key: 'alerts', label: 'Alerts', title: 'PAINS and Brenk structural alerts' },
]

function StructureGrid({ p }: { p: GridPayload }) {
  const [sort, setSort] = useState<GridSort>({ key: 'index', dir: 'asc' })
  const rows = sortGrid(p.items, sort)
  const cell = (key: GridSortKey, r: (typeof p.items)[number]) => {
    switch (key) {
      case 'index':
        return <span className="font-medium">{r.index}</span>
      case 'similarity':
        return r.similarity === null ? '–' : fmt(r.similarity)
      case 'tpsa':
        return fmt(r.tpsa, 1)
      case 'sa_score':
        return fmt(r.sa_score, 1)
      case 'mw':
      case 'clogp':
      case 'qed':
        return fmt(r[key])
      case 'lipinski_violations':
        return r.lipinski_violations === 0 ? 'pass' : r.lipinski_violations
      case 'alerts': {
        const n = alertCount(r)
        return n ? n : 'none'
      }
      default:
        return r[key]
    }
  }
  return (
    <figure className="overflow-hidden rounded-lg border border-line" data-testid="artifact-grid">
      <figcaption className="flex items-center justify-between gap-2 border-b border-line bg-panel px-3 py-1.5 text-xs">
        <span className="font-medium text-muted">
          {p.title} · {p.items.length} structure{p.items.length === 1 ? '' : 's'}
        </span>
        <TabLink tabId={p.tab_id}>Opened in “{p.tab_name}”</TabLink>
      </figcaption>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-1.5 p-2">
        {p.parent && (
          <div className="flex flex-col items-center rounded-md border border-dashed border-line p-1">
            <Thumb item={{ smiles: p.parent.smiles, svg: p.parent.svg, label: 'Parent', molfile: null }} size="sm" />
            <span className="text-[11px] text-muted">Parent</span>
          </div>
        )}
        {p.items.map((item) => (
          <div key={item.index} className="flex flex-col rounded-md border border-line p-1" data-testid="grid-card">
            <div className="self-center">
              <Thumb item={item} size="sm" />
            </div>
            <div className="flex items-center justify-between px-0.5 text-[11px] tabular-nums">
              <span className="font-medium">#{item.index}</span>
              {item.similarity !== null && (
                <span className="text-muted" title="Tanimoto similarity to the parent">
                  sim {fmt(item.similarity)}
                </span>
              )}
            </div>
            <p className="line-clamp-2 px-0.5 text-[11px] leading-snug text-muted" title={item.rationale}>
              {item.rationale}
            </p>
            <div className="mt-auto self-end">
              <OpenButton item={item} name={`${p.title} #${item.index}`} />
            </div>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto border-t border-line">
        <table className="w-full text-xs tabular-nums">
          <thead className="text-left text-muted">
            <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:font-medium [&>th]:whitespace-nowrap">
              {GRID_COLUMNS.map((c) => (
                <th
                  key={c.key}
                  title={c.title}
                  aria-sort={sort.key !== c.key ? 'none' : sort.dir === 'asc' ? 'ascending' : 'descending'}
                >
                  <button
                    type="button"
                    className="inline-flex items-center gap-0.5 hover:text-ink"
                    onClick={() => setSort((s) => nextSort(s, c.key))}
                  >
                    {c.label}
                    {sort.key === c.key &&
                      (sort.dir === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.index} className="border-t border-line [&>td]:px-2 [&>td]:py-1" title={r.rationale}>
                {GRID_COLUMNS.map((c) => (
                  <td
                    key={c.key}
                    className={
                      (c.key === 'lipinski_violations' && r.lipinski_violations > 1) ||
                      (c.key === 'alerts' && alertCount(r) > 0)
                        ? 'text-danger'
                        : c.key === 'alerts'
                          ? 'text-muted'
                          : ''
                    }
                  >
                    {cell(c.key, r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {p.rejected.length > 0 && (
        <details className="border-t border-line px-3 py-1.5 text-xs text-muted">
          <summary className="cursor-pointer">{p.rejected.length} rejected</summary>
          <ul className="mt-1 space-y-0.5">
            {p.rejected.map((r, i) => (
              <li key={i} className="break-all">
                <code className="text-ink">{r.smiles}</code> — {r.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </figure>
  )
}

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  if (artifact.type === 'grid') return <StructureGrid p={artifact.payload} />
  if (artifact.type === 'diff') return <DiffView p={artifact.payload} />
  if (artifact.type === 'table') return <PropertyTable title={artifact.payload.title} rows={artifact.payload.rows} />
  if (artifact.type === 'structure_set')
    return <StructureSet title={artifact.payload.title} items={artifact.payload.items} />
  return null
}
