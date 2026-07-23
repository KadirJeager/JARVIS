# JARVIS — Kişisel Otonom Asistan Projesi

**Belge tarihi:** 23 Temmuz 2026
**Revizyon:** 23 Temmuz 2026 — SIP/dış telefon hizmetleri çıkarıldı (GSM köprüsü + telesekreter modeli), Tailscale çıkarıldı (GCP-yerli connector deseni), Telegram kanal olmaktan çıkıp Kadir adına kullanılan araca dönüştü (uygulama ana kanal — Gemini benzeri tek yüzey), kademeli ajan fabrikası eklendi, bulut-öncelikli yerleşim ilkesi (İlke 12) eklendi.
**Durum:** **Nihai hedef mimari ("North Star").** Bu belge adım adım yürünecek bir yol planı değil, varılacak yerin tanımıdır; ayrıntılı uygulama planlaması (görev kırılımı, zamanlama) ayrıca ve sonra yapılacaktır. Belgenin görevi, yol boyunca bu hedeften sapılmamasını sağlamaktır.
**Sahibi:** Kadir

---

## 1. Vizyon

Tamamen bulutta (Google Cloud) yaşayan, telefon / akıllı saat / bilgisayar / akıllı ev üzerinden her an erişilebilen, sesli komutla gerçek dünyada işlem yapabilen (sipariş, taksi, arama karşılama), kullanıcı hiçbir şey söylemeden de gerektiğinde kendiliğinden konuşan, **zamanla akıllanan** ve **asistan kimliğine sahip** otonom bir kişisel yapay zeka asistanı.

Jarvis, Kadir'in yerine geçmez; Kadir'in **asistanı** olarak konuşur ("Kadir'in asistanı Jarvis"). Kendi telefon numarası, kendi Telegram botu ve gerektiğinde kendi e-posta adresi vardır. Kadir'i tanır, hatalarından ders çıkarır ve her hafta bir önceki haftadan daha iyi çalışır.

---

## 2. Tasarım İlkeleri (Anayasa)

Bu ilkeler tüm mimari kararların üstündedir; bir çözüm bu ilkelerle çelişiyorsa çözüm değişir, ilke değişmez.

1. **Beyin bulutta, istemciler incedir.** Hiçbir istemcide (uygulama, saat, Pi) zeka yoktur; hepsi aynı beyne bağlanan terminallerdir. Asistan, herhangi bir cihaz çökse de yaşamaya devam eder.
2. **Tek gerçek zamanlı ses geçidi.** Tüm canlı ses — uygulama mikrofonu, saat, telefon hattı (evdeki GSM köprüsü) — aynı ses geçidi bileşeninden girer. Arama devralma, kayıt ve canlı transkript bu tekilliğin doğal sonucudur.
3. **Kanal adaptörleri.** Kadir'in Jarvis'le konuştuğu her kanal (uygulama, saat, ev hoparlörü…) beynin tek API'sine bağlanan ince bir adaptördür. Yeni kanal = yeni adaptör, beyinde değişiklik yok. Mesajlaşma platformları (Telegram, WhatsApp) kanal değil *araçtır*: Jarvis onları Kadir adına kullanır (§10).
4. **Hatalar istisna değil, gözlemdir.** Deterministik kod hata yakalayıp yutmaz; her araç çağrısının sonucu (429 kota hatası, timeout, CAPTCHA sayfası, tuhaf yanıt) olduğu gibi modelin bağlamına düşer. Model yorumlar, karar verir; kod sadece taşır. "Claude kotan dolmuş, 3 saat sonra açılıyor, o zamana kadar Gemini'a danıştım" davranışı bu ilkeden çıkar.
5. **Sistem her etkileşimden öğrenir.** Başarılar tarife, hatalar derse, tepkiler tercihe dönüşür ve kalıcı hafızaya yazılır (bkz. §8). Aynı hatanın ikinci kez yapılması tasarım hatası sayılır.
6. **Eylem yetki matrisi.** Ağ güvenliği yetmez; *eylem* güvenliği ayrı bir politika katmanıdır (bkz. §9). Hangi eylemin sormadan yapılacağı kodda yazılıdır, modelin insafına bırakılmaz.
7. **Ev dışarıya kapı açmaz; dışarıya doğru bağlanır.** CGNAT/sabit IP sorunu VPN/mesh ile değil, outbound-only connector deseniyle çözülür: Pi hiçbir port açmaz; bulut→ev komutlarını Pub/Sub aboneliğinden *çeker*, ev→bulut olaylarını HTTPS ile *iter*. Kimlik GCP servis hesabı/IAM ile doğrulanır. Meşru dış taraflar (Claude/MCP istemcileri, Telegram webhook, FCM push) için buluttaki kimlik doğrulamalı public kapılar vardır.
8. **Asistan kimliği.** Jarvis her kanalda kendini asistan olarak tanıtır. Bu; arama karşılamayı hukuken/etik olarak temizler, mesajlaşmada yetki matrisinin sarı bölgesini genişletir, dış AI'larla konuşurken kimlik karmaşasını önler.
9. **Sıfıra yakın sabit maliyet, kullandıkça öde.** Boşta dururken ~0; kullanınca küçük değişken maliyet. Scale-to-zero (Cloud Run) esastır. Ucuzluk uğruna kırılgan veya ToS-ihlalli çözüm seçilmez — bakım maliyeti ve ban riski de maliyettir.
10. **Dar-sağlam akışlar, geniş-kırılgan akışlara tercih edilir.** "Her şeyi sipariş edebilsin" yerine "en sık yapılan işlemleri kusursuz yapsın" (ör. favori siparişi tekrarla). Kapsam zamanla genişler, güvenilirlikten taviz verilmez.
11. **Kademeli ajan fabrikası.** Omurga, derleme anında tanımlı 5-6 statik uzman ajan + araç kayıt defteriyle kurulur (Kademe 0). Dinamik ajan üretimi yasak değil, kademeli ve politika-kapılıdır (§8.5): önce şablondan parametreli örnekleme (Kademe 1), sonra Kadir onayıyla kayıt defterine kalıcı ajan ekleme (Kademe 2); onaysız çalışma-anı üretimi (Kademe 3) ölçülebilir bir olgunluk kapısına bağlıdır. Hangi kademede olursa olsun: üretilmiş ajan matriste misafir muamelesi görür, araçlarını yalnızca kayıt defterinden seçer, ajan üretemez, TTL'li ve bütçe tavanlıdır.
12. **Bulut-öncelikli yerleşim (Pi'nin omzunda asgari yük).** Bir iş fiziksel bir çıpa (SIM, konut IP'si, LAN, mikrofon) gerektirmiyorsa bulutta yapılır. Pi yalnızca adaptörlük yapar; beyne ait hiçbir işi ve durumu devralmaz — yerelde yalnızca fiziksel role bağlı veri yaşar (ör. tarayıcı profili, o da buluta yedeklenir). Yerleşim tereddüdünde varsayılan cevap buluttur.

