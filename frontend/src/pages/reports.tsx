import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EmptyState, ErrorState, PageHeader, WorkerAvatar } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import type { ChipVariant } from '@/lib/chip'
import { ReportSummary } from '@/components/wf/report-summary'
import {
  addDays,
  fetchDay,
  fetchReport,
  formatDay,
  formatHours,
  isoToday,
  ownerEditReport,
  summaryPdfUrl,
  type DayRow,
  type EntryStatus,
  type ReportData,
} from '@/lib/workforce'
import { cn } from '@/lib/utils'
import {
  Alert02Icon,
  ArrowLeft01Icon,
  ArrowRight01Icon,
  Edit02Icon,
  Pdf01Icon,
  TaskDaily01Icon,
  UserIcon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

const ROW_STATUS: Record<DayRow['status'], { label: string; variant: ChipVariant }> = {
  missing: { label: 'Chýba', variant: 'danger' },
  draft: { label: 'Nezačatý', variant: 'danger' },
  processing: { label: 'Spracúva sa', variant: 'info' },
  questioning: { label: 'Odpovedá na otázky', variant: 'warning' },
  ready: { label: 'Neodovzdaný', variant: 'warning' },
  submitted: { label: 'Odovzdaný', variant: 'success' },
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border px-4 py-3">
      <p className="label-mono text-2xs text-muted-foreground uppercase">{label}</p>
      <p className="text-2xl tabular-nums">{value}</p>
      {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export default function ReportsPage() {
  const [day, setDay] = useState(isoToday())
  const [openReport, setOpenReport] = useState<string | null>(null)
  const overview = useQuery({
    queryKey: ['day', day],
    queryFn: () => fetchDay(day),
    refetchInterval: 15000,
  })
  const s = overview.data?.summary

  return (
    <div className="space-y-6">
      <PageHeader
        title="Denné výpisy"
        description="Kto čo robil, koľko hodín a čo brzdí prácu. Súhrnné PDF sa vytvára automaticky každý večer."
        actions={
          <Button asChild className="gap-2" disabled={!overview.data}>
            <a href={summaryPdfUrl(day)} target="_blank" rel="noreferrer">
              <HugeiconsIcon icon={Pdf01Icon} className="size-4" />
              Súhrnné PDF
            </a>
          </Button>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center rounded-md border">
          <Button variant="ghost" size="icon" aria-label="Predošlý deň" onClick={() => setDay(addDays(day, -1))}>
            <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" />
          </Button>
          <span className="label-mono min-w-44 px-2 text-center text-xs">{formatDay(day)}</span>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Ďalší deň"
            disabled={day >= isoToday()}
            onClick={() => setDay(addDays(day, 1))}
          >
            <HugeiconsIcon icon={ArrowRight01Icon} className="size-4" />
          </Button>
        </div>
        <Input type="date" value={day} max={isoToday()} onChange={(e) => setDay(e.target.value || isoToday())} className="w-40" />
        {day !== isoToday() && (
          <Button variant="ghost" size="sm" onClick={() => setDay(isoToday())}>
            Dnes
          </Button>
        )}
      </div>

      {overview.isPending ? (
        <Skeleton className="h-64" />
      ) : overview.error ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : overview.data.rows.length === 0 ? (
        <EmptyState icon={UserIcon} title="Žiadni pracovníci" description="Pridaj pracovníkov v sekcii Tím a stavby." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="Odovzdané" value={`${s!.submitted_count} / ${s!.worker_count}`} hint={s!.missing.length ? `chýba: ${s!.missing.join(', ')}` : 'všetci odovzdali'} />
            <Kpi label="Odpracované" value={formatHours(s!.total_hours)} />
            <Kpi label="Dokončené úlohy" value={String(s!.done_count)} />
            <Kpi label="Problémy" value={String(s!.problems)} />
          </div>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="border-b bg-muted/40 text-left">
                <tr className="label-mono text-2xs text-muted-foreground uppercase">
                  <th className="px-3 py-2 font-normal">Pracovník</th>
                  <th className="px-3 py-2 font-normal">Výpis</th>
                  <th className="px-3 py-2 font-normal">Začiatok</th>
                  <th className="px-3 py-2 font-normal">Koniec</th>
                  <th className="px-3 py-2 text-right font-normal">Hodiny</th>
                  <th className="px-3 py-2 text-right font-normal">Problémy</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y">
                {overview.data.rows.map((r) => {
                  const st = ROW_STATUS[r.status]
                  return (
                    <tr
                      key={r.worker_id}
                      className={cn('group transition-colors hover:bg-muted/50', r.report_id && 'cursor-pointer')}
                      onClick={() => r.report_id && setOpenReport(r.report_id)}
                    >
                      <td className="px-3 py-2.5">
                        <span className="flex items-center gap-2">
                          <WorkerAvatar name={r.worker_name} />
                          <span className="font-medium transition-colors group-hover:text-primary">{r.worker_name}</span>
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        {r.absent ? (
                          <Chip variant="neutral">neprítomný</Chip>
                        ) : (
                          <Chip variant={st.variant}>
                            {st.label}
                            {r.status === 'questioning' && r.open_questions ? ` (${r.open_questions})` : ''}
                          </Chip>
                        )}
                      </td>
                      <td className="label-mono px-3 py-2.5 text-xs">{r.arrival ?? '—'}</td>
                      <td className="label-mono px-3 py-2.5 text-xs">{r.departure ?? '—'}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">{formatHours(r.worked_hours)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums">
                        {r.problems > 0 ? (
                          <span className="inline-flex items-center gap-1 text-danger">
                            <HugeiconsIcon icon={Alert02Icon} className="size-3.5" />
                            {r.problems}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">0</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {r.pdf_url && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button asChild variant="ghost" size="icon" className="size-8" onClick={(e) => e.stopPropagation()}>
                                <a href={r.pdf_url} target="_blank" rel="noreferrer" aria-label="PDF výpisu">
                                  <HugeiconsIcon icon={Pdf01Icon} className="size-4" />
                                </a>
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>PDF výpisu</TooltipContent>
                          </Tooltip>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <ReportSheet reportId={openReport} onClose={() => setOpenReport(null)} day={day} />
    </div>
  )
}

function ReportSheet({ reportId, onClose, day }: { reportId: string | null; onClose: () => void; day: string }) {
  const report = useQuery({ queryKey: ['report', reportId], queryFn: () => fetchReport(reportId!), enabled: !!reportId })
  const [editing, setEditing] = useState(false)
  return (
    <Sheet
      open={reportId !== null}
      onOpenChange={(o) => {
        if (!o) {
          setEditing(false)
          onClose()
        }
      }}
    >
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{report.data?.worker_name ?? 'Výpis'}</SheetTitle>
          <SheetDescription>
            {report.data ? formatDay(report.data.day) : ''}
            {report.data?.edited_by_owner ? ' · upravené majiteľom' : ''}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-4 px-4 pb-6">
          {!report.data ? (
            <Skeleton className="h-48" />
          ) : editing ? (
            <ReportEditor key={report.data.id} reportId={report.data.id} data={report.data.data} tasks={report.data.tasks} day={day} onDone={() => setEditing(false)} />
          ) : (
            <>
              <div className="flex flex-wrap gap-2">
                {report.data.pdf_url && (
                  <Button asChild variant="outline" size="sm" className="gap-2">
                    <a href={report.data.pdf_url} target="_blank" rel="noreferrer">
                      <HugeiconsIcon icon={Pdf01Icon} className="size-4" /> PDF
                    </a>
                  </Button>
                )}
                {report.data.status !== 'processing' && (
                  <Button variant="outline" size="sm" className="gap-2" onClick={() => setEditing(true)}>
                    <HugeiconsIcon icon={Edit02Icon} className="size-4" /> Opraviť
                  </Button>
                )}
              </div>
              <ReportSummary report={report.data} />
              {report.data.questions.some((q) => q.status === 'answered') && (
                <div className="space-y-2">
                  <p className="label-mono text-2xs text-muted-foreground uppercase">Doplnené na otázky AI</p>
                  {report.data.questions
                    .filter((q) => q.status === 'answered')
                    .map((q) => (
                      <div key={q.id} className="rounded-md bg-muted/50 p-2 text-sm">
                        <p className="text-muted-foreground">{q.text}</p>
                        <p>{q.answer}</p>
                      </div>
                    ))}
                </div>
              )}
              {report.data.inputs.some((i) => i.has_audio) && (
                <div className="space-y-2">
                  <p className="label-mono text-2xs text-muted-foreground uppercase">Nahrávky</p>
                  {report.data.inputs
                    .filter((i) => i.has_audio)
                    .map((i) => (
                      <div key={i.id} className="space-y-1">
                        <p className="text-xs text-muted-foreground">
                          <span className="label-mono uppercase">{i.question_id ? 'odpoveď' : 'hlavný výpis'}</span>
                          {i.text && <span> · „{i.text.length > 90 ? `${i.text.slice(0, 90)}…` : i.text}“</span>}
                        </p>
                        <audio controls preload="none" src={`/api/attendance/inputs/${i.id}/audio`} className="h-9 w-full" />
                      </div>
                    ))}
                </div>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function ReportEditor({
  reportId,
  data,
  tasks,
  day,
  onDone,
}: {
  reportId: string
  data: ReportData
  tasks: { id: string; title: string; site_name: string }[]
  day: string
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ReportData>(() => structuredClone(data))
  const [problems, setProblems] = useState(data.problems.join('\n'))
  const save = useMutation({
    mutationFn: () =>
      ownerEditReport(reportId, {
        ...form,
        problems: problems.split('\n').map((p) => p.trim()).filter(Boolean),
        problems_answered: true,
      }),
    onSuccess: (r) => {
      queryClient.setQueryData(['report', reportId], r)
      void queryClient.invalidateQueries({ queryKey: ['day', day] })
      toast.success(r.status === 'submitted' ? 'Uložené, PDF pregenerované' : 'Uložené')
      onDone()
    },
  })
  const entry = (id: string) => form.tasks.find((e) => e.task_id === id)
  const setEntry = (id: string, patch: Partial<ReportData['tasks'][number]>) =>
    setForm((f) => {
      const exists = f.tasks.some((e) => e.task_id === id)
      const base = { task_id: id, status: null, progress: null, hours: null, note: '' }
      return {
        ...f,
        tasks: exists ? f.tasks.map((e) => (e.task_id === id ? { ...e, ...patch } : e)) : [...f.tasks, { ...base, ...patch }],
      }
    })
  const num = (v: string) => (v === '' ? null : Number(v.replace(',', '.')))

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <div className="grid grid-cols-3 gap-2">
        <label className="space-y-1">
          <span className="label-mono text-2xs text-muted-foreground uppercase">Začiatok</span>
          <Input type="time" value={form.arrival ?? ''} onChange={(e) => setForm({ ...form, arrival: e.target.value || null })} />
        </label>
        <label className="space-y-1">
          <span className="label-mono text-2xs text-muted-foreground uppercase">Koniec</span>
          <Input type="time" value={form.departure ?? ''} onChange={(e) => setForm({ ...form, departure: e.target.value || null })} />
        </label>
        <label className="space-y-1">
          <span className="label-mono text-2xs text-muted-foreground uppercase">Prestávka min</span>
          <Input
            inputMode="numeric"
            value={form.break_minutes ?? ''}
            onChange={(e) => setForm({ ...form, break_minutes: e.target.value === '' ? null : Number(e.target.value) })}
            className="text-right tabular-nums"
          />
        </label>
      </div>
      {tasks.map((t) => {
        const e = entry(t.id)
        return (
          <div key={t.id} className="space-y-2 rounded-md border p-3">
            <p className="text-sm font-medium">
              {t.title} <span className="label-mono text-2xs text-muted-foreground">{t.site_name}</span>
            </p>
            <div className="flex flex-wrap gap-2">
              <Select value={e?.status ?? ''} onValueChange={(v) => setEntry(t.id, { status: v as EntryStatus, progress: v === 'done' ? 100 : e?.progress ?? null })}>
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="Stav" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="done">hotová</SelectItem>
                  <SelectItem value="in_progress">rozpracovaná</SelectItem>
                  <SelectItem value="not_started">nerobilo sa</SelectItem>
                </SelectContent>
              </Select>
              <label className="flex items-center gap-1 text-sm text-muted-foreground">
                <Input
                  inputMode="decimal"
                  value={e?.hours ?? ''}
                  onChange={(ev) => setEntry(t.id, { hours: num(ev.target.value) })}
                  className="w-20 text-right tabular-nums"
                  aria-label="Hodiny"
                />
                h
              </label>
              {e?.status === 'in_progress' && (
                <label className="flex items-center gap-1 text-sm text-muted-foreground">
                  <Input
                    inputMode="numeric"
                    value={e.progress ?? ''}
                    onChange={(ev) => setEntry(t.id, { progress: num(ev.target.value) })}
                    className="w-16 text-right tabular-nums"
                    aria-label="Percento hotovosti"
                  />
                  %
                </label>
              )}
            </div>
            <Input placeholder="poznámka" value={e?.note ?? ''} onChange={(ev) => setEntry(t.id, { note: ev.target.value })} />
          </div>
        )
      })}
      <label className="block space-y-1">
        <span className="label-mono text-2xs text-muted-foreground uppercase">Problémy (každý na nový riadok)</span>
        <Textarea value={problems} onChange={(e) => setProblems(e.target.value)} rows={3} />
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onDone}>
          Zrušiť
        </Button>
        <Button type="submit" disabled={save.isPending} className="gap-2">
          <HugeiconsIcon icon={TaskDaily01Icon} className="size-4" />
          Uložiť opravu
        </Button>
      </div>
    </form>
  )
}
