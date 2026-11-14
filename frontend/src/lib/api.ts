import type { AppConfig, ContentFormat, LightRow, StoredMessage, Tab, Workspace } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown
  constructor(status: number, detail: unknown) {
    super(`API ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    let detail: unknown = null
    try {
      detail = (await res.json()).detail
    } catch {
      /* no body */
    }
    throw new ApiError(res.status, detail)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

export const api = {
  workspace: () => request<Workspace>('GET', '/api/workspace'),
  getTab: (id: string) => request<Tab>('GET', `/api/tabs/${id}`),
  createTab: (body: { name?: string; content_format?: ContentFormat; content?: string; smiles?: string[] } = {}) =>
    request<Tab>('POST', '/api/tabs', body),
  updateTab: (
    id: string,
    body: { revision: number; name?: string; content_format?: ContentFormat; content?: string; smiles?: string[] },
  ) => request<Tab>('PATCH', `/api/tabs/${id}`, body),
  deleteTab: (id: string) => request<void>('DELETE', `/api/tabs/${id}`),
  restoreTab: (id: string) => request<Tab>('POST', `/api/tabs/${id}/restore`),
  duplicateTab: (id: string) => request<Tab>('POST', `/api/tabs/${id}/duplicate`),
  /** One new tab per molecule. Pass the editor's molfile for the active tab (KET is not readable server-side). */
  splitTab: (id: string, molfile: string | null) =>
    request<Tab[]>('POST', `/api/tabs/${id}/split`, molfile ? { molfile } : undefined),
  closedTabs: () => request<Tab[]>('GET', '/api/tabs/closed'),
  config: () => request<AppConfig>('GET', '/api/config'),
  newConversation: () => request<{ id: string; title: string }>('POST', '/api/conversations'),
  messages: (conversationId: string) =>
    request<{ messages: StoredMessage[] }>('GET', `/api/conversations/${conversationId}/messages`),
  lightProperties: (smiles: string[], signal?: AbortSignal) =>
    request<{ rows: LightRow[] }>('POST', '/api/chem/properties', { smiles, light: true }, signal),
}
