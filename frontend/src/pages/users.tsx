import { UsersManager } from '@/components/users-panel'
import { fetchMe } from '@/lib/api'
import { useQuery } from '@tanstack/react-query'

export default function UsersPage() {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display-lg text-2xl">Používatelia</h1>
        <p className="text-sm text-muted-foreground">
          Účty, roly a skupiny — kto sa môže prihlásiť a ktoré dokumenty vidí.
        </p>
      </div>
      {me && !me.auth_enabled && (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          Prihlasovanie je vypnuté (<code>RAG_AUTH_ENABLED=false</code>) — appka je otvorená
          bez účtov. Zapni ho a správa používateľov sa tu sprístupní.
        </p>
      )}
      <UsersManager />
    </div>
  )
}
