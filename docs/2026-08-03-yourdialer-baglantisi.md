# YourDialer — JARVIS bağlantı notu

**Tarih:** 2026-08-03
**Konum:** `/home/user/Projeler/Android Projeleri/YourDialer`
**Durum:** Henüz kod yok. Klasörde tek dosya `Nihai amaç.txt` (4 satırlık niyet notu).

Bu not, YourDialer projesinin burada — JARVIS bağlamı içinde — başladığını kayda geçirmek içindir. İki proje ayrı repolar olacak ama telefon yeteneği üzerinden kesişiyorlar.

## YourDialer nedir

Google Pixel Phone uygulamasının birebir klonu (UI + varsayılan dialer + arama kaydı + spam engelleme) **artı** N-cihazlı arama senkronu: arama kaç cihaz varsa hepsine düşsün, hangisinden istenirse cevaplansın. Uç noktalar yalnız Android değil — **bu Linux masaüstü de bir uç nokta olacak**.

**Yapay zekâ beyni YourDialer'ın kapsamında DEĞİL.** Zekâ bu projede (JARVIS) yaşıyor; YourDialer onun bir istemcisi olacak.

## Neden JARVIS'i ilgilendiriyor

`docs/JARVIS_Proje_Belgesi.md` **§6 Telefon Mimarisi (Katil Özellik)** ile doğrudan aynı alana bakıyor.

§6.1'de zaten belgelenmiş olan kısıt, YourDialer'ın da çarptığı duvarın aynısı: *"telefonun kendisi Jarvis'e çağrı sesi veremez — Android üçüncü parti uygulamaya canlı çağrı sesi erişimi vermez."* Teknik ayrıntı: `InCallService` PCM vermez; `VOICE_CALL` kaydı `CAPTURE_AUDIO_OUTPUT` (signature|privileged) ister; `AudioPlaybackCapture` çağrıyı kapsam dışı bırakır.

§6'nın çözümü — **root'lamak değil, yönlendirmek** (operatör koşullu yönlendirme → evdeki ikinci GSM hattı → ses buluta) — bu duvarı Knox yakmadan ve cihazdan bağımsız şekilde aşıyor. Ancak kapsamı sınırlı: **yalnız Kadir'in AÇMADIĞI aramaları** kapsar. Kadir'in bizzat konuştuğu canlı aramanın sesi hâlâ erişilemez durumda.

Bu ayrım YourDialer için belirleyici: "aramayı tabletten/Linux'tan **cevaplama**" root'suz yapılabilir, ama "tabletten/Linux'tan **konuşma**" (ses köprüsü) yapılamaz. Root'un satın aldığı tek şey odur — Jarvis'in telesekreter/screening senaryosu için root gerekmiyor.

## Alınan kararlar (2026-08-03)

- **Bağımlılık serbest.** Projeler kişisel kullanım için; YourDialer'ın JARVIS'e bağımlı olması kabul edildi. Cihazlar-arası senkron katmanı sıfırdan yazılmayacak — JARVIS'in mevcut Cloud Run brain'i, FCM dağıtımı ve auth'u yeniden kullanılacak. Linux ucu için `brain/web` altındaki PWA aday.
- Kullanıcı ayrıca root/sistem-imza yolunu seçti, ama **henüz rootlu cihazı yok**; gelecekte ayrı bir cihaz olacak. Mevcut iki cihaz stok kalacak.

## Açık karar — YourDialer ↔ JARVIS bağlantı noktası

JARVIS'in zaten bir Android uygulaması var (`android/`). Telefonda iki uygulama olacak ve ikisi de aynı buluta bakacak. Üç seçenek:

- **(a)** YourDialer doğrudan buluta bağlanır — kendi auth'u, kendi FCM kanalı, kendi arka plan servisi. Temiz ayrım, ama cihazda çift kalıcı bağlantı (RAM/pil israfı).
- **(b)** YourDialer, cihazdaki JARVIS uygulamasıyla konuşur (bound service / content provider); bulut bağlantısını JARVIS sahiplenir. Tek auth, tek soket. **Önerilen** — varsayılan dialer rolünü tutan süreç fiilen hep açık kalır, ince olmalı.
- **(c)** YourDialer, JARVIS uygulamasının içine modül olur. En az tesisat, ama ayrı ürün olma ihtimalini kapatır.

Karar verilmedi. Verildiğinde bu not güncellenmeli ve muhtemelen §6'ya bir alt başlık düşmeli.

## Test zemini

- `PHONE_SERIAL` — SM-S911B (Galaxy S23), Android 16 / One UI 8.0.5
- `WATCH_SERIAL` — SM-X526B (Galaxy Tab S9 FE), Android 16 / One UI 8.0.5
- İkisi de stok: bootloader kilitli, `warranty_bit=0` (Knox e-fuse hiç yanmamış)
- Bu Linux masaüstü — hem geliştirme ortamı hem hedef uç nokta

---

## Aynı gün, sonradan: JARVIS tarafında değişen zemin

Bu not yazıldıktan sonraki otonom oturumda JARVIS'e üç şey eklendi ve üçü de yukarıdaki
**açık kararı** (a/b/c) doğrudan ilgilendiriyor. Karar hâlâ verilmedi — burada sadece
zemin kaydediliyor.

- **FCM istemcisi artık VAR ve çalıştığı üretimde kanıtlandı.** Bu not yazıldığında
  Android tarafında `firebase-messaging` bağımlılığı bile yoktu: `fcm_tokens` koleksiyonu
  üretimde boştu ve her bildirim sessizce sohbet fallback'ine düşüyordu. Artık cihaz
  token'ı kaydediliyor ve gerçek push cihazda çiziliyor. **(b) seçeneğinin "tek soket,
  tek auth" gerekçesi bu yüzden somutlaştı:** JARVIS uygulaması artık gerçek bir push
  kanalı taşıyor, YourDialer ikinci bir tane kurmak zorunda değil.
- **Onay merkezi (Faz Y3) var.** Kırmızı bölge eylemleri kuyruğa düşen, zaman aşımına
  uğrayan, idempotent kararlarla çözülen onay kartlarına dönüşüyor. Bir dialer'ın
  ihtiyaç duyacağı "bu aramayı devral / bu numarayı engelle / kaydı sil" sınıfı eylemler
  için hazır bir yetki ve onay yüzeyi demek.
- **Araç kayıt defteri + kazanım merdiveni (Faz Y4) var.** Yeni bir yetenek artık kod
  değil, önerilip onaylanan bir kayıt. YourDialer'ın JARVIS'e açacağı yüzey (arama
  durumu okuma, cihazlar arası devretme) bu defterde bir kayıt olarak, kendi bölgesiyle
  yaşayabilir.

Değişmeyen kısıt: §6.1'in duvarı yerinde duruyor — Kadir'in bizzat konuştuğu canlı
aramanın sesi hâlâ erişilemez. Bugünkü iş bu duvara dokunmadı.

Ek gözlem (ses tarafından): bugün kapatılan yankı hatası, YourDialer'ın da çarpacağı bir
sınıfı gösteriyor — **hoparlör açıkken cihazın kendi çıktısı, kendi mikrofonuna girer ve
cihaz üstü AEC bunu güvenilir biçimde ayıramaz.** Cihazlar-arası arama senkronunda aynı
odada iki uç açıksa bu problem ikiye katlanır; tasarımda baştan hesaba katılmalı.
