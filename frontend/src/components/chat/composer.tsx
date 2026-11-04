import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Microscope, SentIcon, StopIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'

/** Question input with the deep-research toggle and send / stop. Enter sends, Shift+Enter adds a line. */
export function Composer({
  draft,
  onDraftChange,
  research,
  onResearchChange,
  streaming,
  onSend,
  onStop,
}: {
  draft: string
  onDraftChange: (value: string) => void
  research: boolean
  onResearchChange: (value: boolean) => void
  streaming: boolean
  onSend: () => void
  onStop: () => void
}) {
  return (
    <form
      className="flex items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        onSend()
      }}
    >
      <Button
        type="button"
        variant={research ? 'default' : 'outline'}
        size="icon"
        className="size-11 shrink-0"
        aria-pressed={research}
        aria-label="Hĺbkový prieskum"
        title="Hĺbkový prieskum: podotázky, viac zdrojov a štruktúrovaná správa (asi minúta)"
        onClick={() => onResearchChange(!research)}
      >
        <HugeiconsIcon icon={Microscope} className="size-4" />
      </Button>
      <Textarea
        value={draft}
        onChange={(e) => onDraftChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            onSend()
          }
        }}
        aria-label={research ? 'Téma hĺbkového prieskumu' : 'Otázka'}
        aria-describedby="composer-hint"
        placeholder={research ? 'Téma prieskumu, napr. riziká projektu…' : 'Opýtaj sa na dokumenty…'}
        className="max-h-40 min-h-11 flex-1 resize-none"
        rows={1}
      />
      <span id="composer-hint" className="sr-only">
        Enter odošle, Shift+Enter pridá nový riadok.
      </span>
      {streaming ? (
        <Button type="button" variant="outline" size="icon" className="size-11 shrink-0" aria-label="Zastaviť odpoveď" onClick={onStop}>
          <HugeiconsIcon icon={StopIcon} className="size-4" />
        </Button>
      ) : (
        <Button type="submit" size="icon" className="size-11 shrink-0" aria-label="Odoslať otázku" disabled={!draft.trim()}>
          <HugeiconsIcon icon={SentIcon} className="size-4" />
        </Button>
      )}
    </form>
  )
}
