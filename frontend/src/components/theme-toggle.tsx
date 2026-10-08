import { Moon02Icon, Sun03Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useTheme } from 'next-themes'

// Default follows the OS; a click pins the opposite theme (stored under localStorage "theme").
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme === 'dark'
  return (
    <button
      type="button"
      aria-label="Prepnúť tému"
      title={dark ? 'Svetlá téma' : 'Tmavá téma'}
      onClick={() => setTheme(dark ? 'light' : 'dark')}
      className="flex size-9 items-center justify-center rounded-md text-foreground/70 transition-colors hover:text-foreground"
    >
      <HugeiconsIcon icon={dark ? Sun03Icon : Moon02Icon} className="size-[18px]" />
    </button>
  )
}
