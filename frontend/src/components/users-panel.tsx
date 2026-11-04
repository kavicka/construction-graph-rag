import { FormDialog } from '@/components/form-dialog'
import { GroupInput } from '@/components/group-input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { PageHeader, WorkerAvatar } from '@/components/wf/bits'
import {
  createUser,
  deleteUser,
  fetchMe,
  fetchUsers,
  updateUser,
  type AuthUser,
} from '@/lib/api'
import { Delete02Icon, Edit02Icon, UserAdd01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'


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
  })
  const remove = useMutation({
    mutationFn: () => deleteUser(existing!.id),
    onSuccess: () => done('Používateľ zmazaný'),
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

/** User management — only with login enabled, and only for an admin. */
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
              <p className="label-mono truncate text-2xs text-muted-foreground">{u.email}</p>
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
