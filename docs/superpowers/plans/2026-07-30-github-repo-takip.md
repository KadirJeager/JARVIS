# Plan — GitHub Repo Takip Sistemi (Katman 2b sonrası, yeni yetenek)

**Tarih:** 30 Temmuz 2026
**Spec:** `docs/superpowers/specs/2026-07-30-github-repo-takip-design.md` (onaylı)
**Dal:** `feat/github-repo-takip` (main @ 8232be5'ten)
**Test ortamı:** `cd brain && .venv/bin/python -m pytest tests -q` (torch-free .venv yeter; torch'a dokunulmaz)

## Görev 1 — `brain/app/repo_watch.py` çekirdeği + testler (TDD)

İki koleksiyon: `repo_watch` (doc id = `owner/repo`), `repo_watch_events` (auto-id).
Şema spec §2'deki gibi.

1. **KIRMIZI:** `brain/tests/test_repo_watch.py` — sahte `GitHubClient` (senaryolu
   yanıtlar) + `FakeDB` ile:
   - yeni repo (alanları None) ilk turda **olay üretmez**, baseline yazar
   - yeni release tag'i → 1 `release` olayı (title=tag, detail ≤500 char, surfaced=False)
   - yeni commit'ler → 1 `commits` olayı (en fazla 10 başlık detail'de)
   - ikisi birden → 2 olay
   - 304 / değişiklik yok → 0 olay, `last_check` güncellenir, `last_error=None`
   - releases ucunda 404 → "release yok" demektir, hata DEĞİL
   - commits ucunda 404 veya ağ hatası → `last_error` yazılır, **tur devam eder**,
     özet dict'te `hata` sayılır
   - dönen özet: `{"kontrol": n, "olay": n, "hata": n}`
2. **YEŞİL:** `repo_watch.py`:
   - `GitHubClient` — `urllib.request` (proje standardı, bkz. `live_model.py`;
     yeni bağımlılık YOK). `get(path, etag) -> (status, etag, json|None)`.
     `If-None-Match` gönderir; token varsa (`GITHUB_TOKEN` env) `Authorization:
     Bearer`. 304 → `(304, etag, None)`; 2xx → parse; diğer → `GitHubError`
     (gözlem metniyle). Timeout 15 sn. `User-Agent: jarvis-brain-repo-watch`.
   - `poll_once(db, client, now_fn) -> dict` — spec §3'teki akış; repo başına
     hata izole (try/except GitHubError → last_error, continue).
   - Sabitler: `WATCH_COLLECTION`, `EVENTS_COLLECTION`, `MAX_COMMITS=10`,
     `RELEASE_DETAIL_CHARS=500`.
3. Commit: `feat(repo-watch): poller core — baseline, release/commit diff, per-repo error isolation`

## Görev 2 — ajan araçları + testler (TDD)

1. **KIRMIZI:** `brain/tests/test_repo_tools.py` — FakeDB + enjekte sahte client:
   - `watch_repo` biçim reddi (`"foo"` gibi `owner/repo` olmayan → hata dict'i, DB'ye yazmaz)
   - `watch_repo` GitHub 404 → hata gözlemi döner, DB'ye yazmaz
   - başarılı `watch_repo` → doc oluşur (baseline alanları None), "izlemeye alındı"
   - aynı repo ikinci kez → "zaten izleniyor" (üzerine yazılmaz — `create()`)
   - `unwatch_repo` var olanı siler; olmayanı bildirir
   - `list_watched_repos` → repo/note/last_check/last_error projeksiyonu
   - `get_repo_updates` → surfaced=False'ları döndürür VE surfaced=True yapar;
     ikinci çağrı boş döner (mutasyon pimidir: surfaced güncellemesi silinirse kırılmalı)
2. **YEŞİL:** `brain/app/tools.py`'ye 4 araç: `watch_repo`, `unwatch_repo`,
   `list_watched_repos`, `get_repo_updates`. Araçlar hata FIRLATMAZ; gözlem
   dict'i döner (mevcut `get_speaker_status` deseni). DB erişimi mevcut
   `_memory.db` üzerinden; GitHub client tembel modül tekilinden
   (`repo_watch` + `GITHUB_TOKEN`). Docstring'ler Türkçe (LLM araç açıklaması).
   `ALL_TOOLS` ve `config.TOOL_ZONES` güncellenir:
   `watch_repo`/`unwatch_repo` → YELLOW, `list_watched_repos`/`get_repo_updates` → GREEN.
   `agent.py` INSTRUCTION'a bir satır: oturum başında `get_repo_updates` çağır,
   yeni olay varsa kısa Türkçe özetle (kaynak linkiyle).
3. **Mevcut suite kırılmasın:** `test_tools.py`, `test_policy.py` (yeni araçlar
   zone tablosunda olmalı; DEFAULT_ZONE=RED kontrolü varsa oraya da işlenir).
4. Commit: `feat(repo-watch): agent tools — watch/unwatch/list/updates, zones, instruction line`

## Görev 3 — scheduler ucu + auth + testler (TDD)

1. **KIRMIZI:** `brain/tests/test_repo_watch_api.py`:
   - token yok → 401; geçersiz token → 401; yanlış SA e-postası → 403
   - `JARVIS_SCHEDULER_SA` / `JARVIS_SCHEDULER_AUD` yapılandırılmamış → 503
   - doğru token → poller özeti döner (poller monkeypatch'lenir; gerçek ağ yok)
2. **YEŞİL:**
   - `auth.py`'ye `require_scheduler(authorization: Header)` — OIDC token'ı
     `id_token.verify_oauth2_token(token, grequests.Request(), audience)` ile
     doğrular (audience = `config.SCHEDULER_AUD`), `email` claim'i
     `config.SCHEDULER_SA`'ya eşit olmalı.
   - `config.py`'ye `SCHEDULER_SA`/`SCHEDULER_AUD` env okuma.
   - `main.py`'ye `POST /api/jobs/repo-watch` — `Depends(require_scheduler)`;
     `poll_once` `asyncio.to_thread` ile (Firestore + ağ, loop dışı — enroll
     endpoint'indeki gerekçenin aynısı).
3. Commit: `feat(repo-watch): /api/jobs/repo-watch endpoint behind scheduler OIDC`

## Görev 4 — dokümantasyon

- `brain/README.md`'ye kısa bölüm: koleksiyonlar, env'ler (`GITHUB_TOKEN`,
  `JARVIS_SCHEDULER_SA`, `JARVIS_SCHEDULER_AUD`), scheduler kurulum komutu
  (spec §6).
- Commit: `docs(repo-watch): README section + scheduler setup`

## Doğrulama (her görev sonrası)

`cd brain && .venv/bin/python -m pytest tests -q` — tam suite yeşil.

## Deploy sonrası (bu planın dışında, ayrı onayla)

- Cloud Scheduler job + SA + env'ler (spec §6), tek tohum repo ile canlı tetik.
- Tohum repo adlarının GitHub'da doğrulanması (PixelRAG, codebase-memory-mcp).
