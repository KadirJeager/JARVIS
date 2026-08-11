# Ürün yüzeyi yol haritası — rekabet analizinden frontend özelliği + backend mekanizması

**Tarih:** 11 Ağustos 2026 · **Kaynak:** `docs/arastirma/2026-08-05-ekosistem-analizi.md` (5 Ağu) · **Hedef UX:** North Star §4.8 — *"Gemini uygulaması benzeri tek sohbet akışı"*

Bu belge iki taramanın çıktısıdır: (a) analizdeki **kullanıcının gördüğü/yaptığı** her özelliğin ve onu mümkün kılan backend mekanizmasının çıkarılması, (b) mevcut Android uygulamasının Gemini-uygulaması modeline karşı boşluk haritası. Her satır repo'da grep ile doğrulandı; "var/yok" iddiaları file:line taşır.

## Bulunan 15 aday (özet)

| # | Özellik (kullanıcı ne görür) | Backend mekanizması | Alınabilir mi | Bizde | Efor |
|---|---|---|---|---|---|
| F1 | Cevap kelime kelime akar | run-id + sunucu-itmeli olay akışı (SSE/WS), `accepted → event* → final` | Yalnız **protokol şekli** (OpenClaw kodu değil — §1.8 yetki-atlatmayı *yapısal sınıf* diyor) | Yok (`main.py:542-556` tek blob) | M |
| **F2** | Kart *neden*i söyler: kim istedi, hangi yetkiyle, tetikleyici, geri alınabilir mi | Onay kaydı karar bağlamını taşır | Tasarım (HumanLayer terk edilmiş, §5.10 B) | **Yarım kablo:** sunucu yazıyor (`approvals.py:241-250`), DTO alanları hiç bildirmiyor (`ApprovalApi.kt:55-71`) | **S** |
| F3 | Tek dokunuşla gerekçeli ret, hazır düğmelerden | `/reject` gövde alır; gerekçe tool çıktısı olarak modele döner; politika `ResponseOption[]` verir | **Tip tasarımı** (§5.10 B: "kodu değil tip tasarımı") | Yok (`main.py:917-921` gövdesiz) | 2-3 g |
| F4 | "Onayla ama şu parametreyle" | `/approve` payload alır; executor `(args, user, payload)` ve kendi tarafında kısar | ADK-yerli desen ama `require_confirmation` `@experimental` — **önce ölç** (§5.10 C) | Yok | M |
| **F5** | "Bir kez / bu oturum / her zaman" merdiveni | Operanda bağlı kalıcı grant kaydı; politika onu okur; "her zaman" denetlenebilir bölge değişikliği | **Mekanik olarak evet** — §1.9 OpenClaw'un bağlama mekanizmasını *bizimkinden iyi* buluyor | Yok | 2-3 g |
| F6 | Kart kararın hangi kuraldan geldiğini gösterir | Politika eşleşen **kural kimliğini** döner | Tasarım (QwenPaw Apache-2.0, §3.5'te kaynak doğrulanmış) | Kısmi (`cause` var ama iki slug) | S |
| **F7** | Sesli oturum transkripti sohbete kart olarak düşer | Ses köprüsü `messages`'a `kind="voice_session"` satırı yazar | Dış koda gerek yok — kendi `kind`/`meta` uzantı noktamız hazır | **Yok** (`voice.py:582-586` yan snapshot'a yazıyor) | **S** |
| F8 | Zengin mesaj tipleri (grafik/HTML/rapor kartı) | `messages.kind` sözlüğü + artefakt deposu; bilinmeyen kind metne düşer | Fikir (Canvas onların gateway'ine bağlı) | Kısmi — dikiş açık, tek üretici `kind="approval"` | M |
| **F9** | Uzun işte canlı ilerleme ("adım 4/20") | Aktif oturuma adım-başı append + bütçe meta; checkpoint (bizde var) | Desen (OpenHuman GPL-3.0, kendi deyimiyle erken beta) | Kısmi (`tasks.py` checkpoint+bütçe var, rapor **ayrı** oturuma ve sadece sonda) | S-M |
| F10 | Bildirim hacim kontrolü (sessiz saat, rate limit) | Tek dispatch yolunda notify-politikası | Şablon | Yok (`fcm.py:112-130` koşulsuz) | S |
| F11 | "Benim hakkımda ne öğrendin?" — düzenlenebilir öğrenme zaman çizelgesi | Hafıza kayıtlarına id + `origin` + `use_count`; list/edit/archive uçları; silme = arşivleme | Tasarım (§2.8 H2; `active → stale(30g) → archived(90g)`, **asla silme**) | Yok | 2-3 g |
| F12 | Mesaj başına "bunu nereden biliyorsun / unut" | Arama sonucu `{doc_id, collection, ts}` taşır (düz metne erimez) | Kendi açığımız, rapor kendi kodumuzu okuyarak bulmuş (§4.6) | Yok | S |
| **F13** | Haftalık retro / görev raporu ana zaman çizgisinde | Zamanlanmış iş digest satırını **canlı** oturuma yazar | Fikir | **Kısmi + bug:** `retro`/`tasks`/`reminders` üç ayrı yan oturum; `conversations.touch()` çağrılmadığı için uygulamada **hiç görünmüyor** | S |
| F14 | Bekleyen onayı Claude/Gemini üzerinden cevaplama | Misafir kapısına iki MCP aracı, politika hangi onayı çözebileceğini kapar | **Evet** — sözleşme yayınlanmış (Hermes MIT); bizde onlardan iyi çalışır (per-caller capability var) | Yok (misafir kapısı 6 araç, hepsi hafıza/repo) | S-M |
| F15 | Kompozerde ek: her dosya, görsel, ekran | Upload ucu + nesne deposu + metin-dışı `messages` parçaları + güvenilmeyen-içerik zarfı (§1.10 Ç2) | **Alınacak kod yok** — rapor yalnız aracın adını veriyor ve ingest'in birinci enjeksiyon yüzeyi olduğunu söylüyor | Yok | L |

**Bilerek dışarıda:** taint bayrağı (P4), hash-zincirli audit (P5), tool receipts (D1), OCSF adlandırma — iç hijyen, ayrı planlı. Canlı arama "Devral" kartı, Wear eşleştirme, Satellite — donanıma bağlı (Faz D).

## Sıra (kullanıcıya görünen değer ÷ efor, donanımsız)

1. **F2 — kartın karar bağlamı (S).** Backend bugün yazıyor, telefon atıyor. Listenin en yüksek oranı: §5.6'nın *"bilgili karar üretmez"* dediği kartı, "kim, neden, geri alınabilir mi" sorularını cevaplayan bir karta çevirir. → **`docs/superpowers/plans/2026-08-11-onay-karti-2.md` Task 5-6 (devam ediyor)**
2. **F7 — sesli transkript sohbete (S).** North Star'a yazdığımız modelden en büyük sapma. Tek append çağrısı, yeni mekanizma yok, ve "tek zaman çizgisi" cümlesini gerçekten doğru kılar.
3. **F3 — tipli ret + hazır gerekçeler (2-3 g).** Ret bugün model için çıkmaz sokak (`outcome=None`); §5.2'nin tip-düzeyi kuralı + `prompt_fill` her "hayır"ı kullanılabilir bir sonraki adıma çevirir, ve F5'in ön koşuludur. → **aynı planda Task 3**
4. **F13 + F9 — raporlar ve adım ilerlemesi canlı zaman çizgisinde (S-M).** Üç paralel rapor oturumu, kullanıcının bakması gereken üç ayrı yer demek; makine zaten var (`messages.kind`, `tasks` checkpoint).
5. **F1 — akış (M).** Beşinci sırada çünkü tek gerçek yeni taşıma katmanı bu — ama algılanan kaliteyi başka hiçbir şey bu kadar oynatmıyor, ve sıfırdan başlamıyoruz: `voice_protocol.py:31-48` çerçeve sözlüğünü zaten tanımlıyor, `UiMessage` toleranslı-kablo sınırı bilinmeyen satır tiplerini zaten tolere ediyor.

## Mevcut uygulamanın Gemini-modeline karşı üç ana boşluğu (ölçüldü)

1. **Akış yok.** `run_turn` ADK olay akışını dönüyor ama `is_final_response()` dışındakileri **atıyor** (`main.py:468-476`); istemci tek `suspend` çağrısı bekliyor. Protokol değişikliği (SSE/WS), istemci rötuşu değil.
2. **Sesli arama zaman çizgisinin dışında.** Köprü sabit `voice-{user_id}` oturumuna yazıyor (`voice.py:508`), arama ekranı Nav'ın üstünde tam ekran overlay (`MainActivity.kt:396-403`); "Devral" hiç yazılmamış (grep sıfır).
3. **İlerleme sinyali yok + raporlar erişilemez oturumlarda.** Tek "çalışıyor" ipucu gönder düğmesinin sönmesi; `tasks.py`/`retro.py` sabit oturumlara yazıp `conversations.touch()` çağırmıyor, `list_conversations` de yalnız touch edilmişleri döndürüyor (`conversations.py:137-149`) — yani raporlar Firestore'da var, uygulamada yok. `tasks.py:33-34` docstring'i tersini iddia ediyor.

## Kayda değer iki dürüst not

- **F2 bir inşa değil, bitmemiş bir kablodur.** Sunucu `actor/cause/operand/reversible` yazmaya bugün başladı; hiçbir istemci okumuyor.
- **F15 (ekler) analizin kod vermediği tek satır.** Zamanlanırsa "port" değil, **özgün iş + §1.10 Ç2 güvenilmeyen-içerik zarfı** olarak bütçelenmeli — ingest, birinci enjeksiyon yüzeyi.
