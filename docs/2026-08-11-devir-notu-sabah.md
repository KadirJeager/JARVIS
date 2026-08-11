# Devir notu — 11 Ağustos 2026, sabah (gece vardiyası kapanışı)

Önceki devir: `docs/2026-08-11-devir-notu.md` (03:00). Bu not onun üstüne gecenin ikinci yarısını ekler; çelişirlerse BU not günceldir.

## 1. Tek cümlede

Planın kalan tüm kod işleri bitti (Task 6, 7, 8, 9 + yeni Task 11), üç CM kapısı **iki kez** deploy edildi (gece başı `cm-dcc1f89`, fix dalgası sonrası `cm-ecaa964`), dal-geneli final review bir **Critical yakaladı ve kapattı** (istemci rotaları yanlış servise gidiyordu) — kalan tek iş **Task 10: Kadir'le cihaz turu**.

## 2. Canlı durum

| | Değer |
|---|---|
| Dal | `feat/antispoof-cm` — HEAD `ecd8423` (main'e merge YOK; karar Kadir'in) |
| Prod imaj | `gcr.io/your-gcp-project/jarvis-brain:cm-ecaa964` |
| jarvis-voice | `00030-65p` · 4Gi · CM warmup · **sessionAffinity açık (YENİ)** |
| jarvis-brain | `00037-t5b` · 3Gi · CM yüklemiyor + **artık tembel-yükleme koruması var (YENİ)** |
| Brain testleri | **882 geçti, 2 skip** (gece başı 877) |
| Android | JVM + assembleDebug + **compileDebugAndroidTestKotlin** üçü de yeşil |
| SDD ledger | `.superpowers/sdd/2026-08-11-ses-kimligi-pixel-dogrulugu/progress.md` — sabahın haritası |

## 3. Gece kapanan işler (03:00 sonrası)

| İş | Sonuç |
|---|---|
| **Deploy #1** (03:20) | Task 1-3 CM kapıları canlı: voice `00029-5tv`, brain `00036-qgl`, warmup kanıtlı |
| **Task 11 (yeni)** | İlk-kelime arızası için zaman çizelgesi enstrümantasyonu: `VoiceSession`'a `nowMs`-deseniyle logger seam (`tl ev=<olay> t=+<ms> gen=<N>`), `AndroidSpeechToText`'e `onReadyForSpeech` dahil yaşam döngüsü logları. Review 2 Important yakaladı (lock dışı log çağrısı → gen-çapraz zaman bozulması; `firstPartialLogged` yeni aramada sıfırlanmıyor) — ikisi de kapandı. |
| **Task 6** | Ölü `EXTRA_AUDIO_SOURCE` int'i + yanıltıcı yorum silindi; yerine gerçeği anlatan yorum (bayt-bayt brief'ten). |
| **Task 7** | **PFD probu: POZİTİF.** tr-TR tanıyıcı `ParcelFileDescriptor` beslemesini kabul ediyor — 192 KB'ın tamamı okundu, transkript doğru. Tek-`AudioRecord` yeniden yapımının önü açık. Dürüst kayıt: emülatörde AĞ tanıyıcısı cevapladı; cihaz-üstü Soda yolu Pixel'de doğrulanmalı. Probun ayırt edici hilesi: fikstür pipe tamponundan büyük → yazar-thread'i tamamlanması "tanıyıcı okudu"nun mekanik kanıtı. |
| **Task 8** | `VoiceEnrollApi.challenge()/enroll()` istemci rotaları + 409/422 ayrımı testlerle pinli. İki fix turu: (1) `clips` gövde assertion'ı, (2) VoiceApi genişletmesinin kırdığı 4 androidTest fake'i. |
| **Task 9** | "Bu cihazı tanıt" akışı: `EnrollDeviceViewModel` (state machine, Türkçe etiketler), gerçek `AndroidClipRecorder` (AudioRecord/VOICE_COMMUNICATION/16k), `VoiceProfileScreen` bağlama. Review temiz (0 C/I). Kullanıcı-güdümlü `proceedToRecording()` geçişi eklendi. |
| **Final review (fable)** | 1 Critical + 5 Important — aşağıda. |
| **Fix dalgası + Deploy #2** | `ecaa964` — aşağıda. |

