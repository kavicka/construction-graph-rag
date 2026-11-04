import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { DraftCard } from '@/components/task-import/draft-card'
import { EmptyState, ErrorState, PageHeader } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { variantFor } from '@/lib/chip'
import {
  applyImport,
  decideAll,
  fetchImport,
  fetchProfessions,
  fetchWorkers,
  formatShort,
  retryImport,
  type ImportDetail,
} from '@/lib/workforce'
import { IMPORT_STATUS } from '@/lib/workforce-labels'
import { Alert02Icon, ArrowLeft01Icon, RefreshIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { toast } from 'sonner'

export default function TaskImportDetailPage() {
  const { id = '' } = useParams()
  const queryClient = useQueryClient()
  const key = ['task-import', id]
  const detail = useQuery({
    queryKey: key,
    queryFn: () => fetchImport(id),
    refetchInterval: (q) => {
      const s = q.state.data?.source.status
      return s === 'waiting' || s === 'extracting' ? 1500 : false
    },
  })
  const workers = useQuery({ queryKey: ['workers'], queryFn: fetchWorkers })
  const professions = useQuery({ queryKey: ['professions'], queryFn: fetchProfessions })
  const [pdf, setPdf] = useState<PdfTarget | null>(null)
  const [showUnchanged, setShowUnchanged] = useState(false)

  const all = useMutation({
    mutationFn: (decision: 'approved' | 'rejected') => decideAll(id, decision),
    onSuccess: (d) => queryClient.setQueryData(key, d),
  })
  const apply = useMutation({
    mutationFn: () => applyImport(id),
    onSuccess: (c) => {
      toast.success(`Hotovo: ${c.created} nových, ${c.updated} upravených, ${c.cancelled} zrušených úloh`)
      void queryClient.invalidateQueries({ queryKey: key })
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['task-imports'] })
    },
  })
  const retry = useMutation({
    mutationFn: () => retryImport(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: key }),
  })

  if (detail.isPending) return <Skeleton className="h-64" />
  if (detail.error) return <ErrorState error={detail.error} title="Import sa nepodarilo načítať" onRetry={() => void detail.refetch()} />
  const { source, drafts } = detail.data
  const st = IMPORT_STATUS[source.status]
  const busy = source.status === 'waiting' || source.status === 'extracting'
  const visible = drafts.filter((d) => d.kind !== 'unchanged')
  const unchanged = drafts.filter((d) => d.kind === 'unchanged')
  const pending = drafts.filter((d) => d.decision === 'pending').length
  const approved = drafts.filter((d) => d.decision === 'approved' && d.kind !== 'unchanged').length
  const editable = source.status === 'review'

  return (
    <div className="max-w-3xl space-y-6">
      <Link to="/tasks/import" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary">
        <HugeiconsIcon icon={ArrowLeft01Icon} className="size-4" /> Import úloh
      </Link>
      <PageHeader
        title={source.filename}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Chip dot variant={variantFor(source.site_id)}>
              {source.site_name}
            </Chip>
            {source.version > 1 && <Chip variant="primary">verzia {source.version}</Chip>}
            <Chip variant={st.variant}>{st.label}</Chip>
            {source.doc_date && <span className="label-mono text-xs">dokument z {formatShort(source.doc_date)}</span>}
          </span>
        }
      />

      {busy && (
        <div className="space-y-2 rounded-lg border p-4">
          <p className="text-sm">
            {source.status === 'waiting' ? 'Čítam dokument (OCR, tabuľky)…' : 'Lokálny model hľadá v dokumente úlohy…'}
          </p>
          <Progress value={Math.max(5, source.progress * 100)} className="h-1.5" />
        </div>
      )}
      {source.status === 'error' && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-danger/10 p-4 text-sm text-danger">
          <span>{source.error}</span>
          <Button size="sm" variant="outline" className="gap-2" onClick={() => retry.mutate()}>
            <HugeiconsIcon icon={RefreshIcon} className="size-4" /> Skúsiť znova
          </Button>
        </div>
      )}

      {(source.status === 'review' || source.status === 'applied') && (
        <>
          {drafts.length === 0 ? (
            <EmptyState
              icon={Alert02Icon}
              title="V dokumente sa nenašli žiadne úlohy"
              description="Model nenašiel konkrétne práce s dokladom v texte. Skontroluj, či je dokument čitateľný, alebo ho skús znova."
              action={
                <Button variant="outline" onClick={() => retry.mutate()}>
                  Spracovať znova
                </Button>
              }
            />
          ) : (
            <div className="sticky top-14 z-10 -mx-4 flex flex-wrap items-center gap-2 border-b bg-background/95 px-4 py-3 backdrop-blur-sm md:top-0 md:-mx-8 md:px-8">
              <span className="label-mono mr-auto text-xs text-muted-foreground">
                {visible.length} návrhov · {pending} nerozhodnutých · {approved} schválených
              </span>
              {editable && (
                <>
                  <Button size="sm" variant="outline" disabled={!pending || all.isPending} onClick={() => all.mutate('approved')}>
                    Schváliť všetko
                  </Button>
                  <Button
                    size="sm"
                    disabled={pending > 0 || apply.isPending}
                    title={pending > 0 ? 'Najprv rozhodni o každom návrhu' : undefined}
                    onClick={() => apply.mutate()}
                  >
                    Vytvoriť úlohy
                  </Button>
                </>
              )}
              {source.status === 'applied' && (
                <Button size="sm" asChild>
                  <Link to="/tasks">Otvoriť nástenku</Link>
                </Button>
              )}
            </div>
          )}
          <div className="space-y-3">
            {visible.map((d) => (
              <DraftCard
                key={d.id}
                sourceId={id}
                draft={d}
                drafts={drafts}
                workers={workers.data ?? []}
                professions={professions.data ?? []}
                editable={editable}
                onSaved={(updated) =>
                  queryClient.setQueryData<ImportDetail>(key, (old) =>
                    old ? { ...old, drafts: old.drafts.map((x) => (x.id === updated.id ? updated : x)) } : old,
                  )
                }
                onShowSource={() =>
                  source.doc_id &&
                  d.data.page &&
                  setPdf({
                    docId: source.doc_id,
                    filename: source.filename,
                    page: d.data.page,
                    highlights: d.data.boxes,
                  })
                }
                canShowPdf={source.filename.toLowerCase().endsWith('.pdf')}
              />
            ))}
          </div>
          {unchanged.length > 0 && (
            <div className="rounded-lg border">
              <button
                type="button"
                className="w-full px-4 py-3 text-left text-sm text-muted-foreground hover:text-primary"
                onClick={() => setShowUnchanged((v) => !v)}
              >
                {showUnchanged ? 'Skryť' : 'Ukázať'} úlohy bez zmeny ({unchanged.length})
              </button>
              {showUnchanged && (
                <ul className="divide-y border-t">
                  {unchanged.map((d) => (
                    <li key={d.id} className="flex items-center gap-2 px-4 py-2 text-sm">
                      <span className="flex-1">{d.data.title}</span>
                      <span className="label-mono text-xs text-muted-foreground">
                        {formatShort(d.data.date_from)} – {formatShort(d.data.date_to)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
      <PdfDialog target={pdf} onClose={() => setPdf(null)} />
    </div>
  )
}
