import { ApiError, ValidationError } from './errors'

export { ValidationError }

export interface HealthResponse {
  status: string
  version: string
  ram_gb: number
  ram_profile: 'low' | 'high'
  embedding_model: string
  ollama: {
    up: boolean
    models: Record<string, boolean>
  }
  ocr: {
    engine: 'ocrmac' | 'tesseract' | 'none'
    languages: string[]
    slovak: boolean
  }
  pdf_engine: boolean
}

export type DocFormat = 'pdf' | 'docx' | 'pptx' | 'xlsx' | 'html' | 'md'

export interface DocumentOut {
  id: string
  filename: string
  format: DocFormat
  source_path: string | null
  access: string[]
  status: 'queued' | 'parsing' | 'tables' | 'context' | 'embedding' | 'graph' | 'done' | 'error'
  stage_progress: number
  pages_count: number | null
  error: string | null
  created_at: string
}

export interface TableMeta {
  id: string
  page_no: number | null
  caption: string | null
  n_rows: number
  n_cols: number
  summary: string | null
}

export interface DocumentDetail extends DocumentOut {
  pages: { page_no: number; text_len: number }[]
  tables: TableMeta[]
}

async function json<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    // session expired or logged out in another tab — App shows the login screen
    window.dispatchEvent(new Event('rag:unauthorized'))
  }
  if (!res.ok) {
    const body = await res.text()
    let detail: unknown = body
    try {
      detail = (JSON.parse(body) as { detail?: unknown }).detail ?? body
    } catch {
      // not JSON — keep the text
    }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `${res.status}`, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const fetchHealth = () => fetch('/api/health').then((r) => json<HealthResponse>(r))

export const fetchDocuments = () => fetch('/api/documents').then((r) => json<DocumentOut[]>(r))

export const fetchDocument = (id: string) =>
  fetch(`/api/documents/${id}`).then((r) => json<DocumentDetail>(r))

export interface SourceRef {
  n: number
  chunk_id: string
  doc_id: string
  filename: string
  page_from: number
  page_to: number
  type: 'text' | 'table_summary'
  table_id: string | null
  snippet: string
  boxes?: number[][]
}

export interface ChatSummary {
  id: string
  title: string
  created_at: string
}

export type DocType = 'free' | 'report' | 'offer' | 'memo' | 'summary'

export interface GeneratedDoc {
  id: string
  title: string
  doc_type: DocType
  pages: number
  filename: string
  sections: string[]
  url: string
}

export interface SentenceCheck {
  text: string
  status: 'supported' | 'weak' | 'uncited'
  score: number | null
  citations: number[]
}

export interface Verification {
  issues: string[]
  sentences: SentenceCheck[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources: SourceRef[] | null
  document: GeneratedDoc | null
  trace_id: string | null
  rating: -1 | 1 | null
  verification: Verification | null
  created_at: string
}

export interface TraceSummary {
  trace_id: string
  name: string
  question: string | null
  mode: string | null
  status: string
  started_at_ns: number
  duration_ms: number
  n_spans: number
  input_tokens: number
  output_tokens: number
  rating: -1 | 1 | null
}

export interface TraceSpan {
  span_id: string
  parent_id: string | null
  name: string
  start_ms: number
  duration_ms: number
  status: string
  attributes: Record<string, unknown>
}

export interface TraceDetail {
  trace_id: string
  duration_ms: number
  spans: TraceSpan[]
}

export const fetchTraces = () => fetch('/api/traces').then((r) => json<TraceSummary[]>(r))

export const fetchTrace = (id: string) =>
  fetch(`/api/traces/${id}`).then((r) => json<TraceDetail>(r))

export const exportMessagePdf = (messageId: string) =>
  fetch(`/api/messages/${messageId}/export`, { method: 'POST' }).then((r) => json<GeneratedDoc>(r))

export async function setFeedback(messageId: string, rating: -1 | 1 | null): Promise<void> {
  const res = await fetch(`/api/messages/${messageId}/feedback`, {
    method: rating === null ? 'DELETE' : 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: rating === null ? undefined : JSON.stringify({ rating }),
  })
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
}

export interface GraphNode {
  id: string
  type: 'document' | 'page' | 'chunk' | 'table' | 'concept' | 'quantity' | 'reference'
  label: string
  doc_id: string | null
  degree: number
}

export interface GraphLink {
  source: string
  target: string
  type: string
  weight: number
}

export interface GraphOut {
  nodes: GraphNode[]
  links: GraphLink[]
  total_nodes: number
}

export interface NodeEvidence {
  edge_type: string
  doc_id: string | null
  filename: string | null
  page_from: number | null
  snippet: string | null
}

export interface NodeDetail {
  id: string
  type: GraphNode['type']
  label: string
  props: Record<string, unknown>
  neighbors: GraphNode[]
  evidence: NodeEvidence[]
}

export interface TableData {
  id: string
  doc_id: string
  page_no: number | null
  caption: string | null
  n_rows: number
  n_cols: number
  summary: string | null
  markdown: string
  columns: string[]
  rows: (string | number | null)[][]
  truncated: boolean
}

export const fetchGraph = (params: { types?: string; doc_id?: string; limit?: number }) => {
  const qs = new URLSearchParams()
  if (params.types) qs.set('types', params.types)
  if (params.doc_id) qs.set('doc_id', params.doc_id)
  if (params.limit) qs.set('limit', String(params.limit))
  return fetch(`/api/graph?${qs}`).then((r) => json<GraphOut>(r))
}

export interface Community {
  key: string
  title: string | null
  summary: string | null
  concepts: string[]
  size: number
  documents: { doc_id: string; filename: string; chunks: number }[]
}

export const fetchCommunities = (summaries = false) =>
  fetch(`/api/graph/communities${summaries ? '?summaries=true' : ''}`).then((r) =>
    json<Community[]>(r),
  )

export const fetchNodeDetail = (nodeId: string) =>
  fetch(`/api/graph/node/${encodeURIComponent(nodeId)}`).then((r) => json<NodeDetail>(r))

export const fetchTable = (tableId: string) =>
  fetch(`/api/tables/${tableId}`).then((r) => json<TableData>(r))

export const fetchChats = () => fetch('/api/chats').then((r) => json<ChatSummary[]>(r))

export const fetchChatMessages = (chatId: string) =>
  fetch(`/api/chats/${chatId}`).then((r) => json<ChatMessage[]>(r))

export const reindexDocument = (id: string) =>
  fetch(`/api/documents/${id}/reindex`, { method: 'POST' }).then((r) => json<DocumentOut>(r))

export interface CompanySettings {
  name: string
  street: string
  city: string
  postal_code: string
  country: string
  ico: string
  dic: string
  ic_dph: string
  email: string
  phone: string
  website: string
  iban: string
  header_text: string
  footer_text: string
  show_page_numbers: boolean
}

async function jsonOrValidation<T>(res: Response): Promise<T> {
  if (res.status === 422) {
    const body = (await res.json()) as { detail: { loc: string[]; msg: string }[] }
    const fields: Record<string, string> = {}
    for (const d of body.detail) fields[String(d.loc.at(-1))] = d.msg.replace(/^Value error, /, '')
    throw new ValidationError(fields)
  }
  return json<T>(res)
}

export const fetchCompany = () => fetch('/api/company').then((r) => json<CompanySettings>(r))

export const saveCompany = (data: CompanySettings) =>
  fetch('/api/company', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }).then((r) => jsonOrValidation<CompanySettings>(r))

export async function previewCompanyPdf(data: CompanySettings): Promise<Blob> {
  const res = await fetch('/api/company/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (res.status === 422) return jsonOrValidation<Blob>(res)
  if (!res.ok) return json<Blob>(res)
  return res.blob()
}

export const DOCUMENT_ACCEPT = '.pdf,.docx,.pptx,.xlsx,.html,.htm,.md,.markdown'

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`/api/documents/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export interface ChunkOut {
  chunk_id: string
  doc_id: string
  filename: string
  format: DocFormat
  page_from: number
  page_to: number
  section_path: string
  type: string
  text: string
}

export const fetchChunk = (chunkId: string) =>
  fetch(`/api/chunks/${encodeURIComponent(chunkId)}`).then((r) => json<ChunkOut>(r))

export function uploadDocuments(files: File[]): Promise<DocumentOut[]> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  return fetch('/api/documents', { method: 'POST', body: form }).then((r) =>
    json<DocumentOut[]>(r),
  )
}

export interface AuthUser {
  id: string
  email: string
  name: string
  role: 'admin' | 'member'
  groups: string[]
}

export interface Me {
  auth_enabled: boolean
  user: AuthUser | null
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const fetchMe = () => fetch('/api/auth/me').then((r) => json<Me>(r))

export const login = (email: string, password: string) =>
  fetch('/api/auth/login', jsonBody('POST', { email, password })).then((r) => json<AuthUser>(r))

export const logout = () => fetch('/api/auth/logout', { method: 'POST' })

export const fetchUsers = () => fetch('/api/users').then((r) => json<AuthUser[]>(r))

export const createUser = (user: {
  email: string
  name: string
  password: string
  role: 'admin' | 'member'
  groups: string[]
}) => fetch('/api/users', jsonBody('POST', user)).then((r) => json<AuthUser>(r))

export const updateUser = (
  id: string,
  user: Pick<AuthUser, 'name' | 'role' | 'groups'> & { password?: string },
) =>
  fetch(`/api/users/${id}`, jsonBody('PUT', user)).then((r) => json<AuthUser>(r))

export async function deleteUser(id: string): Promise<void> {
  const res = await fetch(`/api/users/${id}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export interface ApiTokenInfo {
  id: string
  name: string
  user_id: string
  user_email: string
  created_at: string
  expires_at: string
}

export const createApiToken = (name: string) =>
  fetch('/api/auth/tokens', jsonBody('POST', { name })).then((r) =>
    json<ApiTokenInfo & { token: string }>(r),
  )

/** Own tokens; an admin may pass allUsers to see everyone's. Never includes the secret. */
export const fetchApiTokens = (allUsers = false) =>
  fetch(`/api/auth/tokens${allUsers ? '?all_users=true' : ''}`).then((r) => json<ApiTokenInfo[]>(r))

export async function revokeApiToken(id: string): Promise<void> {
  const res = await fetch(`/api/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!res.ok) await json(res)
}

export const setDocumentAccess = (id: string, groups: string[]) =>
  fetch(`/api/documents/${id}/access`, jsonBody('PUT', { groups })).then((r) =>
    json<{ groups: string[] }>(r),
  )
