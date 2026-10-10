import { cn } from '@/lib/utils'

/**
 * Logo „Tehly“: múr z kúskov dokumentov, oranžová tehla = kúsok, ktorý RAG vytiahol k odpovedi.
 * Šedé tehly berú farbu textu (currentColor), takže logo funguje vo svetlej aj tmavej téme.
 * Rovnaký tvar je vo favicon.svg a v hlavičke PDF (docgen/workforce šablóny).
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
