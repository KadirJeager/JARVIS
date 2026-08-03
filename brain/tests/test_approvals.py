"""Tests for app/approvals.py (Faz Y3, Görev 1): onay kuyruğu çekirdeği.

Taşıyıcı iki pim (silinirse Y3'ün güvenlik garantisi kalkar):
- `test_decide_on_expired_pending_never_executes` — zaman aşımı KARAR anında
  uygulanır, süpürücü henüz koşmamış olsa bile (spec §4.1).
- `test_decide_twice_calls_executor_only_once` — claim dokümanı çift yürütmeyi
  keser; yürütücü çağrı sayacı 1'de kalır (spec §4.2).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import approvals, config, reminders, tools
from app.memory import Memory
from tests.fakes import FakeDB

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
USER = "kadir@example.com"
OTHER = "baskasi@example.com"


def _now():
    return NOW.isoformat()


def _at(minutes):
    """NOW'dan `minutes` dakika sonrası (negatif = öncesi), ISO UTC."""
    return (NOW + timedelta(minutes=minutes)).isoformat()


def _now_fn_at(minutes):
    return lambda: _at(minutes)


class CountingExecutor:
    """Çağrı sayacı olan sahte yürütücü: `(tool_args, user_id) -> str`.

    Sayaç bu dilimin mutasyon pimidir — "yürütücü ikinci kez çağrılmaz"
    garantisi ancak sayılırsa kanıtlanır."""

    def __init__(self, result="Hatırlatma iptal edildi.", raises=None):
        self.calls = []
        self._result = result
        self._raises = raises

    def __call__(self, tool_args, user_id):
        self.calls.append((tool_args, user_id))
        if self._raises is not None:
            raise self._raises
        return self._result


def _request(db, *, user_id=USER, kind="tool_call", tool_name="cancel_reminder",
             tool_args=None, now_fn=_now, ttl_minutes=60, title="Hatırlatma silinecek"):
    return approvals.request(
        db,
        user_id=user_id,
        kind=kind,
        title=title,
        detail="'su iç' hatırlatması silinecek — kırmızı bölge, onay gerekiyor.",
        tool_name=tool_name,
        tool_args={"reminder_id": "r1"} if tool_args is None else tool_args,
        zone="red",
        session_id="s1",
        now_fn=now_fn,
        ttl_minutes=ttl_minutes,
    )


def _doc(db, approval_id):
    return db.collection(approvals.COLLECTION).document(approval_id).get().to_dict()


# ---------------------------------------------------------------------------
# request()
# ---------------------------------------------------------------------------


def test_request_writes_pending_doc_with_expiry():
    db = FakeDB()
    approval_id = _request(db, ttl_minutes=30)
    d = _doc(db, approval_id)
    assert d["status"] == approvals.STATUS_PENDING
    assert d["user_id"] == USER
    assert d["kind"] == "tool_call"
    assert d["tool_name"] == "cancel_reminder"
    assert d["zone"] == "red"
    assert d["session_id"] == "s1"
    assert d["created_at"] == _now()
    assert d["expires_at"] == _at(30)
    assert d["decided_at"] is None and d["decided_by"] is None and d["outcome"] is None


def test_request_default_ttl_comes_from_config():
    db = FakeDB()
    with patch.object(approvals.config, "APPROVAL_TTL_MINUTES", 15):
        approval_id = _request(db, ttl_minutes=None)
    assert _doc(db, approval_id)["expires_at"] == _at(15)


def test_request_truncates_and_stringifies_tool_args():
    db = FakeDB()
    approval_id = _request(db, tool_args={"reminder_id": "x" * 600, "count": 7})
    args = _doc(db, approval_id)["tool_args"]
    assert args["reminder_id"] == "x" * 500  # audit ile aynı kural: str(v)[:500]
    assert args["count"] == "7"


def test_request_requires_tool_name_for_tool_call():
    with pytest.raises(ValueError):
        _request(FakeDB(), tool_name=None)


