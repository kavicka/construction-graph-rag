import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'
import {
  fetchChatMessages,
  fetchChats,
  type ChatMessage,
  type SourceRef,
} from '@/lib/api'
import { streamChat } from '@/lib/sse'
import { cn } from '@/lib/utils'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, Plus, Send, Square, Table2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { toast } from 'sonner'

type LocalMessage = Pick<ChatMessage, 'role' | 'content' | 'sources'>

function linkifyCitations(content: string): string {
  return content.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_m, nums: string) =>
    nums
      .split(',')
      .map((x) => `[${x.trim()}](#src-${x.trim()})`)
      .join(' '),
  )
}

function SourceChip({ source, onOpen }: { source: SourceRef; onOpen: () => void }) {
  const Icon = source.type === 'table_summary' ? Table2 : FileText
  const pages =
    source.page_to !== source.page_from
      ? `${source.page_from}–${source.page_to}`
      : `${source.page_from}`
  return (
    <button onClick={onOpen} title={source.snippet} className="group">
      <Badge variant="outline" className="gap-1 group-hover:bg-secondary">
        <span className="font-semibold">{source.n}</span>
        <Icon className="size-3" />
        <span className="max-w-40 truncate">{source.filename}</span>
        <span className="text-muted-foreground">str. {pages}</span>
      </Badge>
    </button>
  )
}

