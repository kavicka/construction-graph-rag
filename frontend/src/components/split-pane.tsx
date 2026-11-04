import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

/**
 * Full-height tool layout (chat, traces): a compact header above a list/detail grid that fills
 * the viewport. The height subtracts the <main> padding (py-6 / md:py-8) and, on phones, the
 * 3.5rem mobile bar, so the panes scroll inside instead of the page.
 */
export function SplitPane({
  header,
  aside,
  children,
  className,
  gridClassName,
}: {
  header: ReactNode
  aside: ReactNode
  children: ReactNode
  className?: string
  /** column / row template, e.g. `md:grid-cols-[200px_1fr]` */
  gridClassName?: string
}) {
  return (
    <div className={cn('flex h-[calc(100svh-6.5rem)] flex-col gap-4 md:h-[calc(100svh-4rem)]', className)}>
      {header}
      <div className={cn('grid min-h-0 flex-1 grid-cols-1 gap-6', gridClassName)}>
        {aside}
        {children}
      </div>
    </div>
  )
}