---

## 3. Mimari Genel Bakış

```
                        ┌──────────────────────────────────────────┐
                        │              GOOGLE CLOUD                │
                        │                                          │
  Kadir'in cihazları    │  ┌────────────┐      ┌───────────────┐   │
  ┌──────────────┐      │  │  SES GEÇİDİ │◄────►│  BEYİN        │   │
  │ Android      │◄─ses─┼─►│ Gemini Live │      │ Cloud Run+ADK │   │
  │ uygulama     │      │  │ (Cloud Run) │      │ Orkestratör + │   │
  │ (ANA KANAL)  │      │  └─────▲──────┘      │ uzman ajanlar │   │
  ├──────────────┤      │        │             └──┬────┬───┬───┘   │
  │ Wear OS      │      │  GSM köprüsü (ev)       │    │   │       │
  └──────────────┘      │  üzerinden telefon   Politika Olay Hafıza│
                        │  sesi (connector)    katmanı katmanı     │
  Dış AI'lar            │  ┌──────────────┐      │  (Pub/Sub)      │
  (Claude, Gemini…)◄────┼─►│ MİSAFİR KAPISI│  Firestore + Vektör   │
                        │  │ MCP endpoint  │  + GCS (ses kayıtları)│
  Telegram / WhatsApp   │  │ (IAP/OAuth)   │                       │
  (Jarvis Kadir adına ◄─┼──┤ Araç katmanı  │                       │
   kullanır — araç)     │  └──────────────┘                        │
                        └───────────┬──────────────────────────────┘
                                    │ CONNECTOR (VPN/mesh yok):
                                    │ Pub/Sub pull (bulut→ev)
                                    │ HTTPS push  (ev→bulut)
                        ┌───────────▼──────────────────────────────┐
                        │              EV (açık port: 0)           │
                        │  Pi 5: HAOS + connector ajanı            │
                        │        + GSM köprüsü (telesekreter+OTP)  │
                        │        + browser worker + wake word      │
                        │  Masaüstü (CachyOS+RTX): WOL ile uyanan  │
                        │        kas gücü, Ollama/vLLM, ağır       │
                        │        tarayıcı işleri, son çare emülatör│
                        └──────────────────────────────────────────┘
```

**Ana akış:** Komut herhangi bir kanaldan beyne düşer → orkestratör görevi analiz eder, gerekirse uzman ajanlara böler → her eylem politika katmanından geçer → araçlar (tarayıcı, ev, telefon, dış AI) tetiklenir → tüm sonuçlar (hatalar dahil) modele gözlem olarak döner → süreç Firestore'a loglanır → iş bitince Cloud Run uykuya döner → öğrenilenler hafızaya işlenir.

---

## 4. Bileşenler

### 4.1 Beyin (Orkestratör)
- **Teknoloji:** Cloud Run + Google ADK, Gemini API (ağır analiz için Pro sınıfı, hızlı işler için Flash sınıfı model). **Model adlandırma kuralı:** tüm yapılandırmalarda `-latest` alias'ları kullanılır (`gemini-flash-latest`, `gemini-pro-latest`); sabit sürüm pinlenmez — pinli sürümler yeni API kullanıcılarına kapatılabiliyor (23 Tem 2026'da `gemini-2.5-flash` ile yaşandı).
- **Yapı:** Tek orkestratör + derleme anında tanımlı statik uzman ajanlar: Sekreter (takvim/mail/hatırlatma), Operatör (tarayıcı otomasyonu), Ev Sorumlusu (HA), Araştırmacı, Arşivci (hafıza/özetleme/öğrenme). Her ajanın araç listesi sabittir.
- **Kalıcılık:** State Firestore'da, dosyalar GCS API ile okunur/yazılır. Cloud Run'a disk mount (GCSFuse vb.) yapılmaz — cold start'ı şişirir, tutarlılık sorunu getirir.
- **API kotası gerçeği:** Google AI Pro (öğrenci) aboneliği **API kotası vermez**; API ayrı dünyadır. Ücretsiz API katmanıyla başlanır, yoğunluk artınca ücretli katmana (Tier 1) geçilir.

### 4.2 Ses Geçidi (Kalp)
- **Teknoloji:** Ayrı bir Cloud Run servisi; Gemini Live API (native ses — canlı oturumda ayrı STT/TTS zinciri gerekmez).
- **Girişler (adaptörler):** (a) Android uygulama mikrofonu (WebSocket/WebRTC), (b) Wear OS, (c) evdeki GSM köprüsü (ikinci hat — telesekreter ve dış arama sesi, connector üzerinden).
- **Görevler:** Oturum yönetimi, canlı transkript yayını (uygulamaya), araya girme/devralma köprüsü, kayıt tee'si (§6).

### 4.3 Politika Katmanı (Eylem Yetki Matrisi)
- Tüm uzman ajanların ve misafir AI'ların tüm eylemleri tek policy katmanından geçer; her karar Firestore audit log'una yazılır. Detay §9.

### 4.4 Olay Katmanı (Otonomi)
- **Teknoloji:** Pub/Sub + Cloud Scheduler.
- **Kaynaklar:** Gmail push, Calendar watch, Home Assistant olayları (Pi'deki connector'dan HTTPS push), zamanlanmış kontroller, sistem sağlık olayları.
- **Akış:** Olay düşer → Cloud Run uyanır → beyin "bu, kullanıcıya söylenmeye değer mi / bir eylem gerektirir mi?" diye değerlendirir → değerse uygun kanaldan konuşur veya (matris izin veriyorsa) eyleme geçer.
- Bu katman, sistemi "çok yetenekli bir kumanda" olmaktan çıkarıp "asistan" yapan katmandır.

