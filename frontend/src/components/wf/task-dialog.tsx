import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { WorkerAvatar } from '@/components/wf/bits'
import { EntryChip } from '@/components/wf/report-summary'
import {
  createTask,
  deleteTask,
  fetchTask,
  formatDay,
  formatHours,
  STATUS_LABEL,
  updateTask,
  type Site,
  type Task,
  type TaskInput,
  type TaskStatus,
  type Worker,
} from '@/lib/workforce'
import { Delete02Icon, FileAttachmentIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

const NONE = '__none__'

function emptyInput(siteId: string, assignee?: string): TaskInput {
  return {
    site_id: siteId,
    title: '',
    description: '',
    work_type: null,
    date_from: null,
    date_to: null,
    depends_on: null,
    status: 'todo',
    progress: 0,
    assignees: assignee ? [assignee] : [],
  }
}

function fromTask(t: Task): TaskInput {
  return {
    site_id: t.site_id,
    title: t.title,
    description: t.description,
    work_type: t.work_type,
    date_from: t.date_from,
    date_to: t.date_to,
    depends_on: t.depends_on,
    status: t.status,
    progress: t.progress,
    assignees: t.assignees,
  }
}

export interface TaskDialogState {
  task?: Task
  siteId?: string
  assignee?: string
}

export function TaskDialog({
  state,
  onClose,
  sites,
  workers,
  professions,
  tasks,
}: {
  state: TaskDialogState | null
  onClose: () => void
  sites: Site[]
  workers: Worker[]
  professions: string[]
  tasks: Task[]
}) {
  return (
    <Dialog open={state !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        {state && (
          <TaskForm
            key={state.task?.id ?? 'new'}
            state={state}
            onClose={onClose}
            sites={sites}
            workers={workers}
            professions={professions}
            tasks={tasks}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function TaskForm({
  state,
  onClose,
  sites,
  workers,
  professions,
  tasks,
}: {
  state: TaskDialogState
  onClose: () => void
  sites: Site[]
  workers: Worker[]
  professions: string[]
  tasks: Task[]
}) {
  const queryClient = useQueryClient()
  const existing = state.task
  const [form, setForm] = useState<TaskInput>(() =>
    existing ? fromTask(existing) : emptyInput(state.siteId ?? sites[0]?.id ?? '', state.assignee),
  )
  const [pdf, setPdf] = useState<PdfTarget | null>(null)
  const detail = useQuery({
    queryKey: ['task', existing?.id],
    queryFn: () => fetchTask(existing!.id),
    enabled: !!existing,
  })
  const set = <K extends keyof TaskInput>(k: K, v: TaskInput[K]) => setForm((f) => ({ ...f, [k]: v }))
  const done = () => {
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    onClose()
  }
  const save = useMutation({
    mutationFn: () => (existing ? updateTask(existing.id, form) : createTask(form)),
    onSuccess: () => {
      toast.success(existing ? 'Úloha uložená' : 'Úloha vytvorená')
      done()
    },
    onError: (e) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: () => deleteTask(existing!.id),
    onSuccess: () => {
      toast.success('Úloha zmazaná')
      done()
    },
    onError: (e) => toast.error(e.message),
  })
  const depOptions = tasks.filter((t) => t.site_id === form.site_id && t.id !== existing?.id)
  const activeWorkers = workers.filter((w) => w.active || form.assignees.includes(w.id))

  return (
    <>
      <DialogHeader>
        <DialogTitle>{existing ? 'Úloha' : 'Nová úloha'}</DialogTitle>
        <DialogDescription>
          {existing?.source_filename ? `Z dokumentu ${existing.source_filename}` : 'Stavba, termín a kto ju robí.'}
        </DialogDescription>
      </DialogHeader>
      <form
        id="task-form"
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="t-title">Názov</Label>
          <Input id="t-title" value={form.title} onChange={(e) => set('title', e.target.value)} required autoFocus={!existing} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Stavba</Label>
            <Select value={form.site_id} onValueChange={(v) => setForm((f) => ({ ...f, site_id: v, depends_on: null }))}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Vyber stavbu" />
              </SelectTrigger>
              <SelectContent>
                {sites.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Typ práce</Label>
            <Select value={form.work_type ?? NONE} onValueChange={(v) => set('work_type', v === NONE ? null : v)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>—</SelectItem>
                {[...new Set([...professions, ...(form.work_type ? [form.work_type] : [])])].map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="t-from">Začiatok</Label>
            <Input id="t-from" type="date" value={form.date_from ?? ''} onChange={(e) => set('date_from', e.target.value || null)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="t-to">Termín</Label>
            <Input id="t-to" type="date" value={form.date_to ?? ''} min={form.date_from ?? undefined} onChange={(e) => set('date_to', e.target.value || null)} />
          </div>
          <div className="space-y-1.5">
            <Label>Čaká na</Label>
            <Select value={form.depends_on ?? NONE} onValueChange={(v) => set('depends_on', v === NONE ? null : v)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>nič — môže začať hneď</SelectItem>
                {depOptions.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Stav</Label>
            <div className="flex gap-2">
              <Select value={form.status} onValueChange={(v) => set('status', v as TaskStatus)}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(STATUS_LABEL) as TaskStatus[]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {STATUS_LABEL[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.status === 'in_progress' && (
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={form.progress}
                  onChange={(e) => set('progress', Math.max(0, Math.min(100, Number(e.target.value))))}
                  className="w-20 text-right tabular-nums"
                  aria-label="Percento hotovosti"
                />
              )}
            </div>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label>Pracovníci</Label>
          <div className="flex flex-wrap gap-2">
            {activeWorkers.map((w) => {
              const checked = form.assignees.includes(w.id)
              return (
                <label
                  key={w.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors has-[[data-state=checked]]:border-primary/50 has-[[data-state=checked]]:bg-primary/5"
                >
                  <Checkbox
                    checked={checked}
                    onCheckedChange={(c) =>
                      set('assignees', c ? [...form.assignees, w.id] : form.assignees.filter((x) => x !== w.id))
                    }
                  />
                  <WorkerAvatar name={w.name} size="sm" />
                  {w.name}
                </label>
              )
            })}
            {activeWorkers.length === 0 && <p className="text-sm text-muted-foreground">Zatiaľ žiadni pracovníci.</p>}
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="t-desc">Popis</Label>
          <Textarea id="t-desc" value={form.description} onChange={(e) => set('description', e.target.value)} rows={3} />
        </div>
      </form>

      {existing?.source_quote && (
        <div className="space-y-2 rounded-md bg-muted/60 p-3">
          <p className="label-mono text-[11px] text-muted-foreground uppercase">
            Zdroj{existing.source_page ? ` · str. ${existing.source_page}` : ''}
          </p>
          <p className="text-sm italic">„{existing.source_quote}“</p>
          {existing.source_doc_id && existing.source_filename?.toLowerCase().endsWith('.pdf') && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={() =>
                setPdf({ docId: existing.source_doc_id!, filename: existing.source_filename!, page: existing.source_page ?? 1 })
              }
            >
              <HugeiconsIcon icon={FileAttachmentIcon} className="size-4" />
              Otvoriť dokument
            </Button>
          )}
        </div>
      )}

      {detail.data && detail.data.history.length > 0 && (
        <div className="space-y-2">
          <p className="label-mono text-[11px] text-muted-foreground uppercase">História z denných výpisov</p>
          <ol className="relative space-y-3 border-l pl-4">
            {detail.data.history.map((h) => (
              <li key={`${h.report_id}`} className="relative">
                <span className="absolute top-1.5 -left-[21px] size-2.5 rounded-full border-2 border-background bg-primary" />
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="label-mono text-xs text-muted-foreground">{formatDay(h.day, false)}</span>
                  <span className="font-medium">{h.worker_name}</span>
                  <EntryChip entry={h.entry} />
                  {h.entry.hours !== null && <span className="text-xs tabular-nums text-muted-foreground">{formatHours(h.entry.hours)}</span>}
                </div>
                {h.entry.note && <p className="text-xs text-muted-foreground">{h.entry.note}</p>}
              </li>
            ))}
          </ol>
        </div>
      )}

      <DialogFooter className="gap-2 sm:justify-between">
        {existing ? (
          <Button
            type="button"
            variant="ghost"
            className="gap-2 text-destructive hover:text-destructive"
            onClick={() => {
              if (confirm(`Zmazať úlohu „${existing.title}“?`)) remove.mutate()
            }}
          >
            <HugeiconsIcon icon={Delete02Icon} className="size-4" />
            Zmazať
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Zrušiť
          </Button>
          <Button type="submit" form="task-form" disabled={save.isPending || !form.title.trim() || !form.site_id}>
            Uložiť
          </Button>
        </div>
      </DialogFooter>
      <PdfDialog target={pdf} onClose={() => setPdf(null)} />
    </>
  )
}
