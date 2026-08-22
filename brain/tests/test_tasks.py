"""Görev döngüsü (North Star §7.6, Faz Y1.3): tasks modülü birim testleri.

Gerçek Firestore yok (FakeDB); step_fn'ler saf Python callable'larıdır.
Rapor testleri gerçek MessageStore üzerinden gider — raporun sohbet
transcript'ine doğru oturum/rol ile düştüğü FakeDB sorgularıyla doğrulanır.
"""
import pytest

from app import config, tasks
from app.messages import MessageStore
from tests.fakes import FakeDB

OWNER = sorted(config.ALLOWED_EMAILS)[0]


def _doc(db, task_id):
    return db.collection(tasks.TASKS_COLLECTION).document(task_id).get().to_dict()


def _set(db, task_id, **fields):
    db.collection(tasks.TASKS_COLLECTION).document(task_id).set(fields, merge=True)


def _ok_step(checkpoint):
    return {
        "done": False,
        "checkpoint": {"n": (checkpoint or {}).get("n", 0) + 1},
        "result": "adım ok",
    }


def _reports(db):
    return MessageStore(db).history(OWNER, tasks.REPORT_SESSION_ID)


# --- enqueue -------------------------------------------------------------------


def test_enqueue_schema_and_default_budget():
    db = FakeDB()
    task_id = tasks.enqueue(db, "gece devriyesi", "siteyi yokla",
                            now_fn=lambda: "2026-07-31T12:00:00+00:00")
    doc = _doc(db, task_id)
    assert set(doc) == {"title", "goal", "status", "budget", "checkpoint",
                        "created_at", "updated_at", "last_step_result"}
    assert doc["title"] == "gece devriyesi" and doc["goal"] == "siteyi yokla"
    assert doc["status"] == tasks.STATUS_ACTIVE
    assert doc["budget"] == {"max_steps": config.TASKS_DEFAULT_MAX_STEPS,
                             "steps_used": 0}
    assert doc["checkpoint"] is None and doc["last_step_result"] is None
    assert doc["created_at"] == doc["updated_at"] == "2026-07-31T12:00:00+00:00"


def test_enqueue_explicit_budget_and_checkpoint():
    db = FakeDB()
    task_id = tasks.enqueue(db, "t", "g", max_steps=3, checkpoint={"url": "u"})
    doc = _doc(db, task_id)
    assert doc["budget"]["max_steps"] == 3
    assert doc["checkpoint"] == {"url": "u"}


@pytest.mark.parametrize("kwargs", [
    {"title": "", "goal": "g"},
    {"title": "t", "goal": ""},
    {"title": "t", "goal": "g", "max_steps": 0},
    {"title": "t", "goal": "g", "max_steps": -2},
    {"title": "t", "goal": "g", "max_steps": "beş"},
])
def test_enqueue_validation(kwargs):
    with pytest.raises(ValueError):
        tasks.enqueue(FakeDB(), **kwargs)


def test_is_budget_left():
    assert tasks.is_budget_left({"budget": {"max_steps": 2, "steps_used": 1}})
    assert not tasks.is_budget_left({"budget": {"max_steps": 2, "steps_used": 2}})


# --- step_once -----------------------------------------------------------------


def test_step_once_advances_counter_checkpoint_and_result():
    db = FakeDB()
    task_id = tasks.enqueue(db, "t", "g", max_steps=5)
    out = tasks.step_once(db, task_id, _ok_step)
    assert out["stepped"] is True and out["status"] == tasks.STATUS_ACTIVE
    assert out["steps_used"] == 1 and out["result"] == "adım ok"
    doc = _doc(db, task_id)
    assert doc["budget"]["steps_used"] == 1
    assert doc["checkpoint"] == {"n": 1}
    assert doc["last_step_result"] == "adım ok"


def test_step_once_ignores_non_active_task():
    db = FakeDB()
    task_id = tasks.enqueue(db, "t", "g", max_steps=5)
    _set(db, task_id, status=tasks.STATUS_DONE)
    called = []
    out = tasks.step_once(db, task_id, lambda cp: called.append(1))
    assert out == {"stepped": False, "status": tasks.STATUS_DONE, "task_id": task_id}
    assert called == [] and _doc(db, task_id)["budget"]["steps_used"] == 0


