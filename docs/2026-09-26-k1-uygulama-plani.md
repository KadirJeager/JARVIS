# K1 — bağımsız bulut asistanı ve panel: uygulama planı

Tarih: 2026-09-26. Durum: plan; uygulama başlıyor. Esas gereksinimler
[mimari belgede](2026-09-25-moduler-kisisel-asistan-mimarisi.md), runtime
kanıtı [K0 kaydında](2026-09-26-k0-gercek-model-arac-deneyi.md).

K1 geçme şartı: PC kapalıyken gerçek model ve gerçek araçla bir iş biter;
soğuk başlangıç ve yeniden başlatmadan sonra konuşma, hafıza ve sonuç
korunur; model ayarı PWA'dan değişir ve bir sonraki turda kullanılır.

## Hazır bileşenler ve yazılacak dar parçalar

| İhtiyaç | Kullanılacak hazır bileşen | Yazılacak dar parça |
| --- | --- | --- |
| Model turu, araç döngüsü | Pydantic AI 2.50 `Agent` | Ayar sürümünden model/sağlayıcı kurma |
| Seçmeli araç | Çekirdek `ToolSearch` + K0'daki Jev seçim işlevi | — |
| Çökme sonrası devam, yan etki defteri | Harness `StepPersistence` (`continue_run`, `list_unresolved_tool_effects`) | `StepStore` protokolü için Firestore deposu |
| Kalıcı kişisel hafıza (vault) | Harness `Memory` (CAS, idempotency, kullanıcıya göre namespace) | `MemoryStore` protokolü için Firestore deposu |
| Gerçek bulut aracı | Çekirdek `WebFetch` (yerel çekme) | — |
| Bütçe | Harness `Spend Limits` | Ayardan limit okuma |
| Olay tekilliği, iş durumu | Eski `brain/app/harness_tasks.py` deseni (kararlı kimlik, `create_once`, transaction geçişleri; kod kaldırıldı, `d36bd4d`) | Yeni koleksiyon adları ve kullanıcı kapsamı |
| Kuyruk | Cloud Tasks (adlandırılmış görev = tekrar gönderime karşı koruma) | Onarım taraması (Cloud Scheduler) |
| Kimlik | Firebase Authentication (Google girişi, token yenileme) | Sunucuda ID token doğrulama, sahip e-postası kurulum ayarından |
| Teslim | Firestore istemci dinleyicisi + güvenlik kuralları | Kurallar dosyası |

Harness depoları 0.x sürümündedir; sürüm `uv.lock` ile sabitlenir, protokol
değişikliği sürüm yükseltirken adaptör testleriyle yakalanır.

## Değerlendirilen alternatifler (2026-09-26)

