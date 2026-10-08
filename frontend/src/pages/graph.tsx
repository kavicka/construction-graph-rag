import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { ThemesPanel } from '@/components/themes-panel'
import { Badge } from '@/components/ui/badge'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  fetchDocuments,
  fetchGraph,
  fetchNodeDetail,
  type GraphNode,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

/** Farby uzlov = brand tokeny z index.css (menia sa so svetlou/tmavou témou). */
const TYPE_VARS: Record<GraphNode['type'], string> = {
  document: '--chart-1',
  table: '--chart-4',
  concept: '--chart-2',
  quantity: '--chart-3',
  reference: '--chart-5',
  chunk: '--muted-foreground',
  page: '--input',
}

const TYPE_COLORS = Object.fromEntries(
  Object.entries(TYPE_VARS).map(([t, v]) => [t, `var(${v})`]),
) as Record<GraphNode['type'], string>

const FALLBACK_COLOR = 'var(--muted-foreground)'

function readCanvasColors() {
  const css = getComputedStyle(document.documentElement)
  return Object.fromEntries(
    Object.entries(TYPE_VARS).map(([t, v]) => [t, css.getPropertyValue(v).trim()]),
  ) as Record<GraphNode['type'], string>
}

/** Canvas nepozná CSS premenné — hodnoty sa čítajú z <html> pri každej zmene triedy (téma). */
function useCanvasColors() {
  const [colors, setColors] = useState(readCanvasColors)
  useEffect(() => {
    const observer = new MutationObserver(() => setColors(readCanvasColors()))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])
  return colors
}

const TYPE_LABELS: Record<GraphNode['type'], string> = {
  document: 'dokumenty',
  table: 'tabuľky',
  concept: 'pojmy',
  quantity: 'veličiny',
  reference: 'citácie',
  chunk: 'chunky',
  page: 'strany',
}

const DEFAULT_TYPES: GraphNode['type'][] = [
  'document',
  'table',
  'concept',
  'quantity',
  'reference',
]

