import { AssistantBody } from '@/components/chat/assistant-body'
import { ChatHistory } from '@/components/chat/chat-history'
import { Composer } from '@/components/chat/composer'
import { FeedbackBar } from '@/components/chat/feedback-bar'
import { PassageDialog } from '@/components/passage-dialog'
import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { SplitPane } from '@/components/split-pane'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { PageHeader } from '@/components/wf/bits'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import {
  fetchChatMessages,
  fetchChats,
  type ChatMessage,
  type GeneratedDoc,
  type SourceRef,
  type Verification,
} from '@/lib/api'
import { notifyError } from '@/lib/errors'
import { streamChat, type PlanMode } from '@/lib/sse'
import { Add01Icon, Clock01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

type LocalMessage = Pick<ChatMessage, 'role' | 'content' | 'sources'> & {
  id?: string
  document?: GeneratedDoc | null
  trace_id?: string | null
  rating?: -1 | 1 | null
  rewritten?: string | null
  verification?: Verification | null
}

const MODE_LABEL: Partial<Record<PlanMode, string>> = {
  filter: 'presný filter',
  compose: 'agent',
  generate: 'tvorba dokumentu',
  research: 'hĺbkový prieskum',
  firma: 'firemné údaje',
}

export default function ChatPage() {
  const queryClient = useQueryClient()
  const reducedMotion = usePrefersReducedMotion()
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
  const [historyOpen, setHistoryOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const chats = useQuery({ queryKey: ['chats'], queryFn: fetchChats })

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [messages, streamText, reducedMotion])

  const openChat = (id: string) => {
    abortRef.current?.abort()
    setHistoryOpen(false)
    setChatId(id)
    setStreamText(null)
    fetchChatMessages(id)
      .then(setMessages)
      .catch((e: unknown) => notifyError(e, 'Konverzáciu sa nepodarilo otvoriť'))
  }

  const newChat = () => {
    abortRef.current?.abort()
    setHistoryOpen(false)
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
    // open the page of the first highlight (a chunk may start on the previous page)
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
            { id: messageId, role: 'assistant', content: acc, sources, document, trace_id: traceId, rewritten, verification },
          ])
          setStreamText(null)
          void queryClient.invalidateQueries({ queryKey: ['chats'] })
        },
        onError: (msg) => {
          toast.error('Odpoveď sa nepodarilo dokončiť', { description: msg })
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
    ).catch((e: unknown) => {
      notifyError(e, 'Odpoveď sa nepodarilo načítať')
      setStreamText(null)
    })
  }

  const stop = () => {
    abortRef.current?.abort()
    if (streamText) {
      setMessages((m) => [...m, { role: 'assistant', content: streamText, sources: streamSources, document: streamDocument }])
    }
    setStreamText(null)
  }

  const historyProps = { chats: chats.data, loading: chats.isPending, activeId: chatId, onSelect: openChat }

  return (
    <SplitPane
      className="max-w-5xl"
      gridClassName="md:grid-cols-[200px_1fr]"
      header={
        <PageHeader
          compact
          title="Chat"
          actions={
            <>
              <Button variant="outline" size="sm" className="h-9 gap-2 md:hidden" onClick={() => setHistoryOpen(true)}>
                <HugeiconsIcon icon={Clock01Icon} className="size-4" /> História
              </Button>
              <Button variant="outline" size="sm" className="h-9 gap-2" onClick={newChat}>
                <HugeiconsIcon icon={Add01Icon} className="size-4" /> Nový chat
              </Button>
            </>
          }
        />
      }
      aside={
        <aside className="hidden min-h-0 md:flex">
          <ChatHistory {...historyProps} className="w-full" />
        </aside>
      }
    >
      <section aria-label="Konverzácia" className="flex min-h-0 flex-col gap-3">
        <ScrollArea className="min-h-0 flex-1 rounded-md border">
          <div className="space-y-4 p-4" aria-live="polite" aria-busy={streamText !== null}>
            {messages.length === 0 && streamText === null && (
              <div className="mx-auto max-w-md space-y-2 py-16 text-center text-sm text-muted-foreground">
                <p className="text-foreground">Opýtaj sa na čokoľvek z nahratých dokumentov.</p>
                <p>Odpoveď cituje konkrétne strany a čísla overí v podkladoch.</p>
                <p className="text-xs">
                  Chat vie vytvoriť aj dokument, napr. „Priprav report o…“ alebo „Vytvor cenovú ponuku…“. PDF dostane
                  hlavičku firmy z Nastavení.
                </p>
              </div>
            )}
            {messages.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] rounded-lg bg-secondary px-3 py-2 text-sm break-words whitespace-pre-wrap text-secondary-foreground">
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
                      onRated={(rating) => setMessages((ms) => ms.map((x, j) => (j === i ? { ...x, rating } : x)))}
                      onExported={(doc) => {
                        setMessages((ms) => ms.map((x, j) => (j === i ? { ...x, document: doc } : x)))
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

        <Composer
          draft={draft}
          onDraftChange={setDraft}
          research={research}
          onResearchChange={setResearch}
          streaming={streamText !== null}
          onSend={send}
          onStop={stop}
        />
      </section>

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="right" className="w-80 gap-2 p-0 pb-4">
          <SheetHeader>
            <SheetTitle>História konverzácií</SheetTitle>
            <SheetDescription className="sr-only">Otvor staršiu konverzáciu</SheetDescription>
          </SheetHeader>
          <ChatHistory {...historyProps} showLabel={false} className="flex-1 px-2" />
        </SheetContent>
      </Sheet>

      <PdfDialog target={pdfTarget} onClose={() => setPdfTarget(null)} />
      <PassageDialog chunkId={passageId} onClose={() => setPassageId(null)} />
    </SplitPane>
  )
}
