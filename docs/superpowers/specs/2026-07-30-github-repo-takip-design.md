# GitHub Repo Takip Sistemi — Tasarım Spec'i

**Tarih:** 30 Temmuz 2026
**Durum:** taslak — Kadir onayı bekliyor
**Bağlam:** Kadir ilginç repo'ları (PixelRAG, codebase-memory-mcp vb.) sosyal
medyada görüp not alıyor; bunlar kayboluyor. İstenen: Jarvis'in bu tarz
repo'ları **düzenli takip etmesi** ve değişiklikleri Kadir'e özetlemesi.
Bu, North Star §8.5'teki "araç kazanım merdiveni"nin besleyicisidir: izlenen
repo önce **aday**dır, olgunlaşınca merdivenden geçip araç olur (bu spec
yalnız izleme+özeti kapsar; merdiven entegrasyonu ayrı iş).
İlk tohum liste: `PixelRAG` (görsel RAG, Apache-2.0), `codebase-memory-mcp`
(tam repo adları uygulama planında GitHub aramasıyla doğrulanacak — sosyal
medya paylaşımındaki iddialar kanıt sayılmaz).

## 1. Kapsam ve ilkeler

- **v1 kapsamı:** repo izleme listesi (ekle/çıkar/listele), saatlik periyodik
  kontrol, yeni release ve default-branch commit'i tespiti, olayların Kadir'e
  sohbet başında Türkçe özetlenmesi.
- **v1 dışı (bilinçli):** issue/PR takibi, yıldız sayısı, konu/etiket keşfi
  ("bana benzer repo'lar bul"), otomatik araç-adayı puanlaması, PixelRAG
  entegrasyonu. Bunlar sistemin doğal genişleme noktaları; şimdi eklenirse
  kırılgan-geniş olur (İlke 10: dar-sağlam).
- **İlke 4 (hata = gözlem):** GitHub API hatası (403 rate-limit, ağ, 404) yutulmaz;
  repo kaydına `last_error` yazılır ve `list_watched_repos` çıktısında görünür.
- **İlke 9 (maliyet):** beyin zaten Cloud Run + scale-to-zero; tetikleyici
  Cloud Scheduler'dır (ayda 3 ücretsiz iş; 1 iş yeter). GitHub kota dostu:
  koşullu istek (ETag/If-None-Match) ile değişmeyen repo **0 kota** harcar.
- Repo listesi tek kullanıcılı sistem olduğundan **kullanıcı bazlı değil,
  tekil** koleksiyon (profile/main gibi).

## 2. Veri modeli (Firestore)

`repo_watch` (doc id = `owner/repo`'nin `%2F`-kodlu hali — Firestore doc id'si
`/` içeremez; `repo_watch.doc_id()` yardımcısı üretir. FakeDB bu kuralı
zorlamadığı için ham-id hatası testleri geçip canlıda patlamıştı, 30 Temmuz
tohum kaydında yakalanıp düzeltildi), tekil:

```python
{
  "repo": "owner/repo",          # doc id ile aynı; projeksiyon kolaylığı
  "note": str,                   # Kadir'in notu: "PixelRAG — görsel RAG adayı"
  "added_at": str,               # ISO
  # poller durumu:
  "last_check": str | None,
  "last_error": str | None,      # son hatanın gözlemi; başarıda None'a çekilir
  "release_etag": str | None,    # koşullu istek için
  "commits_etag": str | None,
  "last_release_tag": str | None,
  "last_commit_sha": str | None,
}
```

`repo_watch_events` (auto-id):

```python
{
  "repo": "owner/repo",
  "kind": "release" | "commits",
  "title": str,                  # "v1.3.0" ya da "7 yeni commit"
  "detail": str,                 # kısa özet (release notunun ilk ~500 char'ı / commit başlıkları)
  "url": str,
  "ts": str,                     # ISO
  "surfaced": False,             # Kadir'e gösterildi mi
}
```

Olaylar birikir; `surfaced` bayrağı "gösterildi" demektir, silinmezler
(geçmiş = öğrenme malzemesi, İlke 5).

## 3. Poller (`brain/app/repo_watch.py`)

Saf mantık + ince HTTP katmanı; torch'suz test edilir (mevcut test düzeniyle
aynı: FakeDB + sahte GitHub istemcisi).

