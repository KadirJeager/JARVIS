# JARVIS

Kişisel otonom asistan. Nihai hedef mimari: `docs/JARVIS_Proje_Belgesi.md` (North Star).
Bu repo Katman 1'den (Omurga) itibaren o hedefe doğru büyür.

- `brain/` — Cloud Run servisi: ADK orkestratör + politika katmanı + hafıza + Web/PWA istemci
- Uygulama planları: `docs/superpowers/plans/`
- **Ekosistem analizi:** `docs/arastirma/2026-08-05-ekosistem-analizi.md` — benzer projeler (OpenClaw, Hermes, QwenPaw…) ve katman katman hazır çözüm manzarası. **Yeni bir mekanizma yazmadan önce oku** (onay kuyruğu, hafıza konsolidasyonu, anti-spoofing, ses geçidi…). Öncelik sırası ve karar kaydı §7'de.
- Kardeş proje: **YourDialer** (`~/Projeler/Android Projeleri/YourDialer`) — §6 Telefon Mimarisi ile kesişir, JARVIS'e istemci olarak bağlanacak. Bkz. `docs/2026-08-03-yourdialer-baglantisi.md`