def test_request_requires_user_and_title():
    with pytest.raises(ValueError):
        _request(FakeDB(), user_id="")
    with pytest.raises(ValueError):
        _request(FakeDB(), title="")


def test_request_rejects_unparseable_now():
    with pytest.raises(ValueError):
        _request(FakeDB(), now_fn=lambda: "dün")


# ---------------------------------------------------------------------------
# list_pending() / get()
# ---------------------------------------------------------------------------


def test_list_pending_returns_only_owners_pendings_newest_first():
    db = FakeDB()
    eski = _request(db, now_fn=_now_fn_at(-10))
    yeni = _request(db, now_fn=_now_fn_at(-1))
    _request(db, user_id=OTHER)

    items = approvals.list_pending(db, USER, now_fn=_now)
    assert [i["id"] for i in items] == [yeni, eski]
    assert all(i["user_id"] == USER for i in items)


def test_list_pending_skips_decided_ones():
    db = FakeDB()
    kalan = _request(db)
    karara_baglanan = _request(db)
    approvals.decide(db, karara_baglanan, USER, "rejected", {}, now_fn=_now)

    assert [i["id"] for i in approvals.list_pending(db, USER, now_fn=_now)] == [kalan]


def test_list_pending_hides_expired_even_before_the_sweeper_runs():
    db = FakeDB()
    _request(db, ttl_minutes=5)          # NOW+5'te dolar
    canli = _request(db, ttl_minutes=90)

    items = approvals.list_pending(db, USER, now_fn=_now_fn_at(30))
    assert [i["id"] for i in items] == [canli]


def test_list_pending_treats_corrupt_expiry_as_expired():
    """Fail-closed: okunamayan expires_at'li onay kuyrukta görünmez."""
    db = FakeDB()
    approval_id = _request(db)
    db.collection(approvals.COLLECTION).document(approval_id).set(
        {"expires_at": "belirsiz"}, merge=True)
    assert approvals.list_pending(db, USER, now_fn=_now) == []


def test_list_pending_caps_at_max_pending():
    db = FakeDB()
    for i in range(approvals.MAX_PENDING + 5):
        _request(db, now_fn=_now_fn_at(-i - 1), ttl_minutes=600)
    items = approvals.list_pending(db, USER, now_fn=_now)
    assert len(items) == approvals.MAX_PENDING
    assert items[0]["created_at"] == _at(-1)  # yeniden eskiye


def test_get_returns_own_approval_and_none_for_others():
    db = FakeDB()
    approval_id = _request(db)
    assert approvals.get(db, approval_id, USER)["id"] == approval_id
    assert approvals.get(db, approval_id, OTHER) is None
    assert approvals.get(db, "yok-boyle-bir-id", USER) is None


# ---------------------------------------------------------------------------
# decide() — onay / ret
# ---------------------------------------------------------------------------


def test_decide_approve_runs_executor_and_records_decision():
    db = FakeDB()
    approval_id = _request(db)
    ex = CountingExecutor(result="Hatırlatma iptal edildi.")

    out = approvals.decide(db, approval_id, USER, "approved",
                           {"cancel_reminder": ex}, now_fn=_now)

    assert out["status"] == approvals.STATUS_APPROVED
    assert out["already"] is False
    assert out["outcome"] == "Hatırlatma iptal edildi."
    assert ex.calls == [({"reminder_id": "r1"}, USER)]

    d = _doc(db, approval_id)
    assert d["status"] == approvals.STATUS_APPROVED
    assert d["decided_at"] == _now() and d["decided_by"] == USER
    assert d["outcome"] == "Hatırlatma iptal edildi."
    # Kararın birincil kaydı claim dokümanıdır (spec §3.2).
    claim = db.collection(approvals.CLAIMS_COLLECTION).document(approval_id).get()
    assert claim.exists and claim.to_dict()["decision"] == "approved"


