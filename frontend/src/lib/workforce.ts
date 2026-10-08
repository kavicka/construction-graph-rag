/** API modulu Firma — pracovníci, stavby, úlohy, import úloh, dochádzka. */

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (res.status === 401) window.dispatchEvent(new Event('rag:unauthorized'))
  if (!res.ok) {
    const text = await res.text()
    let detail: unknown = text
    try {
      detail = (JSON.parse(text) as { detail?: unknown }).detail ?? text
    } catch {
      // nie JSON
    }
    let message = typeof detail === 'string' ? detail : `${res.status}`
    if (Array.isArray(detail)) {
      message = (detail as { msg: string }[]).map((d) => d.msg.replace(/^Value error, /, '')).join(', ')
    } else if (detail && typeof detail === 'object' && 'message' in detail) {
      message = String((detail as { message: unknown }).message)
    }
    throw new ApiError(res.status, message, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const body = (method: string, payload: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
})

// --- pracovníci -------------------------------------------------------------------------

export interface Worker {
  id: string
  name: string
  professions: string[]
  active: boolean
  locked: boolean
}

export const fetchWorkers = () => call<Worker[]>('/api/workers')
export const createWorker = (w: { name: string; pin: string; professions: string[] }) =>
  call<Worker>('/api/workers', body('POST', w))
export const updateWorker = (id: string, w: { name: string; professions: string[]; active: boolean }) =>
  call<Worker>(`/api/workers/${id}`, body('PUT', w))
export const resetWorkerPin = (id: string, pin: string) =>
  call<void>(`/api/workers/${id}/pin`, body('PUT', { pin }))
export const deleteWorker = (id: string) => call<void>(`/api/workers/${id}`, { method: 'DELETE' })
export const fetchProfessions = () => call<string[]>('/api/professions')
export const saveProfessions = (names: string[]) =>
  call<string[]>('/api/professions', body('PUT', { names }))

export interface WorkerPublic {
  id: string
  name: string
}
export const fetchLoginWorkers = () => call<WorkerPublic[]>('/api/auth/workers')
export const pinLogin = (worker_id: string, pin: string) =>
  call<WorkerPublic>('/api/auth/pin', body('POST', { worker_id, pin }))

// --- stavby a úlohy ---------------------------------------------------------------------

export interface Site {
  id: string
  name: string
  address: string
  active: boolean
}

export const fetchSites = () => call<Site[]>('/api/sites')
export const createSite = (s: { name: string; address: string }) =>
  call<Site>('/api/sites', body('POST', s))
export const updateSite = (id: string, s: { name: string; address: string; active: boolean }) =>
  call<Site>(`/api/sites/${id}`, body('PUT', s))
export const deleteSite = (id: string) => call<void>(`/api/sites/${id}`, { method: 'DELETE' })

export type TaskStatus = 'todo' | 'in_progress' | 'done' | 'cancelled'

export interface Task {
  id: string
  site_id: string
  site_name: string
  title: string
  description: string
  work_type: string | null
  date_from: string | null
  date_to: string | null
  depends_on: string | null
  depends_on_title: string | null
  status: TaskStatus
  progress: number
  done_on: string | null
  assignees: string[]
  source_id: string | null
  source_doc_id: string | null
  source_filename: string | null
  source_quote: string | null
  source_page: number | null
  blocked: boolean
}

export interface TaskInput {
  site_id: string
  title: string
  description: string
  work_type: string | null
  date_from: string | null
  date_to: string | null
  depends_on: string | null
  status: TaskStatus
  progress: number
  assignees: string[]
}

export interface TaskHistoryItem {
  report_id: string
  day: string
  worker_name: string
  entry: TaskEntry
}

export const fetchTasks = (params: {
  site_id?: string
  date_from?: string
  date_to?: string
  include_done?: boolean
}) => {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v))
  return call<Task[]>(`/api/tasks?${q}`)
}
export const fetchTask = (id: string) =>
  call<Task & { history: TaskHistoryItem[] }>(`/api/tasks/${id}`)
export const createTask = (t: TaskInput) => call<Task>('/api/tasks', body('POST', t))
export const updateTask = (id: string, t: TaskInput) => call<Task>(`/api/tasks/${id}`, body('PUT', t))
export const setTaskAssignees = (id: string, user_ids: string[]) =>
  call<Task>(`/api/tasks/${id}/assignees`, body('PUT', { user_ids }))
export const deleteTask = (id: string) => call<void>(`/api/tasks/${id}`, { method: 'DELETE' })
export const fetchMyTasks = () => call<Task[]>('/api/me/tasks')
export const fetchMyBoard = () =>
  call<{ tasks: Task[]; today_ids: string[] }>('/api/me/board')

export const STATUS_LABEL: Record<TaskStatus, string> = {
  todo: 'Na rade',
  in_progress: 'Rozpracovaná',
  done: 'Hotová',
  cancelled: 'Zrušená',
}