**Google ADK 2.10.0** (2026-09-25, Apache-2.0) K1 runtime'ı olarak seçilmedi.
Araç arama yok; `tool_filter`, `BaseToolset.get_tools` ve deneysel skill
yaşam döngüsü var. Devam eden çalışmada araçlar "en az bir kez" çalışıyor,
yan etki defteri yok. Araç onayı kalıcı `DatabaseSessionService` ve
`VertexAiSessionService` ile desteklenmiyor. Python'da istek başına model
seçimi yok (`RoutedLlm` yalnız TypeScript ve deneysel). JARVIS'in kullanacağı
eklerle yerel ölçüm: ADK 86 paket/186 MB/1,39 sn içe aktarma; Pydantic AI +
Harness 80 paket/108 MB/1,06 sn. ADK'nin güçlü yanı Gemini Live ve deneysel
LiveKit SIP ile telefon araması; K5'te Pydantic AI gerçek zamanlı ses kabulü
geçmezse yeniden değerlendirilir. Kaynaklar:
[sürümler](https://github.com/google/adk-python/releases),
[modeller](https://adk.dev/agents/models/index.md),
[devam](https://adk.dev/runtime/resume/index.md),
[onay](https://adk.dev/tools-custom/confirmation/index.md),
[LiveKit](https://adk.dev/integrations/livekit/index.md).

**Google GraphRAG** tek ürün değil; Spanner Graph + LangChain
`LLMGraphTransformer` + Agent Runtime referans mimarisi. Spanner Graph
Enterprise sürüm ister; en küçük örnek (100 işlem birimi) ayda yaklaşık
90 USD ve sıfıra inmez; ücretsiz deneme 90 gün sonra veriyi siler. RAG
Engine'de grafik modu yok; Memory Bank düz olgu tutar ve yalnız Google
modelleriyle çalışır. K1'de alınmaz. Vault kayıtları isteğe bağlı varlık
kimliği ve tipli ilişki (özne, ilişki, nesne) alanlarını kaynak, zaman,
kapsam ve sürümle taşır; böylece grafik dışa aktarımdan yeniden türetilebilir.
Yeniden değerlendirme tetikleyicisi: gerçek sorulardaki hataların belirgin
kısmı çok adımlı ilişki gerektirir, vault binlerce etkin kayda ulaşıp düz
aramayla doğru kayıt bulunamaz veya ilişki sorgusu isteyen bir özellik
gelir. Kaynaklar:
[referans mimari](https://docs.cloud.google.com/architecture/gen-ai-graphrag-spanner),
[Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/overview),
[Spanner fiyat](https://cloud.google.com/spanner/pricing),
[Memory Bank](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/setup),
[Firestore vektör arama](https://docs.cloud.google.com/firestore/native/docs/vector-search).

## Akış

1. PWA mesajı istemci tarafı kimlikle `POST /v1/conversations/{cid}/messages`
   ile gönderir. Sunucu Firebase ID token'ını doğrular.
2. Tek Firestore transaction'ı: kullanıcı mesajı (istemci mesaj kimliğiyle
   tekil), `jobs/{jid}` (`queued`, ayar sürümü, deneme sayısı).
3. Commit sonrası `jobs/{jid}` adlı Cloud Task oluşturulur. Başarısızsa iş
   `queued` kalır; zamanlanmış onarım taraması sahipsiz işleri yeniden kuyruğa
   verir.
4. `POST /internal/turns/{jid}` (Cloud Tasks OIDC token'ı doğrulanır) işi
   lease ile alır, `StepPersistence` ile `run_id=jid` çalıştırır. Önceki
   deneme varsa `continue_run`; çözülmemiş yan etki varsa iş `reconciling`
   olur, kör tekrar yapılmaz.
5. Asistan mesajı ve iş sonucu aynı transaction'da yazılır. PWA mesajı
   Firestore dinleyicisiyle görür; sunucu açık bağlantı tutmaz.

## Veri (Firestore)

- `users/{uid}`: sahip/rol, oluşturulma.
- `users/{uid}/settings/current` ve `users/{uid}/settings_history/{version}`:
  sağlayıcı türü, uç adresi, model kimliği, Secret Manager referansı, bütçe.
  Sır değeri Firestore'a yazılmaz.
- `users/{uid}/conversations/{cid}` ve `.../messages/{mid}`.
- `jobs/{jid}`: kullanıcı, konuşma, giriş mesajı, durum, lease, denemeler.
- `step_runs/...`: `StepStore` kayıtları. `memory/{namespace}/...`: `MemoryStore`.

## Dilimler

1. **Tamamlandı (2026-09-26):** Firestore `StepStore` ve `MemoryStore`
   depoları ([core/](../core/README.md)). Harness v0.35.0 sözleşme testleri
   MIT lisansıyla uyarlandı; Firestore'a özgü eşzamanlı CAS, aynı işlemin
   eşzamanlı tekrarı, eşzamanlı olay ekleme, aynı anahtarlı anlık görüntünün
   tek uygulanması, 1 MiB üstü anlık görüntünün parçalanması ve 10 MiB
   transaction sınırı eklendi. Resmî emülatöre (1.22.0) karşı 51 test üç
   ardışık çalıştırmada geçti. Gerçek Firestore'da bileşik indeksler
   `core/deploy/firestore.indexes.json` ile kurulacak; gerçek veritabanında
   henüz sınanmadı.
2. Çekirdek servis: ayar modeli, ajan kurma, iş alma/çalıştırma, mesaj API'si;
   yerelde emülatör + gerçek model (LM Studio küçük model veya doğrulanmış
   proxy) ile uçtan uca tur; süreç öldürülüp yeniden başlatılarak devam.
   Tasarım ayrıntıları:
   - `StepPersistence` `run_id`'si tek kullanımlık; her deneme
     `<iş>.<deneme>` kimliğiyle çalışır. Yeni deneme önceki denemenin
     tamamlanmış anlık görüntüsünden (`continue_run`) devam eder;
     `inspect_recovery` çözülmemiş yan etki gösterirse iş `reconciling`
     olur, yeniden çalıştırılmaz.
   - Konuşma başı (`users/{uid}/conversations/{cid}`) son başarılı
     çalıştırmanın kimliğini ve revizyonu tutar; sonraki tur geçmişi o
     anlık görüntüden okur, mesaj geçmişi ikinci kez saklanmaz. Aynı
     konuşmada iş çalışırken yeni iş sırasını bekler.
   - Sır referansı ayarda şema ile tutulur (`env:` yerel, Secret Manager
     bulut); değer Firestore'a yazılmaz.

   **Durum (2026-09-26):** ayarlar, sırlar, ajan kurucusu, tur defteri,
   işçi, Cloud Tasks gönderici, onarım taraması ve FastAPI uçları yazıldı.
   Emülatörde 69 test geçiyor: tur sırası ve dışlama, kiralama ve eski
   denemenin yazamaması, uzlaştırma, geç çözülen turun başı geri almaması,
   kontrol noktası dışında kalan araç etkisinin uzlaştırmaya gitmesi, hazır
   cevabın modelsiz kaydı, deneme sınırı, onarım sorgusu. Modeli çağıran yol
   ve HTTP uçları henüz gerçek modelle ve gerçek kimlik doğrulamayla
   çalıştırılmadı: Gemini hesabı doğrulama bekliyor, yerel LM Studio
   kullanıcı kararıyla kullanılmıyor. Firestore kuralları ve Cloud Storage
   medya deposu dağıtım dilimine kaldı.
3. PWA: giriş, konuşmalar, canlı mesajlar, Beyin (katalogdan model seç,
   bağlantıyı dene, kaydet), Hafıza (gör, düzelt, sil).
4. Dağıtım: yeni Cloud Run servisi (proxy yan kapsayıcısıyla), Cloud Tasks
   kuyruğu, onarım zamanlaması, Firestore kuralları, Firebase Auth. 2026-09-26
   kullanıcı kararıyla yeni çekirdek eski projenin yerine geçer; eski Cloud
   Run servisleri, zamanlamalar ve uygulamalar silinebilir. Yeni servisin
   kullandığı sırlar korunur. Native uygulama geliştirilmez.
   **Durum (2026-09-26):** PWA (giriş, konuşmalar, Beyin, Hafıza) Cloud Run
   servisinden sunuluyor; giriş Firebase `signInWithPopup` ile. `core/deploy/deploy.sh`
   gerçek projede çalıştı: API'ler, Artifact Registry, `jarvis-runtime` ve
   `jarvis-invoker` hesapları, imajlar, 7 bileşik indeks, Firestore kuralları,
   `jarvis-turns` kuyruğu, `jarvis` servisi (proxy yan kapsayıcısı 1 OAuth
   kaydıyla açıldı), 5 dakikalık onarım zamanlaması. Dışarıdan: PWA 200,
   kimliksiz `/v1` ve `/internal` istekleri 401. Eksik: Firebase'de Google
   sağlayıcısı etkinleştirilmediği için giriş alan adı eklenmedi; ilk giriş,
   Beyin ayarı ve gerçek tur yapılmadı. `/healthz` Cloud Run'ın ayrılmış yolu
   olduğu için dışarıdan 404 veriyordu; `/health` olarak değiştirildi, sonraki
   dağıtımda yayınlanacak.
   **Güncelleme (2026-09-26, 03:10 sonrası):** Google girişi etkinleştirildi,
   servis alan adı eklendi. Sahip PWA'ya girdi; ayar yokken gönderilen mesaj
   gerçek zincirden geçip `SettingsMissing` hatasıyla canlı olarak ekrana
   düştü. Model ayarı sunucu tarafında sürüm 1 olarak kaydedildi (Gemini
   biçimi, yan kapsayıcı proxy, `gemini-3.8-flash-high`). Eski hafıza
   `core/deploy/migrations/legacy_brain_memory.py` ile kaynak notlu olarak
   vault'a taşındı; eski servisler, zamanlamalar, 21 eski Firestore kök
   koleksiyonu, eski indeksler, boş eski imaj depoları ve eski hizmet hesapları
   silindi. Gerçek model turu abonelik kotası ve Google hesap doğrulaması
   beklediği için henüz yapılmadı.
5. K1 kabulü: PC kapalıyken PWA'dan gerçek iş, soğuk başlangıç, servis
   yeniden başlatma ve ayar değişikliği kanıtları.

## Bağımlılıklar ve açık engeller

- Kadir'in bulut model yolu (Antigravity OAuth + CLIProxyAPI) şu anda
  `VALIDATION_REQUIRED` veriyor. Dilim 1–3 yerelde ilerler; bulut kabulü
  hesap doğrulanmadan yapılamaz. Ücretli API yedeği açılmaz.
- Firebase Authentication'ın projede etkin olup olmadığı dağıtım dilimi
  başında gerçek hesapla kontrol edilecek.
- PWA Cloud Run servisinden sunulur. İlk sürüm derlemesizdi; 2026-09-27'den
  beri React PWA imajın Node 24 aşamasında derlenir. Firebase Hosting'e geçiş
  ayrı karar olarak kalır.
