# Wear OS İstemcisi — Saat Eşi (North Star §4 "Wear OS eşi", Katman 2 kuyruğu)

Tarih: 2026-08-04 · Durum: Kadir onayladı (sohbette, 05:23; kalıcı token revizyonu dahil)
Kapsam: `brain/` (W0) + yeni `android/wear` modülü (W1, W2)

## 1. Amaç ve bağlam

Katman 2'nin "saatten komut veriyorum" ölçütü hâlâ boş. Hedef cihaz **Galaxy Watch
Ultra (2024) LTE — Wear OS 5, API 34**. North Star tanımı: "Aynı borunun saat
karşılığı (ses + bildirim)" — saat da telefon gibi **sıfır zekâlı bir kabuktur**
(İlke 1), Jarvis'in API'sine bağlanır.

**Kadir'in iki bağlayıcı şartı (05:09 + 05:19):**

1. **Emülatör-önce.** Geliştirme ve test YALNIZ Wear OS 5 (API 34) emülatöründe.
   Gerçek saate kurulum, "günlük kullanım seviyesi" çıtası emülatörde kanıtlanıp
   Kadir onay verene kadar YASAK. Saatte adb cambazlığı istenmiyor.
2. **Kalıcı oturum.** Saat telefona saat başı muhtaç olmayacak: brain-basımı,
   kayar süreli cihaz token'ı (§4). Google ID token yalnız basım anında kullanılır.

**Günlük kullanım çıtası (Kadir: "tüm özellikler"):** dördü de emülatörde uçtan uca
çalışmadan saate tek bayt gitmez — (a) sesli komut + cevap, (b) bildirimler saatte,
(c) onay kartına saatten Onayla/Reddet, (d) yazılı hızlı komutlar.

## 2. Mimari karar — standalone saat + kalıcı cihaz token'ı

Değerlendirilen üç yaklaşımdan A seçildi (05:12 sunumu):

- **A (SEÇİLDİ) — Standalone:** saat, brain REST'ine doğrudan konuşur; kimlik
  kalıcı cihaz token'ı. LTE saatinin değeri korunur; emülatörde eşleşmesiz test.
- **B — Companion relay (RED):** REST dururken özel relay protokolü inşa etmek
  "var olanı yeniden inşa etme" hatası; telefon yokken saat ölür; her test
  eşleşme ister.
- **C — Tam ses paritesi / saatten speaker-ID (ERTELENDİ):** ses kimliği ve yankı
  kuralları telefon kanalına kalibre; saat mikrofonu ayrı dilim. v1'de saat
  kanalında ses kanıtı YOKTUR ve güven füzyonu bunu zaten hesaba katar
  (kanal-dayanıklı tasarım); kırmızı bölge her hâlükârda onay kartındadır.

## 3. Dilimler

- **W0 — brain: cihaz token altyapısı** (bas / doğrula / iptal, kayar süre)
- **W1 — saat çekirdeği:** modül iskeleti + kimlik + sesli komut + hızlı komutlar
- **W2 — onaylar + bildirimler:** saat-yerel onay ekranı + bildirim stratejisi

Her dilim kendi planıyla gider; W0 tek başına deploy edilebilir ve telefon
uygulamasını etkilemez (yeni uçlar ekler, mevcut davranışı değiştirmez).

## 4. W0 — Cihaz token altyapısı (brain)

### 4.1 Veri modeli — `device_tokens` koleksiyonu

Doküman kimliği = token'ın SHA-256 hex'i (düz token sunucuda ASLA saklanmaz).

```
{
  token_hash:   str,   # doc id ile aynı (SHA-256 hex)
  email:        str,   # basan kimlik (allowlist'ten)
  device:       str,   # "watch-ultra" — insan-okur etiket
  created_at:   str,   # ISO-8601 UTC
  last_used_at: str,
  expires_at:   str,   # KAYAR: her başarılı kullanımda now + SLIDING_DAYS
  revoked:      bool,
  revoked_at:   str | None,
}
```

- Token biçimi: `jdt_` + 32 bayt `secrets.token_urlsafe` (≈43 karakter gövde).
  `jdt_` öneki, doğrulama katmanının Google yolu ile kayıt-defteri yolunu tek
  bakışta ayırmasını sağlar (`kind:` önekiyle aynı isim-uzayı fikri).
- `SLIDING_DAYS = 30`: son kullanımdan 30 gün sonra kendiliğinden ölür;
  kullanıldıkça yaşar. Mutlak üst sınır YOK (bilinçli: tek kullanıcı, iptal tek
  istek, `last_used_at` audit izi var).

### 4.2 Uçlar

- `POST /api/device-tokens` — **yalnız taze Google ID token'la** (`require_user`
  Google dalı; cihaz token'ıyla YENİ token basılamaz — token token doğuramaz,
  fabrika recursion yasağının kimlik düzlemi izdüşümü). Body: `{device: str}`.
  Dönüş: `{token: "jdt_...", id: <hash>, device, expires_at}` — düz token
  YALNIZ bu cevapta görünür.
