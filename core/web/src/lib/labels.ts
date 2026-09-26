// Owner-facing wording for states, errors, tools and capabilities the core
// reports. Unknown values are shown as they arrive rather than guessed.
import type { ActivityItem, TurnError, TurnStatus } from '@/lib/types'

export const TURN_STATUS: Record<TurnStatus, string> = {
  queued: 'Sırada',
  running: 'Çalışıyor',
  completed: 'Tamamlandı',
  failed: 'Başarısız',
  reconciling: 'Kontrol bekliyor',
  cancelled: 'İptal edildi',
}

// Hints for known error types and provider reason codes (matched in the technical text).
const ERROR_HINTS: [string, string][] = [
  ['SettingsMissing', 'Model ayarı yok; Beyin sayfasından bir bağlantı kaydet.'],
  ['VALIDATION_REQUIRED', 'Model sağlayıcısı hesabın doğrulanmasını istiyor; aynı hesapla sağlayıcının resmî uygulamasından doğrula.'],
  ['RESOURCE_EXHAUSTED', 'Model kotası dolu; kota yenilenince tekrar dene.'],
  ['HTTP 429', 'Model sağlayıcısı istek sınırına ulaştı; biraz sonra tekrar dene.'],
  ['PERMISSION_DENIED', 'Model sağlayıcısı isteği reddetti; anahtarı ve hesap iznini kontrol et.'],
  ['HTTP 401', 'Model anahtarı geçersiz; Beyin sayfasında anahtarı yenile.'],
  ['UsageLimitExceeded', 'Tur sınırına ulaşıldı; Beyin sayfasından sınırları artırabilirsin.'],
  ['SecretResolutionError', 'Anahtar okunamadı; Beyin sayfasında anahtar seçimini kontrol et.'],
  ['AttemptsExhausted', 'Tur birkaç kez denendi ve bitirilemedi.'],
  ['HistoryMissing', 'Konuşmanın önceki kaydı bulunamadı.'],
  ['unresolved_tool_effects', 'Bir aracın işini yapıp yapmadığı bilinmiyor. Sonucu kontrol etmeden tekrar gönderme.'],
]

export function errorHint(error: TurnError | { type?: string; detail?: string }): string | null {
  const technical = `${error.type ?? ''}: ${error.detail ?? ''}`
  return ERROR_HINTS.find(([code]) => technical.includes(code))?.[1] ?? null
}

export function errorTechnical(error: { type?: string; detail?: string }): string {
  return [error.type, error.detail].filter(Boolean).join(': ')
}

export const NOTICE_TEXT: Record<string, string> = {
  cancelled: 'İptal edildi.',
  closed_by_owner: 'Kontrol edildi ve kapatıldı.',
}

type ToolLabel = { running: string; done: string; detail?: (args: Record<string, unknown>) => string | undefined }

const str = (value: unknown) => (typeof value === 'string' ? value : undefined)

// Tools of the capabilities the core ships (see `capability_manifest`).
const TOOL_LABELS: Record<string, ToolLabel> = {
  search_tools: {
    running: 'Uygun araç aranıyor',
    done: 'Araç arandı',
    detail: (a) => (Array.isArray(a.queries) ? a.queries.join(', ') : str(a.query)),
  },
  web_fetch: { running: 'Web sayfası okunuyor', done: 'Web sayfası okundu', detail: (a) => str(a.url) },
  read_memory: { running: 'Hafıza okunuyor', done: 'Hafıza okundu', detail: (a) => str(a.path) },
  write_memory: { running: 'Hafızaya yazılıyor', done: 'Hafıza güncellendi', detail: (a) => str(a.path) },
  delete_memory: { running: 'Hafızadan siliniyor', done: 'Hafızadan silindi', detail: (a) => str(a.path) },
  search_memory: { running: 'Hafızada aranıyor', done: 'Hafızada arandı', detail: (a) => str(a.query) },
}

export function parseArgs(args: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(args)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

export function describeActivity(item: ActivityItem): { title: string; detail?: string } {
  const label = TOOL_LABELS[item.tool]
  const detail = label?.detail?.(parseArgs(item.args))
  if (!label) return { title: item.tool, detail: item.args }
  return { title: item.state === 'running' ? label.running : label.done, detail }
}

export const CAPABILITIES: Record<string, { name: string; description: string }> = {
  memory: {
    name: 'Hafıza',
    description: 'Bulut hafızasını okur, arar ve günceller. Ana hafıza dosyası her tura eklenir.',
  },
  tool_search: {
    name: 'Araç arama',
    description: 'Temel olmayan araçları gerektiğinde bulur ve yükler; model tüm araçları baştan görmez.',
  },
  web_fetch: {
    name: 'Web sayfası okuma',
    description: 'Bir bağlantının içeriğini sunucuda okur (yerel ve özel ağ adresleri engellidir).',
  },
  step_persistence: {
    name: 'Kalıcı tur kaydı',
    description: 'Her adımı kaydeder; kesintiden sonra kaldığı yerden devam eder, belirsiz araç işini tekrar etmez.',
  },
}

export const TOOL_STRATEGIES: Record<string, string> = {
  builtin: 'Yerleşik araç arama',
  typesafe_jev: 'TypeSafe Jev',
}
