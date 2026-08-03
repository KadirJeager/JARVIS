"""Araç kayıt defteri (North Star §8.5, Faz Y4.1): kazanılmış yetenekler KOD
değil VERİdir.

Y4 öncesinde Jarvis'in araç kümesi derleme anında sabitti: `tools.ALL_TOOLS`
bir Python listesi, `config.TOOL_ZONES` bir sözlük. Yeni bir yetenek kazanmanın
tek yolu bir insanın kod yazıp deploy etmesiydi. Bu modül o kümenin veri tarafını
kurar: onaylanmış her yetenek `tool_registry` koleksiyonunda bir dokümandır.

Model (Firestore, doküman kimliği = araç/sunucu ADI — tekil isim uzayı):
{name, kind: builtin|mcp, zone: green|yellow, status: granted|revoked, why,
 mcp: dict|None, approval_id, granted_at, revoked_at}

Dört değişmez bu modülün nasıl yazıldığını belirler:

1. **Kayıt yazan tek yol onay yürütücüsüdür** (spec §4.1). Bu modül `grant()`
   fonksiyonunu SAĞLAR ama kimseyi çağırmaz; üretimde tek çağıran
   `tools._execute_tool_grant` — yani `approvals.decide(..., "approved")`.
   `propose_tool` buraya YAZMAZ.
2. **Kayıt defteri koddaki bölgeyi GEVŞETEMEZ** (spec §4.2). Bu garanti burada
   değil `policy.check_zone`'da uygulanır (sıra: config.TOOL_ZONES -> kayıt
   defteri -> DEFAULT_ZONE) — ama sebebi burada yazılı: aksi hâlde onaylanmış
   tek bir `{name: "cancel_reminder", zone: "green"}` kaydı onay merkezini
   tamamen baypas ederdi.
3. **Kırmızı asla verilmez** (spec §4.3). `grant()` de doğrular, `propose_tool`
   da: öneri doğrulaması tek savunma hattı değildir.
4. **Revoke geri alınabilirliktir** (spec §4.4): kayıt SİLİNMEZ, `revoked`
   damgalanır — ve aynı ad yeniden kazandırılabilir (`grant()`'in AlreadyExists
   dalı bu yüzden koşulsuz "hayır" demez).

Fonksiyonlar exception FIRLATMAZ, Türkçe gözlem döner (İlke 4): bu modülün
çağıranı ya bir araç gövdesi ya da bir onay yürütücüsüdür; ikisi de bir
hatayı kullanıcıya metin olarak taşır.
"""
import logging
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists

from . import config

COLLECTION = "tool_registry"

KIND_BUILTIN = "builtin"
KIND_MCP = "mcp"
KINDS = (KIND_BUILTIN, KIND_MCP)

STATUS_GRANTED = "granted"
STATUS_REVOKED = "revoked"

# §4.3: önerilebilir/kazandırılabilir bölgeler. Kırmızı bir yetenek hâlâ insan
# eliyle koda girer — üretilmiş yetenek misafir muamelesi görür.
GRANTABLE_ZONES = (config.ZONE_GREEN, config.ZONE_YELLOW)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate(name: str, kind: str, zone: str) -> str | None:
    """Ortak doğrulama: sorun varsa Türkçe gözlem, yoksa None.

    `propose_tool` (öneri anı) ve `grant()` (kazandırma anı) AYNI kuralları
    uygular. İki kez doğrulamak fazlalık değil: öneriyle kazandırma arasında
    onay kartı ve Firestore turu var, ve kayıt defterine yazan yol kendi
    girdisini kendisi doğrulamak zorundadır."""
    if not isinstance(name, str) or not name.strip():
        return "Araç adı boş olamaz."
    if kind not in KINDS:
        return f"Geçersiz araç türü: '{kind}' (beklenen: {' veya '.join(KINDS)})."
    if zone == config.ZONE_RED:
        return ("Kırmızı bölge önerilemez: kazanılan bir yetenek en fazla sarı "
                "olabilir (§8.5). Kırmızı bir yetenek koda insan eliyle girer.")
    if zone not in GRANTABLE_ZONES:
        return f"Geçersiz bölge: '{zone}' (beklenen: {' veya '.join(GRANTABLE_ZONES)})."
    return None


