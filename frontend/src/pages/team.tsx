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
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { EmptyState, PageHeader, WorkerAvatar } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import {
  createSite,
  createWorker,
  deleteSite,
  deleteWorker,
  fetchProfessions,
  fetchSites,
  fetchWorkers,
  resetWorkerPin,
  saveProfessions,
  updateSite,
  updateWorker,
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
import { FormDialog } from '@/components/form-dialog'
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
    onError: (e) => toast.error(e.message),
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
                  {!w.professions.length && <span className="label-mono text-[11px] text-muted-foreground">bez profesie</span>}
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

function WorkerDialog({ worker, onClose }: { worker: Worker | 'new' | null; onClose: () => void }) {
  return (
    <Dialog open={worker !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        {worker && <WorkerForm key={worker === 'new' ? 'new' : worker.id} worker={worker} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  )
}

function WorkerForm({ worker, onClose }: { worker: Worker | 'new'; onClose: () => void }) {
  const queryClient = useQueryClient()
  const isNew = worker === 'new'
  const professions = useQuery({ queryKey: ['professions'], queryFn: fetchProfessions })
  const [name, setName] = useState(isNew ? '' : worker.name)
  const [pin, setPin] = useState('')
  const [selected, setSelected] = useState<string[]>(isNew ? [] : worker.professions)
  const [active, setActive] = useState(isNew ? true : worker.active)
  const save = useMutation({
    mutationFn: () =>
      isNew ? createWorker({ name, pin, professions: selected }) : updateWorker(worker.id, { name, professions: selected, active }),
    onSuccess: () => {
      toast.success(isNew ? `Pracovník ${name} pridaný. PIN mu oznám osobne.` : 'Uložené')
      void queryClient.invalidateQueries({ queryKey: ['workers'] })
      onClose()
    },
    onError: (e) => toast.error(e.message),
  })
  const pinOk = /^\d{4,6}$/.test(pin)
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
      className="space-y-4"
    >
      <DialogHeader>
        <DialogTitle>{isNew ? 'Nový pracovník' : 'Upraviť pracovníka'}</DialogTitle>
        <DialogDescription>Profesie použije AI pri návrhu, kto má robiť úlohu z dokumentu.</DialogDescription>
      </DialogHeader>
      <div className="space-y-1.5">
        <Label htmlFor="w-name">Meno</Label>
        <Input id="w-name" value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
      </div>
      {isNew && (
        <div className="space-y-1.5">
          <Label htmlFor="w-pin">PIN (4–6 číslic)</Label>
          <Input id="w-pin" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" autoComplete="off" className="label-mono w-32 tracking-[0.3em]" />
        </div>
      )}
      <div className="space-y-1.5">
        <Label>Profesie</Label>
        <div className="flex flex-wrap gap-1.5">
          {professions.data?.map((p) => {
            const on = selected.includes(p)
            return (
              <button
                key={p}
                type="button"
                onClick={() => setSelected(on ? selected.filter((x) => x !== p) : [...selected, p])}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-xs transition-colors',
                  on ? 'border-primary bg-primary/10 text-primary' : 'text-muted-foreground hover:border-foreground/30',
                )}
                aria-pressed={on}
              >
                {p}
              </button>
            )
          })}
        </div>
      </div>
      {!isNew && (
        <label className="flex items-center gap-2 text-sm">
          <Switch checked={active} onCheckedChange={setActive} />
          Aktívny (neaktívny sa nevie prihlásiť a nemá stĺpec na nástenke)
        </label>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Zrušiť
        </Button>
        <Button type="submit" disabled={!name.trim() || (isNew && !pinOk) || save.isPending}>
          Uložiť
        </Button>
      </DialogFooter>
    </form>
  )
}

function PinDialog({ worker, onClose }: { worker: Worker | null; onClose: () => void }) {
  const [pin, setPin] = useState('')
  const queryClient = useQueryClient()
  const save = useMutation({
    mutationFn: () => resetWorkerPin(worker!.id, pin),
    onSuccess: () => {
      toast.success('PIN zmenený, zámok zrušený')
      void queryClient.invalidateQueries({ queryKey: ['workers'] })
      setPin('')
      onClose()
    },
    onError: (e) => toast.error(e.message),
  })
  return (
    <Dialog open={worker !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Nový PIN: {worker?.name}</DialogTitle>
          <DialogDescription>Starý PIN prestane platiť, prípadný zámok po zlých pokusoch sa zruší.</DialogDescription>
        </DialogHeader>
        <Input value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" className="label-mono w-32 tracking-[0.3em]" autoFocus />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Zrušiť
          </Button>
          <Button disabled={!/^\d{4,6}$/.test(pin) || save.isPending} onClick={() => save.mutate()}>
            Nastaviť
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
    onError: (e) => toast.error(e.message),
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
                {s.address && <p className="label-mono truncate text-[11px] text-muted-foreground">{s.address}</p>}
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

function SiteForm({ site, onClose }: { site: Site | 'new'; onClose: () => void }) {
  const queryClient = useQueryClient()
  const isNew = site === 'new'
  const [name, setName] = useState(isNew ? '' : site.name)
  const [address, setAddress] = useState(isNew ? '' : site.address)
  const [active, setActive] = useState(isNew ? true : site.active)
  const save = useMutation({
    mutationFn: () => (isNew ? createSite({ name, address }) : updateSite(site.id, { name, address, active })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['sites'] })
      onClose()
    },
    onError: (e) => toast.error(e.message),
  })
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <DialogHeader>
        <DialogTitle>{isNew ? 'Nová stavba' : 'Upraviť stavbu'}</DialogTitle>
      </DialogHeader>
      <div className="space-y-1.5">
        <Label htmlFor="s-name">Názov</Label>
        <Input id="s-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="RD Rovinka" required autoFocus />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="s-addr">Adresa</Label>
        <Input id="s-addr" value={address} onChange={(e) => setAddress(e.target.value)} />
      </div>
      {!isNew && (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={active} onCheckedChange={(c) => setActive(c === true)} />
          Prebieha (ukončená stavba sa neponúka pri importe)
        </label>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Zrušiť
        </Button>
        <Button type="submit" disabled={!name.trim() || save.isPending}>
          Uložiť
        </Button>
      </DialogFooter>
    </form>
  )
}

function ProfessionsTab() {
  const queryClient = useQueryClient()
  const professions = useQuery({ queryKey: ['professions'], queryFn: fetchProfessions })
  const [adding, setAdding] = useState(false)
  const save = useMutation({
    mutationFn: saveProfessions,
    onSuccess: (names) => queryClient.setQueryData(['professions'], names),
    onError: (e) => toast.error(e.message),
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

function ProfessionDialog({
  open,
  existing,
  saving,
  onClose,
  onAdd,
}: {
  open: boolean
  existing: string[]
  saving: boolean
  onClose: () => void
  onAdd: (name: string) => void
}) {
  const [name, setName] = useState('')
  const trimmed = name.trim()
  const duplicate = existing.some((p) => p.toLowerCase() === trimmed.toLowerCase())
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      size="md"
      title="Nová profesia"
      description="Napr. geodet, žeriavnik, elektrikár."
      onSubmit={() => onAdd(trimmed)}
      submitLabel="Pridať"
      submitting={saving}
      submitDisabled={!trimmed || duplicate}
    >
      <div className="space-y-1.5">
        <Label htmlFor="p-name">Názov profesie</Label>
        <Input id="p-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="napr. geodet" autoFocus />
        {duplicate && <p className="text-xs text-destructive">Táto profesia už v zozname je.</p>}
      </div>
    </FormDialog>
  )
}