- `GitHubClient`: `GET /repos/{r}/releases/latest` ve
  `GET /repos/{r}/commits?per_page=10` — her ikisinde de `If-None-Match:
  <etag>`; 304 → değişiklik yok. Token **opsiyonel**: `GITHUB_TOKEN` env
  (Secret Manager'dan) varsa Authorization header; yoksa anonim kota
  (60 istek/saat/IP — ~10 repo × 2 uç × saatlik = 20 istek, yeterli; token
  eklemek kota baş ağrısı çıkarsa tek satırlık iştir).
- `poll_once(db, client, now_fn) -> dict`: her izlenen repo için
  1. release sorusu → yeni tag ise `repo_watch_events`a `release` olayı
  2. commits sorusu → `last_commit_sha`'dan sonraki commit'ler varsa tek
     `commits` olayı (başlıklar detail'de, en fazla 10)
  3. durum alanlarını güncelle; hata olursa `last_error` yaz, repo'yu atla,
     diğerlerine devam (bir repo'nun 404'ü tüm turu öldürmez).
  Dönen dict: `{"kontrol": n, "olay": n, "hata": n}` — scheduler log'u için.
- **Yeni eklenen repo ilk turda olay ÜRETMEZ:** `last_release_tag`/`last_commit_sha`
  None ise mevcut durum baseline olarak kaydedilir (yoksa 10 yıllık tarih
  "yeni" diye raporlanır). Bu davranış testle pinlenir.

## 4. HTTP ucu (`main.py`)

`POST /api/jobs/repo-watch` — Cloud Scheduler çağırır. Koruma: mevcut
`require_user` deseninin servis ikizi — Scheduler'ın OIDC token'ını
`id_token.verify_oauth2_token(token, audience=<service-url>)` ile doğrula,
`email` claim'i `JARVIS_SCHEDULER_SA` env'indeki servis hesabına eşit olmalı.
Cloud Run tarafında servis zaten invoker-IAM'lı; uç ayrıca uygulama içinde
kontrol edilir (derinlikli savunma). Yanıt: poller'ın özet dict'i.

## 5. Ajan araçları (`tools.py`)

| Araç | Bölge | İş |
|---|---|---|
| `watch_repo(repo, note="")` | YELLOW | `owner/repo` biçimini doğrula, yoksa GitHub'dan varlık kontrolü (404 ise ekleme — hata gözlem olarak döner), baseline alanları boş ekle |
| `unwatch_repo(repo)` | YELLOW | listeden çıkar; olaylar silinmez |
| `list_watched_repos()` | GREEN | liste + `last_error` dahil durum |
| `get_repo_updates()` | GREEN | `surfaced=False` olayları döndür VE surfaced işaretle |

`TOOL_ZONES`'a eklenir; `DEFAULT_ZONE = RED` olduğundan kayıt şart.
`AGENT_NAME` instruction'ına bir satır: *"Oturum başında get_repo_updates
çağır; yeni olay varsa Türkçe, kısa özetle — kaynak linkiyle."*

**Neden push değil pull:** FCM/push altyapısı henüz yok; sohbet-başı pull
mevcut düzenin (get_user_profile gibi) aynası ve sıfır yeni kanal demek.
Push geldiğinde olay katmanına (Pub/Sub) taşınır — veri modeli buna uygun
(`surfaced` bayrağı taşımayı bloklamaz).

## 6. Scheduler (plan aşamasında kurulur, kod değil)

```bash
gcloud scheduler jobs create http repo-watch \
  --schedule="17 * * * *" --time-zone=Europe/Istanbul \
  --uri="https://<brain-url>/api/jobs/repo-watch" --http-method=POST \
  --oidc-service-account-email="$JARVIS_SCHEDULER_SA" \
  --oidc-token-audience="https://<brain-url>"
```

## 7. Test stratejisi

- **Poller saf mantık:** sahte client ile — 304 (olay yok), yeni release,
  yeni commit'ler, ikisi birden, 404 (last_error yazılır, tur devam eder),
  yeni repo baseline (olay ÜRETMEZ), kota hatası gözlemi.
- **Araçlar:** FakeDB ile — ekle/çıkar/listele, `watch_repo` biçim doğrulaması
  (`owner/repo` değilse reddet), surfaced işaretleme (ikinci çağrı boş döner).
- **Uç:** sahte poller ile — yanlış/eksik OIDC → 401/403, doğru → özet döner.
- **Mutasyon fikirleri:** `surfaced` güncellemesini sil → "ikinci çağrı boş"
  testi kırılmalı; baseline kontrolünü sil → "yeni repo olay üretmez" kırılmalı.
- Gerçek GitHub'a istek atan test YOK; canlı doğrulama deploy sonrası tek
  repo ile elle yapılır (scheduler'ı bir kez tetikle).

## 8. Açık sorular (uygulama planında kapanır)

1. PixelRAG ve codebase-memory-mcp'nin **tam repo adları** GitHub aramasıyla
   doğrulanacak (sosyal medya linki görülmedi; aday adı kanıt değil).
2. `GITHUB_TOKEN` ilk sürümde konulacak mı? Varsayılan: hayır (anonim kota
   yeter), ilk 403'te eklenir — gözlem `last_error`'dan okunur.
3. Saatlik frekans yeter mi? Varsayılan evet; `note`'a "sık takip" yazılan
   repo'lar için ileride repo-bazlı frekans düşünülür (v1 dışı).
