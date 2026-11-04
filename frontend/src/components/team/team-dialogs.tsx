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
import { Switch } from '@/components/ui/switch'
import {
  createSite,
  createWorker,
  fetchProfessions,
  resetWorkerPin,
  updateSite,
  updateWorker,
  type Site,
  type Worker,
} from '@/lib/workforce'
import { cn } from '@/lib/utils'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormDialog } from '@/components/form-dialog'
import { useState } from 'react'
import { toast } from 'sonner'

export function WorkerDialog({ worker, onClose }: { worker: Worker | 'new' | null; onClose: () => void }) {
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

export function PinDialog({ worker, onClose }: { worker: Worker | null; onClose: () => void }) {
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

export function SiteForm({ site, onClose }: { site: Site | 'new'; onClose: () => void }) {
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

export function ProfessionDialog({
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
