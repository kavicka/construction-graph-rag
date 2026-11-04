import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { WorkerAvatar } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import type { ChipVariant } from '@/lib/chip'
import { cn } from '@/lib/utils'
import { formatShort, STATUS_LABEL, updateDraft, type Draft, type DraftUpdate, type Worker } from '@/lib/workforce'
import {
  Alert02Icon,
  Cancel01Icon,
  FileAttachmentIcon,
  Link01Icon,
  Tick02Icon,
  UserAdd01Icon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

const KIND: Record<Draft['kind'], { label: string; variant: ChipVariant }> = {
  new: { label: 'Nová', variant: 'primary' },
  changed: { label: 'Zmenená', variant: 'info' },
  cancelled: { label: 'Zrušená', variant: 'danger' },
  unchanged: { label: 'Bez zmeny', variant: 'neutral' },
}
const CONFIDENCE: Record<Draft['data']['confidence'], { label: string; variant: ChipVariant }> = {
  high: { label: 'istota vysoká', variant: 'success' },
  medium: { label: 'istota stredná', variant: 'warning' },
  low: { label: 'treba doplniť', variant: 'danger' },
}
const FIELD_LABEL: Record<string, string> = {
  title: 'Názov',
  description: 'Popis',
  work_type: 'Typ práce',
  date_from: 'Začiatok',
  date_to: 'Termín',
}
const NONE = '__none__'

/** One extracted task proposal: decide, edit fields, see the source quote. */
export function DraftCard({
  sourceId,
  draft,
  drafts,
  workers,
  professions,
  editable,
  onSaved,
  onShowSource,
  canShowPdf,
}: {
  sourceId: string
  draft: Draft
  drafts: Draft[]
  workers: Worker[]
  professions: string[]
  editable: boolean
  onSaved: (d: Draft) => void
  onShowSource: () => void
  canShowPdf: boolean
}) {
  const d = draft.data
  const [form, setForm] = useState<Omit<DraftUpdate, 'decision'>>({
    title: d.title,
    description: d.description ?? '',
    work_type: d.work_type,
    date_from: d.date_from,
    date_to: d.date_to,
    assignees: d.assignees,
  })
  const dirty = JSON.stringify(form) !== JSON.stringify({
    title: d.title,
    description: d.description ?? '',
    work_type: d.work_type,
    date_from: d.date_from,
    date_to: d.date_to,
    assignees: d.assignees,
  })
  const save = useMutation({
    mutationFn: (u: { decision: Draft['decision']; clear_dependency?: boolean }) =>
      updateDraft(sourceId, draft.id, { ...form, ...u }),
    onSuccess: onSaved,
  })
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }))
  const kind = KIND[draft.kind]
  const conf = CONFIDENCE[d.confidence]
  const cancelled = draft.kind === 'cancelled'
  const decided = draft.decision !== 'pending'
  const dep = d.depends_on
  const depTitle = dep ? (dep.draft !== undefined ? drafts[dep.draft]?.data.title : dep.title) : null
  const disabled = !editable || cancelled

  return (
    <article
      className={cn(
        'space-y-3 rounded-lg border p-4 transition-colors',
        draft.decision === 'approved' && 'border-success/40 bg-success/[0.03]',
        draft.decision === 'rejected' && 'opacity-60',
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip variant={kind.variant}>{kind.label}</Chip>
        {!cancelled && <Chip variant={conf.variant}>{conf.label}</Chip>}
        {d.issues.map((i) => (
          <Chip key={i} variant="danger" icon={<HugeiconsIcon icon={Alert02Icon} className="size-3" />}>
            {i}
          </Chip>
        ))}
        {d.task_status && d.task_status !== 'todo' && <Chip variant="neutral">{STATUS_LABEL[d.task_status]}</Chip>}
        <span className="ml-auto flex gap-1">
          {editable && (
            <>
              <Button
                size="sm"
                variant={draft.decision === 'approved' ? 'default' : 'outline'}
                className="gap-1"
                disabled={save.isPending}
                onClick={() => save.mutate({ decision: draft.decision === 'approved' ? 'pending' : 'approved' })}
              >
                <HugeiconsIcon icon={Tick02Icon} className="size-3.5" />
                {cancelled ? 'Zrušiť úlohu' : dirty ? 'Uložiť a schváliť' : 'Schváliť'}
              </Button>
              <Button
                size="sm"
                variant={draft.decision === 'rejected' ? 'secondary' : 'ghost'}
                className="gap-1"
                disabled={save.isPending}
                onClick={() => save.mutate({ decision: draft.decision === 'rejected' ? 'pending' : 'rejected' })}
                aria-label={cancelled ? 'Ponechať úlohu' : 'Zamietnuť návrh'}
              >
                <HugeiconsIcon icon={Cancel01Icon} className="size-3.5" />
                {cancelled ? 'Ponechať' : 'Zamietnuť'}
              </Button>
            </>
          )}
        </span>
      </div>

      {disabled ? (
        <p className="font-medium">{form.title}</p>
      ) : (
        <Input value={form.title} onChange={(e) => set('title', e.target.value)} className="font-medium" aria-label="Názov úlohy" />
      )}

      {!cancelled && (
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1">
            <span className="label-mono text-2xs text-muted-foreground uppercase">Začiatok</span>
            <Input type="date" value={form.date_from ?? ''} disabled={disabled} onChange={(e) => set('date_from', e.target.value || null)} />
          </label>
          <label className="space-y-1">
            <span className="label-mono text-2xs text-muted-foreground uppercase">
              Termín{d.date_text ? ` · „${d.date_text}“` : ''}
            </span>
            <Input type="date" value={form.date_to ?? ''} disabled={disabled} onChange={(e) => set('date_to', e.target.value || null)} />
          </label>
          <div className="space-y-1">
            <span className="label-mono text-2xs text-muted-foreground uppercase">Typ práce</span>
            <Select value={form.work_type ?? NONE} disabled={disabled} onValueChange={(v) => set('work_type', v === NONE ? null : v)}>
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
        </div>
      )}

      {!cancelled && (
        <div className="flex flex-wrap items-center gap-2">
          <AssigneePicker
            workers={workers}
            value={form.assignees}
            disabled={disabled}
            onChange={(v) => set('assignees', v)}
          />
          {d.assign_reason && <span className="label-mono text-2xs text-muted-foreground">{d.assign_reason}</span>}
        </div>
      )}

      {dep && depTitle && (
        <div className="flex items-center gap-2 text-sm">
          <Chip variant="warning" icon={<HugeiconsIcon icon={Link01Icon} className="size-3" />}>
            čaká na
          </Chip>
          <span className="flex-1">{depTitle}</span>
          {!disabled && (
            <Button size="sm" variant="ghost" onClick={() => save.mutate({ decision: draft.decision, clear_dependency: true })}>
              Odstrániť
            </Button>
          )}
        </div>
      )}

      {draft.kind === 'changed' && d.changes && Object.keys(d.changes).length > 0 && (
        <div className="space-y-1 rounded-md bg-info/5 p-3 text-sm">
          {Object.entries(d.changes).map(([field, [from, to]]) => (
            <p key={field}>
              <span className="label-mono text-xs text-muted-foreground">{FIELD_LABEL[field] ?? field}: </span>
              <span className="text-muted-foreground line-through">{field.startsWith('date') ? formatShort(from) : from || '—'}</span>
              {' → '}
              <span className="font-medium">{field.startsWith('date') ? formatShort(to) : to}</span>
            </p>
          ))}
        </div>
      )}

      {!disabled && form.description !== undefined && (d.description || form.description) && (
        <Textarea value={form.description} onChange={(e) => set('description', e.target.value)} rows={2} aria-label="Popis" />
      )}

      {d.quote && (
        <blockquote className="flex items-start gap-2 border-l-2 pl-3 text-sm text-muted-foreground italic">
          <span className="flex-1">„{d.quote}“</span>
          {canShowPdf && d.page && (
            <Button size="sm" variant="ghost" className="shrink-0 gap-1 not-italic" onClick={onShowSource}>
              <HugeiconsIcon icon={FileAttachmentIcon} className="size-3.5" />
              str. {d.page}
            </Button>
          )}
        </blockquote>
      )}
      {decided && dirty && editable && (
        <p className="text-xs text-warning">Máš neuložené zmeny. Klikni znova na Schváliť.</p>
      )}
    </article>
  )
}

function AssigneePicker({
  workers,
  value,
  disabled,
  onChange,
}: {
  workers: Worker[]
  value: string[]
  disabled?: boolean
  onChange: (v: string[]) => void
}) {
  const selected = workers.filter((w) => value.includes(w.id))
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-8 gap-2" disabled={disabled}>
          {selected.length ? (
            <>
              <span className="flex -space-x-1.5">
                {selected.map((w) => (
                  <WorkerAvatar key={w.id} name={w.name} size="sm" className="ring-2 ring-background" />
                ))}
              </span>
              {selected.map((w) => w.name).join(', ')}
            </>
          ) : (
            <>
              <HugeiconsIcon icon={UserAdd01Icon} className="size-4" />
              Nepriradené
            </>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-1" align="start">
        {workers
          .filter((w) => w.active)
          .map((w) => (
            <label key={w.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted">
              <Checkbox
                checked={value.includes(w.id)}
                onCheckedChange={(c) => onChange(c ? [...value, w.id] : value.filter((x) => x !== w.id))}
              />
              <WorkerAvatar name={w.name} size="sm" />
              <span className="flex-1">{w.name}</span>
              <span className="label-mono truncate text-2xs text-muted-foreground">{w.professions.join(', ')}</span>
            </label>
          ))}
      </PopoverContent>
    </Popover>
  )
}
