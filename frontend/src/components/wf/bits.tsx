import { Button } from '@/components/ui/button'
import { errorMessage } from '@/lib/errors'
import { initials } from '@/lib/workforce'
import { Alert02Icon, RefreshIcon } from '@hugeicons/core-free-icons'
import { cn } from '@/lib/utils'
import { HugeiconsIcon, type IconSvgElement } from '@hugeicons/react'
import type { ReactNode } from 'react'

/** Initials avatar — the tint follows the name so people are told apart at a glance. */
const AVATAR_TONES = [
  'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  'bg-amber-500/20 text-amber-800 dark:text-amber-300',
  'bg-rose-500/15 text-rose-700 dark:text-rose-300',
  'bg-violet-500/15 text-violet-700 dark:text-violet-300',
  'bg-primary/15 text-primary',
]

export function WorkerAvatar({
  name,
  size = 'md',
  className,
}: {
  name: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  let h = 0
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return (
    <span
      title={name}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full font-medium',
        size === 'sm' && 'size-5 text-[0.625rem]',
        size === 'md' && 'size-7 text-xs',
        size === 'lg' && 'size-10 text-sm',
        AVATAR_TONES[h % AVATAR_TONES.length],
        className,
      )}
    >
      {initials(name) || '?'}
    </span>
  )
}

/** Empty state is content: why it is empty and the next step. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon: IconSvgElement
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center',
        className,
      )}
    >
      <span className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <HugeiconsIcon icon={icon} className="size-5" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/** Query failed: say so in Slovak and offer a retry instead of an empty page. */
export function ErrorState({
  error,
  title = 'Údaje sa nepodarilo načítať',
  onRetry,
  className,
}: {
  error: unknown
  title?: string
  onRetry?: () => void
  className?: string
}) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-12 text-center',
        className,
      )}
    >
      <span className="flex size-10 items-center justify-center rounded-lg bg-danger/10 text-danger">
        <HugeiconsIcon icon={Alert02Icon} className="size-5" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="max-w-sm text-sm text-muted-foreground">{errorMessage(error)}</p>
      </div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <HugeiconsIcon icon={RefreshIcon} className="size-4" /> Skúsiť znova
        </Button>
      )}
    </div>
  )
}

/**
 * Page title row. `compact` is for full-height split layouts (chat, traces, graph): a smaller
 * heading that keeps actions on the same line even on phones.
 */
export function PageHeader({
  title,
  description,
  actions,
  compact = false,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  compact?: boolean
}) {
  if (compact) {
    return (
      <div className="flex min-h-9 items-center justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <h1 className="font-display truncate text-xl">{title}</h1>
          {description && <p className="truncate text-xs text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        <h1 className="font-display-lg text-2xl break-words">{title}</h1>
        {description && <div className="text-sm text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