def grant(db, *, name: str, kind: str, zone: str, why: str, approval_id: str,
          mcp: dict | None = None, now_fn=_now) -> str:
    """Bir yeteneği kayıt defterine yazar. ÜRETİMDE TEK ÇAĞIRAN onay
    yürütücüsüdür (spec §4.1) — bu fonksiyonun bir araç gövdesinden
    çağrılması, insan yetkilendirmesini baypas etmek demektir.

    Yazım `create()` ile başlar: Firestore'un atomik "yoksa yaz"ı (aynı desen
    approvals.py'nin claim'i ve conversations.py'nin başlık yarışı). Çakışma
    hâlinde ne yapılacağı MEVCUT kaydın durumuna bağlıdır:
    - `granted` -> DOKUNULMAZ. İkinci bir öneri var olan bir yeteneğin bölgesini
      veya gerekçesini sessizce ezemez.
    - `revoked` -> yeniden kazandırılır (§4.4). Aksi hâlde bir kez iptal edilen
      ad sonsuza dek yakılmış olurdu.
    """
    problem = validate(name, kind, zone)
    if problem:
        logging.warning("tool_registry: grant reddedildi name=%r kind=%r zone=%r -- %s",
                        name, kind, zone, problem)
        return problem

    name = name.strip()
    now = now_fn()
    doc = {
        "name": name,
        "kind": kind,
        "zone": zone,
        "status": STATUS_GRANTED,
        "why": why,
        "mcp": mcp if kind == KIND_MCP else None,
        "approval_id": approval_id,
        "granted_at": now,
        "revoked_at": None,
    }
    ref = db.collection(COLLECTION).document(name)
    try:
        ref.create(doc)
    except AlreadyExists:
        current = ref.get().to_dict() or {}
        if current.get("status") == STATUS_GRANTED:
            logging.info("tool_registry: '%s' zaten kayıtlı -- üzerine yazılmadı", name)
            return f"'{name}' zaten araç kayıt defterinde kayıtlı; kayıt değiştirilmedi."
        # merge=True: dokümanın bilinmeyen/ileride eklenen alanları korunur;
        # revoked_at açıkça temizlenir, yoksa granted bir kayıt iptal damgası
        # taşımaya devam ederdi.
        ref.set(doc, merge=True)
        logging.info("tool_registry: '%s' yeniden kazandırıldı (önceki durum: %s)",
                     name, current.get("status"))

    logging.info("tool_registry: grant name=%s kind=%s zone=%s approval=%s",
                 name, kind, zone, approval_id)
    return (f"'{name}' araç kayıt defterine eklendi (bölge: {zone}). "
            "Bir sonraki açılışta etkin olacak.")


def revoke(db, name: str, *, now_fn=_now) -> str:
    """Bir yeteneği geri alır: kayıt SİLİNMEZ, `revoked` damgalanır (§4.4).

    Silmemek iki şey satın alır: audit izi (§8.5 değişmez 5) okunabilir kalır ve
    hangi yeteneğin ne zaman verilip ne zaman alındığı geri kurulabilir."""
    ref = db.collection(COLLECTION).document(name)
    snap = ref.get()
    if not snap.exists:
        return f"'{name}' araç kayıt defterinde yok."
    ref.set({"status": STATUS_REVOKED, "revoked_at": now_fn()}, merge=True)
    logging.info("tool_registry: revoke name=%s", name)
    return f"'{name}' geri alındı; kayıt iptal edilmiş olarak duruyor."


def get(db, name: str) -> dict | None:
    """Tek kaydın ham hâli (durum farkı gözetmeksizin) veya None."""
    snap = db.collection(COLLECTION).document(name).get()
    return snap.to_dict() if snap.exists else None


def list_granted(db) -> list[dict]:
    """Yalnızca `granted` kayıtlar, ada göre sıralı."""
    rows = [snap.to_dict() for snap in db.collection(COLLECTION).stream()]
    granted = [r for r in rows if r.get("status") == STATUS_GRANTED]
    granted.sort(key=lambda r: r.get("name") or "")
    return granted


def zone_for(db, name: str) -> str | None:
    """Kayıt defterinin bu ad için önerdiği bölge; kayıt yoksa/iptalse None.

    HİÇBİR hata dışarı sızmaz: Firestore erişilemezse None döner, çağıran
    (policy.check_zone) da DEFAULT_ZONE'a (red) düşer. Fail-closed — bir altyapı
    hıçkırığı bir aracı ASLA açmaz, ama ajanı da düşürmez."""
    try:
        doc = get(db, name)
    except Exception:
        logging.exception("tool_registry: zone_for okunamadı name=%s -- red'e düşülüyor", name)
        return None
    if not doc or doc.get("status") != STATUS_GRANTED:
        return None
    return doc.get("zone")


def make_zone_resolver(db):
    """`policy.check_zone`'a enjekte edilen çözücü: (araç adı) -> bölge | None.

    `check_zone` bu yüzden db'siz ve saf kalır; kayıt defterine bağımlılık
    çağrı anında ENJEKTE edilir (üretimde main._init, testlerde doğrudan).
    Çözücü verilmeyen her çağrı — guest_gate dahil — Y4 öncesiyle birebir aynı
    davranır."""
    return lambda name: zone_for(db, name)
