import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EmptyState, ErrorState, PageHeader } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { AbsenceChoice, QuestionChoices } from '@/components/wf/question-choices'
import { RecordButton } from '@/components/wf/record-button'
import { ReportSummary } from '@/components/wf/report-summary'
import { useVoice } from '@/hooks/use-voice'
import {
  answerAudio,
  answerChoice,
  answerText,
  fetchTodayReport,
  fetchWorkers,
  formatDay,
  isoToday,
  questionSpeechUrl,
  sendReportAudio,
  sendReportText,
  submitReport,
  type ChoiceAnswer,
  type Question,
  type Report,
} from '@/lib/workforce'
import {
  Loading03Icon,
  Pdf01Icon,
  SentIcon,
  Tick02Icon,
  UserGroupIcon,
  VolumeHighIcon,
  VolumeOffIcon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

const MUTE_KEY = 'attendance-muted'

function readMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === '1'
  } catch {
    return false
  }
}

const STATUS_CHIP: Record<Report['status'], { label: string; variant: 'neutral' | 'info' | 'warning' | 'success' }> = {
  draft: { label: 'Začni výpisom', variant: 'neutral' },
  processing: { label: 'Spracúvam', variant: 'info' },
  questioning: { label: 'Doplň odpovede', variant: 'warning' },
  ready: { label: 'Pripravené na odovzdanie', variant: 'success' },
  submitted: { label: 'Odovzdané', variant: 'success' },
}

