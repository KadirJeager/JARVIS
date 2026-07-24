# Katman 2b — Native Android Sohbet + Kalıcı Oturum (Tasarım / Spec)

**Tarih:** 2026-07-24
**Durum:** Onaylandı (brainstorming) → writing-plans'e hazır
**Kapsam:** Katman 2b'nin **ilk dikey dilimi**. 2b vizyonunun kalanı (sesli mod, ASSIST/güç tuşu asistanı, onay merkezi) ayrı dilimlerde, ayrı spec'lerde.

İlke: **idareten/geçici çözüm yok**; her parça production-grade, araştırmayla kanıtlanmış, teknik borç bırakmayan, North Star'a ("ikame") hizmet eden. (Bkz. memory `nihai-amaca-uygunluk`.)

---

## 1. Amaç

Kadir'in gerçek Android telefonunda, PWA'nın çözemediği iki sorunu kökten çözen native bir istemci:

1. **Kalıcı oturum** — uygulama her açılışta tekrar giriş istemeyecek (Credential Manager + silent re-auth).
2. **Kalıcı, tutarlı sohbet** — geçmiş balonlar ve Jarvis'in konuşma bağlamı, uygulama/sunucu yeniden başlasa da korunacak (backend'de kalıcı `messages` + cold-start rehydration).

Başarı ölçütü: Telefonda uygulama açılır, giriş **hatırlanır**, önceki mesajlar **görünür**, "az önce konuştuğumuz konuyu özetle" dendiğinde Jarvis **hatırlar** — hepsi cold-start sonrası dahil.

## 2. Kapsam

**Dahil:**
- Native Kotlin + Jetpack Compose uygulaması (`android/`), tek Gemini-benzeri sohbet yüzeyi.
- Google Sign-In via Credential Manager + silent re-auth (kalıcı giriş).
- Backend'de kalıcı mesaj deposu (`messages` koleksiyonu) + `GET /api/history` + cold-start rehydration.

