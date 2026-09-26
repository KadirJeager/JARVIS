# JARVIS çalışma bağlamı

Önemli veya devam eden işlerde önce şunları oku:

- `/mnt/Ortak/Hafıza/Memory/HAFIZA_PROTOKOLU.md`
- `/mnt/Ortak/Hafıza/Memory/ORTAK_HAFIZA.md`
- `/mnt/Ortak/Hafıza/Memory/Projeler/` altındaki JARVIS notu
- `docs/2026-09-25-moduler-kisisel-asistan-mimarisi.md` (güncel ürün/mimari, kod denetimi ve gerçek kabul kapıları)

Güncel kullanıcı kararı (2026-09-25): JARVIS, bulutta vault/hafızası bulunan, olayla uyanan, kullanıcıyı tanıyan modüler kişisel asistandır. Model sağlayıcısı ve erişim yolu değiştirilebilir; mevcut Google aboneliği Kadir'in kurulum tercihidir. Başka kullanıcılar desteklenen ChatGPT/Claude/diğer sağlayıcı veya API yolunu seçebilir. Abonelikli uygulama oturumu genel model API hakkı varsayılmaz. PWA kontrol paneli gereklidir; Google Chat, Telegram, WhatsApp ve diğer istemciler seçeneklerdir. Temel araçlar sınırlı tutulur, diğer araçlar select-tool mekanizmasıyla gerektiğinde yüklenir.

Yerel yönetici değiştirilebilir (Hermes/OpenClaw vb.); PC/Pi ve desteklenen diğer cihazlarda dışarı yönlü bağlantıyla görev alır. Ortamı keşfeder, uygulama/uzman ajanları açar, asıl işi onlara devreder ve sonucu denetler. Terminal önceliklidir; gerektiğinde GUI kullanılır. Güç/akıllı ev opsiyonel modüldür; Google Home önceliği gerçek hesap/cihaz desteğiyle doğrulanır. Yerel ajanların kendi hafızaları korunur; ilgili bağlam ve raporlar bulut asistanıyla paylaşılır. Sesli mesaj, belge ve gerçek sesli/görüntülü arama ayrı kabul kapılarıdır.

Mevcut kod yeni tasarımın kanıtı değildir. `docs/2026-09-24-harness-yonetim-plani.md` ve eski envanter yalnız önceki kurulum/deney bağlamı gerektiğinde okunur; zorunlu Hermes, tek Chat, genel API yasağı ve PWA/ses yeteneklerini kaldırma yönü yeni kararla geçersizdir. CLIProxyAPI geçmişte doğrulanmış kişisel bağlantı adayıdır; bu oturumun yeni asistan/gerçek araç turu çalışıyor sayılmaz.

Yeni araştırma veya alt görev tanımında güncel kararları açıkça aktar. Mevcut yapıya bağlılık nedeniyle gereksinimi daraltma. Geçici patch, mock/sabit başarılı sonuç ve kullanıcı/cihaz/model seçimini kaynak koduna gömme yok. Protokol sabitleriyle açık kullanıcı yapılandırmasını bu yasakla karıştırma. Canlıda doğrulanmamış bağlantıyı çalışıyor yazma; gerçek kabul deneyleri olmadan hazır ilan etme. 2026-09-26 kullanıcı kararı: yeni çekirdek eski projenin yerine dağıtılır; eski bulut servisleri ve eski uygulamalar silinebilir, yeni servisin kullandığı sırlar korunur. Native mobil/masaüstü uygulama geliştirilmez; istemci PWA ve mesajlaşma kanallarıdır. Parola, token veya OAuth dosyası içeriğini notlara ya da çıktılara koyma. Güncel kullanıcı isteği bu notlardan üstündür.
