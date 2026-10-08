import type { GeneratedDoc, SentenceCheck, SourceRef } from './api'

export type PlanMode = 'lookup' | 'filter' | 'compose' | 'generate' | 'research'

export interface StreamHandlers {
  onSources: (chatId: string, sources: SourceRef[]) => void
  onDelta: (text: string) => void
  onDone: (chatId: string, messageId: string, traceId: string | null) => void
  onError: (message: string) => void
  onPlan?: (mode: PlanMode) => void
  onStep?: (tool: string, summary: string) => void
  onVerify?: (ok: boolean, issues: string[], sentences: SentenceCheck[]) => void
  onDocument?: (doc: GeneratedDoc) => void
  onRewrite?: (question: string) => void
}

type StreamEvent =
  | { type: 'sources'; chat_id: string; sources: SourceRef[] }
  | { type: 'delta'; text: string }
  | { type: 'done'; chat_id: string; message_id: string; trace_id: string | null }
  | { type: 'error'; message: string }
  | { type: 'plan'; mode: PlanMode }
  | { type: 'step'; tool: string; summary: string }
  | { type: 'verify'; ok: boolean; issues: string[]; sentences?: SentenceCheck[] }
  | { type: 'document'; document: GeneratedDoc }
  | { type: 'rewrite'; question: string }

export async function streamChat(
  body: { message: string; chat_id?: string; mode?: 'auto' | 'research' },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    handlers.onError(`server vrátil ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatch = (evt: StreamEvent) => {
    switch (evt.type) {
      case 'sources':
        handlers.onSources(evt.chat_id, evt.sources)
        break
      case 'delta':
        handlers.onDelta(evt.text)
        break
      case 'done':
        handlers.onDone(evt.chat_id, evt.message_id, evt.trace_id ?? null)
        break
      case 'error':
        handlers.onError(evt.message)
        break
      case 'plan':
        handlers.onPlan?.(evt.mode)
        break
      case 'step':
        handlers.onStep?.(evt.tool, evt.summary)
        break
      case 'verify':
        handlers.onVerify?.(evt.ok, evt.issues, evt.sentences ?? [])
        break
      case 'document':
        handlers.onDocument?.(evt.document)
        break
      case 'rewrite':
        handlers.onRewrite?.(evt.question)
        break
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const dataLine = frame.split('\n').find((l) => l.startsWith('data: '))
      if (!dataLine) continue
      try {
        dispatch(JSON.parse(dataLine.slice(6)) as StreamEvent)
      } catch {
        // poškodený frame preskoč — stream pokračuje
      }
    }
  }
}