### 4.5 Hafıza
- **Kademe 1 — Anlık:** Oturum bağlamı (context window).
- **Kademe 2 — Kısa vade:** Son 24 saat + oturum state'leri, uykuya geçmeden Firestore'a JSON olarak yazılır.
- **Kademe 3 — Uzun vade (RAG):** Geçmiş konuşmalar, Drive dosyaları, **arama kayıtları ve özetleri** (§6), **kullanıcı profili, ders defteri ve tarifler** (§8). Firestore vektör (KNN) araması — tamamen bulutta; Pi üzerinde vektör DB tutulmaz (İlke 12).
- **Bakım:** Arşivci ajan; aylık/3 aylık özetleme ile veri şişmesini önler.
- **Öz-farkındalık verisi:** `check_my_vitals` aracı — kota sayaçları, aylık API harcaması, servis sağlık durumu modele sorgulanabilir veri olarak açılır ("bütçenin %80'i gitti, ağır işleri Flash'a düşürüyorum" davranışının altyapısı).

### 4.6 Ev Düğümü (Pi 5)
- **Konum gerekçesi:** Pi'nin işleri tesadüfen evde değildir; hepsi bulutun barındıramayacağı fiziksel kaynaklara çıpalıdır — SIM (GSM köprüsü), konut IP'si (browser worker), LAN (HAOS/Zigbee), mikrofon (wake word). Bu rolleri buluta taşımak, çıkarılmış dış hizmetleri (SIP sağlayıcısı, konut proxy'si, bulut-ev API'leri) geri almak demektir. Pi bir sunucu değil, **fiziksel dünya adaptörüdür**.
- **Takvim:** Aşama 1-2 Pi'siz, %100 bulutta yaşar; Pi ancak Aşama 3 (telefon) ve 4 (tarayıcı) ile kritik yola girer.
- **Roller (asgari küme — İlke 12):** HAOS (akıllı ev — LAN çıpası), **connector ajanı** (Pub/Sub pull + HTTPS push — evin buluta tek bağlantısı, açık port yok), **GSM köprüsü** (ikinci hat SIM'i: telesekreter sesi + OTP/SMS — SIM çıpası; §6.1), **browser worker** (ince yürütücü: tarayıcıyı koşar, tüm karar ve akış yönetimi bulutta; ağır işler masaüstüne devredilir — konut IP'si çıpası; §10), wake word (openWakeWord/Porcupine — mikrofon çıpası). Bu listenin dışında Pi'ye görev verilmez; listeye giriş şartı fiziksel çıpadır.
- **Çevrimdışı mod:** İnternet yokken hedef yalnızca ev kontrolüdür; bunu HAOS'un yerleşik Assist pipeline'ı (yerel STT + intent + TTS/Piper) karşılar. Ayrı bir yerel LLM zinciri kritik yolda değildir (masaüstünde deney olarak serbest).
- **Not:** Pi'nin SPOF kapsamı bilinçli olarak büyümüştür: Pi düşerse ev fonksiyonları, telefon (telesekreter/OTP) ve tarayıcı otomasyonu devre dışı kalır; buluttaki beyin ve uygulama/ses kanalı yaşamaya devam eder. Kabul edilmiş risktir.
- 7/24 açık cihaz görevi Pi'dedir; bulutta ayrıca sürekli çalışan bir makine (VM/yönlendirici) tutulmaz.

