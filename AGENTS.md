# JARVIS çalışma bağlamı

Önemli veya devam eden işlerde önce şunları oku:

- `/mnt/Ortak/Hafıza/Memory/HAFIZA_PROTOKOLU.md`
- `/mnt/Ortak/Hafıza/Memory/ORTAK_HAFIZA.md`
- `/mnt/Ortak/Hafıza/Memory/Projeler/` altındaki JARVIS notu
- `docs/2026-09-24-harness-yonetim-plani.md` (güncel teknik karar ve açık kabul kapıları)

Güncel kullanıcı kararı: Hermes, JARVIS'in PC'de çalışan ajanıdır. İnce bulut servisi görevleri kalıcı olarak taşır ve PC kapalıyken de çalışabilir; Google Chat hedef istemcidir. Mevcut CLIProxyAPI, canlı `jarvis-brain` servisinin yanında `localhost:8317` üzerinde çalışır ve Google aboneliği OAuth yolunu kullanır. Hermes model çağrıları JARVIS'in `/v1/chat/completions` sınırından bu mevcut proxy'ye gider. AI Studio API anahtarı ve Codex aboneliği bu mimarinin şartı değildir. Eski ses/TTS/ADK/MCP/Android/PWA parçaları ancak yeni yol doğrulandıktan sonra kaldırılır.

Yeni araştırma veya alt görev başlatırken bu kararları görev tanımına açıkça geçir. Canlıda doğrulanmamış bir bağlantıyı çalışıyor diye yazma. Parola, token veya OAuth dosyası içeriğini notlara ya da çıktılara koyma. Güncel kullanıcı isteği bu notlardan üstündür.
