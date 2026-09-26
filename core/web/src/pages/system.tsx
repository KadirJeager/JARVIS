// Installation state as the core reports it, and this device's settings:
// notifications, read-aloud voice and app install. Nothing here is assumed;
// each value is read from the server or detected on the device.
import { BellIcon, BellOffIcon, CheckCircle2Icon, PlugZapIcon, RefreshCwIcon, SendIcon, Trash2Icon, XCircleIcon } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { PageHeader } from '@/components/app-shell'
import { speechSupported } from '@/components/chat/speech'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api, errorMessage } from '@/lib/api'
import { formatDateTime, formatNumber, formatRelative } from '@/lib/format'
import { CAPABILITIES, TOOL_STRATEGIES } from '@/lib/labels'
import { currentSubscription, disablePush, enablePush, pushSupported, subscriptionId } from '@/lib/push'
import { useSignedIn } from '@/lib/session'
import type { SystemStatus } from '@/lib/types'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-2 py-1.5 text-sm sm:grid-cols-[12rem_1fr]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

function hostOf(url: string | null): string {
  if (!url) return 'Sağlayıcının varsayılan adresi'
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function useVoices(): SpeechSynthesisVoice[] {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([])
  useEffect(() => {
    if (!speechSupported()) return
    const update = () => setVoices(window.speechSynthesis.getVoices())
    update()
    window.speechSynthesis.addEventListener('voiceschanged', update)
    return () => window.speechSynthesis.removeEventListener('voiceschanged', update)
  }, [])
  return voices
}

function Notifications({ status, reload }: { status: SystemStatus; reload: () => void }) {
  const { user } = useSignedIn()
  const [thisDevice, setThisDevice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const push = status.push
  const supported = pushSupported()
  const permission = supported ? Notification.permission : 'denied'

  useEffect(() => {
    void currentSubscription().then(async (subscription) =>
      setThisDevice(subscription ? await subscriptionId(subscription.endpoint) : null),
    )
  }, [status])

  const run = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true)
    try {
      await work()
      toast.success(success)
      reload()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const test = (id?: string) =>
    run(async () => {
      const result = await api<{ outcomes: { id: string; ok: boolean; status?: number | null; removed?: boolean }[] }>(
        user,
        'POST',
        `/v1/push/test${id ? `?subscription_id=${id}` : ''}`,
      )
      const failed = result.outcomes.filter((o) => !o.ok)
      if (result.outcomes.length === 0) throw new Error('Kayıtlı cihaz yok.')
      if (failed.length) throw new Error(`${failed.length} cihaza gönderilemedi (${failed.map((f) => f.status ?? 'ağ').join(', ')}).`)
    }, 'Test bildirimi push servisine teslim edildi.')

  if (!push.configured) {
    return (
      <p className="text-muted-foreground text-sm">
        Bu kurulumda bildirim anahtarı yok. Dağıtım betiği yeniden çalıştırıldığında anahtar oluşturulur.
      </p>
    )
  }
  const registered = push.subscriptions.some((s) => s.id === thisDevice)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {!supported ? (
          <p className="text-muted-foreground text-sm">Bu tarayıcı Web Push desteklemiyor. iPhone/iPad’de önce ana ekrana ekle.</p>
        ) : registered ? (
          <>
            <Badge className="border-0 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">Bu cihaz bildirim alıyor</Badge>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => test(thisDevice ?? undefined)}>
              <SendIcon /> Test gönder
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(() => disablePush(user), 'Bu cihazda bildirimler kapatıldı.')}>
              <BellOffIcon /> Kapat
            </Button>
          </>
        ) : permission === 'denied' ? (
          <p className="text-muted-foreground text-sm">Bildirim izni bu site için engellenmiş; tarayıcının site ayarlarından izin ver.</p>
        ) : (
          <Button disabled={busy} onClick={() => run(() => enablePush(user, push.public_key), 'Bu cihazda bildirimler açıldı.')}>
            <BellIcon /> Bu cihazda bildirimleri aç
          </Button>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        Yanıt ya da hata geldiğinde bildirim gönderilir; uygulama o konuşmada açıkken gösterilmez. Test, push servisinin
        mesajı kabul ettiğini doğrular; ekranda görünmesi cihazın bildirim ayarlarına bağlıdır.
      </p>
      {push.subscriptions.length > 0 && (
        <ul className="divide-y rounded-lg border">
          {push.subscriptions.map((subscription) => (
            <li key={subscription.id} className="flex items-center gap-2 px-3 py-2 text-sm">
              <div className="min-w-0 flex-1">
                <p className="truncate">
                  {subscription.label || 'Adsız cihaz'} {subscription.id === thisDevice && <Badge variant="secondary">bu cihaz</Badge>}
                </p>
                <p className="text-muted-foreground text-xs">
                  {subscription.last_success_at
                    ? `Son teslim ${formatRelative(subscription.last_success_at)}`
                    : `Eklendi ${formatRelative(subscription.created_at)}`}
                  {subscription.last_failure && ` · son hata ${subscription.last_failure.status ?? subscription.last_failure.error}`}
                </p>
              </div>
              <Button size="icon" variant="ghost" aria-label="Test gönder" disabled={busy} onClick={() => test(subscription.id)}>
                <SendIcon />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Cihazı kaldır"
                disabled={busy}
                onClick={() => run(() => api(user, 'DELETE', `/v1/push/subscriptions/${subscription.id}`), 'Cihaz kaldırıldı.')}
              >
                <Trash2Icon />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type Probe = { state: 'idle' | 'testing' } | { state: 'ok'; count: number } | { state: 'failed'; message: string }

function ModelProbe() {
  const { user } = useSignedIn()
  const [probe, setProbe] = useState<Probe>({ state: 'idle' })
  const check = async () => {
    setProbe({ state: 'testing' })
    try {
      const { settings } = await api<{ settings: { model: { protocol: string; base_url: string | null; api_key_ref: string | null } } }>(
        user,
        'GET',
        '/v1/settings',
      )
      const result = await api<{ ok: boolean; models?: string[]; error?: string; http_status?: number }>(
        user,
        'POST',
        '/v1/connections/test',
        settings.model,
      )
      setProbe(
        result.ok
          ? { state: 'ok', count: result.models?.length ?? 0 }
          : { state: 'failed', message: `${result.error} (${result.http_status ? `HTTP ${result.http_status}` : 'yanıt yok'})` },
      )
    } catch (error) {
      setProbe({ state: 'failed', message: errorMessage(error) })
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="outline" onClick={() => void check()} disabled={probe.state === 'testing'}>
        <PlugZapIcon /> {probe.state === 'testing' ? 'Deneniyor…' : 'Bağlantıyı şimdi dene'}
      </Button>
      {probe.state === 'ok' && (
        <span className="flex items-center gap-1 text-sm text-emerald-600 dark:text-emerald-400">
          <CheckCircle2Icon className="size-4" /> Uç yanıt verdi · {probe.count} model
        </span>
      )}
      {probe.state === 'failed' && (
        <span className="text-destructive flex items-center gap-1 text-sm">
          <XCircleIcon className="size-4" /> {probe.message}
        </span>
      )}
    </div>
  )
}

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function SystemPage() {
  const { user } = useSignedIn()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const voices = useVoices()
  const lang = document.documentElement.lang
  const localVoices = voices.filter((v) => v.lang.toLowerCase().startsWith(lang))
  const standalone = window.matchMedia('(display-mode: standalone)').matches

  const load = useCallback(async () => {
    try {
      setStatus(await api<SystemStatus>(user, 'GET', '/v1/status'))
      setError(null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [user])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Sistem">
        <Button variant="ghost" size="icon" aria-label="Yenile" onClick={() => void load()}>
          <RefreshCwIcon />
        </Button>
      </PageHeader>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-4 p-3 sm:p-6">
          {error && <p className="text-destructive text-sm">Durum okunamadı: {error}</p>}
          {!status && !error && <Skeleton className="h-64 w-full" />}
          {status && (
            <>
              <Section title="Model" description="Şu an kullanılan bağlantı. Değiştirmek için Beyin sayfasına git.">
                {status.settings ? (
                  <dl>
                    <Row label="Model">{status.settings.model}</Row>
                    <Row label="Biçim">{status.settings.protocol === 'google' ? 'Gemini API biçimi' : 'OpenAI uyumlu biçim'}</Row>
                    <Row label="Uç">{hostOf(status.settings.base_url)}</Row>
                    <Row label="Araç seçimi">{TOOL_STRATEGIES[status.settings.tool_selection] ?? status.settings.tool_selection}</Row>
                    <Row label="Ayar sürümü">
                      <Link to="/brain" className="underline underline-offset-2">
                        {status.settings.version}
                      </Link>{' '}
                      · {formatDateTime(status.settings.updated_at)}
                    </Row>
                    <div className="pt-2">
                      <ModelProbe />
                    </div>
                  </dl>
                ) : (
                  <p className="text-sm">
                    Model ayarı yok.{' '}
                    <Link to="/brain" className="underline underline-offset-2">
                      Beyin sayfasından ayarla
                    </Link>
                    .
                  </p>
                )}
              </Section>

              <Section title="Yetenekler" description="Her turda asistana verilenler. “Gerektiğinde” olanlar araç aramasıyla yüklenir.">
                <ul className="divide-y">
                  {status.capabilities.map((capability) => (
                    <li key={capability.id} className="py-2">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{CAPABILITIES[capability.id]?.name ?? capability.id}</span>
                        <Badge variant={capability.loading === 'core' ? 'secondary' : 'outline'}>
                          {capability.loading === 'core' ? 'Her turda' : 'Gerektiğinde'}
                        </Badge>
                        {capability.strategy && (
                          <span className="text-muted-foreground text-xs">{TOOL_STRATEGIES[capability.strategy] ?? capability.strategy}</span>
                        )}
                      </div>
                      <p className="text-muted-foreground text-xs">{CAPABILITIES[capability.id]?.description}</p>
                      {capability.tools && <p className="text-muted-foreground font-mono text-xs">{capability.tools}</p>}
                    </li>
                  ))}
                </ul>
              </Section>

              <Section title="Bildirimler">
                <Notifications status={status} reload={() => void load()} />
              </Section>

              <Section title="Bu cihaz">
                <dl>
                  <Row label="Uygulama">{standalone ? 'Ana ekrandan açıldı' : 'Tarayıcıda açık (menüden “Uygulamayı yükle” ile eklenebilir)'}</Row>
                  <Row label="Sesli okuma">
                    {!speechSupported()
                      ? 'Desteklenmiyor'
                      : localVoices.length
                        ? `${localVoices.length} Türkçe ses (${localVoices.map((v) => v.name).slice(0, 2).join(', ')})`
                        : voices.length
                          ? 'Türkçe ses yok; tarayıcının varsayılan sesi kullanılır'
                          : 'Ses listesi henüz yüklenmedi'}
                  </Row>
                  <Row label="Sesle yazma">Klavyenin kendi sesle yazma özelliği kullanılır.</Row>
                </dl>
              </Section>

              <Section title="Kurulum">
                <dl>
                  <Row label="Hesap">{status.account.email}</Row>
                  <Row label="Adres">{status.service.service_url}</Row>
                  <Row label="Proje">{status.service.project_id}</Row>
                  <Row label="Revizyon">{status.service.revision ?? '—'}</Row>
                  <Row label="Sürüm">{status.service.build ?? '—'}</Row>
                  <Row label="Hafıza">
                    {status.memory.files} dosya · {formatNumber(status.memory.total_chars)} karakter ·{' '}
                    <Link to="/memory" className="underline underline-offset-2">
                      yönet
                    </Link>
                  </Row>
                </dl>
              </Section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
