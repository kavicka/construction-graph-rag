import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { ChoiceAnswer, EntryStatus, Question, Report } from '@/lib/workforce'
import { formatHours } from '@/lib/workforce'
import { cn } from '@/lib/utils'
import { useState, type ReactNode } from 'react'

/** Choice mode — after two unclear answers. Always completable, no LLM involved. */
export function QuestionChoices({
  question,
  report,
  disabled,
  onAnswer,
}: {
  question: Question
  report: Report
  disabled?: boolean
  onAnswer: (choice: ChoiceAnswer) => void
}) {
  switch (question.kind) {
    case 'arrival':
    case 'departure':
      return (
        <TimeChoice
          presets={
            question.kind === 'arrival'
              ? ['06:00', '06:30', '07:00', '07:30', '08:00']
              : ['14:00', '15:00', '15:30', '16:00', '17:00']
          }
          disabled={disabled}
          onPick={(time) => onAnswer({ time })}
        />
      )
    case 'break':
      return (
        <Buttons
          options={[0, 15, 30, 45, 60].map((m) => ({
            label: m === 0 ? 'Nebola' : `${m} min`,
            value: m,
          }))}
          disabled={disabled}
          onPick={(minutes) => onAnswer({ minutes })}
        />
      )
    case 'task':
      return <TaskChoice question={question} report={report} disabled={disabled} onAnswer={onAnswer} />
    case 'extra':
      return (
        <Buttons
          options={[0.5, 1, 2, 3, 4, 6, 8].map((h) => ({ label: formatHours(h), value: h }))}
          disabled={disabled}
          onPick={(hours) => onAnswer({ hours })}
        />
      )
    case 'problems':
      return <ProblemsChoice disabled={disabled} onAnswer={onAnswer} />
    case 'hours':
      return <HoursChoice report={report} disabled={disabled} onAnswer={onAnswer} />
    case 'absence':
      return <AbsenceChoice disabled={disabled} onAnswer={onAnswer} />
  }
}

function Buttons<T extends string | number>({
  options,
  value,
  disabled,
  onPick,
}: {
  options: { label: ReactNode; value: T }[]
  value?: T | null
  disabled?: boolean
  onPick: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => (
        <Button
          key={String(o.value)}
          type="button"
          variant={value === o.value ? 'default' : 'outline'}
          disabled={disabled}
          onClick={() => onPick(o.value)}
          className="h-11 min-w-16"
        >
          {o.label}
        </Button>
      ))}
    </div>
  )
}

function TimeChoice({
  presets,
  disabled,
  onPick,
}: {
  presets: string[]
  disabled?: boolean
  onPick: (t: string) => void
}) {
  const [custom, setCustom] = useState('')
  return (
    <div className="space-y-3">
      <Buttons options={presets.map((p) => ({ label: p, value: p }))} disabled={disabled} onPick={onPick} />
      <div className="flex gap-2">
        <Input
          type="time"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          className="h-11 w-36"
          aria-label="Iný čas"
        />
        <Button type="button" variant="secondary" className="h-11" disabled={disabled || !custom} onClick={() => onPick(custom)}>
          Potvrdiť
        </Button>
      </div>
    </div>
  )
}

const HOUR_OPTIONS = [0.5, 1, 2, 3, 4, 5, 6, 7, 8]

function TaskChoice({
  question,
  report,
  disabled,
  onAnswer,
}: {
  question: Question
  report: Report
  disabled?: boolean
  onAnswer: (c: ChoiceAnswer) => void
}) {
  const entry = report.data.tasks.find((e) => e.task_id === question.task_id)
  const [status, setStatus] = useState<EntryStatus | null>(entry?.status ?? null)
  const [hours, setHours] = useState<number | null>(entry?.hours ?? null)
  const [progress, setProgress] = useState<number | null>(entry?.progress ?? null)
  const [note, setNote] = useState(entry?.note ?? '')

  const ready =
    status === 'not_started'
      ? note.trim().length >= 3
      : status === 'done'
        ? hours !== null
        : status === 'in_progress' && hours !== null && progress !== null

  return (
    <div className="space-y-4">
      <Field label="Stav">
        <Buttons
          options={[
            { label: 'Hotová', value: 'done' as const },
            { label: 'Rozpracovaná', value: 'in_progress' as const },
            { label: 'Nerobilo sa', value: 'not_started' as const },
          ]}
          value={status}
          disabled={disabled}
          onPick={setStatus}
        />
      </Field>
      {status && status !== 'not_started' && (
        <Field label="Koľko hodín">
          <Buttons
            options={HOUR_OPTIONS.map((h) => ({ label: String(h).replace('.', ','), value: h }))}
            value={hours}
            disabled={disabled}
            onPick={setHours}
          />
        </Field>
      )}
      {status === 'in_progress' && (
        <Field label="Hotovo na">
          <Buttons
            options={[10, 25, 50, 75, 90].map((p) => ({ label: `${p} %`, value: p }))}
            value={progress}
            disabled={disabled}
            onPick={setProgress}
          />
        </Field>
      )}
      {status === 'not_started' && (
        <Field label="Prečo sa nerobilo">
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="napr. chýbal materiál" />
        </Field>
      )}
      <Button
        type="button"
        className="h-11 w-full"
        disabled={disabled || !ready}
        onClick={() =>
          onAnswer({
            status: status ?? undefined,
            hours: status === 'not_started' ? undefined : (hours ?? undefined),
            progress: status === 'in_progress' ? (progress ?? undefined) : undefined,
            note: note.trim() || undefined,
          })
        }
      >
        Potvrdiť
      </Button>
    </div>
  )
}