/** Attendance: a worker (PIN) reports for themselves; the owner picks a worker and a day. */
export default function AttendancePage({ isAdmin }: { isAdmin: boolean }) {
  const [picked, setWorkerId] = useState<string>('')
  const [day, setDay] = useState(isoToday())
  const workers = useQuery({ queryKey: ['workers'], queryFn: fetchWorkers, enabled: isAdmin })
  const activeWorkers = workers.data?.filter((w) => w.active) ?? []
  // no selection → the first active worker (derived during render, not in an effect)
  const workerId = picked || activeWorkers[0]?.id || ''

  const enabled = !isAdmin || !!workerId
  return (
    <div className="max-w-2xl space-y-6">
      {/* the worker already has the heading in the "Denný výpis" tab; a second one would repeat it */}
      {isAdmin && (
        <PageHeader
          title="Dochádzka"
          description="Vyplň alebo skontroluj denný výpis pracovníka."
          actions={
            activeWorkers.length > 0 ? (
              <>
                <Select value={workerId} onValueChange={setWorkerId}>
                  <SelectTrigger className="w-44">
                    <SelectValue placeholder="Pracovník" />
                  </SelectTrigger>
                  <SelectContent>
                    {activeWorkers.map((w) => (
                      <SelectItem key={w.id} value={w.id}>
                        {w.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input type="date" value={day} max={isoToday()} onChange={(e) => setDay(e.target.value || isoToday())} className="w-40" />
              </>
            ) : undefined
          }
        />
      )}
      {isAdmin && workers.isSuccess && activeWorkers.length === 0 ? (
        <EmptyState
          icon={UserGroupIcon}
          title="Zatiaľ žiadni pracovníci"
          description="Pridaj pracovníkov v sekcii Tím a stavby. Každý dostane PIN na prihlásenie z mobilu."
        />
      ) : enabled ? (
        <ReportView workerId={isAdmin ? workerId : undefined} day={isAdmin ? day : undefined} />
      ) : (
        <Skeleton className="h-64" />
      )}
    </div>
  )
}

function ReportView({ workerId, day }: { workerId?: string; day?: string }) {
  const queryClient = useQueryClient()
  const key = ['attendance', workerId ?? 'me', day ?? 'today']
  const { data: report, isPending, error, refetch } = useQuery({
    queryKey: key,
    queryFn: () => fetchTodayReport(workerId, day),
    refetchInterval: (q) => (q.state.data?.status === 'processing' ? 1000 : false),
  })
  const setReport = (r: Report) => queryClient.setQueryData(key, r)

  const sendAudio = useMutation({
    mutationFn: (blob: Blob) => sendReportAudio(report!.id, blob),
    onSuccess: setReport,
  })
  const sendText = useMutation({
    mutationFn: (text: string) => sendReportText(report!.id, text),
    onSuccess: setReport,
  })
  const submit = useMutation({
    mutationFn: () => submitReport(report!.id),
    onSuccess: (r) => {
      setReport(r)
      toast.success('Výpis odovzdaný, majiteľ dostal PDF')
    },
  })

  if (isPending) return <Skeleton className="h-64" />
  if (error) return <ErrorState error={error} title="Výpis sa nepodarilo načítať" onRetry={() => void refetch()} />

  const busy = report.status === 'processing' || sendAudio.isPending || sendText.isPending
  const open = report.questions.filter((q) => q.status === 'open')
  const answered = report.questions.filter((q) => q.status === 'answered').length
  const current = open[0]
  const chip = STATUS_CHIP[report.status]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <p className="label-mono text-sm text-muted-foreground">{formatDay(report.day)}</p>
        <Chip variant={chip.variant}>{chip.label}</Chip>
      </div>

      {report.status === 'draft' && <TodayTasks report={report} own={!workerId} />}

      {report.error && report.status !== 'processing' && (
        <p className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger" role="alert">
          {report.error}
        </p>
      )}

      {report.status === 'processing' && (
        <div className="flex flex-col items-center gap-3 rounded-lg border px-4 py-10 text-center">
          <HugeiconsIcon icon={Loading03Icon} className="size-8 animate-spin text-primary" />
          <p className="text-sm font-medium">{report.step === 'prepis' ? 'Prepisujem reč…' : 'Spracúvam výpis…'}</p>
          <p className="max-w-xs text-xs text-muted-foreground">Spracúva ho lokálny model na tvojom serveri. Nič neodchádza von.</p>
        </div>
      )}

      {report.status === 'draft' && (
        <MainInput
          busy={busy}
          onAudio={(b) => sendAudio.mutate(b)}
          onText={(t) => sendText.mutate(t)}
          prompt="Povedz, čo sa dnes robilo: začiatok a koniec dňa, na ktorých úlohách a koľko hodín, čo je hotové a či boli problémy."
        />
      )}

      {report.status === 'questioning' && current && (
        <QuestionCard
          key={current.id}
          report={report}
          question={current}
          index={answered + 1}
          total={answered + open.length}
          onUpdate={setReport}
        />
      )}

      {report.status !== 'draft' && report.status !== 'processing' && <ReportSummary report={report} />}

      {(report.status === 'questioning' || report.status === 'ready') && (
        <details className="group rounded-lg border px-4 py-3">
          <summary className="cursor-pointer text-sm text-muted-foreground select-none group-open:mb-3">
            Doplniť ďalšiu informáciu hlasom alebo textom
          </summary>
          <MainInput busy={busy} compact onAudio={(b) => sendAudio.mutate(b)} onText={(t) => sendText.mutate(t)} />
        </details>
      )}

      {report.status === 'submitted' ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-success/30 bg-success/5 px-4 py-6 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-success/15 text-success">
            <HugeiconsIcon icon={Tick02Icon} className="size-5" />
          </span>
          <p className="text-sm font-medium">Výpis je odovzdaný</p>
          {report.pdf_url && (
            <Button asChild variant="outline" className="gap-2">
              <a href={report.pdf_url} target="_blank" rel="noreferrer">
                <HugeiconsIcon icon={Pdf01Icon} className="size-4" />
                Otvoriť PDF
              </a>
            </Button>
          )}
        </div>
      ) : (
        report.status !== 'draft' && (
          <Tooltip>
            <TooltipTrigger asChild>
              {/* span — the tooltip must work over a disabled button too */}
              <span className="block">
                <Button
                  className="h-12 w-full text-base"
                  disabled={!report.can_submit || submit.isPending || busy}
                  onClick={() => submit.mutate()}
                >
                  Odovzdať výpis
                </Button>
              </span>
            </TooltipTrigger>
            {!report.can_submit && (
              <TooltipContent>
                {report.open_questions > 0
                  ? `Najprv odpovedz na ${report.open_questions} ${report.open_questions === 1 ? 'otázku' : 'otázky'}`
                  : 'Počkaj, kým sa výpis spracuje'}
              </TooltipContent>
            )}
          </Tooltip>
        )
      )}
    </div>
  )
}

function TodayTasks({ report, own }: { report: Report; own: boolean }) {
  if (!report.tasks.length) {
    return <p className="text-sm text-muted-foreground">Na tento deň nie sú priradené žiadne úlohy. Stačí povedať, čo sa robilo.</p>
  }
  return (
    <div className="space-y-2">
      <p className="label-mono text-xs text-muted-foreground uppercase">{own ? 'Tvoje úlohy na dnes' : 'Úlohy na tento deň'}</p>
      <ul className="divide-y rounded-lg border">
        {report.tasks.map((t) => (
          <li key={t.id} className="flex items-center gap-3 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              <p className="label-mono text-2xs text-muted-foreground">{t.site_name}</p>
            </div>
            {t.status === 'in_progress' && <Chip variant="info">{t.progress} %</Chip>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function MainInput({
  busy,
  onAudio,
  onText,
  prompt,
  compact,
}: {
  busy: boolean
  onAudio: (b: Blob) => void
  onText: (t: string) => void
  prompt?: string
  compact?: boolean
}) {
  const { stt } = useVoice()
  const [typing, setTyping] = useState(false)
  const [text, setText] = useState('')
  const send = () => {
    if (text.trim()) {
      onText(text.trim())
      setText('')
      setTyping(false)
    }
  }
  return (
    <div className={compact ? 'space-y-3' : 'space-y-4 rounded-lg border px-4 py-6'}>
      {prompt && <p className="text-center text-sm text-muted-foreground">{prompt}</p>}
      {stt && !typing ? (
        <div className={compact ? 'flex flex-wrap items-center gap-3' : 'flex flex-col items-center gap-4'}>
          <RecordButton onRecorded={onAudio} disabled={busy} size={compact ? 'sm' : 'lg'} idleLabel={compact ? 'Nahrať' : 'Ťukni a hovor'} />
          <button
            type="button"
            onClick={() => setTyping(true)}
            className="text-sm text-muted-foreground underline-offset-2 hover:text-primary hover:underline"
          >
            Radšej napísať
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={compact ? 3 : 6}
            placeholder="Dnes som prišiel o siedmej, murovali sme priečky na 2. NP…"
            autoFocus
          />
          <div className={stt ? 'flex justify-between gap-2' : 'flex justify-end gap-2'}>
            {stt && (
              <Button type="button" variant="ghost" onClick={() => setTyping(false)}>
                Späť na hlas
              </Button>
            )}
            <Button type="button" onClick={send} disabled={busy || !text.trim()} className="gap-2">
              <HugeiconsIcon icon={SentIcon} className="size-4" />
              Odoslať
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function QuestionCard({
  report,
  question,
  index,
  total,
  onUpdate,
}: {
  report: Report
  question: Question
  index: number
  total: number
  onUpdate: (r: Report) => void
}) {
  const { stt } = useVoice()
  const [muted, setMuted] = useState(readMuted)
  const [text, setText] = useState('')
  const [absence, setAbsence] = useState(false)
  const audio = useRef<HTMLAudioElement>(null)
  const viaText = useMutation({ mutationFn: (t: string) => answerText(report.id, question.id, t), onSuccess: onUpdate })
  const viaAudio = useMutation({ mutationFn: (b: Blob) => answerAudio(report.id, question.id, b), onSuccess: onUpdate })
  const viaChoice = useMutation({
    mutationFn: (c: ChoiceAnswer) => answerChoice(report.id, question.id, c),
    onSuccess: onUpdate,
  })
  const busy = viaText.isPending || viaAudio.isPending || viaChoice.isPending

  // the question is read aloud as soon as it arrives (the browser allows it after the previous tap)
  useEffect(() => {
    if (muted || !report.tts || !audio.current) return
    audio.current.src = questionSpeechUrl(question.id)
    void audio.current.play().catch(() => undefined)
  }, [question.id, question.text, muted, report.tts])

  const toggleMute = () => {
    const next = !muted
    setMuted(next)
    try {
      localStorage.setItem(MUTE_KEY, next ? '1' : '0')
    } catch {
      // without storage, only for this page
    }
    if (next) audio.current?.pause()
  }

  return (
    <div className="space-y-4 rounded-lg border border-primary/30 bg-primary/[0.03] p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="label-mono text-xs text-primary">
          otázka {index} z {total}
        </p>
        {report.tts && (
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                if (audio.current) {
                  audio.current.src = questionSpeechUrl(question.id)
                  void audio.current.play().catch(() => undefined)
                }
              }}
              aria-label="Prečítať otázku"
            >
              <HugeiconsIcon icon={VolumeHighIcon} className="size-4" />
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={toggleMute} aria-label={muted ? 'Zapnúť čítanie' : 'Vypnúť čítanie'}>
              <HugeiconsIcon icon={muted ? VolumeOffIcon : VolumeHighIcon} className="size-4" />
              <span className="text-xs">{muted ? 'zvuk vyp.' : 'zvuk zap.'}</span>
            </Button>
          </div>
        )}
      </div>
      <audio ref={audio} preload="none" />
      <p className="text-lg leading-snug">{question.text}</p>
      {question.attempts === 1 && question.mode === 'open' && (
        <p className="text-xs text-muted-foreground">Odpoveď nebola jasná. Skús to konkrétnejšie.</p>
      )}

      {absence ? (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">Prečo dnes nebola práca?</p>
          <AbsenceChoice disabled={busy} onAnswer={(c) => viaChoice.mutate(c)} />
          <button type="button" onClick={() => setAbsence(false)} className="text-xs text-muted-foreground hover:text-primary">
            Späť na otázku
          </button>
        </div>
      ) : question.mode === 'choices' ? (
        <QuestionChoices question={question} report={report} disabled={busy} onAnswer={(c) => viaChoice.mutate(c)} />
      ) : (
        <div className="space-y-3">
          {stt && <RecordButton size="sm" idleLabel="Odpovedať hlasom" disabled={busy} onRecorded={(b) => viaAudio.mutate(b)} />}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (text.trim()) viaText.mutate(text.trim())
            }}
          >
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={stt ? 'alebo napíš odpoveď' : 'Napíš odpoveď'} className="h-11" />
            <Button type="submit" className="h-11" disabled={busy || !text.trim()} aria-label="Odoslať odpoveď">
              <HugeiconsIcon icon={SentIcon} className="size-4" />
            </Button>
          </form>
        </div>
      )}
      {!absence && question.kind !== 'absence' && (
        <button
          type="button"
          onClick={() => setAbsence(true)}
          className="text-xs text-muted-foreground underline-offset-2 hover:text-primary hover:underline"
        >
          Dnes som nepracoval(a): dovolenka, PN, lekár
        </button>
      )}
    </div>
  )
}