def test_decide_twice_calls_executor_only_once():
    """Çift dokunuş (push + kuyruk senkronu) kırmızı eylemi iki kez çalıştıramaz."""
    db = FakeDB()
    approval_id = _request(db)
    ex = CountingExecutor()
    executors = {"cancel_reminder": ex}

    first = approvals.decide(db, approval_id, USER, "approved", executors, now_fn=_now)
    second = approvals.decide(db, approval_id, USER, "approved", executors, now_fn=_now)

    assert first["already"] is False
    assert second["already"] is True
    assert second["status"] == approvals.STATUS_APPROVED
    assert len(ex.calls) == 1


def test_decide_stops_at_existing_claim_even_when_status_still_pending():
    """Yarış hali: claim yazılmış ama status projeksiyonu henüz yazılmamış."""
    db = FakeDB()
    approval_id = _request(db)
    db.collection(approvals.CLAIMS_COLLECTION).document(approval_id).create(
        {"decision": "approved", "by": USER, "at": _now()})
    ex = CountingExecutor()

    out = approvals.decide(db, approval_id, USER, "approved",
                           {"cancel_reminder": ex}, now_fn=_now)

    assert out["already"] is True
    assert ex.calls == []


def test_decide_reject_never_runs_executor():
    db = FakeDB()
    approval_id = _request(db)
    ex = CountingExecutor()

    out = approvals.decide(db, approval_id, USER, "rejected",
                           {"cancel_reminder": ex}, now_fn=_now)

    assert out["status"] == approvals.STATUS_REJECTED and out["already"] is False
    assert ex.calls == []
    d = _doc(db, approval_id)
    assert d["status"] == approvals.STATUS_REJECTED
    assert d["decided_by"] == USER and d["decided_at"] == _now()


def test_decide_rejects_unknown_decision():
    db = FakeDB()
    approval_id = _request(db)
    with pytest.raises(ValueError):
        approvals.decide(db, approval_id, USER, "belki", {}, now_fn=_now)
    assert _doc(db, approval_id)["status"] == approvals.STATUS_PENDING


# ---------------------------------------------------------------------------
# decide() — zaman aşımı, sahiplik, yürütme hataları
# ---------------------------------------------------------------------------


def test_decide_on_expired_pending_never_executes():
    """Zaman aşımı = reddet, KARAR anında (spec §4.1). Süpürücü koşmamış olsa bile."""
    db = FakeDB()
    approval_id = _request(db, ttl_minutes=5)
    ex = CountingExecutor()

    out = approvals.decide(db, approval_id, USER, "approved",
                           {"cancel_reminder": ex}, now_fn=_now_fn_at(30))

    assert out["status"] == approvals.STATUS_EXPIRED
    assert out["already"] is True
    assert ex.calls == []
    d = _doc(db, approval_id)
    assert d["status"] == approvals.STATUS_EXPIRED
    assert d["decided_by"] is None  # kararı kimse vermedi, süre verdi
    # Süresi geçmiş onaya claim de yazılmaz.
    assert not db.collection(approvals.CLAIMS_COLLECTION).document(approval_id).get().exists


def test_decide_on_corrupt_expiry_never_executes():
    db = FakeDB()
    approval_id = _request(db)
    db.collection(approvals.COLLECTION).document(approval_id).set(
        {"expires_at": None}, merge=True)
    ex = CountingExecutor()

    out = approvals.decide(db, approval_id, USER, "approved",
                           {"cancel_reminder": ex}, now_fn=_now)

    assert out["status"] == approvals.STATUS_EXPIRED and ex.calls == []


def test_decide_on_someone_elses_approval_is_not_found():
    db = FakeDB()
    approval_id = _request(db)
    ex = CountingExecutor()

    out = approvals.decide(db, approval_id, OTHER, "approved",
                           {"cancel_reminder": ex}, now_fn=_now)

    assert out["status"] == approvals.STATUS_NOT_FOUND
    assert ex.calls == []
    assert _doc(db, approval_id)["status"] == approvals.STATUS_PENDING
    # Var olmayan onayla BİREBİR aynı cevap: varlık sızmaz (spec §4.4).
    assert out == approvals.decide(db, "yok-boyle-bir-id", OTHER, "approved",
                                   {"cancel_reminder": ex}, now_fn=_now)


