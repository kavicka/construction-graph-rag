import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EmptyState, PageHeader, WorkerAvatar } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { variantFor } from '@/lib/chip'
import { TaskDialog, type TaskDialogState } from '@/components/wf/task-dialog'
import {
  addDays,
  fetchProfessions,
  fetchSites,
  fetchTasks,
  fetchWorkers,
  formatShort,
  isoToday,
  setTaskAssignees,
  startOfWeek,
  type Task,
  type Worker,
} from '@/lib/workforce'
import { cn } from '@/lib/utils'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  type CollisionDetection,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import {
  Add01Icon,
  ArrowLeft01Icon,
  ArrowRight01Icon,
  Building03Icon,
  CheckListIcon,
  FileAttachmentIcon,
  FileImportIcon,
  Link01Icon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

const UNASSIGNED = 'unassigned'

/** cieľ = stĺpec pod kurzorom; mimo stĺpcov (okraj) podľa prekryvu karty */
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args)
  return hits.length ? hits : rectIntersection(args)
}
const ALL = '__all__'

type Period = { kind: 'week'; start: string } | { kind: 'all' }

/** Nástenka: stĺpce = pracovníci (+ Nepriradené). Ťahanie presúva priradenie, ⌥/Alt pridá. */
export default function TasksBoardPage() {
  const queryClient = useQueryClient()
  const [siteId, setSiteId] = useState<string>(ALL)
  const [period, setPeriod] = useState<Period>({ kind: 'week', start: startOfWeek(isoToday()) })
  const [showDone, setShowDone] = useState(false)
  const [dialog, setDialog] = useState<TaskDialogState | null>(null)
  const [dragging, setDragging] = useState<{ task: Task; from: string } | null>(null)
  const altHeld = useRef(false)

  useEffect(() => {
    const down = (e: KeyboardEvent) => (altHeld.current = e.altKey)
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', down)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', down)
    }
  }, [])

  const params = {
    site_id: siteId === ALL ? undefined : siteId,
    include_done: showDone,
    ...(period.kind === 'week' ? { date_from: period.start, date_to: addDays(period.start, 6) } : {}),
  }
  const tasksKey = ['tasks', params]
  const tasks = useQuery({ queryKey: tasksKey, queryFn: () => fetchTasks(params) })
  const allTasks = useQuery({ queryKey: ['tasks', 'all'], queryFn: () => fetchTasks({ include_done: true }) })
  const workers = useQuery({ queryKey: ['workers'], queryFn: fetchWorkers })
  const sites = useQuery({ queryKey: ['sites'], queryFn: fetchSites })
  const professions = useQuery({ queryKey: ['professions'], queryFn: fetchProfessions })

  const move = useMutation({
    mutationFn: ({ task, assignees }: { task: Task; assignees: string[] }) => setTaskAssignees(task.id, assignees),
    onMutate: async ({ task, assignees }) => {
      await queryClient.cancelQueries({ queryKey: tasksKey })
      const prev = queryClient.getQueryData<Task[]>(tasksKey)
      queryClient.setQueryData<Task[]>(tasksKey, (old) => old?.map((t) => (t.id === task.id ? { ...t, assignees } : t)))
      return { prev }
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(tasksKey, ctx.prev)
      toast.error(e.message)
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 6 } }),
  )

  const columnWorkers = useMemo(() => (workers.data ?? []).filter((w) => w.active), [workers.data])
  const byColumn = useMemo(() => {
    const map = new Map<string, Task[]>([[UNASSIGNED, []]])
    for (const w of columnWorkers) map.set(w.id, [])
    for (const t of tasks.data ?? []) {
      const cols = t.assignees.filter((a) => map.has(a))
      if (!cols.length) map.get(UNASSIGNED)!.push(t)
      for (const a of cols) map.get(a)!.push(t)
    }
    return map
  }, [tasks.data, columnWorkers])
  const nameOf = useMemo(
    () => new Map((workers.data ?? []).map((w) => [w.id, w.name])),
    [workers.data],
  )

  function onDragStart(e: DragStartEvent) {
    const data = e.active.data.current as { task: Task; from: string }
    const ev = e.activatorEvent as PointerEvent | undefined
    if (ev?.altKey) altHeld.current = true
    setDragging(data)
  }

  function onDragEnd(e: DragEndEvent) {
    const data = e.active.data.current as { task: Task; from: string } | undefined
    setDragging(null)
    const to = e.over?.id as string | undefined
    if (!data || !to || to === data.from) return
    const { task, from } = data
    const adding = to !== UNASSIGNED && (from === UNASSIGNED || altHeld.current)
    let next: string[]
    if (to === UNASSIGNED) next = task.assignees.filter((a) => a !== from)
    else if (adding) next = [...new Set([...task.assignees, to])]
    else next = [...new Set([...task.assignees.filter((a) => a !== from), to])]
    move.mutate({ task, assignees: next })
    const label = to === UNASSIGNED ? 'Nepriradené' : nameOf.get(to)
    toast.success(adding ? `Priradené: ${label}` : `Presunuté: ${label}`)
  }

  const loading = tasks.isPending || workers.isPending || sites.isPending
  const noSites = sites.isSuccess && sites.data.length === 0
  const weekLabel =
    period.kind === 'week' ? `${formatShort(period.start)} – ${formatShort(addDays(period.start, 6))}` : 'Všetky termíny'

  return (
    <div className="space-y-5">
      <PageHeader
        title="Úlohy"
        description="Ťahaj karty medzi pracovníkmi. S podržaným ⌥ / Alt pracovníka pridáš, nie presunieš."
        actions={
          <>
            <Button asChild variant="outline" className="gap-2">
              <Link to="/tasks/import">
                <HugeiconsIcon icon={FileImportIcon} className="size-4" />
                Import z dokumentu
              </Link>
            </Button>
            <Button className="gap-2" disabled={noSites} onClick={() => setDialog({ siteId: siteId === ALL ? undefined : siteId })}>
              <HugeiconsIcon icon={Add01Icon} className="size-4" />
              Nová úloha
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <Select value={siteId} onValueChange={setSiteId}>
          <SelectTrigger className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Všetky stavby</SelectItem>
            {sites.data?.map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center rounded-md border">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Predošlý týždeň"
            disabled={period.kind !== 'week'}
            onClick={() => period.kind === 'week' && setPeriod({ kind: 'week', start: addDays(period.start, -7) })}
          >
            <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" />
          </Button>
          <button
            type="button"
            className="label-mono min-w-36 px-2 text-center text-xs hover:text-primary"
            onClick={() =>
              setPeriod(period.kind === 'week' ? { kind: 'all' } : { kind: 'week', start: startOfWeek(isoToday()) })
            }
            title="Prepnúť týždeň / všetko"
          >
            {weekLabel}
          </button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Ďalší týždeň"
            disabled={period.kind !== 'week'}
            onClick={() => period.kind === 'week' && setPeriod({ kind: 'week', start: addDays(period.start, 7) })}
          >
            <HugeiconsIcon icon={ArrowRight01Icon} className="size-4" />
          </Button>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch checked={showDone} onCheckedChange={setShowDone} />
          Ukázať hotové
        </label>
      </div>

      {loading ? (
        <div className="flex gap-3 overflow-hidden">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-72 w-72 shrink-0" />
          ))}
        </div>
      ) : noSites ? (
        <EmptyState
          icon={Building03Icon}
          title="Najprv pridaj stavbu"
          description="Úlohy patria k stavbám. Stavby a pracovníkov spravuješ v sekcii Tím a stavby."
          action={
            <Button asChild>
              <Link to="/team">Tím a stavby</Link>
            </Button>
          }
        />
      ) : (
        <DndContext sensors={sensors} collisionDetection={collision} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setDragging(null)}>
          {/* stĺpce sa rozdelia na celú šírku (min 9,5 rem); až keď sa nezmestia, nástenka sa posúva */}
          <div className="-mx-4 overflow-x-auto px-4 pb-4 md:-mx-6 md:px-6">
            <div className="flex min-w-full items-stretch gap-2">
              <Column
                id={UNASSIGNED}
                title="Nepriradené"
                tasks={byColumn.get(UNASSIGNED) ?? []}
                nameOf={nameOf}
                onOpen={(t) => setDialog({ task: t })}
                onAdd={() => setDialog({ siteId: siteId === ALL ? undefined : siteId })}
              />
              {columnWorkers.map((w) => (
                <Column
                  key={w.id}
                  id={w.id}
                  worker={w}
                  title={w.name}
                  tasks={byColumn.get(w.id) ?? []}
                  nameOf={nameOf}
                  onOpen={(t) => setDialog({ task: t })}
                  onAdd={() => setDialog({ siteId: siteId === ALL ? undefined : siteId, assignee: w.id })}
                />
              ))}
            </div>
          </div>
          <DragOverlay dropAnimation={null}>
            {dragging && <TaskCard task={dragging.task} from={dragging.from} nameOf={nameOf} overlay />}
          </DragOverlay>
        </DndContext>
      )}

      {columnWorkers.length === 0 && !loading && !noSites && (
        <p className="text-sm text-muted-foreground">
          Pridaj pracovníkov v sekcii <Link to="/team" className="text-primary hover:underline">Tím a stavby</Link> — každý dostane vlastný stĺpec.
        </p>
      )}

      <TaskDialog
        state={dialog}
        onClose={() => setDialog(null)}
        sites={sites.data ?? []}
        workers={workers.data ?? []}
        professions={professions.data ?? []}
        tasks={allTasks.data ?? []}
      />
    </div>
  )
}

function Column({
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
            <p className="label-mono truncate text-[11px] text-muted-foreground">{worker.professions.join(', ')}</p>
          )}
        </div>
        <span className="label-mono text-xs tabular-nums text-muted-foreground">{tasks.length}</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7" onClick={onAdd} aria-label={`Nová úloha — ${title}`}>
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
  return (
    <div ref={setNodeRef} {...attributes} {...listeners} className={cn('touch-manipulation', isDragging && 'opacity-40')}>
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

function TaskCard({
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
      onKeyDown={(e) => e.key === 'Enter' && onOpen?.()}
      tabIndex={0}
      className={cn(
        'group cursor-grab space-y-2 rounded-md border bg-background p-3 text-left transition-colors duration-150 hover:border-foreground/20 focus-visible:border-primary focus-visible:outline-none active:cursor-grabbing',
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
        <span className="label-mono text-[11px] text-muted-foreground">
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
