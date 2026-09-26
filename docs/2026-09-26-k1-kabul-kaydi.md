# K1 kabul kaydı

Tarih: 2026-09-26 (Europe/Istanbul). Plan ve dilim ayrıntısı:
[K1 uygulama planı](2026-09-26-k1-uygulama-plani.md). Bu kayıt yalnız gerçek
ortamda gözlenenleri tutar; gözlenmemiş madde "bekliyor" olarak kalır.

Kurulum: Google Cloud projesi `your-gcp-project`, bölge `europe-west1`, Cloud Run
servisi `jarvis` (PWA ve API tek servis; CLIProxyAPI 7.2.111 yan kapsayıcısı),
Firestore `(default)` `eur3`, Cloud Tasks kuyruğu `jarvis-turns`, onarım
zamanlaması `jarvis-repair`. Dağıtım `core/deploy/deploy.sh` ile yapıldı.

| Kabul maddesi | Durum | Gözlenen kanıt |
| --- | --- | --- |
| Özel alan adı olmadan HTTPS PWA | Geçti | `https://jarvis-000000000000.europe-west1.run.app` sayfa, manifest ve service worker 200 |
| Kimlik | Geçti | Firebase Google girişi (`signInWithPopup`) ile sahip hesabı girdi; kimliksiz `/v1/*` ve `/internal/*` istekleri 401 |
| Mesaj → kalıcı tur → kuyruk → işçi → sonuç → PWA | Geçti | 00:10:58 UTC tur `seq 1`: API 202, Cloud Tasks teslimi, işçi `SettingsMissing` ile turu kapattı, hata mesajı Firestore dinleyicisiyle ekrana düştü |
| Geçici hata sonrası otomatik yeniden deneme, kalıcı hatada durma | Geçti | 00:22:28 UTC tur `seq 2`: 1. teslimde proxy 429 → tur kuyruğa döndü, Cloud Tasks 503 üzerine 10 sn sonra yeniden teslim etti; 2. denemede proxy 403 → tur `failed`, yeniden denenmedi |
| Onarım taraması | Geçti | Cloud Scheduler 5 dakikada bir `/internal/repair` çağırıyor, OIDC ile 200 |
| Sürümlü ayar | Kısmen | Model ayarı sürüm 1 sunucu tarafında kaydedildi; PWA'dan değiştirip sonraki turda kullanılması bekliyor |
| Bulut vault | Kısmen | Eski hafıza (21 olgu, 4 ders, 15 profil alanı) kaynak notuyla `MEMORY.md`, `profil.md`, `dersler.md` dosyalarına yazıldı; modelin okuyup kullanması bekliyor |
| Yeniden dağıtım sonrası kalıcılık | Kısmen | Konuşma oluşturulduktan sonraki yeniden dağıtımlarda (yeni revizyonlar) konuşma, turlar ve vault korundu; model işiyle doğrulanması bekliyor |
| PC kapalıyken gerçek model + araç işi | Bekliyor | Model çağrısı 403 döndü (aşağıda) |
| Soğuk başlangıç | Ölçüldü | `jarvis-00005` yeni örnek: başlatma → proxy hazır 1,6 sn → çekirdek süreci 10,3 sn → başlangıç kontrolü 12,1 sn. Yerelde içe aktarma 0,7 sn; farkın imaj yükleme ve ilk dosya okumalarından geldiği tahmin ediliyor, ayrıştırılmadı. 5 dakikalık onarım çağrısı çoğu zaman bir örneği sıcak tutuyor; garanti değil. Minimum örnek 1 sürekli ücret getireceği için açılmadı |

## Model yolu engeli

Aynı Antigravity OAuth hesabı 26 Eylül 01:10'dan beri Cloud Code API'den
`403 PERMISSION_DENIED / VALIDATION_REQUIRED` alıyor (yerel K0 denemesi);
bulutta 00:22 UTC'de önce 429 (kota), sonra 403 döndü. Bu tarihte kullanıcının
abonelik kotası da dolmuştu. 403'ün gerekçesi bulutta henüz okunmadı: işçi
o anda yalnız HTTP kodunu kaydediyordu. Hata kaydına Google'ın
makine okunur gerekçe kodlarını (yalnız büyük harfli kodlar; mesaj ve
bağlantılar değil) ekleyen düzeltme yapıldı, PWA bilinen kodları Türkçe
açıklıyor. Kota yenilenince tek mesajla gerekçe okunacak.

## Notlar

- Harness `Memory` her turda `MEMORY.md`'yi yaklaşık 7.187 karakter
  bütçeyle ekliyor; taşınan dosya 6.461 karakter. Dosya büyürse ekleme
  kesilir, model dosyayı araçla okumaya devam eder.
- Firestore kuralları gerçek hesapla sahibin kendi konuşmasını okuyabildiğini
  gösterdi; başka hesabın okuyamadığı ikinci hesapla sınanmadı.
