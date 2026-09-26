# K0 — gerçek model, Jev ve seçmeli araç deneyi

Tarih: 2026-09-26 (Europe/Istanbul). Durum: gerçek model → seçilmiş araç →
araç sonucu → devam turu Google ve yerel modellerle geçti; Gemini görsel turu
hesap doğrulaması nedeniyle açık; runtime kararı K1 öncesi öneri düzeyinde.

Bu kayıt, [güncel mimarinin](2026-09-25-moduler-kisisel-asistan-mimarisi.md)
ilk uygulama kapısını izler. Üretim servisi veya mevcut kullanıcı verisi
değiştirilmedi. Deney kodu [experiments/k0](../experiments/k0/README.md) altındadır.

## Doğrulananlar

- Yerel Google Cloud CLI oturumu yoktu; mevcut Chrome hesabıyla Cloud Shell
  oturumu kullanıldı. CLI 586.0.0 geçici araç dizinine indirildi; sistem PATH'i
  veya kullanıcı oturum dosyaları değiştirilmedi.
- Canlı `jarvis-brain` revizyonu `jarvis-brain-00041-k8x`. Proxy yapılandırması
  `v7.2.111` gösteriyor. Kayıt deposundan hem etiket hem de revizyonun işaret
  ettiği digest ile indirme `not found` döndürdü. Bu, üretim servisinin o anda
  çalışmadığının kanıtı değildir; aynı imajdan yeniden dağıtım yolu eksiktir.
- Üretimdeki imaj değişmeden bırakıldı. Aynı CLIProxyAPI sürümünün resmî
  `linux_amd64_no-plugin` yayın dosyası Cloud Shell'de indirildi ve GitHub
  yayın metadata'sındaki SHA256 ile karşılaştırıldı:
  `d6d8c35c10067fcd700e43fdd8dea7bb6152b3a8cc02806b812764ef63009980`.
  Bu deney üretim kapsayıcısının birebir kopyası değil, doğrulanmış resmî
  binary ile aynı mevcut OAuth bağlantısının ayrı süreçte kullanımıdır.
- Mevcut Secret Manager kayıtlarının korumalı geçici kopyasıyla, yalnız
  loopback üzerinde gerçek proxy başlatıldı. Canlı model kataloğu okundu.
  Katalogdan seçilen `gemini-3.8-flash-high`, gerçek Gemini `generateContent`
  çağrısında HTTP 200 döndürdü; ölçülen süre 3,12 saniye. Bu yalnız model
  çıkarımı kanıtıdır, araç turu veya ürün sağlığı kanıtı değildir.
- TypeSafe doğrudan gerçek API çağrısı HTTP 200 döndürdü. Dönen sürüm
  `jev-1.13.0`; ilk dar runtime deneyi için `pydantic_ai` seçildi.
  [Kaydedilen soru, kanıt ve yanıt](evidence/2026-09-26-k0/runtime-jev.json).
- Pydantic AI 2.50.0 içindeki hazır `TypeSafeModel` ile gerçek araç tanımlarına
  karşı üç canlı seçim yapıldı: paket sorgusunda `fetch_package_release`,
  saat sorgusunda `get_utc_time`, sağlanmayan Cloud Run yeteneğinde
  `no_matching_tool`. [Paket](evidence/2026-09-26-k0/jev-tool-selection.json),
  [saat](evidence/2026-09-26-k0/jev-clock-selection.json),
  [eşleşme yok](evidence/2026-09-26-k0/jev-unavailable-selection.json).
  Bunlar yalnız bu dar girdilerin sonuçlarıdır; genel yönlendirme doğruluğu
  oranı değildir. Olasılık/güven 1 dönmesi bağımsız doğruluk kanıtı sayılmaz.
- Paket aracı gerçekten PyPI'yi okudu; o anda `pydantic-ai-slim` sürümü 2.50.0,
  Python gereksinimi `>=3.10` döndü. Yanıt hash'i gerçek HTTP gövdesinden
  hesaplandı. [Kaynak sonucu](evidence/2026-09-26-k0/live-pypi-read.json).

## Tam araç turu (26 Eylül 00:58–01:15)

Cloud Shell'e dosya yükleme engeli yerine deney yerel PC'de çalıştırıldı.
Aynı resmî CLIProxyAPI v7.2.111 binary'si indirildi; SHA256 GitHub yayın
metadata'sı ve yukarıdaki değerle aynı. Secret Manager'daki mevcut
`cliproxy-api-key` ve `cliproxy-oauth-antigravity` kayıtları, kasaya ve
`~/.config/gcloud`'a dokunmayan geçici gcloud oturumuyla 700 izinli tmpfs
dizinine alındı; proxy yalnız `127.0.0.1` üzerinde çalıştı. TypeSafe anahtarı
yerel ortam dosyasından okundu, başka yere aktarılmadı. Raporlarda anahtar,
token ve e-posta bulunmadığı betikle doğrulandı.

