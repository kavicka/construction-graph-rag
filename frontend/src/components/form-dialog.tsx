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
 * One dialog for adding and editing (same as the task dialog): header, form, footer with
 * Delete on the left and Cancel/Save on the right. The body scrolls when taller than the window.
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
  /** left side of the footer — typically the Delete button */
  footerStart?: React.ReactNode
  hideSubmit?: boolean
  children: React.ReactNode
}) {
  const formId = useId()
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      {/* minmax(0,1fr): long content (select, description) must not stretch the column past the dialog */}
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
