import { AccessEditor } from '@/components/access-editor'
import { TableDialog } from '@/components/table-dialog'
import { Badge } from '@/components/ui/badge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { deleteDocument, fetchDocument, reindexDocument } from '@/lib/api'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  ArrowLeft01Icon,
  Delete02Icon,
  LinkSquare02Icon,
  RefreshIcon,
} from '@hugeicons/core-free-icons'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { StatusBadge } from './documents'

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [openTable, setOpenTable] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const remove = useMutation({
    mutationFn: () => deleteDocument(id!),
    onSuccess: () => {
      toast.success('Dokument zmazaný')
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void navigate('/documents')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const reindex = useMutation({
    mutationFn: () => reindexDocument(id!),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['documents'] }),
  })
  const { data: doc, isPending } = useQuery({
    queryKey: ['documents', id],
    queryFn: () => fetchDocument(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s && s !== 'done' && s !== 'error' ? 2000 : false
    },
  })

  if (isPending) return <Skeleton className="h-64 max-w-3xl" />
  if (!doc) return <p className="text-sm text-muted-foreground">Dokument neexistuje.</p>

  return (
    <div className="max-w-3xl space-y-6">
      <Link to="/documents" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary">
        <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" /> Dokumenty
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 flex-1 truncate font-display-lg text-2xl">{doc.filename}</h1>
        <StatusBadge doc={doc} />
        <Button
          variant="outline"
          size="sm"
          disabled={reindex.isPending || (doc.status !== 'done' && doc.status !== 'error')}
          onClick={() => reindex.mutate()}
        >
          <HugeiconsIcon icon={RefreshIcon} className="size-4" /> Reindex
        </Button>
        <Button variant="outline" size="sm" asChild>
          <a href={`/api/documents/${doc.id}/file`} target="_blank" rel="noreferrer">
            <HugeiconsIcon icon={LinkSquare02Icon} className="size-4" /> {doc.format.toUpperCase()}
          </a>
        </Button>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              aria-label="Zmazať dokument"
              disabled={remove.isPending || (doc.status !== 'done' && doc.status !== 'error')}
            >
              <HugeiconsIcon icon={Delete02Icon} className="size-4" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Zmazať {doc.filename}?</AlertDialogTitle>
              <AlertDialogDescription>
                Dokument zmizne z vyhľadávania, grafu aj tabuliek. Staré odpovede v chate si
                citácie ponechajú, ale PDF sa už neotvorí.
                {doc.source_path && ' Súbor ostáva v inboxe. Zmaž ho aj tam, inak sa vráti.'}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Zrušiť</AlertDialogCancel>
              <AlertDialogAction variant="destructive" onClick={() => remove.mutate()}>
                Zmazať
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="space-y-3">
        <p className="label-mono text-xs text-muted-foreground">
          strany: {doc.pages_count ?? '?'} · tabuľky: {doc.tables.length}
          {doc.error && <span className="text-destructive"> · {doc.error}</span>}
        </p>
        <AccessEditor docId={doc.id} access={doc.access} />
      </div>

      {doc.tables.length > 0 && (
        <section className="space-y-2">
          <h2 className="label-mono text-[11px] text-muted-foreground uppercase">Tabuľky z dokumentu</h2>
          <ul className="divide-y rounded-lg border">
            {doc.tables.map((t) => (
              <li key={t.id}>
                <button
                  onClick={() => setOpenTable(t.id)}
                  className="group flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-muted/50"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium transition-colors group-hover:text-primary">
                      {t.caption ?? `Tabuľka ${t.n_rows}×${t.n_cols}`}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {t.n_rows}×{t.n_cols}
                      {t.summary && ` · ${t.summary.slice(0, 80)}…`}
                    </p>
                  </div>
                  <Badge variant="outline">str. {t.page_no ?? '?'}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <TableDialog tableId={openTable} onClose={() => setOpenTable(null)} />
    </div>
  )
}
