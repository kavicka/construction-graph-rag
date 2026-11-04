import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchCommunities } from '@/lib/api'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import { AiBrain01Icon } from '@hugeicons/core-free-icons'

/** Library themes = concept communities in the graph; summaries are computed on demand (LLM). */
export function ThemesPanel() {
  const queryClient = useQueryClient()
  const { data, isPending } = useQuery({
    queryKey: ['communities'],
    queryFn: () => fetchCommunities(false),
  })
  const summarize = useMutation({
    mutationFn: () => fetchCommunities(true),
    onSuccess: (out) => queryClient.setQueryData(['communities'], out),
  })
  const missing = data?.some((c) => !c.summary)

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle className="text-base">Témy knižnice</CardTitle>
          <CardDescription>
            Komunity pojmov naprieč dokumentmi (Louvain nad grafom). Na otázky typu „aké sú
            hlavné témy dokumentov?“ odpovedá chat z týchto súhrnov.
          </CardDescription>
        </div>
        {missing && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => summarize.mutate()}
            disabled={summarize.isPending}
          >
            <HugeiconsIcon icon={AiBrain01Icon} className="size-4" />
            {summarize.isPending ? 'Sumarizujem…' : 'Zhrnúť témy'}
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isPending && <Skeleton className="h-32 w-full" />}
        {data?.length === 0 && (
          <p className="text-sm text-muted-foreground">Graf zatiaľ nemá dosť pojmov na témy.</p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {data?.map((c) => (
            <div key={c.key} className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">{c.title ?? c.concepts.slice(0, 3).join(', ')}</p>
              {c.summary ? (
                <p className="text-xs leading-relaxed text-muted-foreground">{c.summary}</p>
              ) : (
                <p className="text-xs text-muted-foreground">{c.concepts.join(' · ')}</p>
              )}
              <div className="flex flex-wrap gap-1">
                {c.documents.slice(0, 4).map((d) => (
                  <Badge key={d.doc_id} variant="outline" className="max-w-44 truncate text-2xs">
                    {d.filename}
                  </Badge>
                ))}
                {c.documents.length > 4 && (
                  <Badge variant="outline" className="text-2xs">
                    +{c.documents.length - 4}
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
