import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { useId } from 'react'

const SIZE = {
  md: 'sm:max-w-lg',
  lg: 'sm:max-w-2xl',
  xl: 'sm:max-w-3xl',
} as const

/**
 * Jednotný dialóg pre pridávanie a úpravy (rovnaký ako dialóg úlohy): hlavička, formulár,
 * pätička so „Zmazať“ vľavo a Zrušiť/Uložiť vpravo. Obsah sa posúva, keď je dlhší než okno.
 */
export function FormDialog({
  open,
  onClose,
  title,
  description,
  size = 'lg',
  onSubmit,
  submitLabel = 'Uložiť',
  submitting = false,
  submitDisabled = false,
  footerStart,
  hideSubmit = false,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  size?: keyof typeof SIZE
  onSubmit: () => void
  submitLabel?: string
  submitting?: boolean
  submitDisabled?: boolean
  /** vľavo v pätičke — typicky tlačidlo Zmazať */
  footerStart?: React.ReactNode
  hideSubmit?: boolean
  children: React.ReactNode
}) {
  const formId = useId()
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      {/* minmax(0,1fr): dlhý obsah (select, popis) nesmie natiahnuť stĺpec nad šírku dialógu */}
      <DialogContent className={cn('max-h-[90svh] grid-cols-[minmax(0,1fr)] overflow-y-auto', SIZE[size])}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <form
          id={formId}
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            onSubmit()
          }}
        >
          {children}
        </form>
        <DialogFooter className="gap-2 sm:justify-between">
          {footerStart ?? <span />}
          <div className="flex gap-2">
            <Button type="button" variant="outline" onClick={onClose}>
              {hideSubmit ? 'Zavrieť' : 'Zrušiť'}
            </Button>
            {!hideSubmit && (
              <Button type="submit" form={formId} disabled={submitting || submitDisabled}>
                {submitLabel}
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
