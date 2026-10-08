import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { variantFor } from '@/lib/chip'
import { fetchMyBoard, formatShort, isoToday, type Task } from '@/lib/workforce'
import { cn } from '@/lib/utils'
import { CheckListIcon, Link01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useQuery } from '@tanstack/react-query'

const COLUMNS: { key: 'todo' | 'in_progress' | 'done'; title: string }[] = [
  { key: 'todo', title: 'Na rade' },
  { key: 'in_progress', title: 'Rozpracované' },
  { key: 'done', title: 'Hotové' },
]

/** Pracovníkova nástenka v štýle Trello — len na čítanie, stav mení denný výpis. */
export default function MyTasksPage() {
  const { data, isPending } = useQuery({ queryKey: ['my-board'], queryFn: fetchMyBoard })
  if (isPending) return <Skeleton className="h-64" />
  if (!data?.tasks.length)
    return (
      <EmptyState
        icon={CheckListIcon}
        title="Zatiaľ nemáš priradené úlohy"
        description="Úlohy ti prideľuje majiteľ. Keď nejaké dostaneš, uvidíš ich tu aj s termínmi."
      />
    )
  const today = new Set(data.today_ids)
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Stav úloh sa mení podľa tvojho denného výpisu. Úlohy označené <span className="text-primary">dnes</span> sa ťa AI
        opýta, ak ich vo výpise nespomenieš.
      </p>
      {/* na mobile sa stĺpce posúvajú do strany so zachytením ako v Trello */}
      <div className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-3">
        {COLUMNS.map((col) => {
          const tasks = data.tasks.filter((t) => t.status === col.key)
          return (
            <section key={col.key} className="w-[82vw] max-w-72 shrink-0 snap-start rounded-lg border bg-muted/40 sm:w-72">
              <header className="flex items-center justify-between border-b px-3 py-2.5">
                <p className="text-sm font-medium">{col.title}</p>
                <span className="label-mono text-xs tabular-nums text-muted-foreground">{tasks.length}</span>
              </header>
              <div className="flex min-h-20 flex-col gap-2 p-2">
                {tasks.map((t) => (
                  <MyCard key={t.id} task={t} today={today.has(t.id)} />
                ))}
                {!tasks.length && <p className="px-2 py-5 text-center text-xs text-muted-foreground">Nič</p>}
              </div>
            </section>
          )
        })}
      </div>
    </div>
  )
}

function MyCard({ task, today }: { task: Task; today: boolean }) {
  const overdue = task.status !== 'done' && task.date_to !== null && task.date_to < isoToday()
  return (
    <article className={cn('space-y-2 rounded-md border bg-background p-3', today && 'border-primary/40')}>
      <div className="flex items-start gap-2">
        <p className="flex-1 text-sm leading-snug font-medium">{task.title}</p>
        {today && <Chip variant="primary">dnes</Chip>}
      </div>
      {task.description && <p className="line-clamp-2 text-xs text-muted-foreground">{task.description}</p>}
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip dot variant={variantFor(task.site_id)}>
          {task.site_name}
        </Chip>
        {overdue && <Chip variant="danger">po termíne</Chip>}
        {task.blocked && (
          <Chip variant="warning" icon={<HugeiconsIcon icon={Link01Icon} className="size-3" />}>
            čaká na: {task.depends_on_title}
          </Chip>
        )}
      </div>
      <p className="label-mono text-[11px] text-muted-foreground">
        {task.date_from || task.date_to ? `${formatShort(task.date_from)} – ${formatShort(task.date_to)}` : 'bez termínu'}
      </p>
      {task.status === 'in_progress' && (
        <div className="flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary/70" style={{ width: `${task.progress}%` }} />
          </div>
          <span className="label-mono text-[11px] tabular-nums text-muted-foreground">{task.progress} %</span>
        </div>
      )}
    </article>
  )
}
