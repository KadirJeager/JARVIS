# jarvis-core

JARVIS'in yeni bulut çekirdeği. Runtime Pydantic AI 2.50 ve resmî Pydantic AI
Harness 0.35 yetenekleridir; bu paket yalnız hazır bileşenlerde olmayan dar
parçaları içerir. Plan: [K1 uygulama planı](../docs/2026-09-26-k1-uygulama-plani.md).

## İçerik

- `jarvis_core.firestore_stores.FirestoreMemoryStore`: Harness `Memory`
  yeteneğinin `MemoryStore`/`SearchableMemoryStore` protokolü için Firestore
  deposu. Yazma ve silme tek transaction'da CAS ve işlem makbuzu uygular.
- `jarvis_core.firestore_stores.FirestoreStepStore`: Harness
  `StepPersistence` yeteneğinin `StepStore` protokolü için Firestore deposu.
  Çalıştırma kaydı, olay günlüğü, devam edilebilir anlık görüntüler ve araç
  yan etki defteri. Anlık görüntü mesajları 1 MiB belge sınırı nedeniyle
  parçalanır; büyük medya verilen `MediaStore`'a taşınır.

- `jarvis_core.settings`: sürümlü kullanıcı ayarları (model bağlantısı, araç
  seçim stratejisi, tur sınırları); kayıt CAS ile yapılır, geçmiş sürümler
  tutulur. Ayarda sır değeri değil yalnız referansı bulunur.
- `jarvis_core.secrets`: `env:AD` ve Secret Manager sürüm referanslarını
  kullanım anında çözer; tanınmayan şemayı reddeder.
- `jarvis_core.assistant`: ayardan tur ajanını kurar. Çekirdek araç Memory;
  `WebFetch` (Pydantic AI'ın SSRF korumalı yerel aracı) ertelenmiş olarak
  `ToolSearch` ile bulunur; seçim ayara göre yerleşik arama ya da TypeSafe Jev.
- `jarvis_core.turns`: tur defteri ve konuşma başı. Tekil gönderim, konuşma
  içinde sıra ve karşılıklı dışlama, kiralama, deneme başına `run_id`,
  uzlaştırma ve onarım taraması sorgusu.
- `jarvis_core.worker`: alınan turu çalıştırır; önceki denemenin kayıtlarına
  bakarak belirsiz yan etkide uzlaştırmaya, hazır cevapta modeli çağırmadan
  kayda, kontrol noktasından devama veya baştan başlamaya karar verir.
- `jarvis_core.api` (FastAPI): mesaj gönderme, ayarlar, bağlantı testi,
  Cloud Tasks'in çağırdığı tur ucu ve onarım. Kişiler Firebase ID token'ı,
  Cloud Tasks/Scheduler Google OIDC token'ı ile doğrulanır.

Depolar Harness'in herkese açık olmayan serileştirme yardımcılarını kullanır;
bu yüzden `pyproject.toml` sürümleri tam sabitler. Sürüm yükseltmede testler
çalıştırılmadan kilit güncellenmez.

## Test

Testler resmî Firestore emülatörüne karşı çalışır; sahte depo kullanılmaz.
Google Cloud CLI, `cloud-firestore-emulator` bileşeni ve Java gerekir.

```bash
gcloud components install cloud-firestore-emulator
uv run pytest
```

Fixture emülatörü boş bir loopback portunda başlatır ve her teste ayrı proje
kimliği verir. Çalışan bir emülatörü kullanmak için `FIRESTORE_EMULATOR_HOST`
ayarlanabilir. Bellek sözleşme testleri Harness v0.35.0
`tests/memory/test_stores.py`, adım testleri `tests/step_persistence/test_mongo.py`
dosyalarından MIT lisansıyla uyarlanmıştır.

## Dağıtım notu

Gerçek Firestore, `run_id` ile filtreleyip `seq` ile sıralayan sorgular ve
onarım taraması için bileşik indeks ister:
[deploy/firestore.indexes.json](deploy/firestore.indexes.json). Koleksiyon
adları varsayılan `steps` önekine göredir. İstemci okuma kuralları
[deploy/firestore.rules](deploy/firestore.rules); bunlar henüz otomatik
sınanmadı, dağıtımda gerçek hesapla doğrulanacak.

Servis ortam değişkenleri: `JARVIS_PROJECT_ID`, `JARVIS_ALLOWED_EMAILS`,
`JARVIS_SERVICE_URL`, `JARVIS_TASKS_QUEUE`, `JARVIS_INVOKER_SERVICE_ACCOUNT`,
`JARVIS_FIREBASE_WEB_CONFIG`. Varsayılanları yoktur; eksik değer açılışı açık
hatayla durdurur. İsteğe bağlı olanlar: `JARVIS_VAPID_KEY_REF` (yoksa Web Push
kapalı), `JARVIS_SECRET_PREFIX` (yoksa panelden anahtar saklama kapalı),
`JARVIS_BUILD` (panelde gösterilen imaj etiketi). Dağıtım betiği hepsini
kendisi üretir.

## PWA

Kaynak [web/](web/) altında (React + Vite). İmaj derlenirken Node 24 aşaması
`npm ci && npm run build` çalıştırır, çıktı paket içinde `jarvis_core/web`
dizinine kopyalanır ve servis onu sunar; bu dizin depoda tutulmaz. Uygulama
rotaları (`/c/<id>`, `/share` vb.) ilk ziyarette de uygulama kabuğunu alır;
hash'li `assets/` bir yıl önbelleğe alınır, diğer dosyalar her açılışta
yeniden doğrulanır. Geliştirmede `cd web && npm run dev`; `/v1` ve
`/config.json` istekleri `127.0.0.1:8080`'deki çekirdeğe yönlenir.

## Kurulum ve güncelleme

1. Bir Google Cloud projesinde Firebase'i etkinleştir, bir web uygulaması
   kaydet ve Authentication'da Google giriş sağlayıcısını aç (OAuth istemcisini
   Firebase konsolu oluşturur).
2. `deploy/installation.env.example` dosyasını `deploy/installation.env`
   olarak kopyalayıp doldur (bu dosya depoya girmez).
3. `gcloud auth login` ile giriş yapıp `deploy/deploy.sh` çalıştır.

Betik her adımda var olanı yeniden kullanır; güncelleme için aynı komut
çalıştırılır. Yaptıkları: gerekli API'ler, Artifact Registry deposu, çalışma ve
çağırıcı hizmet hesapları ile en az yetkiler, Cloud Build ile imajlar,
Web Push için VAPID anahtarı (`<servis>-vapid` sırrı; yoksa bir kez üretilir,
değeri diske ve çıktıya düşmez), panelden girilen anahtarlar için
`<servis>-key-` önekiyle sınırlı özel rol (oluştur, sürüm ekle, oku, sil) ve
yalnız ad/üstveri listeleme yetkisi, Firestore indeksleri ve kuralları, Cloud
Tasks kuyruğu, Cloud Run servisi
(PWA ve API tek servis; isteğe bağlı CLIProxyAPI yan kapsayıcısı), 5 dakikada
bir onarım zamanlaması ve servis alan adının Firebase giriş izinlerine
eklenmesi. Servis adresi `https://<servis>-<proje numarası>.<bölge>.run.app`
biçiminde önceden bellidir; özel alan adı gerekmez.

Model bağlantısı PWA'nın Beyin ekranından ayarlanır. CLIProxyAPI yan
kapsayıcısıyla uç adresi `http://localhost:8317`, anahtar referansı ise
proxy anahtarının Secret Manager sürümüdür.