function ProblemsChoice({ disabled, onAnswer }: { disabled?: boolean; onAnswer: (c: ChoiceAnswer) => void }) {
  const [text, setText] = useState('')
  return (
    <div className="space-y-3">
      <Button type="button" variant="outline" className="h-11" disabled={disabled} onClick={() => onAnswer({ problems: [] })}>
        Žiadne problémy
      </Button>
      <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} placeholder="alebo napíš, čo sa stalo" />
      <Button
        type="button"
        variant="secondary"
        className="h-11"
        disabled={disabled || text.trim().length < 3}
        onClick={() => onAnswer({ problems: text.split('\n').map((s) => s.trim()).filter(Boolean) })}
      >
        Uložiť problém
      </Button>
    </div>
  )
}

function HoursChoice({
  report,
  disabled,
  onAnswer,
}: {
  report: Report
  disabled?: boolean
  onAnswer: (c: ChoiceAnswer) => void
}) {
  const worked = report.worked_hours ?? 0
  const worked_tasks = report.tasks.filter((t) => {
    const e = report.data.tasks.find((x) => x.task_id === t.id)
    return e && e.status !== 'not_started'
  })
  const [taskHours, setTaskHours] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      worked_tasks.map((t) => [t.id, String(report.data.tasks.find((e) => e.task_id === t.id)?.hours ?? '')]),
    ),
  )
  const [extraHours, setExtraHours] = useState<string[]>(() =>
    report.data.extra_work.map((x) => String(x.hours ?? '')),
  )
  const [otherText, setOtherText] = useState('')
  const [otherHours, setOtherHours] = useState('')

  const num = (s: string) => Number(s.replace(',', '.')) || 0
  const total =
    Object.values(taskHours).reduce((a, s) => a + num(s), 0) +
    extraHours.reduce((a, s) => a + num(s), 0) +
    (otherText.trim() ? num(otherHours) : 0)
  const ok = Math.abs(total - worked) <= 0.5

  return (
    <div className="space-y-3">
      {worked_tasks.map((t) => (
        <HoursRow key={t.id} label={t.title} value={taskHours[t.id] ?? ''} onChange={(v) => setTaskHours({ ...taskHours, [t.id]: v })} />
      ))}
      {report.data.extra_work.map((x, i) => (
        <HoursRow
          key={i}
          label={x.description}
          value={extraHours[i] ?? ''}
          onChange={(v) => setExtraHours(extraHours.map((old, j) => (j === i ? v : old)))}
        />
      ))}
      <div className="flex gap-2">
        <Input value={otherText} onChange={(e) => setOtherText(e.target.value)} placeholder="Iná práca (napr. upratovanie)" className="h-11" />
        <Input value={otherHours} onChange={(e) => setOtherHours(e.target.value)} inputMode="decimal" placeholder="h" className="h-11 w-20 tabular-nums" />
      </div>
      <p className={cn('label-mono text-xs', ok ? 'text-success' : 'text-muted-foreground')}>
        spolu {formatHours(total)} z {formatHours(worked)}
      </p>
      <Button
        type="button"
        className="h-11 w-full"
        disabled={disabled || !ok}
        onClick={() =>
          onAnswer({
            task_hours: Object.fromEntries(Object.entries(taskHours).map(([k, v]) => [k, num(v)])),
            extra_hours: extraHours.map(num),
            note: otherText.trim() || undefined,
            hours: otherText.trim() ? num(otherHours) : undefined,
          })
        }
      >
        Potvrdiť
      </Button>
    </div>
  )
}

function HoursRow({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2">
      <span className="min-w-0 flex-1 truncate text-sm">{label}</span>
      <Input value={value} onChange={(e) => onChange(e.target.value)} inputMode="decimal" className="h-11 w-20 text-right tabular-nums" />
      <span className="text-sm text-muted-foreground">h</span>
    </label>
  )
}

export function AbsenceChoice({ disabled, onAnswer }: { disabled?: boolean; onAnswer: (c: ChoiceAnswer) => void }) {
  const [other, setOther] = useState('')
  return (
    <div className="space-y-3">
      <Buttons
        options={['Dovolenka', 'Choroba (PN)', 'Lekár', 'Voľno'].map((v) => ({ label: v, value: v }))}
        disabled={disabled}
        onPick={(absence_reason) => onAnswer({ absence_reason })}
      />
      <div className="flex gap-2">
        <Input value={other} onChange={(e) => setOther(e.target.value)} placeholder="Iný dôvod" className="h-11" />
        <Button type="button" variant="secondary" className="h-11" disabled={disabled || other.trim().length < 3} onClick={() => onAnswer({ absence_reason: other.trim() })}>
          Uložiť
        </Button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="label-mono text-xs text-muted-foreground uppercase">{label}</p>
      {children}
    </div>
  )
}
