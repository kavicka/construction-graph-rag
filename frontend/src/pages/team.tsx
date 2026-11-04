import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EmptyState, PageHeader, WorkerAvatar } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import {
  deleteSite,
  deleteWorker,
  fetchProfessions,
  fetchSites,
  fetchWorkers,
  saveProfessions,
  type Site,
  type Worker,
} from '@/lib/workforce'
import { cn } from '@/lib/utils'
import {
  Add01Icon,
  Building03Icon,
  Delete02Icon,
  Edit02Icon,
  LockIcon,
  SquareLock02Icon,
  UserGroupIcon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PinDialog, ProfessionDialog, SiteForm, WorkerDialog } from '@/components/team/team-dialogs'
import { useState } from 'react'
import { toast } from 'sonner'

export default function TeamPage() {
  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="Tím a stavby" description="Pracovníci sa prihlasujú z mobilu menom a PIN-om. Profesie slúžia na návrh, kto má robiť importovanú úlohu." />
      <Tabs defaultValue="workers">
        <TabsList>
          <TabsTrigger value="workers">Pracovníci</TabsTrigger>
          <TabsTrigger value="sites">Stavby</TabsTrigger>
          <TabsTrigger value="professions">Profesie</TabsTrigger>
        </TabsList>
        <TabsContent value="workers" className="pt-4">
          <WorkersTab />
        </TabsContent>
        <TabsContent value="sites" className="pt-4">
          <SitesTab />
        </TabsContent>
        <TabsContent value="professions" className="pt-4">
          <ProfessionsTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function RowAction({ label, icon, onClick, danger }: { label: string; icon: typeof Edit02Icon; onClick: () => void; danger?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className={cn('size-8', danger && 'hover:text-destructive')} onClick={onClick} aria-label={label}>
          <HugeiconsIcon icon={icon} className="size-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function WorkersTab() {
  const queryClient = useQueryClient()
  const workers = useQuery({ queryKey: ['workers'], queryFn: fetchWorkers })
  const [editing, setEditing] = useState<Worker | 'new' | null>(null)
  const [pinFor, setPinFor] = useState<Worker | null>(null)
  const remove = useMutation({
    mutationFn: deleteWorker,
    onSuccess: () => {
      toast.success('Pracovník zmazaný')
      void queryClient.invalidateQueries({ queryKey: ['workers'] })
    },
  })

  if (workers.isPending) return <Skeleton className="h-40" />
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button className="gap-2" onClick={() => setEditing('new')}>
          <HugeiconsIcon icon={Add01Icon} className="size-4" /> Pridať pracovníka
        </Button>
      </div>
      {workers.data?.length === 0 ? (
        <EmptyState
          icon={UserGroupIcon}
          title="Zatiaľ žiadni pracovníci"
          description="Pridaj pracovníka, nastav mu PIN a profesie. Na mobile si potom vyberie svoje meno a zadá PIN."
          action={<Button onClick={() => setEditing('new')}>Pridať prvého pracovníka</Button>}
        />
      ) : (
        <ul className="divide-y rounded-lg border">
          {workers.data?.map((w) => (
            <li key={w.id} className={cn('group flex items-center gap-3 px-3 py-2.5', !w.active && 'bg-muted/40 opacity-60')}>
              <WorkerAvatar name={w.name} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{w.name}</p>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {w.professions.map((p) => (
                    <Chip key={p} dot variant="info">
                      {p}
                    </Chip>
                  ))}
                  {!w.professions.length && <span className="label-mono text-2xs text-muted-foreground">bez profesie</span>}
                </div>
              </div>
              {w.locked && (
                <Chip variant="danger" icon={<HugeiconsIcon icon={LockIcon} className="size-3" />}>
                  zamknutý PIN
                </Chip>
              )}
              {!w.active && <Chip variant="neutral">neaktívny</Chip>}
              <div className="flex opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
                <RowAction label="Upraviť" icon={Edit02Icon} onClick={() => setEditing(w)} />
                <RowAction label="Nový PIN" icon={SquareLock02Icon} onClick={() => setPinFor(w)} />
                <RowAction
                  label="Zmazať"
                  icon={Delete02Icon}
                  danger
                  onClick={() => confirm(`Zmazať pracovníka ${w.name}? Zmažú sa aj jeho výpisy. Ak ich chceš zachovať, pracovníka len deaktivuj.`) && remove.mutate(w.id)}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
      <WorkerDialog worker={editing} onClose={() => setEditing(null)} />
      <PinDialog worker={pinFor} onClose={() => setPinFor(null)} />
    </div>
  )
}

function SitesTab() {
  const queryClient = useQueryClient()
  const sites = useQuery({ queryKey: ['sites'], queryFn: fetchSites })
  const [editing, setEditing] = useState<Site | 'new' | null>(null)
  const remove = useMutation({
    mutationFn: deleteSite,
    onSuccess: () => {
      toast.success('Stavba zmazaná')
      void queryClient.invalidateQueries({ queryKey: ['sites'] })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
  if (sites.isPending) return <Skeleton className="h-40" />
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button className="gap-2" onClick={() => setEditing('new')}>
          <HugeiconsIcon icon={Add01Icon} className="size-4" /> Pridať stavbu
        </Button>
      </div>
      {sites.data?.length === 0 ? (
        <EmptyState
          icon={Building03Icon}
          title="Zatiaľ žiadne stavby"
          description="Každá úloha patrí k stavbe. Názov stavby sa použije aj ako slovník pre prepis reči."
          action={<Button onClick={() => setEditing('new')}>Pridať prvú stavbu</Button>}
        />
      ) : (
        <ul className="divide-y rounded-lg border">
          {sites.data?.map((s) => (
            <li key={s.id} className={cn('group flex items-center gap-3 px-3 py-2.5', !s.active && 'bg-muted/40 opacity-60')}>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{s.name}</p>
                {s.address && <p className="label-mono truncate text-2xs text-muted-foreground">{s.address}</p>}
              </div>
              {!s.active && <Chip variant="neutral">ukončená</Chip>}
              <div className="flex opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
                <RowAction label="Upraviť" icon={Edit02Icon} onClick={() => setEditing(s)} />
                <RowAction
                  label="Zmazať"
                  icon={Delete02Icon}
                  danger
                  onClick={() => confirm(`Zmazať stavbu ${s.name} aj všetky jej úlohy?`) && remove.mutate(s.id)}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
      <Dialog open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
          {editing && <SiteForm key={editing === 'new' ? 'new' : editing.id} site={editing} onClose={() => setEditing(null)} />}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function ProfessionsTab() {
  const queryClient = useQueryClient()
  const professions = useQuery({ queryKey: ['professions'], queryFn: fetchProfessions })
  const [adding, setAdding] = useState(false)
  const save = useMutation({
    mutationFn: saveProfessions,
    onSuccess: (names) => queryClient.setQueryData(['professions'], names),
  })
  const list = professions.data ?? []
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Pri importe dokumentu AI zaradí každú úlohu k jednej profesii z tohto zoznamu.
        </p>
        <Button size="sm" onClick={() => setAdding(true)}>
          <HugeiconsIcon icon={Add01Icon} className="size-4" /> Pridať profesiu
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {list.map((p) => (
          <span key={p} className="group inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs">
            {p}
            <button
              type="button"
              aria-label={`Odstrániť ${p}`}
              className="text-muted-foreground hover:text-destructive"
              onClick={() => save.mutate(list.filter((x) => x !== p))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <ProfessionDialog
        key={String(adding)}
        open={adding}
        existing={list}
        saving={save.isPending}
        onClose={() => setAdding(false)}
        onAdd={(name) => save.mutate([...list, name], { onSuccess: () => setAdding(false) })}
      />
    </div>
  )
}

