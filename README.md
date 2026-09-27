# JARVIS

Kişisel otonom asistan projesi: bulutta çalışan bir "beyin", Android telefon
uygulaması, Wear OS saat uygulaması ve web istemcisinden oluşur. Temmuz–Eylül
2026 arasında geliştirildi.

> **Durum: arşiv.** Proje Eylül 2026'da kapatıldı ve artık geliştirilmiyor.
> Bağlı olduğu Google Cloud projesi silindi; kod kendi projenizle çalıştırılmak
> üzere olduğu gibi bırakıldı.

## Neler var

**Bulut beyni (`brain/`, Python):** Cloud Run üzerinde FastAPI servisi ve
Google ADK ile kurulmuş orkestratör ajan.

- **Sesli konuşma:** WebSocket ses geçidi, Vertex Gemini ile konuşma tanıma.
- **Konuşmacı doğrulama:** SpeechBrain ECAPA ses izi ve uyarlanan ses izi galerisi.
- **Sahte ses tespiti:** Kaydedilmiş veya yapay zekâyla üretilmiş sesi ayırt eden karşı önlem modülü.
- **Risk tabanlı güven:** Kimlik sinyallerini birleştirip her istek için güven düzeyi çıkarır.
- **Eylem yetki matrisi ve onay merkezi:** Her araç çağrısı politika katmanından geçer. Riskli eylemler sahibinin onayını bekleyen bir kuyruğa düşer.
- **Katmanlı hafıza:** Profil, olgular, dersler ve oturum özetleri Firestore'da tutulur.
- **Misafir kapısı:** Seçilmiş araçları kimlik doğrulamalı bir MCP ucuyla dış yapay zekâlara açar.
- **İkinci görüş:** Önemli kararlarda farklı bir model ailesine danışır.
- **Haftalık retrospektif ve ajan fabrikası:** Kalıptan yeni görev ajanları üretir.
- **Görev devri:** Kapalı bilgisayarı akıllı güç kartıyla açar, işi bilgisayardaki yönetici ajana iletir, sonucu doğrulayıp kanala geri gönderir.

**Android (`android/app`, Kotlin):** Jetpack Compose sohbet ve sesli arama
ekranları, ses kimliği kaydı, onay kartları, Firebase Cloud Messaging bildirimleri.

**Wear OS (`android/wear`, Kotlin):** Telefondan tek seferlik eşleştirme,
Android Keystore (AES/GCM) ile şifreli token saklama, sohbet, hızlı komutlar ve
sesli komut.

## Dallar

| Dal | İçerik |
|---|---|
| `feat/antispoof-cm` (varsayılan) | En kapsamlı sürüm: beyin, Android ve Wear kodu, görev devri altyapısı |
| `feat/jarvis-core-k1` | Yeni bulut çekirdeği: Pydantic AI ve Pydantic AI Harness, Firestore, Cloud Tasks, PWA kontrol paneli |
| `main` | Ağustos 2026 başındaki ana dal: beyin, Android ve Wear planları |

Diğer dallar tek tek özelliklerin geliştirme dallarıdır.

## Teknolojiler

- **Beyin:** Python 3.12, FastAPI, Google ADK, Firestore, Cloud Run, Cloud Tasks, SpeechBrain / PyTorch
- **Mobil:** Kotlin 2.4, Jetpack Compose, Android Gradle Plugin 9.3, Retrofit, DataStore, Wear OS

Varsayılan dalda yaklaşık 31 bin satır Python ve 19 bin satır Kotlin, 991
Python ve 404 Android testi bulunur.

## Çalıştırma

Depoda sır, `google-services.json` veya dağıtım kimliği yoktur. Proje,
hesap ve cihaz kimlikleri yer tutucularla değiştirilmiştir (`your-gcp-project`,
`owner@example.com` gibi). Çalıştırmak için kendi Google Cloud ve Firebase
projenizi kurup bu değerleri doldurmanız gerekir. Beynin kurulumu ve ortam
değişkenleri [`brain/README.md`](brain/README.md) içinde anlatılıyor.

## Belgeler

- [`docs/JARVIS_Proje_Belgesi.md`](docs/JARVIS_Proje_Belgesi.md): vizyon, mimari, güvenlik ve yetki matrisi
- [`docs/superpowers/plans/`](docs/superpowers/plans/): katman katman uygulama planları
- [`docs/arastirma/`](docs/arastirma/): benzer projeler ve hazır çözümler üzerine araştırmalar

## Not

Depo herkese açılmadan önce geçmişi yeniden yazıldı. Kişisel belgeler,
yerel araç veritabanları ve Firebase yapılandırması bütün commit'lerden
çıkarıldı; kişisel tanımlayıcılar yer tutucularla değiştirildi. Bu yüzden
commit özetleri özgün depodakilerden farklıdır.

Lisans belirtilmemiştir; bütün hakları saklıdır.
