import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { EmptyState, PageHeader } from '@/components/wf/bits'
import { Column, TaskCard } from '@/components/tasks/board-column'
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
} from '@/lib/workforce'
import {
  DndContext,
  DragOverlay,
  KeyboardCode,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  type Announcements,
  type CollisionDetection,
  type KeyboardCoordinateGetter,
  type ScreenReaderInstructions,
  TouchSensor,
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
  FileImportIcon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

const UNASSIGNED = 'unassigned'

/** target = the column under the pointer; outside columns (the margin) by card overlap */
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args)
  return hits.length ? hits : rectIntersection(args)
}
const ALL = '__all__'

/**
 * Keyboard drag: ←/→ jump the card to the neighbouring column (columns sit side by side and
 * have no order inside), so the overlap-based collision lands it in that column.
 */
const columnCoordinates: KeyboardCoordinateGetter = (event, { context, currentCoordinates }) => {
  const { active, over, collisionRect, droppableRects, droppableContainers } = context
  const step = event.code === KeyboardCode.Right ? 1 : event.code === KeyboardCode.Left ? -1 : 0
  if (!step || !active || !collisionRect) return undefined
  event.preventDefault()
  const columns = droppableContainers
    .getEnabled()
    .map((c) => ({ id: c.id, rect: droppableRects.get(c.id) }))
    .filter((c): c is { id: typeof c.id; rect: NonNullable<typeof c.rect> } => !!c.rect)
    .sort((a, b) => a.rect.left - b.rect.left)
  const from = (active.data.current as { from: string } | undefined)?.from
  const currentId = over?.id ?? from
  const index = columns.findIndex((c) => c.id === currentId)
  const target = columns[index + step]
  if (!target) return currentCoordinates
  return {
    x: target.rect.left + (target.rect.width - collisionRect.width) / 2,
    y: Math.max(target.rect.top + 48, currentCoordinates.y),
  }
}

const screenReaderInstructions: ScreenReaderInstructions = {
  draggable:
    'Enter otvorí detail úlohy. Medzerníkom úlohu zdvihneš, šípkami doľava a doprava ju presunieš ' +
    'k inému pracovníkovi, medzerníkom alebo Enterom ju položíš, Escape presun zruší. ' +
    'S podržaným Alt pracovníka pridáš, nie presunieš.',
}
type Period = { kind: 'week'; start: string } | { kind: 'all' }

/** Board: columns = workers (+ Unassigned). Dragging moves the assignment, ⌥/Alt adds one. */
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
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(tasksKey, ctx.prev)
    },
    meta: { errorTitle: 'Presun sa nepodaril' },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: columnCoordinates,
      keyboardCodes: {
        start: [KeyboardCode.Space],
        cancel: [KeyboardCode.Esc],
        end: [KeyboardCode.Space, KeyboardCode.Enter],
      },
    }),
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

  const columnName = (id: string | number | undefined) =>
    id === UNASSIGNED ? 'Nepriradené' : id ? (nameOf.get(String(id)) ?? 'stĺpec') : undefined
  const taskTitle = (data: unknown) => (data as { task?: Task } | undefined)?.task?.title ?? 'Úloha'
  const announcements: Announcements = {
    onDragStart: ({ active }) => `Zdvihnutá úloha ${taskTitle(active.data.current)}.`,
    onDragOver: ({ active, over }) =>
      over
        ? `Úloha ${taskTitle(active.data.current)} je nad stĺpcom ${columnName(over.id)}.`
        : `Úloha ${taskTitle(active.data.current)} nie je nad žiadnym stĺpcom.`,
    onDragEnd: ({ active, over }) =>
      over
        ? `Úloha ${taskTitle(active.data.current)} položená do stĺpca ${columnName(over.id)}.`
        : `Úloha ${taskTitle(active.data.current)} položená mimo stĺpcov, bez zmeny.`,
    onDragCancel: ({ active }) => `Presun úlohy ${taskTitle(active.data.current)} zrušený.`,
  }

  function onDragStart(e: DragStartEvent) {
    const data = e.active.data.current as { task: Task; from: string }
    const ev = e.activatorEvent as PointerEvent | KeyboardEvent | undefined
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
    <div className="space-y-6">
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
        <DndContext
          sensors={sensors}
          collisionDetection={collision}
          accessibility={{ announcements, screenReaderInstructions }}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onDragCancel={() => setDragging(null)}
        >
          {/* columns share the full width (min 9.5rem); the board scrolls only when they no longer fit */}
          <div className="-mx-4 overflow-x-auto px-4 pb-4 md:-mx-8 md:px-8">
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
          Pridaj pracovníkov v sekcii <Link to="/team" className="text-primary hover:underline">Tím a stavby</Link>. Každý dostane vlastný stĺpec.
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
