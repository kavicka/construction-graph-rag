import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState, PageHeader } from '@/components/wf/bits'
import { DOCUMENT_ACCEPT, fetchDocuments, uploadDocuments, type DocumentOut } from '@/lib/api'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import { File02Icon, Upload04Icon } from '@hugeicons/core-free-icons'
import { useRef } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

const STATUS_LABEL: Record<DocumentOut['status'], string> = {
  queued: 'v rade',
  parsing: 'čítam',
  tables: 'tabuľky',
  context: 'kontext úsekov',
  embedding: 'indexujem',
  graph: 'graf',
  done: 'hotovo',
  error: 'chyba',
}

export function StatusBadge({ doc }: { doc: DocumentOut }) {
  const variant =
    doc.status === 'error' ? 'destructive' : doc.status === 'done' ? 'secondary' : 'outline'
  return <Badge variant={variant}>{STATUS_LABEL[doc.status]}</Badge>
}

const pagesLabel = (n: number) => `${n} ${n === 1 ? 'strana' : n >= 2 && n <= 4 ? 'strany' : 'strán'}`

function DocumentRow({ doc }: { doc: DocumentOut }) {
  const active = doc.status !== 'done' && doc.status !== 'error'
  return (
    <li>
      <Link
        to={`/documents/${doc.id}`}
        className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-muted/50"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium transition-colors group-hover:text-primary">
            {doc.filename}
          </p>
          <p className="label-mono text-2xs text-muted-foreground">
            {doc.format.toUpperCase()}
            {doc.pages_count != null && ` · ${pagesLabel(doc.pages_count)}`}
            {doc.source_path && ' · inbox'}
            {doc.error && <span className="text-destructive"> · {doc.error}</span>}
          </p>
          {active && <Progress value={doc.stage_progress * 100} className="mt-1.5 h-1" />}
        </div>
        <StatusBadge doc={doc} />
      </Link>
    </li>
  )
}

export default function DocumentsPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const { data: docs, isPending, error, refetch } = useQuery({
    queryKey: ['documents'],
    queryFn: fetchDocuments,
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status !== 'done' && d.status !== 'error') ? 2000 : false,
  })

  const upload = useMutation({
    mutationFn: uploadDocuments,
    onSuccess: (created) => {
      toast.success(`Nahraté: ${created.length}, indexujem`)
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    meta: { errorTitle: 'Nahrávanie zlyhalo' },
  })
  const pick = () => inputRef.current?.click()

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Dokumenty"
        description="PDF, Office, HTML a Markdown. Každý dokument sa rozloží na úseky, tabuľky a uzly grafu."
        actions={
          <Button onClick={pick} disabled={upload.isPending} className="gap-2">
            <HugeiconsIcon icon={Upload04Icon} className="size-4" />
            Nahrať dokumenty
          </Button>
        }
      />
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

      {isPending && <Skeleton className="h-24 w-full" />}
      {error && <ErrorState error={error} title="Dokumenty sa nepodarilo načítať" onRetry={() => void refetch()} />}
      {docs?.length === 0 && (
        <EmptyState
          icon={File02Icon}
          title="Zatiaľ žiadne dokumenty"
          description={
            <>
              Nahraj PDF, DOCX, PPTX, XLSX, HTML alebo Markdown, alebo ich nakopíruj do priečinka{' '}
              <code>data/inbox</code>.
            </>
          }
          action={<Button onClick={pick}>Nahrať dokumenty</Button>}
        />
      )}
      {!!docs?.length && (
        <ul className="divide-y rounded-lg border">
          {docs.map((d) => (
            <DocumentRow key={d.id} doc={d} />
          ))}
        </ul>
      )}
    </div>
  )
}
