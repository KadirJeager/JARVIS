# JARVIS Web/PWA — Durum Notu (30 Temmuz 2026)

**Bu PWA artık desteklenmiyor.** Aşama 1'deki geçici kabuk rolünü tamamladı;
ana kanal Android uygulamasıdır.

- **Ses yolu v1 protokolde kaldı.** Ses geçidi 30 Temmuz 2026'da protokol
  v2'ye geçti (cihaz-üstü STT/TTS + metin turu; Gemini Live kaldırıldı).
  Sunucu, WS hello'sunda `client_caps` gönderemeyen istemcilere
  `evt_error("Uygulamayı güncelle")` + close code **4409** döner. Bu PWA v1
  konuştuğu için sesli oturum açamaz — kod değiştirilmemiştir, bilinçli
  olarak böyle bırakılmıştır.
- **Metin chat** (`/api/chat`) teknik olarak hâlâ yanıt verir, ancak PWA bir
  bütün olarak bakım kapsamı dışındadır; yeni geliştirme Android istemcisinde
  yapılır.

Kod silinmedi/değiştirilmedi — bu dosya yalnızca durum notudur. Güncel
mimari için: `../README.md` → "LLM proxy + yerel embedding + ses protokolü
v2".
