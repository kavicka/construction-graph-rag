import { Chip } from '@/components/wf/chip'
import type { Report, TaskEntry } from '@/lib/workforce'
import { formatHours } from '@/lib/workforce'
import { cn } from '@/lib/utils'
import { Alert02Icon, Tick02Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useState } from 'react'

export function EntryChip({ entry }: { entry: TaskEntry | undefined }) {
  if (!entry?.status) return <Chip variant="warning">chýba</Chip>
  if (entry.status === 'done')
    return (
      <Chip variant="success" icon={<HugeiconsIcon icon={Tick02Icon} className="size-3" />}>
        hotová
      </Chip>
    )
  if (entry.status === 'in_progress')
    return <Chip variant="info">rozpracovaná{entry.progress !== null ? ` ${entry.progress} %` : ''}</Chip>
  return <Chip variant="neutral">nerobilo sa</Chip>
}

function Stat({ label, value, missing }: { label: string; value: string; missing?: boolean }) {
  return (
    <div className="rounded-md bg-muted/60 px-3 py-2">
      <p className="label-mono text-[11px] text-muted-foreground uppercase">{label}</p>
      <p className={cn('text-lg tabular-nums', missing && 'text-muted-foreground')}>{value}</p>
    </div>
  )
}

/** Čo AI z výpisu pochopila — pracovník si to skontroluje pred odovzdaním. */
export function ReportSummary({ report }: { report: Report }) {
  const [showTranscript, setShowTranscript] = useState(false)
  const d = report.data
  if (d.absent) {
    return (
      <div className="rounded-lg border p-4">
        <p className="label-mono text-xs text-muted-foreground uppercase">Neprítomnosť</p>
        <p className="mt-1 text-sm">{d.absence_reason || '—'}</p>
      </div>
    )
  }
  return (
    <div className="@container space-y-4 rounded-lg border p-4">
      {/* container query — v úzkom paneli (sheet) 2 stĺpce, na stránke 4 */}
      <div className="grid grid-cols-2 gap-2 @lg:grid-cols-4">
        <Stat label="Začiatok" value={d.arrival ?? '—'} missing={!d.arrival} />
        <Stat label="Koniec" value={d.departure ?? '—'} missing={!d.departure} />
        <Stat label="Prestávka" value={d.break_minutes !== null ? `${d.break_minutes} min` : '—'} missing={d.break_minutes === null} />
        <Stat label="Odpracované" value={formatHours(report.worked_hours)} missing={report.worked_hours === null} />
      </div>

      {d.summary && <p className="text-sm text-foreground/80">{d.summary}</p>}

      {report.tasks.length > 0 && (
        <ul className="divide-y">
          {report.tasks.map((t) => {
            const entry = d.tasks.find((e) => e.task_id === t.id)
            return (
              <li key={t.id} className="flex items-start gap-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{t.title}</p>
                  <p className="label-mono text-[11px] text-muted-foreground">{t.site_name}</p>
                  {entry?.note && <p className="mt-0.5 text-xs text-muted-foreground">{entry.note}</p>}
                </div>
                <span className="label-mono pt-0.5 text-xs tabular-nums text-muted-foreground">
                  {entry?.hours !== null && entry?.hours !== undefined ? formatHours(entry.hours) : ''}
                </span>
                <EntryChip entry={entry} />
              </li>
            )
          })}
        </ul>
      )}

      {d.extra_work.length > 0 && (
        <div className="space-y-1">
          <p className="label-mono text-xs text-muted-foreground uppercase">Mimo plánu</p>
          {d.extra_work.map((x, i) => (
            <div key={i} className="flex justify-between gap-3 text-sm">
              <span>{x.description}</span>
              <span className="tabular-nums text-muted-foreground">{formatHours(x.hours)}</span>
            </div>
          ))}
        </div>
      )}

      {d.problems.length > 0 && (
        <div className="space-y-1">
          {d.problems.map((p, i) => (
            <p key={i} className="flex items-start gap-2 rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-700 dark:text-rose-300">
              <HugeiconsIcon icon={Alert02Icon} className="mt-0.5 size-4 shrink-0" />
              {p}
            </p>
          ))}
        </div>
      )}
      {d.problems_answered && d.problems.length === 0 && (
        <p className="text-sm text-muted-foreground">Bez problémov.</p>
      )}

      {report.transcript && (
        <div>
          <button
            type="button"
            onClick={() => setShowTranscript((v) => !v)}
            className="text-xs text-muted-foreground underline-offset-2 hover:text-primary hover:underline"
          >
            {showTranscript ? 'Skryť pôvodný výpis' : 'Zobraziť pôvodný výpis (prepis)'}
          </button>
          {showTranscript && (
            <p className="mt-2 rounded-md bg-muted/60 p-3 text-xs whitespace-pre-wrap text-foreground/80">
              {report.transcript}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
