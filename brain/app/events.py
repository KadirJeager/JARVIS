"""Olay katmanı iskeleti (North Star §4.4, Faz Y1): olay düşer → kaydedilir →
basit kural motoru değerlendirir.

Akış: Cloud Scheduler (ileride Pub/Sub push) /api/jobs/event'e POST atar →
her olay Firestore `events` koleksiyonuna yazılır → kind'a göre işlenir.

Desteklenen kind'lar:
- "ping": canlılık sinyali; sadece kayıt (handled=True, notify=False).
- "health_check": payload'daki servis listesinin /api/health'ini çağırır;
  bir servis bile 200 dönmezse notify=True + Türkçe özet.
- "task_tick": görev döngüsü (§7.6, app/tasks.py) — her active görev bir adım
  ilerler; biten/bütçesi dolan görev varsa notify=True + Türkçe özet.
- "task_enqueue": payload {title, goal, max_steps?, checkpoint?} ile görev
  kurar (events şeması bozulmaz; kurulum da olaydır).

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

from . import conversations, messages, retro, tasks

EVENTS_COLLECTION = "events"
SUPPORTED_KINDS = ("ping", "health_check", "task_tick", "task_enqueue", "weekly_retro",
                   "calendar_event", "gmail_message")
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


def _handle_task_tick(db, payload: dict, fetch, now_fn) -> dict:
    """Görev döngüsü turu (§7.6): her active görev bir adım ilerler.

    fetch (events.http_status imzalı) health_patrol step'ine enjekte edilir;
    raporlar payload["owner"] yoksa tasks.default_owner()'ın REPORT_SESSION_ID
    oturumuna düşer (owner çözülemezse rapor atlanır, görevler yine yürür).
    F9: aynı oturumda TEK canlı ilerleme satırı da tutulur (upsert; terminalde
    silinir) — uzun işte uygulama "adım 4/20"yi tick aralarında görür."""
    owner = payload.get("owner") or tasks.default_owner()
    report_fn = None
    progress_fn = None
    if owner:
        store = messages.MessageStore(db)
        report_fn = tasks.make_reporter(store, owner,
                                        conversations.ConversationStore(db))
        progress_fn = tasks.make_progress_writer(store, owner)
    return tasks.tick(db, tasks.health_patrol_step(fetch),
                      report_fn=report_fn, progress_fn=progress_fn, now_fn=now_fn)


def _handle_task_enqueue(db, payload: dict, now_fn) -> tuple[dict | None, bool, str, bool]:
    """Görev kurulumu: (result, notify, summary, handled). Geçersiz payload
    502 değil handled=False gözlemidir — kurulum isteğinin reddi de kayda
    geçer (hata = gözlem)."""
    try:
        task_id = tasks.enqueue(
            db,
            title=payload.get("title"),
            goal=payload.get("goal"),
            max_steps=payload.get("max_steps"),
            checkpoint=payload.get("checkpoint"),
            now_fn=now_fn,
        )
    except ValueError as exc:
        return None, False, f"Görev kurulamadı: {exc}", False
    return {"task_id": task_id}, False, f"Görev kuruldu: {payload.get('title')}", True


def _handle_weekly_retro(db, payload: dict, now_fn) -> tuple[dict, bool, str]:
    """Haftalık retro (§8.4, app/retro.py): geçmiş 7 günün metriklerini ve derslerini
    derler, LLM ile özet oluşturur ve 'retro' oturumuna rapor atar."""
    owner = payload.get("owner")
    llm_fn = payload.get("llm_fn")
    out = retro.run(db, owner=owner, llm_fn=llm_fn, now_fn=now_fn)
    notify = bool(out.get("ok"))
    summary = out.get("summary") or "Haftalık retro çalıştırıldı"
    return out, notify, summary


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
    elif kind == "task_tick":
        out = _handle_task_tick(db, payload, fetch, now_fn)
        result = {"stepped": out["stepped"], "done": out["done"],
                  "exhausted": out["exhausted"], "errors": out["errors"]}
        notify, summary = out["notify"], out["summary"]
        handled = True
    elif kind == "task_enqueue":
        result, notify, summary, handled = _handle_task_enqueue(db, payload, now_fn)
    elif kind == "weekly_retro":
        result, notify, summary = _handle_weekly_retro(db, payload, now_fn)
        handled = True
    elif kind in ("calendar_event", "gmail_message"):
        # Poller-written items (workspace_poll): the poller already computed
        # notify per item (24h window / importance rule); record() just needs
        # to persist it in the shared schema. handled=True -- the item WAS
        # processed, "processed = stored" for stream kinds.
        notify = bool(payload.get("notify"))
        result = {"notify": notify}
        summary = payload.get("summary") or payload.get("subject") or kind
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
