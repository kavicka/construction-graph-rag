import { toast } from 'sonner'

/** HTTP error from the backend; `message` is the backend `detail` when it is readable text. */
export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

/** FastAPI 422 → readable messages per field. */
export class ValidationError extends Error {
  fields: Record<string, string>

  constructor(fields: Record<string, string>) {
    super(Object.values(fields).join(', '))
    this.fields = fields
  }
}

const BY_STATUS: Record<number, string> = {
  401: 'Prihlásenie vypršalo. Prihlás sa znova.',
  403: 'Na túto akciu nemáš oprávnenie.',
  404: 'Položka sa nenašla. Možno ju medzitým niekto zmazal.',
  409: 'Akcia je v konflikte s aktuálnym stavom. Obnov stránku.',
  413: 'Súbor je príliš veľký.',
  429: 'Príliš veľa pokusov. Chvíľu počkaj.',
}

const FALLBACK = 'Niečo sa pokazilo. Skús to znova.'

/**
 * One user-facing Slovak message for any thrown value. Backend `detail` strings are already
 * Slovak; raw status lines, HTML error pages and network failures get a generic message.
 */
export function errorMessage(e: unknown): string {
  if (e instanceof ValidationError) return 'Skontroluj zvýraznené polia.'
  // fetch() rejects with TypeError only when the request never reached the server
  if (e instanceof TypeError) return 'Server je nedostupný. Skontroluj pripojenie a skús to znova.'
  const message = e instanceof Error ? e.message.trim() : ''
  const status = e instanceof ApiError ? e.status : undefined
  const readable = message && !/^\d{3}\b/.test(message) && !message.startsWith('<') && message.length < 300
  if (status && status >= 500 && (!readable || /server error|bad gateway|unavailable|timeout/i.test(message))) {
    return 'Chyba servera. Skús to znova o chvíľu.'
  }
  if (readable) return message
  return (status && BY_STATUS[status]) || FALLBACK
}

/** Toast for errors outside react-query mutations (streams, plain promises). */
export function notifyError(e: unknown, title?: string) {
  if (e instanceof Error && e.name === 'AbortError') return
  if (title) toast.error(title, { description: errorMessage(e) })
  else toast.error(errorMessage(e))
}
