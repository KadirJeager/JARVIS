# Katman 2b — Dilim 3d: Ses Kimliği Yönetimi (görünürlük, düzeltme, silme) — Tasarım / Spec

**Tarih:** 24 Temmuz 2026
**Durum:** plan yazıldı (docs/superpowers/plans/2026-07-24-katman-2b-dilim3d-ses-kimligi-yonetimi-server.md), sunucu implementasyonu tamam, nihai review bekliyor. 3d-3 (Android tarafı) ayrı, ileriye dönük bir plan.
**Öncül:** Dilim 3a (sunucu çekirdeği) main'e merge edildi (`6c7b177`). Bu spec 3a'nın
üstüne biner ve onun veri modelini genişletir.
**İlgili:** `2026-07-24-katman-2b-dilim3a-ses-kimligi-design.md` (§5 galeri modeli,
§10 enrollment, §12 mahremiyet/güvenlik sınırları)

---

## 1. Amaç

Dilim 3a Kadir'i sesinden tanıyan bir çekirdek üretti, ama o çekirdek **görünmez ve
geri alınamaz**: profil yazılabiliyor (`POST /api/voice/enroll`, yalnızca ekleme),
okunamıyor, silinemiyor, düzeltilemiyor. Galeri kendi kendini besliyor ve insan
düzeltmesi yok.

Bu dilim üç soruyu cevaplanabilir kılar:

1. **Sistem beni ne kadar iyi tanıyor?** — galeride ne var, hangi koşullardan, son
   söyleyişlerde hangi skorlar alındı, gidişat iyi mi kötü mü.
2. **Yanlış yaptığında düzeltebilir miyim?** — "bu bendim" / "bu ben değildim".
3. **Vazgeçebilir miyim?** — profili gerçekten silebilmek.

Nihai amaca (ikame) bağı: Kadir'in sesi, ses yüzeyindeki kimlik sinyalinin tamamı.
Sinyalin kalitesi ölçülemiyorsa güvenilemez; güvenilemiyorsa ikame edecek yetki
verilemez. Görünürlük bu yüzden konfor değil, ön koşul.

## 2. Kapsam ve bölümleme

| Parça | İçerik | Mikrofon gerekir mi |
|---|---|---|
| **3d-1** | Şema genişletmesi + doğrulama geçmişi kaydı | hayır |
| **3d-2** | Yönetim uçları: oku / sil / etiketle / düzelt | hayır |
| **3d-3** | Android yönetim ekranı (biyometri kapılı) | hayır |
| **3b'ye devredildi** | "Kaydet" / "Yeniden eğit" akışları | **evet** |

**Kapsam dışı (borç değil, sınır):** mikrofonlu kayıt/yeniden kayıt akışı (3b),
çoklu kullanıcı, ses kaydı saklama, sunucuda biyometrik doğrulama.

## 3. Zamanlama kararı — şema enrollment'tan ÖNCE

`SpeakerProfile.anchors` bugün metadatasız düz vektör listesi. Görünürlük için
kaynak/tarih/cihaz/etiket alanları gerekiyor.

Kritik gerçek: **profil henüz hiç kullanılmadı — Kadir ses kaydetmedi, üretimde
`speaker_profiles` belgesi yok.** Şema değişikliği için en ucuz an bu. Enrollment'tan
sonra yapılırsa gerçek biyometrik veriyi taşımak (migration) gerekir.

**Karar:** 3d-1 enrollment HITL'inden önce tamamlanır. Kayıt birkaç saat gecikir,
göç işi tamamen ortadan kalkar.

## 4. Veri modeli

### 4.1 Örnek (sample) — çapa ve uyarlanabilir için ORTAK şekil

Bugün çapalar düz `list[float]`, uyarlanabilirler `{vec, device_hint, ts}`. İkisi tek
şekle getirilir:

```python
{
  "id": str,                                 # kararlı kimlik: PATCH/DELETE bunu hedefler
  "vec": [float] * 192,
  "source": "enroll" | "auto" | "manual",   # nereden geldi
  "ts": "2026-07-24T11:00:00Z",
  "device_hint": "phone" | "headset" | "unknown",
  "label": "saglikli" | "hasta" | "yorgun" | "gurultulu" | "kulaklik" | "hoparlor" | "arac" | None,
  "note": str | None,                        # serbest metin, opsiyonel
}
```

