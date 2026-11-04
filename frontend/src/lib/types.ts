export type ContentFormat = 'ket' | 'mol'

export interface Tab {
  id: string
  name: string
  kind: string
  source: 'user' | 'ai'
  origin_message_id: string | null
  content_format: ContentFormat
  content: string
  smiles_cache: string[]
  revision: number
  position: number
  is_deleted: boolean
  created_at: string
  updated_at: string
}

export interface Workspace {
  id: string
  name: string
  conversation_id: string
  tabs: Tab[]
}

export interface LightProperties {
  ok: true
  input: string
  smiles: string
  formula: string
  mw: number
  clogp: number
  tpsa: number
  hbd: number
  hba: number
  rotatable_bonds: number
  lipinski_violations: number
}

export interface PropertyError {
  ok: false
  input: string
  error: string
}

export type LightRow = LightProperties | PropertyError

// ---- chat ---------------------------------------------------------------

export interface StructureItem {
  smiles: string
  label: string | null
  svg: string | null
  molfile: string | null
}

export interface PropertyRow extends StructureItem {
  ok: true
  formula: string
  mw: number
  exact_mass: number
  clogp: number
  tpsa: number
  hbd: number
  hba: number
  rotatable_bonds: number
  fsp3: number
  stereocenters_defined: number
  stereocenters_undefined: number
  lipinski_violations: number
  veber_pass: boolean
  qed: number
  sa_score: number
  pains_alerts: string[]
  brenk_alerts: string[]
  inchikey: string | null
}

export type Artifact =
  | { id: string; type: 'table'; payload: { title: string; call_id: string; rows: PropertyRow[] } }
  | { id: string; type: 'structure_set'; payload: { title: string; call_id: string; items: StructureItem[] } }

export type Part =
  | { kind: 'text'; text: string }
  | {
      kind: 'tool'
      callId: string
      name: string
      status: 'running' | 'ok' | 'error'
      summary?: string
      durationMs?: number
    }
  | { kind: 'artifact'; artifact: Artifact }

export interface Turn {
  id: string
  role: 'user' | 'assistant'
  parts: Part[]
  streaming?: boolean
  error?: string
  stopReason?: string | null
  model?: string | null
}

export interface StoredBlock {
  type: 'text' | 'tool_use' | 'tool_result'
  text?: string
  id?: string
  name?: string
  arguments?: string
  tool_call_id?: string
  content?: string
}

export interface StoredMessage {
  id: string
  role: 'user' | 'assistant'
  blocks: StoredBlock[]
  artifacts: Artifact[]
  stop_reason: string | null
  model: string | null
}

export interface AppConfig {
  provider: string
  model: string
  models: string[]
  api_key_set: boolean
}
