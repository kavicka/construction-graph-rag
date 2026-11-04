import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { ArrowLeft01Icon, ArrowRight01Icon, Download04Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { lazy, Suspense, useState } from 'react'

const PdfPage = lazy(() => import('./pdf-page'))

export interface PdfTarget {
  /** uploaded document — the PDF comes from /api/documents/{docId}/pdf */
  docId?: string
  /** or any URL (generated document, preview blob) */
  url?: string
  /** when set, the header shows a Download button */
  downloadUrl?: string
  filename: string
  page: number
  /** [page, x0, y0, x1, y1] — 0..1, origin top-left; marks the cited passage */
  highlights?: number[][]
}

const pdfUrl = (t: PdfTarget) => t.url ?? `/api/documents/${t.docId}/pdf`

export function PdfDialog({ target, onClose }: { target: PdfTarget | null; onClose: () => void }) {
  const [page, setPage] = useState(1)
  const [numPages, setNumPages] = useState<number>()

  // a new target (another citation) jumps to its page; adjusting state during render instead
  // of an effect (React's "adjusting state when a prop changes" pattern)
  const [shownTarget, setShownTarget] = useState(target)
  if (target !== shownTarget) {
    setShownTarget(target)
    if (target) setPage(target.page)
  }

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between gap-4 pr-6 text-sm">
            <span className="truncate">{target?.filename}</span>
            <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
              {target?.downloadUrl && (
                <Button variant="outline" size="sm" asChild className="mr-2 gap-1.5">
                  <a href={target.downloadUrl} download={target.filename}>
                    <HugeiconsIcon icon={Download04Icon} className="size-3.5" /> Stiahnuť
                  </a>
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Predošlá strana"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" />
              </Button>
              <span aria-live="polite" className="tabular-nums">
                str. {page}
                {numPages ? ` / ${numPages}` : ''}
              </span>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Ďalšia strana"
                disabled={numPages !== undefined && page >= numPages}
                onClick={() => setPage((p) => p + 1)}
              >
                <HugeiconsIcon icon={ArrowRight01Icon} className="size-4" />
              </Button>
            </span>
          </DialogTitle>
          <DialogDescription className="sr-only">Náhľad PDF dokumentu so zvýraznenou citáciou</DialogDescription>
        </DialogHeader>
        {target && (
          <Suspense fallback={<Skeleton className="aspect-[210/297] w-full" />}>
            <PdfPage url={pdfUrl(target)} page={page} highlights={target.highlights} onLoaded={setNumPages} />
          </Suspense>
        )}
      </DialogContent>
    </Dialog>
  )
}
