import { FormDialog } from '@/components/form-dialog'
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { ErrorState } from '@/components/wf/bits'
import { Chip } from '@/components/wf/chip'
import { createApiToken, fetchApiTokens, fetchMe, revokeApiToken, type ApiTokenInfo } from '@/lib/api'
import { Add01Icon, Copy01Icon, Delete02Icon, Key01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

const TOKENS_KEY = 'api-tokens'
const DAY_MS = 86_400_000

const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString('sk-SK', { day: 'numeric', month: 'numeric', year: 'numeric' })

type TokenRowData = ApiTokenInfo & { daysLeft: number }

function ExpiryChip({ days }: { days: number }) {
  if (days <= 0) return <Chip variant="danger">vypršal</Chip>
  if (days <= 14) return <Chip variant="warning">vyprší o {days} {days === 1 ? 'deň' : days <= 4 ? 'dni' : 'dní'}</Chip>
  return null
}

/** key = open → closing discards the form, so a new token starts from a clean state. */
function TokenDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <TokenForm key={String(open)} open={open} onClose={onClose} />
}

function TokenForm({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('mcp')
  const [token, setToken] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => createApiToken(name.trim()),
    onSuccess: (out) => {
      setToken(out.token)
      void queryClient.invalidateQueries({ queryKey: [TOKENS_KEY] })
    },
    meta: { errorTitle: 'Token sa nepodarilo vytvoriť' },
  })
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      size="md"
      title="Nový API token"
      description="Claude Desktop / Code sa pripojí na /mcp s hlavičkou Authorization: Bearer … a uvidí len tvoje dokumenty."
      onSubmit={() => create.mutate()}
      submitLabel="Vytvoriť token"
      submitting={create.isPending}
      submitDisabled={!name.trim() || token !== null}
      hideSubmit={token !== null}
    >
      <div className="space-y-1.5">
        <Label htmlFor="token-name">Názov</Label>
        <Input id="token-name" value={name} onChange={(e) => setName(e.target.value)} disabled={token !== null} autoFocus />
      </div>
      {token && (
        <div className="space-y-2 rounded-md border bg-muted/40 p-3">
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate text-xs">{token}</code>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Kopírovať token"
              onClick={() => void navigator.clipboard.writeText(token).then(() => toast.success('Skopírované'))}
            >
              <HugeiconsIcon icon={Copy01Icon} className="size-4" />
            </Button>
          </div>
          <p className="text-xs text-warning">Token sa zobrazí len teraz. Ulož si ho.</p>
        </div>
      )}
    </FormDialog>
  )
}

function TokenRow({ token, showOwner }: { token: TokenRowData; showOwner: boolean }) {
  const queryClient = useQueryClient()
  const revoke = useMutation({
    mutationFn: () => revokeApiToken(token.id),
    onSuccess: () => {
      toast.success(`Token ${token.name} zrušený`)
      void queryClient.invalidateQueries({ queryKey: [TOKENS_KEY] })
    },
    meta: { errorTitle: 'Token sa nepodarilo zrušiť' },
  })
  return (
    <li className="flex items-center gap-3 py-3">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
        <HugeiconsIcon icon={Key01Icon} className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
          <span className="truncate">{token.name}</span>
          <ExpiryChip days={token.daysLeft} />
        </p>
        <p className="label-mono truncate text-2xs text-muted-foreground">
          {showOwner && `${token.user_email} · `}vytvorený {formatDate(token.created_at)} · platí do{' '}
          {formatDate(token.expires_at)}
        </p>
      </div>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={`Zrušiť token ${token.name}`} disabled={revoke.isPending}>
            <HugeiconsIcon icon={Delete02Icon} className="size-4" />
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Zrušiť token {token.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Skripty a MCP klienti s týmto tokenom stratia prístup okamžite. Akciu nejde vrátiť.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Ponechať</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => revoke.mutate()}>
              Zrušiť token
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  )
}

/** API tokens for MCP clients and scripts — own tokens for everyone, all tokens for an admin. */
export function ApiTokensCard() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  const [open, setOpen] = useState(false)
  const [allUsers, setAllUsers] = useState(false)
  const enabled = !!me?.auth_enabled && !!me.user
  const isAdmin = me?.user?.role === 'admin'
  const tokens = useQuery({
    queryKey: [TOKENS_KEY, allUsers],
    queryFn: () => fetchApiTokens(allUsers),
    // days left are computed when data arrives, not on every render
    select: (rows): TokenRowData[] => {
      const now = Date.now()
      return rows.map((r) => ({ ...r, daysLeft: Math.ceil((new Date(r.expires_at).getTime() - now) / DAY_MS) }))
    },
    enabled,
  })
  if (!enabled) return null

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle>Prístup cez API a MCP</CardTitle>
          <CardDescription>Token nahrádza heslo pre skripty a MCP klientov. Platí obmedzený čas.</CardDescription>
        </div>
        <div className="flex items-center gap-3">
          {isAdmin && (
            <Label className="gap-2 text-sm font-normal text-muted-foreground">
              <Switch checked={allUsers} onCheckedChange={setAllUsers} />
              Všetci používatelia
            </Label>
          )}
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            <HugeiconsIcon icon={Add01Icon} className="size-4" /> Nový token
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {tokens.isPending && <Skeleton className="h-16" />}
        {tokens.error && (
          <ErrorState error={tokens.error} title="Tokeny sa nepodarilo načítať" onRetry={() => void tokens.refetch()} />
        )}
        {tokens.data?.length === 0 && (
          <p className="text-sm text-muted-foreground">Zatiaľ žiadne tokeny. Vytvor prvý pre Claude Desktop alebo skript.</p>
        )}
        {!!tokens.data?.length && (
          <ul className="divide-y">
            {tokens.data.map((t) => (
              <TokenRow key={t.id} token={t} showOwner={allUsers} />
            ))}
          </ul>
        )}
      </CardContent>
      <TokenDialog open={open} onClose={() => setOpen(false)} />
    </Card>
  )
}
