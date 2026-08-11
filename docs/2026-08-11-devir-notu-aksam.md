# Devir notu — 11 Ağustos 2026, akşam (gece+gündüz vardiyası kapanışı)

Bu not sabahki `docs/2026-08-11-devir-notu-sabah.md`'nin üstüne 05:45 → 18:50 arasını ekler. Çelişirlerse **bu** güncel.

## 1. Tek cümlede

Ses kimliği dilimi bitti ve canlıda; tek-`AudioRecord` PFD beslemesi kanıtlandı ve yan ürünü olarak sesli modun gerçek bir kırılganlığı bulunup kapatıldı; North Star gerçeğe eşitlendi; ve öğleden sonra **ürün yüzeyine** geçildi — rekabet analizinden 15 frontend özelliği + backend mekanizması çıkarılıp sıralandı, ilk dilim (Onay Kartı 2.0) sunucu tarafında 3 task tamam.

## 2. Canlı durum

| | Değer |
|---|---|
| Dal | `feat/antispoof-cm` — main'in ~60+ commit önünde, merge kararı Kadir'in |
| Prod imaj | `gcr.io/your-gcp-project/jarvis-brain:cm-60e568b` |
| jarvis-voice | `00032-gpd` · 4Gi · CM warm · sessionAffinity |
| jarvis-brain | `00039-jtp` · 3Gi · CM yüklemiyor + tembel-yükleme koruması |
| Brain testleri | **920+** (gün başında 877) |
| Android | JVM + assembleDebug + compileDebugAndroidTestKotlin üçü de yeşil |
| APK | Pixel 10 Pro'da (16:34), tanıyıcı-fallback düzeltmesi dahil (paket içinde `ev=recognizer.fallback` doğrulandı) |

## 3. Bugün kapanan işler

### Ses kimliği / PFD (gece + sabah)
- Üç CM kapısı canlı (enroll fail-closed, "bu bendim" kanıt-okuma, challenge taze-cm).
- **PFD probu POZİTİF:** tr-TR tanıyıcı `ParcelFileDescriptor` beslemesini okuyup **fikstürün metnini** döndürdü — yani boru hattı çalışıyor, ambient ses değil bizim sesimiz transkript edildi.
- Tek-`AudioRecord` dilimi (4 task): mic tap, `PfdFeedPolicy` (süre-koşullu üç durumlu karar), `AndroidSpeechToText` PFD modu, enstrümante kanıt.
- **Üretim kusuru bulundu ve düzeltildi:** cihaz-üstü tanıyıcı **dile bakmadan** seçiliyordu; tr-TR paketi yoksa `ERROR_LANGUAGE_UNAVAILABLE` → kurtarılamaz sayılıp **arama ölüyordu**, ağa düşülmüyordu. Fallback eklendi; ölçümle ikinci katmanı da bulundu (`EXTRA_PREFER_OFFLINE` ağa düşerken de "çevrimdışını tercih et" diyordu) ve kapatıldı.
- Köprü kaydı sızıntısı (kurulum hatası ölü kayıt bırakıyordu) RED testle kanıtlanıp kapatıldı.
- 4 deploy, hepsi doğrulandı.