def test_step_once_missing_task_raises():
    with pytest.raises(KeyError):
        tasks.step_once(FakeDB(), "yok-boyle-gorev", _ok_step)


def test_step_once_budget_full_stops_without_stepping_and_reports():
    """Bütçe doluysa adım ATILMAZ: status budget_exhausted, step_fn çağrılmaz,
    rapor 'tasks' oturumuna model mesajı olarak düşer (§7.6: dur + danış)."""
    db = FakeDB()
    task_id = tasks.enqueue(db, "uzun iş", "g", max_steps=2)
    _set(db, task_id, budget={"max_steps": 2, "steps_used": 2})
    called = []
    report_fn = tasks.make_reporter(MessageStore(db), OWNER)
    out = tasks.step_once(db, task_id, lambda cp: called.append(1),
                          report_fn=report_fn)
    assert out["stepped"] is False
    assert out["status"] == tasks.STATUS_BUDGET_EXHAUSTED
    assert called == []
    doc = _doc(db, task_id)
    assert doc["status"] == tasks.STATUS_BUDGET_EXHAUSTED
    assert doc["budget"]["steps_used"] == 2
    (msg,) = _reports(db)
    assert msg["role"] == "model"
    assert "uzun iş" in msg["text"] and "bütçesi doldu" in msg["text"]
    assert "2/2" in msg["text"]


def test_step_once_done_marks_done_and_reports():
    db = FakeDB()
    task_id = tasks.enqueue(db, "kısa iş", "g", max_steps=5)
    report_fn = tasks.make_reporter(MessageStore(db), OWNER)

    def finishing_step(checkpoint):
        return {"done": True, "checkpoint": {"son": 42}, "result": "her şey bitti"}

    out = tasks.step_once(db, task_id, finishing_step, report_fn=report_fn)
    assert out["stepped"] is True and out["status"] == tasks.STATUS_DONE
    doc = _doc(db, task_id)
    assert doc["status"] == tasks.STATUS_DONE
    assert doc["budget"]["steps_used"] == 1
    assert doc["checkpoint"] == {"son": 42}
    (msg,) = _reports(db)
    assert "kısa iş" in msg["text"] and "tamamlandı" in msg["text"]
    assert "her şey bitti" in msg["text"] and "1/5" in msg["text"]


def test_step_fn_error_keeps_active_but_counts_budget():
    """İlke 4 (hata = gözlem) + bütçe disiplini: hata last_step_result'a yazılır,
    status active kalır (sonraki uyanışta tekrar) ama steps_used yine artar —
    kaçak retry bütçeyi yesin."""
    db = FakeDB()
    task_id = tasks.enqueue(db, "t", "g", max_steps=5)

    def boom(checkpoint):
        raise RuntimeError("ağ koptu")

    out = tasks.step_once(db, task_id, boom)
    assert out["stepped"] is True and out["status"] == tasks.STATUS_ACTIVE
    assert out["error"] == "ağ koptu"
    doc = _doc(db, task_id)
    assert doc["status"] == tasks.STATUS_ACTIVE
    assert doc["budget"]["steps_used"] == 1
    assert "ağ koptu" in doc["last_step_result"]


def test_step_consuming_last_budget_stops_immediately():
    """Adım tavanı doldurursa bir tick beklenmez: aynı adımda budget_exhausted
    + rapor (bildirim bir tur gecikmesin)."""
    db = FakeDB()
    task_id = tasks.enqueue(db, "tek atımlık", "g", max_steps=1)
    report_fn = tasks.make_reporter(MessageStore(db), OWNER)
    out = tasks.step_once(db, task_id, _ok_step, report_fn=report_fn)
    assert out["stepped"] is True
    assert out["status"] == tasks.STATUS_BUDGET_EXHAUSTED
    assert _doc(db, task_id)["budget"]["steps_used"] == 1
    (msg,) = _reports(db)
    assert "tek atımlık" in msg["text"] and "1/1" in msg["text"]


# --- health_patrol (bu fazın tek gerçek görev tipi) -----------------------------


def test_health_patrol_records_status_into_checkpoint():
    step = tasks.health_patrol_step(lambda url: 200)
    out = step({"url": "https://x.example/health"})
    assert out["done"] is False
    assert out["checkpoint"] == {"url": "https://x.example/health", "last_status": 200}
    assert "sağlıklı" in out["result"]