### 4.7 Kas Gücü (Masaüstü)
- CachyOS + RTX 5070; WOL ile Pi üzerinden uyandırılır, iş bitince suspend. Ollama/vLLM ile ağır yerel modeller, ağır veri/kod işleme; Pi'ye ağır gelen tarayıcı işlerini gerektiğinde devralır (ikinci browser worker).
- **Ek görev:** Android emülatörü *gerekirse* burada çalışır (ev IP'si, güçlü donanım, yalnızca gerektiğinde açık). Bulutta sanal Android emülatörü çalıştırılmaz — maliyet + emülatör tespiti/fraud-ban riski.

### 4.8 İstemciler
- **Android uygulama (ana kanal):** Kadir'in Jarvis'le tek etkileşim yüzeyi. UX modeli **Gemini uygulaması benzeri tek sohbet akışıdır**: yazılı sohbet, sesli mod, geçmiş — hepsi tek zaman çizgisinde. Sesli oturumların ve aramaların transkriptleri sohbete kart olarak düşer ("Devral" butonu canlı arama kartının üstündedir); **onay merkezi** ayrı ekran değil, sohbete düşen etkileşimli onay kartlarıdır (kırmızı bölge onayları, sarı bölge bildirimleri, fabrika Kademe 2 önerileri — §8.5); haftalık retro raporu da sohbete düşen bir mesajdır. Ek görevler: ASSIST intent ile varsayılan asistan olmak (güç tuşu tetiklemesi), push bildirimleri, cihaz kimliği. Kapsam büyüse de içinde sıfır zeka (İlke 1): uygulama "Gemini gibi görünen, Jarvis'in API'sine bağlanan bir kabuk"tur.
- **Onay akışının kritikliği:** Kırmızı bölge onayları yalnızca uygulamadan aktığı için FCM push güvenilirliği kritik yoldadır; onaylar uygulama açılınca senkronize olan bir kuyrukta da bekler (push kaçarsa onay kaybolmaz), kritik onaylarda zaman aşımı = reddet.
- **Wear OS eşi:** Aynı borunun saat karşılığı (ses + bildirim).
- **Telegram / WhatsApp:** Kadir'le iletişim kanalı DEĞİLDİR; Jarvis'in Kadir adına kullandığı mesajlaşma araçlarıdır (§10). WhatsApp için ToS kuralı geçerli: **kişisel numara üzerinden gayri resmi kütüphane (whatsapp-web.js/Baileys) asla** (kalıcı ban riski); resmi Cloud API veya gözden çıkarılabilir ayrı numara.

### 4.9 Misafir Kapısı (Dış AI Birlikte Çalışabilirliği)
- **Jarvis MCP sunucusudur:** Cloud Run üzerinde public, kimlik doğrulamalı (IAP/OAuth + istemci başına scoped token) bir MCP endpoint'i. Claude'a "custom connector" olarak eklenir; Gemini ekosistemi de MCP konuşur. Dış AI'lar Jarvis'in *seçilmiş* araçlarını çağırabilir (hafızaya not, görev kuyruğu, ev durumu okuma…).
- **Jarvis MCP istemcisidir:** `consult_claude`, `consult_gemini` araçları — orkestratör gerektiğinde danışman beyinlere başvurur ("bu iş kod işi, Claude'a danışayım"), ikinci görüş alır.
- **Kural:** Misafir ajanlar yetki matrisinde yalnızca **yeşil + sarı** bölgeye erişir; kırmızıya asla. Her misafir isteği ayrı token, ayrı log.
- **A2A (Agent2Agent):** Ajan-seviyesi birlikte çalışma protokolü (ADK yerleşik destekler). MCP = araç seviyesi, A2A = ajan seviyesi (görev delegasyonu). Pragmatik sıra: önce MCP; A2A, mimaride yeri hazır bir sonraki adım.

---

## 5. Ağ ve Güvenlik

- **Outbound-only connector (VPN/mesh yok):** Türkiye'de sabit IP yokluğu + CGNAT, eve içeri doğru erişimi imkânsızlaştırır; çözüm, içeri doğru erişime hiç ihtiyaç duymamaktır. Pi'deki connector ajanı bulut→ev komutlarını **Pub/Sub aboneliğinden çeker**, ev→bulut olaylarını **HTTPS ile iter**; kimlik GCP servis hesabı/IAM ile doğrulanır (SIM anahtar dosyası yerine mümkünse Workload Identity Federation). Evde açık port sayısı sıfırdır. Önemli ayrım: IAM/rol atamaları *kimlik ve yetki* sağlar, ağ erişilebilirliğini çözmez — erişilebilirliği çekme (pull) deseni sağlar. Bu desen kurumsal dağıtımda da aynen çalışır: müşteri sahasına tek bir connector ajanı kurulur, firewall'da hiçbir şey açılmaz.
- **Public kapılar (kimlik doğrulamalı, hepsi bulutta):** (1) Misafir Kapısı — MCP endpoint (IAP/OAuth, scoped token), (2) Kanal webhook'ları — Telegram Bot API webhook'u, FCM push. Bunların dışında hiçbir servis internete açık değildir; evde hiçbir kapı yoktur.
- **Eylem güvenliği:** §9'daki matris + Firestore audit log (kim/ne/ne zaman/hangi gerekçeyle).
- **Sırlar:** API anahtarları ve token'lar Secret Manager'da; istemcilerde sır tutulmaz (İlke 1).
- **KVKK / kayıt hukuku:** Arama kaydı Türkiye'de hukuken hassastır (karşı taraf rızası). Tasarım kuralı: ses geçidinin açılış anonsu sabittir — Jarvis kendini asistan olarak tanıtır **ve** görüşmenin kayıt altında olduğunu söyler. (Not: hukuki danışmanlık değildir; canlıya almadan önce ayrıca değerlendirilecek.)

---

## 6. Telefon Mimarisi (Katil Özellik)

### 6.1 Numaralar (dış hizmet yok)
- **İlke:** SIP/santral sağlayıcısı (Verimor, Netgsm…) kullanılmaz. Telefon yeteneği tamamen mevcut hatlar + evdeki donanımla kurulur; aylık dış hizmet kirası sıfırdır.
- **Kadir'in ana GSM hattı:** Değişmez. Operatör koşullu yönlendirmesi (meşgulde / cevapsızda / ulaşılamadığında) → ikinci GSM hattına döner. Kadir açmazsa Jarvis açar — hat fiilen **akıllı bir telesekretere** dönüşür. (Teknik gerçek: telefonun kendisi Jarvis'e çağrı sesi veremez — Android üçüncü parti uygulamaya canlı çağrı sesi erişimi vermez; bu yüzden yönlendirme hedefi olarak evdeki ikinci hat şarttır.)
- **İkinci GSM hattı (boştaki SIM) — Jarvis'in hattı:** SIM evde, ses destekli donanımda yaşar (Quectel EC25 sınıfı GSM modülü veya GoIP):
  - **Telesekreter sesi:** Yönlendirilen aramayı evdeki modem açar; ses Pi → connector → buluttaki ses geçidine akar; konuşmayı Gemini Live yürütür.
  - **OTP/SMS köprüsü:** Gelen her SMS Jarvis'e akar. Tarayıcı otomasyonu SMS doğrulama ekranına düştüğünde Jarvis kodu kendisi okuyup girer → "farklı cihazdan giriş, SMS onayı" döngüsü kökten kapanır.
  - **Dış arama kimliği:** Jarvis dışarıyı bu hattan arar; taksi durağı/restoran gerçek bir cep numarası görür (0850'ye çıkmayan esnaf gerçeği burada avantaja döner).
- **Bilinçli sınırlar:** Aynı anda tek arama (tek GSM kanalı); ses dar bant (GSM 8 kHz); Pi/modem düşerse telefon yeteneği düşer (SPOF kapsamı, §4.6). Yönlendirilen aramanın eve giden bacağı Kadir'in tarifesinden dakika yer — tarife buna göre seçilir.

### 6.2 Gelen Arama Akışı (Screening + Devralma)
1. Arama Kadir'in GSM'ine gelir; açmazsa/reddederse yönlendirme evdeki ikinci hatta düşer; modem açar, ses connector üzerinden buluta köprülenir.
2. Ses geçidi oturum açar; Jarvis anons yapar: *"Merhaba, ben Kadir'in asistanı Jarvis; görüşme kayıt altındadır. Nasıl yardımcı olabilirim?"*
3. Gemini Live konuşmayı yürütür; canlı transkript eş zamanlı olarak uygulamaya akar.
4. Kadir isterse uygulamadaki **"Devral"** butonuna basar → aynı ses köprüsüne katılır → Jarvis susar, gerekirse arka planda not almaya devam eder.
5. Arama biter → kayıt/özet pipeline'ı çalışır (§6.3).

### 6.3 Kayıt ve Hatırlama Pipeline'ı
- Ses akışı geçitte tee'lenir → ham ses GCS'e yazılır.
- Transkript zaten Live oturumunun çıktısıdır → arama bitince Flash ile son-işlem: özet, arayan kimliği, konu, verilen sözler/takip işleri.
- Sonuç Firestore'a yazılır: `{arayan, numara, zaman, süre, transkript, özet, taahhütler, ses_dosyası_linki}` + vektör embedding.
- **Sorgu:** "Geçen salı Ali'yle ne görüşmüştük?" = sıradan bir RAG hafıza sorgusu (Kademe 3).
- **Sekreter refleksi:** Özette taahhüt varsa ("yarın dönüş yapılacak") Jarvis kendiliğinden hatırlatma kurar (olay katmanı + sarı bölge eylemi).
- **Cihaz bağımsızlığı:** Kayıtlar Kadir'in bulutundadır; telefon/marka değişimi hiçbir şeyi kaybettirmez. (Samsung Call Assist'in cihaza kilitli yaptığı işin, cihazdan bağımsız ve sorgulanabilir hali.)

