import { Button } from '@/components/ui/button'
import { WorkerAvatar } from '@/components/wf/bits'
import { fetchLoginWorkers, pinLogin, type WorkerPublic } from '@/lib/workforce'
import { cn } from '@/lib/utils'
import { ArrowLeft01Icon, Delete02Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

const LAST_WORKER_KEY = 'pin-last-worker'

function lastWorker(): string | null {
  try {
    return localStorage.getItem(LAST_WORKER_KEY)
  } catch {
    return null
  }
}

/** Prihlásenie pracovníka na mobile: ťukne na svoje meno, zadá PIN na veľkej klávesnici. */
export function PinLogin() {
  const queryClient = useQueryClient()
  const workers = useQuery({ queryKey: ['login-workers'], queryFn: fetchLoginWorkers })
  const [selected, setSelected] = useState<WorkerPublic | null>(null)
  const [pin, setPin] = useState('')
  const remembered = lastWorker()

  const login = useMutation({
    mutationFn: (code: string) => pinLogin(selected!.id, code),
    onSuccess: (w) => {
      try {
        localStorage.setItem(LAST_WORKER_KEY, w.id)
      } catch {
        // bez úložiska sa len nebude pamätať posledné meno
      }
      void queryClient.resetQueries()
    },
    onError: () => setPin(''),
  })

  const press = (digit: string) => {
    if (login.isPending) return
    const next = (pin + digit).slice(0, 6)
    setPin(next)
    if (next.length === 6) login.mutate(next)
  }

  if (workers.isPending) return <p className="py-6 text-center text-sm text-muted-foreground">Načítavam…</p>
  if (!workers.data?.length)
    return <p className="py-6 text-center text-sm text-muted-foreground">Zatiaľ nie sú pridaní žiadni pracovníci.</p>

  if (!selected) {
    const sorted = [...workers.data].sort((a, b) => Number(b.id === remembered) - Number(a.id === remembered))
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">Kto si?</p>
        <div className="grid max-h-80 gap-2 overflow-y-auto">
          {sorted.map((w) => (
            <button
              key={w.id}
              type="button"
              onClick={() => {
                setSelected(w)
                setPin('')
                login.reset()
              }}
              className={cn(
                'flex items-center gap-3 rounded-lg border px-3 py-3 text-left transition-colors hover:border-primary/50 hover:bg-primary/5',
                w.id === remembered && 'border-primary/40',
              )}
            >
              <WorkerAvatar name={w.name} size="lg" />
              <span className="font-medium">{w.name}</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => setSelected(null)}
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
      >
        <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" /> iné meno
      </button>
      <div className="flex items-center gap-3">
        <WorkerAvatar name={selected.name} size="lg" />
        <p className="font-medium">{selected.name}</p>
      </div>
      <div className="flex justify-center gap-2" aria-label={`Zadaných ${pin.length} číslic`}>
        {Array.from({ length: Math.max(4, pin.length) }).map((_, i) => (
          <span
            key={i}
            className={cn('size-3 rounded-full border', i < pin.length ? 'border-primary bg-primary' : 'border-input')}
          />
        ))}
      </div>
      {login.isError && (
        <p className="text-center text-sm text-destructive" role="alert">
          {login.error.message}
        </p>
      )}
      <div className="grid grid-cols-3 gap-2">
        {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((d) => (
          <Button key={d} type="button" variant="outline" className="h-14 text-xl tabular-nums" onClick={() => press(d)}>
            {d}
          </Button>
        ))}
        <Button
          type="button"
          variant="ghost"
          className="h-14"
          onClick={() => setPin(pin.slice(0, -1))}
          aria-label="Zmazať číslicu"
        >
          <HugeiconsIcon icon={Delete02Icon} className="size-5" />
        </Button>
        <Button type="button" variant="outline" className="h-14 text-xl" onClick={() => press('0')}>
          0
        </Button>
        <Button
          type="button"
          className="h-14"
          disabled={pin.length < 4 || login.isPending}
          onClick={() => login.mutate(pin)}
        >
          OK
        </Button>
      </div>
    </div>
  )
}
