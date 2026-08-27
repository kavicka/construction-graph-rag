import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

export interface PdfTarget {
  docId: string
  filename: string
  page: number
}

export function PdfDialog({
  target,
  onClose,
}: {
  target: PdfTarget | null
  onClose: () => void
}) {
  const [page, setPage] = useState(1)
  const [numPages, setNumPages] = useState<number>()

  useEffect(() => {
    if (target) setPage(target.page)
  }, [target])

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90svh] max-w-3xl overflow-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between gap-4 pr-6 text-sm">
            <span className="truncate">{target?.filename}</span>
            <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
              <Button
                variant="ghost"
                size="icon"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                <ChevronLeft className="size-4" />
              </Button>
              str. {page}
              {numPages ? ` / ${numPages}` : ''}
              <Button
                variant="ghost"
                size="icon"
                disabled={numPages !== undefined && page >= numPages}
                onClick={() => setPage((p) => p + 1)}
              >
                <ChevronRight className="size-4" />
              </Button>
            </span>
          </DialogTitle>
        </DialogHeader>
        {target && (
          <div className="flex justify-center rounded-md border bg-white">
            <Document
              file={`/api/documents/${target.docId}/pdf`}
              onLoadSuccess={(doc) => setNumPages(doc.numPages)}
              loading={<p className="p-8 text-sm text-muted-foreground">Načítavam PDF…</p>}
              error={<p className="p-8 text-sm text-red-600">PDF sa nepodarilo načítať.</p>}
            >
              <Page
                pageNumber={page}
                width={680}
                renderTextLayer={false}
                renderAnnotationLayer={false}
              />
            </Document>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
