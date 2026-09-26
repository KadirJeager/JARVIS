# JARVIS

**Güncel temel — [modüler kişisel asistan mimarisi](docs/2026-09-25-moduler-kisisel-asistan-mimarisi.md) (25 Eylül 2026).** JARVIS; bulutta kalıcı hafızası olan, ihtiyaç geldiğinde çalışan, model sağlayıcısı ve iletişim kanalları değiştirilebilen kişisel asistandır. PWA kontrol panelinden bağlantılar, modeller, modüller, cihazlar ve görevler yönetilir. Araçlar ihtiyaca göre seçilir. Cihaz gerektiren işlerde dışarıya bağlanan yerel yönetici uygun uzman ajanları çalıştırır, takip eder ve kanıtlı sonucu JARVIS'e iletir.

Gemini/Google aboneliği ve CLIProxyAPI mevcut kişisel kurulum yoludur; ürünün zorunlu modeli veya erişim biçimi değildir. Hermes/OpenClaw benzeri harness'ler ve Google Chat/Telegram/WhatsApp gibi kanallar modül seçenekleridir. Sesli mesaj, belge ve gerçek sesli/görüntülü arama ayrı yetenekler olarak kapsamda kalır. Özel alan adı veya tek işletim sistemi zorunluluğu yoktur.

**Uygulama durumu:** Yeni çekirdek [`core/`](core/README.md) altındadır (Pydantic AI + Pydantic AI Harness, Firestore, Cloud Tasks, PWA) ve Google Cloud'a dağıtılmıştır; K1 kabulü sürmektedir, durum [K1 planında](docs/2026-09-26-k1-uygulama-plani.md). Eski `brain/` (ADK) ve `android/` kodu 2026-09-26'da kullanıcı kararıyla kaldırıldı; git geçmişinde `d36bd4d` commit'inde durur. Native uygulama geliştirilmez; istemci PWA ve mesajlaşma kanallarıdır. Yerel/sahte servis testleri canlı entegrasyon kanıtı değildir.

- [Güncel mimari, kod denetimi ve kabul kapıları](docs/2026-09-25-moduler-kisisel-asistan-mimarisi.md)
- [Yeni çekirdek: kod, test ve kurulum](core/README.md)
- [K0 gerçek model ve araç deneyi](docs/2026-09-26-k0-gercek-model-arac-deneyi.md)
- [Önceki harness planı ve tarihli deneyler](docs/2026-09-24-harness-yonetim-plani.md), [önceki canlı envanter](docs/2026-09-24-canli-sadelestirme-envanteri.md)
- [Eski North Star](docs/JARVIS_Proje_Belgesi.md), `docs/superpowers/` ve eski ekosistem araştırmaları tarihsel girdilerdir; güncel ürün kararlarının yerine geçmez.

Geçici yama, kullanıcıya özel hardcoded uygulama dalları ve mock sonuçlarla tamamlanmış gösterilen özellikler kabul edilmez. Hazır çözümler gerçek model/araç deneyleriyle değerlendirilir; eski parçalar işlev ve bakım değerine göre tutulur veya değiştirilir.