### 6.4 Maliyet Notu
Telefon tarafında dış hizmet kirası yoktur. Giderler: ikinci SIM'in konuşma/SMS tarifesi + yönlendirme dakikaları (Kadir'in tarifesinden) + (tek seferlik) ses destekli GSM modülü/GoIP donanımı. Bilinçli ve kabul edilmiş maliyettir (İlke 9).

---

## 7. Otonomi ve Durum Farkındalığı

Hedef davranış: Jarvis yalnızca komut beklemez; dünyayı izler, yorumlar, gerektiğinde konuşur ve **kendi durumunun farkındadır** (kota doldu, servis düştü, bütçe bitti → anlar, yorumlar, bildirir, strateji değiştirir).

Mekanizmalar:
1. **Olay katmanı (§4.4):** Dış dünya değişimleri (mail, takvim, ev, kargo, fatura) beyni uyandırır.
2. **Hata = gözlem (İlke 4):** Tüm araç sonuçları sansürsüz modele gider. "Kota anlama modülü" yazılmaz; hatayı modelden saklamamak yeter.
3. **`check_my_vitals`:** Jarvis kendi kota sayaçlarını, harcamasını, servis sağlığını sorgulayabilir.
4. **Değerlendirme döngüsü:** Üst üste hatada ajan körlemesine retry yapmaz; durup "burada ne oluyor?" akıl yürütmesi çalışır → strateji değişir (model düşür, ertele, kullanıcıya danış).
5. **Fallback'ler seçimdir, hardcode değildir:** Alternatif yolları model *seçer*, politika katmanı *sınırlar*.

---

## 8. Sürekli Öğrenme ve Kişiselleşme

Jarvis'in zekası iki kaynaktan gelir: modelin kalitesi (Google'ın işi) ve biriktirdiği bağlam (bu projenin işi). Bu bölüm ikincisinin mimarisidir. Fine-tuning gerekmez; öğrenme tamamen hafıza ve bağlam yönetimiyle olur. Not: Bu desen, Kadir'in Antigravity IDE'de kurduğu ajanlarda hâlihazırda uygulanmakta ve çalışmaktadır; Jarvis'in uzman ajanları aynı kanıtlanmış kurguyla (talimat + ders defteri + geri bildirim döngüsü) inşa edilir.

### 8.1 Kullanıcı Modeli (Kadir'i tanıma)
- Firestore'da yapılandırılmış, sürekli güncellenen bir **profil**: tercihler (yemek, saatler, iletişim tonu), rutinler, önemli kişiler ve ilişkiler, "asla yapma" listesi.
- **Örtük öğrenme:** Arşivci ajan, her anlamlı etkileşimden sonra profili gözden geçirir ("Kadir salı akşamları aranmak istemiyor", "kahveyi hep X'ten söylüyor").
- **Açık öğrenme:** "Bunu bir daha yapma" / "bundan sonra şöyle yap" tarzı geri bildirimler anında kalıcı kurala dönüşür ve profile yazılır.
- Profil, her görevin başında ilgili kısımlarıyla modelin bağlamına eklenir — kişiselleşme buradan doğar.

### 8.2 Ders Defteri (hatalardan öğrenme)
- Başarısız veya kullanıcı tarafından düzeltilmiş her görevden yapılandırılmış bir **ders kaydı** çıkarılır: `{bağlam, ne denendi, ne yanlış gitti, doğrusu ne}`.
- Kayıtlar vektör indekslidir; benzer bir görev başlarken ilgili dersler RAG ile bağlama çağrılır → **aynı hata iki kez yapılmaz** (İlke 5).
- Örnekler: "Yemeksepeti'nde ödeme butonu değişti, yeni akış şu", "Bu saatte taksi durağı açmıyor, uygulamadan çağır", "Claude kotası genelde akşam doluyor, ağır danışmaları sabaha planla".

### 8.3 Tarifler (başarılardan öğrenme — prosedürel hafıza)
- Başarıyla tamamlanan karmaşık akışlar, yeniden oynatılabilir **tarif** olarak kaydedilir (ör. "favori sipariş akışının adımları").
- Tarif varsa ajan sıfırdan keşif yapmaz → hız, tutarlılık ve daha az token.
- Tarif bozulursa (site değişti, akış kırıldı) bu bir ders kaydına dönüşür ve tarif güncellenir — tarifler yaşayan belgelerdir.
- **Tarif → ajan terfisi:** Defalarca kanıtlanmış bir tarif, ajan fabrikasının hammaddesidir — Kademe 1'de şablon ajana parametre, Kademe 2'de kalıcı ajan önerisi olur (§8.5). Üretilen ajan boşluktan değil, gözlemlenmiş ve kanıtlanmış bir akıştan doğar.

