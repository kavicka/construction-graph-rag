import { PdfDialog, type PdfTarget } from '@/components/pdf-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { ApiTokenCard } from '@/components/users-panel'
import { PageHeader } from '@/components/wf/bits'
import {
  fetchCompany,
  fetchMe,
  previewCompanyPdf,
  saveCompany,
  ValidationError,
  type CompanySettings,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { File02Icon, FloppyDiskIcon, Loading03Icon, RotateLeft01Icon } from '@hugeicons/core-free-icons'
import { HugeiconsIcon } from '@hugeicons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

/** Svetlá téma PDF — rovnaké hodnoty ako backend/app/docgen/theme.py (a index.css :root). */
const PAPER = {
  ink: '#26251d',
  muted: '#8c8b85',
  surface: '#efeee8',
  border: '#edece8',
  rule: '#cfcdc4',
  primary: '#f54e00',
}

type TextKey =
  | 'name'
  | 'street'
  | 'city'
  | 'postal_code'
  | 'country'
  | 'ico'
  | 'dic'
  | 'ic_dph'
  | 'iban'
  | 'email'
  | 'phone'
  | 'website'
  | 'header_text'
  | 'footer_text'

function Field({
  id,
  label,
  error,
  hint,
  className,
  children,
}: {
  id: string
  label: string
  error?: string
  hint?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        hint && <p className="text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  )
}

function DocumentPreview({ form }: { form: CompanySettings }) {
  const address = [form.street, [form.postal_code, form.city].filter(Boolean).join(' '), form.country].filter(Boolean)
  const registry = [
    form.ico && `IČO: ${form.ico}`,
    form.dic && `DIČ: ${form.dic}`,
    form.ic_dph && `IČ DPH: ${form.ic_dph}`,
  ].filter(Boolean)
  const footerLine = [form.name, ...address, registry.join(' · ')].filter(Boolean).join(' · ')

  return (
    <div
      className="flex aspect-[210/297] w-full flex-col rounded-sm bg-white px-[8%] py-[7%] font-sans shadow-sm ring-1 ring-black/5"
      style={{ color: PAPER.ink }}
    >
      <div className="flex items-center justify-between gap-2 border-b pb-2" style={{ borderColor: PAPER.rule }}>
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="size-[7px] shrink-0 rounded-[1.5px]" style={{ background: PAPER.primary }} />
          <span className="truncate text-[10px] font-bold">{form.name || 'Názov firmy'}</span>
        </div>
        {form.header_text && (
          <span className="max-w-[45%] truncate text-right font-mono text-[6.5px]" style={{ color: PAPER.muted }}>
            {form.header_text}
          </span>
        )}
      </div>

      <p className="mt-4 font-mono text-[6.5px] tracking-[0.14em] uppercase" style={{ color: PAPER.primary }}>
        Report
      </p>
      <p className="text-[18px] leading-tight tracking-tight">Ukážkový dokument</p>
      <p className="mt-1 font-mono text-[6.5px]" style={{ color: PAPER.muted }}>
        Dátum: 07. 10. 2026
      </p>

      <p className="mt-4 flex items-center gap-1.5 text-[10px] font-medium">
        <span className="size-[6px] rounded-[1px]" style={{ background: PAPER.primary }} />
        Zhrnutie
      </p>
      <p className="mt-1 text-[8px] leading-relaxed">
        Dielo musí byť dokončené do 30. 6. 2027
        <sup className="font-mono font-bold" style={{ color: PAPER.primary }}>
          [1]
        </sup>
        . Rozpočet projektu je 248 500 EUR bez DPH
        <sup className="font-mono font-bold" style={{ color: PAPER.primary }}>
          [2]
        </sup>
        .
      </p>
      <div className="mt-2 text-[7px]">
        <div
          className="flex border-b px-1.5 py-1 font-mono text-[6px] font-bold tracking-wider uppercase"
          style={{ background: PAPER.surface, borderColor: PAPER.rule, color: PAPER.muted }}
        >
          <span className="flex-1">Etapa</span>
          <span>Suma</span>
        </div>
        {[
          ['Hrubá stavba', '120 000 €'],
          ['Dokončenie', '128 500 €'],
        ].map(([a, b]) => (
          <div key={a} className="flex border-b px-1.5 py-1" style={{ borderColor: PAPER.border }}>
            <span className="flex-1">{a}</span>
            <span>{b}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 space-y-1">
        {[100, 92, 96, 70].map((w) => (
          <div key={w} className="h-1 rounded-full" style={{ width: `${w}%`, background: PAPER.surface }} />
        ))}
      </div>

      <div
        className="mt-auto flex items-end justify-between gap-3 border-t pt-1.5 font-mono text-[5.5px] leading-snug"
        style={{ borderColor: PAPER.border, color: PAPER.muted }}
      >
        <div className="min-w-0">
          {form.footer_text && <p className="truncate">{form.footer_text}</p>}
          <p className="truncate">{footerLine || 'Údaje firmy v pätičke'}</p>
        </div>
        {form.show_page_numbers && <span className="shrink-0">Strana 1 z 1</span>}
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: company, isPending, isError } = useQuery({ queryKey: ['company'], queryFn: fetchCompany })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: fetchMe })
  // pri zapnutom prihlasovaní mení nastavenia len admin (backend vráti 403)
  const canEdit = !me?.auth_enabled || me.user?.role === 'admin'

  // draft = neuložené úpravy; null → formulár ukazuje uložený stav
  const [draft, setDraft] = useState<CompanySettings | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null)

  const form = draft ?? company ?? null
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(company)

  const set = <K extends keyof CompanySettings>(key: K, value: CompanySettings[K]) => {
    setDraft((d) => {
      const base = d ?? company
      return base ? { ...base, [key]: value } : d
    })
    setErrors((e) => {
      if (!(key in e)) return e
      const next = { ...e }
      delete next[key]
      return next
    })
  }

  const save = useMutation({
    mutationFn: saveCompany,
    onSuccess: (out) => {
      queryClient.setQueryData(['company'], out)
      setDraft(null)
      setErrors({})
      toast.success('Nastavenia uložené')
    },
    onError: (e: Error) => {
      if (e instanceof ValidationError) {
        setErrors(e.fields)
        toast.error('Skontroluj zvýraznené polia')
      } else toast.error(e.message)
    },
  })

  const preview = useMutation({
    mutationFn: previewCompanyPdf,
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      setPdfTarget({ url, downloadUrl: url, filename: 'nahlad-dokumentu.pdf', page: 1 })
    },
    onError: (e: Error) => {
      if (e instanceof ValidationError) {
        setErrors(e.fields)
        toast.error('Náhľad: skontroluj zvýraznené polia')
      } else toast.error(e.message)
    },
  })

  const closePreview = () => {
    if (pdfTarget?.url?.startsWith('blob:')) URL.revokeObjectURL(pdfTarget.url)
    setPdfTarget(null)
  }

  if (isError) {
    return <p className="text-sm text-destructive">Nastavenia sa nepodarilo načítať. Beží backend?</p>
  }
  if (isPending || !form) {
    return (
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <Skeleton className="h-[640px]" />
        <Skeleton className="h-[450px]" />
      </div>
    )
  }

  const text = (key: TextKey) => ({
    id: key,
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => set(key, e.target.value),
    'aria-invalid': !!errors[key],
    disabled: !canEdit,
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title="Nastavenia"
        description={
          <>
            Údaje a hlavička, ktoré dostane každý dokument vygenerovaný z chatu. Farby a písmo preberá z témy
            aplikácie.
            {!canEdit && (
              <span className="mt-1 block text-xs text-amber-600 dark:text-amber-400">
                Len na čítanie. Nastavenia mení administrátor.
              </span>
            )}
          </>
        }
        actions={
          <>
            {dirty && <Badge variant="outline">Neuložené zmeny</Badge>}
            <Button
              variant="ghost"
              size="sm"
              disabled={!dirty}
              onClick={() => {
                setDraft(null)
                setErrors({})
              }}
            >
              <HugeiconsIcon icon={RotateLeft01Icon} className="size-4" /> Zahodiť
            </Button>
            <Button variant="outline" size="sm" onClick={() => preview.mutate(form)} disabled={preview.isPending}>
              <HugeiconsIcon
                icon={preview.isPending ? Loading03Icon : File02Icon}
                className={cn('size-4', preview.isPending && 'animate-spin')}
              />
              Náhľad PDF
            </Button>
            <Button size="sm" onClick={() => save.mutate(form)} disabled={!canEdit || !dirty || save.isPending}>
              <HugeiconsIcon
                icon={save.isPending ? Loading03Icon : FloppyDiskIcon}
                className={cn('size-4', save.isPending && 'animate-spin')}
              />
              Uložiť
            </Button>
          </>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Údaje firmy</CardTitle>
              <CardDescription>Hlavička, pätička a kontaktný blok cenových ponúk.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-6">
              <Field id="name" label="Obchodné meno" error={errors.name} className="sm:col-span-6">
                <Input {...text('name')} maxLength={120} placeholder="Stavby Kráľ s.r.o." />
              </Field>
              <Field id="street" label="Ulica a číslo" error={errors.street} className="sm:col-span-6">
                <Input {...text('street')} maxLength={120} />
              </Field>
              <Field id="postal_code" label="PSČ" error={errors.postal_code} className="sm:col-span-2">
                <Input {...text('postal_code')} maxLength={12} />
              </Field>
              <Field id="city" label="Mesto" error={errors.city} className="sm:col-span-4">
                <Input {...text('city')} maxLength={80} />
              </Field>
              <Field id="country" label="Krajina" error={errors.country} className="sm:col-span-6">
                <Input {...text('country')} maxLength={60} />
              </Field>

              <Separator className="sm:col-span-6" />

              <Field id="ico" label="IČO" error={errors.ico} className="sm:col-span-2">
                <Input {...text('ico')} maxLength={12} inputMode="numeric" />
              </Field>
              <Field id="dic" label="DIČ" error={errors.dic} className="sm:col-span-2">
                <Input {...text('dic')} maxLength={14} />
              </Field>
              <Field id="ic_dph" label="IČ DPH" error={errors.ic_dph} className="sm:col-span-2">
                <Input {...text('ic_dph')} maxLength={16} placeholder="SK…" />
              </Field>
              <Field id="iban" label="IBAN" error={errors.iban} className="sm:col-span-6">
                <Input {...text('iban')} maxLength={42} className="font-mono" placeholder="SK31 1200 …" />
              </Field>

              <Separator className="sm:col-span-6" />

              <Field id="email" label="E-mail" error={errors.email} className="sm:col-span-2">
                <Input {...text('email')} type="email" maxLength={120} />
              </Field>
              <Field id="phone" label="Telefón" error={errors.phone} className="sm:col-span-2">
                <Input {...text('phone')} type="tel" maxLength={40} />
              </Field>
              <Field id="website" label="Web" error={errors.website} className="sm:col-span-2">
                <Input {...text('website')} maxLength={120} />
              </Field>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Hlavička a pätička</CardTitle>
              <CardDescription>Adresa a IČO sa v pätičke doplnia automaticky z údajov firmy.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field
                id="header_text"
                label="Text v hlavičke"
                hint="Krátky slogan alebo zameranie firmy vpravo hore."
                error={errors.header_text}
              >
                <Input {...text('header_text')} maxLength={160} placeholder="Stavebné práce a projekcia" />
              </Field>
              <Field
                id="footer_text"
                label="Text v pätičke"
                hint="Napr. zápis v obchodnom registri."
                error={errors.footer_text}
              >
                <Textarea
                  {...text('footer_text')}
                  maxLength={300}
                  rows={2}
                  placeholder="Zapísaná v OR Okresného súdu Žilina, oddiel Sro, vložka 1234/L"
                />
              </Field>
              <div className="flex items-center justify-between gap-4 rounded-md border px-3 py-2.5">
                <Label htmlFor="show_page_numbers" className="font-normal">
                  Čísla strán v pätičke
                </Label>
                <Switch
                  id="show_page_numbers"
                  checked={form.show_page_numbers}
                  disabled={!canEdit}
                  onCheckedChange={(v) => set('show_page_numbers', v)}
                />
              </div>
            </CardContent>
          </Card>
        </div>

        <aside className="space-y-3 lg:sticky lg:top-6">
          <p className="text-sm font-medium">Živý náhľad</p>
          <DocumentPreview form={form} />
          <p className="text-xs text-muted-foreground">
            Presný výstup ukáže tlačidlo Náhľad PDF. Renderuje sa rovnako ako dokumenty z chatu.
          </p>
        </aside>
      </div>

      <ApiTokenCard />

      <PdfDialog target={pdfTarget} onClose={closePreview} />
    </div>
  )
}