## 4. Final review'ün yakaladığı Critical (C1) ve düzeltmesi

**Sorun:** İstemcinin yeni challenge/enroll çağrıları `BASE_URL`'e = **jarvis-brain**'e gidiyordu; oysa `active_bridges` (kodun sesli okunması) ve sıcak CM **jarvis-voice** sürecinde. Akış prod'da ilk adımda ölürdü (`code_spoken` hep false) ve brain'de enroll, 1.2 GiB CM'i 3 Gi konteynere tembel-yükleyip canlı servisi OOM'a sürükleyebilirdi. Kısmen plan kusuru: Task 8 spec'i rotayı kaynak dosyayla tanımlamış, deploy edilmiş servisi hiç sormamıştı. Hiçbir görev-review'ü göremezdi (unit testler sunucuyu mock'lar; sunucu testleri iki yarıyı tek proceste koşar).

**Düzeltme (`ecaa964`, re-review'den geçti):**
- İstemci: `VoiceEnrollApi` ayrı arayüz, `VOICE_BASE_URL`'e gidiyor (aynı auth zinciri, `buildRetrofit` paylaşımlı — 401 riski yok).
- Sunucu: enroll, CM gerekli ama **yüklü değilse** tembel-yükleme YAPMADAN 503 (`antispoof.is_loaded()` probu) — brain artık misrouted çağrıyla OOM edilemez.
- `jarvis-voice.yaml`: `sessionAffinity: 'true'` (best-effort; tek kullanıcıda çoğunlukla tek instance zaten).
- **Kalıcı mimari karar Kadir'e:** affinity çerez-tabanlı ve garanti değil. Kalıcı çözüm adayı: challenge isteğini REST yerine WS köprüsünün kendisinden geçirmek (`challenge_request` çerçevesi) — instance eşleşmesi yapısal olarak garantiye alınır. Şimdilik gerek yok, Task 10 ölçsün.

**Diğer Important'lar:** I1 canlı-köprü deregistrasyonu (owner-karşılaştırmalı pop + reconnect testi, kapandı) · I5 mutation-kapsamasız spoof-red yolu (test eklendi, mutasyon öldürüldüğü kanıtlı) · I3 eko-kuyruk PCM budaması (UI metnine "kısa duraklama" rehberi eklendi; asıl gözlem Task 10'da) · I2 eko-METİN kapısının kodu yutması ve I4 kayıt-sırasında-TTS — **ikisi de cihaz turu gözlemi, §6'da**.

## 5. AÇIK ARIZA — ilk kelimeler (durum değişti: artık ölçülebilir)

İstemci artık tam zaman çizelgesi logluyor. Sabah turunda `adb logcat -d | grep -E "VoiceSession|AndroidSpeechToText"` ile: `stt.arm → (AndroidSpeechToText) startListening → onReadyForSpeech` arası **sağır pencere**dir; `relisten` sonrası pencere + `mic.gate` geçişleri ilk-kelime kaybının nerede olduğunu söyleyecek. Ölçmeden hipotez kurulmadı; enstrümantasyon davranışı DEĞİŞTİRMEDİ (review doğruladı).

## 6. Task 10 — sabah cihaz turu (Kadir + ajan, senaryo)

Ajan kurar/okur, Kadir sürer. Kurulum: bölüm 9'daki adb prosedürü aynı (port Kadir'den).