### 8.4 Haftalık Retro (öz-değerlendirme)
- Cloud Scheduler ile haftalık bir iş: Arşivci, haftanın loglarını tarar; başarı/başarısızlık örüntülerini çıkarır, ders defterini ve profili günceller, gereksiz/eskimiş kayıtları özetleyip temizler.
- Çıktı olarak Kadir'e kısa bir rapor atar: *"Bu hafta şunları öğrendim, şu hataları bir daha yapmayacağım, şu tarifi güncelledim."*
- Bu döngü, "gittikçe akıllanma"nın görünür kanıtıdır ve sistemin sağlığını da izlettirir.

### 8.5 Kademeli Ajan Fabrikası
Dinamik ajan üretimi tek bir açma/kapama kararı değil, olgunlukla tırmanılan bir merdivendir (İlke 11). Politika katmanı, `check_my_vitals` ve dry-run modu fabrikanın ön koşullarıdır ve mimaride zaten vardır.

- **Kademe 0 — Statik çekirdek:** Derleme anında tanımlı 5-6 uzman ajan. Omurga budur; değişmez.
- **Kademe 1 — Kalıphane (şablondan türetme):** Derleme anında tanımlı ajan *şablonları*; orkestratör çalışma anında bunları parametreyle örnekler (araç alt kümesi + tarif + token tavanı + TTL). Şablon sayısı sabit olduğundan debug yüzeyi statik mimariye yakındır. Aşama 5'te devreye girer.
- **Kademe 2 — Onaylı fabrika (HITL):** Orkestratör yeni bir ajan tanımı önerir (talimat + araç listesi + bölge ataması); öneri uygulamadaki onay merkezine düşer; Kadir onaylarsa kayıt defterine kalıcı ajan olarak yazılır. Üretim çalışma anında değil, **onay anında** gerçekleşir — runtime hep statik ajanlarla döner, ama ajan kümesi evrimleşir.
- **Kademe 3 — Serbest fabrika:** Onaysız çalışma-anı üretimi. Kapısı ölçülebilirdir: Kademe 2, üst üste 8 haftalık retro'da fabrika kaynaklı sıfır kritik hata raporlamadan tartışmaya dahi açılmaz.

**Değişmezler (kademeden bağımsız fabrika anayasası):**
1. Üretilmiş ajan matriste **misafir muamelesi** görür: yeşil+sarı ile başlar, kırmızıya asla; bölge terfisi yalnızca Kadir onayıyla.
2. Araçlarını yalnızca **araç kayıt defterinden** seçer; yeni araç/kod üretemez.
3. **Ajan üretemez** (recursion yasağı).
4. **TTL'lidir:** iş bitince ölür; kalıcılaşmak Kademe 2 onayından geçmek demektir.
5. Token/maliyet tavanı zorunludur; audit log'da "kim üretti, hangi tariften, hangi talimatla" izi tutulur.
6. Haftalık retro (§8.4) üretilmiş ajan envanterini de tarar — ajan sürünmesine karşı temizlik.

---

## 9. Eylem Yetki Matrisi

Tek policy katmanı; tüm ajanlar ve tüm misafir AI'lar buradan geçer. Her karar loglanır.

| Bölge | Kural | Örnekler |
|---|---|---|
| 🟢 Yeşil | Sormadan yap | Bilgi okuma (takvim, mail, ev durumu), ışık/klima kontrolü, hatırlatma kurma, hafızaya not, transkript sorgusu |
| 🟡 Sarı | Yap + anında bildir | Asistan imzalı mesaj gönderme, düşük tutarlı *tekrar* siparişi, arama karşılama sırasında bilgi verme, takvime etkinlik ekleme |
| 🔴 Kırmızı | Onaysız asla | Eşik üstü para harcama, yeni/alışılmadık sipariş, dışarıyı arama başlatma (ilk aşamalarda), bir şey silme, ev kilidi/güvenlik, hesap ayarı değiştirme |
| 🚪 Misafir | Yeşil+Sarı'nın alt kümesi | Dış AI'lar (Claude, Gemini…) kırmızıya hiçbir koşulda erişemez; istemci başına scoped token |
| 🏭 Üretilmiş ajan | Misafirle aynı başlangıç | Fabrika çıktısı ajanlar (§8.5) yeşil+sarı ile başlar; bölge terfisi yalnızca Kadir onayıyla |

Eşikler (harcama limiti, "alışılmadık" tanımı) konfigürasyondur; güven arttıkça gevşetilebilir — kodda, sohbette değil. Öğrenme sistemi (§8) matrisin *içeriğini* önerebilir ("bu siparişi hep onaylıyorsun, sarıya alayım mı?") ama değişiklik her zaman Kadir'in onayıyla olur.

---

## 10. Entegrasyonlar (Sipariş, Taksi, Web)

- **Yöntem:** Hedef servislerin halka açık API'si yok (Yemeksepeti/Getir/BiTaksi) → tarayıcı otomasyonu. Yürütme evde: Pi'deki (ağır işlerde masaüstündeki) **browser worker** Playwright/Browser-Use'u yerelde koşar; beyin görevleri connector üzerinden kuyruklar ve adım kararlarını bulutta verir (İlke 1 korunur: evdeki şey zeka değil, araç yürütücüsüdür).
- **Anti-bot önlemleri (browser worker'ın doğal sonuçları):**
  - Trafik zaten gerçek konut IP'sinden ve gerçek donanımdan çıkar — exit node/proxy hilesine gerek kalmaz.
  - **Kalıcı tarayıcı profili** Pi'de yerelde yaşar (GCS'e yedeklenir — Pi kaybı profili kaybettirmez) → "farklı cihazdan giriş" tetiklenmez.
  - **OTP köprüsü** (§6.1) → SMS doğrulaması gelirse Jarvis kendisi çözer.