| Deney | Model | Sonuç | Kanıt |
| --- | --- | --- | --- |
| Gemini protokolü tam tur | `gemini-3.8-flash-high` | 5/5 geçti; 3 istek, 471 çıktı token | [rapor](evidence/2026-09-26-k0/gemini-tool-round-trip.json) |
| Kaydedilmiş geçmişten yeni süreçte devam | aynı | 4/4 geçti; yeni araç çalışmadı | [rapor](evidence/2026-09-26-k0/gemini-history-resume.json) |
| Aynı proxy, OpenAI Chat protokolü | aynı | 5/5 geçti | [rapor](evidence/2026-09-26-k0/gemini-openai-protocol-round-trip.json) |
| LM Studio yerel, OpenAI protokolü | Gemma 4 26B-A4B QAT | 5/5 geçti; ~31 sn | [rapor](evidence/2026-09-26-k0/lmstudio-gemma4-tool-round-trip.json) |
| LM Studio yerel | Qwen3.8-27B IQ2_S | 5/5 geçti; ~32 sn | [rapor](evidence/2026-09-26-k0/lmstudio-qwen38-iq2s-tool-round-trip.json) |
| LM Studio yerel | Bonsai-27B (1-bit) | 5/5 geçti; ~65 sn, 4000 çıktı token | [rapor](evidence/2026-09-26-k0/lmstudio-bonsai27b-tool-round-trip.json) |
| Görsel araç sonucu | Gemma 4 | 7/7 geçti; kod birebir okundu | [rapor](evidence/2026-09-26-k0/vision-gemma4.json), [girdi](evidence/2026-09-26-k0/vision-gemma4.png) |
| Görsel araç sonucu | Bonsai-27B | katı kontrol kaldı; karakterler doğru, araya boşluk eklendi | [rapor](evidence/2026-09-26-k0/vision-bonsai27b.json) |
| Görsel araç sonucu | Qwen3.8 IQ2_S | kaldı; `9` karakteri `8` okundu | [rapor](evidence/2026-09-26-k0/vision-qwen38-iq2s.json) |
| Görsel araç sonucu | `gemini-3.8-flash-high` | ilk istekte HTTP 403 | [rapor](evidence/2026-09-26-k0/vision-gemini.json) |

Her turda ilk model isteğinde yalnız `search_tools` vardı; Jev gerçek araç
tanımlarından doğru aracı seçti (güven 0.98–1.0), araç gerçek kaynağı okudu ve
model sonucu içeren devam isteğiyle cevap verdi. PyPI yanıtı ayrıca bağımsız
indirildi; sürüm `2.50.0` ve SHA256 `7d02721f…42405` raporla aynı. Görsel
deneyde her çalıştırma için rastgele kod üretilip görsele yazıldı; kod metin
olarak modele hiç gönderilmedi, görsel yalnız ertelenmiş araç çağrısından
sonra istek gövdesinde göründü. [Canlı katalog](evidence/2026-09-26-k0/proxy-catalog.json).

Bu sonuçlar dar girdilerdir; genel doğruluk oranı değildir. Yerel süreler
kabuk ölçümüdür ve `uv` başlangıcını içerir.

### Gemini hesap doğrulaması

