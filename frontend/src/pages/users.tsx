import { UsersManager } from '@/components/users-panel'
import { PageHeader } from '@/components/wf/bits'
import { fetchMe } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'

export default function UsersPage() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  return (
    <div className="max-w-3xl space-y-6">
      {me && !me.auth_enabled ? (
        <>
          <PageHeader title="Používatelia" />
          <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
            Prihlasovanie je vypnuté (<code>RAG_AUTH_ENABLED=false</code>), appka je otvorená bez
            účtov. Keď ho zapneš, používateľov spravuješ tu.
          </p>
        </>
      ) : (
        <UsersManager />
      )}
    </div>
  )
}