### Altyapı
- **github_mcp ilk kez gerçekten çalışıyor:** imajda Node yoktu, `npx -y @modelcontextprotocol/server-github` her turda spawn hatası veriyordu. Node + paket imaja gömüldü; loglarda "MCP toolset bağlandı" görünüyor, spawn hatası sıfır. (PAT yok → anonim limitler; mint kararı Kadir'in.)
- LESSONS.md 93 → 40 girdi (gerisi ARCHIVE.md'ye).
- 8 ertelenen minor kapandı.

### North Star (`JARVIS_Proje_Belgesi.md`, commit 4ef1665)
§12 durum bloğu 3 Ağustos'ta donmuştu. Güncellendi: Y3 ✅, fabrika K1+K2 canlı ve K3 sayacı işliyor, Y4'ün gerçekte neyi eksik (Keep MCP + Wear), GitHub MCP'nin bugüne kadar kağıt üstünde olduğu, ses kimliği işinin **yeni katman değil §4.8 vizyonunun ön koşulu** olduğu, ve **Faz D'nin tetiğinin yarı dolduğu** (telefon geldi; birleşecek proje `YourDialer` kararsız).

### Ürün yüzeyi (öğleden sonra)
- `docs/arastirma/2026-08-11-urun-yuzeyi-yol-haritasi.md` (commit 2490dea): analizden **15 aday**, her biri "backend mekanizması / alınabilir mi (kod-tip-fikir) / bizde var mı (file:line) / efor" ile. Sıra: F2 kart bağlamı → F7 sesli transkript sohbete → F3 tipli ret → F13+F9 rapor/ilerleme → F1 akış.
- **Ölçülen üç boşluk:** (1) akış yok — sunucu ADK olay akışını alıp `is_final_response()` dışını atıyor, protokol işi; (2) sesli arama zaman çizgisinin dışında (sabit `voice-{user_id}` + tam ekran overlay, "Devral" hiç yazılmamış); (3) ilerleme sinyali yok **ve** `tasks`/`retro` raporları `conversations.touch()` çağrılmadığı için uygulamada görünmüyor — `tasks.py` docstring'i tersini iddia ediyor.

### Onay Kartı 2.0 (plan: `docs/superpowers/plans/2026-08-11-onay-karti-2.md`)
Analizden Ç1 (operanda bağlı onay), §5.6 (kartta ne olmalı + onay yorgunluğu bir güvenlik açığıdır), P2 (tipli ret), P5/K3 (hash-zincirli karar kaydı).
- **Task 1 ✅** araç-başına geri alınabilirlik tablosu, bilinmeyende fail-closed. Review tutarsızlık yakaladı: iç silme "geri alınamaz" iken iç üzerine-yazma "geri alınabilir" deniyordu (`update_user_profile` çevrildi), ve tablonun False yarısı hiçbir testle çivili değildi.
- **Task 2 ✅** politikanın hesaplayıp attığı bağlam (`actor`, `trust_level`, `cause`, `operand`, `reversible`) onay kaydına taşındı. Review "Ç1 beyan edildi ama teslim edilmedi" dedi — `operand` hiçbir yerde dolmuyordu; `TOOL_OPERAND_ARG` tablosuyla (beyan et, tahmin etme) kapatıldı.
- **Task 3 🟡** tipli ret. Ölçülen bulgu: **ret modele hiçbir yoldan ulaşmıyordu.** Düzeltme turu inerken bu not yazıldı — durumu ledger'dan doğrula.

## 4. Sıradaki iş (öncelik sırasıyla)

1. **Task 3'ün düzeltme turu için scoped re-review** — kod indi, review'ü koşulmadı. İlk iş bu.
2. **Task 4** hash-zincirli karar kaydı + geri-alınabilirliğe göre zaman aşımı.
3. **Task 5-6** istemci: DTO/domain alanları + kartın kendisi (kim istedi, neye, geri alınabilir mi, hazır ret düğmeleri). Kullanıcının göreceği kısım burası.
4. **F7** sesli transkripti sohbet zaman çizgisine (tek append, "tek zaman çizgisi" cümlesini gerçekten doğru kılar).
5. **F13/F9** rapor görünürlük bug'ı + adım ilerlemesi.
6. **F1** akış (SSE) — algılanan kaliteyi en çok oynatan iş.

## 5. Kadir'e kalanlar

1. **Pixel test turu:** normal tur (ilk-kelime zaman çizelgesi ilk kez gerçek cihazda okunacak) + "Bu cihazı tanıt" enroll akışı. Senaryo sabah notunun §6'sında; kritik detay: Jarvis kodu söyleyip **bitirdikten sonra kısa bir duraklamayla** tekrar et.
2. **Merge kararı.**
3. **Saat credential kararı** (LTE mi?) — Wear'ı ve Katman 2'nin "saatten komut" ölçütünü bloke ediyor.
4. **Faz D kararı** — tetiğin yarısı doldu.
5. **GitHub PAT** (opsiyonel).

## 6. Bugünün tekrar eden dersi

Review her task'ta "yapıldı denilen ama yapılmayan" bir şey buldu: kağıt üstünde çalışan GitHub MCP, hiç dolmayan `operand`, kendi kurduğu çağrıyı doğrulayan tautolojik test, hiçbir yere ulaşmayan ret gerekçesi, fiziğe çarpan üç ayrı eşik. Hiçbiri tembellik değildi — hepsi "makul görünen ama ölçülmemiş" varsayımlardı. Ölçüm ucuz, varsayım pahalı.
