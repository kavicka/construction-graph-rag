import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { WorkerAvatar } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { variantFor } from '@/lib/chip'
import { cn } from '@/lib/utils'
import { formatShort, isoToday, type Task, type Worker } from '@/lib/workforce'
import { useDraggable, useDroppable } from '@dnd-kit/core'
import { Add01Icon, CheckListIcon, FileAttachmentIcon, Link01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'

/** One worker's column (or Unassigned): a drop target with draggable task cards. */
export function Column({
  id,
  title,
  worker,
  tasks,
  nameOf,
  onOpen,
  onAdd,
}: {
  id: string
  title: string
  worker?: Worker
  tasks: Task[]
  nameOf: Map<string, string>
  onOpen: (t: Task) => void
  onAdd: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id })
  return (
    <section
      ref={setNodeRef}
      className={cn(
        'flex min-w-[9.5rem] flex-1 basis-[9.5rem] flex-col rounded-lg border bg-muted/40 transition-colors',
        isOver && 'border-primary/60 bg-primary/5',
      )}
      aria-label={`Stĺpec ${title}`}
    >
      <header className="flex items-center gap-2 border-b px-3 py-2.5">
        {worker ? (
          <WorkerAvatar name={worker.name} />
        ) : (
          <span className="flex size-7 items-center justify-center rounded-full border border-dashed text-muted-foreground">
            <HugeiconsIcon icon={CheckListIcon} className="size-3.5" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium" title={title}>{title}</p>
          {worker && worker.professions.length > 0 && (
            <p className="label-mono truncate text-2xs text-muted-foreground">{worker.professions.join(', ')}</p>
          )}
        </div>
        <span className="label-mono text-xs tabular-nums text-muted-foreground">{tasks.length}</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" onClick={onAdd} aria-label={`Nová úloha: ${title}`}>
              <HugeiconsIcon icon={Add01Icon} className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Nová úloha{worker ? ` pre ${worker.name}` : ''}</TooltipContent>
        </Tooltip>
      </header>
      <div className="flex min-h-24 flex-col gap-2 p-2">
        {tasks.map((t) => (
          <DraggableCard key={t.id} task={t} from={id} nameOf={nameOf} onOpen={onOpen} />
        ))}
        {tasks.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">
            {worker ? 'Žiadne úlohy v tomto období' : 'Všetko je priradené'}
          </p>
        )}
      </div>
    </section>
  )
}

function DraggableCard({
  task,
  from,
  nameOf,
  onOpen,
}: {
  task: Task
  from: string
  nameOf: Map<string, string>
  onOpen: (t: Task) => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `${from}:${task.id}`,
    data: { task, from },
  })
  // the wrapper is the single tab stop: Space picks the card up (dnd-kit), Enter opens it
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      aria-roledescription="presúvateľná úloha"
      aria-label={`${task.title}, ${task.site_name}`}
      onKeyDown={(e) => {
        listeners?.onKeyDown?.(e)
        if (e.key === 'Enter' && !isDragging && !e.defaultPrevented) onOpen(task)
      }}
      className={cn(
        'touch-manipulation rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none',
        isDragging && 'opacity-40',
      )}
    >
      <TaskCard task={task} from={from} nameOf={nameOf} onOpen={() => onOpen(task)} />
    </div>
  )
}

function statusChip(task: Task) {
  if (task.status === 'done') return <Chip variant="success">Hotová</Chip>
  if (task.status === 'cancelled') return <Chip variant="neutral">Zrušená</Chip>
  if (task.status === 'in_progress') return <Chip variant="info">{task.progress} %</Chip>
  if (task.date_to && task.date_to < isoToday()) return <Chip variant="danger">Po termíne</Chip>
  return null
}

export function TaskCard({
  task,
  from,
  nameOf,
  onOpen,
  overlay,
}: {
  task: Task
  from: string
  nameOf: Map<string, string>
  onOpen?: () => void
  overlay?: boolean
}) {
  const others = task.assignees.filter((a) => a !== from)
  return (
    <article
      onClick={onOpen}
      className={cn(
        'group cursor-grab space-y-2 rounded-md border bg-background p-3 text-left transition-colors duration-150 hover:border-foreground/20 active:cursor-grabbing',
        overlay && 'rotate-1 border-primary/50',
        task.status === 'done' && 'opacity-60',
      )}
    >
      <div className="flex items-start gap-2">
        <p className="flex-1 text-sm leading-snug font-medium transition-colors group-hover:text-primary">{task.title}</p>
        {task.source_id && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-muted-foreground">
                <HugeiconsIcon icon={FileAttachmentIcon} className="size-3.5" />
              </span>
            </TooltipTrigger>
            <TooltipContent>Z dokumentu {task.source_filename}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip dot variant={variantFor(task.site_id)}>
          {task.site_name}
        </Chip>
        {statusChip(task)}
        {task.blocked && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Chip variant="warning" icon={<HugeiconsIcon icon={Link01Icon} className="size-3" />}>
                  čaká
                </Chip>
              </span>
            </TooltipTrigger>
            <TooltipContent>Začne po dokončení: {task.depends_on_title}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="label-mono text-2xs text-muted-foreground">
          {task.date_from || task.date_to ? `${formatShort(task.date_from)} – ${formatShort(task.date_to)}` : 'bez termínu'}
        </span>
        {others.length > 0 && (
          <span className="flex -space-x-1.5">
            {others.map((a) => (
              <WorkerAvatar key={a} name={nameOf.get(a) ?? '?'} size="sm" className="ring-2 ring-background" />
            ))}
          </span>
        )}
      </div>
      {task.status === 'in_progress' && (
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary/70" style={{ width: `${task.progress}%` }} />
        </div>
      )}
    </article>
  )
}