`id` **zorunlu ve kararlı**: liste içindeki konum kimlik olarak kullanılamaz, çünkü
eviction (en gereksiz örneğin atılması) konumları kaydırır ve istemcinin elindeki
"3. örneği sil" isteği yanlış örneği silerdi. Üretimde uuid, testlerde enjekte
edilebilir bir üreteç.

`source` alanı davranışı belirler (§5). `label` sabit küme + serbest not: sabit küme
toplulaştırmayı mümkün kılar ("gürültülü ortamda ortalama skor 0.41"), serbest not
sabit kümenin kaçırdığını yakalar.

**Geriye dönük uyum gerekmiyor** — üretimde veri yok (§3). Yine de `load_profile`
eski düz-vektör biçimini okuyabilmeli (tek satırlık normalizasyon), çünkü geliştirme
sırasında yazılmış fixture'lar var.

### 4.2 Doğrulama geçmişi — YENİ koleksiyon

`speaker_history/{user_id}` altında halka tamponu (son **50** kayıt):

```python
{
  "id": str,                    # düzeltmenin hedefleyeceği kimlik
  "ts": "...",
  "score": float,               # ölçülen kosinüs
  "verified": bool,
  "device_hint": str,
  "presence": str,
  "trust_level": str,           # füzyon sonucu
  "adapted_sample_id": str | None,   # bu söyleyiş galeriye girdiyse hangi örnek
  "correction": "confirmed" | "rejected" | None,   # insan düzeltmesi (idempotanslık için)
  "vec": [float] * 192,         # "bu bendim" demeyi mümkün kılan şey
}
```

**Vektör saklanır, ses saklanmaz.** Gömme geri döndürülüp dinlenemez; galeride zaten
aynı türden veri tutuluyor. Ses kaydı saklamak mahremiyet açısından bambaşka bir
yükümlülük olurdu ve bu dilimde yapılmaz.

50 sınırı: kalibrasyon için fazlasıyla yeterli, hem maliyet hem mahremiyet açısından
sınırlı. Halka tamponu — 51. kayıt en eskiyi düşürür.

## 5. Elle eklenen örneğin yetkisi

Galeri örneğinin **iki ayrı rolü** var ve `manual` bunların yalnız birine katılır:

| Rol | Neye bakılır | `manual` dahil mi |
|---|---|---|
| **ACCEPT** — "bu Kadir mi?" | tüm galeri (çapa ∪ uyarlanabilir) | **evet** |
| **ADAPT** — "bu yeni sesi öğrenelim mi?" | **yalnız çapalar** | hayır |

Yani elle eklenen örnek **oy kullanır, hakemlik yapmaz**. Faydası doğrudan: hasta
sesini elle eklersen ertesi gün hasta halinle tanınırsın. Hakemlikten dışlanması ise
zincirleme kaymayı keser — yanlış giren bir örnek kendine benzeyen bir sonrakini
içeri alamaz, hata kendisiyle sınırlı kalır.

Bu ayrım **yeni kod gerektirmez**: 3a'da ADAPT zaten `SpeakerProfile.anchor_score`
ile yalnız çapalara karşı skorlanıyor. `manual` örnekler `adaptive` listesine
girdiği için otomatik olarak hakemlik dışında kalıyor.

Ek kısıtlar:

- `manual` örnek **asla çapa olamaz** (çapalar sürüklenmeye karşı sabit referans).
- `manual` örnek sayısı sınırlı: **5** (`JARVIS_SPEAKER_MANUAL_CAP`).

**Sınırın ne olduğu / ne OLMADIĞI konusunda dürüstlük:** bu sayı *kazaya* karşı —
gürültülü bir kaydı birkaç kez yanlışlıkla "bendim" diye işaretlemek galerinin
şeklini bozmasın diye. Cihaz kilidini geçmiş *kararlı* birine karşı değil; o kişi
zaten Kadir'in ID token'ıyla ses doğrulamasını tamamen atlayabilir (3a spec §12:
`presence` istemci-beyanlı, tek gerçek sınır token). Buradaki asıl güvence sayı
değil, **geri alınabilirlik**: her `manual` örnek ekranda kaynağıyla görünür ve tek
işlemle silinir.

