"""Hash-zincirli karar kaydı (Onay Kartı 2.0, Task 4; P5/K3): her nihai onay
kararının — onayla/reddet/süre — bu kullanıcının zincirine TEK bir halka olarak
yazıldığı append-only koleksiyon.

Model (Firestore):
- `decision_log` (doc id = approval_id): {approval_id, user_id, decision, by,
  at, actor, zone, cause, operand, reversible, reason, prev_hash, hash, seq}.

Neden imza değil hash zinciri (P5): tek kullanıcıda imza az şey satın alır;
zincir, bir kararı GERİYE DÖNÜK değiştirmeyi her halka için bir sha256 maliyetine
algılanır kılar. `verify()` bunu kanıta çevirir: zincirde bozuk ilk halkanın
kimliğini döner.

Neden doc id = approval_id: bir onayın yaşamında tam BİR terminal geçişi vardır
(pending -> approved/rejected/expired; claim ya da süre kontrolü ikinci geçişi
keser) ve süreye-takılma yolu bilinçli olarak claim YAZMAZ (pinli test). Yarış
kanalının kapatılmadığı tek yer orasıdır; `create()` atomik "yoksa yaz" olduğu
için approval_id'yi anahtar yapmak, hangi yolun önce ulaştığına bakmaksızın
onay başına EN FAZLA bir halka garantiler — yarış kaybedeni sessizce çıkar.
Bu yüzden `append()` çift-yazmada yeni hash yerine "" döner.

Neden zincir `prev_hash` üzerinden gezilir, `at` sıralamasıyla değil: karar
zaman damgaları ISO metinlerdir ve testler saati DONdurabilir — aynı saniyede
yazılan iki halka leksikografik sıralamada keyfi dizilir ve dokunulmamış zincir
yanlışlıkla "bozuk" sayılırdı. Doğru sıra zincirin İÇİNDEDIR: prev_hash'i ""
olan tek kök bulunur, sonra her adımda prev_hash'i bir öncekinin hash'ine eşit
halka izlenir. Ulaşılamayan halka kalmışsa zincir orada kırıktır.

Yazım anında "önceki halka" seçimi için halkalar kullanıcı-başına monotonik bir
`seq` taşır (en büyük seq + 1). Sıralamayı `at`'e bırakmak aynı saniyede yazılan
halkalarda yanlış uca bağlayabilirdi; `seq` yalnızca bu seçimi belirleyici kılar,
doğrulama yine tamamen prev_hash gezintisidir.

Dürüst sınır (belgenin kendisinde yazılması istendi): zincir **kurcalanmayı**
kanıtlar, **yazarlığı** kanıtlamaz — Firestore'a yazabilen herkes verilen bir
noktadan itibaren tüm zinciri yeniden kurabilir. Koleksiyonu ajan-yazılmez kılan
Firestore rules (K3'ün öteki yarısı) BU GÖREVDE DEĞİLDİR ve burada uygulanmış
gibi gösterilmez; canlı projeye karşı ayrıca doğrulanması gereken bir rules/IAM
işidir.

`reason` alanı yalnızca ret kararlarında doludur; `by` sürenin kararı için
None'dır ("kararı kimse vermedi, süre verdi" — approvals invariant 1 ile aynı
sözleşme).

İki bilinçli sınır daha: (1) halkalar KARARLARI kaydeder, yürütme SONUÇLARINI
değil — yürütücü sonradan patlarsa doküman `failed` olurken halka `approved`
kalır ("karar VERİLMİŞ sayılır" ilkesiyle tutarlıdır). (2) Aynı kullanıcının
FARKLI onaylarına gelen eşzamanlı terminal geçişleri aynı prev_hash'i okuyup
zinciri fork'layabilir; tek-kullanıcı üründe pencere milisaniyelerdir ama
sıfır değildir — kalıcı çözüm transaction'lı append ya da Firestore rules'dır
ve burada değildir.

TODO(debt): OCSF 6003 alan-adlandırması bu koleksiyonda YOK — alan adları bu
modülün kendisidir. 5 Ağu ekosistem analizi OCSF'i audit şemasının isimlendirme
referansı sayıyordu; eşleme schema.ocsf.io'dan DOĞRULANMIŞ sınıf tanımıyla
yapılmalı (çevrimiçi doğrulama 22-23 Ağu oturumunda yapılamadı: arama kotası
bitik + şema sitesi JS-only; uydurma eşleme yazılmadı). Koleksiyon gençken
yeniden adlandırma bedava; hash zinciri alan adlarını kapsadığı için SONRADAN
yeniden adlandırma eski halkaların hash'ini kırar (verify bozuk raporlar) —
o noktadan sonra tek yol bir mapping tablosudur.
"""
import hashlib
import json
import logging

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "decision_log"


