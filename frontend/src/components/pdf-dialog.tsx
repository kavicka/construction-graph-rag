import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  ArrowLeft01Icon,
  ArrowRight01Icon,
  Download04Icon,
} from '@hugeicons/core-free-icons'
import { useCallback, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

export interface PdfTarget {
  /** nahratý dokument — PDF ide z /api/documents/{docId}/pdf */
  docId?: string
  /** alebo ľubovoľná URL (vygenerovaný dokument, blob náhľadu) */
  url?: string
  /** ak je zadaná, v hlavičke je tlačidlo Stiahnuť */
  downloadUrl?: string
  filename: string
  page: number
  /** [strana, x0, y0, x1, y1] — 0..1, počiatok vľavo hore; zvýraznenie citovaného miesta */
  highlights?: number[][]
}

const pdfUrl = (t: PdfTarget) => t.url ?? `/api/documents/${t.docId}/pdf`
const MAX_PAGE_WIDTH = 680

export function PdfDialog({
  target,
  onClose,
}: {
  target: PdfTarget | null
  onClose: () => void
}) {
  const [page, setPage] = useState(1)
  const [numPages, setNumPages] = useState<number>()
  const [pageWidth, setPageWidth] = useState(MAX_PAGE_WIDTH)
  const observer = useRef<ResizeObserver | null>(null)

  // nový cieľ (iná citácia) → skoč na jeho stranu; úprava stavu počas renderu namiesto
  // efektu (odporúčaný React vzor „adjusting state when a prop changes“)
  const [shownTarget, setShownTarget] = useState(target)
  if (target !== shownTarget) {
    setShownTarget(target)
    if (target) setPage(target.page)
  }

  // šírka stránky podľa kontajnera — na úzkom okne PDF nepretečie
  const measure = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    if (!el) return
    observer.current = new ResizeObserver(([entry]) =>
      setPageWidth(Math.min(MAX_PAGE_WIDTH, Math.floor(entry.contentRect.width) - 2)),
    )
    observer.current.observe(el)
  }, [])

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
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" />
              </Button>
              str. {page}
              {numPages ? ` / ${numPages}` : ''}
              <Button
                variant="ghost"
                size="icon"
                disabled={numPages !== undefined && page >= numPages}
                onClick={() => setPage((p) => p + 1)}
              >
                <HugeiconsIcon icon={ArrowRight01Icon} className="size-4" />
              </Button>
            </span>
          </DialogTitle>
        </DialogHeader>
        {target && (
          <div ref={measure} className="flex min-w-0 justify-center rounded-md border bg-white">
            <Document
              file={pdfUrl(target)}
              onLoadSuccess={(doc) => setNumPages(doc.numPages)}
              loading={<p className="p-8 text-sm text-muted-foreground">Načítavam PDF…</p>}
              error={<p className="p-8 text-sm text-destructive">PDF sa nepodarilo načítať.</p>}
            >
              <div className="relative">
                <Page
                  pageNumber={page}
                  width={pageWidth}
                  renderTextLayer={false}
                  renderAnnotationLayer={false}
                />
                {target.highlights
                  ?.filter(([p]) => p === page)
                  .map(([, x0, y0, x1, y1], i) => (
                    <span
                      key={i}
                      data-testid="pdf-highlight"
                      className="pointer-events-none absolute rounded-[2px] bg-yellow-300/35 ring-1 ring-yellow-500/70 mix-blend-multiply"
                      style={{
                        left: `${x0 * 100}%`,
                        top: `${y0 * 100}%`,
                        width: `${(x1 - x0) * 100}%`,
                        height: `${(y1 - y0) * 100}%`,
                      }}
                    />
                  ))}
              </div>
            </Document>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
