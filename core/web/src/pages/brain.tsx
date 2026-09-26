// The assistant's brain: which model it uses and how it is reached, its
// standing instructions, tool selection and per-turn limits. Every save is a
// new settings version; the next message uses it and older versions can be
// compared and restored.
import { collection, limit, onSnapshot, orderBy, query } from 'firebase/firestore'
import { CheckIcon, ChevronsUpDownIcon, HistoryIcon, PlugZapIcon, RotateCcwIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'

import { PageHeader } from '@/components/app-shell'
import { SecretPicker, secretName } from '@/components/secret-picker'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, api, errorMessage } from '@/lib/api'
import { formatDateTime, formatRelative } from '@/lib/format'
import { TOOL_STRATEGIES } from '@/lib/labels'
import { useSignedIn } from '@/lib/session'
import type { AssistantSettings, Protocol, StoredSettings } from '@/lib/types'
import { cn } from '@/lib/utils'

// Wire protocols the core speaks. "Custom" presets differ only in that the
// owner gives the endpoint; a preset never implies which account or plan is used.
const PRESETS = [
  { id: 'gemini', protocol: 'google', custom: false, label: 'Gemini API', hint: 'Google’ın genel Gemini API adresi; anahtar gerekir.' },
  { id: 'openai', protocol: 'openai', custom: false, label: 'OpenAI API', hint: 'OpenAI’ın genel API adresi; anahtar gerekir.' },
  {
    id: 'openai-compatible',
    protocol: 'openai',
    custom: true,
    label: 'OpenAI uyumlu uç',
    hint: 'OpenAI Chat Completions biçimini konuşan herhangi bir uç: aracı hizmetler, yerel sunucular, vekiller.',
  },
  {
    id: 'gemini-compatible',
    protocol: 'google',
    custom: true,
    label: 'Gemini uyumlu uç',
    hint: 'Gemini API biçimini konuşan bir uç; örneğin kurulumdaki yan kapsayıcı vekil.',
  },
] as const satisfies readonly { id: string; protocol: Protocol; custom: boolean; label: string; hint: string }[]

type PresetId = (typeof PRESETS)[number]['id']

const DEFAULTS: AssistantSettings = {
  instructions: '',
  model: { protocol: 'google', base_url: null, api_key_ref: null, model: '' },
  tool_selection: { strategy: 'builtin', jev_model: 'jev-latest', api_key_ref: null },
  limits: { request_limit: 12, tool_calls_limit: 16 },
}

function presetOf(model: AssistantSettings['model']): PresetId {
  const custom = model.base_url !== null
  return PRESETS.find((p) => p.protocol === model.protocol && p.custom === custom)?.id ?? 'openai-compatible'
}

const FIELD_LABELS: Record<string, string> = {
  instructions: 'Talimatlar',
  'model.protocol': 'İstek biçimi',
  'model.base_url': 'Uç adresi',
  'model.api_key_ref': 'Anahtar',
  'model.model': 'Model',
  'tool_selection.strategy': 'Araç seçimi',
  'tool_selection.jev_model': 'Jev modeli',
  'tool_selection.api_key_ref': 'Jev anahtarı',
  'limits.request_limit': 'Model isteği sınırı',
  'limits.tool_calls_limit': 'Araç çağrısı sınırı',
}

function flatten(value: unknown, prefix = ''): Record<string, unknown> {
  if (value === null || typeof value !== 'object') return { [prefix]: value }
  return Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>(
    (all, [key, inner]) => ({ ...all, ...flatten(inner, prefix ? `${prefix}.${key}` : key) }),
    {},
  )
}

function display(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (key.endsWith('api_key_ref')) return secretName(String(value))
  if (key === 'instructions') {
    const text = String(value)
    return text.length > 80 ? `${text.slice(0, 80)}… (${text.length} karakter)` : text
  }
  return String(value)
}

function changes(from: AssistantSettings, to: AssistantSettings): { key: string; before: unknown; after: unknown }[] {
  const a = flatten(from)
  const b = flatten(to)
  return [...new Set([...Object.keys(a), ...Object.keys(b)])]
    .filter((key) => JSON.stringify(a[key]) !== JSON.stringify(b[key]))
    .map((key) => ({ key, before: a[key], after: b[key] }))
}

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  )
}

function Field({ label, htmlFor, hint, children }: { label: string; htmlFor?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  )
}