01:10'da aynı yol `cloudcode-pa.googleapis.com` üzerinden
`403 PERMISSION_DENIED / VALIDATION_REQUIRED` ("Verify your account to
continue") döndü; proxy tek OAuth kaydını bu hatadan sonra beklemeye aldı.
Google'ın verdiği doğrulama sayfası tarayıcıda açıldı; kullanıcı QR adımının
hata verdiğini bildirdi, doğrulama tamamlanmadı. Hesap düzeyindeki bu durum
aynı OAuth kaydını kullanan üretimdeki `jarvis-brain` metin yolunu da büyük
olasılıkla etkiler; üretimde son iki günde istek olmadığı için etki
gözlenmedi. Doğrulama tamamlanana kadar Gemini yolu çalışıyor sayılmaz ve
art arda deneme yapılmaz.

### Yerel işçi modelleri

LM Studio (masaüstü uygulaması Bionic 1.1.6, `lms` CLI) loopback üzerinde
anahtarsız OpenAI-uyumlu sunucu olarak aynı runtime ile çalıştı; bu, farklı
bir sağlayıcıya geçişin kod değişmeden yapılabildiğinin ilk kanıtıdır. Bu PC
için ölçülen sonuç: Gemma 4 hem araç hem görsel turunu geçen tek yerel model;
Bonsai uzun düşünüyor ve biçimi bozuyor; 2-bit Qwen görsel ayrıntıda hata
yapıyor.

Sınırlar: GPU 12 GB ve masaüstü, IDE'ler ve NVIDIA host GPU'lu Android
emülatörüyle paylaşılıyor. 26 Eylül 01:11'de yüklü bırakılan ~10 GB'lık yerel
model VRAM'i doldurdu; emülatör ve IDE çöktü. Yerel model modülü bu nedenle
VRAM payı, kısa TTL ve iş bitince boşaltma gibi kaynak bütçesi olmadan
etkinleştirilmez. Ayrıca Electron tabanlı editörden başlatılan ajan süreçleri
`ELECTRON_RUN_AS_NODE` değişkenini miras alıyor; bu değişken varken `lms`
Electron tabanlı LM Studio uygulamasını başlatamıyor. Yerel yönetici uygulama
açarken bu ortamı temizlemelidir.

## Runtime kararı ve sınırları

Pydantic AI ilk deney adayıdır: hazır araç döngüsü, `FunctionToolset` üzerinde
ertelenmiş yükleme, özel arama işlevini kabul eden `ToolSearch`, yerleşik
TypeSafe modeli ve değiştirilebilir model sağlayıcıları var. Jev yalnız araç
tanımları ve arama sorgusunu değerlendirir; Google modeli metni üretir ve
araç sonucuyla devam eder. Özel ajan framework'ü veya upstream patch yazılmadı.

LangGraph'ın kalıcı checkpoint/store ayrımı ve mevcut ADK kodunun yeniden
kullanım ihtimali de değerlendirildi. Jev seçimi son mimari kararı değildir.
Model turu, depolama ve olayla devam sınırları deneyle doğrulanmalıdır.

Pydantic AI belgesi de konuşma saklamayı dayanıklı yürütmeden ayırıyor.

| K0 karar ölçütü | Pydantic AI 2.50.0 ile durum |
| --- | --- |
| Seçmeli araç yükleme | Geçti: ertelenmiş araçlar + `ToolSearch` + özel Jev seçim işlevi, üç sağlayıcı yolunda |
| Taşınabilir model protokolü | Geçti: Gemini yerel protokolü, OpenAI Chat protokolü, yerel LM Studio; kod değişmeden yalnız ortam ayarıyla |
| Dışarıdan kalıcı durum | Kısmen: mesaj geçmişi JSON'a yazılıp yeni süreçte devam edildi; Firestore/nesne deposu ve olayla devam sınanmadı |
| Multimodal içerik | Kısmen: araçtan dönen görsel yerel Gemma 4'e yerel olarak iletildi ve okundu; Gemini görsel turu hesap doğrulaması nedeniyle açık; ses/belge sınanmadı |
| İptal/devam | Sınanmadı |
| Paket ağırlığı | `pydantic-ai-slim[google,openai,typesafe]`: 39 paket, sanal ortam 82 MB |
| Upstream'e özel patch | Gerekmedi; yalnız resmî API kullanıldı |

Öneri: K1 çekirdeği Pydantic AI ile kurulur. Bu son mimari hükmü değildir;
kalıcı durum, iptal/devam ve PC kapalıyken Cloud Run'da çalışma K1'in gerçek
kabulüyle doğrulanmadan runtime kesinleşmiş sayılmaz. Cloud Run/Firestore/
outbox, soğuk başlangıç, PWA ve PC gerçekten kapalıyken çalışma bu kayıtla
geçmez; deney runtime'ı bulutta değil yerel PC'de çalıştı.

## Açık kabul

- Gemini hesabı doğrulandıktan sonra Gemini görsel turu tek istekle yeniden
  çalıştırılacak; doğrulanmadan art arda deneme yapılmayacak.
- Ses ve belge içeriği, iptal ve bulutta dayanıklı devam K1 kapsamında.
- Yerel model modülü için kaynak bütçesi kuralı tasarıma girecek.

Codex'in Cloud Shell yolu (TypeSafe anahtarını geçici olarak aktarma ve Chrome
dosya yükleme ayarı) yerel çalıştırma nedeniyle artık gerekli değil.

## Resmî kaynaklar

- [CLIProxyAPI v7.2.111 yayını](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.2.111)
- [Pydantic AI araç keşfi](https://pydantic.dev/docs/ai/capabilities/tool-search/)
- [Pydantic AI TypeSafe modeli](https://pydantic.dev/docs/ai/models/typesafe/)
- [Pydantic AI kalıcı yürütme](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- [LangGraph kalıcılık](https://docs.langchain.com/oss/python/langgraph/persistence)
- [TypeSafe API](https://docs.typesafe.ai/api)