def test_decide_marks_failed_when_executor_raises_and_does_not_retry():
    db = FakeDB()
    approval_id = _request(db)
    ex = CountingExecutor(raises=RuntimeError("firestore öldü"))
    executors = {"cancel_reminder": ex}

    out = approvals.decide(db, approval_id, USER, "approved", executors, now_fn=_now)

    assert out["status"] == approvals.STATUS_FAILED
    assert "firestore öldü" in out["outcome"]
    d = _doc(db, approval_id)
    assert d["status"] == approvals.STATUS_FAILED and "firestore öldü" in d["outcome"]
    assert d["decided_by"] == USER  # karar VERİLDİ; hata bir gözlemdir

    again = approvals.decide(db, approval_id, USER, "approved", executors, now_fn=_now)
    assert again["already"] is True and len(ex.calls) == 1


def test_decide_refuses_tool_without_registered_executor():
    db = FakeDB()
    approval_id = _request(db, tool_name="rm_rf_everything")

    out = approvals.decide(db, approval_id, USER, "approved",
                           {"cancel_reminder": CountingExecutor()}, now_fn=_now)

    assert out["status"] == approvals.STATUS_FAILED
    assert "yürütücü kayıtlı değil" in out["outcome"]
    assert _doc(db, approval_id)["status"] == approvals.STATUS_FAILED


def test_decide_approves_non_executable_kind_without_executing_anything():
    """Yürütücüsü olmayan bir tür onaylanır, yan etkisi olmaz.

    Örnek tür Y3'te `tool_grant` idi; Faz Y4.1 onu YÜRÜTÜLEBİLİR yaptı
    (approvals.EXECUTABLE_KINDS -> tool_registry.grant), bu yüzden örnek §8.5'in
    henüz uygulanmamış türüyle (`agent_spec`) güncellendi. Pimin kendisi
    değişmedi: tanınmayan bir tür ASLA bir yürütücü çağırmaz."""
    db = FakeDB()
    approval_id = _request(db, kind="agent_spec", tool_name=None)
    ex = CountingExecutor()

    out = approvals.decide(db, approval_id, USER, "approved",
                           {"cancel_reminder": ex}, now_fn=_now)

    assert out["status"] == approvals.STATUS_APPROVED and ex.calls == []


# ---------------------------------------------------------------------------
# expire_due()
# ---------------------------------------------------------------------------


def test_expire_due_expires_only_due_pendings():
    db = FakeDB()
    dolan = _request(db, ttl_minutes=5)
    duran = _request(db, ttl_minutes=90)

    out = approvals.expire_due(db, now_fn=_now_fn_at(30))

    assert out["expired"] == 1
    assert _doc(db, dolan)["status"] == approvals.STATUS_EXPIRED
    assert _doc(db, duran)["status"] == approvals.STATUS_PENDING


def test_expire_due_leaves_decided_approvals_alone():
    db = FakeDB()
    approval_id = _request(db, ttl_minutes=5)
    approvals.decide(db, approval_id, USER, "rejected", {}, now_fn=_now)

    out = approvals.expire_due(db, now_fn=_now_fn_at(30))

    assert out["expired"] == 0
    assert _doc(db, approval_id)["status"] == approvals.STATUS_REJECTED


# ---------------------------------------------------------------------------
# Yürütücü kayıt defteri + ilk gerçek kırmızı araç (Görev 2)
# ---------------------------------------------------------------------------


def _seed_reminder(db, text="su iç", status="pending"):
    _, ref = db.collection(reminders.REMINDERS_COLLECTION).add({
        "text": text, "due_at": _at(120), "status": status,
        "created_at": _now(), "sent_at": None, "fcm_result": None,
    })
    return ref.id


def test_register_executor_fills_the_default_registry():
    def noop(tool_args, user_id):
        return "tamam"

    original = dict(approvals.EXECUTORS)
    try:
        approvals.register_executor("deneme_araci", noop)
        assert approvals.EXECUTORS["deneme_araci"] is noop
    finally:
        approvals.EXECUTORS.clear()
        approvals.EXECUTORS.update(original)