- `GET /api/device-tokens` — kayıt listesi (hash-id, device, created/last_used/
  expires, revoked; düz token asla). Telefondan görünürlük için.
- `DELETE /api/device-tokens/{id}` — iptal: `revoked=true` damgalanır, kayıt
  SİLİNMEZ (tool/agent registry ile aynı geri-alınabilirlik deseni). İptal ucu
  da yalnız taze Google ID token'la çalışır (çalınan saat kendi token'ını
  koruyamasın).

### 4.3 Doğrulama yolu

`auth.verify_bearer_email(token, db) -> str` yeni ortak kapı:

1. Token `jdt_` ile başlıyorsa: SHA-256 → `device_tokens` dokümanı; `revoked`
   veya süresi geçmişse RED (`PermissionError("Geçersiz oturum")` — Google
   yoluyla AYNI mesaj, hangi şemanın reddettiği dışarı sızmaz). Geçerliyse
   `last_used_at` + kayar `expires_at` güncellenir ve `email` döner.
2. Değilse mevcut `verify_token_email` (Google) aynen.

`require_user` (HTTP) ve ses WS handshake'i bu ortak kapıya bağlanır — saat
ileride (dilim C) sesli oturumu bedavaya açar. Firestore erişilemezse
fail-closed: cihaz token'ı REDDEDİLİR (401), Google yolu etkilenmez.

Kayar güncelleme "best-effort" DEĞİLDİR: yazım hatası doğrulamayı düşürmez ama
`WARNING` loglanır (sonuçsuz best-effort yasağı — 4 Ağu FCM dersi).

### 4.4 Güvenlik değerlendirmesi (bilinçli dengeler)

- Uzun ömürlü bearer token'ın dengeleri: Keystore-destekli şifreli depo (saat
  tarafı, §5), bilek kilidi, sunucudan tek-istek iptal, kayar süre (30 gün
  atalet ölümü), `last_used_at` izi, tek-kullanıcı kapsam
  ([[kapsam-tek-kullanici]]).
- Basım ve iptal Google-taze-token'a kilitli; cihaz token'ı kendi soyunu
  yönetemez.
- Saat kanalında ses kanıtı yok → güven füzyonunda kanal düşük güvenli;
  kırmızı bölge onay kartlarının arkasında (o kartı saatten onaylamak da W2'de
  yine Kadir'in parmağıyla).

## 5. W1 — Saat çekirdeği (`android/wear` modülü)

- **Modül:** `android/wear`, Compose for Wear OS (+ Horologist), minSdk 34
  (Watch Ultra = Wear OS 5/API 34; tek kullanıcı, geriye uyum yükü yok),
  compileSdk/targetSdk telefonla hizalı (36). Paket `com.jarvis.wear`.
  Kütüphane sürümleri plan yazımında güncel resmî dokümandan doğrulanır
  (hızlı-değişen alan — global kural).
- **Kimlik:** telefon uygulamasına "Saati eşleştir" eylemi eklenir: telefon
  `POST /api/device-tokens` ile basar, Wearable `MessageClient` ile saate iter;
  saat Keystore-destekli şifreli yerel depoya yazar (somut kütüphane seçimi
  plan-anı doğrulaması — `androidx.security-crypto`'nun güncel durumu kontrol
  edilecek). Köprü YALNIZ bu tek seferlik akışta kullanılır.
- **Sesli komut:** mikrofon butonu → sistem `RecognizerIntent` STT → metin →
  `POST /api/chat` → cevap ekranda + saat TTS'iyle seslendirilir. Ses kaydı
  sunucuya GİTMEZ (v1 — §2/C).
- **Hızlı komutlar:** ana ekranda 3-4 hazır komut ("durum raporu",
  "hatırlatmalarım", "bekleyen onaylar" vb. — metinleri planda sabitlenir),
  aynı chat ucuna tek dokunuş; ek olarak bir Tile (saat yüzünden tek kaydırma).
- **Oturum/UX sınırı dürüst:** token yoksa/iptal edilmişse saat "Telefondan
  eşleştir" ekranı gösterir; sessiz boş ekran YOK (25 Tem "silent spinner"
  dersi sınıfı).

## 6. W2 — Onaylar + bildirimler

- **Onay ekranı:** `GET /api/approvals` listesi; kart detayı (başlık + detay
  metni) saat ekranına uygun sadelikte; Onayla/Reddet →
  `POST /api/approvals/{id}/approve|reject`. Sunucu tarafı değişmez (uçlar Y3'ten
  beri canlı ve tür-bağımsız).
- **Bildirim stratejisi ÖLÇÜMLE seçilir:** önce telefon push'unun saate varsayılan
  köprülenmesi emülatör eşleşmesinde ölçülür. Yeterliyse v1 bedava. Değilse (ör.
  LTE-yalnız senaryo körlüğü) saat kendi FCM token'ını `/api/fcm/register`'a
  kaydeder — dispatch çoklu token + ölü token budamayı zaten biliyor (4 Ağu).
  Karar ölçüm sonucuyla spec'e ek not olarak işlenir.

