import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { ThemesPanel } from '@/components/themes-panel'
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState, PageHeader } from '@/components/wf/bits'
import { usePrefersReducedMotion } from '@/hooks/use-media-query'
import { fetchDocuments, fetchGraph, fetchNodeDetail, type GraphNode } from '@/lib/api'
import { cn } from '@/lib/utils'
import { HierarchyIcon } from '@hugeicons/core-free-icons'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from 'react-force-graph-2d'

/** Node colours = brand tokens from index.css (they change with the light / dark theme). */
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

/** Canvas knows no CSS variables — values are re-read from <html> whenever its class (theme) changes. */
function useCanvasColors() {
  const [colors, setColors] = useState(readCanvasColors)
  useEffect(() => {
    const observer = new MutationObserver(() => setColors(readCanvasColors()))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])
  return colors
}

/** Element size as state (ResizeObserver), so the canvas never reads refs during render. */
function useElementSize() {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const ref = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    if (!el) return
    observer.current = new ResizeObserver(([entry]) =>
      setSize({ width: Math.floor(entry.contentRect.width), height: Math.floor(entry.contentRect.height) }),
    )
    observer.current.observe(el)
  }, [])
  return [ref, size] as const
}

const TYPE_LABELS: Record<GraphNode['type'], string> = {
  document: 'dokumenty',
  table: 'tabuľky',
  concept: 'pojmy',
  quantity: 'veličiny',
  reference: 'citácie',
  chunk: 'úseky textu',
  page: 'strany',
}

const DEFAULT_TYPES: GraphNode['type'][] = ['document', 'table', 'concept', 'quantity', 'reference']

const ALL_DOCS = '__all__'

type FgNode = NodeObject<GraphNode>
type FgLink = LinkObject<GraphNode, { type: string }>