def _canonical(entry: dict) -> bytes:
    """Hash'in ÜZERİNDEN geçen kanonik serileştirme: sıralı anahtarlar, açık
    ayraçlar, UTF-8. Bir hash'in `str(dict)` üzerine kurulması, Python'un repr'i
    değiştiği gün sessizce çürür — bu yüzden serileştirme burada sabitlenmiştir."""
    return json.dumps(entry, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _entry_hash(entry_without_hash: dict) -> str:
    return hashlib.sha256(_canonical(entry_without_hash)).hexdigest()


def _stream_user(db, user_id: str):
    return db.collection(COLLECTION).where(
        filter=FieldFilter("user_id", "==", user_id)).stream()


def append(db, *, approval_id: str, user_id: str, decision: str, by: str | None,
           at: str, actor: str | None = None, zone: str | None = None,
           cause: str | None = None, operand: str | None = None,
           reversible: bool | None = None,
           reason: str | None = None) -> str:
    """Bir karar halkasını yazar, yeni hash'i döner (çift-yazmada "").

    Alanlar çağıran tarafından taşınır — bu fonksiyon hiçbir bağlamı HESAPLAMAZ,
    yalnızca kaydeder (Task 2'nin "beyan et, tahmin etme" kuralının aynısı).

    Önceki halka, `seq`'i en büyük olan halkadır (kullanıcı-başına monotonik
    sayaç; bkz. modül docstring). Yeni halka max(seq)+1 alır."""
    latest_seq = -1
    prev_hash = ""
    for snap in _stream_user(db, user_id):
        d = snap.to_dict()
        cand_seq = d.get("seq")
        if not isinstance(cand_seq, int) or cand_seq <= latest_seq:
            continue
        latest_seq = cand_seq
        prev_hash = d.get("hash") or ""

    entry = {
        "approval_id": approval_id,
        "user_id": user_id,
        "decision": decision,
        "by": by,
        "at": at,
        "actor": actor,
        "zone": zone,
        "cause": cause,
        "operand": operand,
        "reversible": reversible,
        "reason": reason,
        "prev_hash": prev_hash,
        "seq": latest_seq + 1,
    }
    entry["hash"] = _entry_hash(entry)
    try:
        db.collection(COLLECTION).document(approval_id).create(entry)
    except AlreadyExists:
        logging.info("decision_log: halka zaten var approval=%s (yarış kaybedildi)",
                     approval_id)
        return ""
    logging.info("decision_log: halka yazıldı approval=%s decision=%s by=%s",
                 approval_id, decision, by)
    return entry["hash"]


def verify(db, user_id: str) -> tuple[bool, str | None]:
    """Zinciri kökten sona gezdirir: (True, None) ya da (False, ilk bozuk halka).

    Bozukluk üç biçimde olabilir ve hepsi aynı şekilde raporlanır: (a) halkanın
    kendi hash'i alanlarıyla tutmuyor (içerik kurcalanmış), (b) prev_hash'i
    işaret ettiği halka yok ya da zincire giren birden çok kök var (halka
    koparılmış / fork), (c) kökten başlayan yürüyüşe katılamayan artık halkalar
    var. Rapor edilen kimlik halkanın approval_id'sidir."""
    entries = [snap.to_dict() for snap in _stream_user(db, user_id)]
    if not entries:
        return True, None

    def _key(e: dict) -> tuple[str, str]:
        # Rapor sırasını saat eşitliğinde bile belirleyici yapmak için:
        return (e.get("at") or "", e.get("approval_id") or "")

    def _broken(e: dict) -> tuple[bool, str | None]:
        return False, e.get("approval_id") or "<bilinmeyen>"

    roots = [e for e in entries if e.get("prev_hash") == ""]
    if len(roots) != 1:
        return _broken(min(entries, key=_key))

    by_prev: dict[str, dict] = {}
    for e in entries:
        by_prev.setdefault(e.get("prev_hash") or "", e)

    walked_hashes: set[str] = set()
    cur = roots[0]
    while True:
        stored = cur.get("hash")
        body = {k: v for k, v in cur.items() if k != "hash"}
        if not isinstance(stored, str) or _entry_hash(body) != stored:
            return _broken(cur)
        if stored in walked_hashes:
            return _broken(cur)  # döngü: zincir kendine bağlandı
        walked_hashes.add(stored)
        nxt = by_prev.get(stored)
        if nxt is None or nxt is cur:
            break
        cur = nxt

    if len(walked_hashes) != len(entries):
        unreachable = [e for e in entries if e.get("hash") not in walked_hashes]
        return _broken(min(unreachable, key=_key))
    return True, None
