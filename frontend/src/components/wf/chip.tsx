import type { ChipVariant } from '@/lib/chip'
import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

/** Kategorické farby čipov — explicitné triedy, téma má len jeden akcent. */
const TINT: Record<ChipVariant, string> = {
  success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400',
  warning: 'bg-amber-500/12 text-amber-700 dark:text-amber-400',
  danger: 'bg-rose-500/10 text-rose-700 dark:text-rose-400',
  info: 'bg-sky-500/10 text-sky-700 dark:text-sky-400',
  primary: 'bg-primary/10 text-primary',
  neutral: 'bg-muted text-muted-foreground',
}

const DOT: Record<ChipVariant, string> = {
  success: 'bg-emerald-500',
  warning: 'bg-amber-500',
  danger: 'bg-rose-500',
  info: 'bg-sky-500',
  primary: 'bg-primary',
  neutral: 'bg-muted-foreground',
}

/** Tónovaný čip (stav) alebo bodkový čip s rámikom (kategória, napr. stavba). */
export function Chip({
  variant = 'neutral',
  dot = false,
  icon,
  className,
  children,
}: {
  variant?: ChipVariant
  dot?: boolean
  icon?: ReactNode
  className?: string
  children: ReactNode
}) {
  if (dot) {
    return (
      <span
        className={cn(
          'inline-flex max-w-full items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs whitespace-nowrap text-foreground/80',
          className,
        )}
      >
        <span className={cn('size-[7px] shrink-0 rounded-[2px]', DOT[variant])} />
        <span className="truncate">{children}</span>
      </span>
    )
  }
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        TINT[variant],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  )
}