## 7. Emülatör test stratejisi ve saate yükleme çıtası

- **AVD:** Wear OS 5 (API 34) — gerçek saatle aynı seviye. Mikrofon host'tan
  geçer (sesli akış test edilebilir); TTS emülatörde çalışır.
- **Eşleştirme testi (tek seferlik kimlik akışı):** birincil yol S23 ↔ Wear AVD
  eşleşmesi (adb yalnız TELEFON tarafında — Kadir'in günlük akışı). Bu akış
  plan-anı doğrulanır; pürüzlüyse yedek yol telefon-AVD (Play imajlı, Kadir'in
  hesabıyla) ↔ Wear AVD eşleşmesi — emülatör-emülatör eşleşmesi Android
  Studio'da desteklenir.
- **Gerçek saate kurulum kapısı (HITL):** dört özellik + şu senaryolar
  emülatörde kanıtlı olacak: token süresi dolunca davranış, telefon
  erişilemezken davranış (token hâlâ geçerli → çalışır; eşleştirme gerekiyorsa
  dürüst ekran), uçak modu/ağ yok. Kapıyı Kadir açar; kurulum tek seferliktir
  ve sonrasında saat güncellemeleri de aynı çıtaya tabidir.

## 8. Test planı

- **W0 (brain, TDD + FakeDB):** basım (401'siz olmaz, cihaz token'ıyla basım
  reddi, düz token yalnız cevapta), doğrulama (geçerli/revoked/süresi-geçmiş/
  bozuk önek; kayar sürenin İLERLEDİĞİ; Google yolunun etkilenmediği), iptal
  (damga, silme yok), WS handshake'in ortak kapıdan geçtiği, Firestore hatasında
  fail-closed. Uç testleri gerçek FastAPI test-client'ıyla (mevcut
  `test_approvals_api` deseni).
- **W1/W2 (wear, JVM + emülatör):** ViewModel/repo birim testleri JVM'de
  (telefon modülü deseni); uçtan uca akışlar Wear AVD'de elle + mümkünse
  birkaç instrumented test. Emülatör doğrulaması ekran görüntüsüyle biter
  (3 Ağu dersi: ürün-görünür işte kanıt ekran görüntüsüdür).

## 9. Bu işe özgü başarısızlık modları

1. **Wear emülatöründe Google hesabı yok** → kimlik bu yüzden telefon-basımlı
   kalıcı token; emülatör eşleşmesi yalnız tek seferlik basım akışının testinde
   gerekir, günlük akışta gerekmez.
2. **MessageClient yalnız bağlı düğümle konuşur** → köprü yalnız eşleştirme
   anında kullanılır; kalıcı token bu bağımlılığı bilinçli olarak yok eder.
   Eşleştirme ekranı "telefon bağlı değil" durumunu açıkça söyler.
3. **Köprülenmiş bildirime körü körüne güven** → W2'de ölçüm önce; LTE-yalnız
   senaryoda sessiz körlük çıkarsa saat-yerel FCM'e geçilir (sonuçsuz
   best-effort yasağı).
4. **Emülatör-gerçek cihaz farkı** (Samsung One UI Watch katmanı) → API
   seviyesi birebir tutulur; One UI'a özgü davranış farkı çıkarsa gerçek saate
   geçiş kapısında yakalanır ve nota işlenir — emülatör yeşili "kanıt", gerçek
   saat yeşili "kapanış"tır.

## 10. Canlı kanıt ölçütü ("bitti" tanımları)

- **W0 bitti:** deploy + `/openapi.json` 23 yol (21+2 — üç operasyon iki path
  anahtarı paylaşır: `/api/device-tokens` POST+GET tek anahtardır) + uydurma `jdt_` token
  401 alıyor + Google yolu regresyonsuz (telefon uygulaması çalışmaya devam
  ediyor). Gerçek mint→chat 200→revoke→401 zinciri taze Google ID token
  gerektirdiğinden W1'in eşleştirme akışıyla kanıtlanır (telefon basar) —
  W0'da bu zincir uç testlerinde pinlidir, canlıda W1'de kapanır.
- **W1 bitti (emülatörde):** Wear AVD'de eşleştirme → sesli komut → cevap
  ekranda+seste; hızlı komut tek dokunuşla cevap getiriyor; ekran görüntüleri.
- **W2 bitti (emülatörde):** gerçek bir bekleyen onay saat ekranında; Onayla
  saatten → sunucuda `approved` + yürütücü koştu; bildirim stratejisi ölçüldü
  ve karar yazıldı.
- **Saate kurulum:** ayrı HITL kapısı (§7) — bu spec'in parçası ama hiçbir
  dilimin "bitti"sine dahil değil.
