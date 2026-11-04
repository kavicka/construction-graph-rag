import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { useCallback, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const MAX_PAGE_WIDTH = 680

/**
 * One PDF page with optional highlight boxes. Lives in its own module so react-pdf and pdf.js
 * load only when a dialog actually opens a PDF.
 */
export default function PdfPage({
  url,
  page,
  highlights,
  onLoaded,
}: {
  url: string
  page: number
  /** [page, x0, y0, x1, y1] — 0..1, origin top-left; marks the cited passage */
  highlights?: number[][]
  onLoaded: (numPages: number) => void
}) {
  const [pageWidth, setPageWidth] = useState(MAX_PAGE_WIDTH)
  const observer = useRef<ResizeObserver | null>(null)

  // page width follows the container, so the PDF never overflows a narrow window
  const measure = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    if (!el) return
    observer.current = new ResizeObserver(([entry]) =>
      setPageWidth(Math.min(MAX_PAGE_WIDTH, Math.floor(entry.contentRect.width) - 2)),
    )
    observer.current.observe(el)
  }, [])

  return (
    <div ref={measure} className="flex min-w-0 justify-center rounded-md border bg-white">
      <Document
        file={url}
        onLoadSuccess={(doc) => onLoaded(doc.numPages)}
        loading={<p className="p-8 text-sm text-muted-foreground">Načítavam PDF…</p>}
        error={<p className="p-8 text-sm text-destructive">PDF sa nepodarilo načítať.</p>}
      >
        <div className="relative">
          <Page pageNumber={page} width={pageWidth} renderTextLayer={false} renderAnnotationLayer={false} />
          {highlights
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
  )
}