def test_health_patrol_unreachable_is_observation_not_crash():
    step = tasks.health_patrol_step(lambda url: None)
    out = step({"url": "https://x.example/health"})
    assert out["checkpoint"]["last_status"] is None
    assert "ulaşılamadı" in out["result"]


# --- tick -----------------------------------------------------------------------


def test_tick_steps_only_active_tasks_and_notifies_on_done():
    """2 active + 1 done karışımı: yalnız active'ler adım atar; biten görev
    notify=True yapar; özet Türkçe ve sayıları doğru."""
    db = FakeDB()
    a = tasks.enqueue(db, "A", "g", max_steps=5)
    b = tasks.enqueue(db, "B", "g", max_steps=5)
    c = tasks.enqueue(db, "C", "g", max_steps=5)
    _set(db, c, status=tasks.STATUS_DONE)

    def step(checkpoint):
        # A'nın checkpoint'i "bitir" işaretli: bu turda done dönsün.
        if (checkpoint or {}).get("bitir"):
            return {"done": True, "result": "A bitti"}
        return {"done": False, "result": "adım"}

    _set(db, a, checkpoint={"bitir": True})
    out = tasks.tick(db, step)
    assert out["stepped"] == 2 and out["done"] == 1 and out["exhausted"] == 0
    assert out["notify"] is True
    assert "2 görev adım attı" in out["summary"]
    assert "1 bitti" in out["summary"]
    assert "0 bütçe doldu" in out["summary"]
    assert _doc(db, a)["status"] == tasks.STATUS_DONE
    assert _doc(db, b)["budget"]["steps_used"] == 1
    # done görev dokunulmaz:
    assert _doc(db, c)["budget"]["steps_used"] == 0


def test_tick_budget_exhaustion_notifies_and_reports():
    db = FakeDB()
    task_id = tasks.enqueue(db, "dolan", "g", max_steps=1)
    _set(db, task_id, budget={"max_steps": 1, "steps_used": 1})
    report_fn = tasks.make_reporter(MessageStore(db), OWNER)
    out = tasks.tick(db, _ok_step, report_fn=report_fn)
    assert out["stepped"] == 0 and out["exhausted"] == 1
    assert out["notify"] is True
    assert "1 bütçe doldu" in out["summary"]
    (msg,) = _reports(db)
    assert "dolan" in msg["text"]


def test_tick_with_no_active_tasks_notifies_false():
    out = tasks.tick(FakeDB(), _ok_step)
    assert out == {"stepped": 0, "done": 0, "exhausted": 0, "errors": 0,
                   "notify": False,
                   "summary": "Görev turu: 0 görev adım attı, 0 bitti, 0 bütçe doldu"}


def test_tick_per_task_error_isolation(monkeypatch):
    """Bir görevin step_once'u fırlatırsa (örn. doküman okunamadı) diğer
    görevlerin adımı kesilmez; hata sayaca girer, özete yansımaz."""
    db = FakeDB()
    a = tasks.enqueue(db, "A", "g", max_steps=5)
    b = tasks.enqueue(db, "B", "g", max_steps=5)
    real_step_once = tasks.step_once

    def flaky(db_, task_id, step_fn, **kw):
        if task_id == a:
            raise RuntimeError("firestore hıçkırığı")
        return real_step_once(db_, task_id, step_fn, **kw)

    monkeypatch.setattr(tasks, "step_once", flaky)
    out = tasks.tick(db, _ok_step)
    assert out["errors"] == 1 and out["stepped"] == 1
    assert _doc(db, b)["budget"]["steps_used"] == 1


# ---------------------------------------------------------------------------
# F13 görünürlük: rapor oturumu conversations indeksine touch'lanmalı
# ---------------------------------------------------------------------------


def test_reporter_touches_the_conversation_index():
    """list_conversations yalnızca touch'lı oturumları döndürür — touch'sız
    görev raporu Firestore'da var ama uygulamada GÖRÜNMEZDİ (ölçülen bug)."""
    from app import conversations

    db = FakeDB()
    report_fn = tasks.make_reporter(MessageStore(db), OWNER,
                                    conversations.ConversationStore(db))
    report_fn({"title": "t"}, "Görev tamamlandı: 't'\nSonuç: ok")

    listed = conversations.ConversationStore(db).list_conversations(OWNER)
    assert [(c["session_id"], c["title"]) for c in listed] == \
        [("tasks", "Görev raporları")]


