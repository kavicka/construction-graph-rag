import { FormDialog } from '@/components/form-dialog'
import { GroupInput } from '@/components/group-input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { fetchMe, fetchUsers, setDocumentAccess } from '@/lib/api'
import { Edit02Icon, LockKeyIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

function AccessForm({
  docId,
  access,
  open,
  onClose,
}: {
  docId: string
  access: string[]
  open: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const { data: users } = useQuery({ queryKey: ['users'], queryFn: fetchUsers })
  const [groups, setGroups] = useState(access)
  const save = useMutation({
    mutationFn: () => setDocumentAccess(docId, groups),
    onSuccess: () => {
      toast.success('Prístup uložený')
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      onClose()
    },
  })
  const known = [...new Set((users ?? []).flatMap((u) => u.groups))].sort()
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      size="md"
      title="Prístup k dokumentu"
      description="Dokument uvidia členovia týchto skupín (administrátor vidí všetko). „všetci“ = bez obmedzenia."
      onSubmit={() => save.mutate()}
      submitting={save.isPending}
      submitDisabled={groups.length === 0}
    >
      <div className="space-y-1.5">
        <Label htmlFor="access-groups">Skupiny</Label>
        <GroupInput id="access-groups" value={groups} onChange={setGroups} suggestions={known} allowEveryone />
      </div>
    </FormDialog>
  )
}

/** Who can see a document — user groups; "*" = everyone. Only with login enabled. */
export function AccessEditor({ docId, access }: { docId: string; access: string[] }) {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  const [open, setOpen] = useState(false)
  if (!me?.auth_enabled) return null
  const isAdmin = me.user?.role === 'admin'

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <HugeiconsIcon icon={LockKeyIcon} className="size-4 text-muted-foreground" />
      <span className="text-muted-foreground">Prístup:</span>
      {access.map((g) => (
        <Badge key={g} variant="outline">
          {g === '*' ? 'všetci' : g}
        </Badge>
      ))}
      {isAdmin && (
        <>
          <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2" onClick={() => setOpen(true)}>
            <HugeiconsIcon icon={Edit02Icon} className="size-3.5" /> Upraviť
          </Button>
          {/* key = access — after saving, the form reloads with the new values */}
          <AccessForm
            key={`${open}-${access.join(',')}`}
            docId={docId}
            access={access}
            open={open}
            onClose={() => setOpen(false)}
          />
        </>
      )}
    </div>
  )
}