- **Emülatör politikası:** Bulutta sanal Android yok. Uygulama-zorunlu istisnalar için masaüstünde, WOL ile uyanan emülatör — son çare.
- **Kapsam stratejisi (İlke 10):** Önce "favoriyi tekrarla", "eve taksi çağır" gibi dar-sağlam akışlar kusursuzlaşır ve tarifleşir (§8.3); genel serbest sipariş sonra.
- **Mesajlaşma araçları (Jarvis, Kadir adına):** Telegram birincil mesajlaşma aracıdır — Jarvis, Kadir'in sohbetlerini okur ve asistan imzasıyla yanıtlar (sarı bölge; İlke 8 kimlik kuralı geçerli). Mekanizma açık karardır (§15): resmi **Telegram Business bot bağlantısı** (Premium gerektirir, ToS-temiz, yetenekleri sınırlı — esasen gelen sohbetlere yanıt) vs **MTProto kullanıcı oturumu** (tam yetenek, ToS-gri, hesap riski). WhatsApp için §4.8'deki ToS kuralları geçerli.
- **Diğer MCP araçları:** Google Workspace MCP, Maps MCP, GitHub MCP, MCP Toolbox for Databases vb. — hepsi araç kayıt defterinde, politika katmanına tabi.

---

## 11. Geliştirme Ortamı ve Süreç

