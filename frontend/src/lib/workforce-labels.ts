import type { ChipVariant } from '@/lib/chip'
import type { ImportStatus } from '@/lib/workforce'

export const IMPORT_STATUS: Record<ImportStatus, { label: string; variant: ChipVariant }> = {
  waiting: { label: 'Čaká na spracovanie', variant: 'neutral' },
  extracting: { label: 'AI číta dokument', variant: 'info' },
  review: { label: 'Na schválenie', variant: 'warning' },
  applied: { label: 'Použité', variant: 'success' },
  error: { label: 'Chyba', variant: 'danger' },
}
