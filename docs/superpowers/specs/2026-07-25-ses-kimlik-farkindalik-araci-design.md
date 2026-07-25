# Ses Kimliği Farkındalık Aracı (`get_speaker_status`) — Mini Spec

**Tarih:** 25 Temmuz 2026
**Durum:** onaylandı (Kadir: "Jarvis'i kısıtlamak istemiyorum"), tek görevlik dilim
**Bağlam:** Canlı HITL'de Jarvis "ses tanıma diye bir şey yok" dedi — kimlik sinyali
bilinçli olarak yalnız politika katmanına akıyordu (3a spec §7), modelin bağlamında
yoktu. Bu dilim modele **salt-okunur** bir pencere açar; yönetim yetkisi AÇMAZ.

## 1. Araç

`tools.get_speaker_status(tool_context) -> dict` — GREEN bölge, `ALL_TOOLS`'a eklenir
(metin + ses, iki runner da alır). Docstring Türkçe (LLM'in araç açıklaması):
"Kadir'in ses kimliği durumunu getirir: bu oturumda ses eşleşmesi var mı, son
skorlar, ses profili özeti. 'Beni tanıyor musun / ses tanıma var mı' tarzı sorularda çağır."

Dönen alanlar (vektör ASLA yok; örnek id'leri de gereksiz — sayı ve skor yeter):

```python
{
  "canli_ses_kanali": bool,          # voice_trust.lookup(tool_context) != None
  "guven_seviyesi": str | None,      # signals.trust_level
  "son_eslesme_skoru": float | None, # signals.voice_score
  "cihaz": str | None, "presence": str | None,
  "profil": {"capa": int, "otomatik": int, "elle": int},
  "son_dogrulamalar": [ {"ts": str, "skor": float, "tanindi": bool} ],  # son 5
  "aciklama": str,                   # tek cümle Türkçe durum yorumu (aşağıda)
}
```

`aciklama` örnekleri: canlı+verified → "Bu oturumda Kadir'in sesi %X eşleşmeyle
doğrulandı."; canlı+henüz yok → "Sesli oturum açık ama henüz doğrulanmış söyleyiş yok.";
metin kanalı → "Bu kanal metin — canlı ses kanıtı yok; profil özeti yine de geçerli."

## 2. Veri kaynakları (hepsi mevcut)

- Canlı sinyal: `voice_trust.lookup(tool_context)` (politika katmanıyla AYNI okuma yolu
  — iki ayrı gerçek kaynağı oluşmaz).
- Profil/geçmiş: `speaker_store.load_profile` + `speaker_history.load_history` üzerinden,
  `tools._memory.db` ile; `user_id = tool_context.session.user_id` (public Session alanı,
  voice_trust.lookup ile aynı erişim deseni). Mevcut hafıza araçları gibi senkron okuma
  (aynı konvansiyon).
- `tool_context` enjeksiyonu: ADK FunctionTool'un `tool_context` parametre adına göre
  enjeksiyonu **kurulu 1.36.2 kaynağından doğrulanacak** (3a C1 dersi: mekanizma
  seçimini kaynaktan doğrulamadan plana yazma).

## 3. Güvenlik sınırları (değişmez)

- Araç SALT-OKUNUR. Yönetim uçlarının (sil/etiketle/düzelt) modele araç olarak
  verilmesi bu dilimin DIŞINDA — açık Kadir kararı olmadan verilmez.
- INSTRUCTION'a eklenecek satırlar sesi olduğundan güçlü ANLATMAMALI: ses eşleşmesi
  bir **risk sinyalidir**, sert kimlik kanıtı değildir (presence istemci-beyanlı, 3a
  spec §12). Model "ikinci faktörle doğruladım" tarzı iddia ETMEMELİ; "sesinden
  tanıdım (skor %X)" diyebilir.
- Hata durumunda araç istisna fırlatmaz: `{"hata": "ses kimliği durumu şu an
  okunamıyor"}` döner (araç hatası ses/metin akışını bozamaz; tools.py'nin mevcut
  hata davranış konvansiyonuna uyulur).
- `trust_provider=None` metin-yolu yapısal ayrımı DEĞİŞMEZ: bu araç trust'ı
  `voice_trust.lookup` ile okur; politika zinciriyle ilgisi yok.

## 4. Test stratejisi

- Canlı sinyal varken: alanlar sinyalden geliyor (sahte tool_context + publish).
- Sinyal yokken (metin): `canli_ses_kanali=False`, profil özeti yine dolu.
- Vektör sızıntısı: dönen yapıda hiçbir seviyede `vec`/`anchors` ham verisi yok
  (recursive tarama, 3d'deki desen).
- Firestore hatası → `{"hata": ...}`, istisna yok.
- GREEN bölge kaydı: `config.TOOL_ZONES["get_speaker_status"] == ZONE_GREEN`, ve
  aracın `ALL_TOOLS`'da olduğu (üretim wiring'i — accessor dersi).
- INSTRUCTION güncellemesi: araca işaret eden satır var; "ikinci faktör" iddiası yok.
