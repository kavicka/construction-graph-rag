import { cn } from '@/lib/utils'

/**
 * "Bricks" logo: a wall of document chunks; the orange brick is the chunk RAG pulled for the answer.
 * Grey bricks take the text colour (currentColor), so the logo works in light and dark themes.
 * The same shape is in favicon.svg and in the PDF header (docgen / workforce templates).
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 26 16" aria-hidden="true" className={cn('h-4 w-auto shrink-0', className)}>
      <rect x="0" y="0" width="12" height="7" rx="1.5" fill="currentColor" />
      <rect x="14" y="0" width="12" height="7" rx="1.5" fill="currentColor" />
      <rect x="0" y="9" width="5" height="7" rx="1.5" fill="currentColor" />
      <rect x="7" y="9" width="12" height="7" rx="1.5" className="fill-primary" />
      <rect x="21" y="9" width="5" height="7" rx="1.5" fill="currentColor" />
    </svg>
  )
}
