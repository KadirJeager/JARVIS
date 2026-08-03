# Devir notu — 3 Ağustos 2026

Uzun bir otonom oturumun kapanışı. Bu belge "ne yapıldı"dan çok **neyin kanıtlandığı,
neyin kanıtlanmadığı ve sıradaki kişinin nereden devam edeceği** içindir.

## Canlı durum

| | Değer |
|---|---|
| `jarvis-brain` | `00026-b5f` — imaj `y3y4-e6fb446` |
| `jarvis-voice` | `00018-hxj` — aynı imaj |
| Geri alma noktaları | `jarvis-brain-00025-9xf`, `jarvis-voice-00017-mm4` |
| Android | `main` derlemesi S23 (`PHONE_SERIAL`) ve Tab S9 FE (`WATCH_SERIAL`) üzerinde kurulu |
| Testler | brain **702**, Android **238 JVM** + **63 instrumented** (gerçek cihazda koştu) |

Deploy kanıtı sağlık kontrolü değil: çalışan uygulamanın kendi `/openapi.json`'ı
**21 yol** listeliyor, beşi yeni onay ucu (deploy öncesi 404'tü).

## Faz durumu (North Star §12)

- **Y3 — Onay Merkezi: CANLI.** Kırmızı bölge artık çıkmaz sokak değil; engel bekleyen
  bir onaya dönüşüyor, kart sohbete düşüyor, karar verilince araç çalışıyor.
- **Y4 — CANLI:** araç kayıt defteri, araç kazanım merdiveni (`propose_tool` → onay →
  kayıt), onaylı MCP sunucularının ajana bağlanması, ajan fabrikası Kademe 1.
- **Kalan:** fabrika Kademe 2, Wear OS, ve Faz D (donanım).

Geliştirici ajanı ve Keep MCP artık **kod değil onay** işi: merdiven kurulu, ilgili MCP
sunucusu önerilip onaylandığında bağlanır.

## Bugün kapanan dört canlı hata

1. **Ses kimliği Jarvis'in kendi TTS'ini Kadir sanıyordu.** Üretim verisi teşhisi verdi:
   son 50 doğrulamanın yalnız 23'ü ACCEPT'i geçiyordu ve başarısızlar 0.10–0.24'te,
   yani repo'nun kendi *farklı-konuşmacı* fixture'ının (0.1603) bandında kümeleniyordu.
   Yarım-düpleks yankı koruması + sunucuda `allow_adapt`. Galeri **zehirlenmemişti**.
2. **FCM push HİÇ çalışmamıştı.** Android'de `firebase-messaging` bağımlılığı yoktu;
   `fcm_tokens` üretimde boştu ve her gönderim sessizce sohbet fallback'ine düşüyordu —
   Y2.4 bu yüzden "canlı doğrulandı" diye kayda geçmişti. İstemci eklendi, üretimde
   kanıtlandı (0 → 2 token, gerçek push cihazda çizildi).
3. **Sohbet listesi 25 Temmuz'dan beri 502 veriyordu.** `conversations` composite
   index'i üretimde hiç oluşturulmamış ve `firestore.indexes.json`'da hiç beyan
   edilmemişti. Testler göremezdi: `FakeDB` index kavramını bilmiyor.
4. **Her açılışta "Oturumunuz açılıyor".** 26 Temmuz'daki `setNonce` düzeltmesinin yan
   etkisi — nonce, kimlik önbelleğini tasarım gereği geçersiz kılıyor. Artık token'ın
   kendi `exp`'ine bakılıyor; taze ise Credential Manager'a hiç gidilmiyor.

## Oturumda ben sebep oldum, aynı oturumda düzelttim

**Deploy prod'u geri aldı.** Her iki servis YAML'ında 30 Temmuz'dan kalma pinli bir
traffic bloğu vardı; `services replace` onu aynen uyguladı ve trafik canlı revizyondan
30 Temmuz'unkine düştü — uygulama Y2'nin bütün uçlarını kaybetti. `/api/health` boyunca
200 döndüğü için sessiz oldu. Trafik yeni revizyona alındı, YAML'lar `latestRevision:
true` yapıldı ve olay dosyaya yorum olarak yazıldı.

**Ayrıca:** Cloud Scheduler'da yalnız `repo-watch` işi varmış. `scheduler-jobs.md`'de
belgelenen diğer beş iş hiç oluşturulmamıştı — yani Y1 ve Y2 kod olarak canlıydı ama
hiç tetiklenmiyordu. Altısı da kuruldu; ikisi zorla çalıştırılıp uygulama logunda 200
görülerek doğrulandı.

## Oturum kapanışında ölçülenler (22:53)

**Yankı düzeltmesi ÇALIŞTI — artık sayıyla.** AEC düzeltmesinden sonraki 6 doğrulama:

| | Öncesi (50 kayıt) | Sonrası (6 kayıt) |
|---|---|---|
| imposter bandı (<0.25) | 23/50 = **%46** | 1/6 = **%17** |
| ortalama skor | 0.366 | **0.4256** |
| en yüksek | 0.749 | 0.6402 |
| tanındı | 23/50 | **5/6** |

Kadir de bağımsız olarak "yankı düzeldi" dedi. Örneklem küçük (6), ama yön net ve
hipotez (TTS'i AEC referans yoluna almak) destekleniyor.

**Onay kartı: SUNUCU TARAFI ÇALIŞIYOR, sorun İSTEMCİDE.** Bu ayrım ölçüldü:

- audit: `19:52:47 tool=cancel_reminder zone=red karar=block` — politika doğru çalıştı
- `approvals` koleksiyonu: **1 doküman**, `status=pending`,
  `tool_args={'reminder_id': 'ZvHISmfHoreoe6HvHFch'}`

Yani kırmızı bölge → engel → onay kaydı zinciri uçtan uca kuruldu. Kart Android'de
görünmüyor. Sıradaki oturum doğrudan istemciye bakmalı (sunucuyu tekrar kurcalamasın):
`ChatViewModel.syncApprovals` / `pinnedApprovals` / kartın çizim yolu. İnceleme için
bekleyen gerçek bir onay kaydı Firestore'da duruyor.

**Ses mimarisi kararı verildi:** cihaz üstü kalıyor, Live API'ye geçilmiyor;
iyileştirmeler devam edecek.

## KANITLANMADI — sıradaki kişinin ilk işi

- ~~Yankı düzeltmesinin akustik doğrulaması~~ — **ÖLÇÜLDÜ, yukarı bakın.** Kalan: örneklem
  küçük, birkaç gün sonra tabloya tekrar bakıp %17'nin sabit olup olmadığını görmek.
- **TTS'in AEC yoluna alınması bir hipotez.** Mikrofon tarafı `VOICE_COMMUNICATION`
  kullanıyordu ama TTS varsayılan `STREAM_MUSIC`'ten çalıyordu; artık ikisi aynı yolda.
  AEC davranışı HAL'e özgü, cihazda ölçülmeli.
- **`EXTRA_BIASING_STRINGS` de hipotez.** API 33, kurulu SDK'da doğrulandı, ama AOSP
  değerin tipini dokümante etmiyor ve kardeş sabitte "etkisi olmayabilir" yazıyor.
  Kadir "Jarvis artık doğru yazılıyor" dedi — tek gözlem, tekrar edilmeli.
- **Onay kartı Android'de görünmüyor** (sunucu tarafı kanıtlanmış durumda, yukarı bakın).
  Bu, yeni oturumun İLK işi.
- **Fabrika tavanları ölçülmedi.** Adım/süre tavanları gerekçeli tahmin. Bilinen ve
  belgelenmiş açık: senkron bir araç çağrısı event loop'u bloklar, süre tavanı onu
  kesemez (`factory.py` docstring'inde yazılı). Gerçek çözüm çağrıları
  `asyncio.to_thread`'e taşımak — ayrı dilim.

## Açık stratejik karar — ses mimarisi

Kadir "Gemini uygulamasının birebir aynısı olsun" diyor ve **haklı**: Gemini Live ham
sesi akıtıyor (native audio), bugünkü protokol v2 ise Google'ın terk ettiği
STT→LLM→TTS boru hattı.

Araştırma sonucu (kanıtlarıyla `~/.agents-shared/LESSONS.md` ve oturum kaydında):

- "Live API OAuth ile çalışmaz" **yanlış** — Vertex AI Live API *sadece* OAuth2 bearer
  ile çalışır.
- Ama **bu projenin abonelik-proxy'si üzerinden çalışmaz**: CLIProxyAPI v7.2.111'de
  `BidiGenerateContent` hiç geçmiyor ve abonelik upstream'i yalnız üç HTTP metodu
  sunuyor. Kaynak seviyesinde kanıtlı.
- **Vertex abonelikten harcamaz**, GCP projesine faturalanır (~$0.023/dk → günde 30 dk
  ≈ aylık ~$21). Bu, 30 Temmuz'da bilinçli olarak çıkılan yere dönmek demek ve §13'ün
  "boşta ~0" ilkesiyle çelişir.

**Öneri:** ucuz düzeltmeler ölçülmeden Live API'ye dönülmesin. Ölçüm kötü çıkarsa doğru
yol **hibrit**: metin abonelik-OAuth'ta kalır, ses Vertex AI service account'a ayrılır.
Bu bir bug fix değil, §13 maliyet modelini değiştiren bir yön kararı — Kadir'in.

## Ek — aynı gece 23:10: onay kartı vakası KAPANDI (teşhis düzeltmesi)

Yukarıdaki "sorun istemcide" sonucu **yanlıştı**. İstemcide hata yok; kart artık ekranda.

Kanıt zinciri (yeni oturum, 23:00–23:10):

1. Firestore dokümanı eksiksiz: `title` var, `user_id=owner@example.com`,
   `created_at=19:52:47Z` (=22:52 yerel), `expires_at=20:52Z` (=23:52 yerel).
2. `list_pending`'in birebir sorgusu Firestore'a karşı elle koşuldu: kayıt dönüyor.
3. Cloud Run istek logları: Kadir'in uygulama kontrollerinin TAMAMI (18:27–19:41Z =
   21:27–22:41 yerel) `GET /api/approvals` için **404** aldı — çünkü istekleri
   **Y3-öncesi revizyonlar** servis etti (`00025-9xf`, 19:41'de `00016-pur`).
   İstemci 404'ü tasarım gereği "bu sunucuda onay merkezi yok" sayar ve sessizce
   boş kuyruk gösterir (`ApprovalRepository.pending`) — kartsızlığın ve hatasızlığın
   tam açıklaması.
4. Admin audit: `ReplaceService` 19:37:58Z trafiği yine `00016-pur`'a sardı;
   19:43:57Z'deki ikinci replace düzeltti. Onay kaydı 19:52:47Z'de (düzeltmeden
   SONRA, ses oturumundan) kuruldu; oturum boyunca uygulama bir daha açılmadığı
   için kuyruk hiç yeniden okunmadı.
5. Yeni oturumda soğuk başlatma (23:06): kart S23 ekranında — "Onay bekliyor",
   `'cancel_reminder' çalıştırılsın mı?`, Onayla/Reddet. Sunucu logu:
   `20:05:58Z GET /api/approvals → 200`, revizyon `00026-b5f`.

Onay kaydına DOKUNULMADI — karar Kadir'in (kart 23:52 yerele kadar karar alabilir).

Not edilen tasarım gözlemi (değiştirilmedi): istemcinin 404→boş-liste toleransı,
sürüm kayması için bilinçli bir seçim ama bu olayda gerçek bir prod kesintisini de
sessizleştirdi. Trafik kök nedeni YAML'da düzeltilip belgelendiği için istemci
tarafında değişiklik yapılmadı.

## Küçük notlar

- Sohbet listesindeki `%20`'li iki başlık 30 Temmuz curl smoke testlerinden kalma eski
  veri; canlı hata değil, listeden × ile silinebilir.
- Ağaçta commit edilmemiş iki şey Kadir'in: `YourDialer/` ve `jarvis-profil.md`.
- Bu oturumun dersleri ortak ledger'a işlendi; ayrıca global `~/.claude/CLAUDE.md`'ye
  "Don't rebuild what exists" kuralı eklendi (Kadir'in düzeltmesi: mekanizma inşa
  etmeden önce araştır).
