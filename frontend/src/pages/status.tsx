import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { ErrorState, PageHeader } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { fetchHealth } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'
import { HugeiconsIcon } from '@hugeicons/react'
import { CancelCircleIcon, CheckmarkCircle02Icon } from '@hugeicons/core-free-icons'

function StatusRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  )
}

function BoolBadge({ ok, okText, failText }: { ok: boolean; okText: string; failText: string }) {
  return ok ? (
    <Chip variant="success" icon={<HugeiconsIcon icon={CheckmarkCircle02Icon} className="size-3.5" />}>
      {okText}
    </Chip>
  ) : (
    <Chip variant="danger" icon={<HugeiconsIcon icon={CancelCircleIcon} className="size-3.5" />}>
      {failText}
    </Chip>
  )
}

export default function StatusPage() {
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 5000,
  })

  return (
    <div className="max-w-xl space-y-6">
      <PageHeader title="Stav systému" description="Lokálne modely, OCR a generovanie PDF. Obnovuje sa každých 5 sekúnd." />
      {isPending && <Skeleton className="h-40 w-full" />}
      {error && (
        <ErrorState
          error={error}
          title="Backend je nedostupný"
          onRetry={() => void refetch()}
        />
      )}
      {data && (
        <div className="divide-y rounded-lg border px-4">
          <StatusRow label="Backend" value={<BoolBadge ok okText={`v${data.version}`} failText="" />} />
          <StatusRow label="RAM" value={`${data.ram_gb} GB`} />
          <StatusRow label="Profil" value={<Badge variant="outline">{data.ram_profile}</Badge>} />
          <StatusRow label="Embedding model" value={<code className="text-xs">{data.embedding_model}</code>} />
          <StatusRow
            label="Ollama"
            value={<BoolBadge ok={data.ollama.up} okText="beží" failText="nebeží" />}
          />
          {Object.entries(data.ollama.models).map(([model, present]) => (
            <StatusRow
              key={model}
              label={model}
              value={<BoolBadge ok={present} okText="stiahnutý" failText="chýba" />}
            />
          ))}
          <StatusRow
            label="OCR"
            value={
              <span className="flex items-center gap-2">
                <code className="text-xs">{data.ocr.engine}</code>
                <BoolBadge ok={data.ocr.slovak} okText="slovenčina" failText="bez SK" />
              </span>
            }
          />
          <StatusRow
            label="Generovanie PDF"
            value={
              <BoolBadge ok={data.pdf_engine} okText="WeasyPrint" failText="chýba pango" />
            }
          />
        </div>
      )}
    </div>
  )
}
