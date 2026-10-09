import { FormDialog } from '@/components/form-dialog'
import { GroupInput } from '@/components/group-input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PageHeader, WorkerAvatar } from '@/components/wf/bits'
import {
  createApiToken,
  createUser,
  deleteUser,
  fetchMe,
  fetchUsers,
  updateUser,
  type AuthUser,
} from '@/lib/api'
import { Add01Icon, Copy01Icon, Delete02Icon, Edit02Icon, UserAdd01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

// --- API token ---------------------------------------------------------------------------

/** key = open → po zatvorení sa formulár zahodí, nový token začne s čistým stavom. */
function TokenDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <TokenForm key={String(open)} open={open} onClose={onClose} />
}

function TokenForm({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('mcp')
  const [token, setToken] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => createApiToken(name.trim()),
    onSuccess: (out) => setToken(out.token),
    onError: (e: Error) => toast.error(e.message),
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
        <Input
          id="token-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={token !== null}
          autoFocus
        />
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
              onClick={() =>
                void navigator.clipboard.writeText(token).then(() => toast.success('Skopírované'))
              }
            >
              <HugeiconsIcon icon={Copy01Icon} className="size-4" />
            </Button>
          </div>
          <p className="text-xs text-amber-600 dark:text-amber-400">
            Token sa zobrazí len teraz. Ulož si ho.
          </p>
        </div>
      )}
    </FormDialog>
  )
}

/** API token pre MCP klienta — vlastný token prihláseného používateľa (aj člena). */
export function ApiTokenCard() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  const [open, setOpen] = useState(false)
  if (!me?.auth_enabled || !me.user) return null
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle>Prístup cez API a MCP</CardTitle>
          <CardDescription>Token nahrádza heslo pre skripty a MCP klientov.</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          <HugeiconsIcon icon={Add01Icon} className="size-4" /> Nový token
        </Button>
      </CardHeader>
      <TokenDialog open={open} onClose={() => setOpen(false)} />
    </Card>
  )
}

// --- používatelia -------------------------------------------------------------------------

interface UserForm {
  email: string
  name: string
  password: string
  role: AuthUser['role']
  groups: string[]
}

function UserDialog({
  user,
  groupSuggestions,
  selfId,
  onClose,
}: {
  user: AuthUser | 'new' | null
  groupSuggestions: string[]
  selfId?: string
  onClose: () => void
}) {
  const isNew = user === 'new'
  const existing = user && user !== 'new' ? user : null
  return (
    <UserForm
      key={existing?.id ?? (isNew ? 'new' : 'closed')}
      open={user !== null}
      existing={existing}
      groupSuggestions={groupSuggestions}
      isSelf={!!existing && existing.id === selfId}
      onClose={onClose}
    />
  )
}

