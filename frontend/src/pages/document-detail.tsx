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
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { deleteDocument, fetchDocument, reindexDocument } from '@/lib/api'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  ArrowLeft02Icon,
  Delete02Icon,
  LinkSquare02Icon,
  RefreshIcon,
  Table01Icon,
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

  if (isPending) return <Skeleton className="mx-auto h-64 max-w-2xl" />
  if (!doc) return <p className="text-center text-sm text-muted-foreground">Dokument neexistuje.</p>

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild>
          <Link to="/documents">
            <HugeiconsIcon icon={ArrowLeft02Icon} className="size-4" />
          </Link>
        </Button>
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
                {doc.source_path && ' Súbor ostáva v inboxe — zmaž ho aj tam, inak sa vráti.'}
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

      <Card>
        <CardContent className="flex gap-6 py-3 text-sm">
          <span>
            <span className="text-muted-foreground">Strany:</span> {doc.pages_count ?? '—'}
          </span>
          <span>
            <span className="text-muted-foreground">Tabuľky:</span> {doc.tables.length}
          </span>
          {doc.error && (
            <span className="text-destructive">{doc.error}</span>
          )}
        </CardContent>
        <CardContent className="pb-3">
          <AccessEditor docId={doc.id} access={doc.access} />
        </CardContent>
      </Card>

      {doc.tables.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <HugeiconsIcon icon={Table01Icon} className="size-4" /> Extrahované tabuľky
            </CardTitle>
          </CardHeader>
          <CardContent className="divide-y">
            {doc.tables.map((t) => (
              <button
                key={t.id}
                onClick={() => setOpenTable(t.id)}
                className="flex w-full items-center justify-between py-2 text-left text-sm hover:bg-secondary/40"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">
                    {t.caption ?? `Tabuľka (${t.n_rows}×${t.n_cols})`}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {t.n_rows}×{t.n_cols}
                    {t.page_no != null && ` · strana ${t.page_no}`}
                    {t.summary && ` · ${t.summary.slice(0, 80)}…`}
                  </p>
                </div>
                <Badge variant="outline">str. {t.page_no ?? '?'}</Badge>
              </button>
            ))}
          </CardContent>
        </Card>
      )}
      <TableDialog tableId={openTable} onClose={() => setOpenTable(null)} />
    </div>
  )
}
