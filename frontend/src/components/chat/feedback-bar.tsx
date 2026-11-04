import { Button } from '@/components/ui/button'
import { exportMessagePdf, setFeedback, type GeneratedDoc } from '@/lib/api'
import { notifyError } from '@/lib/errors'
import { cn } from '@/lib/utils'
import { Activity01Icon, FileExportIcon, ThumbsDownIcon, ThumbsUpIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

/** Under an assistant answer: rate it, save it as a PDF, open its trace (Priebeh odpovedí). */
export function FeedbackBar({
  messageId,
  traceId,
  rating,
  hasDocument,
  onRated,
  onExported,
}: {
  messageId: string
  traceId?: string | null
  rating?: -1 | 1 | null
  hasDocument: boolean
  onRated: (rating: -1 | 1 | null) => void
  onExported: (doc: GeneratedDoc) => void
}) {
  const [exporting, setExporting] = useState(false)
  const exportPdf = () => {
    setExporting(true)
    exportMessagePdf(messageId)
      .then((doc) => {
        onExported(doc)
        toast.success('Odpoveď uložená ako PDF')
      })
      .catch((e: unknown) => notifyError(e, 'PDF sa nepodarilo vytvoriť'))
      .finally(() => setExporting(false))
  }
  const rate = (value: -1 | 1) => {
    const next = rating === value ? null : value
    setFeedback(messageId, next)
      .then(() => onRated(next))
      .catch((e: unknown) => notifyError(e, 'Hodnotenie sa neuložilo'))
  }
  return (
    <div className="flex flex-wrap items-center gap-0.5 text-muted-foreground">
      <Button
        variant="ghost"
        size="icon"
        className={cn('size-8', rating === 1 && 'text-success')}
        aria-label="Dobrá odpoveď"
        aria-pressed={rating === 1}
        onClick={() => rate(1)}
      >
        <HugeiconsIcon icon={ThumbsUpIcon} className="size-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={cn('size-8', rating === -1 && 'text-danger')}
        aria-label="Zlá odpoveď"
        aria-pressed={rating === -1}
        onClick={() => rate(-1)}
      >
        <HugeiconsIcon icon={ThumbsDownIcon} className="size-3.5" />
      </Button>
      {!hasDocument && (
        <Button variant="ghost" size="sm" className="h-8 gap-1 px-2 text-xs" onClick={exportPdf} disabled={exporting}>
          <HugeiconsIcon icon={FileExportIcon} className="size-3.5" /> Uložiť ako PDF
        </Button>
      )}
      {traceId && (
        <Button variant="ghost" size="sm" className="h-8 gap-1 px-2 text-xs" asChild>
          <Link to={`/traces?id=${traceId}`}>
            <HugeiconsIcon icon={Activity01Icon} className="size-3.5" /> Priebeh
          </Link>
        </Button>
      )}
    </div>
  )
}
