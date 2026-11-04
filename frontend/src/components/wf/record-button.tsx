import { Button } from '@/components/ui/button'
import { formatSeconds, useRecorder } from '@/hooks/use-recorder'
import { cn } from '@/lib/utils'
import { Mic01Icon, StopIcon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'

/** Large "tap to talk, tap to send" button — usable even in work gloves. */
export function RecordButton({
  onRecorded,
  disabled,
  size = 'lg',
  idleLabel = 'Nahrať hlasom',
}: {
  onRecorded: (blob: Blob) => void
  disabled?: boolean
  size?: 'lg' | 'sm'
  idleLabel?: string
}) {
  const rec = useRecorder()
  const recording = rec.state === 'recording'

  async function toggle() {
    if (recording) {
      const blob = await rec.stop()
      if (blob) onRecorded(blob)
      return
    }
    await rec.start()
  }

  if (size === 'sm') {
    return (
      <div className="flex flex-col items-start gap-1">
        <Button
          type="button"
          variant={recording ? 'default' : 'outline'}
          onClick={() => void toggle()}
          disabled={disabled || rec.state === 'stopping'}
          className="gap-2"
          aria-label={recording ? 'Zastaviť a odoslať' : idleLabel}
        >
          <HugeiconsIcon icon={recording ? StopIcon : Mic01Icon} className="size-4" />
          {recording ? `Odoslať ${formatSeconds(rec.seconds)}` : idleLabel}
          {recording && <LevelBars level={rec.level} />}
        </Button>
        {rec.error && <p className="text-xs text-destructive">{rec.error}</p>}
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={disabled || rec.state === 'stopping'}
        aria-label={recording ? 'Zastaviť nahrávanie a odoslať' : idleLabel}
        className={cn(
          'relative flex size-28 items-center justify-center rounded-full border-2 transition-all duration-150 active:translate-y-px disabled:opacity-50',
          recording
            ? 'border-primary bg-primary text-primary-foreground'
            : 'border-primary/30 bg-primary/10 text-primary hover:border-primary hover:bg-primary/15',
        )}
      >
        {recording && (
          <span
            className="absolute inset-0 rounded-full border-2 border-primary/40 transition-transform duration-100"
            style={{ transform: `scale(${1 + rec.level * 0.35})` }}
          />
        )}
        <HugeiconsIcon icon={recording ? StopIcon : Mic01Icon} className="size-10" strokeWidth={1.6} />
      </button>
      <p className="text-sm font-medium">
        {recording ? (
          <span className="label-mono text-primary">● {formatSeconds(rec.seconds)} · ťukni a odošli</span>
        ) : (
          idleLabel
        )}
      </p>
      {recording && (
        <button
          type="button"
          onClick={rec.cancel}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          Zrušiť nahrávanie
        </button>
      )}
      {rec.error && <p className="max-w-xs text-center text-sm text-destructive">{rec.error}</p>}
    </div>
  )
}

function LevelBars({ level }: { level: number }) {
  return (
    <span className="flex h-3 items-end gap-0.5" aria-hidden>
      {[0.3, 0.6, 1].map((t) => (
        <span
          key={t}
          className="w-0.5 rounded-full bg-current transition-all duration-100"
          style={{ height: `${Math.max(20, Math.min(100, level * 100 * t + 20))}%` }}
        />
      ))}
    </span>
  )
}