## 6. API yüzeyi (sunucu — yetki ve mantık burada)

Hepsi `require_user` arkasında (bugünkü enrollment ucuyla aynı), hepsi kullanıcıya
anahtarlı.

| Uç | İş |
|---|---|
| `GET /api/voice/profile` | Galeri özeti: çapa/uyarlanabilir/elle sayıları, kaynak+etiket+cihaz kırılımı, örnek listesi (vektörsüz), geçmişin **tamamı** — halka tamponu zaten 50 ile sınırlı, ikinci bir sayfalama katmanı eklemek erken karmaşıklık olur — ve türetilmiş kalite göstergeleri |
| `PATCH /api/voice/sample/{id}` | Etiket ve/veya not güncelle |
| `DELETE /api/voice/sample/{id}` | Tek örneği sil (çapa dahil — ama son çapa silinemez, §8) |
| `DELETE /api/voice/profile` | Profili **ve geçmişi** sil |
| `POST /api/voice/history/{id}/confirm` | "Bu bendim" → geçmişteki vektörü `manual` örnek olarak galeriye ekler (cap + işaretleme kuralları uygulanır) |
| `POST /api/voice/history/{id}/reject` | "Bu ben değildim" → o söyleyiş galeriye girdiyse (`adapted_sample_id`) o örneği siler; geçmiş kaydı reddedildi diye işaretlenir |

**Her iki düzeltme ucu da idempotent.** Geçmiş kaydı hangi düzeltmeyi aldığını
saklar (`correction: "confirmed" | "rejected" | None`) ve aynı kayda ikinci kez
aynı işlem uygulanmaz — aksi halde çift dokunan bir istemci cap'i boşa harcar ya da
zaten silinmiş bir örneği tekrar silmeye çalışır. Ters yönde düzeltme (önce
"bendim", sonra "ben değildim") **serbesttir**: eklenen `manual` örnek geri
alınır. Fikir değiştirmek meşru bir kullanım; yasaklamak kullanıcıyı yanlış bir
kaydın üstünde kilitli bırakırdı.

**Vektörler asla dışarı verilmez.** İstemcinin onlara ihtiyacı yok; düzeltme
kimlikle yapılıyor. Biyometrik veriyi ağ üstünde gereksiz dolaştırmamak ilke.

### 6.1 Türetilmiş kalite göstergeleri (`GET /api/voice/profile` içinde)

Ham sayıya ek olarak yorumlanmış çıktı: doğrulanmış söyleyişlerin yürüyen ortalama
skoru, başarısız oran, **etiket kırılımlı ortalama** (kalibrasyonun asıl işine
yarayan şey), ve son 10 ile önceki 10 arasındaki eğilim.

## 7. Güvenlik duruşu — iki katman, birbirine güvenmeden

**İstemci katmanı:** yönetim ekranı Android'de `BiometricPrompt` +
`DEVICE_CREDENTIAL` yedeğiyle korunur (parmak izi/yüz, yoksa telefonun PIN/deseni).
Uygulamaya özel PIN **yazılmaz** — ayrı bir sır üretmek/saklamak/korumak zorunda
kalmak, telefonun kendi kilidinden daha zayıf ve daha bakımlı bir çözüm olurdu.

**Sunucu katmanı:** yukarıdakine **güvenmez.** Sunucu telefonun gerçekten biyometri
sorduğunu doğrulayamaz — `presence` ile aynı sınıf, doğrulanamaz istemci beyanı.

**Bu yüzden "biyometri yaptım" başlığı GÖNDERİLMEZ.** Doğrulanamayan bir sinyali
protokole koymak, güvenlik gibi görünüp güvenlik olmayan bir şey üretir; bu dilimde
aynı hatayı bir kez yaptık ve 3a spec §12'de kayda geçirdik. Sunucu kendi frenlerini
(§5) bağımsız olarak uygular.

