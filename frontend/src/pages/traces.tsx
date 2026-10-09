import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchTrace, fetchTraces, type TraceSpan, type TraceSummary } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useQuery } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import { ThumbsDownIcon, ThumbsUpIcon } from '@hugeicons/core-free-icons'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms.toFixed(0)} ms`)

/** Farba podľa druhu spanu — LLM volania sú to drahé, retrieval lacné. */
function spanColor(name: string): string {
  if (name.startsWith('chat ') || name.startsWith('generate ')) return 'bg-chart-1'
  if (name.startsWith('retrieve.')) return 'bg-chart-5'
  if (name.startsWith('docgen.')) return 'bg-chart-3'
  if (name === 'rag.query') return 'bg-muted-foreground'
  return 'bg-chart-2'
}

function orderSpans(spans: TraceSpan[]): { span: TraceSpan; depth: number }[] {
  const children = new Map<string | null, TraceSpan[]>()
  const ids = new Set(spans.map((s) => s.span_id))
  for (const s of spans) {
    const parent = s.parent_id && ids.has(s.parent_id) ? s.parent_id : null
    children.set(parent, [...(children.get(parent) ?? []), s])
  }
  const out: { span: TraceSpan; depth: number }[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const s of (children.get(parent) ?? []).sort((a, b) => a.start_ms - b.start_ms)) {
      out.push({ span: s, depth })
      walk(s.span_id, depth + 1)
    }
  }
  walk(null, 0)
  return out
}

function TraceRow({
  trace,
  active,
  onSelect,
}: {
  trace: TraceSummary
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={cn(
        'w-full rounded-md px-2.5 py-2 text-left transition-colors',
        active ? 'bg-secondary' : 'hover:bg-secondary/50',
      )}
    >
      <p className="line-clamp-2 text-xs font-medium">{trace.question ?? trace.name}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        {trace.mode && <Badge variant="outline">{trace.mode}</Badge>}
        <span>{fmtMs(trace.duration_ms)}</span>
        {trace.input_tokens + trace.output_tokens > 0 && (
          <span>{(trace.input_tokens + trace.output_tokens).toLocaleString('sk')} tok</span>
        )}
        {trace.status === 'ERROR' && <Badge variant="destructive">chyba</Badge>}
        {trace.rating === 1 && <HugeiconsIcon icon={ThumbsUpIcon} className="size-3 text-emerald-600" />}
        {trace.rating === -1 && <HugeiconsIcon icon={ThumbsDownIcon} className="size-3 text-destructive" />}
      </div>
    </button>
  )
}

function Waterfall({ traceId }: { traceId: string }) {
  const { data, isPending } = useQuery({
    queryKey: ['trace', traceId],
    queryFn: () => fetchTrace(traceId),
  })
  const [selected, setSelected] = useState<string | null>(null)
  const rows = useMemo(() => (data ? orderSpans(data.spans) : []), [data])

  if (isPending || !data) return <Skeleton className="h-80 w-full" />
  const total = Math.max(data.duration_ms, 1)
  const llm = data.spans.filter((s) => 'gen_ai.request.model' in s.attributes)
  const tokensIn = llm.reduce((a, s) => a + Number(s.attributes['gen_ai.usage.input_tokens'] ?? 0), 0)
  const tokensOut = llm.reduce((a, s) => a + Number(s.attributes['gen_ai.usage.output_tokens'] ?? 0), 0)
  const llmMs = llm.reduce((a, s) => a + s.duration_ms, 0)
  const active = rows.find((r) => r.span.span_id === selected)?.span

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ['Celkom', fmtMs(data.duration_ms)],
          ['LLM volania', `${llm.length} · ${fmtMs(llmMs)}`],
          ['Tokeny vstup', tokensIn.toLocaleString('sk')],
          ['Tokeny výstup', tokensOut.toLocaleString('sk')],
        ].map(([label, value]) => (
          <div key={label} className="rounded-md border px-3 py-2">
            <p className="text-[11px] text-muted-foreground">{label}</p>
            <p className="text-sm font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      <div className="space-y-0.5">
        {rows.map(({ span, depth }) => (
          <button
            key={span.span_id}
            onClick={() => setSelected(span.span_id === selected ? null : span.span_id)}
            className={cn(
              'grid w-full grid-cols-[minmax(0,14rem)_1fr_4.5rem] items-center gap-3 rounded px-1.5 py-1 text-left text-xs hover:bg-secondary/60',
              selected === span.span_id && 'bg-secondary',
            )}
          >
            <span className="truncate" style={{ paddingLeft: depth * 12 }}>
              {span.name}
            </span>
            <span className="relative h-3 rounded-sm bg-muted">
              <span
                className={cn(
                  'absolute inset-y-0 rounded-sm',
                  spanColor(span.name),
                  span.status === 'ERROR' && 'bg-red-500',
                )}
                style={{
                  left: `${(span.start_ms / total) * 100}%`,
                  width: `max(2px, ${(span.duration_ms / total) * 100}%)`,
                }}
              />
            </span>
            <span className="text-right tabular-nums text-muted-foreground">
              {fmtMs(span.duration_ms)}
            </span>
          </button>
        ))}
      </div>

      {active && (
        <div className="rounded-md border bg-muted/30 p-3">
          <p className="mb-2 text-xs font-semibold">{active.name}</p>
          <dl className="grid grid-cols-[minmax(0,12rem)_1fr] gap-x-3 gap-y-1 text-xs">
            {Object.entries(active.attributes).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="truncate font-mono text-muted-foreground">{k}</dt>
                <dd className="break-words whitespace-pre-wrap">
                  {typeof v === 'string' ? v : JSON.stringify(v)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  )
}

export default function TracesPage() {
  const [params, setParams] = useSearchParams()
  const { data: traces, isPending } = useQuery({
    queryKey: ['traces'],
    queryFn: fetchTraces,
    refetchInterval: 5000,
  })
  const selected = params.get('id') ?? traces?.[0]?.trace_id ?? null

  return (
    <div className="grid h-[calc(100svh-6.5rem)] grid-cols-1 gap-6 md:h-[calc(100svh-4rem)] md:grid-cols-[280px_1fr]">
      <aside className="flex min-h-0 flex-col gap-2">
        <h1 className="text-sm font-semibold">Traces</h1>
        <ScrollArea className="min-h-0 flex-1 rounded-md border">
          <div className="space-y-0.5 p-1.5">
            {isPending && <Skeleton className="h-40" />}
            {traces?.length === 0 && (
              <p className="p-4 text-xs text-muted-foreground">
                Zatiaľ žiadne. Každá otázka v chate vytvorí trace.
              </p>
            )}
            {traces?.map((t) => (
              <TraceRow
                key={t.trace_id}
                trace={t}
                active={t.trace_id === selected}
                onSelect={() => setParams({ id: t.trace_id })}
              />
            ))}
          </div>
        </ScrollArea>
      </aside>
      <Card className="min-h-0 overflow-auto">
        <CardHeader>
          <CardTitle className="text-sm">
            {traces?.find((t) => t.trace_id === selected)?.question ?? 'Detail trace'}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {selected ? (
            <Waterfall traceId={selected} />
          ) : (
            <p className="text-sm text-muted-foreground">Vyber trace vľavo.</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