- **IDE:** Antigravity (Kadir'in mevcut, ajanlarla çalışan kurulu ortamı). Deploy: `gcloud auth login` + `gcloud run deploy` — standart terminal akışı.
- **Mevcut birikimin taşınması:** Antigravity'de kurulmuş ve kendini kanıtlamış ajan kurguları (talimat yapısı, ders/geri bildirim döngüsü), Jarvis uzman ajanlarının şablonudur — sıfırdan tasarım değil, çalışan desenin buluta taşınması.
- **Bilgi bankası:** Bu belge NotebookLM'de projenin tek kaynak belgesi olarak tutulur; büyük kararlar belgeye işlenir, kod repo'sunda da `docs/` altında bir kopyası yaşar.
- **Test yaklaşımı:** Her uzman ajan için senaryo testleri; politika katmanının "kuru çalıştırma" (dry-run) modu — eylemleri yapmadan ne yapacağını loglar; tarayıcı akışları için kayıtlı-oturum testleri.

---

## 12. Katmanlar (uygulama sırası değil, bağımlılık haritası)

Bu tablo bir takvim veya görev planı değildir (belgenin statüsü gereği — bkz. başlık). Yalnızca hangi yeteneğin hangi temelin üstüne oturduğunu ve her katmanın tek başına değer ürettiğini sabitler; ayrıntılı uygulama planı ayrıca yapılır ve bu haritadan sapmadığı sürece serbesttir.

| Aşama | İçerik | "Bitti" ölçütü |
|---|---|---|
| **1 — Omurga** | Cloud Run + ADK beyni, asgari sohbet istemcisi (Web/PWA — nihai uygulamanın ilk kabuğu, §15), 3 kademeli hafıza + kullanıcı profili + ders defteri iskeleti, politika katmanı, Firestore loglama | Kendi uygulamamdan yazışıyorum, beni hatırlıyor ve tanımaya başlıyor, her eylem loglanıyor |
| **2 — Ses** | Ses geçidi + Gemini Live, Android uygulama olgunlaşır (ASSIST intent + canlı ekran + onay merkezi), Wear OS asgari | Güç tuşuna basıp konuşuyorum, saatten komut veriyorum, onaylar uygulamadan akıyor |
| **3 — Telefon** | GSM köprüsü pilotu (ikinci SIM + ses destekli modem/GoIP), koşullu yönlendirme, telesekreter screening + anons, canlı transkript + Devral, kayıt/RAG pipeline'ı, OTP köprüsü | Açmadığım arama Jarvis'e düşüyor, devralabiliyorum, "X'le ne konuşmuştuk" cevaplanıyor |
| **4 — Eller ve Refleksler** | Tarayıcı operatörü (evde browser worker + kalıcı profil), dar akışlar: favori sipariş, taksi; olay katmanı + proaktif bildirimler; haftalık retro devrede | Sesli komutla sipariş geliyor; Jarvis kendiliğinden anlamlı şeyler söylüyor ve haftalık öğrenme raporu atıyor |
| **5 — Ekosistem** | Home Assistant tam entegrasyon, Misafir Kapısı (MCP sunucu), consult_claude/gemini, Telegram'ı Kadir adına kullanma, fabrika Kademe 1 (kalıphane), A2A hazırlığı, WhatsApp kararı | Evi yönetiyor; mesajlarımı asistan imzasıyla yanıtlıyor; Claude↔Jarvis konuşuyor; sistem "tam Jarvis" |

---

## 13. Maliyet Modeli

| Kalem | Tür | Not |
|---|---|---|
| Cloud Run, Firestore, Pub/Sub, Scheduler, GCS | Değişken, ~0 başlangıç | Ücretsiz katman içinde başlar; scale-to-zero |
| Gemini API | Değişken | Ücretsiz katman → yoğunlukta ücretli Tier; Live API kullanımı ayrıca izlenir. AI Pro aboneliği API'yi **kapsamaz** |
| İkinci SIM tarifesi | Sabit (küçük, aylık) | Jarvis'in hattı — konuşma/SMS paketi |
| Yönlendirme + arama dakikaları | Değişken | Yönlendirme bacağı Kadir'in tarifesinden; kullanım kadar |
| Ses destekli GSM modülü / GoIP | Tek seferlik donanım | Telesekreter + OTP köprüsü + dış arama kimliği |
| Telegram Premium | Opsiyonel (aylık) | Business bot yolu seçilirse gerekli (§15) |
| WhatsApp resmi API | Opsiyonel | Karar Aşama 5'te |
| Pi 5 + masaüstü | Mevcut | Elektrik ihmal |

Prensip: boşta ~0; sabit giderler yalnızca telefon tarafında ve bilinçli. Fiyatlar kurulum öncesi güncel tarifelerden doğrulanacak.

---

## 14. Risk Kaydı

| Risk | Önlem / Durum |
|---|---|
| Anti-bot sistemleri (Cloudflare, reCAPTCHA) datacenter IP'yi engeller | Browser worker evde çalışır: gerçek konut IP'si + gerçek donanım + kalıcı yerel profil |
| "Farklı cihazdan giriş" SMS doğrulama döngüsü | Sabit çıkış IP'si + kalıcı profil + OTP köprüsü |
| Emülatör tespiti / hesapların fraud gerekçesiyle banlanması | Bulutta emülatör yok; son çare masaüstünde, gerçek ağ ve donanım üzerinde |
| Gemini API RPM/kota kilidi | Statik ajan mimarisi (ajanlar arası gereksiz trafik yok) + gerekirse ücretli katman + `check_my_vitals` ile izleme |
| WhatsApp hesabının banlanması | Kişisel numarayla gayri resmi kütüphane yasak; resmi API veya ayrı numara |
| Pi tek nokta hatası (SPOF) — kapsamı büyüdü | Kabul edildi: bulut beyni ve uygulama kanalı bağımsız yaşar; Pi düşerse ev, telefon (telesekreter/OTP) ve tarayıcı otomasyonu durur |
| GSM modem ses entegrasyonunun DIY kırılganlığı (eko, senkron, sürücü) | Aşama 3'te pilot; kalite yetmezse donanım DSP'li GoIP'e geçiş |
| Onay akışı push'a bağımlı — kırmızı bölge kilitlenebilir | FCM + uygulamada senkronize onay kuyruğu; kritik onaylarda zaman aşımı = reddet |
| Telegram hesabının riske girmesi (MTProto yolu seçilirse) | Öncelik resmi Business bot bağlantısında; MTProto ancak bilinçli kararla |
| Üretilmiş ajan sürünmesi (kontrolsüz çoğalma) | TTL + Kademe 2 zorunlu onay + haftalık retro envanter temizliği (§8.5) |
| Arama kaydının hukuki durumu (KVKK, rıza) | Sabit kayıt anonsu + canlıya almadan önce hukuki değerlendirme |
| Gemini Live API oturum/kota sınırları | Oturum yönetimi + değerlendirme döngüsü + kullanım izleme |
| Ders defteri/profilin şişmesi ve bağlamı kirletmesi | Haftalık retro'da özetleme ve temizlik (§8.4); RAG ile yalnızca ilgili kayıtlar çağrılır |

---

## 15. Açık Kararlar (bilinçli ertelenenler)

1. **Ses destekli GSM modülü mü (EC25 sınıfı), GoIP mi?** İkisi de telesekreter sesini taşıyabilir; modül ucuz ve tek parça, GoIP ses işini donanım DSP'siyle denenmiş biçimde çözer. Aşama 3 pilotunda netleşir.
2. **WhatsApp stratejisi:** Resmi Cloud API (ücretli) vs ayrı numarayla gayri resmi (riskli) vs hiç. Aşama 5 kararı.
3. **A2A zamanlaması:** MCP yeterli olduğu sürece bekler.
4. **AI Studio API vs Vertex AI — KARAR VERİLDİ (23 Tem 2026):** AI Studio ile başlanır (ücretsiz katman; belgedeki kademeli maliyet stratejisine uygun). Vertex'e geçiş ADK'da ortam değişkeniyle mümkün; yoğunluk artınca yeniden değerlendirilir.
5. **Yerel LLM deneyleri:** Masaüstünde hobi/deney olarak serbest; kritik yola girmez.
6. **Fabrika Kademe 3 kapısı:** Kademe 2'nin kaç haftalık temiz retro'su yeterli sayılır (mevcut öneri: 8)? Kademe 2 canlıya çıkınca netleşir (§8.5).
7. **Telegram mekanizması:** Resmi Business bot bağlantısı (Premium, ToS-temiz, sınırlı yetenek) vs MTProto kullanıcı oturumu (tam yetenek, hesap riski). Aşama 5 kararı; yetenek sınırları kurulum öncesi güncel dokümandan doğrulanacak.
8. **Aşama 1 istemcisi — KARAR VERİLDİ (23 Tem 2026):** Web/PWA. Nihai uygulamanın API sözleşmesini şimdiden kullanır; Android kabuğu Katman 2'de gelir.

---

## 16. Sözlük

- **Ses Geçidi:** Tüm canlı ses trafiğinin girdiği tek Cloud Run servisi (Gemini Live oturum yöneticisi).
- **Misafir Kapısı:** Dış AI'ların Jarvis'e eriştiği kimlik doğrulamalı MCP endpoint'i.
- **OTP Köprüsü:** Evdeki ikinci SIM'in SMS'lerini Jarvis'e akıtan donanım+yazılım köprüsü.
- **Politika Katmanı / Yetki Matrisi:** Yeşil-sarı-kırmızı eylem izin sistemi (§9).
- **Olay Katmanı:** Jarvis'i komutsuz uyandıran tetikleyici altyapısı (Pub/Sub).
- **Connector:** Pi'nin buluta kurduğu dışa-doğru tek bağlantı deseni (Pub/Sub pull + HTTPS push); evde açık port bırakmayan CGNAT çözümü.
- **Browser Worker:** Tarayıcı otomasyonunu evde (Pi/masaüstü) yürüten araç sunucusu; beyin karar verir, worker uygular.
- **GSM Köprüsü:** İkinci SIM'i taşıyan ses destekli modem/GoIP; telesekreter sesi + OTP/SMS + dış arama kimliği.
- **Telesekreter Modeli:** Kadir'in ana hattının koşullu yönlendirmeyle ikinci hatta, oradan Jarvis'e düşmesi (§6).
- **Kalıphane / Fabrika Kademeleri:** §8.5'teki kademeli dinamik ajan üretim modeli (Kademe 0-3).
- **Devral:** Uygulamada, süren bir aramayı Jarvis'ten canlı olarak alma eylemi.
- **Hata=Gözlem:** Araç hatalarının yutulmayıp modele gösterilmesi ilkesi (İlke 4).
- **Kullanıcı Modeli / Profil:** Kadir'in tercihlerini, rutinlerini ve kurallarını tutan yapılandırılmış hafıza (§8.1).
- **Ders Defteri:** Hatalardan çıkarılan, görev öncesi bağlama çağrılan yapılandırılmış dersler (§8.2).
- **Tarif:** Başarılı akışların yeniden oynatılabilir prosedürel kaydı (§8.3).
- **Haftalık Retro:** Sistemin kendi haftasını değerlendirip öğrendiklerini raporladığı zamanlanmış iş (§8.4).
