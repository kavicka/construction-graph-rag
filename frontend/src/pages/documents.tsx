import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { DOCUMENT_ACCEPT, fetchDocuments, uploadDocuments, type DocumentOut } from '@/lib/api'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import { File02Icon, Upload04Icon } from '@hugeicons/core-free-icons'
import { useRef } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

const STATUS_LABEL: Record<DocumentOut['status'], string> = {
  queued: 'v rade',
  parsing: 'parsujem',
  tables: 'tabuľky',
  context: 'kontext chunkov',
  embedding: 'embedujem',
  graph: 'graf',
  done: 'hotovo',
  error: 'chyba',
}

export function StatusBadge({ doc }: { doc: DocumentOut }) {
  const variant =
    doc.status === 'error' ? 'destructive' : doc.status === 'done' ? 'secondary' : 'outline'
  return <Badge variant={variant}>{STATUS_LABEL[doc.status]}</Badge>
}

function DocumentRow({ doc }: { doc: DocumentOut }) {
  const active = doc.status !== 'done' && doc.status !== 'error'
  return (
    <Link to={`/documents/${doc.id}`} className="group block">
      <Card className="transition-colors hover:bg-muted">
        <CardContent className="flex items-center gap-3 py-3">
          <HugeiconsIcon icon={File02Icon} className="size-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium transition-colors group-hover:text-primary">
              {doc.filename}
            </p>
            <p className="label-mono text-muted-foreground">
              {doc.format.toUpperCase()} ·{' '}
              {doc.pages_count != null ? `${doc.pages_count} strán` : '—'}
              {doc.source_path && ' · inbox'}
              {doc.error && <span className="text-destructive"> · {doc.error}</span>}
            </p>
            {active && <Progress value={doc.stage_progress * 100} className="mt-1.5 h-1" />}
          </div>
          <StatusBadge doc={doc} />
        </CardContent>
      </Card>
    </Link>
  )
}

export default function DocumentsPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const { data: docs, isPending } = useQuery({
    queryKey: ['documents'],
    queryFn: fetchDocuments,
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status !== 'done' && d.status !== 'error') ? 2000 : false,
  })

  const upload = useMutation({
    mutationFn: uploadDocuments,
    onSuccess: (created) => {
      toast.success(`Nahraté: ${created.length} — indexujem`)
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    onError: (e) => toast.error(`Upload zlyhal: ${e.message}`),
  })

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="font-display-lg text-2xl">Dokumenty</h1>
        <Button
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
          className="gap-2"
        >
          <HugeiconsIcon icon={Upload04Icon} className="size-4" />
          Nahrať dokumenty
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept={DOCUMENT_ACCEPT}
          multiple
          hidden
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            if (files.length) upload.mutate(files)
            e.target.value = ''
          }}
        />
      </div>

      {isPending && <Skeleton className="h-24 w-full" />}
      {docs?.length === 0 && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          Zatiaľ žiadne dokumenty — nahraj PDF, DOCX, PPTX, XLSX, HTML alebo Markdown, alebo
          ich nakopíruj do priečinka <code>data/inbox</code>.
        </p>
      )}
      <div className="space-y-2">
        {docs?.map((d) => <DocumentRow key={d.id} doc={d} />)}
      </div>
    </div>
  )
}
