# Plan — Faz Y3: Onay Merkezi

**Tarih:** 3 Ağustos 2026
**Spec:** `docs/superpowers/specs/2026-08-03-y3-onay-merkezi-design.md` (onaylı)
**Dal:** `feat/y3-onay-merkezi` (main @ 50ec1c7'den)
**Test ortamı:** `cd brain && .venv/bin/python -m pytest -q` (torch-free .venv yeter; torch'a dokunulmaz).
Android: `cd android && ./gradlew testDebugUnitTest` (instrumented ayrı, cihaz gerektirir).
**Taban:** 465 passed, 2 skipped.

**DEPLOY YASAK (bu oturum):** Kadir "bulutta başka ajanlar da çalışıyor" dedi. Bu plan
yalnızca kod + yerel test üretir. `gcloud run services replace`, `gcloud scheduler jobs
create` ve secret yazımı **yapılmaz**; deploy adımları Görev 8'de belge olarak bırakılır.

---

## Görev 1 — `brain/app/approvals.py` çekirdeği (TDD)

1. **KIRMIZI:** `brain/tests/test_approvals.py` — `FakeDB` ile:
   - `request()` doküman yazar: `status=pending`, `expires_at = created_at + TTL`,
     `tool_args` değerleri stringify + 500 karakterde kesilmiş (audit kuralıyla aynı),
     dönen değer approval id
   - `list_pending(db, user_id)` yalnızca **o kullanıcının** `pending`'lerini, yeniden
     eskiye, en fazla 50 döner
   - `list_pending` **süresi geçmiş** olanı DÖNDÜRMEZ (süpürücü henüz koşmamış olsa bile)
   - `get(db, id, user_id)` başkasının onayında `None` döner (sahiplik, spec §4.4)
   - `decide(..., "approved")` → status/decided_at/decided_by yazılır, `outcome`
     yürütücünün döndürdüğü metin, dönen dict `{status:"approved", already: False}`
   - **aynı çağrı ikinci kez** → `{already: True, status: "approved"}` ve yürütücü
     **ikinci kez ÇAĞRILMAZ** (çağrı sayacı ile pinlenir — mutasyon pimi)
   - `decide(..., "rejected")` yürütücüyü hiç çağırmaz
   - **süresi geçmiş** bir `pending`'i onaylamak: `status=expired` yazılır, yürütücü
     çağrılmaz, dönen dict `{status:"expired", already: True}` (spec §4.1 — bu testin
     silinmesi zaman aşımı garantisini kaldırır)
   - yürütücü fırlatırsa → `status=failed`, `outcome` hata metnini taşır, çağrı yine de
     "karar verildi" sayılır (tekrar onay yürütmeyi RETRY ETMEZ)
   - `tool_name` için kayıtlı yürütücü yoksa → `status=failed`, outcome "yürütücü kayıtlı
     değil" (allowlist, spec §6)
   - `expire_due()` süresi geçen `pending`'leri `expired` yapar, sayı döner; süresi
     geçmemişe dokunmaz; zaten karara bağlanmışa dokunmaz
2. **YEŞİL:** `app/approvals.py`:
   - Sabitler: `COLLECTION="approvals"`, `CLAIMS_COLLECTION="approval_claims"`,
     `STATUS_*`, `MAX_PENDING=50`
   - `request(db, *, user_id, kind, title, detail, tool_name=None, tool_args=None,
     zone, session_id, now_fn=_now, ttl_minutes=None) -> str`
   - `list_pending(db, user_id, now_fn=_now) -> list[dict]`
   - `get(db, approval_id, user_id) -> dict | None`
   - `decide(db, approval_id, user_id, decision, executors, now_fn=_now) -> dict`
     — sıra: oku → sahiplik → status pending mi → **süre kontrolü** → claim `create()`
     (`AlreadyExists` → `{already: True}` + güncel durumu döndür) → status yaz →
     onaysa yürüt
   - `expire_due(db, now_fn=_now) -> dict`
   - `config.py`: `APPROVAL_TTL_MINUTES = int(os.environ.get("JARVIS_APPROVAL_TTL_MINUTES", "60"))`
3. Commit: `feat(approvals): queue core — ownership, claim-guarded idempotent decide, timeout=reject`

## Görev 2 — hatırlatma iptali + kırmızı araç + yürütücü (TDD)

1. **KIRMIZI:** `brain/tests/test_reminders.py`'ye ekle:
   - `reminders.cancel(db, id)` `status=cancelled` yazar, `cancelled_at` damgalar
   - olmayan id → Türkçe hata metni, yazma yok
   - zaten `cancelled` → idempotent ("zaten iptal")
   - `sent` bir hatırlatma iptal edilemez (Türkçe gözlem)
   `brain/tests/test_approvals.py`'ye: `EXECUTORS["cancel_reminder"]` çağrıldığında
   gerçekten `reminders.cancel`'a düştüğü (fake db üzerinden sonuç metni)
2. **YEŞİL:**
   - `app/reminders.py`: `cancel(db, reminder_id) -> str`
   - `app/tools.py`: `cancel_reminder(reminder_id: str) -> str` — Türkçe docstring; gövde
     **savunma amaçlıdır**: kırmızı bölge callback'i zaten önce keser, gövdeye ulaşılırsa
     "bu araç yalnızca onaydan sonra çalışır" döner (spec §6)
   - `app/approvals.py`: `EXECUTORS = {"cancel_reminder": ...}` — kayıt `tools`/`main`
     tarafından enjekte edilir (modül döngüsü olmasın diye `register_executor()`)
   - `config.TOOL_ZONES["cancel_reminder"] = ZONE_RED`
   - `agent.py` INSTRUCTION: kırmızı bir araç onay kartına düşerse Kadir'e "onay kartı
     gönderdim, karar verince yapacağım" de; kartı tekrar tekrar oluşturma
3. Commit: `feat(approvals): cancel_reminder is the first real red tool, executed only on approval`

## Görev 3 — `messages` kind/meta + FCM dispatch (TDD)

1. **KIRMIZI:** `brain/tests/test_messages.py`:
   - `append(..., kind="approval", meta={"approval_id": "a1"})` alanları yazar
   - `history()` `kind`/`meta` **yalnızca doluysa** projeksiyona koyar; eski (alansız)
     satırlar bugünkü üç alanla aynen döner — **geriye dönük uyum pimi**
   `brain/tests/test_fcm.py` (yeni): `dispatch()` token yokken sohbete düşer;
   token varken payload `data` alanlarını taşır; `send_reminder` davranışı **değişmez**
   (mevcut çağrı imzası ve sonucu birebir); `send_approval` `data.approval_id` gönderir
2. **YEŞİL:** `messages.MessageStore.append(..., kind=None, meta=None)`;
   `fcm.dispatch(db, *, title, body, data, fallback_text)` + `send_reminder` /
   `send_approval` sarmalayıcıları
3. Commit: `feat(approvals): chat cards ride the transcript, FCM gains a generic dispatch`

## Görev 4 — politika bağlantısı (TDD)

1. **KIRMIZI:** `brain/tests/test_policy.py`:
   - `approval_sink` **verilmediğinde** kırmızı metin bugünküyle **birebir aynı**
     (mevcut testler zaten bunu tutuyor; regresyon pimi olarak açıkça yazılır)
   - verildiğinde kırmızı dalda çağrılır, dönen metin modele gider, araç **çalışmaz**
   - sink fırlatırsa eski metne düşülür ve araç yine **çalışmaz** (fail-closed pimi)
   - sarı/yeşil dallarda sink **hiç çağrılmaz**
   - audit satırı yine yazılır (`decision="block"`)
2. **YEŞİL:** `policy.make_policy_callback(audit, trust_provider=None, approval_sink=None)`
3. Commit: `feat(approvals): red-zone blocks become approval requests, fail-closed`

## Görev 5 — uçlar + sink kurulumu (TDD)

1. **KIRMIZI:** `brain/tests/test_approvals_api.py`:
   - beş ucun auth'u (token yok → 401; scheduler ucu yanlış SA → 403)
   - `GET /api/approvals` bekleyenleri döner
   - başkasının onayı → 404 (403 değil — varlık sızmasın)
   - `approve` → `{status, outcome}`; ikinci çağrı `already: true`
   - `reject` → yürütme yok
   - `approvals-tick` → süresi geçenlerin sayısı
   - sink: kırmızı araç çağrısı → `approvals` dokümanı + `messages`'ta
     `kind="approval"` satırı + FCM çağrısı (enjekte sahte)
2. **YEŞİL:** `main.py`: `_approval_sink()` fabrikası (`_init()` sonrası `_memory.db`,
   `_messages`, `fcm.send_approval` ile), `build_agent(..., approval_sink=...)` bağlantısı
   (**yalnızca metin + ses runner'ı; `guest_gate` DEĞİL** — misafirler kırmızıya asla,
   §4.9), beş uç
3. Commit: `feat(approvals): five endpoints and the sink that turns a red block into a card`

## Görev 6 — Android wire + repository (TDD, JVM)

1. **KIRMIZI:** `ChatMessageWire`'a opsiyonel `kind`/`meta`; bilinmeyen `kind` düz metne
   düşer (hoşgörülü-wire pimi). `ApprovalApi` (list/get/approve/reject) MockWebServer ile
   **teldeki baytlar** seviyesinde (3d-3 dersi: mutasyon ancak üretimin gerçekten
   kullandığı nesneyi ölçtüğünde taşıyıcıdır).
2. **YEŞİL:** `data/net/ApprovalApi.kt`, wire modelleri, `data/approvals/ApprovalRepository.kt`
3. Commit: `feat(android): approval wire + repository`

## Görev 7 — Android onay kartı + kuyruk senkronu (TDD, JVM + instrumented)

1. **KIRMIZI:** ViewModel testleri — açılışta kuyruk senkronu; karar **iyimser güncelleme
   YAPMAZ**, sunucunun döndürdüğü duruma geçer; hata Türkçe mesaja düşer; karar sırasında
   düğmeler devre dışı ve **devre dışı GÖRÜNÜR** (10296e1 dersi)
2. **YEŞİL:** `ui/chat/ApprovalCard.kt` + ChatViewModel bağlantısı + FCM `data.approval_id`
   ile açılış
3. Commit: `feat(android): approval cards in the chat stream`

## Görev 8 — belge + deploy notu (kod yok)

`brain/README.md`'ye "Onay merkezi (Faz Y3)" bölümü; `brain/deploy/scheduler-jobs.md`'ye
`approvals-tick` job tanımı **komut olarak, çalıştırılmadan**. Deploy Kadir'in onayıyla,
tek elden.

Commit: `docs(approvals): operator notes + approvals-tick scheduler definition`

---

## Bu dilimin en olası üç kırılma noktası

1. **Zaman aşımının yalnızca süpürücüde uygulanması.** En kolay yapılacak hata; sonucu
   süresi dolmuş bir kırmızı eylemin çalışması. Görev 1'in "süresi geçmişi onaylamak"
   testi bunun pimidir.
2. **Çift yürütme.** Push bildirimi + kuyruk senkronu aynı kartı iki kez gönderebilir.
   Claim dokümanı (`create()`) + "yürütücü ikinci kez çağrılmaz" sayaç testi pimidir.
3. **Misafir kapısının sink'i alması.** `guest_gate` de `make_policy_callback` kullanıyor;
   sink oraya da bağlanırsa dış AI'lar kırmızı eylem için onay kartı üretebilir hale gelir
   (§4.9 ihlali). Görev 5 bunu açıkça dışarıda bırakır ve testi yazılır.
