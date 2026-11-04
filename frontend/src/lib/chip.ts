export type ChipVariant = 'success' | 'warning' | 'danger' | 'info' | 'primary' | 'neutral'

/** Stable dot colour for any string (a site). */
export function variantFor(key: string): ChipVariant {
  const options: ChipVariant[] = ['info', 'success', 'warning', 'primary', 'danger']
  let h = 0
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return options[h % options.length]
}