function ModelPicker({ value, models, onChange }: { value: string; models: string[] | null; onChange: (model: string) => void }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" role="combobox" aria-expanded={open} className="w-full justify-between font-normal">
          <span className={cn('truncate', !value && 'text-muted-foreground')}>{value || 'Model seç'}</span>
          <ChevronsUpDownIcon className="opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) p-0" align="start">
        <Command>
          <CommandInput placeholder="Model ara veya adını yaz" value={search} onValueChange={setSearch} />
          <CommandList>
            <CommandEmpty>{models === null ? 'Listeyi görmek için önce bağlantıyı dene.' : 'Eşleşen model yok.'}</CommandEmpty>
            {search.trim() && !models?.includes(search.trim()) && (
              <CommandGroup heading="Elle">
                <CommandItem
                  value={`__manual__${search}`}
                  onSelect={() => {
                    onChange(search.trim())
                    setOpen(false)
                  }}
                >
                  “{search.trim()}” kullan
                </CommandItem>
              </CommandGroup>
            )}
            {models && models.length > 0 && (
              <CommandGroup heading={`Uçtaki modeller (${models.length})`}>
                {models.map((model) => (
                  <CommandItem
                    key={model}
                    value={model}
                    onSelect={() => {
                      onChange(model)
                      setOpen(false)
                    }}
                  >
                    <CheckIcon className={cn(model === value ? 'opacity-100' : 'opacity-0')} />
                    {model}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

type Probe = { state: 'idle' | 'testing' } | { state: 'ok'; count: number } | { state: 'failed'; message: string }

function History({ current, onRestore }: { current: StoredSettings; onRestore: (settings: AssistantSettings) => void }) {
  const { db, user } = useSignedIn()
  const [versions, setVersions] = useState<StoredSettings[]>([])
  const [openVersion, setOpenVersion] = useState<number | null>(null)
  useEffect(
    () =>
      onSnapshot(
        query(collection(db, 'users', user.uid, 'settings_history'), orderBy('version', 'desc'), limit(30)),
        (snapshot) => setVersions(snapshot.docs.map((d) => d.data() as StoredSettings)),
      ),
    [db, user.uid],
  )
  return (
    <Section title="Sürüm geçmişi" description="Her kayıt yeni bir sürümdür. Bir sürümü seçip farkları gör, istersen o sürüme dön.">
      <ul className="divide-y rounded-lg border">
        {versions.map((version) => {
          const previous = versions.find((v) => v.version === version.version - 1)
          const diff = previous ? changes(previous.settings, version.settings) : []
          const expanded = openVersion === version.version
          return (
            <li key={version.version}>
              <button
                type="button"
                className="hover:bg-muted/50 flex w-full items-center gap-2 px-3 py-2 text-start text-sm"
                onClick={() => setOpenVersion(expanded ? null : version.version)}
              >
                <span className="font-medium">Sürüm {version.version}</span>
                {version.version === current.version && <Badge variant="secondary">Kullanımda</Badge>}
                <span className="text-muted-foreground ms-auto text-xs" title={formatDateTime(version.updated_at)}>
                  {formatRelative(version.updated_at)}
                </span>
              </button>
              {expanded && (
                <div className="space-y-2 px-3 pb-3 text-xs">
                  {previous ? (
                    diff.length ? (
                      <ul className="space-y-1">
                        {diff.map((change) => (
                          <li key={change.key}>
                            <span className="font-medium">{FIELD_LABELS[change.key] ?? change.key}:</span>{' '}
                            <span className="text-muted-foreground line-through">{display(change.key, change.before)}</span> →{' '}
                            <span>{display(change.key, change.after)}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-muted-foreground">Önceki sürümle aynı.</p>
                    )
                  ) : (
                    <p className="text-muted-foreground">İlk kayıt: {version.settings.model.model}</p>
                  )}
                  {version.version !== current.version && (
                    <Button size="sm" variant="outline" onClick={() => onRestore(version.settings)}>
                      <RotateCcwIcon /> Bu sürümü forma yükle
                    </Button>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

export function BrainPage() {
  const { user } = useSignedIn()
  const [stored, setStored] = useState<StoredSettings | null | undefined>(undefined)
  const [form, setForm] = useState<AssistantSettings>(DEFAULTS)
  const [preset, setPreset] = useState<PresetId>('gemini')
  const [models, setModels] = useState<string[] | null>(null)
  const [probe, setProbe] = useState<Probe>({ state: 'idle' })
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const current = await api<StoredSettings>(user, 'GET', '/v1/settings')
      setStored(current)
      setForm(current.settings)
      setPreset(presetOf(current.settings.model))
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setStored(null)
        return
      }
      toast.error(`Ayarlar okunamadı: ${errorMessage(error)}`)
    }
  }, [user])

  useEffect(() => {
    void load()
  }, [load])

  const dirty = useMemo(
    () => (stored ? changes(stored.settings, form).length > 0 : form.model.model !== ''),
    [stored, form],
  )
  const presetInfo = PRESETS.find((p) => p.id === preset) ?? PRESETS[0]

  const setModel = (change: Partial<AssistantSettings['model']>) => {
    setForm((f) => ({ ...f, model: { ...f.model, ...change } }))
    if ('protocol' in change || 'base_url' in change || 'api_key_ref' in change) {
      setModels(null)
      setProbe({ state: 'idle' })
    }
  }

  const choosePreset = (id: PresetId) => {
    const next = PRESETS.find((p) => p.id === id) ?? PRESETS[0]
    setPreset(id)
    setModel({ protocol: next.protocol, base_url: next.custom ? (form.model.base_url ?? '') : null })
  }

  const testConnection = async () => {
    setProbe({ state: 'testing' })
    try {
      const result = await api<{ ok: boolean; models?: string[]; error?: string; http_status?: number }>(
        user,
        'POST',
        '/v1/connections/test',
        { protocol: form.model.protocol, base_url: form.model.base_url || null, api_key_ref: form.model.api_key_ref },
      )
      if (!result.ok) {
        const status = result.http_status ? `HTTP ${result.http_status}` : 'yanıt yok'
        setProbe({ state: 'failed', message: `${result.error} (${status})` })
        return
      }
      setModels(result.models ?? [])
      setProbe({ state: 'ok', count: result.models?.length ?? 0 })
    } catch (error) {
      setProbe({ state: 'failed', message: errorMessage(error) })
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      const body = {
        expected_version: stored?.version ?? null,
        settings: { ...form, model: { ...form.model, base_url: form.model.base_url || null } },
      }
      const result = await api<StoredSettings>(user, 'PUT', '/v1/settings', body)
      setStored(result)
      setForm(result.settings)
      toast.success(`Sürüm ${result.version} kaydedildi. Sonraki mesajdan itibaren kullanılır.`)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast.error('Ayarlar başka bir yerde değişti. Güncel hâli yüklendi; değişikliğini yeniden yap.')
        void load()
      } else toast.error(`Kaydedilemedi: ${errorMessage(error)}`)
    } finally {
      setSaving(false)
    }
  }

  if (stored === undefined) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="Beyin" />
        <div className="mx-auto w-full max-w-3xl space-y-4 p-6">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Beyin">
        {stored && (
          <span className="text-muted-foreground hidden text-xs sm:inline" title={formatDateTime(stored.updated_at)}>
            Sürüm {stored.version} · {formatRelative(stored.updated_at)}
          </span>
        )}
      </PageHeader>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <form
          className="mx-auto w-full max-w-3xl space-y-4 p-3 pb-28 sm:p-6 sm:pb-28"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          {!stored && (
            <p className="rounded-lg border border-dashed p-3 text-sm">
              Henüz model ayarı yok. Bir bağlantı seç, deneyip modeli belirle ve kaydet; mesajlar ondan sonra yanıtlanır.
            </p>
          )}

          <Section
            title="Model bağlantısı"
            description="Biçim yalnız isteklerin nasıl yazıldığını belirler; hangi hesabın veya planın kullanıldığı uç adresine ve anahtara bağlıdır."
          >
            <Field label="Bağlantı türü" htmlFor="preset" hint={presetInfo.hint}>
              <Select value={preset} onValueChange={(v) => choosePreset(v as PresetId)}>
                <SelectTrigger id="preset" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRESETS.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {presetInfo.custom && (
              <Field label="Uç adresi" htmlFor="base-url" hint="HTTPS adresi; düz HTTP yalnız aynı makinedeki (localhost) uçlar için.">
                <Input
                  id="base-url"
                  type="url"
                  inputMode="url"
                  value={form.model.base_url ?? ''}
                  onChange={(e) => setModel({ base_url: e.target.value })}
                  placeholder="https://…"
                  required
                />
              </Field>
            )}
            <Field label="Anahtar" htmlFor="api-key">
              <SecretPicker id="api-key" value={form.model.api_key_ref} onChange={(ref) => setModel({ api_key_ref: ref })} />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="secondary" onClick={() => void testConnection()} disabled={probe.state === 'testing'}>
                <PlugZapIcon /> {probe.state === 'testing' ? 'Deneniyor…' : 'Bağlantıyı dene'}
              </Button>
              {probe.state === 'ok' && <span className="text-sm text-emerald-600 dark:text-emerald-400">Çalışıyor · {probe.count} model listelendi</span>}
              {probe.state === 'failed' && <span className="text-destructive text-sm">Bağlanılamadı: {probe.message}</span>}
            </div>
            <Field label="Model" hint="Listeden seç ya da adını yaz. Liste, bağlantı denendiğinde uçtan canlı alınır.">
              <ModelPicker value={form.model.model} models={models} onChange={(model) => setModel({ model })} />
            </Field>
          </Section>

          <Section title="Kişilik ve talimatlar" description="JARVIS’in her turda uyduğu kalıcı yönergeler. Hafıza dosyalarından ayrıdır.">
            <Textarea
              value={form.instructions}
              onChange={(e) => setForm((f) => ({ ...f, instructions: e.target.value }))}
              className="min-h-56 text-sm"
              placeholder="Nasıl konuşsun, neye dikkat etsin, neyi asla yapmasın…"
            />
            <p className="text-muted-foreground text-xs">{form.instructions.length} karakter</p>
          </Section>

          <Section title="Araç seçimi" description="Temel olmayan araçlar gerektiğinde aranıp yüklenir. Aramayı kimin yapacağını seç.">
            <Field label="Strateji" htmlFor="strategy">
              <Select
                value={form.tool_selection.strategy}
                onValueChange={(v) => setForm((f) => ({ ...f, tool_selection: { ...f.tool_selection, strategy: v as 'builtin' | 'typesafe_jev' } }))}
              >
                <SelectTrigger id="strategy" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(TOOL_STRATEGIES).map(([id, label]) => (
                    <SelectItem key={id} value={id}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {form.tool_selection.strategy === 'typesafe_jev' && (
              <Field label="Jev anahtarı" htmlFor="jev-key">
                <SecretPicker
                  id="jev-key"
                  value={form.tool_selection.api_key_ref}
                  onChange={(ref) => setForm((f) => ({ ...f, tool_selection: { ...f.tool_selection, api_key_ref: ref } }))}
                />
              </Field>
            )}
          </Section>

          <Section title="Tur sınırları" description="Bir mesaj için en fazla kaç model isteği ve araç çağrısı yapılabileceği. Sınıra ulaşan tur hata olarak kapanır.">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Model isteği" htmlFor="request-limit">
                <Input
                  id="request-limit"
                  type="number"
                  min={1}
                  value={form.limits.request_limit}
                  onChange={(e) => setForm((f) => ({ ...f, limits: { ...f.limits, request_limit: Number(e.target.value) } }))}
                />
              </Field>
              <Field label="Araç çağrısı" htmlFor="tool-limit">
                <Input
                  id="tool-limit"
                  type="number"
                  min={0}
                  value={form.limits.tool_calls_limit}
                  onChange={(e) => setForm((f) => ({ ...f, limits: { ...f.limits, tool_calls_limit: Number(e.target.value) } }))}
                />
              </Field>
            </div>
          </Section>

          {stored && (
            <History
              current={stored}
              onRestore={(settings) => {
                setForm(settings)
                setPreset(presetOf(settings.model))
                setModels(null)
                toast.info('Sürüm forma yüklendi. Kullanmak için kaydet.')
              }}
            />
          )}

          <div className="bg-background/95 supports-[backdrop-filter]:bg-background/80 fixed inset-x-0 bottom-0 z-10 border-t backdrop-blur md:sticky md:rounded-xl md:border">
            <div className="mx-auto flex max-w-3xl items-center gap-3 p-3 pb-[max(env(safe-area-inset-bottom),0.75rem)]">
              <HistoryIcon className="text-muted-foreground hidden size-4 sm:block" />
              <span className="text-muted-foreground min-w-0 flex-1 truncate text-sm">
                {dirty ? 'Kaydedilmemiş değişiklikler var.' : stored ? `Sürüm ${stored.version} kullanımda.` : 'Henüz kaydedilmedi.'}
              </span>
              {dirty && stored && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setForm(stored.settings)
                    setPreset(presetOf(stored.settings.model))
                  }}
                >
                  Geri al
                </Button>
              )}
              <Button type="submit" disabled={!dirty || saving || !form.model.model}>
                {saving ? 'Kaydediliyor…' : 'Kaydet'}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