export default function GraphPage() {
  const [activeTypes, setActiveTypes] = useState<Set<string>>(new Set(DEFAULT_TYPES))
  const [docFilter, setDocFilter] = useState<string>(ALL_DOCS)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null)
  const [containerRef, size] = useElementSize()
  const fgRef = useRef<ForceGraphMethods<FgNode, FgLink> | undefined>(undefined)
  const fitted = useRef(false)
  const canvasColors = useCanvasColors()
  const reducedMotion = usePrefersReducedMotion()

  const typesCsv = [...activeTypes].sort().join(',')
  const graph = useQuery({
    queryKey: ['graph', typesCsv, docFilter],
    queryFn: () => fetchGraph({ types: typesCsv, doc_id: docFilter === ALL_DOCS ? undefined : docFilter }),
  })
  const { data: docs } = useQuery({ queryKey: ['documents'], queryFn: fetchDocuments })
  const { data: detail } = useQuery({
    queryKey: ['graph-node', selectedNode],
    queryFn: () => fetchNodeDetail(selectedNode!),
    enabled: !!selectedNode,
  })

  const graphData = useMemo(
    () => ({
      nodes: graph.data?.nodes.map((n) => ({ ...n })) ?? [],
      links: graph.data?.links.map((l) => ({ ...l })) ?? [],
    }),
    [graph.data],
  )

  // new data → fit the view once more when the simulation settles (not after every node drag)
  useEffect(() => {
    fitted.current = false
  }, [graphData])

  const toggleType = (t: string) => {
    setActiveTypes((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next.size ? next : prev
    })
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Graf znalostí"
        description="Pojmy, veličiny a citácie prepojené naprieč dokumentmi. Klikni na uzol a uvidíš dôkazy."
        actions={
          <div className="flex items-center gap-2">
            <Label htmlFor="graph-doc" className="text-sm font-normal text-muted-foreground">
              Dokument
            </Label>
            <Select value={docFilter} onValueChange={setDocFilter}>
              <SelectTrigger id="graph-doc" className="w-56 max-w-[60vw]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_DOCS}>Všetky dokumenty</SelectItem>
                {docs?.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.filename}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        }
      />

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Typy uzlov">
          {(Object.keys(TYPE_LABELS) as GraphNode['type'][]).map((t) => (
            <button
              type="button"
              key={t}
              aria-pressed={activeTypes.has(t)}
              onClick={() => toggleType(t)}
              className="rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <Badge
                variant={activeTypes.has(t) ? 'secondary' : 'outline'}
                className={cn('h-7 gap-1.5 px-2.5', !activeTypes.has(t) && 'text-muted-foreground')}
              >
                <span
                  className={cn('size-2 rounded-full', !activeTypes.has(t) && 'opacity-40')}
                  style={{ background: TYPE_COLORS[t] }}
                />
                {TYPE_LABELS[t]}
              </Badge>
            </button>
          ))}
          {graph.data && (
            <span className="label-mono ml-auto text-xs text-muted-foreground">
              {graph.data.nodes.length}/{graph.data.total_nodes} uzlov
            </span>
          )}
        </div>

        <div ref={containerRef} className="h-[65svh] min-h-80 overflow-hidden rounded-md border">
          {graph.isPending && <Skeleton className="h-full w-full" />}
          {graph.error && (
            <ErrorState
              error={graph.error}
              title="Graf sa nepodarilo načítať"
              onRetry={() => void graph.refetch()}
              className="h-full justify-center border-0"
            />
          )}
          {graph.data && graph.data.nodes.length === 0 && (
            <EmptyState
              icon={HierarchyIcon}
              title="Graf je prázdny"
              description="Nahraj a zaindexuj dokumenty alebo zapni ďalšie typy uzlov."
              className="h-full justify-center border-0"
            />
          )}
          {graph.data && graph.data.nodes.length > 0 && size && (
            <ForceGraph2D<GraphNode, { type: string }>
              ref={fgRef}
              graphData={graphData}
              width={size.width}
              height={size.height}
              nodeLabel={(n) => `${n.label} (${TYPE_LABELS[n.type] ?? n.type})`}
              nodeVal={(n) => 1 + Math.sqrt(n.degree)}
              nodeColor={(n) => canvasColors[n.type] ?? canvasColors.chunk}
              linkColor={() => 'rgba(128,128,128,0.25)'}
              linkWidth={(l) => (l.type === 'SAME_AS' ? 2 : 1)}
              onNodeClick={(n) => setSelectedNode(n.id)}
              // reduced motion: lay the graph out off-screen and show it already settled
              warmupTicks={reducedMotion ? 120 : 0}
              cooldownTicks={reducedMotion ? 0 : 120}
              onEngineStop={() => {
                if (fitted.current) return
                fitted.current = true
                fgRef.current?.zoomToFit(reducedMotion ? 0 : 400, 40)
              }}
            />
          )}
        </div>
      </div>

      <Sheet open={!!selectedNode} onOpenChange={(o) => !o && setSelectedNode(null)}>
        <SheetContent className="w-full overflow-y-auto sm:w-96 sm:max-w-96">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2 text-base">
              {detail && (
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: TYPE_COLORS[detail.type] }} />
              )}
              {detail?.label ?? 'Načítavam…'}
            </SheetTitle>
            <SheetDescription className="sr-only">Dôkazy a susedné uzly v grafe</SheetDescription>
          </SheetHeader>
          {detail && (
            <div className="space-y-4 px-4 pb-6">
              <Badge variant="outline">{TYPE_LABELS[detail.type]}</Badge>

              {detail.evidence.length > 0 && (
                <div className="space-y-2">
                  <h3 className="label-mono text-muted-foreground">Dôkazy ({detail.evidence.length})</h3>
                  {detail.evidence.map((ev, i) => (
                    <button
                      type="button"
                      key={i}
                      className="w-full rounded-md border p-2 text-left text-xs hover:bg-secondary/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      onClick={() =>
                        ev.doc_id &&
                        ev.page_from &&
                        setPdfTarget({ docId: ev.doc_id, filename: ev.filename ?? '', page: ev.page_from })
                      }
                    >
                      <p className="font-medium">
                        {ev.filename ?? '?'}
                        {ev.page_from != null && <span className="text-muted-foreground"> · str. {ev.page_from}</span>}
                        <Badge variant="outline" className="ml-1.5 text-2xs">
                          {ev.edge_type}
                        </Badge>
                      </p>
                      {ev.snippet && <p className="mt-1 line-clamp-3 text-muted-foreground">{ev.snippet}</p>}
                    </button>
                  ))}
                </div>
              )}

              {detail.neighbors.length > 0 && (
                <div className="space-y-2">
                  <h3 className="label-mono text-muted-foreground">Susedia ({detail.neighbors.length})</h3>
                  <div className="flex flex-wrap gap-1.5">
                    {detail.neighbors.slice(0, 40).map((n) => (
                      <button
                        type="button"
                        key={n.id}
                        onClick={() => setSelectedNode(n.id)}
                        className="rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      >
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
          )}
        </SheetContent>
      </Sheet>

      <ThemesPanel />

      <PdfDialog target={pdfTarget} onClose={() => setPdfTarget(null)} />
    </div>
  )
}
