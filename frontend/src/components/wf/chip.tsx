import type { ChipVariant } from '@/lib/chip'
import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

/** Status tints come from the theme's status tokens (index.css), AA in both themes. */
const TINT: Record<ChipVariant, string> = {
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/12 text-warning',
  danger: 'bg-danger/10 text-danger',
  info: 'bg-info/10 text-info',
  primary: 'bg-primary/10 text-primary',
  neutral: 'bg-muted text-muted-foreground',
}

const DOT: Record<ChipVariant, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  primary: 'bg-primary',
  neutral: 'bg-muted-foreground',
}

/** Tinted chip (status) or outlined dot chip (category, e.g. a site). */
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
