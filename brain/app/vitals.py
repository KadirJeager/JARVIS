"""Vitals sayaçları ve `check_my_vitals` verisi (North Star §4.5, Faz Y1).

Tek Firestore dokümanı (`vitals/counters`) üç günlük sayaç tutar:
chat turu (main.run_turn), ses turu (voice._serve_turn), araç çağrısı
(policy allow kararları). Gün UTC bazlı: `last_reset` alanı bugünden farklıysa
sayaçlar sıfırlanıp yeniden yazılır (merge) — ayrı bir reset job'u yoktur;
sıfırlama ilk yazım sırasında kendiliğinden olur.

Her yazım "best effort" ilkesiyle çağrılır (hata = gözlem): sayaç yazımı
başarısız olursa ana akış ASLA bozulmaz — çağıran taraf try/except ile loglayıp
geçer (main.run_turn'deki conversations.touch deseninin aynısı).

`aylik_harcama` bilinçli None döner: 30 Temmuz geçişiyle metin yolu yerel
LLM proxy'sine (CLIProxyAPI, abonelik) taşındı; faturalı API çağrısı yok,
dolandırılacak kota da yok. Alan, faturalandırma verisi tekrar anlamlı
olursa doldurulmak üzere şemada null olarak durur — uydurma sıfır/dolar
değeri yazılmaz.
"""
import os
import time
from datetime import datetime, timezone

from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "vitals"
COUNTERS_DOC = "counters"
COUNTER_FIELDS = ("chat_turns_today", "voice_turns_today", "tool_calls_today")

AUDIT_COLLECTION = "audit_log"
RECENT_BLOCKS_LIMIT = 5

# Process içi gerçekler: modül import anı ≈ process başlangıcı (main.py app/
# paketini startup'ta import eder). Monotonic saat uptime için, duvar saati
# başlangıç damgası için — NTP sıçraması uptime'ı bozamaz.
_STARTED_AT = datetime.now(timezone.utc)
_STARTED_MONO = time.monotonic()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def bump(db, field: str, *, today: str | None = None) -> None:
    """`vitals/counters` dokümanında `field` sayacını 1 artırır (gün bazlı).

    Gün değişmişse (last_reset != bugün) önce tüm sayaçlar sıfırlanır, sonra
    artış uygulanır; tek merge yazımı yapılır. `today` enjeksiyonu testlerde
    determinizm içindir; prod çağrıları parametresizdir. Firestore hatası
    ÇAĞIRANA döner — "best effort" sarması çağıranın sorumluluğudur."""
    if field not in COUNTER_FIELDS:
        raise ValueError(f"bilinmeyen vitals sayacı: {field}")
    today = today or _today()
    ref = db.collection(COLLECTION).document(COUNTERS_DOC)
    snap = ref.get()
    data = dict(snap.to_dict()) if snap.exists else {}
    if data.get("last_reset") != today:
        data = {name: 0 for name in COUNTER_FIELDS}
    data["last_reset"] = today
    data[field] = int(data.get(field, 0) or 0) + 1
    ref.set(data, merge=True)


def read_counters(db) -> dict:
    """`vitals/counters` içeriği; doküman yoksa boş dict (hata değil)."""
    snap = db.collection(COLLECTION).document(COUNTERS_DOC).get()
    if not snap.exists:
        return {}
    return dict(snap.to_dict())


def service_facts() -> dict:
    """Brain'in kendi process'i hakkında ÖLÇÜLEBİLİR gerçekler — uydurma yok.

    min-instance sayısı, CPU/bellek, istek sayısı gibi metrikler process içinden
    okunamaz (Cloud Run API'si gerekir); o alanlar burada YOKTUR, "bilinmiyor"
    diye de yazılmaz. revizyon yalnızca Cloud Run'ın K_REVISION env'i varsa
    dolar; yerelde None kalır."""
    return {
        "baslangic_utc": _STARTED_AT.isoformat(),
        "uptime_saniye": int(time.monotonic() - _STARTED_MONO),
        "revizyon": os.environ.get("K_REVISION") or None,
    }


def recent_blocks(db, limit: int = RECENT_BLOCKS_LIMIT) -> list[dict]:
    """audit_log'dan son `limit` block/confirm kararı, yeniden eskiye.

    FakeDB'nin "in" operatörü desteği olmadığından iki ayrı == sorgusu yapılır
    ve birleşim Python'da sıralanır. İskelet aşamasında block/confirm hacmi
    düşük; koleksiyon büyürse order_by+limit'e (ve composite index'e) geçilir."""
    out = []
    for decision in ("block", "confirm"):
        snaps = (
            db.collection(AUDIT_COLLECTION)
            .where(filter=FieldFilter("decision", "==", decision))
            .stream()
        )
        for snap in snaps:
            entry = snap.to_dict()
            out.append({
                "ts": entry.get("ts"),
                "actor": entry.get("actor"),
                "tool": entry.get("tool"),
                "zone": entry.get("zone"),
                "decision": entry.get("decision"),
            })
    out.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return out[:limit]


def read(db) -> dict:
    """`check_my_vitals` aracının döndürdüğü tam vitals paketi (salt okuma)."""
    return {
        "servisler": service_facts(),
        "kota": read_counters(db),
        "son_hatalar": recent_blocks(db),
        # 30 Temmuz proxy/abonelik geçişi: faturalı API yok, ölçülecek harcama
        # da yok — alan bilinçli null (modül docstring'ine bak).
        "aylik_harcama": None,
    }
