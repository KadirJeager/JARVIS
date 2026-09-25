# JARVIS

**Güncel yön — [harness yönetimi planı](docs/2026-09-24-harness-yonetim-plani.md):** JARVIS'in ölçeği sıfıra inen bağımsız çekirdeği, çalışan harness'leri yöneterek Kadir'i işlev olarak ikame etme hedefine ilerler. Mesaj gelince PC gerekliyse Google Home'a bağlı Tuya kartıyla açılır; otomatik oturumdan sonra Hermes terminali, gerekli ajan oturumlarını ve gerektiğinde bilgisayar arayüzünü kullanır. Terminal önceliklidir. Kullanıcının sürekli aboneliği Google'dır; Codex aboneliği bir bağımlılık olamaz. AI Studio API anahtarı veya ayrı ücretli model API kullanılmaz. Mevcut `/api/chat` Hermes model protokolü değildir; yeni `/v1/chat/completions` sınırı yerelde eklendi. Canlı CLIProxyAPI'nin mevcut Gemini yolu çalışıyor, fakat Hermes'in OpenAI araç turu ve yeni ince servisin dağıtımı henüz doğrulanmadı. Önceki [JARVIS sadeleşiyor](docs/2026-09-23-jarvis-sadelesiyor.md) değerlendirmesi bu planın girdisidir.

Kişisel otonom asistan. [Eski North Star belgesi](docs/JARVIS_Proje_Belgesi.md) ilk niyetin tarihsel kaydıdır; güncel uygulama yönü üstteki plandadır.

- `brain/` — Cloud Run servisi: ADK orkestratör + politika katmanı + hafıza + Web/PWA istemci
- Uygulama planları: `docs/superpowers/plans/`
- **Ekosistem analizi:** `docs/arastirma/2026-08-05-ekosistem-analizi.md` — benzer projeler (OpenClaw, Hermes, QwenPaw…) ve katman katman hazır çözüm manzarası. **Yeni bir mekanizma yazmadan önce oku** (onay kuyruğu, hafıza konsolidasyonu, anti-spoofing, ses geçidi…). Öncelik sırası ve karar kaydı §7'de.
- Kardeş proje: **YourDialer** (`~/Projeler/Android Projeleri/YourDialer`) — §6 Telefon Mimarisi ile kesişir, JARVIS'e istemci olarak bağlanacak. Bkz. `docs/2026-08-03-yourdialer-baglantisi.md`