function UserForm({
  open,
  existing,
  groupSuggestions,
  isSelf,
  onClose,
}: {
  open: boolean
  existing: AuthUser | null
  groupSuggestions: string[]
  isSelf: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<UserForm>({
    email: existing?.email ?? '',
    name: existing?.name ?? '',
    password: '',
    role: existing?.role ?? 'member',
    groups: existing?.groups ?? [],
  })
  const set = <K extends keyof UserForm>(k: K, v: UserForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const done = (msg: string) => {
    toast.success(msg)
    void queryClient.invalidateQueries({ queryKey: ['users'] })
    onClose()
  }
  const save = useMutation({
    mutationFn: () =>
      existing
        ? updateUser(existing.id, {
            name: form.name.trim(),
            role: form.role,
            groups: form.groups,
            ...(form.password ? { password: form.password } : {}),
          })
        : createUser({ ...form, email: form.email.trim(), name: form.name.trim() }),
    onSuccess: () => done(existing ? 'Používateľ uložený' : 'Používateľ pridaný'),
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: () => deleteUser(existing!.id),
    onSuccess: () => done('Používateľ zmazaný'),
    onError: (e: Error) => toast.error(e.message),
  })
  const pwTooShort = form.password.length > 0 && form.password.length < 10

  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={existing ? 'Upraviť používateľa' : 'Nový používateľ'}
      description={
        existing
          ? existing.email
          : 'Účet sa dá hneď prihlásiť. Skupiny určujú, ktoré dokumenty uvidí.'
      }
      onSubmit={() => save.mutate()}
      submitting={save.isPending}
      submitDisabled={
        !form.name.trim() || pwTooShort || (!existing && (!form.email.trim() || form.password.length < 10))
      }
      footerStart={
        existing && !isSelf ? (
          <Button
            type="button"
            variant="ghost"
            className="gap-2 text-destructive hover:text-destructive"
            onClick={() => {
              if (confirm(`Zmazať používateľa ${existing.email}?`)) remove.mutate()
            }}
          >
            <HugeiconsIcon icon={Delete02Icon} className="size-4" /> Zmazať
          </Button>
        ) : undefined
      }
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2">
        {!existing && (
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="u-email">E-mail</Label>
            <Input
              id="u-email"
              type="email"
              autoComplete="off"
              value={form.email}
              onChange={(e) => set('email', e.target.value)}
              required
              autoFocus
            />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="u-name">Meno</Label>
          <Input id="u-name" value={form.name} onChange={(e) => set('name', e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="u-role">Rola</Label>
          <Select
            value={form.role}
            onValueChange={(v) => set('role', v as AuthUser['role'])}
            disabled={isSelf}
          >
            <SelectTrigger id="u-role" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="member">member: len dokumenty svojich skupín</SelectItem>
              <SelectItem value="admin">admin: všetko</SelectItem>
            </SelectContent>
          </Select>
          {isSelf && <p className="text-xs text-muted-foreground">Sám sebe admin práva neodoberieš.</p>}
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="u-groups">Skupiny</Label>
          <GroupInput
            id="u-groups"
            value={form.groups}
            onChange={(g) => set('groups', g)}
            suggestions={groupSuggestions}
          />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="u-password">{existing ? 'Nové heslo' : 'Heslo'}</Label>
          <Input
            id="u-password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => set('password', e.target.value)}
            aria-invalid={pwTooShort}
            placeholder={existing ? 'nechaj prázdne, ak sa nemení' : 'min. 10 znakov'}
          />
          <p className={pwTooShort ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
            {existing
              ? 'Zmena hesla odhlási používateľa a zruší jeho API tokeny.'
              : 'Minimálne 10 znakov.'}
          </p>
        </div>
      </div>
    </FormDialog>
  )
}

/** Správa používateľov — len pri zapnutom prihlasovaní a pre admina. */
export function UsersManager() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  const isAdmin = me?.user?.role === 'admin'
  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: fetchUsers,
    enabled: !!me?.auth_enabled && isAdmin,
  })
  const [editing, setEditing] = useState<AuthUser | 'new' | null>(null)
  if (!me?.auth_enabled || !isAdmin) return null
  const groups = [...new Set((users ?? []).flatMap((u) => u.groups))].sort()

  return (
    <>
      <PageHeader
        title="Používatelia"
        description="Skupiny určujú, ktoré dokumenty kto vidí. Filter beží priamo vo vyhľadávaní, takže odpovede ani citácie nikdy nepoužijú cudzí dokument."
        actions={
          <Button className="gap-2" onClick={() => setEditing('new')}>
            <HugeiconsIcon icon={UserAdd01Icon} className="size-4" /> Pridať používateľa
          </Button>
        }
      />
      <ul className="divide-y rounded-lg border">
        {users?.map((u) => (
          <li key={u.id} className="flex items-center gap-3 px-3 py-2.5">
            <WorkerAvatar name={u.name} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{u.name}</p>
              <p className="label-mono truncate text-[11px] text-muted-foreground">{u.email}</p>
            </div>
            <Badge variant={u.role === 'admin' ? 'default' : 'outline'}>{u.role}</Badge>
            <div className="hidden flex-wrap justify-end gap-1 sm:flex">
              {u.groups.map((g) => (
                <Badge key={g} variant="secondary">
                  {g}
                </Badge>
              ))}
            </div>
            <Button variant="ghost" size="icon" aria-label={`Upraviť ${u.email}`} onClick={() => setEditing(u)}>
              <HugeiconsIcon icon={Edit02Icon} className="size-4" />
            </Button>
          </li>
        ))}
      </ul>
      <UserDialog
        user={editing}
        groupSuggestions={groups}
        selfId={me.user?.id}
        onClose={() => setEditing(null)}
      />
    </>
  )
}
