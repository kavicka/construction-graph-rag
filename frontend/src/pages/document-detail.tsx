import { TableDialog } from '@/components/table-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchDocument } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ExternalLink, Table2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { StatusBadge } from './documents'

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [openTable, setOpenTable] = useState<string | null>(null)
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
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <h1 className="min-w-0 flex-1 truncate text-lg font-semibold">{doc.filename}</h1>
        <StatusBadge doc={doc} />
        <Button variant="outline" size="sm" asChild>
          <a href={`/api/documents/${doc.id}/pdf`} target="_blank" rel="noreferrer">
            <ExternalLink className="size-4" /> PDF
          </a>
        </Button>
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
            <span className="text-red-600 dark:text-red-400">{doc.error}</span>
          )}
        </CardContent>
      </Card>

      {doc.tables.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Table2 className="size-4" /> Extrahované tabuľky
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
