"""Onay merkezi (North Star §4.8/§9, Faz Y3): kırmızı bölge eylemleri Kadir'in
kararını bekleyen bir kuyruğa düşer, karar anında çalışır.

Bugüne kadar kırmızı bölge bir çıkmaz sokaktı: policy callback aracı reddediyor,
model Kadir'e soruyor, Kadir "evet" diyor ve hiçbir şey olmuyordu — "onay"
kavramının sistemde bir temsili yoktu. Bu modül o temsili kurar.

Model (Firestore):
- `approvals` (auto-id): {user_id, kind, title, detail, tool_name, tool_args,
  zone, session_id, status, created_at, expires_at, decided_at, decided_by,
  outcome}. status: pending|approved|rejected|expired|failed.
- `approval_claims` (doc id = onay id): {decision, by, at}. Kararın BİRİNCİL
  kaydı budur; onay dokümanındaki `status` onun izdüşümüdür.

Üç değişmez bu modülün varlık sebebidir:

1. **Zaman aşımı = reddet ve KARAR anında uygulanır** (spec §4.1). Süpürücü iş
   (`expire_due`) tek başına yeterli değildir: süpürme ile son kullanma arasındaki
   pencerede gelen bir onay, süresi geçmiş kırmızı bir eylemi çalıştırırdı.
   `decide()` bu yüzden yürütücüden ÖNCE süreyi kontrol eder. Süpürücü bir
   temizlik/bildirim yoludur, güvenlik sınırı değil.
2. **Karar idempotenttir** (spec §4.2). Firestore'da işlemsiz koşullu güncelleme
   yoktur, ama `DocumentReference.create()` atomik bir "yoksa yaz"dır ve
   `AlreadyExists` fırlatır (aynı desen: conversations.py başlık yarışı). Çift
   dokunuş — push bildirimi + kuyruk senkronu aynı anda — kırmızı bir eylemi iki
   kez çalıştıramaz.
3. **Yürütme bir allowlist'tir** (spec §6). `executors` sözlüğünde kayıtlı
   olmayan bir yürütücü ANAHTARI ÇALIŞMAZ; onay kaydı, keyfi bir isim yazarak
   rastgele kod çalıştırmanın yolu değildir. Anahtar `kind`'a göre seçilir
   (Faz Y4, `_executor_key`): `tool_call` -> `tool_name`, `tool_grant` ->
   isim-uzaylı sabit `EXECUTOR_TOOL_GRANT`, `agent_grant` (Fabrika Kademe 2) ->
   isim-uzaylı sabit `EXECUTOR_AGENT_GRANT`.

Zaman karşılaştırmaları (süre doldu mu) bilinçli olarak Python tarafındadır,
Firestore sorgusunda değil: `status == pending` eşitlik filtresi + `expires_at`
aralık filtresi bir composite index ister; kuyruk zaten MAX_PENDING ile sınırlı
olduğu için karşılaştırmayı burada yapmak hem indekssiz hem de daha okunur.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from . import config

COLLECTION = "approvals"
CLAIMS_COLLECTION = "approval_claims"

KIND_TOOL_CALL = "tool_call"
# Faz Y4.1 (§8.5): "eksik yeteneği kendisi tespit eder... öneri onay merkezine
# düşer, Kadir'in tek tık onayıyla araç kayıt defterine girer". Bu tür bir
# onayın yürütülmesi = tool_registry.grant().
KIND_TOOL_GRANT = "tool_grant"

# Fabrika Kademe 2 (§8.5): "öneri onay merkezine düşer; Kadir onaylarsa kayıt
# defterine kalıcı ajan olarak yazılır". Yürütülmesi = agent_registry.grant().
KIND_AGENT_GRANT = "agent_grant"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"
# Sadece decide()'ın DÖNÜŞ değerinde geçer, dokümanda asla: sahibi olmayan ve hiç
# var olmayan onay BİREBİR aynı cevabı alır, böylece uç 404 verirken başkasının
# onayının varlığı bile sızmaz (spec §4.4 — 403 değil, 404).
STATUS_NOT_FOUND = "not_found"

DECISIONS = (STATUS_APPROVED, STATUS_REJECTED)
MAX_PENDING = 50

# `cause` slugs (Onay Kartı 2.0, Task 2): short machine-readable reasons an
# approval was demanded, stored on the record's `cause` field (see
# `request()` below) -- never a Turkish sentence, that stays in `detail`.
# Declared here, beside KIND_*/STATUS_*, rather than split across the modules
# that produce them (main.py, tools.py): Task 4's decision ledger and Task 6's
# slug -> Turkish-phrase map both need this vocabulary in one place.
CAUSE_RED_ZONE = "red_zone"              # policy._decide[_voice] returned "block"
CAUSE_CAPABILITY_REQUEST = "capability_request"  # tool_grant/agent_grant proposal


@dataclass(frozen=True)
class RejectReason:
    """One preset rejection reason served to the client's reject picker
    (Onay Kartı 2.0, Task 3, P2c).

    `title` is the short Turkish label a picker button shows. `prompt_fill`
    is a DIFFERENT, longer Turkish clause -- the text a client is expected to
    send back as the actual `reason` when this preset is chosen, because it
    has to read as a full sentence once substituted into
    `main.REJECTION_MODEL_NOTICE`'s "... çağrısını reddetti: {reason}" slot,
    which a bare noun-phrase title does not."""

    id: str
    title: str
    prompt_fill: str


# Starting set (Task 3 brief, Step 4). Free text is also accepted by the
# reject route -- these are picker shortcuts, not the only allowed values.
REJECT_REASONS: tuple[RejectReason, ...] = (
    RejectReason("wrong_target", "Yanlış kişi/hedef",
                "yanlış kişi ya da hedef seçilmiş"),
    RejectReason("not_now", "Şimdi olmaz, sonra",
                "şimdi uygun değil, daha sonra tekrar denenebilir"),
    RejectReason("no_share", "Bu bilgiyi paylaşma",
                "bu bilgi paylaşılmamalı"),
    RejectReason("different_approach", "Farklı yap (açıklayacağım)",
                "istenen farklı; Kadir ayrıntıyı kendisi anlatacak"),
)

EXPIRED_OUTCOME = "Onay süresi doldu; eylem çalıştırılmadı."
NO_EXECUTOR_OUTCOME = "bu araç onaydan sonra çalıştırılamıyor (yürütücü kayıtlı değil)"

# (tool_args, user_id) -> Türkçe sonuç metni
Executor = Callable[[dict, str], str]

# `kind=tool_grant` onaylarının yürütücü anahtarı. "kind:" ön eki bir İSİM
# UZAYI ayracıdır, süs değil: `tool_call` onayları yürütücüyü `tool_name` ile
# seçer ve araç adları Python tanımlayıcısıdır — iki nokta içeremez. Böylece
# hiçbir araç çağrısı, adını uydurup kayıt defterine yazan yürütücüyü
# çağıramaz. _executor_key ayrıca bu ön eki taşıyan tool_name'leri açıkça
# reddeder (kuşak + kemer).
EXECUTOR_TOOL_GRANT = "kind:tool_grant"

EXECUTOR_AGENT_GRANT = "kind:agent_grant"

_EXECUTOR_KIND_PREFIX = "kind:"

# Onaylandığında bir yürütücü koşan türler. Bu kümede OLMAYAN bir tür onaylanır
# ama yan etkisi yoktur — Y3'ün "yürütülebilir tek tür tool_call" davranışının
# genelleştirilmiş hâli. (§8.5'in agent_spec öngörüsü Kademe 2 ile dolduruldu.)
EXECUTABLE_KINDS = (KIND_TOOL_CALL, KIND_TOOL_GRANT, KIND_AGENT_GRANT)

# Onaydan sonra çalıştırılabilecek araçların ALLOWLIST'i (spec §6). Bu modül
# `tools`'u BİLEREK import etmez: kayıt ters yönde, `tools.py`'nin sonunda
# yapılır (app/tools.py: register_executor("cancel_reminder", ...)). Aksi hâlde
# approvals -> tools -> reminders/policy -> approvals döngüsü kurulurdu ve bu
# modül test edilemez hâle gelirdi.
EXECUTORS: dict[str, Executor] = {}


def register_executor(name: str, fn: Executor) -> None:
    """Bir aracı onay-sonrası yürütülebilir olarak kaydeder.

    İsim `approvals` dokümanındaki `tool_name` ile aynı olmalıdır; kayıtlı
    olmayan isim decide() içinde `failed` olur (allowlist)."""
    EXECUTORS[name] = fn
    logging.info("approvals: yürütücü kaydedildi tool=%s", name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str) -> datetime | None:
    """ISO-8601 string'ini UTC datetime'a çevirir; okunamazsa None."""
    if not isinstance(iso_str, str):
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_expired(doc: dict, parsed_now: datetime) -> bool:
    """Süresi doldu mu. Okunamayan/eksik `expires_at` DOLMUŞ sayılır: fail-closed
    — bozuk bir damga kırmızı bir eylemi süresiz onaylanabilir bırakmasın."""
    parsed_expires = _parse_iso(doc.get("expires_at"))
    if parsed_expires is None:
        return True
    return parsed_expires <= parsed_now


def _project(approval_id: str, d: dict) -> dict:
    """Onay dokümanının okuyucuya (uç, kart, test) verilen biçimi.

    The decision context (actor/trust_level/cause/operand/reversible, Task 2)
    is read with `.get()`: documents written BEFORE this change have none of
    these keys at all -- `.get()` returns None on absence, it does not
    invent a default (deliberate, so old records keep reading)."""
    return {
        "id": approval_id,
        "user_id": d.get("user_id"),
        "kind": d.get("kind"),
        "title": d.get("title"),
        "detail": d.get("detail"),
        "tool_name": d.get("tool_name"),
        "tool_args": d.get("tool_args") or {},
        "zone": d.get("zone"),
        "session_id": d.get("session_id"),
        "status": d.get("status"),
        "created_at": d.get("created_at"),
        "expires_at": d.get("expires_at"),
        "decided_at": d.get("decided_at"),
        "decided_by": d.get("decided_by"),
        "outcome": d.get("outcome"),
        # Task 3 (Onay Kartı 2.0): only rejections carry this key at all (see
        # decide() below) -- .get() reports it as None for every approved
        # decision and for every document written before this change, same
        # absence-not-sentinel contract as actor/trust_level/cause/... below.
        "decision_reason": d.get("decision_reason"),
        "actor": d.get("actor"),
        "trust_level": d.get("trust_level"),
        "cause": d.get("cause"),
        "operand": d.get("operand"),
        "reversible": d.get("reversible"),
    }


def _result(status: str, outcome: str | None, already: bool) -> dict:
    return {"status": status, "outcome": outcome, "already": already}


def _executor_key(doc: dict) -> str | None:
    """Yürütülebilir bir onayın yürütücü anahtarı; çözülemezse None (-> failed).

    Yalnızca EXECUTABLE_KINDS için çağrılır. `tool_call` Y3'teki davranışını
    birebir korur: anahtar `tool_name`'in kendisidir. `tool_grant` ve
    `agent_grant` sabit, isim-uzaylı birer anahtar kullanır (sırasıyla
    EXECUTOR_TOOL_GRANT, EXECUTOR_AGENT_GRANT).

    `tool_call` dalındaki `kind:` reddi, isim uzayını sızdırmaz kılar: kendi
    adını `kind:tool_grant` diye bildiren bir araç, kayıt defterine yazan
    yürütücüyü ödünç alamaz — anahtar çözülmez, onay `failed` olur."""
    kind = doc.get("kind")
    if kind == KIND_TOOL_GRANT:
        return EXECUTOR_TOOL_GRANT
    if kind == KIND_AGENT_GRANT:
        return EXECUTOR_AGENT_GRANT
    tool_name = doc.get("tool_name")
    if isinstance(tool_name, str) and tool_name.startswith(_EXECUTOR_KIND_PREFIX):
        logging.warning("approvals: tool_call '%s' isim uzayını ihlal ediyor -- reddedildi",
                        tool_name)
        return None
    return tool_name


def _normalize_args(tool_args: dict | None) -> dict:
    """`tool_args`'ın kayda YAZILAN biçimi: değerler stringify edilip 500
    karakterde kesilir — policy.write_audit ile AYNI kural (kayıt yeniden kurmak
    içindir, tam yük dökümü için değil).

    Ayrı bir fonksiyon, çünkü mükerrer kart araması (find_pending_duplicate)
    kayıtlı biçimle karşılaştırmak zorunda: ham argümanla karşılaştırsaydı 500
    karakteri aşan her istek kendi kartını asla mükerrer bulamazdı."""
    return {k: str(v)[:500] for k, v in (tool_args or {}).items()}


def request(db, *, user_id: str, kind: str, title: str, detail: str,
            tool_name: str | None = None, tool_args: dict | None = None,
            zone: str, session_id: str, now_fn=_now,
            ttl_minutes: int | None = None, doc_id: str | None = None,
            actor: str | None = None, trust_level: str | None = None,
            cause: str | None = None, operand: str | None = None,
            reversible: bool | None = None) -> str:
    """Bekleyen bir onay kaydı kurar, onay id'sini döner.

    `tool_args` değerleri stringify edilip 500 karakterde kesilir —
    policy.write_audit ile AYNI kural: kayıt yeniden kurmak içindir, tam yük
    dökümü için değil. `ttl_minutes` verilmezse config.APPROVAL_TTL_MINUTES.

    `doc_id` verilirse onay O kimlikle (atomik `create()`) yazılır; verilmezse
    Firestore auto-id (`add()`) — kırmızı bölge yolunun (main._approval_sink)
    Y3'ten beri kullandığı davranış, harfi harfine korunur. Çağıranın id'yi
    ÖNCEDEN bilmesi Y4'ün ihtiyacı: `tool_grant` onaylarının `tool_args`'ı,
    kaydı doğuran onayın kimliğini taşımak zorunda (spec §3, izlenebilirlik) ve
    yürütücü sözleşmesi `(tool_args, user_id)` — yürütücü onay id'sini başka
    hiçbir yerden göremez. Alternatif (id öğrenildikten sonra dokümana geri
    yazmak) ikinci bir yazma ve yarış penceresi demekti.

    `actor`/`trust_level`/`cause`/`operand`/`reversible` (Task 2, Onay Kartı
    2.0) are the context that PRODUCED the decision -- this function does not
    COMPUTE them, it only carries what the caller (the policy callback, or a
    capability-proposal path) already computed. All default to None so every
    caller predating this change keeps working unchanged. Only the fields
    ACTUALLY given (not None) are written to the document -- a missing field
    is the ABSENCE of the key, not a `None` sentinel value; that is what lets
    documents written before this change be told apart from new ones by
    absence rather than a fabricated default (`_project` reads it back with
    `.get()`)."""
    if not user_id or not isinstance(user_id, str):
        raise ValueError("onay için user_id gerekli")
    if not title or not isinstance(title, str):
        raise ValueError("onay için title gerekli")
    if kind == KIND_TOOL_CALL and not tool_name:
        raise ValueError("kind=tool_call için tool_name zorunlu")

    now = now_fn()
    parsed_now = _parse_iso(now)
    if parsed_now is None:
        raise ValueError(f"geçersiz zaman damgası: {now!r}")
    if ttl_minutes is None:
        ttl_minutes = config.APPROVAL_TTL_MINUTES

    doc = {
        "user_id": user_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "tool_name": tool_name,
        "tool_args": _normalize_args(tool_args),
        "zone": zone,
        "session_id": session_id,
        "status": STATUS_PENDING,
        "created_at": now,
        "expires_at": (parsed_now + timedelta(minutes=ttl_minutes)).isoformat(),
        "decided_at": None,
        "decided_by": None,
        "outcome": None,
    }
    # Decision context: only fields ACTUALLY given (not None) get written --
    # see docstring. A fixed `None` sentinel would land on every caller's
    # document too and destroy the absence signal that tells old and new
    # documents apart.
    context = {"actor": actor, "trust_level": trust_level, "cause": cause,
               "operand": operand, "reversible": reversible}
    doc.update({k: v for k, v in context.items() if v is not None})
    if doc_id:
        ref = db.collection(COLLECTION).document(doc_id)
        ref.create(doc)          # AlreadyExists çağırana taşınır: aynı id iki kez yazılamaz
    else:
        _, ref = db.collection(COLLECTION).add(doc)
    logging.info("approvals: request id=%s user=%s kind=%s tool=%s ttl=%s dk",
                 ref.id, user_id, kind, tool_name, ttl_minutes)
    return ref.id


def list_pending(db, user_id: str, now_fn=_now) -> list[dict]:
    """Bu kullanıcının bekleyen onayları, yeniden eskiye, en fazla MAX_PENDING.

    Süresi geçmişler süpürücü henüz koşmamış olsa bile DÖNMEZ (§4.1: zaman aşımı
    süpürücüye bağlı değildir). Sıralama ve süre elemesi Python tarafındadır —
    modül docstring'indeki composite-index gerekçesi."""
    parsed_now = _parse_iso(now_fn())
    snaps = (
        db.collection(COLLECTION)
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    items = [
        _project(snap.reference.id, snap.to_dict())
        for snap in snaps
        if not _is_expired(snap.to_dict(), parsed_now)
    ]
    items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    return items[:MAX_PENDING]


def find_pending_duplicate(db, user_id: str, tool_name: str,
                           tool_args: dict | None, now_fn=_now,
                           kind: str = KIND_TOOL_CALL) -> str | None:
    """Aynı isteğin ZATEN bekleyen bir kartı varsa onun id'si, yoksa None.

    Neden var: kırmızı bölge engeli her turda yeniden tetiklenir. Ajan talimatı
    "kartı tekrar oluşturma" diye RİCA eder, ama bu bir garanti değildir — model
    döngüye girerse her tur bir onay dokümanı, bir transcript satırı ve bir push
    üretirdi. Bir kartın zaten beklediği bir istek için ikincisini kurmak hiçbir
    şey kazandırmaz: Kadir'in vereceği karar aynı karardır.

    Süresi geçmiş kartlar mükerrer SAYILMAZ (list_pending ile aynı kural): süresi
    dolmuş bir istek yeniden sorulabilir olmalıdır."""
    normalized = _normalize_args(tool_args)
    parsed_now = _parse_iso(now_fn())
    snaps = (
        db.collection(COLLECTION)
        .where(filter=FieldFilter("user_id", "==", user_id))
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    for snap in snaps:
        doc = snap.to_dict()
        if doc.get("kind") != kind:
            continue
        if doc.get("tool_name") != tool_name:
            continue
        # tool_grant önerilerinde argümanlar (gerekçe, mcp url'i) öneriden
        # öneriye değişebilir ama mükerrerliği belirleyen ARAÇ ADIdır; çağıran
        # bu yüzden {} geçer ve o durumda argüman karşılaştırması atlanır.
        if normalized and doc.get("tool_args") != normalized:
            continue
        if _is_expired(doc, parsed_now):
            continue
        return snap.reference.id
    return None


def get(db, approval_id: str, user_id: str) -> dict | None:
    """Tek onayın güncel hali. Başkasının onayı YOK sayılır (spec §4.4):
    çağıran 404 verir, 403 değil — varlığı bile sızmasın."""
    snap = db.collection(COLLECTION).document(approval_id).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    if d.get("user_id") != user_id:
        logging.warning("approvals: get sahiplik reddi id=%s isteyen=%s", approval_id, user_id)
        return None
    return _project(approval_id, d)


def decide(db, approval_id: str, user_id: str, decision: str, *,
           reason: str | None = None,
           executors: dict[str, Executor] | None = None, now_fn=_now) -> dict:
    """Bir onayı karara bağlar ve onaysa eylemi ÇALIŞTIRIR.

    Dönen dict: {status, outcome, already}. `already=True`, bu çağrının kararı
    vermediği (başkası verdi / süre verdi) anlamına gelir; yürütücü çağrılmaz.

    `executors` VERİLMEZSE modül kayıt defteri (EXECUTORS) kullanılır — üretim
    yolu budur. Açıkça `{}` geçmek "hiçbir yürütücü yok" demektir ve kayıt
    defterine SESSİZCE düşmez: testlerin izolasyonu buna bağlıdır (None ile {}
    ayrımı bilinçlidir).

    `reason` (Onay Kartı 2.0, Task 3, P2a): MANDATORY when `decision ==
    STATUS_REJECTED` -- a bare rejection is a dead end for the model, it
    learns nothing and just retries the same call. Enforced HERE, at this
    function's own boundary, not only in main.py's route: a future caller
    (a job, a CLI, another route) cannot reject without leaving a reason
    behind. Checked immediately, beside the `decision not in DECISIONS`
    guard -- i.e. BEFORE the Firestore read, let alone the claim create()
    below -- so a malformed call fails before it can consume the claim's
    idempotency slot for a real rejection later. Blank-after-strip counts as
    missing, same rule as config.operand_of's own blank check.

    Sıra sözleşmedir, gevşetilemez:
    oku → sahiplik → status pending mi → SÜRE → claim create() → status yaz →
    onaysa yürüt. Süre kontrolü claim'den önce gelir ki süresi geçmiş bir onay
    claim bile üretmesin; yürütme en sonda gelir ki karar kaydı her hâlükârda
    Firestore'a işlenmiş olsun.
    """
    if decision not in DECISIONS:
        raise ValueError(f"geçersiz karar: {decision!r} (beklenen: {DECISIONS})")
    if decision == STATUS_REJECTED:
        reason = reason.strip() if isinstance(reason, str) else reason
        if not reason:
            raise ValueError("ret için gerekçe zorunlu (reason boş/eksik olamaz)")
    if executors is None:
        executors = EXECUTORS

    ref = db.collection(COLLECTION).document(approval_id)
    snap = ref.get()
    if not snap.exists:
        return _result(STATUS_NOT_FOUND, None, False)
    d = snap.to_dict()
    if d.get("user_id") != user_id:
        logging.warning("approvals: decide sahiplik reddi id=%s isteyen=%s",
                        approval_id, user_id)
        return _result(STATUS_NOT_FOUND, None, False)

    if d.get("status") != STATUS_PENDING:
        return _result(d.get("status"), d.get("outcome"), True)

    now = now_fn()
    parsed_now = _parse_iso(now)
    if _is_expired(d, parsed_now):
        # §4.1: zaman aşımı KARAR anında da uygulanır. decided_by yazılmaz —
        # kararı kimse vermedi, süre verdi.
        ref.set({"status": STATUS_EXPIRED, "decided_at": now,
                 "outcome": EXPIRED_OUTCOME}, merge=True)
        logging.info("approvals: decide süresi dolmuş id=%s (yürütme yok)", approval_id)
        return _result(STATUS_EXPIRED, EXPIRED_OUTCOME, True)

    claim = {"decision": decision, "by": user_id, "at": now}
    doc_update = {"status": decision, "decided_at": now, "decided_by": user_id}
    if decision == STATUS_REJECTED:
        # Stored on BOTH records (brief's explicit requirement): the claim is
        # the decision's primary record (module docstring, invariant 2), the
        # approval doc is what _project()/GET routes read back.
        claim["decision_reason"] = reason
        doc_update["decision_reason"] = reason

    try:
        db.collection(CLAIMS_COLLECTION).document(approval_id).create(claim)
    except AlreadyExists:
        # Yarışı kaybettik: kararı başka bir dokunuş verdi. Tek gerçek kaynak
        # onay dokümanıdır — yeniden okunur (kazanan status'ü henüz yazmamış
        # olabilir; o zaman hâlâ pending görünür, karar yine de bizim değildir).
        current = ref.get().to_dict() or {}
        logging.info("approvals: decide claim yarışı kaybedildi id=%s", approval_id)
        return _result(current.get("status"), current.get("outcome"), True)

    ref.set(doc_update, merge=True)

    if decision == STATUS_REJECTED:
        logging.info("approvals: reddedildi id=%s by=%s reason=%r", approval_id, user_id, reason)
        return _result(STATUS_REJECTED, None, False)

    if d.get("kind") not in EXECUTABLE_KINDS:
        # Yürütücüsü olmayan türler (§8.5: agent_spec) onaylanır ama burada bir
        # yan etkileri yoktur.
        logging.info("approvals: onaylandı id=%s kind=%s (yürütme yok)",
                     approval_id, d.get("kind"))
        return _result(STATUS_APPROVED, None, False)

    # Yürütücü seçimi `kind`'a göredir (Faz Y4): tool_call -> tool_name,
    # tool_grant -> sabit anahtar. Bkz. _executor_key.
    key = _executor_key(d)
    executor = (executors or {}).get(key) if key else None
    if executor is None:
        # Allowlist (§6): kayıt defterinde olmayan isim ÇALIŞMAZ.
        ref.set({"status": STATUS_FAILED, "outcome": NO_EXECUTOR_OUTCOME}, merge=True)
        logging.warning("approvals: yürütücü yok id=%s kind=%s key=%s",
                        approval_id, d.get("kind"), key)
        return _result(STATUS_FAILED, NO_EXECUTOR_OUTCOME, False)

    try:
        outcome = executor(d.get("tool_args") or {}, user_id)
        status = STATUS_APPROVED
    except Exception as exc:
        # İlke 4 (hata = gözlem): hata modelden de Kadir'den de saklanmaz. Karar
        # VERİLMİŞ sayılır — ikinci bir onay bu eylemi tekrar DENEMEZ (§4.5).
        logging.exception("approvals: yürütme hatası id=%s key=%s", approval_id, key)
        # Ham istisna metni KORUNUR (İlke 4: hata = gözlem — model ve Kadir
        # neyin patladığını görmeli), ama kartta çıplak JVM/gRPC nesri
        # görünmesin diye Türkçe bir çerçeveye alınır.
        outcome = f"Yürütme başarısız ({type(exc).__name__}). Ayrıntı: {exc}"
        status = STATUS_FAILED

    ref.set({"status": status, "outcome": outcome}, merge=True)
    logging.info("approvals: karar id=%s status=%s by=%s", approval_id, status, user_id)
    return _result(status, outcome, False)


def expire_due(db, now_fn=_now) -> dict:
    """Süresi geçmiş `pending` onayları `expired`'a çeker, özet döner.

    Bir TEMİZLİK/bildirim yoludur, güvenlik sınırı DEĞİL: gerçek zaman aşımı
    garantisi decide()'ın süre kontrolündedir (§4.1). Bu iş hiç koşmasa bile
    süresi geçmiş bir onay çalıştırılamaz."""
    now = now_fn()
    parsed_now = _parse_iso(now)
    snaps = list(
        db.collection(COLLECTION)
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .stream()
    )
    expired = 0
    for snap in snaps:
        if not _is_expired(snap.to_dict(), parsed_now):
            continue
        snap.reference.set({"status": STATUS_EXPIRED, "decided_at": now,
                            "outcome": EXPIRED_OUTCOME}, merge=True)
        expired += 1
    summary = f"Onay turu: {len(snaps)} bekleyen onaydan {expired} tanesinin süresi doldu"
    logging.info("approvals: expire_due checked=%d expired=%d", len(snaps), expired)
    return {"expired": expired, "checked": len(snaps), "summary": summary}
