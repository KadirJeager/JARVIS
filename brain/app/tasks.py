"""Görev döngüsü (North Star §7.6, Faz Y1.3): uzun soluklu görevler kuyruklanmış
adımlar + checkpoint + bütçe ile yürür.

Model (Firestore `tasks` koleksiyonu): her görev tek doküman —
{title, goal, status, budget: {max_steps, steps_used}, checkpoint,
created_at, updated_at, last_step_result}. status değerleri:
- "active": her tick'te bir adım ilerler,
- "done": step_fn bitti dedi (terminal),
- "paused": ileride; Kadir durdurdu (bu fazda üreten kod yok),
- "budget_exhausted": bütçe tavanına dayandı — Kadir'e danışmadan devam etmez
  (terminal sayılır; yeniden kurulum task_enqueue ile).

Bütçe bu fazda TEK türdür: adım sayısı (max_steps). Token/para tavanı Y1.x
işidir — check_my_vitals sayaçları üzerinden eklenecek; budget dict'i
genişlemeye açık tutulur (yeni anahtar = yeni tavan türü).

Yürütme modeli: gerçek Pub/Sub topic kurulumu BU FAZDA YOK — Cloud Scheduler
/api/jobs/event'e kind="task_tick" POST'lar; her tick'te her active görev TAM
BİR adım ilerler. Böylece scale-to-zero korunur: "gece boyu projeyi bitir"
yüzlerce kısa uyanışa bölünür. Görev kurulumu da aynı yoldan: kind=
"task_enqueue" (bu fazda LLM aracı YOK; scheduler/kurulum yolu yeterli).

Hata modeli (İlke 4, hata = gözlem): step_fn fırlatırsa görev ÖLMEZ — hata
last_step_result'a yazılır, status active kalır (bir sonraki uyanışta tekrar
dener) ama steps_used YİNE artar: kaçak retry bütçeyi yesin, sonsuz döngü
bütçe tavanında dursun.

step_fn sözleşmesi: step_fn(checkpoint) -> {"done": bool, "checkpoint": ...,
"result": str}. Görev tipine özeldir; bu fazda tek gerçek örnek
health_patrol'dür (checkpoint["url"]'i yoklar, sonucu checkpoint'e geri yazar;
devriye süreklidir, asla done demez — bütçesi bitince durur).

Raporlama: görev done/budget_exhausted olunca özet, sohbet transcript'ine
REPORT_SESSION_ID oturumuna model mesajı olarak düşer; üretim kablosu
(events._handle_task_tick) bu oturumu conversations.touch ile indekse de
yazar — doküman var ama listede yok hatası F13'te kapatıldı. FCM push Y2
işi olmaya devam eder. Alıcı tek-kullanıcı varsayımıyla default_owner()'dır
— çok kullanıcılı sahiplik (owner alanı) sonraki faz.

Canlı ilerleme (F9): görev ACTIVE adım attıkça REPORT_SESSION_ID'deki TEK
ilerleme satırı yerinde güncellenir ("'X' çalışıyor — adım 4/20"). Satır
append DEĞİL upsert'tir (deterministik doc id — progress_row_id): tick-başı
append messages koleksiyonunu spam'leyeceği için F9 uzun süre açık kalmıştı;
upsert tek yazım/tick ile sınırlar. Görev terminal'e (done /
budget_exhausted) ulaşınca nihai rapor düşer ve ilerleme satırı silinir —
geçici satır kalıcı çöp bırakmaz.
"""
import logging
from datetime import datetime, timezone

from google.cloud.firestore_v1.base_query import FieldFilter

from . import config

TASKS_COLLECTION = "tasks"
# Tek kavram tek isim: görev raporları her zaman bu oturum kimliğine düşer.
REPORT_SESSION_ID = "tasks"

# F9 canlı ilerleme satırının transcript kind'ı. İstemci bilinmeyen kind'ları
# toleranslı okur (spec §7): eski APK bu satırı düz model baloncuğu olarak
# gösterir; terminalde satır silindiği için kalıcı iz bırakmaz.
PROGRESS_KIND = "task_progress"

STATUS_ACTIVE = "active"
STATUS_DONE = "done"
STATUS_PAUSED = "paused"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_owner() -> str | None:
    """Raporun düşeceği kullanıcı: ALLOWED_EMAILS'in alfabetik ilki (§7.6 bu
    fazda tek kullanıcıya, Kadir'e hizmet eder). Boşsa None — rapor atlanır,
    görev durma kararı yine de uygulanır."""
    emails = sorted(config.ALLOWED_EMAILS)
    return emails[0] if emails else None


