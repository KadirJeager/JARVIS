# Devir notu — 22 Ağustos 2026 (Kimi oturumu; 23 Ağu sabahı uzadı)

## 1. Tek cümlede

Task 3'ün bekleyen re-review'ü CLEAN ile kapandı; köklü dönüşüm raporuna Rev 3.1 çekildi (durum iddiaları repoya karşı doğrulandı, yol haritası tek haritaya indirildi); Onay Kartı 2.0 **Task 4** (hash-zincirli karar kaydı + geri-alınabilirliğe göre zaman aşımı) TDD + review + fix turuyla bitti; **F7** (sesli transkript sohbet zaman çizgisine) ve **F13** (rapor görünürlük bug'ı) kapandı; **cm-d966ba0 prod'a deploy edildi**; **Task 5** (istemci DTO/domain) alt-ajanla yazıldı, review'de çıkan Critical (Retrofit default-gövde özyinelemesi) düzeltildi.

## 2. Dal durumu

| | Değer |
|---|---|
| Dal | `feat/antispoof-cm` — **prod `cm-d966ba0` ile aynı** |
| Prod | jarvis-voice-00033-lg6 · jarvis-brain-00040-p5d · ikisi de %100 · health 200/200 (`5298028`) |
| Yeni commitler | `f070b01` rapor Rev 3.1 · `a00d123` Task 4 · `b830f77` F7 · `d966ba0` F13 · `5298028` deploy pinleri |
| Brain testleri | **957 passed, 2 skipped** (gün başında 932) |
| Android | Task 5 kodu yazıldı; üç Gradle kapısı koşuyor (bu not güncellenir) |

## 3. Kapanan işler

### Task 3 re-review (devir notundaki 1. sıradaki iş)
`96252ed` scoped re-review → **CLEAN**. İki IMPORTANT düzeltme kodda kanıtlandı: reason guard tip-kontrollü ve claim'den önce; canlı ADK Event teslimatı `InMemorySessionService.append_event`'in copy-session davranışını doğru ele alıyor (ADK 1.36.2 kaynağına karşı izlendi). Minör bulgu: kapanmamış oturumda yalnız HTTP 200 assert eden test — sonraki dilime.

### Rapor Rev 3.1 (`docs/arastirma/2026-08-20-koklu-donusum-ve-ikame-raporu.md`)
"Anti-spoofing yok" bayatlığı düzeltildi; `recipes.py` yok-dan planlıya; PerTh 6 Ağu kararına bağlandı; UI/Wear "devret" satırları mevcut yatırımla uzlaştırıldı; §7 tek yol haritası (North Star §12 + ürün yüzeyi boşlukları dahil, çift-sayım temizlendi).

### Task 4 — decision_log (`a00d123`)
- `brain/app/decision_log.py`: onay başına EN FAZLA bir halka — doc id = approval_id + atomik `create()`. Süre yolu bilinçli olarak claim yazmadığı için (pinli test) yarış dedup'u buradan gelir.
- `verify()` saat-bağımsız `prev_hash` gezintisi (test saatleri donuyor); halkalar kullanıcı-başına monotonik `seq` taşır (yalnız yazım anında önceki-halka seçimi için).
- Dürüst sınırlar docstring'de: kurcalanma kanıtı, yazarlık DEĞİL; farklı onaylara eşzamanlı geçiş zinciri fork'layabilir; ajan-yazılmez Firestore rules hâlâ yapılacak yarım.
- Zaman aşımı ikiye ayrıldı (§5.6): geri alınamaz → eski fail-closed ret; geri alınabilir → `EXPIRED_NOTIFY_OUTCOME` ("ret değildir, yeniden sorulabilir") + opsiyonel `notify` kancası (FCM kablosu bilinçli olarak bağlanmadı).
- Review 1 Important buldu, kapandı: halka yazımı `_log_decision` sarmalayıcısına alındı — transient Firestore hatası artık "onaylandı ama yürütülmedi" yarım-durumu üretemez.
- Kalan gözlem (bilinçli): executor hatasında doküman `failed` olurken halka `approved` kalır — kararyürütüm ayrımı, docstring'de belgeli.

### F7 — sesli transkript sohbete (`b830f77`)
Bağlantı kapanınca transkript TEK `kind="voice_session"` mesaj satırı olarak `voice-{user_id}` oturumuna düşer + `conversations.touch()` ile listelenir. Başlık aramanın ilk kullanıcı cümlesinden; sessiz aramada başlık uydurulmaz. `touch()` opsiyonel `title=` aldı (first-wins kuralı aynı). Sabit oturum bilinçli: çağrı-başına id her kapanışta liste satırı basardı; "Devral" işiyle birlikte düşünülecek.

### F13 — rapor görünürlük bug'ı (`d966ba0`)
Ölçülen bug kapatıldı: `tasks.py`/`retro.py` raporları Firestore'da var ama `list_conversations` yalnız touch'lı oturumları döndürdüğü için uygulamada GÖRÜNMÜYORDU (docstring tersini iddia ediyordu). İki `make_reporter` da opsiyonel indeks-store aldı; üretim kabloları `events._handle_task_tick` ve `retro.run`'da bağlandı. Yalan docstring'ler gerçeğe eşitlendi.

## 4. Bilinçli kapsam dışı / açık

1. **Task 5-6 istemci** — Task 5 kodu yazıldı ve review'den geçti (aşağıda); Task 6 kart UI'ı sıradaki.
2. **F9 (adım-başı canlı ilerleme)** — kasıtlı open: tick-başı mesaj appends `messages` koleksiyonunu spamler; kendi throttling tasarımını hak ediyor.
3. **F1 akış/SSE** — algılanan kaliteyi en çok oynatan kalan iş.
4. **Süre-dolum bildirim kanca kablosu (FCM)** — kanca var, tüketici yok.
5. Minör review notları: kapanmamış-oturum testinin transcript assert'i; `expire_due`↔`decide` milisaniyelik claim yarışı (Task 4 öncesi vardı).

## 4.5 Deploy — cm-d966ba0 CANLI

- Build 8dk55sn SUCCESS; iki YAML pinlendi (`cmd966ba01` nonce); voice→brain replace; `jarvis-voice-00033-lg6` + `jarvis-brain-00040-p5d` %100, `/api/health` 200/200, `/api/approvals/reasons` auth arkasında 401 (rota kanıtı). Pin commit'i `5298028`.
- **Build tuzağı (iki kez yaşandı):** gcloud config'deki `billing.quota_project = yourappsecosystem`, `--project your-gcp-project` bayrağına RAĞMEN Cloud Build çağrısının consumer'ı oluyor → "API not enabled" PERMISSION_DENIED. Çözüm: çağrıda `CLOUDSDK_BILLING_QUOTA_PROJECT=your-gcp-project` env pini (global config'e dokunulmadı — başka projeler etkilenmesin). JARVIS GCP işlerinde bundan sonra bu pin STANDART.
- Deploy edilen sunucuda ret artık reason ZORUNLU (422): eski APK'nın reddi yeni sunucuya karşı 422 alır — sürüm kayması bilinçli; Task 6 picker'ı bunun kullanıcı-yüzü çözümü.

## 4.6 Task 5 + Task 6 — istemci yarısı BİTTİ (`11ff042`, `84f0c96`)

**Planın tamamı kapandı (Task 1-6).** Task 6 alt-ajanla yazıldı; bu kez diff review'i kusursuz çıktı.

- Kart artık karar üreten kart: operanda belirgin satır, "Kim istedi", güven+neden Türkçe ifadelerle (bilinmeyen slug → "bilinmiyor", asla ham değer), ve **"Geri alınamaz" rozeti bilgi bloğunun en göze batan öğesi** — §5.6'nın "onay yorgunluğu güvenlik açığıdır" kuralı gereği sadece kararı değiştiren şey gürültülü.
- Ret iki adım oldu: Reddet → `/api/approvals/reasons`'dan gelen hazır gerekçe çipleri (tek dokunuş, `prompt_fill` gönderilir) + serbest metin. Akış durumu ChatViewModel'de (`rejectingApprovalId`) — JVM testleri sürebiliyor; MainActivity değişmeden derleniyor (tek-parametreli overload `beginReject`'e yönlendiriyor).
- Gerekçeler oturum başına bir kez, best-effort çekilir; çekilemezse serbest metin çalışmaya devam eder.
- Kapılar: tam ünite süit + assembleDebug + compileDebugAndroidTestKotlin üçü yeşil.
- **Yeni APK kurulmalı** — sunucu (cm-d966ba0) ile bu istemci birlikte tam anlamını kazanıyor.

## 4.7 OCSF borcu dürüst kayda geçti (`f758ba6`)

decision_log'a OCSF eşlemesi YAZILMADI — doğrulanmış şema kaynağına bu oturumda erişilemedi (arama kotası + JS-only site) ve uydurma eşleme yasak. Docstring'de kritik kısıt belgeli: halkalar oluşTUKTAN sonra alan yeniden adlandırma tüm eski hash'leri kırar → ya koleksiyon gençken yap ya mapping tablosu kullan.

## 5. Sıradaki iş (önceliğiyle)

0. **✅ ÇÖZÜLDÜ: "sesli arama bozuk" raporu — kök neden ölçüldü, düzeltildi, canlı kanıtlandı (`f03b6a7`, 23 Ağu ~01:20).**
   Kök neden: **Pixel 10 Pro / Android 17'de cihaz-üstü tanıyıcı (tr-TR) PFD beslemesiyle final basmıyor** — kısmi sonuçlar ve `onEndOfSpeech` geliyor ama `onResults`/`onError` hiç gelmiyor (üç oturum da `hadPartial=true hadResult=false`). İstemci `utterance_final` gönderemiyor, sunucu tamamen sessiz oturum görüyordu. Dün geceki "sunucuya hiç istek ulaşmadı" okuması eksikti: voice WS üç kez BAŞARIYLA bağlanmıştı (00:40:38/56, 00:41:41 → 101); sessizlik oturumun içindeydi. Mikrofon izni, ağ, auth, çökme — hepsi temiz.
   Düzeltme: `AndroidSpeechToText`'e endOfSpeech sonrası 2 sn watchdog — terminal callback gelmezse döngüyü kapatıp en iyi kısmi sonucu final olarak teslim ediyor; geç gelen gerçek terminal `terminal.afterSynthetic` ile yutuluyor; `tl ev=mode onDevice=` teşhis satırı eklendi.
   Canlı kanıt (Kadir'le): `stt.final out=sent` → sunucuda CM `bonafide` (p_fake=0.0011) → `speaker.identify verified=True` (0.62) → trust `HIGH` → ajan turu koştu, `tts.speak`. Kadir: çalışıyor.
   **Kalan gözlemler (sonraki dilim):** (1) bu geceki TÜM finaller sentetikti (+2 sn gecikme, kısmi-kalitesi metin) — olası temiz çözüm endOfSpeech'te EOF flush; PfdFeedPolicy muhasebesi (hadResult her zaman false kalır) ve barge-in penceresiyle tasarlanmalı. (2) `onError code=5 ERROR_CLIENT` iki kez ölümcül sayılıp "yeniden dene" diyaloğu bastı (ilk açılış dâhil) — kademelendirme (Retry-with-delay?) değerlendirilecek. (3) Aşağıdaki UX memnuniyetsizliği dilimi hâlâ açık.
   **AYRICA (23 Ağu Kadir düzeltmesi, duran kural):** Kadir'in verdiği ürün örnekleri (Gemini app, OpenClaw...) AMAÇ değil NİYET kanıtıdır: UI'da çıta "güncel birinci sınıf asistan uygulamaları seviyesinde cilalı, dandik-olmayan deneyim" (somut ölçüt: M3 kalite rehberi), mekanizmada "çözülmüş sorunu yeniden icat etme — hazır yaklaşımı oku, uyarla". Sabit-rakip-kopyası ya da klon yok; amaç North Star. Rahatsızlık listesi İSTENMEZ — envanterlenen temel eksikler (tek-seferlik yanıt, markdown/yazıyor-göstergesi yok, inline literal'ler, ayarlar ekranı yok, tek neon tema, mesaj aksiyonları/ek yok) UX revize diliminin girdisidir; F1 akış en büyük tekil kaldıraç olmaya devam ediyor.

1. ~~Yeni APK telefona~~ → **KURULDU (23 Ağu 00:39, Tailscale ağ-adb `100.64.0.10:41397`, `install -r` Success, `lastUpdateTime=2026-08-23 00:39:35`).** Not: Tabletteki JARVIS hâlâ 11 Ağu APK'sı — dokunulmadı.
2. **F9** (canlı adım ilerlemesi, throttling tasarımıyla) → **F1** (SSE akışı — algılanan kalitenin en büyük kalan kaldıracı).
3. **Keep MCP** (master token Kadir'in) + **Wear asgari** (credential kararı) → Y4 ✅'ya.
4. Güvenlik kalanı: taint bayrağı, Mem0 yazma kapısı, hafıza regresyon seti, kalibrasyon+güçlü-klon testi.
5. **Merge kararı** — dal artık hem prod ile aynı imajda hem 6-task'lık planı kapatmış durumda.
