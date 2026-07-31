"""Olay katmanı iskeleti (North Star §4.4, Faz Y1): olay düşer → kaydedilir →
basit kural motoru değerlendirir.

Akış: Cloud Scheduler (ileride Pub/Sub push) /api/jobs/event'e POST atar →
her olay Firestore `events` koleksiyonuna yazılır → kind'a göre işlenir.

İlk desteklenen kind'lar:
- "ping": canlılık sinyali; sadece kayıt (handled=True, notify=False).
- "health_check": payload'daki servis listesinin /api/health'ini çağırır;
  bir servis bile 200 dönmezse notify=True + Türkçe özet.

Bilinmeyen kind HATA DEĞİLDİR (hata = gözlem): 400 yerine handled=False ile
kaydedilir — yeni kind'lar sonradan kod eklemeden gönderilmeye başlanabilir,
kayıt zinciri kopmaz.

Değerlendirme ŞİMDİLİK kural motorudur; LLM destekli değerlendirme (olayı
bağlamla yorumlayıp Kadir'e konuşma/eylem kararı) Faz Y1.3+ işidir.

repo-watch'a bilinçli BAĞLI DEĞİLDİR: repo-watch kendi endpoint'inde
(/api/jobs/repo-watch) ve kendi koleksiyonlarında kalır; iki katman yalnızca
"per-item hata izolasyonu + DATA-level log + scheduler OIDC" desenini paylaşır.
"""
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

EVENTS_COLLECTION = "events"
SUPPORTED_KINDS = ("ping", "health_check")
HEALTH_TIMEOUT_SECONDS = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_status(url: str) -> int | None:
    """GET url -> HTTP status. Ağ hatası/zaman aşımı -> None (ulaşılamaz gözlemi).

    HTTPError'da sunucunun döndürdüğü kod (503, 404...) anlamlı bir cevaptır:
    sağlık kontrolü için "sağlıksız" sayılır ama "ulaşılamadı"dan farklıdır."""
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _handle_health_check(payload: dict, fetch) -> tuple[dict, bool, str]:
    """payload["services"]: [{"name": ..., "url": ...}, ...] — her birini yokla.

    Döner: (result, notify, özet). Servis başına hata izolasyonu repo-watch
    deseninin aynısı: bir servisin ağ hatası diğerlerinin kontrolünü kesmez,
    o servisin satırına status=None yazılır."""
    services = payload.get("services") or []
    checks = []
    failed = []
    for svc in services:
        name = svc.get("name") or svc.get("url") or "?"
        url = svc.get("url", "")
        status = fetch(url)
        ok = status == 200
        checks.append({"name": name, "url": url, "status": status, "ok": ok})
        if not ok:
            failed.append(name)
    result = {"servisler": checks}
    if failed:
        return (
            result,
            True,
            f"Sağlık kontrolü: {len(failed)}/{len(checks)} servis sorunlu: {', '.join(failed)}",
        )
    return result, False, f"Sağlık kontrolü: {len(checks)} servisin hepsi sağlıklı"


def record(db, *, source: str, kind: str, payload: dict, fetch=None,
           now_fn=_now) -> dict:
    """Tek olayı işler, `events` koleksiyonuna yazar, endpoint özetini döner.

    Kural motoru kararı (notify) + işlenme bayrağı (handled) + işlem sonucu
    (result) olayla birlikte kalıcılaşır — sonradan "beyin bu olaya ne dedi"
    sorusu koleksiyondan cevaplanabilir.

    `fetch` None bırakılırsa çağrı anında http_status çözümlenir (def-time
    default DEĞİL): testler ve ilerideki istemciler modül fonksiyonunu
    monkeypatch'leyebilsin diye."""
    fetch = fetch or http_status
    if kind == "ping":
        handled, result, notify = True, None, False
        summary = "ping kaydedildi"
    elif kind == "health_check":
        result, notify, summary = _handle_health_check(payload, fetch)
        handled = True
    else:
        handled, result, notify = False, None, False
        summary = f"Bilinmeyen olay türü '{kind}' gözlem olarak kaydedildi"
    event = {
        "source": source,
        "kind": kind,
        "payload": payload,
        "ts": now_fn(),
        "handled": handled,
        "result": result,
    }
    db.collection(EVENTS_COLLECTION).add(event)
    # DATA-level log: source → kind → handled (+ notify) — tek satırda olayın
    # akıbeti görünür; bir hata kaydı ilk turda lokalize edilebilir.
    logging.info(
        "events: source=%s kind=%s handled=%s notify=%s summary=%s",
        source, kind, handled, notify, summary,
    )
    return {"handled": handled, "notify": notify, "summary": summary}