1. **Normal sesli tur** (kontrol): `aec=true` beklenir, `speaker.identify` satırı okunur; `tl ev=` zaman çizelgesi İLK KEZ gerçek cihazda okunur → ilk-kelime analizi.
2. **"Bu cihazı tanıt" akışı:** Ses ekranı → "Ses örneklerini yönet" → buton. Jarvis 4 haneli kodu söyler.
   - **I3 gözlemi:** kodu Jarvis bitirdikten sonra KISA BİR DURAKLAMAYLA tekrar et (UI da öyle diyor). Anında cevap verirsen sunucu kesik tampon skorlar → "canlılık doğrulanamadı" (bug değil, bilinen pencere; retry çalışır).
   - **I2 gözlemi:** sunucu logunda kod-tekrarının hiç ULAŞMADIĞI görülürse (`user_text` yok), istemci logunda `stt.final out=drop_echo_text` ara — tanıyıcı rakamları YAZIYLA çevirdiyse eko-metin kapısı yutmuş olabilir (~%26 kod için mümkün). Bulunursa kapıya akış-aktif istisnası yazılır.
   - **I4 gözlemi:** "Kodu söyledim, devam et"e Jarvis'in onay cümlesi BİTTİKTEN sonra bas; kayıt sırasında Jarvis konuşursa klipler kirlenir (CM 422 vermeli — o da veri).
3. **Sonuç okuma:** `enroll CM:` satırları hepsi bonafide + `anchors` 7'den yükselir; sonraki normal turda `anchor_score` 0.60'ı geçip `adapted=True` görünmesi = zafer (galeriyi Pixel öğrenmeye başladı).
4. Öncesi/sonrası `anchor_score` değerlerini ledger'a yaz.

## 7. Kadir'e sorular/kararlar (öncelik sırasıyla)

1. **Task 10 turu** — yukarıdaki senaryo (~15 dk).
2. **Saat credential kararı** — değişmedi (önceki devir §7.1; önce saat modeli LTE mi öğrenilecek).
3. **Challenge instance-eşleşme mimarisi** — §4'teki WS-çerçevesi önerisi; acil değil, Task 10 affinity'nin yettiğini gösterirse erteler.
4. **Merge kararı** — dal main'in 30 commit önünde, final review "fix'lerle merge'e hazır" dedi ve fix'ler kapandı; prod zaten bu daldan koşuyor. Merge etmek istersen dal-tabanı temiz.

## 8. Tuzaklar (geceden yeni öğrenilenler; eskiler önceki devirde geçerli)

- **Deploy edilen topoloji ≠ kaynak dosya.** İstemci rotası eklerken "hangi SERVİS" sorusu spec'e yazılacak (LESSONS'ta).
- **`testDebugUnitTest`+`assembleDebug` androidTest'i DERLEMEZ.** Paylaşılan interface genişleten her iş `:app:compileDebugAndroidTestKotlin` koşmalı (bu gece 4 fake sessiz kırıldı; ikinci vaka).
- **İskelet + mandate çelişirse iskelet kazanır.** Şablon geçersiz kılınıyorsa çelişen satırı ADIYLA sil dedir (Task 7 Critical'ının kaynağı).
- **androidTest asset'leri TEST APK'sında:** `InstrumentationRegistry.getInstrumentation().context` ile okunur; `ApplicationProvider` sahte negatif üretir.
- **Emülatör gerçeği:** `jarvis_avd`/`bd_phone` google_apis imajında GERÇEK Google tanıyıcısı var (GoogleTTSRecognitionService) — Task 7 orada koştu. Tablet adb'si gece boyu tıkalıydı (`get-state` OK, `shell` timeout — muhtemelen diğer ajanın yoğun kullanımı); `adb kill-server` YAPMA, öbür ajanı düşürür.
- **LESSONS.md bakım borcu:** 92 girdi (protokol ≤40 der) ve düzen karışık (kimi en üste, kimi en sona ekliyor) — bir ara toplu arşivleme gerekiyor.

## 9. Ertelenen minor'lar

Hepsi ledger'da gerekçeli; final review üçünü "merge öncesi şart değil" diye triyajladı. Kod tarafında bekleyen tek anlamlı aday: `createVoiceEnrollApi`'nin ikinci OkHttpClient kurması (paylaşım ucuz iyileştirme) ve M3 (enroll'a clip sayısı/boyut tavanı).
