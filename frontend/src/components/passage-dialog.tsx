import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchChunk } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import { Download04Icon } from '@hugeicons/core-free-icons'

/** Cited passage for formats without pages (DOCX, PPTX, HTML, MD) — PDFs go through PdfDialog. */
export function PassageDialog({
  chunkId,
  onClose,
}: {
  chunkId: string | null
  onClose: () => void
}) {
  const { data, isPending, isError } = useQuery({
    queryKey: ['chunk', chunkId],
    queryFn: () => fetchChunk(chunkId!),
    enabled: chunkId !== null,
  })

  return (
    <Dialog open={chunkId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85svh] overflow-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 pr-6 text-sm">
            <span className="truncate">{data?.filename ?? 'Citovaná pasáž'}</span>
            {data && <Badge variant="outline">{data.format.toUpperCase()}</Badge>}
          </DialogTitle>
          {data?.section_path && <DialogDescription>{data.section_path}</DialogDescription>}
        </DialogHeader>
        {isPending && <Skeleton className="h-32 w-full" />}
        {isError && <p className="text-sm text-destructive">Pasáž sa nepodarilo načítať.</p>}
        {data && (
          <>
            <blockquote className="border-l-2 border-primary/60 bg-muted/40 px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">
              {data.text}
            </blockquote>
            <div className="flex justify-end">
              <Button variant="outline" size="sm" asChild>
                <a href={`/api/documents/${data.doc_id}/file`} download={data.filename}>
                  <HugeiconsIcon icon={Download04Icon} className="size-4" /> Originál
                </a>
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
