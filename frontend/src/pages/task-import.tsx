import { DOCUMENT_ACCEPT } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, PageHeader } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { variantFor } from '@/lib/chip'
import { IMPORT_STATUS } from '@/lib/workforce-labels'
import {
  fetchImports,
  fetchSites,
  formatDay,
  suggestPrevious,
  uploadTaskDocument,
  type ImportSource,
} from '@/lib/workforce'
import { FileImportIcon, Upload04Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'

export default function TaskImportPage() {
  const [open, setOpen] = useState(false)
  const imports = useQuery({
    queryKey: ['task-imports'],
    queryFn: fetchImports,
    refetchInterval: (q) => (q.state.data?.some((i) => i.status === 'waiting' || i.status === 'extracting') ? 2000 : false),
  })

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader
        title="Import úloh"
        description="Nahraj zadanie od dodávateľa (PDF, Word, Excel, sken). Lokálny model z neho navrhne úlohy. Úloha vznikne, až keď ju schváliš."
        actions={
          <Button className="gap-2" onClick={() => setOpen(true)}>
            <HugeiconsIcon icon={Upload04Icon} className="size-4" />
            Nahrať dokument
          </Button>
        }
      />
      {imports.isPending && <Skeleton className="h-32" />}
      {imports.data?.length === 0 && (
        <EmptyState
          icon={FileImportIcon}
          title="Zatiaľ žiadny import"
          description="Stačí aj e-mail, zápis z porady alebo harmonogram v tabuľke. Pri každej navrhnutej úlohe uvidíš citát zo zdroja."
          action={<Button onClick={() => setOpen(true)}>Nahrať prvý dokument</Button>}
        />
      )}
      {!!imports.data?.length && (
        <ul className="divide-y rounded-lg border">
          {imports.data.map((i) => <ImportRow key={i.id} item={i} />)}
        </ul>
      )}
      <UploadDialog open={open} onClose={() => setOpen(false)} />
    </div>
  )
}

function ImportRow({ item }: { item: ImportSource }) {
  const st = IMPORT_STATUS[item.status]
  const busy = item.status === 'waiting' || item.status === 'extracting'
  return (
    <li>
      <Link to={`/tasks/import/${item.id}`} className="group block px-4 py-3 transition-colors hover:bg-muted/50">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-medium transition-colors group-hover:text-primary">
            {item.filename}
          </span>
          {item.version > 1 && <Chip variant="primary">verzia {item.version}</Chip>}
          <Chip dot variant={variantFor(item.site_id)}>
            {item.site_name}
          </Chip>
          <Chip variant={st.variant}>{st.label}</Chip>
        </div>
        <p className="label-mono mt-1 text-2xs text-muted-foreground">
          nahraté {formatDay(item.created_at.slice(0, 10), false)}
          {item.error && <span className="text-destructive"> · {item.error}</span>}
        </p>
        {busy && <Progress value={item.progress * 100} className="mt-2 h-1" />}
      </Link>
    </li>
  )
}

function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const sites = useQuery({ queryKey: ['sites'], queryFn: fetchSites, enabled: open })
  const [siteId, setSiteId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [previous, setPrevious] = useState<ImportSource | null>(null)
  const [asVersion, setAsVersion] = useState<boolean | null>(null)

  const reset = () => {
    setFile(null)
    setPrevious(null)
    setAsVersion(null)
  }
  const check = async (f: File, site: string) => {
    setPrevious(null)
    setAsVersion(null)
    if (!site) return
    const prev = await suggestPrevious(site, f.name).catch(() => null)
    setPrevious(prev)
  }
  const upload = useMutation({
    mutationFn: () => uploadTaskDocument(file!, siteId, previous && asVersion ? previous.id : null),
    onSuccess: (src) => {
      void queryClient.invalidateQueries({ queryKey: ['task-imports'] })
      toast.success('Dokument nahratý, AI v ňom hľadá úlohy')
      reset()
      onClose()
      navigate(`/tasks/import/${src.id}`)
    },
  })

  const activeSites = sites.data?.filter((s) => s.active) ?? []
  const needsDecision = previous !== null && asVersion === null

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          reset()
          onClose()
        }
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Nahrať dokument s úlohami</DialogTitle>
          <DialogDescription>Úlohy sa priradia k vybranej stavbe.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>Stavba</Label>
            {sites.isSuccess && activeSites.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Najprv pridaj stavbu v sekcii <Link to="/team" className="text-primary hover:underline" onClick={onClose}>Tím a stavby</Link>.
              </p>
            ) : (
              <Select
                value={siteId}
                onValueChange={(v) => {
                  setSiteId(v)
                  if (file) void check(file, v)
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Vyber stavbu" />
                </SelectTrigger>
                <SelectContent>
                  {activeSites.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="imp-file">Dokument</Label>
            <input
              id="imp-file"
              type="file"
              accept={DOCUMENT_ACCEPT}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:text-sm file:text-secondary-foreground"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null
                setFile(f)
                if (f) void check(f, siteId)
              }}
            />
          </div>
          {previous && (
            <div className="space-y-2 rounded-md border border-primary/30 bg-primary/5 p-3">
              <p className="text-sm">
                Vyzerá to ako <strong>nová verzia</strong> dokumentu „{previous.filename}“ (verzia {previous.version}).
                AI porovná úlohy a navrhne, čo pribudlo, zmenilo sa alebo zaniklo.
              </p>
              <div className="flex gap-2">
                <Button size="sm" variant={asVersion === true ? 'default' : 'outline'} onClick={() => setAsVersion(true)}>
                  Áno, nová verzia
                </Button>
                <Button size="sm" variant={asVersion === false ? 'default' : 'outline'} onClick={() => setAsVersion(false)}>
                  Nie, iný dokument
                </Button>
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Zrušiť
          </Button>
          <Button disabled={!file || !siteId || needsDecision || upload.isPending} onClick={() => upload.mutate()}>
            Nahrať a spracovať
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