export default function GraphPage() {
  const [activeTypes, setActiveTypes] = useState<Set<string>>(new Set(DEFAULT_TYPES))
  const [docFilter, setDocFilter] = useState<string>('')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasColors = useCanvasColors()

  const typesCsv = [...activeTypes].sort().join(',')
  const { data: graph, isPending } = useQuery({
    queryKey: ['graph', typesCsv, docFilter],
    queryFn: () => fetchGraph({ types: typesCsv, doc_id: docFilter || undefined }),
  })
  const { data: docs } = useQuery({ queryKey: ['documents'], queryFn: fetchDocuments })
  const { data: detail } = useQuery({
    queryKey: ['graph-node', selectedNode],
    queryFn: () => fetchNodeDetail(selectedNode!),
    enabled: !!selectedNode,
  })

  const graphData = useMemo(
    () => ({
      nodes: graph?.nodes.map((n) => ({ ...n })) ?? [],
      links: graph?.links.map((l) => ({ ...l })) ?? [],
    }),
    [graph],
  )

  const toggleType = (t: string) => {
    setActiveTypes((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next.size ? next : prev
    })
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {(Object.keys(TYPE_LABELS) as GraphNode['type'][]).map((t) => (
          <button key={t} onClick={() => toggleType(t)}>
            <Badge
              variant={activeTypes.has(t) ? 'secondary' : 'outline'}
              className={cn('gap-1.5', !activeTypes.has(t) && 'opacity-50')}
            >
              <span className="size-2 rounded-full" style={{ background: TYPE_COLORS[t] }} />
              {TYPE_LABELS[t]}
            </Badge>
          </button>
        ))}
        <select
          value={docFilter}
          onChange={(e) => setDocFilter(e.target.value)}
          className="ml-auto h-8 rounded-md border bg-background px-2 text-xs"
        >
          <option value="">všetky dokumenty</option>
          {docs?.map((d) => (
            <option key={d.id} value={d.id}>
              {d.filename}
            </option>
          ))}
        </select>
        {graph && (
          <span className="text-xs text-muted-foreground">
            {graph.nodes.length}/{graph.total_nodes} uzlov
          </span>
        )}
      </div>

      <div ref={containerRef} className="h-[calc(100svh-14rem)] overflow-hidden rounded-md border">
        {isPending && <Skeleton className="h-full w-full" />}
        {graph && graph.nodes.length === 0 && (
          <p className="py-24 text-center text-sm text-muted-foreground">
            Graf je prázdny — nahraj a zaindexuj dokumenty.
          </p>
        )}
        {graph && graph.nodes.length > 0 && (
          <ForceGraph2D
            graphData={graphData}
            width={containerRef.current?.clientWidth}
            height={containerRef.current?.clientHeight}
            nodeLabel={(n) => `${(n as GraphNode).label} (${(n as GraphNode).type})`}
            nodeVal={(n) => 1 + Math.sqrt((n as GraphNode).degree)}
            nodeColor={(n) => canvasColors[(n as GraphNode).type] ?? canvasColors.chunk}
            linkColor={() => 'rgba(128,128,128,0.25)'}
            linkWidth={(l) => ((l as { type: string }).type === 'SAME_AS' ? 2 : 1)}
            onNodeClick={(n) => setSelectedNode((n as GraphNode).id)}
            cooldownTicks={120}
          />
        )}
      </div>

      <Sheet open={!!selectedNode} onOpenChange={(o) => !o && setSelectedNode(null)}>
        <SheetContent className="w-96 overflow-y-auto sm:max-w-96">
          {detail && (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2 text-base">
                  <span
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ background: TYPE_COLORS[detail.type] }}
                  />
                  {detail.label}
                </SheetTitle>
              </SheetHeader>
              <div className="space-y-4 px-4 pb-6">
                <Badge variant="outline">{TYPE_LABELS[detail.type]}</Badge>

                {detail.evidence.length > 0 && (
                  <div className="space-y-2">
                    <h3 className="label-mono text-muted-foreground">
                      Dôkazy ({detail.evidence.length})
                    </h3>
                    {detail.evidence.map((ev, i) => (
                      <button
                        key={i}
                        className="w-full rounded-md border p-2 text-left text-xs hover:bg-secondary/50"
                        onClick={() =>
                          ev.doc_id &&
                          ev.page_from &&
                          setPdfTarget({
                            docId: ev.doc_id,
                            filename: ev.filename ?? '',
                            page: ev.page_from,
                          })
                        }
                      >
                        <p className="font-medium">
                          {ev.filename ?? '?'}
                          {ev.page_from != null && (
                            <span className="text-muted-foreground"> · str. {ev.page_from}</span>
                          )}
                          <Badge variant="outline" className="ml-1.5 text-[10px]">
                            {ev.edge_type}
                          </Badge>
                        </p>
                        {ev.snippet && (
                          <p className="mt-1 line-clamp-3 text-muted-foreground">{ev.snippet}</p>
                        )}
                      </button>
                    ))}
                  </div>
                )}

                {detail.neighbors.length > 0 && (
                  <div className="space-y-2">
                    <h3 className="label-mono text-muted-foreground">
                      Susedia ({detail.neighbors.length})
                    </h3>
                    <div className="flex flex-wrap gap-1.5">
                      {detail.neighbors.slice(0, 40).map((n) => (
                        <button key={n.id} onClick={() => setSelectedNode(n.id)}>
                          <Badge variant="outline" className="gap-1 hover:bg-secondary">
                            <span
                              className="size-1.5 rounded-full"
                              style={{ background: TYPE_COLORS[n.type] ?? FALLBACK_COLOR }}
                            />
                            <span className="max-w-36 truncate">{n.label}</span>
                          </Badge>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <ThemesPanel />

      <PdfDialog target={pdfTarget} onClose={() => setPdfTarget(null)} />
    </div>
  )
}