// --- import úloh ------------------------------------------------------------------------

export type ImportStatus = 'waiting' | 'extracting' | 'review' | 'applied' | 'error'

export interface ImportSource {
  id: string
  doc_id: string | null
  site_id: string
  site_name: string
  filename: string
  previous_id: string | null
  version: number
  doc_date: string | null
  status: ImportStatus
  progress: number
  error: string | null
  created_at: string
}

export interface DraftData {
  title: string
  description: string
  work_type: string | null
  person?: string | null
  date_text?: string | null
  after_text?: string | null
  date_from: string | null
  date_to: string | null
  date_how?: 'absolute' | 'relative' | 'none'
  depends_on?: { draft?: number; task?: string; title: string } | null
  assignees: string[]
  assign_reason?: string
  assign_basis?: 'named' | 'scored' | 'none'
  quote: string
  page: number | null
  boxes?: number[][]
  confidence: 'high' | 'medium' | 'low'
  issues: string[]
  changes?: Record<string, [string | null, string | null]>
  task_status?: TaskStatus
}

export interface Draft {
  id: string
  kind: 'new' | 'changed' | 'cancelled' | 'unchanged'
  target_task_id: string | null
  decision: 'pending' | 'approved' | 'rejected'
  data: DraftData
}

export interface ImportDetail {
  source: ImportSource
  drafts: Draft[]
}

export interface DraftUpdate {
  title: string
  description: string
  work_type: string | null
  date_from: string | null
  date_to: string | null
  assignees: string[]
  decision: Draft['decision']
  clear_dependency?: boolean
}

export const fetchImports = () => call<ImportSource[]>('/api/task-imports')
export const fetchImport = (id: string) => call<ImportDetail>(`/api/task-imports/${id}`)
export const suggestPrevious = (site_id: string, filename: string) =>
  call<ImportSource | null>(
    `/api/task-imports/suggest-previous?${new URLSearchParams({ site_id, filename })}`,
  )
export function uploadTaskDocument(file: File, site_id: string, previous_id: string | null) {
  const form = new FormData()
  form.append('file', file)
  form.append('site_id', site_id)
  if (previous_id) form.append('previous_id', previous_id)
  return call<ImportSource>('/api/task-imports', { method: 'POST', body: form })
}
export const updateDraft = (sourceId: string, draftId: string, d: DraftUpdate) =>
  call<Draft>(`/api/task-imports/${sourceId}/drafts/${draftId}`, body('PUT', d))
export const decideAll = (sourceId: string, decision: 'approved' | 'rejected') =>
  call<ImportDetail>(`/api/task-imports/${sourceId}/decide-all`, body('POST', { decision }))
export const applyImport = (sourceId: string) =>
  call<{ created: number; updated: number; cancelled: number }>(
    `/api/task-imports/${sourceId}/apply`,
    { method: 'POST' },
  )
export const retryImport = (sourceId: string) =>
  call<ImportSource>(`/api/task-imports/${sourceId}/retry`, { method: 'POST' })

// --- dochádzka --------------------------------------------------------------------------

export type EntryStatus = 'done' | 'in_progress' | 'not_started'

export interface TaskEntry {
  task_id: string
  status: EntryStatus | null
  progress: number | null
  hours: number | null
  note: string
}

export interface ReportData {
  absent: boolean
  absence_reason: string
  arrival: string | null
  departure: string | null
  break_minutes: number | null
  tasks: TaskEntry[]
  extra_work: { description: string; hours: number | null }[]
  problems: string[]
  problems_answered: boolean
  summary: string
}

export type QuestionKind =
  | 'absence'
  | 'arrival'
  | 'departure'
  | 'break'
  | 'task'
  | 'extra'
  | 'problems'
  | 'hours'

export interface Question {
  id: string
  key: string
  kind: QuestionKind
  task_id: string | null
  detail: string
  text: string
  attempts: number
  mode: 'open' | 'choices'
  status: 'open' | 'answered' | 'obsolete'
  answer: string | null
}

export type ReportStatus = 'draft' | 'processing' | 'questioning' | 'ready' | 'submitted'

export interface Report {
  id: string
  user_id: string
  worker_name: string
  day: string
  status: ReportStatus
  step: string | null
  error: string | null
  transcript: string
  data: ReportData
  tasks: {
    id: string
    title: string
    site_name: string
    description: string
    status: TaskStatus
    progress: number
  }[]
  questions: Question[]
  open_questions: number
  worked_hours: number | null
  reported_hours: number
  can_submit: boolean
  submitted_at: string | null
  edited_by_owner: boolean
  pdf_url: string | null
  inputs: {
    id: string
    kind: 'audio' | 'text' | 'choice'
    text: string
    question_id: string | null
    has_audio: boolean
    created_at: string
  }[]
  tts: boolean
}