def test_decide_falls_back_to_the_module_registry_when_no_executors_passed():
    db = FakeDB()
    approval_id = _request(db)
    ex = CountingExecutor(result="kayıt defterinden çalıştı")

    original = dict(approvals.EXECUTORS)
    try:
        approvals.register_executor("cancel_reminder", ex)
        out = approvals.decide(db, approval_id, USER, "approved", now_fn=_now)
    finally:
        approvals.EXECUTORS.clear()
        approvals.EXECUTORS.update(original)

    assert out["status"] == approvals.STATUS_APPROVED
    assert out["outcome"] == "kayıt defterinden çalıştı"
    assert len(ex.calls) == 1


def test_explicit_empty_executors_still_means_empty_not_the_registry():
    """`{}` bilinçli bir "hiçbir yürütücü yok" ifadesidir; varsayılan kayıt
    defterine SESSİZCE düşmez — yoksa bir test kendi izolasyonunu kaybeder."""
    db = FakeDB()
    approval_id = _request(db)

    out = approvals.decide(db, approval_id, USER, "approved", {}, now_fn=_now)

    assert out["status"] == approvals.STATUS_FAILED
    assert "yürütücü kayıtlı değil" in out["outcome"]


def test_registered_cancel_reminder_executor_really_cancels_the_reminder():
    """Uçtan uca: onay verildiğinde kayıt defterindeki yürütücü gerçekten
    `reminders.cancel`'a düşer ve Firestore'daki hatırlatma iptal olur."""
    db = FakeDB()
    tools.init(Memory(db))
    reminder_id = _seed_reminder(db, "su iç")
    approval_id = _request(db, tool_args={"reminder_id": reminder_id})

    out = approvals.decide(db, approval_id, USER, "approved", now_fn=_now)

    assert out["status"] == approvals.STATUS_APPROVED
    assert "iptal edildi" in out["outcome"] and "su iç" in out["outcome"]
    d = db.collection(reminders.REMINDERS_COLLECTION).document(reminder_id).get().to_dict()
    assert d["status"] == reminders.STATUS_CANCELLED


def test_cancel_reminder_executor_reports_missing_reminder_as_observation():
    """İlke 4: yürütücü fırlatmaz, gözlem döner — onay `approved` kalır ama
    outcome hatayı taşır, Kadir ne olduğunu görür."""
    db = FakeDB()
    tools.init(Memory(db))
    approval_id = _request(db, tool_args={"reminder_id": "yok-boyle-bir-id"})

    out = approvals.decide(db, approval_id, USER, "approved", now_fn=_now)

    assert out["outcome"].startswith("Hata")


def test_cancel_reminder_tool_is_red_zone():
    assert config.TOOL_ZONES["cancel_reminder"] == config.ZONE_RED


def test_cancel_reminder_tool_is_wired_into_all_tools():
    """Üretim bağlantısı: model ancak ALL_TOOLS'taki aracı görebilir."""
    assert tools.cancel_reminder in tools.ALL_TOOLS


def test_cancel_reminder_tool_body_is_defensive_only():
    """Gövde savunma amaçlıdır: kırmızı bölge callback'i çağrıyı zaten önce
    keser. Buraya ULAŞILIRSA (politika bağlantısı kopmuşsa) araç hatırlatmayı
    silmez, onaya işaret eden bir gözlem döner."""
    db = FakeDB()
    tools.init(Memory(db))
    reminder_id = _seed_reminder(db, "dokunulmayacak")

    out = tools.cancel_reminder(reminder_id)

    assert "onay" in out.lower()
    d = db.collection(reminders.REMINDERS_COLLECTION).document(reminder_id).get().to_dict()
    assert d["status"] == reminders.STATUS_PENDING


def test_instruction_tells_the_model_to_wait_for_the_approval_card():
    from app.agent import INSTRUCTION

    assert "onay kartı" in INSTRUCTION
