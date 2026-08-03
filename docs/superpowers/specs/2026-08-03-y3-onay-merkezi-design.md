# Tasarım — Faz Y3: Onay Merkezi

**Tarih:** 3 Ağustos 2026
**North Star:** §4.8 (İstemciler / onay akışının kritikliği), §9 (Eylem Yetki Matrisi), §12 (Y3 satırı)
**Durum:** onaylı (Kadir'in "tüm hedefler bitene kadar otonom çalış" talimatı kapsamında)

## 1. Problem

Bugün kırmızı bölge bir **çıkmaz sokak**: `policy.make_policy_callback` kırmızı bir araç
çağrısını `"POLİTİKA ENGELİ: ... Kadir'e ne yapmak istediğini söyle ve onay iste"` metniyle
geri çeviriyor. Model Kadir'e soruyor, Kadir "evet" diyor — ve **hiçbir şey olmuyor**, çünkü
bir sonraki turda aynı araç yine aynı engele takılıyor. "Onay" kavramının sistemde bir
temsili yok: ne kuyruğu var, ne kaydı, ne de onaydan sonra eylemi çalıştıran bir yol.

North Star §12 Y3 satırının "bitti" ölçütü: *"Kırmızı bölge onayları uygulamadan akıyor; push
kaçarsa kuyruk uygulama açılınca senkronlanıyor."*

## 2. Kapsam kararı — ilk gerçek kırmızı araç

`config.TOOL_ZONES` bugün **her** aracı açıkça yeşil veya sarı yapıyor. `DEFAULT_ZONE = red`
yalnızca "zone tablosuna yazılmamış araç" için geçerli, ki öyle bir araç yok. Yani onay
merkezi, onaylanacak gerçek bir eylem olmadan doğrulanamaz bir mekanizma olurdu.

Bu yüzden bu dilim **`cancel_reminder(reminder_id)`** aracını kırmızı bölgede tanıtıyor.
Seçim gerekçesi:

- §9 kırmızı bölge örneklerinden **"bir şey silme"**nin en küçük, en zararsız örneği.
- Model bugün zaten zincirleyebiliyor: `list_reminders` (yeşil) hatırlatma ID'lerini
  döndürüyor, yeni bir keşif aracına gerek yok.
- Üretimde uçtan uca denenebilir: yanlış giderse kaybedilen şey bir hatırlatma kaydıdır.
- Zone ataması **bilinçli olarak muhafazakâr** ve konfigürasyondur (§9: "eşikler
  konfigürasyondur; güven arttıkça gevşetilebilir — kodda, sohbette değil"). Mekanizma
  oturduktan sonra sarıya alınabilir; o karar bu dilimin konusu değil.

Bu, Y4'ün (araç kazanım merdiveni §8.5, fabrika Kademe 2) ihtiyaç duyduğu onay kanalının
da temelidir: `kind` alanı bu yüzden `tool_call` dışına açık tutuluyor.

## 3. Veri modeli

### 3.1 `approvals` koleksiyonu (auto-id)

| Alan | Tür | Anlam |
|---|---|---|
| `user_id` | str | Kararı verecek kişi. Sahiplik sınırı: başkasının onayı okunamaz/karara bağlanamaz |
| `kind` | str | `tool_call` (bu dilim). İleride `tool_grant`, `agent_spec` (§8.5) |
| `title` | str | Kartın Türkçe tek satırlık başlığı |
| `detail` | str | Ne olacağı + neden onay gerektiği (Türkçe) |
| `tool_name` | str \| None | `kind=tool_call` için zorunlu |
| `tool_args` | dict | Aracın argümanları (stringify edilmiş, ≤500 karakter/değer — audit ile aynı kural) |
| `zone` | str | Karar anındaki bölge (`red`) |
| `session_id` | str | İsteğin geldiği sohbet — kart oraya düşer |
| `status` | str | `pending` \| `approved` \| `rejected` \| `expired` \| `failed` |
| `created_at` / `expires_at` | str (ISO UTC) | Kuruluş ve zaman aşımı anı |
| `decided_at` / `decided_by` | str \| None | Karar anı ve kararı veren e-posta |
| `outcome` | str \| None | Onaydan sonra aracın döndürdüğü sonuç metni (veya hata gözlemi) |

### 3.2 `approval_claims` koleksiyonu (doc id = approval id)

Kararın **birincil kaydı**. `{decision, by, at}`. Onay dokümanındaki `status` bunun bir
izdüşümüdür.

Neden ayrı bir doküman: Firestore'da işlemsiz (transaction'sız) koşullu güncelleme yoktur,
ama `DocumentReference.create()` atomik bir "yoksa yaz"dır ve `AlreadyExists` fırlatır. Bu
repo aynı deseni daha önce `conversations` başlık yarışında kullandı (`doc_ref.create()`),
`FakeDoc.create()` de zaten var. Böylece çift-onay — çift dokunuş, push + kuyruk senkronu
aynı anda — kırmızı bir eylemi **iki kez çalıştıramaz**, ek bir işlem altyapısı gerekmeden.

## 4. Değişmezler (bu dilimin taşıyıcı kuralları)

1. **Zaman aşımı = reddet, KARAR anında da uygulanır.** Süpürücü iş (`approvals-tick`) tek
   başına yeterli değildir: süpürme ile son kullanma arasındaki pencerede gelen bir onay,
   süresi geçmiş bir kırmızı eylemi çalıştırırdı. `decide()` bu yüzden **önce** süreyi
   kontrol eder ve süresi geçmişse onayı `expired`'a çeker, kararı reddeder. Süpürücü bir
   temizlik/bildirim yoludur, güvenlik sınırı değil.
2. **Karar idempotenttir.** İkinci `approve` çağrısı `{already: true, status: ...}` döner ve
   aracı **yeniden çalıştırmaz** (claim dokümanı §3.2).
3. **Kuyruk push'tan bağımsızdır.** `GET /api/approvals` bekleyenleri döndürür; uygulama her
   açılışta senkronlar. FCM kaçarsa onay kaybolmaz (§4.8).
4. **Sahiplik.** Her uç `require_user` ile korunur ve `approval["user_id"] != email` ise 404
   döner (403 değil: başka birinin onayının varlığı bile sızmasın).
5. **Yürütme hatası onayı geçersiz kılmaz.** Araç fırlatırsa `status=failed`,
   `outcome=<hata gözlemi>` — İlke 4 (hata = gözlem): hata modelden ve Kadir'den saklanmaz.
6. **Onay kartı sohbetin bir parçasıdır** (§4.8: "ayrı ekran değil"). Kart, transcript'e
   `kind="approval"` alanlı bir model mesajı olarak düşer; karar sonrası da orada kalır.

## 5. Politika bağlantısı

`make_policy_callback(audit, trust_provider=None, approval_sink=None)`.

`approval_sink`, `(tool_name, args, tool_context) -> str` imzalı **opsiyonel** bir
callable'dır. Verilmediğinde kırmızı davranış bugünkü metnin **birebir aynısıdır** —
mevcut testler ve `guest_gate` yolu değişmez.

Verildiğinde, `decision == "block"` dalında çağrılır ve döndürdüğü Türkçe metin modele
gider ("onay kartı gönderildi, Kadir'in kararını bekliyorum"). Araç yine **çalışmaz**;
çalışma anı onay anıdır.

`approval_sink` başarısız olursa (Firestore hıçkırığı) kırmızı davranış eski metne düşer —
onay oluşturulamaması bir aracın çalışmasına **asla** yol açmaz (fail-closed).

Kullanıcı/oturum kimliği `tool_context.session.user_id` / `.id` üzerinden okunur —
`voice_trust.py`'nin belgelediği public ADK yüzeyi (`ReadonlyContext.session`).

## 6. Onaydan sonra yürütme

`approvals.EXECUTORS: dict[str, Callable[[dict, str], str]]` — `(tool_args, user_id) -> sonuç`.

`kind=tool_call` için kayıtlı olmayan bir `tool_name` **yürütülmez**: `status=failed`,
`outcome="bu araç onaydan sonra çalıştırılamıyor (yürütücü kayıtlı değil)"`. Yani onay
kaydı, keyfi bir isim yazılarak rastgele kod çalıştırmanın yolu değildir — kayıt defteri
allowlist'tir.

Bu dilimin tek yürütücüsü: `cancel_reminder`.

## 7. Sohbet kartı ve wire

`messages` satırlarına iki **opsiyonel** alan eklenir: `kind` (str) ve `meta` (dict).
`MessageStore.history()` bunları yalnızca doluysa projeksiyona koyar; eski satırlar (alansız)
aynen bugünkü gibi okunur. Android tarafındaki hoşgörülü-wire sınırı (3d-3'te kurulan desen)
bilinmeyen `kind`'ı düz metin olarak çizer — yani eski istemci yeni satırda kırılmaz.

Kart durumu **canlıdır**: Android, `kind="approval"` mesajını çizerken `meta.approval_id`
ile `GET /api/approvals/{id}`'den güncel durumu okur. "Onaylandı/Reddedildi/Süresi doldu"
rozeti transcript'e gömülmez — tek gerçek kaynak onay dokümanıdır.

## 8. FCM

`fcm.py` bugün yalnızca hatırlatma gönderiyor. Ortak bir `dispatch(db, title, body, data,
fallback_text)` çıkarılır; `send_reminder` onun ince bir sarmalayıcısı olur (davranışı
değişmez, mevcut testler yeşil kalır), `send_approval` ikinci sarmalayıcıdır.

Cihaz token'ı yoksa bugünkü davranış korunur: bildirim sohbete düşer. Onay için bu zaten
yeterlidir — kart **sohbette de** var (§7).

## 9. Uçlar

| Uç | Auth | İş |
|---|---|---|
| `GET /api/approvals` | `require_user` | Bekleyenler (süresi geçmişler hariç, en fazla 50, yeniden eskiye) |
| `GET /api/approvals/{id}` | `require_user` | Tek onayın güncel durumu (kart yenilemesi) |
| `POST /api/approvals/{id}/approve` | `require_user` | Karar + yürütme; `{status, outcome, already}` |
| `POST /api/approvals/{id}/reject` | `require_user` | Karar; yürütme yok |
| `POST /api/jobs/approvals-tick` | `require_scheduler` | Süresi geçenleri `expired`'a çeker, özet döner |

## 10. Android

- `ChatMessage` wire modeline opsiyonel `kind` + `meta` eklenir (hoşgörülü sınır).
- `kind == "approval"` mesajı **onay kartı** olarak çizilir: başlık, detay, "Onayla" /
  "Reddet" düğmeleri, durum rozeti.
- Uygulama açılışında `GET /api/approvals` ile kuyruk senkronu; bekleyen onay varsa sohbetin
  altında sabitlenmiş kart(lar).
- FCM `data.approval_id` taşıyan bildirime dokunmak uygulamayı açıp ilgili kartı gösterir.
- Karar düğmeleri **iyimser güncelleme yapmaz** (3d-3 dersi): sunucu cevabı beklenir, kart
  sunucunun döndürdüğü duruma geçer.

## 11. Kapsam dışı (bilinçli)

- Sarı bölge bildirimleri (§4.8'de onay kartlarının yanında anılıyor) — ayrı, küçük dilim.
- Onay kartlarının Wear OS karşılığı (Y4/donanım).
- Onay geçmişi ekranı; karar verilmiş onaylar transcript'te zaten görünür.
- Çok kullanıcılı sahiplik devri; tek kullanıcı varsayımı sürüyor ([[kapsam-tek-kullanici]]).