def is_budget_left(task: dict) -> bool:
    """Adım bütçesi kaldı mı: steps_used < max_steps."""
    budget = task.get("budget") or {}
    return budget.get("steps_used", 0) < budget.get("max_steps", 0)


def enqueue(db, title, goal, max_steps=None, checkpoint=None, now_fn=_now) -> str:
    """Yeni görev kurar, task_id döner. max_steps verilmezse
    config.TASKS_DEFAULT_MAX_STEPS (bütçesiz görev YOK — §7.6)."""
    if not isinstance(title, str) or not title:
        raise ValueError("görev başlığı (title) gerekli")
    if not isinstance(goal, str) or not goal:
        raise ValueError("görev hedefi (goal) gerekli")
    if max_steps is None:
        max_steps = config.TASKS_DEFAULT_MAX_STEPS
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
        raise ValueError("max_steps pozitif tam sayı olmalı")
    now = now_fn()
    doc = {
        "title": title,
        "goal": goal,
        "status": STATUS_ACTIVE,
        "budget": {"max_steps": max_steps, "steps_used": 0},
        "checkpoint": checkpoint,
        "created_at": now,
        "updated_at": now,
        "last_step_result": None,
    }
    _, ref = db.collection(TASKS_COLLECTION).add(doc)
    logging.info("tasks: enqueue id=%s title=%r max_steps=%d", ref.id, title, max_steps)
    return ref.id


def progress_row_id(user_id: str, task_id: str) -> str:
    """F9 ilerleme satırının deterministik transcript doc id'si: kullanıcı ve
    görev başına TEK satır — tick sayısı ne olursa olsun. Firestore doc id
    kurallarına uygun ([A-Za-z0-9._@-]; '/' yok)."""
    return f"task-progress-{user_id}-{task_id}"


def _progress_text(task: dict) -> str:
    budget = task["budget"]
    return (f"'{task['title']}' çalışıyor — "
            f"adım {budget['steps_used']}/{budget['max_steps']}\n"
            f"Son adım: {task.get('last_step_result')}")


def make_progress_writer(store, user_id: str):
    """F9 canlı ilerleme yazıcısı. store: MessageStore (upsert_row/delete_row
    imzalı). Sözleşme step_once ile aramızda: her adım sonunda (status neyse)
    progress(task, task_id) çağrılır; ACTIVE'de satır upsert edilir, terminal
    durumda satır SİLİNİR — nihai rapor zaten düşmüş olur, geçici satır
    kalıcı çöp bırakmaz.

    Tick-başı append yapmamanın sebebi ölçülmüştür (devir notu §4): messages
    koleksiyonu görev başına yüzlerce "adım 3/20" satırıyla dolar. Upsert tek
    dokümanı günceller; yazma maliyeti tick sıklığına, koleksiyon boyutu 1'e
    sabittir."""
    def progress(task: dict, task_id: str) -> None:
        row_id = progress_row_id(user_id, task_id)
        if task.get("status") in (STATUS_DONE, STATUS_BUDGET_EXHAUSTED):
            store.delete_row(user_id, REPORT_SESSION_ID, row_id)
            return
        store.upsert_row(user_id, REPORT_SESSION_ID, row_id, "model",
                         _progress_text(task), kind=PROGRESS_KIND,
                         meta={"task_id": task_id})
    return progress


def _progress(progress_fn, task: dict, task_id: str) -> None:
    """İlerleme yazımı best-effort — _report'un aynısı: hata görevin kararını
    (Firestore'a işlenmiş durum) bozmaz, loglanır ve yutulur."""
    if progress_fn is None:
        return
    try:
        progress_fn(task, task_id)
    except Exception:
        logging.exception("tasks: ilerleme satırı yazılamadı (status=%s title=%r)",
                          task.get("status"), task.get("title"))


