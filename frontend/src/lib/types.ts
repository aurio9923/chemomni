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
