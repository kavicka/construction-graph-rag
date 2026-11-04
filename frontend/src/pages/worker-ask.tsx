import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { RecordButton } from '@/components/wf/record-button'
import { useVoice } from '@/hooks/use-voice'
import { askFirma, askFirmaAudio, askSpeech, type AskAnswer } from '@/lib/workforce'
import { cn } from '@/lib/utils'
import {
  BubbleChatQuestionIcon,
  Loading03Icon,
  SentIcon,
  SquareLock02Icon,
  VolumeHighIcon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { toast } from 'sonner'

const SUGGESTIONS = [
  'Čo mám dnes robiť?',
  'Čo budem robiť zajtra?',
  'Koľko hodín mám tento mesiac?',
  'Ktoré moje úlohy meškajú?',
  'S kým robím na mojich úlohách?',
]

type Item = AskAnswer & { id: number }

/** Pracovník sa pýta na svoje úlohy, dochádzku a hodiny — odpovedá kód z jeho údajov. */
export default function WorkerAskPage() {
  const { stt } = useVoice()
  const [items, setItems] = useState<Item[]>([])
  const [text, setText] = useState('')
  const nextId = useRef(1)
  const endRef = useRef<HTMLDivElement>(null)
  const onError = (e: Error) => toast.error(e.message)
  const push = (a: AskAnswer) => setItems((list) => [...list, { ...a, id: nextId.current++ }])

  const viaText = useMutation({ mutationFn: askFirma, onSuccess: push, onError })
  const viaAudio = useMutation({ mutationFn: askFirmaAudio, onSuccess: push, onError })
  const busy = viaText.isPending || viaAudio.isPending

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [items.length, busy])

  const send = (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    viaText.mutate(q)
    setText('')
  }

  return (
    <div className="space-y-4">
      {items.length === 0 && (
        <div className="space-y-3 rounded-lg border px-4 py-5">
          <div className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <HugeiconsIcon icon={BubbleChatQuestionIcon} className="size-5" />
            </span>
            <div className="space-y-1">
              <p className="text-sm font-medium">Opýtaj sa na svoje úlohy, dochádzku a hodiny</p>
              <p className="text-sm text-muted-foreground">Odpovede sú priamo z tvojich údajov. O kolegoch vidíš len mená na spoločných úlohách.</p>
            </div>
          </div>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <HugeiconsIcon icon={SquareLock02Icon} className="size-3.5" />
            Heslá, PIN-y ani iné prihlasovacie údaje sa tu nikdy nezobrazia.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <Exchange key={item.id} item={item} />
        ))}
        {busy && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <HugeiconsIcon icon={Loading03Icon} className="size-4 animate-spin" />
            {viaAudio.isPending ? 'Počúvam a hľadám odpoveď…' : 'Hľadám odpoveď…'}
          </p>
        )}
        <div ref={endRef} />
      </div>

      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            disabled={busy}
            onClick={() => send(s)}
            className="rounded-full border px-3 py-1.5 text-xs transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          send(text)
        }}
      >
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Napíš otázku…"
          maxLength={500}
          className="h-11"
          aria-label="Otázka"
        />
        <Button type="submit" size="icon" className="size-11 shrink-0" disabled={busy || !text.trim()} aria-label="Odoslať otázku">
          <HugeiconsIcon icon={SentIcon} className="size-4" />
        </Button>
      </form>
      {stt && <RecordButton size="sm" idleLabel="Opýtať sa hlasom" disabled={busy} onRecorded={(b) => viaAudio.mutate(b)} />}
    </div>
  )
}

function Exchange({ item }: { item: Item }) {
  const audio = useRef<HTMLAudioElement>(null)
  const urlRef = useRef<string | null>(null)
  const speak = useMutation({
    mutationFn: () => askSpeech(item.speech_text),
    onSuccess: (blob) => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = URL.createObjectURL(blob)
      if (audio.current) {
        audio.current.src = urlRef.current
        void audio.current.play().catch(() => undefined)
      }
    },
    onError: (e: Error) => toast.error(e.message),
  })
  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    },
    [],
  )

  return (
    <div className="space-y-2">
      <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{item.question}</p>
      <div className="rounded-lg border bg-card px-3 py-2.5">
        <div className="text-sm leading-relaxed [&_p]:my-1.5 [&_strong]:font-medium [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5">
          <ReactMarkdown components={{ img: () => null, a: ({ children }) => <span>{children}</span> }}>
            {item.answer}
          </ReactMarkdown>
        </div>
        {item.tts && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="-ml-2 mt-1 gap-1.5 text-muted-foreground"
            onClick={() => speak.mutate()}
            disabled={speak.isPending}
          >
            <HugeiconsIcon icon={speak.isPending ? Loading03Icon : VolumeHighIcon} className={cn('size-4', speak.isPending && 'animate-spin')} />
            Prečítať nahlas
          </Button>
        )}
        <audio ref={audio} preload="none" />
      </div>
    </div>
  )
}