export interface ChoiceAnswer {
  time?: string
  minutes?: number
  status?: EntryStatus
  progress?: number
  hours?: number
  note?: string
  problems?: string[]
  task_hours?: Record<string, number>
  extra_hours?: number[]
  absence_reason?: string
}

export const fetchTodayReport = (workerId?: string, day?: string) => {
  const q = new URLSearchParams()
  if (workerId) q.set('worker_id', workerId)
  if (day) q.set('day', day)
  return call<Report>(`/api/attendance/today?${q}`)
}
export const fetchReport = (id: string) => call<Report>(`/api/attendance/reports/${id}`)

function audioForm(blob: Blob) {
  const form = new FormData()
  const ext = blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'
  form.append('file', blob, `nahravka.${ext}`)
  return form
}

export const sendReportAudio = (id: string, blob: Blob) =>
  call<Report>(`/api/attendance/reports/${id}/audio`, { method: 'POST', body: audioForm(blob) })
export const sendReportText = (id: string, text: string) =>
  call<Report>(`/api/attendance/reports/${id}/text`, body('POST', { text }))
export const answerText = (id: string, qid: string, text: string) =>
  call<Report>(`/api/attendance/reports/${id}/questions/${qid}/text`, body('POST', { text }))
export const answerAudio = (id: string, qid: string, blob: Blob) =>
  call<Report>(`/api/attendance/reports/${id}/questions/${qid}/audio`, {
    method: 'POST',
    body: audioForm(blob),
  })
export const answerChoice = (id: string, qid: string, choice: ChoiceAnswer) =>
  call<Report>(`/api/attendance/reports/${id}/questions/${qid}/choice`, body('POST', choice))
export const submitReport = (id: string) =>
  call<Report>(`/api/attendance/reports/${id}/submit`, { method: 'POST' })
export const ownerEditReport = (id: string, data: ReportData) =>
  call<Report>(`/api/attendance/reports/${id}`, body('PUT', { data }))
export const questionSpeechUrl = (qid: string) => `/api/attendance/questions/${qid}/speech`

// --- otázky pracovníka na vlastné údaje (mobil, PLAN.md §17) -----------------------------

export interface AskAnswer {
  question: string
  answer: string
  speech_text: string
  tts: boolean
}

export const askFirma = (question: string) =>
  call<AskAnswer>('/api/attendance/ask', body('POST', { question }))
export const askFirmaAudio = (blob: Blob) =>
  call<AskAnswer>('/api/attendance/ask/audio', { method: 'POST', body: audioForm(blob) })

/** Odpoveď nahlas (Piper) — wav ako blob, prehrá sa cez object URL. */
export async function askSpeech(text: string): Promise<Blob> {
  const res = await fetch('/api/attendance/ask/speech', body('POST', { text }))
  if (res.status === 401) window.dispatchEvent(new Event('rag:unauthorized'))
  if (!res.ok) throw new ApiError(res.status, 'odpoveď sa nedá prečítať nahlas', null)
  return res.blob()
}

export interface DayRow {
  worker_id: string
  worker_name: string
  report_id: string | null
  status: ReportStatus | 'missing'
  absent: boolean
  arrival: string | null
  departure: string | null
  worked_hours: number | null
  problems: number
  open_questions: number
  pdf_url: string | null
}

export interface DayOverview {
  day: string
  rows: DayRow[]
  summary_url: string | null
  summary_stale: boolean
  summary: {
    submitted_count: number
    worker_count: number
    total_hours: number
    done_count: number
    missing: string[]
    problems: number
  }
}

export const fetchDay = (day: string) => call<DayOverview>(`/api/attendance/days/${day}`)
export const generateSummary = (day: string) =>
  call<DayOverview>(`/api/attendance/days/${day}/summary`, { method: 'POST' })
export const summaryPdfUrl = (day: string) => `/api/attendance/days/${day}/summary.pdf`

// --- pomocníci --------------------------------------------------------------------------

export function isoToday(): string {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00`)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

export function startOfWeek(iso: string): string {
  const d = new Date(`${iso}T12:00:00`)
  const shift = (d.getDay() + 6) % 7
  return addDays(iso, -shift)
}

const WEEKDAYS = ['nedeľa', 'pondelok', 'utorok', 'streda', 'štvrtok', 'piatok', 'sobota']

export function formatDay(iso: string, withWeekday = true): string {
  const d = new Date(`${iso}T12:00:00`)
  const base = `${d.getDate()}. ${d.getMonth() + 1}. ${d.getFullYear()}`
  return withWeekday ? `${WEEKDAYS[d.getDay()]} ${base}` : base
}

export function formatShort(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(`${iso}T12:00:00`)
  return `${d.getDate()}. ${d.getMonth() + 1}.`
}

export function formatHours(h: number | null | undefined): string {
  if (h === null || h === undefined) return '—'
  return `${String(Math.round(h * 100) / 100).replace('.', ',')} h`
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join('')
}