function AssistantBody({
  content,
  sources,
  onCite,
}: {
  content: string
  sources: SourceRef[] | null
  onCite: (n: number) => void
}) {
  return (
    <div className="space-y-2">
      <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed [&_p]:my-2 [&_ul]:my-2">
        <ReactMarkdown
          components={{
            a: ({ href, children }) =>
              href?.startsWith('#src-') ? (
                <button
                  onClick={() => onCite(Number(href.slice(5)))}
                  className="mx-0.5 inline-flex size-4 items-center justify-center rounded bg-secondary align-super text-[10px] font-semibold text-secondary-foreground hover:bg-primary hover:text-primary-foreground"
                >
                  {children}
                </button>
              ) : (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              ),
          }}
        >
          {linkifyCitations(content)}
        </ReactMarkdown>
      </div>
      {sources && sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {sources.map((s) => (
            <SourceChip key={s.n} source={s} onOpen={() => onCite(s.n)} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ChatPage() {
  const queryClient = useQueryClient()
  const [chatId, setChatId] = useState<string | null>(null)
  const [messages, setMessages] = useState<LocalMessage[]>([])
  const [draft, setDraft] = useState('')
  const [streamText, setStreamText] = useState<string | null>(null)
  const [streamSources, setStreamSources] = useState<SourceRef[] | null>(null)
  const [steps, setSteps] = useState<{ tool: string; summary: string }[]>([])
  const [planMode, setPlanMode] = useState<string | null>(null)
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const { data: chats } = useQuery({ queryKey: ['chats'], queryFn: fetchChats })

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamText])

  const openChat = async (id: string) => {
    abortRef.current?.abort()
    setChatId(id)
    setStreamText(null)
    setMessages(await fetchChatMessages(id))
  }

  const newChat = () => {
    abortRef.current?.abort()
    setChatId(null)
    setMessages([])
    setStreamText(null)
  }

  const citeFrom = (sources: SourceRef[] | null) => (n: number) => {
    const src = sources?.find((s) => s.n === n)
    if (src) setPdfTarget({ docId: src.doc_id, filename: src.filename, page: src.page_from })
  }

  const send = () => {
    const question = draft.trim()
    if (!question || streamText !== null) return
    setDraft('')
    setMessages((m) => [...m, { role: 'user', content: question, sources: null }])
    setStreamText('')
    setStreamSources(null)
    setSteps([])
    setPlanMode(null)

    const controller = new AbortController()
    abortRef.current = controller
    let acc = ''
    let sources: SourceRef[] | null = null

    void streamChat(
      { message: question, chat_id: chatId ?? undefined },
      {
        onSources: (id, s) => {
          setChatId(id)
          sources = s
          setStreamSources(s)
        },
        onDelta: (t) => {
          acc += t
          setStreamText(acc)
        },
        onDone: () => {
          setMessages((m) => [...m, { role: 'assistant', content: acc, sources }])
          setStreamText(null)
          void queryClient.invalidateQueries({ queryKey: ['chats'] })
        },
        onError: (msg) => {
          toast.error(msg)
          if (acc) setMessages((m) => [...m, { role: 'assistant', content: acc, sources }])
          setStreamText(null)
        },
        onPlan: (mode) => setPlanMode(mode),
        onStep: (tool, summary) => setSteps((s) => [...s, { tool, summary }]),
        onVerify: (ok, issues) => {
          if (!ok) toast.warning(`Overenie čísel: v podkladoch chýba ${issues.join(', ')}`)
        },
      },
      controller.signal,
    ).catch((e: Error) => {
      if (e.name !== 'AbortError') toast.error(e.message)
      setStreamText(null)
    })
  }

  const stop = () => {
    abortRef.current?.abort()
    if (streamText) {
      setMessages((m) => [
        ...m,
        { role: 'assistant', content: streamText, sources: streamSources },
      ])
    }
    setStreamText(null)
  }

  return (
    <div className="mx-auto grid h-[calc(100svh-8.5rem)] max-w-5xl grid-cols-[200px_1fr] gap-6">
      <aside className="flex min-h-0 flex-col gap-2">
        <Button variant="outline" size="sm" onClick={newChat} className="justify-start gap-2">
          <Plus className="size-4" /> Nový chat
        </Button>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-1 pr-2">
            {chats?.map((c) => (
              <button
                key={c.id}
                onClick={() => void openChat(c.id)}
                className={cn(
                  'w-full truncate rounded-md px-2 py-1.5 text-left text-xs transition-colors',
                  c.id === chatId
                    ? 'bg-secondary font-medium'
                    : 'text-muted-foreground hover:bg-secondary/50',
                )}
              >
                {c.title}
              </button>
            ))}
          </div>
        </ScrollArea>
      </aside>

      <section className="flex min-h-0 flex-col gap-3">
        <ScrollArea className="min-h-0 flex-1 rounded-md border">
          <div className="space-y-4 p-4">
            {messages.length === 0 && streamText === null && (
              <p className="py-16 text-center text-sm text-muted-foreground">
                Opýtaj sa na čokoľvek z nahratých PDF — odpoveď príde s citáciami na strany.
              </p>
            )}
            {messages.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                    {m.content}
                  </div>
                </div>
              ) : (
                <AssistantBody
                  key={i}
                  content={m.content}
                  sources={m.sources}
                  onCite={citeFrom(m.sources)}
                />
              ),
            )}
            {streamText !== null && (
              <div className="space-y-2">
                {(planMode === 'filter' || planMode === 'compose') && (
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <p>
                      režim: <Badge variant="outline">{planMode === 'filter' ? 'presný filter' : 'agent'}</Badge>
                    </p>
                    {steps.map((s, i) => (
                      <p key={i} className="truncate">
                        → {s.tool}: {s.summary}
                      </p>
                    ))}
                  </div>
                )}
                <AssistantBody
                  content={streamText || '…'}
                  sources={streamSources}
                  onCite={citeFrom(streamSources)}
                />
              </div>
            )}
            <div ref={endRef} />
          </div>
        </ScrollArea>

        <div className="flex items-end gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder="Otázka… (Enter odošle, Shift+Enter nový riadok)"
            className="max-h-40 min-h-11 flex-1 resize-none"
            rows={1}
          />
          {streamText !== null ? (
            <Button variant="outline" size="icon" onClick={stop}>
              <Square className="size-4" />
            </Button>
          ) : (
            <Button size="icon" onClick={send} disabled={!draft.trim()}>
              <Send className="size-4" />
            </Button>
          )}
        </div>
      </section>

      <PdfDialog target={pdfTarget} onClose={() => setPdfTarget(null)} />
    </div>
  )
}