**Dışında (bilinçli — sonraki dilimler/katmanlar):**
- Sesli mod (2a'daki WS'i native'e taşıma) — sonraki 2b dilimi.
- ASSIST intent / güç tuşu asistanı, onay merkezi — sonraki 2b dilimleri.
- Çoklu konuşma başlığı (birden çok session_id yönetimi), çok-cihaz gerçek-zamanlı senkron — Katman 3+.
- Wear OS — Katman 2c.

### 2.1 Yol haritası — Gemini app dilim eşlemesi

2b+ hem **UI/UX** hem **özellik yol haritası** olarak Google Gemini Android uygulamasını (2026 redesign) referans alır — kararları sıfırdan icat etmek yerine kanıtlanmış bir modeli taklit ederiz. **Marka Jarvis'tir** (LogoKJ, kendi paleti); Gemini'nin logosu/ismi/birebir görsel varlıkları kullanılmaz — yalnızca UX kalıpları ve genel layout dili.

| JARVIS dilimi | Gemini karşılığı | Ne zaman |
|---|---|---|
| **1 (bu MVP)**: tek sohbet + kalıcı oturum | Ana chat yüzeyi + Library (geçmiş) çekirdeği | **şimdi** |
| 2: çoklu konuşma + arama | Yan çekmece (New Chat, Search chats, Library) | sonra |
| 3: native ses **+ ses kişiselleştirme + konuşmacı tanıma** | Gemini Live + **kişiselleştirilebilir asistan sesi (her türlü)** + **Kadir'in sesini tanıma** (diarization + speaker-ID) — 2a'yı native'e taşır | sonra |
| 4: multimodal giriş | Images / Videos | sonra |
| JARVIS-özel: ASSIST/güç tuşu, onay merkezi | kısmen "araçlar / bağlı uygulamalar" | sonra |

> **Kesin gereksinim (Kadir, 24 Tem 2026):** Ses diliminde (Dilim 3) asistan Kadir'i **sesinden tanımalı** — konuşmacı diarization (ses akışında kaç kişi konuştuğunu ayırt etme) + Kadir'in sesini doğrulama (speaker verification / voiceprint enrollment). Gemini Live'ın bunu sağlamadığı varsayılıyor → ayrı bir konuşmacı-kimlik pipeline'ı gerekebilir (speaker embedding ya da Cloud STT diarization); ses dilimi spec'inde araştırılıp tasarlanacak. **Bu dilimi (text chat) etkilemez.** Bkz. memory `kadir-ses-kimlik`.

> **Kişiselleştirilebilir asistan sesi (Kadir, 24 Tem 2026):** Ses diliminde asistanın **çıktı sesi kişiselleştirilebilir** olmalı — her türlü ses/ton, sabit tek ses değil. Gemini Live prebuilt sesler (`voiceConfig`/`prebuiltVoiceConfig`) hazır-seçimi karşılar; "her türlü" (özel/klonlanmış ses) ayrı bir TTS / voice-cloning pipeline gerektirebilir — ses dilimi spec'inde araştırılacak. **Bu dilimi (text chat) etkilemez.** Bkz. memory `asistan-ses-kisisellestirme`.

Bu dilimin **kalıcı oturumu**, Gemini'nin "Library/geçmiş" kavramının çekirdeğidir: tek kalıcı `session_id`, Dilim 2'de çoklu konuşmaya doğal olarak genişler (bu yüzden şimdiden `user_id`+`session_id` ile anahtarlanıyor).

## 3. Mimari genel bakış

Monorepo, iki ayrı toolchain:

```
JARVIS/
  brain/      (mevcut Python/FastAPI backend — küçük, additive değişiklik)
  android/    (YENİ — Kotlin + Compose, Gradle)
  web/        (mevcut PWA — dokunulmuyor; 2b devreye girince emekliye ayrılır)
```

Backend, native istemci için **değiştirilmiyor**, yalnızca **genişletiliyor** (yeni endpoint + kalıcı depolama). Mevcut auth sözleşmesi aynen korunuyor: `Authorization: Bearer <Google ID token>`, audience = Web client ID ([auth.py](../../../brain/app/auth.py)).

## 4. Android bileşen sınırları (temiz mimari)

Her modül tek sorumluluk; iyi tanımlı arayüzle konuşur; bağımsız test edilebilir.

| Katman | Modül | Ne yapar | Neye bağlı |
|--------|-------|----------|------------|
| data/auth | `AuthManager` | `signIn()`, `silentSignIn()`, `currentToken()` — Credential Manager sarmalayıcı | androidx.credentials, googleid |
| data/net | `JarvisApi` | Retrofit arayüzü: `chat()`, `history()` | Retrofit/OkHttp |
| data/net | `AuthInterceptor` | Her isteğe `Bearer` enjekte; `401`'de silent re-auth + tek retry | `AuthManager` |
| data/chat | `SessionStore` | DataStore'da **kalıcı session_id** (UUID, ilk açılışta üretilir) | DataStore |
| data/chat | `ChatRepository` | `loadHistory()`, `send()` — API + session_id orkestrasyonu | `JarvisApi`, `SessionStore` |
| ui/chat | `ChatViewModel` | `StateFlow<ChatUiState>` — mesaj listesi, gönderim, yükleme/hata durumları | `ChatRepository`, `AuthManager` |
| ui/chat | `ChatScreen` | Compose: mesaj listesi + input bar (Gemini-benzeri tek yüzey) | `ChatViewModel` |
| ui/auth | `SignInScreen` | İlk giriş / silent-fail düşüşü; LogoKJ branding | `AuthManager` |
| — | `MainActivity`, `JarvisApp` | Compose host + manuel DI kökü | tümü |

**DI:** MVP küçük olduğu için manuel constructor injection (Hilt eklemek şu an gereksiz — YAGNI). Bağımlılıklar tek kökte (`JarvisApp`) kurulur; büyüdüğünde Hilt'e geçiş sınırları korur. Bu bir borç değil, kapsamlı bir sadeleştirme.

## 5. Teknik seçimler (production-grade, gerekçeli)

| Seçim | Karar | Neden |
|-------|-------|-------|
| Dil/UI | Kotlin + Jetpack Compose | Modern Android standardı; native ASSIST/güç tuşu (sonraki dilim) buna oturur |
| HTTP | Retrofit + OkHttp | De-facto olgun; `Interceptor` ile temiz `Bearer` + retry |
| Serialization | kotlinx.serialization | Resmi, reflection'sız, Compose ekosistemiyle uyumlu |
| Async | Coroutines + Flow | Compose'un doğal state modeli |
| Yerel depo | DataStore (Preferences) | session_id için; SharedPreferences'ın modern halefi |
| Auth | Credential Manager (`androidx.credentials` + `googleid`) | Google'ın güncel önerdiği yol; eski `GoogleSignInClient` **değil** |

Kesin bağımlılık **sürümleri** (Compose BOM, AGP, Kotlin, credentials, retrofit) plan aşamasında resmi kaynaktan pinlenecek — özellikle **AGP ↔ JDK uyumu** (ortamda JDK 26 var; AGP muhtemelen JDK 17/21 ister → ayrı JDK gerekecek).

### 5.1 UI referansı — Gemini 2026 estetiği

MVP'nin tek sohbet yüzeyi, Gemini app'in 2026 redesign'ını referans alır: **minimalist, immersive tek sohbet**, temiz boşluk, büyük okunur metin, yumuşak gradyan zemin — ama Gemini'nin mavi-beyazı yerine **Jarvis paleti + LogoKJ**. Compose UX kalıpları için açık kaynak referanslar: Google'ın resmi [Androidify](https://android-developers.googleblog.com/2025/09/androidify-ai-gemini-android-jetpack-compose-firebase-camerax.html) (AI-first Compose kalıpları) ve [GetStream/gemini-android](https://github.com/GetStream/gemini-android) (Compose chat UI). Bunlar **kalıp/desen** referansıdır, kod veya marka kopyası değil.

Kesin görsel dil (renk token'ları, tipografi ölçeği, mesaj-balonu bileşenleri, input bar) implementation aşamasında **frontend-design** skill'iyle şekillenecek. Görsel doğrulama headless emulator ekran görüntüsünden yapılır (§11).

## 6. Backend eklemeleri (brain/)

### 6.1 Kalıcı mesaj deposu — `app/messages.py` (YENİ)

Tek sorumluluk: sohbet turlarını Firestore'da kalıcı biriktirmek. Mevcut `Memory`/`FirestoreAudit`'ten ayrı modül (temiz sınır).

```
class MessageStore:
    append(user_id: str, session_id: str, role: str, text: str) -> None
    history(user_id: str, session_id: str, limit: int) -> list[dict]  # ts artan
```

**Firestore şeması** — koleksiyon `messages`, her mesaj bir doküman:

| Alan | Tip | Not |
|------|-----|-----|
| `user_id` | str | require_user'dan gelen doğrulanmış email |
| `session_id` | str | client'tan; **sanitize edilir** (bkz. §9) |
| `role` | str | `"user"` \| `"model"` |
| `text` | str | mesaj içeriği |
| `ts` | server timestamp | sıralama anahtarı |

**Gerekli composite index:** `(user_id, session_id, ts)`. Bu index tanımı `firestore.indexes.json` benzeri bir dosyaya yazılır ve deploy'a dahil edilir (idareten "elle oluştur" değil).

`run_turn` içindeki mevcut `snapshot_session` **çağrısı** (yalnızca son turu yazan) `MessageStore.append` ile değiştirilir — artık tam transcript birikir. `snapshot_session` **metodu** silinmez: voice tarafı (2a) onu kendi transcript'i için hâlâ kullanıyor; ses dilimini `messages`'a taşımak sonraki 2b dilimidir. Bu dilimde `/api/history` yalnızca **chat** turlarını döndürür.

### 6.2 `app/main.py` değişiklikleri

- **`GET /api/history?session_id=`** — `Depends(require_user)`; yalnızca çağıranın kendi `user_id`'si; `MessageStore.history()` döndürür. session_id sanitize edilir.
- **`run_turn` içinde append** — her turda `messages.append(user_id, session_id, "user", message)` ve reply için `"model"`.
- **Cold-start rehydration** — `get_session` `None` dönerse: `create_session` → `messages.history(..., limit=N)` → her geçmiş tur `append_event` ile ADK oturumuna yüklenir (bkz. §8).

## 7. Auth akışı (kalıcı oturumun çekirdeği)

```
Açılış → AuthManager.silentSignIn()
         ├─ token var  → ChatScreen
         └─ token yok  → SignInScreen → signIn() → ChatScreen
Her API isteği → AuthInterceptor: "Bearer <token>"
         └─ 401 → silentSignIn() → yeni token → tek retry
                  └─ yine 401 → SignInScreen
```

- Credential Manager'a `setServerClientId(WEB_CLIENT_ID)` verilir → dönen ID token'ın audience'ı = Web client ID → backend **değişmeden** doğrular.
- **Web-client-first doğrulama stratejisi:** Önce yalnızca mevcut Web client ID ile denenir (emulator). SHA-1 hatası gelmezse Android OAuth client'a gerek yoktur (HITL sıfır). Gelirse §10'daki kurulum devreye girer. Bu, gerçek gereksinimi **kanıtlar**, gereksiz kurulumu önler.

## 8. Cold-start rehydration (kanıtlanmış API)

ADK 1.36.2'de `Session.events: list[Event]` alanı ve `BaseSessionService.append_event(session, event)` mevcuttur (kurulu kaynaktan doğrulandı: `google/adk/sessions/`). Rehydration:

1. `run_turn` `get_session` `None` (cold-start) → `create_session`.
2. `MessageStore.history(user_id, session_id, limit=N)`.
3. Her tur `types.Content(role, parts=[Part(text)])` → `Event` → `append_event`.
4. Jarvis, önceki bağlamı doğal biçimde görür.

- **N = 50 tur** (context penceresi + Firestore okuma maliyeti dengesi). Aşımda en yeni 50 tur.
- Kesin `Event` inşa alanları (author, invocation_id vb.) plan aşamasında ADK kaynağından doğrulanacak.
- Bu, Katman 1 backlog'undaki "snapshot restore / cold-start bağlamı sıfırlanıyor" maddesini de kapatır.

## 9. Hata yönetimi & güvenlik

- **401** → interceptor silent re-auth + **tek** retry → yine 401 ise `SignInScreen` (sonsuz döngü yok).
- **Ağ hatası** → UI'de hata balonu + retry; gönderilecek mesaj kaybolmaz.
- **session_id sanitization** — client'tan gelen `session_id` backend'de doğrulanır (izinli karakter kümesi; boş/aşırı uzun/path-benzeri reddedilir). Depolama daima `user_id` ile anahtarlanır; bir kullanıcı asla başkasının session_id'sine erişemez. (Backlog "session_id sanitization + user-keying" kapanır.)
- **Rehydration DATA-log** — kaç tur çekildi/yüklendi, session_id, user_id (PII log politikasına uygun) loglanır; bir cold-start sorunu tek çalıştırmada lokalize olur.
- **Eşzamanlılık** — `messages` append-only (her mesaj ayrı doküman) olduğundan çok-instance yazımı çakışmaz. In-memory ADK oturumları instance-yerel; her biri Firestore'dan rehydrate eder. Gerçek-zamanlı çok-cihaz senkronu kapsam dışı (Katman 3+).

## 10. Kurulum muhasebesi (kim ne yapıyor)

Araştırma sonucu: Android OAuth client'ı **programatik oluşturmanın desteklenen yolu yok** (IAP OAuth API 19 Oca 2026'da kapandı; Firebase projede yok; gcloud genel OAuth client üretmez).

**Ben (otomatik):**
- Android SDK + emulator headless kurulumu (KVM hazır: `/dev/kvm`, AMD-V, 16 çekirdek).
- Tüm Kotlin + backend kodu, testler, build, deploy (gcloud).
- SHA-1 fingerprint üretimi (gradle `signingReport` / keytool).
- Web-client-first deneme.

**Kadir (yalnızca emulator testi SHA-1 hatası verirse):**
- Google Cloud Console'da Android OAuth client (paket adı + SHA-1). Ben tam adım listesi + değerleri veririm (~2 dk).
- *Firebase ekleme veya browser-otomasyonu önerilmiyor: biri gereksiz bağımlılık/borç, diğeri hesap-riski.*

## 11. Test stratejisi (production-grade)

| Katman | Kapsam | Araç |
|--------|--------|------|
| Backend | `MessageStore` append/history, rehydration, `/api/history` auth+izolasyon, session_id sanitize | pytest — mevcut 70 teste eklenir; fakes ile Firestore taklidi |
| Android birim | `ChatRepository`, `AuthManager` (silent retry mantığı), `ChatViewModel` state geçişleri | JVM: Robolectric + kotlinx-coroutines-test |
| Android UI (instrumented) | login → chat → history render, 401→re-auth | **headless emulator** (android-35 google_apis x86_64, `-no-window -gpu swiftshader_indirect`) + ekran görüntüsü |

Doğrulama, headless emulator ekran görüntüsüyle görsel olarak da kanıtlanır — ayrı mockup'a gerek yok.

## 12. Kanıtlanmış teknik gerçekler (bu tasarımın dayanağı)

1. Backend auth ID token audience'ı Web client ID'ye sabitler → native istemci backend'i **değiştirmeden** bağlanır. (kaynak: `auth.py`)
2. ADK 1.36.2 kalıcı session servisleri: `Sqlite` (Cloud Run efemeral → uçar), `Database` (Cloud SQL gerektirir), `VertexAi` (Agent Engine gerektirir) — **Firestore-native yok** → uygulama-katmanı `messages` seçildi. (kaynak: `google/adk/sessions/`)
3. `Session.events` + `append_event` mevcut → rehydration fizibil. (kaynak: `base_session_service.py`, `session.py`)
4. Android OAuth client programatik oluşturulamaz (IAP API kapalı, Firebase yok). (kaynak: gcloud, Google duyurusu)
5. Web client ID Credential Manager için gerekli+yeterli olabilir; SHA-1 gerekliliği emulator testinde kesinleşecek. (kaynak: developer.android.com)
6. KVM hazır → headless emulator native hızda. (kaynak: `/dev/kvm`, `/proc/cpuinfo`)

## 13. Plan aşamasında kesinleşecek (bilinçli açık uçlar)

- AGP/Kotlin/Compose/credentials/retrofit **kesin sürümleri** (resmi docs, JDK uyumu).
- ADK `Event` inşa alanlarının tam imzası.
- SHA-1'in gerçekten gerekip gerekmediği (emulator testi).
- Firestore `messages` composite index'inin deploy'a bağlanma biçimi.