def make_reporter(store, user_id: str, conversations_store=None):
    """store: messages.MessageStore (veya aynı append imzalı herhangi bir şey).
    Raporlar sabit REPORT_SESSION_ID oturumuna model mesajı olarak düşer.

    conversations_store (F13 görünürlük düzeltmesi) verilirse her rapordan
    sonra oturum indeksi touch() edilir: list_conversations yalnızca touch'lı
    oturumları döndürdüğü için bu çağrı olmadan rapor Firestore'da var ama
    UYGULAMADA görünmezdi — modül docstring'i aksini iddia ediyordu ve ölçüm
    onu yalan çıkardı. Başlık sabit verilir (ilk kullanıcı mesajı kuralına
    muhtaç olmadan); touch hatası raporu bozmaz."""
    def report(task: dict, text: str) -> None:
        store.append(user_id, REPORT_SESSION_ID, "model", text)
        if conversations_store is not None:
            conversations_store.touch(user_id, REPORT_SESSION_ID, "model", text,
                                      title="Görev raporları")
    return report


def _report_text(task: dict, new_status: str) -> str:
    budget = task["budget"]
    adim = f"Adım: {budget['steps_used']}/{budget['max_steps']}"
    if new_status == STATUS_DONE:
        head = f"Görev tamamlandı: '{task['title']}'"
        what = f"Sonuç: {task.get('last_step_result')}"
    else:
        # budget_exhausted: §7.6 — bütçesi biten görev durur ve Kadir'e danışır.
        head = f"Görev bütçesi doldu, Kadir'e danış: '{task['title']}'"
        what = f"Hedef: {task['goal']}"
    return f"{head}\n{what}\nSon checkpoint: {task.get('checkpoint')}\n{adim}"


def _report(report_fn, task: dict, new_status: str) -> None:
    """Rapor yazımı best-effort: mesaj yazılamazsa görevin durma kararı
    zaten Firestore'a işlenmiştir; hata loglanır, yutulur."""
    if report_fn is None:
        return
    try:
        report_fn(task, _report_text(task, new_status))
    except Exception:
        logging.exception("tasks: rapor yazılamadı (status=%s title=%r)",
                          new_status, task.get("title"))


def step_once(db, task_id: str, step_fn, report_fn=None, progress_fn=None,
              now_fn=_now) -> dict:
    """Tek görevde TAM BİR adım. Dönen dict en az {stepped, status, task_id}.

    Sıra: status active değilse no-op → bütçe dolmuşsa adım ATILMAZ, görev
    budget_exhausted'a çekilir + raporlanır → step_fn(checkpoint) çalışır,
    steps_used+1 / checkpoint / last_step_result yazılır. step_fn done derse
    status done + rapor. Adım tavanı doldurursa bir tick bekletmeden hemen
    budget_exhausted + rapor (bildirim bir tur gecikmesin). step_fn hatası
    görevi öldürmez: hata last_step_result'a, sayaç yine +1 (modül docstring).

    progress_fn (F9): her MUTASYONLU çıkışta progress(task, task_id) çağrılır —
    active adımda ilerleme satırı güncellenir, terminalde silinir
    (make_progress_writer sözleşmesi). Mutasyonsuz no-op çıkışta çağrılmaz."""
    ref = db.collection(TASKS_COLLECTION).document(task_id)
    snap = ref.get()
    if not snap.exists:
        raise KeyError(f"görev bulunamadı: {task_id}")
    task = snap.to_dict()

    if task.get("status") != STATUS_ACTIVE:
        return {"stepped": False, "status": task.get("status"), "task_id": task_id}

    # Bütçe kontrolü adımdan ÖNCE: tavan dolmuşsa adım atılmaz.
    if not is_budget_left(task):
        update = {"status": STATUS_BUDGET_EXHAUSTED, "updated_at": now_fn()}
        ref.set(update, merge=True)
        task.update(update)
        _report(report_fn, task, STATUS_BUDGET_EXHAUSTED)
        _progress(progress_fn, task, task_id)  # bayat ilerleme satırını sil
        logging.info("tasks: bütçe doldu id=%s (adım atılmadı)", task_id)
        return {"stepped": False, "status": STATUS_BUDGET_EXHAUSTED, "task_id": task_id}

    budget = task["budget"]
    steps_used = budget["steps_used"] + 1  # hata da adım sayılır: kaçak retry bütçe yer
    update = {
        "budget": {"max_steps": budget["max_steps"], "steps_used": steps_used},
        "updated_at": now_fn(),
    }
    try:
        outcome = step_fn(task.get("checkpoint")) or {}
    except Exception as exc:
        update["last_step_result"] = f"adım hatası: {exc}"
        ref.set(update, merge=True)
        task.update(update)
        _progress(progress_fn, task, task_id)  # hatayı canlı satırda da göster
        logging.info("tasks: adım hatası id=%s steps=%d hata=%s", task_id, steps_used, exc)
        return {"stepped": True, "status": STATUS_ACTIVE, "task_id": task_id,
                "steps_used": steps_used, "error": str(exc)}

    if "checkpoint" in outcome:
        update["checkpoint"] = outcome["checkpoint"]
    update["last_step_result"] = outcome.get("result")

    if outcome.get("done"):
        update["status"] = STATUS_DONE
        ref.set(update, merge=True)
        task.update(update)
        _report(report_fn, task, STATUS_DONE)
        _progress(progress_fn, task, task_id)  # satır silinir
        logging.info("tasks: bitti id=%s steps=%d", task_id, steps_used)
        return {"stepped": True, "status": STATUS_DONE, "task_id": task_id,
                "steps_used": steps_used, "result": outcome.get("result")}

    if not is_budget_left({"budget": update["budget"]}):
        # Bu adım tavanı doldurdu: hemen durdur + raporla.
        update["status"] = STATUS_BUDGET_EXHAUSTED
        ref.set(update, merge=True)
        task.update(update)
        _report(report_fn, task, STATUS_BUDGET_EXHAUSTED)
        _progress(progress_fn, task, task_id)  # satır silinir
        logging.info("tasks: bütçe doldu id=%s steps=%d", task_id, steps_used)
        return {"stepped": True, "status": STATUS_BUDGET_EXHAUSTED, "task_id": task_id,
                "steps_used": steps_used, "result": outcome.get("result")}

    ref.set(update, merge=True)
    task.update(update)
    _progress(progress_fn, task, task_id)  # "'X' çalışıyor — adım n/m"
    logging.info("tasks: adım id=%s steps=%d/%d", task_id, steps_used, budget["max_steps"])
    return {"stepped": True, "status": STATUS_ACTIVE, "task_id": task_id,
            "steps_used": steps_used, "result": outcome.get("result")}


