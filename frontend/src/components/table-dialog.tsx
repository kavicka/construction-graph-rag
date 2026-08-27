import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchTable } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'

export function TableDialog({
  tableId,
  onClose,
}: {
  tableId: string | null
  onClose: () => void
}) {
  const { data: table, isPending } = useQuery({
    queryKey: ['table', tableId],
    queryFn: () => fetchTable(tableId!),
    enabled: !!tableId,
  })

  return (
    <Dialog open={tableId !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85svh] max-w-4xl overflow-hidden">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {table?.caption ?? 'Tabuľka'}
            {table?.page_no != null && (
              <Badge variant="outline" className="ml-2">
                str. {table.page_no}
              </Badge>
            )}
          </DialogTitle>
        </DialogHeader>
        {isPending && <Skeleton className="h-48 w-full" />}
        {table && (
          <div className="space-y-3 overflow-hidden">
            {table.summary && (
              <p className="text-xs text-muted-foreground">{table.summary}</p>
            )}
            <div className="max-h-[55svh] overflow-auto rounded-md border">
              {table.columns.length > 0 ? (
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-secondary">
                    <tr>
                      {table.columns.map((c, i) => (
                        <th key={i} className="whitespace-nowrap px-2 py-1.5 text-left font-medium">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {table.rows.map((row, ri) => (
                      <tr key={ri} className="border-t">
                        {row.map((cell, ci) => (
                          <td key={ci} className="whitespace-nowrap px-2 py-1 font-mono">
                            {cell == null ? '' : String(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <pre className="overflow-auto p-3 text-xs">{table.markdown}</pre>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {table.n_rows}×{table.n_cols}
              {table.truncated && ' · zobrazených prvých 500 riadkov'} · presné hodnoty z parquet
              (nie z LLM)
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
