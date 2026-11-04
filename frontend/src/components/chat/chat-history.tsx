import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import type { ChatSummary } from '@/lib/api'
import { cn } from '@/lib/utils'

/** Past conversations; the desktop aside and the phone sheet render the same list. */
export function ChatHistory({
  chats,
  loading,
  activeId,
  onSelect,
  showLabel = true,
  className,
}: {
  chats: ChatSummary[] | undefined
  loading: boolean
  activeId: string | null
  onSelect: (id: string) => void
  showLabel?: boolean
  className?: string
}) {
  return (
    <nav aria-label="História konverzácií" className={cn('flex min-h-0 flex-col gap-2', className)}>
      {showLabel && <p className="label-mono px-2 text-2xs text-muted-foreground uppercase">História</p>}
      <ScrollArea className="min-h-0 flex-1">
        <ul className="space-y-0.5 pr-2">
          {loading && <Skeleton className="h-24" />}
          {!loading && !chats?.length && (
            <li className="px-2 py-1.5 text-xs text-muted-foreground">Zatiaľ žiadne konverzácie.</li>
          )}
          {chats?.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={c.id === activeId ? 'true' : undefined}
                className={cn(
                  'w-full truncate rounded-md px-2 py-2.5 text-left text-sm transition-colors md:py-1.5 md:text-xs',
                  'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                  c.id === activeId ? 'bg-secondary font-medium' : 'text-muted-foreground hover:bg-secondary/50',
                )}
              >
                {c.title}
              </button>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </nav>
  )
}