def test_reporter_without_index_store_keeps_old_behavior():
    db = FakeDB()
    report_fn = tasks.make_reporter(MessageStore(db), OWNER)
    report_fn({"title": "t"}, "Görev tamamlandı: 't'")

    assert MessageStore(db).history(OWNER, tasks.REPORT_SESSION_ID)
    # Eski çağrı biçimi (indeks verilmedi): hiçbir doküman yazılmadı.
    assert conversations_empty(db)


def conversations_empty(db):
    return list(db.collection("conversations").stream()) == []


# ---------------------------------------------------------------------------
# F9 canlı ilerleme: TEK satır upsert edilir, terminalde silinir
# ---------------------------------------------------------------------------


def test_progress_writer_keeps_one_row_and_updates_it_in_place():
    """Tick-başı append YASAK (ölçülen spam riski): iki adım sonunda hâlâ TEK
    satır vardır ve metni son adımı gösterir."""
    db = FakeDB()
    progress = tasks.make_progress_writer(MessageStore(db), OWNER)
    task_id = tasks.enqueue(db, "uzun iş", "g", max_steps=5)

    out1 = tasks.step_once(db, task_id, _ok_step, progress_fn=progress)
    out2 = tasks.step_once(db, task_id, _ok_step, progress_fn=progress)

    rows = _reports(db)
    assert len(rows) == 1
    (row,) = rows
    assert row["kind"] == tasks.PROGRESS_KIND
    assert "uzun iş" in row["text"]
    assert f"adım {out2['steps_used']}/5" in row["text"]
    assert out1["steps_used"] == 1 and out2["steps_used"] == 2


def test_progress_row_disappears_when_task_done_and_final_report_lands():
    """Terminalde nihai rapor düşer, geçici ilerleme satırı silinir — kalıcı
    çöp bırakmaz."""
    db = FakeDB()
    progress = tasks.make_progress_writer(MessageStore(db), OWNER)
    report = tasks.make_reporter(MessageStore(db), OWNER)
    task_id = tasks.enqueue(db, "kısa iş", "g", max_steps=5)
    tasks.step_once(db, task_id, _ok_step, report_fn=report, progress_fn=progress)
    assert len(_reports(db)) == 1

    def finishing(checkpoint):
        return {"done": True, "result": "bitti"}

    tasks.step_once(db, task_id, finishing, report_fn=report, progress_fn=progress)

    rows = _reports(db)
    assert [r.get("kind") for r in rows] == [None]
    assert "tamamlandı" in rows[0]["text"]


def test_error_step_is_visible_in_the_live_progress_line():
    """İlke 4 (hata = gözlem): başarısız adım da canlı satıra yazılır — kullanıcı
    'adım hatası: …'yı bir sonraki poll'da görür, karanlıkta beklemmez."""
    db = FakeDB()
    progress = tasks.make_progress_writer(MessageStore(db), OWNER)
    task_id = tasks.enqueue(db, "t", "g", max_steps=5)

    def boom(checkpoint):
        raise RuntimeError("ağ koptu")

    tasks.step_once(db, task_id, boom, progress_fn=progress)
    (row,) = _reports(db)
    assert "adım hatası" in row["text"] and "ağ koptu" in row["text"]


def test_budget_exhausted_without_stepping_clears_stale_progress_row():
    db = FakeDB()
    progress = tasks.make_progress_writer(MessageStore(db), OWNER)
    task_id = tasks.enqueue(db, "t", "g", max_steps=5)
    tasks.step_once(db, task_id, _ok_step, progress_fn=progress)
    assert len(_reports(db)) == 1

    _set(db, task_id, budget={"max_steps": 5, "steps_used": 5})
    tasks.step_once(db, task_id, lambda cp: None, progress_fn=progress)
    assert _reports(db) == []


def test_step_once_without_progress_fn_is_unchanged():
    """progress_fn opsiyoneldir: eski çağrı biçimi (yalnız report_fn) hiçbir
    ilerleme satırı yazmaz, davranış değişmeden çalışır."""
    db = FakeDB()
    task_id = tasks.enqueue(db, "t", "g", max_steps=5)
    out = tasks.step_once(db, task_id, _ok_step)
    assert out["stepped"] is True
    assert _reports(db) == []