def tick(db, step_fn, report_fn=None, progress_fn=None, now_fn=_now) -> dict:
    """Her active görevde bir step_once; tick özetini döner.

    Görev başına hata izolasyonu repo-watch deseninin aynısı: bir görevin
    hatası (örn. doküman okunamadı) diğerlerinin adımını kesmez; loglanır,
    sayaçlara girmez. notify kuralı: bu tick'te biten VEYA bütçesi dolan
    görev varsa True — scheduler log'unda/ilerideki push'ta "Kadir'e haber
    ver" sinyali."""
    stepped = done = exhausted = errors = 0
    snaps = (
        db.collection(TASKS_COLLECTION)
        .where(filter=FieldFilter("status", "==", STATUS_ACTIVE))
        .stream()
    )
    for snap in snaps:
        task_id = snap.reference.id
        try:
            out = step_once(db, task_id, step_fn, report_fn=report_fn,
                            progress_fn=progress_fn, now_fn=now_fn)
        except Exception:
            errors += 1
            logging.exception("tasks: tick görev hatası id=%s", task_id)
            continue
        if out.get("stepped"):
            stepped += 1
        if out.get("status") == STATUS_DONE:
            done += 1
        elif out.get("status") == STATUS_BUDGET_EXHAUSTED:
            exhausted += 1
    notify = (done + exhausted) > 0
    summary = (
        f"Görev turu: {stepped} görev adım attı, {done} bitti, "
        f"{exhausted} bütçe doldu"
    )
    logging.info("tasks: tick stepped=%d done=%d exhausted=%d errors=%d",
                 stepped, done, exhausted, errors)
    return {"stepped": stepped, "done": done, "exhausted": exhausted,
            "errors": errors, "notify": notify, "summary": summary}


def health_patrol_step(fetch):
    """Bu fazın tek gerçek görev tipi: checkpoint["url"]'i yoklayan devriye.

    fetch, events.http_status imzalıdır (url -> status|None) ve events.record
    tarafından enjekte edilir — tasks modülü events'i import etmez (events
    zaten tasks'ı import eder; döngü olmaz). Devriye süreklidir: asla done
    demez, bütçesi bitene kadar her tick'te bir yoklama yapar."""
    def step(checkpoint):
        cp = dict(checkpoint or {})
        url = cp.get("url")
        status = fetch(url)
        cp["last_status"] = status
        if status == 200:
            durum = "sağlıklı"
        elif status is None:
            durum = "ulaşılamadı"
        else:
            durum = f"HTTP {status}"
        return {"done": False, "checkpoint": cp, "result": f"{url} -> {durum}"}
    return step
