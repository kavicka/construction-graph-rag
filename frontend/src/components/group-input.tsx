import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { useState } from 'react'

/** Groups as tags: Enter / comma adds, × removes, existing groups are one-click suggestions. */
export function GroupInput({
  id,
  value,
  onChange,
  suggestions = [],
  allowEveryone = false,
}: {
  id?: string
  value: string[]
  onChange: (groups: string[]) => void
  suggestions?: string[]
  /** "*" = everyone (document access) */
  allowEveryone?: boolean
}) {
  const [draft, setDraft] = useState('')
  const add = (raw: string) => {
    const g = raw.trim()
    if (g && !value.includes(g)) onChange([...value, g])
    setDraft('')
  }
  const offered = [...new Set([...(allowEveryone ? ['*'] : []), ...suggestions])].filter((s) => !value.includes(s))

  return (
    <div className="space-y-2">
      <div className={cn('flex flex-wrap gap-1.5', value.length === 0 && 'hidden')}>
        {value.map((g) => (
          <Badge key={g} variant="secondary" className="gap-1 pr-1">
            {g === '*' ? 'všetci' : g}
            <button
              type="button"
              aria-label={`Odstrániť skupinu ${g}`}
              className="rounded px-1 text-muted-foreground hover:text-destructive"
              onClick={() => onChange(value.filter((x) => x !== g))}
            >
              ×
            </button>
          </Badge>
        ))}
      </div>
      <Input
        id={id}
        value={draft}
        placeholder="názov skupiny + Enter"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            add(draft)
          } else if (e.key === 'Backspace' && !draft && value.length) {
            onChange(value.slice(0, -1))
          }
        }}
        onBlur={() => draft.trim() && add(draft)}
      />
      {offered.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span>Existujúce:</span>
          {offered.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => add(s)}
              className="rounded-full border px-2 py-0.5 hover:bg-secondary hover:text-foreground"
            >
              + {s === '*' ? 'všetci' : s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
