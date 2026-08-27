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
}

export interface DocumentOut {
  id: string
  filename: string
  status: 'queued' | 'parsing' | 'tables' | 'embedding' | 'graph' | 'done' | 'error'
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
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status}: ${body}`)
  }
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
}

export interface ChatSummary {
  id: string
  title: string
  created_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources: SourceRef[] | null
  created_at: string
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

export const fetchNodeDetail = (nodeId: string) =>
  fetch(`/api/graph/node/${encodeURIComponent(nodeId)}`).then((r) => json<NodeDetail>(r))

export const fetchTable = (tableId: string) =>
  fetch(`/api/tables/${tableId}`).then((r) => json<TableData>(r))

export const fetchChats = () => fetch('/api/chats').then((r) => json<ChatSummary[]>(r))

export const fetchChatMessages = (chatId: string) =>
  fetch(`/api/chats/${chatId}`).then((r) => json<ChatMessage[]>(r))

export function uploadDocuments(files: File[]): Promise<DocumentOut[]> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  return fetch('/api/documents', { method: 'POST', body: form }).then((r) =>
    json<DocumentOut[]>(r),
  )
}