Böylece: biyometrik kapı **gerçek tehdide** (eline geçen açık telefon) karşı gerçek
bir koruma; sunucu frenleri ise kapı hiç olmasa da geçerli.

## 8. Silme davranışı

- `DELETE /api/voice/profile` → `speaker_profiles/{user_id}` **ve**
  `speaker_history/{user_id}` birlikte silinir. Yarım silme yok.
- Tek örnek silme çapaları da kapsar, **ama son çapa silinemez**: çapasız bir profil
  ACCEPT skorlayamaz ve ADAPT hakemliği referanssız kalır — sessizce işlevsiz bir
  profil yerine açık bir hata (400) döner. Tamamen silmek isteyen profil silme
  ucunu kullanır.

## 9. Android yönetim ekranı (3d-3)

Sohbet ekranından "Ayarlar → Ses kimliğim". Açılışta biyometrik kapı. İçerik:

- **Durum kartı:** tanınma kalitesi (yürüyen ortalama + eğilim), düz Türkçe yorum.
- **Galeri listesi:** her örnek kaynak rozeti (`kayıt` / `otomatik` / `elle`), tarih,
  cihaz, etiket. Etiket düzenlenebilir, örnek silinebilir.
- **Son söyleyişler:** skor + tanındı/tanınmadı; her satırda "bendim" / "ben
  değildim".
- **Tehlikeli bölge:** profili sil (yazarak onay).
- **3b'de eklenecek yer tutucular:** "Ses kaydet" / "Yeniden eğit" — ekran baştan
  bunları bekleyecek şekilde kurulur.

Mimari: 3a'nın istemci-agnostik ilkesi korunur — ekran yalnızca §6 uçlarını tüketir,
hiçbir karar istemcide alınmaz.

## 10. Hata yönetimi

- Tüm uçlar Türkçe kullanıcı mesajı döner; altyapı hataları 502, kural ihlalleri 400.
- Geçmiş kaydı bulunamazsa 404 (silinmiş/halka tamponundan düşmüş olabilir) —
  istemci bunu "bu kayıt artık yok" diye gösterir, sessizce yutmaz.
- `manual` cap aşımında 400 + kaç tane olduğu ve hangisini silebileceği söylenir.
- Galeri yazan her uç, 3a'daki `_gallery_lock`'u paylaşır ve olay döngüsü dışında
  çalışır (3a'da bu iki kural kanla öğrenildi: kilit paylaşılmazsa yazma kaybı,
  döngüde çalışırsa tüm servis donuyor).

## 11. Test stratejisi

- **Şema:** eski düz-vektör biçiminin okunabildiği, yeni alanların yazıldığı.
- **Yetki ayrımı (§5'in çekirdeği):** `manual` örneğin ACCEPT skoruna **girdiği**,
  ADAPT hakemliğine **girmediği** — ikisi ayrı ayrı, mutasyonla doğrulanmış.
- **Cap:** 6. `manual` örnek 400 döner; cap'i aşan bir yol yok.
- **Çapa koruması:** `manual` örnek hiçbir yoldan çapa olamaz; son çapa silinemez.
- **Silme bütünlüğü:** profil silindiğinde geçmişin de gittiği.
- **Halka tamponu:** 51. kayıt en eskiyi düşürür, sınır aşılmaz.
- **Vektör sızıntısı:** hiçbir yanıt gövdesinde `vec` alanı bulunmadığı (bu bir
  mahremiyet iddiası, testle çivilenmeli).
- **Eşzamanlılık:** düzeltme ile canlı `identify()` aynı anda çalıştığında yazma
  kaybı olmadığı (3a'nın `_gallery_lock` testlerinin deseni).

## 12. Bilinçli açık uçlar (plan aşamasında kesinleşecek)

- Etiket sabit kümesinin son hali (kalibrasyonda hangi ayrımların işe yaradığı
  görülünce netleşir).
- Kalite göstergesinin eşikleri ("iyi/orta/zayıf" nerede başlar) — gerçek skorlar
  görülmeden sayı uydurmak anlamsız.
- Geçmişin 50 sınırı ve `manual` cap 5: ikisi de env ile ayarlanabilir olacak,
  varsayılanlar kalibrasyondan sonra gözden geçirilir.
